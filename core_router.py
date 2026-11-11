"""Model routing, rate limiting and usage accounting for Rasgulla.

Every free prototyping endpoint lives in ``models.json`` so the routing table
can be edited without touching the code.  The router:

* keeps the app under the free tier's requests-per-minute ceiling,
* tries each tier's model chain in order and falls back automatically,
* puts a failing model into a timed cooldown instead of hanging the UI,
* records token usage and latency per model for the Usage panel.

Provider identity never reaches the user interface or the local log: every
string that can be shown goes through :func:`redact` first.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import winreg
from collections import deque
from pathlib import Path
from typing import Any, Iterable

import requests

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PROVIDER_RE = re.compile(
    r"(?:nvidia\s+nim|nvidia\s+ai|nim\s+api|nim\s+agent|nvidia\b|\bnim\b)",
    re.IGNORECASE,
)
_MODEL_PREFIX_RE = re.compile(r"^[a-z0-9._-]+/", re.IGNORECASE)


def redact(text: str) -> str:
    """Strip provider identity and raw endpoints from anything a human reads."""
    if not text:
        return text
    text = _URL_RE.sub("[service endpoint hidden]", text)
    return redact_provider(text)


def redact_provider(text: str) -> str:
    """Strip provider identity but keep links, so replies stay readable."""
    if not text:
        return text
    return _PROVIDER_RE.sub("the assistant", text)


def friendly_model(model_id: str, aliases: dict[str, str] | None = None) -> str:
    """Return a short, brand-neutral label for a model id."""
    if aliases and model_id in aliases:
        return aliases[model_id]
    return _MODEL_PREFIX_RE.sub("", model_id)


class RouterError(RuntimeError):
    """Raised when every model in a tier failed."""


class ModelTimeout(RouterError):
    pass


class ModelUnavailable(RouterError):
    pass


class RateLimited(RouterError):
    pass


def read_api_key(env_names: Iterable[str]) -> str:
    """Read the first API key found in the environment or the user registry."""
    for name in env_names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    for name in env_names:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as registry:
                value, _ = winreg.QueryValueEx(registry, name)
                text = str(value).strip()
                if text:
                    return text
        except OSError:
            continue
    return ""


class RateLimiter:
    """Keeps the app comfortably below the free tier's request ceiling."""

    def __init__(self, per_minute: int = 20) -> None:
        self.per_minute = max(1, int(per_minute))
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 60.0:
                    self._calls.popleft()
                if len(self._calls) < self.per_minute:
                    self._calls.append(now)
                    return
                wait = 60.0 - (now - self._calls[0]) + 0.05
            time.sleep(max(0.05, wait))


