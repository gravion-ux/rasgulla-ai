"""The vendored 'her' personality catalogue from Her--Him.

Rasgulla speaks with one personality at a time.  The cards live in
``personas/her`` and are read straight from disk, so a new card is a file
drop, not a code change.

Only the personality fields of a card ever reach the model.  Everything
operational - the reply format, the JSON action plan, the approval gate and
the line she does not cross - stays in ``core_app`` and is never taken from
here.

Upstream: https://github.com/gravion-ux/Her--Him (MIT, see personas/her/LICENSE).
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
CATALOGUE_DIR = APP_DIR / "personas" / "her"
STATE_PATH = APP_DIR / "persona_state.json"
ENV_VAR = "RASGULLA_PERSONA"

#: The card the upstream catalogue itself names as its default.
DEFAULT_PERSONA_ID = "her-001-sassy"

#: Words that turn a sentence into a request to change personality.
PERSONA_SWITCH_TRIGGERS = (
    "switch to",
    "switch persona",
    "change personality",
    "change to",
    "act like",
    "be like",
    "become",
    "persona",
)

_lock = threading.Lock()
_catalogue: dict[str, dict[str, Any]] | None = None
_active: str | None = None


class PersonaError(RuntimeError):
    """Raised when a personality id is not in the catalogue."""


# -- reading the cards ------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    """Read one JSON object, or nothing at all when the file is unusable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _persona_id(card: dict[str, Any]) -> str:
    """The id a card is filed under."""
    block = (card.get("extensions") or {}).get("herhim") or {}
    return str(block.get("persona_id") or card.get("persona_id") or "").strip()


def load_catalogue() -> dict[str, dict[str, Any]]:
    """Every card in the her catalogue, keyed by its persona id.

    A broken card is skipped rather than allowed to stop the whole catalogue
    from loading: a personality is never worth losing the app over.
    """
    global _catalogue
    with _lock:
        cached = _catalogue
    if cached is not None:
        return cached

    cards: dict[str, dict[str, Any]] = {}
    for path in sorted(CATALOGUE_DIR.glob("her-*.json")):
        card = _read_json(path)
        if not card:
            continue
        pid = _persona_id(card) or path.stem
        card["persona_id"] = pid
        cards[pid] = card

    with _lock:
        _catalogue = cards
    return cards


# -- reading one card -------------------------------------------------------

def persona_name(card: dict[str, Any]) -> str:
    """The name a card answers to, without the archetype in brackets."""
    name = str(card.get("display_name") or card.get("name") or "").strip()
    if "(" in name:
        name = name.split("(", 1)[0].strip()
    return name


def persona_archetype(card: dict[str, Any]) -> str:
    """The archetype of a card, taken from the tail of its id."""
    pid = str(card.get("persona_id") or "")
    parts = pid.split("-")
    return parts[-1].lower() if len(parts) >= 2 else ""


def persona_tagline(card: dict[str, Any]) -> str:
    """The one line description a card carries, when it has one."""
    block = (card.get("extensions") or {}).get("herhim") or {}
    return str(block.get("tagline") or "").strip()


def persona_summary(persona_id: str | None = None) -> dict[str, Any]:
    """The short listing entry of one card."""
    pid = str(persona_id or "").strip() or active_persona_id()
    catalogue = load_catalogue()
    card = catalogue.get(pid) or catalogue.get(DEFAULT_PERSONA_ID) or {}
    if not card:
        return {}
    return {
        "id": pid,
        "name": persona_name(card),
        "tagline": persona_tagline(card),
        "archetype": persona_archetype(card),
        "tags": [str(tag) for tag in (card.get("tags") or [])],
    }


def catalogue_summaries() -> list[dict[str, Any]]:
    """A short listing of every card, for the brain to report."""
    return [
        persona_summary(pid)
        for pid in sorted(load_catalogue())
    ]


# -- sentences ---------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> list[str]:
    """The sentences of a piece of text."""
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(str(text or "").strip())]
    return [part for part in parts if part]


def _key(sentence: str) -> str:
    """The comparison key of a sentence: same words, same order, any case."""
    return " ".join(sentence.lower().split())


