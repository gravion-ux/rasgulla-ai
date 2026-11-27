"""Heuristic static risk analysis for Rasgulla AI.

Findings are hypotheses for human review. The analyzer never executes the code
it inspects, never accesses a network, and never collects credentials.

Every rule carries the metadata a reviewer needs to triage it: how confident
the matcher is, the CWE the weakness maps to, and — for the weaknesses that
only exist in LLM-backed applications — the OWASP Top 10 for LLM Applications
category it belongs to.

Only CWE identifiers MITRE still accepts for vulnerability mapping are used.
An identifier MITRE marks *discouraged* ("this CWE ID should not be used to
map to real-world vulnerabilities") names a catch-all class rather than the
defect itself, so it would not tell a reviewer what to fix.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "EXTENSIONS",
    "IGNORED_DIRS",
    "MANIFEST_NAMES",
    "MANIFEST_PATTERNS",
    "MANIFEST_SUFFIXES",
    "OWASP_LLM_TOP_10",
    "RULES",
    "Finding",
    "Rule",
    "analyze_file",
    "analyze_path",
    "is_manifest",
]

EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".c", ".h", ".cpp", ".rs", ".go", ".cs", ".java",
    ".kt", ".swift", ".rb", ".php", ".sh", ".bash", ".ps1", ".yaml", ".yml", ".json", ".toml",
    ".ini", ".conf", ".cfg", ".env", ".properties", ".xml", ".tf", ".gradle", ".dockerfile",
}
# Dependency, build and cache directories hold third-party or generated code, not project source.
IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".eggs",
    "site-packages", "target", ".idea", ".vscode", "vendor", "coverage",
}

# Agent manifests are not source code, but an agent reads them as instructions.
# They get their own scan scope so a manifest rule can fire without pulling
# every Markdown file in the repository into the source-code scan.
MANIFEST_NAMES = frozenset({
    "skill.md", "agent.md", "agents.md", "claude.md", "gemini.md",
    ".cursorrules", ".clinerules", ".windsurfrules",
})
MANIFEST_SUFFIXES = frozenset({".mdc"})
MANIFEST_PATTERNS = ("*copilot-instructions*",)

# OWASP Top 10 for LLM Applications (2026). A finding can map to one of
# these categories; the identifiers and titles are the published ones.
OWASP_LLM_TOP_10 = {
    "LLM01": "Prompt Injection",
    "LLM02": "Sensitive Information Disclosure",
    "LLM03": "Excessive Agency",
    "LLM04": "Supply Chain",
    "LLM05": "Data and Model Poisoning",
    "LLM06": "Unbounded Consumption",
    "LLM07": "Misinformation",
    "LLM08": "Hidden Context Exposure",
    "LLM09": "Vector and Embedding Weaknesses",
    "LLM10": "Improper Output Handling",
}

CWE_URL = "https://cwe.mitre.org/data/definitions/{number}.html"
OWASP_LLM_URL = "https://genai.owasp.org/top-10-for-llm-applications-2026/"

# The fields a rule had before the table grew its metadata. A caller that
# indexed or unpacked a rule as the original five-value tuple keeps working.
_LEGACY_RULE_FIELDS = ("rule_id", "severity", "pattern", "message", "recommendation")


def _references(cwe: str, owasp_llm: str) -> tuple[str, ...]:
    """The reference links that back a rule."""
    references = []
    if cwe:
        references.append(CWE_URL.format(number=cwe.removeprefix("CWE-")))
    if owasp_llm:
        references.append(OWASP_LLM_URL)
    return tuple(references)


@dataclass(frozen=True)
class Rule:
    """One detection rule and the metadata that travels with it."""

    rule_id: str
    severity: str
    pattern: re.Pattern
    message: str
    recommendation: str
    confidence: str = "medium"
    cwe: str = ""
    owasp_llm: str = ""
    owasp_llm_name: str = ""
    references: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    requires: tuple[re.Pattern, ...] = ()
    forbids: tuple[re.Pattern, ...] = ()

    def __iter__(self):
        """Unpack as the original five values.

        The rule table grew metadata after the first release; older callers
        that treated a rule as a tuple keep working.
        """
        return iter(tuple(getattr(self, name) for name in _LEGACY_RULE_FIELDS))

    def __getitem__(self, index):
        """Index as the original five-value tuple."""
        fields = tuple(getattr(self, name) for name in _LEGACY_RULE_FIELDS)
        if isinstance(index, slice):
            return fields[index]
        if not isinstance(index, int):
            raise TypeError("rule indices must be integers")
        return fields[index]

    def __len__(self):
        return len(_LEGACY_RULE_FIELDS)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "confidence": self.confidence,
            "message": self.message,
            "recommendation": self.recommendation,
            "pattern": self.pattern.pattern,
            "cwe": self.cwe,
            "owasp_llm": self.owasp_llm,
            "owasp_llm_name": self.owasp_llm_name,
            "references": list(self.references),
        }


@dataclass
class Finding:
    rule_id: str
    severity: str
    message: str
    path: str
    line: int
    evidence: str
    recommendation: str
    confidence: str = "medium"
    cwe: str = ""
    owasp_llm: str = ""
    owasp_llm_name: str = ""
    references: tuple[str, ...] = ()

    def to_dict(self):
        return asdict(self)


# --- the vocabulary the LLM rules are written in ---------------------------

# Variables that usually carry data a user controls. The optional underscore
# lets one spelling cover both the Python and the JavaScript convention.
_UNTRUSTED_NAMES = (
    r"user_?input|user_?message|user_?query|user_?content|user_?request|user_?prompt|"
    r"user_?text|user_?data|user_?name|user|input|query|payload|question"
)
# Variables that usually carry something secret.
_SENSITIVE_NAMES = (
    r"api_?key|secret|password|passwd|token|credential|private_?key|ssn|"
    r"credit_?card|passport|patient_?id|access_?token|auth_?token|session_?token"
)
# Variables that hold a prompt.
_PROMPT_VARS = (
    r"prompt|system_?prompt|user_?prompt|instructions|system_?message|system_?content"
)
# Variables that hold a model's answer.
_MODEL_OUTPUT = (
    r"completion|response|output|result|reply|answer|content|generation|"
    r"llm_output|model_output|model_response"
)

_RG007 = re.compile(
    r"(?i)\b(?:" + _PROMPT_VARS + r")\s*=\s*(?:"
    r"f?[\"'][^\"'\n]*\{[^{}\n]*\b(?:" + _UNTRUSTED_NAMES + r")\b"
    r"|`[^`\n]*\$\{[^{}\n]*\b(?:" + _UNTRUSTED_NAMES + r")\b[^`\n]*`"
    r"|[\"'][^\"'\n]*[\"']\s*\.\s*format\s*\(\s*\w*(?:" + _UNTRUSTED_NAMES + r")\w*"
    r"|[\"'][^\"'\n]*[\"']\s*\+\s*\w*(?:" + _UNTRUSTED_NAMES + r")\w*"
    r"|(?:" + _PROMPT_VARS + r"|text)\s*\+\s*\w*(?:" + _UNTRUSTED_NAMES + r")\w*"
    r")"
)
_RG008 = re.compile(
    r"(?i)\b(?:" + _PROMPT_VARS + r"|messages|message|context|query)\s*=\s*"
    r"(?:f?[\"'][^\"'\n]*\{[^{}\n]*\b(?:" + _SENSITIVE_NAMES + r")\b"
    r"|`[^`\n]*\$\{[^{}\n]*\b(?:" + _SENSITIVE_NAMES + r")\b[^`\n]*`)"
)
_RG009 = re.compile(
    r"(?i)[\"']?\b(?:allow_tools|allowed_tools|tool_choice|tools)\b[\"']?\s*[:=]\s*\[?\s*"
    r"[\"'](?:all|\*)[\"']"
    r"|[\"']?\b(?:skip_confirmation|auto_approve|autoapprove|dangerously_allow_all)\b"
    r"[\"']?\s*[:=]\s*(?:True|true|1)"
    r"|[\"']?\b(?:human_in_the_loop|require_approval|require_confirmation)\b[\"']?\s*[:=]\s*"
    r"(?:False|false|0)"
    r"|[\"']?\bapproval[a-z_]*\b[\"']?\s*[:=]\s*[\"']?(?:never|off|none)[\"']?"
)
_RG010 = re.compile(r"(?i)\btrust_remote_code\s*=\s*(?:True|true|1)")
_RG011 = re.compile(
    r"(?i)\bfrom_pretrained\(\s*[\"'][^\"'\n]+[\"']\s*\)"
    r"|\bsnapshot_download\(\s*[\"'][^\"'\n]+[\"']\s*\)"
    r"|\brevision\s*=\s*[\"']main[\"']"
)
_RG012 = re.compile(
    r"\b(?:torch\.load|torch\.jit\.load|joblib\.load|dill\.loads?)\s*\("
    r"|\bnp\.load\s*\([^)]*allow_pickle\s*=\s*True"
)
_RG013 = re.compile(
    r"\b(?:requests|httpx)\.(?:get|post|put|patch|delete|request)\s*\("
    r"(?![^)]*\btimeout\s*=)[^)]*\)"
)
_RG014 = re.compile(
    r"(?:chat\.completions\.create|completions\.create|messages\.create|"
    r"generate_content|\.complete)\s*\("
    r"(?![^)]*\b(?:max_tokens|max_output_tokens|max_completion_tokens|num_predict|"
    r"timeout)\b[\"']?\s*[=:])[^)]*\)"
)
_RG015 = re.compile(
    r"\b(?:eval|exec)\s*\(\s*\w*(?:" + _MODEL_OUTPUT + r")\w*\s*\)"
    r"|\b(?:os\.system|subprocess\.\w+|commands\.getoutput)\s*\(\s*"
    r"\w*(?:" + _MODEL_OUTPUT + r")\w*"
)
_RG016 = re.compile(
    r"\b(?:execute|executemany|executescript)\s*\(\s*f?[\"'][^\"'\n]*"
    r"(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b[^\"'\n]*\{[^{}\n]+\}"
    r"|\b(?:execute|executemany|executescript)\s*\(\s*f?[\"'][^\"'\n]*"
    r"(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b[^\"'\n]*\"\s*\+\s*\w+"
)
_RG017 = re.compile(
    r"(?i)\b(?:print|logger?\.\w+|logging\.\w+|console\.log)\s*\(\s*"
    r"(?:system_prompt|system_message|instructions|prompt_history|messages|"
    r"conversation)\s*\)"
)
_RG018 = re.compile(
    r"\b(?:similarity_search|similarity_search_with_score|vector_search|"
    r"search_similarities)\s*\((?![^)]*\bfilters?\b)[^)]*\)"
)

# --- the agent-runtime rules -------------------------------------------------

# An agent that can run code or a shell turns a prompt injection into code
# execution. The utilities below are the ones that hand a model that power.
_RG019 = re.compile(
    r"\b(?:PythonAstREPLTool|PythonREPLTool|PythonREPL|BashProcess|LLMMathChain)\s*\("
)
# An LLM call inside a loop that never leaves. The loop header is the anchor;
# the file has to hold the model call and no exit for the rule to fire.
_RG020 = re.compile(r"^\s*while\s+True\s*:")
_RG020_REQUIRES = (
    re.compile(
        r"(?i)\b(?:chat\.completions\.create|completions\.create|messages\.create|"
        r"generate_content|agent\.(?:run|invoke)|llm\.(?:run|invoke))\b"
    ),
)
_RG020_FORBIDS = (re.compile(r"^[ \t]*(?:break|return)\b", re.MULTILINE),)
# A tool result reaches the model context, so a credential in it is a leak.
_RG021 = re.compile(
    r"\breturn\s*\{[^{}\n]*[\"'](?:api_?key|password|passwd|secret|secret_?key|"
    r"access_?token|refresh_?token|token|private_?key|credential|credentials)"
    r"[\"']\s*:"
)
# A tool description is read by the model as instructions. Hidden directives in
# it are the tool-poisoning attack.
_RG022 = re.compile(
    r"<IMPORTANT>"  # rasgulla: ignore
    r"|(?i:do\s+not\s+mention|do\s+not\s+tell\s+the\s+user|"
    r"ignore\s+(?:all\s+)?previous\s+instructions|"
    r"disregard\s+(?:the\s+)?(?:above|prior|previous)|"
    r"system\s*:\s*you\s+are|"
    r"(?:read|cat|load|parse|open|access)\s+\.env)"
)
# A provider key written into an MCP configuration file.
_RG023 = re.compile(
    r":\s*[\"'](?:sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9\-]{20,}|"
    r"sk-proj-[A-Za-z0-9\-]{20,}|hf_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9\-_]{20,})[\"']"
)
# A hook that pipes a download straight into a shell.
_RG024 = re.compile(r"(?:curl|wget)\s+[^|;#\n]*\|\s*(?:bash|sh|zsh)\b")
# A hook that allows every tool call without looking at it.
_RG025 = re.compile(r"permissionDecision[\"']?\s*[:=]\s*[\"']?allow\b")
# A hook that carries data out in a DNS query.
_RG026 = re.compile(r"\b(?:ping|nslookup|dig|host)\s+[^#\n]*\$")
# A manifest that instructs the agent to read a credential file.
_RG027 = re.compile(
    r"(?i)(?:read|cat|load|parse|open|access|fetch|grep)\s+(?:the\s+)?"
    r"(?:~/\.ssh/id_(?:rsa|ed25519|ecdsa|dsa)\b|~/\.ssh/known_hosts|"
    r"~/\.aws/credentials|~/\.config/gcloud/\S*credential|/etc/shadow|/etc/passwd|"
    r"~/\.npmrc|~/\.pypirc|~/\.docker/config\.json|~/\.kube/config|~/\.netrc|"
    r"\.env(?:\.\w+)?\b|"
    r"(?:browser\s+)?cookies?\s+(?:from|in)\s+(?:Chrome|Firefox|Safari|Edge|~))"
)
# A manifest that instructs the agent to send data to a remote endpoint.
_RG028 = re.compile(
    r"(?i)(?:send|post|forward|upload|exfiltrate|deliver)\s+(?:the\s+)?"
    r"(?:result|output|contents?|response|data|file|secret|token|key|credential|env)\s+"
    r"(?:to|via)\s+https?://"
    r"|(?:include|append|attach)\s+(?:the\s+)?(?:contents?|output)\s+"
    r"(?:in|to)\s+(?:a\s+)?(?:request|webhook|callback)\s+to\s+https?://"
)
# A manifest that smuggles an encoded payload past a human reviewer.
_RG029 = re.compile(
    r"(?i)(?:decode|run|execute|eval)\s+(?:this\s+|the\s+following\s+)?base64[^\n]{0,40}"
    r"[A-Za-z0-9+/]{60,}={0,2}"
    r"|base64\s+-d\s*<<<\s*[\"']?[A-Za-z0-9+/]{60,}={0,2}"
    r"|echo\s+[\"']?[A-Za-z0-9+/]{60,}={0,2}[\"']?\s*\|\s*base64\s+(?:-d|--decode)"
)
# Invisible and bidirectional characters in a file an agent reads as rules.
_RG030 = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
# Every MCP server of a project is enabled without a confirmation.
_RG031 = re.compile(
    r"(?i)[\"']?enableAllProjectMcpServers[\"']?\s*[:=]\s*(?:true|1)"
)
# The permission and sandbox controls of the agent are switched off.
_RG032 = re.compile(
    r"(?i)[\"']?bypassPermissions[\"']?"
    r"|[\"']?allowUnsandboxedCommands[\"']?\s*[:=]\s*(?:true|1)"
    r"|[\"']?enableWeakerNestedSandbox[\"']?\s*[:=]\s*(?:true|1)"
)


def _rule(
    rule_id: str,
    severity: str,
    pattern: re.Pattern,
    message: str,
    recommendation: str,
    confidence: str,
    cwe: str,
    owasp_llm: str = "",
    scope: tuple[str, ...] = (),
    requires: tuple[re.Pattern, ...] = (),
    forbids: tuple[re.Pattern, ...] = (),
) -> Rule:
    return Rule(
        rule_id=rule_id,
        severity=severity,
        pattern=pattern,
        message=message,
        recommendation=recommendation,
        confidence=confidence,
        cwe=cwe,
        owasp_llm=owasp_llm,
        owasp_llm_name=OWASP_LLM_TOP_10.get(owasp_llm, ""),
        references=_references(cwe, owasp_llm),
        scope=scope,
        requires=requires,
        forbids=forbids,
    )


RULES = [
    _rule(
        "RG001",
        "high",
        re.compile(r"\beval\s*\("),
        "Dynamic evaluation detected",
        "Avoid evaluating untrusted input; use a parser or explicit dispatch.",
        "high",
        "CWE-95",
    ),
    _rule(
        "RG002",
        "high",
        re.compile(r"\b(exec|system)\s*\("),
        "Process execution primitive detected",
        "Validate inputs and prefer argument arrays with shell disabled.",
        "high",
        "CWE-78",
    ),
    # TLS verification is disabled in several ecosystems. Only the Python spelling
    # was detected before, which missed the most common real-world cases.
    _rule(
        "RG003",
        "medium",
        re.compile(
            r"verify\s*=\s*False|CERT_NONE|verify\s*:\s*false|rejectUnauthorized\s*:\s*false"  # rasgulla: ignore
            r"|InsecureSkipVerify\s*:\s*true|verify_ssl\s*:\s*false|check_hostname\s*=\s*False"
            r"|_create_unverified_context|SSL_VERIFY_NONE|CURLOPT_SSL_VERIFYPEER\s*,\s*0"  # rasgulla: ignore
            r"|CURLOPT_SSL_VERIFYHOST\s*,\s*0"),
        "TLS certificate verification may be disabled",
        "Keep certificate and hostname verification enabled.",
        "high",
        "CWE-295",
    ),
    _rule(
        "RG004",
        "medium",
        re.compile(
            r"(?i)[\"']?\b(?:password|passwd|secret|api[_-]?key|token)\b[\"']?\s*[:=]\s*"
            r"['\"][^'\"\n]{4,}['\"]"
            r"|(?im:^\s*[\"']?\b(?:password|passwd|secret|api[_-]?key|token)\b[\"']?\s*:\s*"
            r"(?![<\[({])[^\s'\"#]{4,}\s*$)"),
        "Possible hard-coded secret",
        "Move secrets to a managed secret store and rotate exposed credentials.",
        "medium",
        "CWE-798",
        "LLM02",
    ),
    _rule(
        "RG005",
        "high",
        re.compile(r"shell\s*[:=]\s*True|shell\s*:\s*true", re.IGNORECASE),
        "Shell interpreter enabled for a child process",
        "Pass an argument array and leave shell disabled so input cannot reach a command interpreter.",
        "high",
        "CWE-78",
    ),
    _rule(
        "RG006",
        "high",
        re.compile(
            r"\b(?:pickle|cPickle|marshal).loads?\s*\(|\byaml\.(?:load|unsafe_load)\s*\("
            r"|\bnew\s+Function\s*\(|\bunserialize\s*\("),
        "Unsafe deserialization or dynamic code construction",
        "Use a safe loader for untrusted data and never rebuild code from input.",
        "high",
        "CWE-502",
        "LLM05",
    ),
    _rule(
        "RG007",
        "high",
        _RG007,
        "Prompt is built from untrusted input",
        "Keep untrusted input out of the instruction position: delimit it, "
        "validate it, and give the model an explicit data/instruction split.",
        "medium",
        "CWE-1427",
        "LLM01",
    ),
    _rule(
        "RG008",
        "medium",
        _RG008,
        "Sensitive value is placed into a model prompt",
        "Redact secrets and personal data before it reaches the model, or "
        "resolve it server-side after the call.",
        "medium",
        "CWE-201",
        "LLM02",
    ),
    _rule(
        "RG009",
        "high",
        _RG009,
        "Agent is granted unrestricted tools or no confirmation",
        "Allow-list the tools an agent may call and keep a confirmation step "
        "in front of every privileged action.",
        "medium",
        "CWE-250",
        "LLM03",
    ),
    _rule(
        "RG010",
        "high",
        _RG010,
        "Model loading trusts remote code from the hub",
        "Load remote code only from a source you have reviewed and pin, or "
        "disable it and run the model behind a local, trusted entrypoint.",
        "high",
        "CWE-829",
        "LLM04",
    ),
    _rule(
        "RG011",
        "medium",
        _RG011,
        "Model or dataset is fetched without a pinned revision",
        "Pin the exact commit or tag of every model and dataset you fetch, so "
        "a moved branch cannot change what your build loads.",
        "low",
        "CWE-829",
        "LLM04",
    ),
    _rule(
        "RG012",
        "high",
        _RG012,
        "Model or dataset artifact is loaded without verification",
        "Verify the checksum or signature of the artifact before loading it; "
        "do not unpickle a file you did not produce.",
        "high",
        "CWE-502",
        "LLM05",
    ),
    _rule(
        "RG013",
        "medium",
        _RG013,
        "HTTP request is made without an explicit timeout",
        "Set an explicit timeout on every outbound request so a slow peer "
        "cannot hold the worker indefinitely.",
        "medium",
        "CWE-770",
    ),
    _rule(
        "RG014",
        "low",
        _RG014,
        "Model call has neither an output cap nor a timeout",
        "Cap the generated tokens and the call duration, and budget them per "
        "request so one call cannot consume the whole quota.",
        "low",
        "CWE-770",
        "LLM06",
    ),
    _rule(
        "RG015",
        "high",
        _RG015,
        "Model output is handed to an interpreter or a shell",
        "Treat model output as untrusted data: validate it against a schema "
        "before it reaches an interpreter, a shell or a file.",
        "high",
        "CWE-94",
        "LLM10",
    ),
    _rule(
        "RG016",
        "high",
        _RG016,
        "Query is built by string interpolation",
        "Use a parameterized query or a query builder; never concatenate a "
        "value into SQL text.",
        "medium",
        "CWE-89",
    ),
    _rule(
        "RG017",
        "medium",
        _RG017,
        "System prompt or conversation history is logged",
        "Keep the system prompt and the conversation out of logs, or redact "
        "them before they are written.",
        "medium",
        "CWE-532",
        "LLM08",
    ),
    _rule(
        "RG018",
        "low",
        _RG018,
        "Vector retrieval runs without a filter",
        "Scope vector retrieval to the records the caller may read, and log "
        "the query so a leak can be traced.",
        "low",
        "CWE-862",
        "LLM09",
    ),
    _rule(
        "RG019",
        "high",
        _RG019,
        "Agent is given a code or shell execution utility",
        "Give the agent tools that cannot execute code, and keep every "
        "privileged action behind a confirmation the model cannot skip.",
        "high",
        "CWE-94",
        "LLM03",
        scope=(".py",),
    ),
    _rule(
        "RG020",
        "medium",
        _RG020,
        "Model call sits in a loop that has no visible exit",
        "Bound the loop with a step counter, a timeout or an explicit stop "
        "condition, and reject the run when the budget is spent.",
        "low",
        "CWE-835",
        "LLM06",
        scope=(".py",),
        requires=_RG020_REQUIRES,
        forbids=_RG020_FORBIDS,
    ),
    _rule(
        "RG021",
        "high",
        _RG021,
        "Tool result carries a credential back to the model",
        "Strip credential fields from the tool result before it is returned, "
        "and resolve the secret server-side where it is needed.",
        "medium",
        "CWE-522",
        "LLM02",
        scope=(".py",),
    ),
    _rule(
        "RG022",
        "high",
        _RG022,
        "Tool or skill description carries hidden instructions",
        "Remove the hidden directive from the description, and review every "
        "tool and manifest the agent loads as untrusted input.",
        "medium",
        "CWE-1427",
        "LLM01",
        scope=(".py", "manifest"),
    ),
    _rule(
        "RG023",
        "high",
        _RG023,
        "Plaintext provider key in an MCP configuration file",
        "Replace the literal with an environment variable reference or a "
        "secret from a managed store, and rotate the exposed key.",
        "high",
        "CWE-798",
        "LLM04",
        scope=("*mcp*.json", "claude_desktop_config.json"),
    ),
    _rule(
        "RG024",
        "high",
        _RG024,
        "Hook pipes a downloaded script straight into a shell",
        "Download the script, check its checksum and read it, and only then "
        "run it as a separate step.",
        "high",
        "CWE-829",
        "LLM04",
        scope=(".sh", ".bash"),
    ),
    _rule(
        "RG025",
        "high",
        _RG025,
        "Hook allows a tool call without checking it",
        "Check the tool and its arguments in the hook, and deny anything the "
        "hook does not recognize.",
        "medium",
        "CWE-862",
        "LLM03",
        scope=(".sh", ".bash", ".py", ".json"),
    ),
    _rule(
        "RG026",
        "high",
        _RG026,
        "Hook carries data out through a DNS lookup",
        "Resolve only static host names in a hook, and pass the data the hook "
        "needs through an audited channel.",
        "medium",
        "CWE-201",
        "LLM02",
        scope=(".sh", ".bash"),
    ),
    _rule(
        "RG027",
        "high",
        _RG027,
        "Skill instructs the agent to read a credential file",
        "Remove the file-access directive, or scope it to the paths the user "
        "supplies for this task.",
        "medium",
        "CWE-522",
        "LLM02",
        scope=("manifest",),
    ),
    _rule(
        "RG028",
        "high",
        _RG028,
        "Skill instructs the agent to send data to a remote endpoint",
        "Remove the exfiltration directive, and audit the origin of the skill "
        "before it is allowed into the agent's context.",
        "medium",
        "CWE-201",
        "LLM02",
        scope=("manifest",),
    ),
    _rule(
        "RG029",
        "medium",
        _RG029,
        "Skill embeds an encoded payload to decode and run",
        "Write the instruction in plain text, and review the decoded value "
        "before anything runs it.",
        "medium",
        "CWE-506",
        "LLM01",
        scope=("manifest",),
    ),
    _rule(
        "RG030",
        "high",
        _RG030,
        "Agent configuration carries invisible or bidirectional characters",
        "Remove every zero-width and bidirectional control character from the "
        "file, and compare it with the reviewed version.",
        "high",
        "CWE-176",
        "LLM01",
        scope=(".json", ".yaml", ".yml", ".toml", "manifest"),
    ),
    _rule(
        "RG031",
        "high",
        _RG031,
        "Every MCP server of the project is enabled without confirmation",
        "Remove the setting, or list the servers the user has approved so a "
        "new one in a cloned repository cannot start on its own.",
        "high",
        "CWE-862",
        "LLM03",
        scope=(".json", ".yaml", ".yml"),
    ),
    _rule(
        "RG032",
        "high",
        _RG032,
        "Permission or sandbox control of the agent is switched off",
        "Restore the permission check and the sandbox, and run the agent with "
        "the narrowest authority the task needs.",
        "high",
        "CWE-862",
        "LLM03",
        scope=(".json", ".yaml", ".yml"),
    ),
]

# A secret value that is obviously a placeholder is not worth reporting.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)((?:[\"']?\b(?:password|passwd|secret|api[_-]?key|token)\b[\"']?)"
    r"\s*[:=]\s*)(['\"]?)([^\s'\"#,}]{4,})(['\"]?)"
)
_PLACEHOLDER_PREFIXES = (
    "your", "example", "changeme", "change-me", "change_me", "placeholder", "redacted",
    "todo", "dummy", "fake", "sample", "insert", "replace", "xxx",
)
_PLACEHOLDER_VALUES = {"none", "null", "nil", "true", "false", "test", "password", "secret"}

# Characters that are invisible in an editor but are read by a model. They are
# escaped in the evidence, so a report cannot carry a hidden instruction.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")

# A line can opt out of the scanner with an explicit marker.
_SUPPRESSION = re.compile(r"#\s*rasgulla\s*:\s*ignore\b", re.IGNORECASE)


def _secret_value(line: str) -> str | None:
    match = _SECRET_ASSIGNMENT.search(line)
    return match.group(3) if match else None


def _is_placeholder_secret(value: str | None) -> bool:
    if not value:
        return True
    lowered = value.strip().strip("\"'").lower()
    if not lowered or lowered in _PLACEHOLDER_VALUES:
        return True
    if set(lowered) <= {"x", "*", ".", "-", "_"}:
        return True
    return lowered.startswith(_PLACEHOLDER_PREFIXES)


def _redact_secrets(text: str) -> str:
    """Replace a detected secret value so the report cannot leak it."""
    return _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}***{match.group(4)}", text
    )


def _escape_invisible(text: str) -> str:
    """Write invisible characters out, so the report cannot carry them."""
    return _INVISIBLE.sub(lambda match: f"\\u{ord(match.group()):04x}", text)


def _is_ignored(path: Path, root: Path) -> bool:
    # Only directory components below the scan root are ignored, so a project that
    # happens to live under a directory with an ignored name is still scanned.
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        parts = path.parts[:-1]
    return any(part in IGNORED_DIRS for part in parts)


def _display_path(path: Path, base: Path | None) -> str:
    if base:
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            pass
    return str(path)


def is_manifest(path: Path) -> bool:
    """Whether the file is an agent manifest rather than ordinary source.

    An agent reads a skill file, an agent file or an editor rules file as
    instructions. Those files are scanned by the rules that are scoped to
    manifests, and not by the rules that look at source code.
    """
    name = path.name.lower()
    if name in MANIFEST_NAMES:
        return True
    if path.suffix.lower() in MANIFEST_SUFFIXES:
        return True
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in MANIFEST_PATTERNS)


def _scope_matches(rule: Rule, path: Path, manifest: bool) -> bool:
    """Whether the rule applies to this file.

    A rule without a scope applies to every source file. A rule with a scope
    applies to a file when one of its tokens matches: `source` for any source
    file, `manifest` for any agent manifest, an extension such as `.py`, or a
    file-name pattern such as `*mcp*.json`.
    """
    if not rule.scope:
        return not manifest
    name = path.name.lower()
    suffix = path.suffix.lower()
    for token in rule.scope:
        if token == "source":
            if not manifest:
                return True
            continue
        if token == "manifest":
            if manifest:
                return True
            continue
        if token.startswith("."):
            if suffix == token:
                return True
            continue
        if fnmatch.fnmatchcase(name, token.lower()):
            return True
    return False


def _context_matches(rule: Rule, text: str) -> bool:
    """Whether the file holds what the rule needs to be worth reporting.

    A rule that describes an absence cannot decide from one line alone, so it
    states what the file has to contain (`requires`) and what it must not
    contain (`forbids`).
    """
    if rule.requires and not all(pattern.search(text) for pattern in rule.requires):
        return False
    return not (rule.forbids and any(pattern.search(text) for pattern in rule.forbids))


def should_scan(path: Path, root: Path) -> bool:
    """Decide whether a path is in scope.

    Symbolic links are never followed: a link can point outside the project, or
    back at a parent directory.
    """
    if path.is_symlink():
        return False
    if _is_ignored(path, root):
        return False
    return path.suffix.lower() in EXTENSIONS or is_manifest(path)


def analyze_file(path: Path, base: Path | None = None):
    results = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return results
    lines = text.splitlines()
    display = _display_path(path, base)
    manifest = is_manifest(path)
    applicable = [
        rule for rule in RULES
        if _scope_matches(rule, path, manifest) and _context_matches(rule, text)
    ]
    for number, line in enumerate(lines, 1):
        if _SUPPRESSION.search(line):
            continue
        for rule in applicable:
            if not rule.pattern.search(line):
                continue
            evidence = line.strip()[:240]
            if rule.rule_id == "RG004" and _is_placeholder_secret(_secret_value(line)):
                # A placeholder value is not a finding.
                continue
            # Every rule reports the same line, so the secret has to be
            # removed from every evidence, not only from the one of RG004.
            evidence = _escape_invisible(_redact_secrets(evidence))
            results.append(
                Finding(
                    rule.rule_id,
                    rule.severity,
                    rule.message,
                    display,
                    number,
                    evidence,
                    rule.recommendation,
                    rule.confidence,
                    rule.cwe,
                    rule.owasp_llm,
                    rule.owasp_llm_name,
                    rule.references,
                )
            )
    return results


def analyze_path(root: Path):
    root = root.resolve()
    findings = []
    if root.is_file():
        # A file named explicitly is analyzed even when its type is outside the
        # traversal scope: the operator asked for that file.
        files = [root]
    else:
        files = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                name for name in dirnames
                if name not in IGNORED_DIRS and not (Path(dirpath) / name).is_symlink()
            )
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if should_scan(path, root):
                    files.append(path)
    for file in files:
        findings.extend(analyze_file(file, root if root.is_dir() else file.parent))
    return [f.to_dict() for f in findings]