class UsageTracker:
    """Per-model request, token and latency accounting kept on disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "models": {},
            "routes": {},
            "totals": {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "errors": 0,
            },
        }
        self._load()

    @staticmethod
    def _empty_model_entry() -> dict[str, Any]:
        """The shape every per-model bucket must have."""
        return {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "errors": 0,
            "seconds_total": 0.0,
            "last_used": "",
        }

    def _load(self) -> None:
        """Restore counters, normalising whatever shape the file happens to hold.

        A truncated or hand-edited file must never break the next request, so
        every stored value is checked against the expected shape and anything
        missing falls back to zero.
        """
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(stored, dict):
            return

        models = stored.get("models")
        if isinstance(models, dict):
            for model, entry in models.items():
                if not isinstance(entry, dict):
                    continue
                restored = self._empty_model_entry()
                for key in ("requests", "prompt_tokens", "completion_tokens", "errors"):
                    value = entry.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        restored[key] = value
                seconds = entry.get("seconds_total")
                if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                    restored["seconds_total"] = float(seconds)
                last_used = entry.get("last_used")
                if isinstance(last_used, str):
                    restored["last_used"] = last_used
                restored["total_tokens"] = restored["prompt_tokens"] + restored["completion_tokens"]
                self._data["models"][str(model)] = restored

        routes = stored.get("routes")
        if isinstance(routes, dict):
            for route, entry in routes.items():
                if not isinstance(entry, dict):
                    continue
                requests = entry.get("requests")
                if isinstance(requests, int) and not isinstance(requests, bool):
                    self._data["routes"][str(route)] = {"requests": requests}

        totals = stored.get("totals")
        if isinstance(totals, dict):
            for key in self._data["totals"]:
                value = totals.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    self._data["totals"][key] = value
            self._data["totals"]["total_tokens"] = (
                self._data["totals"]["prompt_tokens"] + self._data["totals"]["completion_tokens"]
            )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def record(
        self,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        seconds: float = 0.0,
        error: bool = False,
        route: str = "",
    ) -> None:
        with self._lock:
            entry = self._data["models"].setdefault(model, self._empty_model_entry())
            entry["requests"] += 1
            entry["prompt_tokens"] += int(prompt_tokens)
            entry["completion_tokens"] += int(completion_tokens)
            entry["total_tokens"] = entry["prompt_tokens"] + entry["completion_tokens"]
            entry["seconds_total"] = round(entry.get("seconds_total", 0.0) + float(seconds), 3)
            entry["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if error:
                entry["errors"] += 1

            totals = self._data["totals"]
            totals["requests"] += 1
            totals["prompt_tokens"] += int(prompt_tokens)
            totals["completion_tokens"] += int(completion_tokens)
            totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
            if error:
                totals["errors"] += 1

            if route:
                route_entry = self._data["routes"].setdefault(route, {"requests": 0})
                route_entry["requests"] += 1

            self._save()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def reset(self) -> None:
        with self._lock:
            self._data["models"] = {}
            self._data["routes"] = {}
            self._data["totals"] = {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "errors": 0,
            }
            self._save()


class TaskClassifier:
    """Picks the routing tier for a request without spending a model call."""

    KEYWORDS: dict[str, tuple[str, ...]] = {
        "code": (
            "code", "coding", "function", "class", "method", "python", "javascript", "typescript",
            "java", "c++", "cpp", "rust", "golang", "react", "vue", "angular", "html", "css",
            "sql", "query", "database", "django", "flask", "fastapi", "node", "npm", "api",
            "json", "yaml", "regex", "debug", "debugging", "bug", "error", "exception",
            "traceback", "stacktrace", "compile", "refactor", "algorithm", "leetcode",
            "github", "git", "repo", "repository", "unit test", "pytest", "docker", "kubernetes",
            "bash", "script", "snippet", "frontend", "backend", "deploy", "script",
        ),
        "creative": (
            "song", "songs", "music", "lyric", "lyrics", "melody", "chord", "chords", "riff",
            "beat", "beats", "rap", "mix", "mastering", "arrangement", "arrange", "producer",
            "production", "scale", "tabla", "guitar", "piano", "drum", "drums", "bass", "vocal",
            "vocals", "theme", "soundtrack", "album", "band", "hook", "verse", "bridge", "intro",
            "story", "novel", "screenplay", "dialogue", "scene", "plot", "poem", "haiku",
            "prose", "character development", "character description", "character profile",
            "character sheet", "characters", "villain", "twist", "chapter", "fiction", "romance",
            "dark romance", "ending", "narrative", "screenwriting", "writing style",
        ),
        "security": (
            "ctf", "vulnerability", "vulnerabilities", "vuln", "pentest", "payload", "exploit",
            "exploitation", "reverse engineer", "reverse engineering", "malware", "phishing",
            "red team", "redteam", "buffer overflow", "sql injection", "sqli", "xss", "csrf",
            "privilege escalation", "forensics", "owasp", "nmap", "wireshark", "burp",
            "security lab", "attack surface", "hardening", "brute force", "firewall rules",
            "credential", "credentials", "cve",
            # recon, enumeration and dumping - the words a red team actually uses
            "recon", "reconnaissance", "enumerate", "enumeration", "port scan", "port scanning",
            "open ports", "service scan", "vuln scan", "vulnerability scan", "scanner",
            "fuzz", "fuzzing", "fuzzer", "subdomain", "subdomains", "endpoint discovery",
            "api routes", "api endpoints", "attack vector", "attack path", "bypass", "evasion",
            "sandbox escape", "escalate", "escalation", "reverse shell", "shellcode",
            "command and control", "ssrf", "xxe", "deserialization", "lfi", "rfi",
            "path traversal", "directory traversal", "jwt", "tls certificate", "ssl certificate",
            "certificate", "harvest", "dump", "dumper", "hash dump", "credential dump",
            "memory dump", "registry dump", "sam dump", "lsass", "mimikatz", "hashes",
            "hashcat", "john the ripper", "ntds", "ad enumeration", "bloodhound", "kerberos",
            # verbatim output - the words people use when they want the raw text
            "verbatim", "character for character", "raw output", "exact values",
            "full output", "complete output", "unmasked", "unredacted",
        ),
        "reason": (
            "analyse", "analyze", "analysis", "deep dive", "step by step", "reason carefully",
            "compare", "trade-off", "tradeoff", "architecture", "design a system", "strategy",
            "debug why", "root cause", "evaluate", "prove", "derive",
        ),
    }

    MODE_TO_TIER = {
        "normal": "chat",
        "coder": "code",
        "music": "creative",
        "writer": "creative",
        "redteam": "security",
    }

    # When two tiers score the same, the one higher up wins.  A red-team
    # dumper must not lose its request to a generic keyword.
    TIER_PRIORITY = ("security", "code", "reason", "creative", "chat")

    @classmethod
    def _rank(cls, tier: str) -> int:
        try:
            return cls.TIER_PRIORITY.index(tier)
        except ValueError:
            return len(cls.TIER_PRIORITY)

    def classify(self, request: str, has_image: bool = False, forced_mode: str = "auto") -> str:
        """Return the tier name for a request."""
        if forced_mode and forced_mode != "auto":
            return self.MODE_TO_TIER.get(forced_mode, "chat")

        text = request.lower()
        scores: dict[str, int] = {}
        for tier, words in self.KEYWORDS.items():
            hits = 0
            for word in words:
                if " " in word:
                    hits += text.count(word)
                else:
                    # Word boundaries stop "api" matching inside "capital".
                    hits += len(re.findall(rf"\b{re.escape(word)}\b", text))
            scores[tier] = hits

        best = max(scores, key=lambda key: (scores[key], -self._rank(key)))
        if scores[best] == 0:
            return "vision" if has_image else "chat"
        if has_image and scores[best] < 2:
            return "vision"
        return best


_REPLY_FIELD_RE = re.compile(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)
_REPLY_UNCLOSED_RE = re.compile(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)\s*$', re.DOTALL)
_ANSWER_KEYS = ("content", "answer", "output", "text", "result")


def _decode_json_string(value: str) -> str:
    """Turn the body of a JSON string back into the text it carried."""
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return (
            value.replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\\\", "\\")
        )


def _salvage_actions(raw: str) -> list[Any]:
    """Recover every complete action object from a truncated JSON payload.

    A reply that hits the token cap is cut in the middle of the actions
    array.  The objects that did arrive are still valid, so they are kept
    instead of being thrown away with the rest of the payload.
    """
    actions: list[Any] = []
    marker = raw.find('"actions"')
    if marker < 0:
        return actions
    start = raw.find("[", marker)
    if start < 0:
        return actions
    depth = 0
    buffer = ""
    in_string = False
    escaped = False
    for char in raw[start + 1:]:
        if in_string:
            buffer += char
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            buffer += char
            continue
        if char == "{":
            depth += 1
            buffer += char
            continue
        if char == "}":
            depth -= 1
            buffer += char
            if depth == 0:
                try:
                    item = json.loads(buffer)
                except json.JSONDecodeError:
                    buffer = ""
                    continue
                if isinstance(item, dict):
                    actions.append(item)
                buffer = ""
            continue
        if depth > 0:
            buffer += char
    return actions


def parse_json_plan(raw: str) -> dict[str, Any]:
    """Parse the model's JSON reply, tolerating fences and stray prose.

    When the payload is broken or was cut off by the token cap, the text that
    did arrive is kept exactly as it was: no markdown is stripped, no value is
    rewritten, and the actions that arrived are recovered too.  A dump is data
    - it must reach the user unchanged.
    """
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    parsed: Any = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                parsed = None

    if not isinstance(parsed, dict):
        match = _REPLY_FIELD_RE.search(raw)
        unterminated = False
        if match:
            reply = _decode_json_string(match.group(1))
        else:
            match = _REPLY_UNCLOSED_RE.search(raw)
            if match:
                # The reply string was cut in half by the token cap.
                reply = _decode_json_string(match.group(1))
                unterminated = True
            else:
                reply = raw
        actions = _salvage_actions(raw)
        mode_match = re.search(r'"mode"\s*:\s*"([^"]{0,40})"', raw)
        mode = mode_match.group(1).strip().lower() if mode_match else "normal"
        if mode not in TaskClassifier.MODE_TO_TIER:
            mode = "normal"
        clarify_match = re.search(r'"clarify"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        clarify = _decode_json_string(clarify_match.group(1)) if clarify_match else None
        warning = ""
        if unterminated:
            warning = "That reply was cut off before it finished - this is everything that arrived."
        elif actions:
            warning = "That reply came back broken - the parts below are what arrived."
        elif reply is raw:
            # No plan block at all: the model wrote plain text (often its own
            # working notes).  Say so instead of passing it off as an answer.
            warning = (
                "That reply came back as plain text instead of the usual format - "
                "here it is exactly as it arrived."
            )
        return {
            "mode": mode,
            "reply": reply.strip() if isinstance(reply, str) else "",
            "clarify": clarify.strip() if clarify else None,
            "actions": actions,
            "_warning": warning,
        }

    reply = parsed.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        # The model sometimes answers in a different field.  Look for it
        # instead of replacing the whole answer with a placeholder.
        for key in _ANSWER_KEYS:
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                reply = value
                break
    if not isinstance(reply, str) or not reply.strip():
        reply = "I prepared a response."
    mode = str(parsed.get("mode", "normal")).lower().strip()
    if mode not in TaskClassifier.MODE_TO_TIER:
        mode = "normal"
    clarify = parsed.get("clarify")
    if not isinstance(clarify, str) or not clarify.strip():
        clarify = None
    actions = parsed.get("actions")
    if not isinstance(actions, list):
        actions = parsed.get("steps") if isinstance(parsed.get("steps"), list) else []
    return {
        "mode": mode,
        "reply": reply.strip(),
        "clarify": clarify.strip() if clarify else None,
        "actions": actions,
        "_warning": "",
    }


class ModelRouter:
    """Chooses a tier, runs the model chain and reports what actually ran."""

    def __init__(self, config_path: Path, api_key: str, usage: UsageTracker) -> None:
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise RouterError(
                f"the model configuration could not be read ({type(error).__name__})"
            ) from error
        except json.JSONDecodeError as error:
            raise RouterError("the model configuration is not valid JSON") from error
        if not isinstance(raw, dict):
            raise RouterError("the model configuration is not a JSON object")

        self.config = raw
        self.defaults = self._section(raw, "defaults")
        self.tiers = self._section(raw, "tiers")
        self.aliases = self._section(raw, "aliases")
        self.model_timeouts = self._section(raw, "model_timeouts")
        self.endpoint = str(raw.get("endpoint", "") or "")
        key_env = raw.get("key_env")
        self.key_env = [str(name) for name in key_env] if isinstance(key_env, list) and key_env else ["RASGULLA_API_KEY"]
        self.key = api_key
        self.usage = usage
        self.limiter = RateLimiter(self.defaults.get("requests_per_minute", 20))
        self._cooldown: dict[str, float] = {}
        self._disabled: set[str] = set()
        self._lock = threading.Lock()
        self.last_model: str | None = None
        self.last_tier: str | None = None
        self.last_latency: float = 0.0

    @staticmethod
    def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
        """Return a configuration section, ignoring a broken value."""
        value = raw.get(name)
        return value if isinstance(value, dict) else {}

    # -- introspection -------------------------------------------------
    def label(self, model: str | None) -> str:
        return friendly_model(model or "", self.aliases)

    def tier_label(self, tier: str) -> str:
        return self.tiers.get(tier, {}).get("label", tier.title())

    def status_rows(self) -> list[dict[str, Any]]:
        """Model health for the Usage panel."""
        snapshot = self.usage.snapshot()
        rows: list[dict[str, Any]] = []
        with self._lock:
            cooldown = dict(self._cooldown)
            disabled = set(self._disabled)
        for model, entry in snapshot.get("models", {}).items():
            until = cooldown.get(model, 0.0)
            remaining = max(0, int(until - time.monotonic()))
            rows.append(
                {
                    "model": model,
                    "label": self.label(model),
                    "requests": entry.get("requests", 0),
                    "tokens": entry.get("total_tokens", 0),
                    "errors": entry.get("errors", 0),
                    "avg_seconds": round(entry.get("seconds_total", 0.0) / max(1, entry.get("requests", 0)), 2),
                    "cooldown": remaining,
                    "disabled": model in disabled,
                }
            )
        rows.sort(key=lambda row: (-row["requests"], row["label"]))
        return rows

    # -- failure handling ----------------------------------------------
    def _handle_failure(self, model: str, error: Exception) -> None:
        with self._lock:
            if isinstance(error, ModelUnavailable):
                self._disabled.add(model)
            elif isinstance(error, RateLimited):
                self._cooldown[model] = time.monotonic() + float(self.defaults.get("cooldown_rate_limit_seconds", 90))
            elif isinstance(error, ModelTimeout):
                self._cooldown[model] = time.monotonic() + float(self.defaults.get("cooldown_timeout_seconds", 300))
            else:
                self._cooldown[model] = time.monotonic() + 30.0

    def _available(self, model: str) -> bool:
        with self._lock:
            if model in self._disabled:
                return False
            return self._cooldown.get(model, 0.0) <= time.monotonic()

    # -- calling -------------------------------------------------------
    def _request(
        self,
        model: str,
        spec: dict[str, Any],
        messages: list[dict[str, Any]],
        image: str | None,
        timeout_override: float | None = None,
    ) -> tuple[str, dict[str, int], float]:
        # A health probe passes its own short timeout, so the startup check
        # cannot hang for the full per-model timeout of a slow model.
        timeout = float(
            timeout_override
            if timeout_override is not None
            else self.model_timeouts.get(
                model,
                spec.get("timeout_seconds", self.defaults.get("timeout_seconds", 90)),
            )
        )
        # The turn that carries the picture must be the user's own request, not
        # a trailing system note, so find the last genuine user message.
        user_index = -1
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                user_index = index
                break

        content: Any = ""
        if user_index >= 0:
            content = messages[user_index].get("content", "")
        else:
            user_index = len(messages)

        if image:
            content = [
                {"type": "text", "text": content if isinstance(content, str) else ""},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
            ]

        payload_messages = list(messages)
        if user_index >= len(payload_messages):
            payload_messages.append({"role": "user", "content": content})
        else:
            payload_messages[user_index] = {"role": "user", "content": content}

        started = time.monotonic()
        response = requests.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": float(spec.get("temperature", 0.4)),
                "max_tokens": int(spec.get("max_tokens", 800)),
                "messages": payload_messages,
            },
            timeout=timeout,
        )
        elapsed = time.monotonic() - started
        if response.status_code == 429:
            raise RateLimited("rate limited")
        if response.status_code in (404, 410):
            # 410 Gone means the model was removed from the endpoint.  Treat it
            # like a missing model so the router retires it instead of
            # retrying it on every request.
            raise ModelUnavailable("model unavailable")
        if response.status_code >= 500:
            raise RouterError(f"service error {response.status_code}")
        if response.status_code >= 400:
            raise RouterError(f"request rejected ({response.status_code})")
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise RouterError("empty response")
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        if not text.strip() and message.get("reasoning_content"):
            text = message["reasoning_content"]
        usage = body.get("usage") or {}
        return text, {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        }, elapsed

    # -- health checking ------------------------------------------------
    def health_check(self, timeout: float = 10.0) -> dict[str, str]:
        """Probe every routed model once so broken ones never block a request.

        A model that times out or is missing is retired for the session, and a
        healthy model is left in place.  Safe to call from a background thread.
        """
        results: dict[str, str] = {}
        models: list[str] = []
        for spec in self.tiers.values():
            chain = spec.get("chain", [])
            # Only the first choice of each tier is probed up front; the rest
            # are discovered lazily the first time they are actually needed.
            for model in chain[:1]:
                if model not in models:
                    models.append(model)
        for model in models:
            probe_spec = {
                "temperature": 0.0,
                "max_tokens": 4,
                "timeout_seconds": timeout,
            }
            try:
                self.limiter.acquire()
                self._request(
                    model,
                    probe_spec,
                    [{"role": "user", "content": "OK"}],
                    None,
                    timeout_override=timeout,
                )
                results[model] = "ready"
            except ModelUnavailable:
                results[model] = "unavailable"
                # A missing model is retired for this session, so the tier
                # falls through to its next choice instead of timing out
                # again on every request.
                self._handle_failure(model, ModelUnavailable("model unavailable"))
            except RateLimited:
                results[model] = "rate limited"
                self._handle_failure(model, RateLimited("rate limited"))
            except ModelTimeout:
                results[model] = "timeout"
                self._handle_failure(model, ModelTimeout("model timed out"))
            except RouterError:
                results[model] = "error"
            except Exception:
                results[model] = "error"
        return results

    def call(
        self,
        tier: str,
        messages: list[dict[str, Any]],
        image: str | None = None,
        extra_instructions: str = "",
    ) -> dict[str, Any]:
        """Run the tier's model chain, falling back when a model fails."""
        spec = self.tiers.get(tier) or self.tiers.get("chat", {})
        chain: list[str] = list(spec.get("chain", []))
        limit = min(int(self.defaults.get("max_attempts_per_request", 3)), max(1, len(chain)))
        attempts: list[str] = []
        failures: list[str] = []

        for model in chain[:limit]:
            if not self._available(model):
                failures.append(f"{self.label(model)} is cooling down")
                continue
            attempt_messages = messages
            if extra_instructions:
                # The brief belongs next to the system prompt.  Appending it
                # would turn it into the final turn and hide the request.
                brief = {"role": "system", "content": extra_instructions}
                position = 1 if messages and messages[0].get("role") == "system" else 0
                attempt_messages = list(messages[:position]) + [brief] + list(messages[position:])
            try:
                self.limiter.acquire()
                text, usage, elapsed = self._request(model, spec, attempt_messages, image)
            except requests.Timeout as error:
                failure = ModelTimeout(f"{self.label(model)} timed out")
                self._handle_failure(model, failure)
                self.usage.record(model, error=True, route=tier)
                failures.append(redact(str(failure)))
                attempts.append(self.label(model))
                continue
            except RouterError as error:
                self._handle_failure(model, error)
                self.usage.record(model, error=True, route=tier)
                failures.append(redact(str(error)))
                attempts.append(self.label(model))
                continue
            except Exception as error:
                failure = RouterError(f"{self.label(model)}: {type(error).__name__}")
                self._handle_failure(model, failure)
                self.usage.record(model, error=True, route=tier)
                failures.append(redact(str(failure)))
                attempts.append(self.label(model))
                continue

            self.usage.record(
                model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                seconds=elapsed,
                route=tier,
            )
            self.last_model = model
            self.last_tier = tier
            self.last_latency = elapsed
            return {
                "text": text,
                "model": model,
                "label": self.label(model),
                "tier": tier,
                "usage": usage,
                "seconds": round(elapsed, 2),
                "attempts": attempts,
                "fallbacks": failures,
            }

        raise RouterError("; ".join(failures) or "no model was available")