def _dedupe_sentences(text: str) -> str:
    """Drop a sentence that is said twice in the same text.

    A generated card repeats its description inside its personality field.
    Saying it once is enough, and twice reads like a broken prompt.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for sentence in _sentences(text):
        key = _key(sentence)
        if key in seen:
            continue
        seen.add(key)
        kept.append(sentence)
    return " ".join(kept)


def _covered_by(inner: str, outer: str) -> bool:
    """True when every sentence of ``inner`` is already in ``outer``."""
    inner_sentences = _sentences(inner)
    if not inner_sentences:
        return True
    outer_keys = {_key(sentence) for sentence in _sentences(outer)}
    return all(_key(sentence) in outer_keys for sentence in inner_sentences)


def is_generated(card: dict[str, Any]) -> bool:
    """True when a card says nothing more than its own description.

    The upstream catalogue holds four hand written cards and a larger set of
    generated ones.  When two cards answer to the same word, the hand written
    one is the better answer, so this is what tells them apart.
    """
    description = _dedupe_sentences(card.get("description", ""))
    personality = _dedupe_sentences(card.get("personality", ""))
    return bool(description) and _covered_by(personality, description)


# -- the prompt block --------------------------------------------------------

def persona_prompt(persona_id: str | None = None) -> str:
    """The personality block for one card, ready to drop into the prompt.

    An unknown id falls back to the default card rather than blanking the
    prompt, so a typo can never leave her with no personality at all.
    """
    pid = str(persona_id).strip() if persona_id else active_persona_id()
    catalogue = load_catalogue()
    card = catalogue.get(pid) or catalogue.get(DEFAULT_PERSONA_ID) or {}
    if not card:
        return ""

    name = persona_name(card)
    about = _dedupe_sentences(card.get("description", ""))
    personality = _dedupe_sentences(card.get("personality", ""))
    if about and _covered_by(personality, about):
        # The personality field only repeats the description: say it once.
        personality = ""
    scenario = _dedupe_sentences(card.get("scenario", ""))
    example = " ".join(str(card.get("mes_example", "")).split())

    lines = ["Her temperament - the personality she speaks with:"]
    if name:
        lines.append(f"Name: {name}")
    if about:
        lines.append(f"About her: {about}")
    if personality:
        lines.append(f"Personality: {personality}")
    if scenario:
        lines.append(f"Where she is: {scenario}")
    if example:
        lines.append(f'How she sounds: "{example}"')
    return "\n".join(lines)


# -- the active personality --------------------------------------------------

def active_persona_id() -> str:
    """The personality she is speaking with right now."""
    global _active
    with _lock:
        if _active:
            return _active

    catalogue = load_catalogue()
    chosen = str(_read_json(STATE_PATH).get("persona") or "").strip()
    override = str(os.environ.get(ENV_VAR, "")).strip()
    if override:
        chosen = override
    if chosen not in catalogue:
        chosen = (
            DEFAULT_PERSONA_ID
            if DEFAULT_PERSONA_ID in catalogue
            else next(iter(sorted(catalogue)), "")
        )
    with _lock:
        _active = chosen
    return chosen


def set_active_persona(persona_id: str) -> str:
    """Make one card the active personality and remember it for the next start."""
    global _active
    catalogue = load_catalogue()
    pid = str(persona_id or "").strip()
    if pid not in catalogue:
        raise PersonaError(f"unknown personality: {pid}")
    with _lock:
        _active = pid
    try:
        STATE_PATH.write_text(
            json.dumps({"persona": pid}, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        # The choice still holds for this run, it just will not survive a
        # restart.  That is better than refusing to switch at all.
        pass
    return pid


# -- spoken switches ---------------------------------------------------------

def _normalize(text: str) -> str:
    """Lower case, drop the punctuation, squeeze the spaces."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def _phrase_in(phrase: str, haystack: str) -> bool:
    """True when the phrase sits in the haystack on whole words."""
    if not phrase:
        return False
    return f" {phrase} " in f" {haystack} "


def _match_score(card: dict[str, Any], tail: str) -> int:
    """How well a card answers a phrase.  Zero means it does not."""
    score = 0
    pid = _normalize(str(card.get("persona_id") or ""))
    if pid and _phrase_in(pid, tail):
        score = max(score, 100)
    name = _normalize(persona_name(card))
    if name and _phrase_in(name, tail):
        score = max(score, 80)
    first = name.split()[0] if name else ""
    if first and first != name and _phrase_in(first, tail):
        score = max(score, 60)
    archetype = _normalize(persona_archetype(card))
    if archetype and _phrase_in(archetype, tail):
        score = max(score, 40)
    return score


def _better(catalogue: dict[str, dict[str, Any]], left: str, right: str) -> str:
    """Pick the better of two cards that answer equally well.

    The hand written card wins, because it says more than the generated one.
    After that the lower id wins, so the answer is always the same.
    """
    left_rich = not is_generated(catalogue[left])
    right_rich = not is_generated(catalogue[right])
    if left_rich != right_rich:
        return left if left_rich else right
    return min(left, right)


def resolve_persona(text: str) -> str | None:
    """The card a sentence is asking for, or None when it is not a switch.

    Only a phrase that asks for a change may change her: a bare name in an
    ordinary sentence is just a name.
    """
    catalogue = load_catalogue()
    if not catalogue:
        return None

    normalized = _normalize(text)
    if not normalized:
        return None

    trigger_index = -1
    for trigger in PERSONA_SWITCH_TRIGGERS:
        index = normalized.find(trigger)
        if index != -1 and (trigger_index == -1 or index < trigger_index):
            trigger_index = index
    if trigger_index == -1:
        return None

    tail = normalized[trigger_index:]
    for trigger in PERSONA_SWITCH_TRIGGERS:
        if tail.startswith(trigger):
            tail = tail[len(trigger) :]
            break
    tail = tail.strip()
    if not tail:
        return None

    best_id = ""
    best_score = 0
    for pid in sorted(catalogue):
        score = _match_score(catalogue[pid], tail)
        if score <= 0:
            continue
        if score > best_score:
            best_id, best_score = pid, score
        elif score == best_score and best_id:
            best_id = _better(catalogue, best_id, pid)
    return best_id or None
