"""Offline test-plan generation.

The plan is derived from the findings with fixed templates. It is deterministic,
needs no model provider, and never executes anything: every step is a local
check the operator performs by hand.
"""
from __future__ import annotations

__all__ = ["RULE_TEMPLATES", "build_test_plan"]

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

RULE_TEMPLATES = {
    "RG001": {
        "objective": "Confirm that no untrusted input reaches dynamic evaluation.",
        "steps": [
            "Locate every call site of the flagged function.",
            "Trace each argument back to its source.",
            "Replace the dynamic call with an explicit dispatch table.",
        ],
        "verification": "A unit test feeds the previously accepted input and expects a rejection.",
    },
    "RG002": {
        "objective": "Confirm that process execution cannot be influenced by untrusted input.",
        "steps": [
            "List the arguments passed to the process call.",
            "Check whether any argument is built from user or network data.",
            "Use an argument array and validate every element.",
        ],
        "verification": "A test passes a metacharacter payload and expects it to be treated as data.",
    },
    "RG003": {
        "objective": "Confirm that TLS certificate and hostname verification stay enabled.",
        "steps": [
            "Find the client configuration that disables verification.",
            "Restore the default verification settings.",
            "Check the trust store that the client uses.",
        ],
        "verification": "A test against a self-signed endpoint expects the connection to fail.",
    },
    "RG004": {
        "objective": "Confirm that no credential is stored in the repository.",
        "steps": [
            "Rotate the exposed credential before removing it from the file.",
            "Move the value into a managed secret store.",
            "Add the file to the secret scanner of the pipeline.",
        ],
        "verification": "A repository scan reports no hard-coded credential.",
    },
    "RG005": {
        "objective": "Confirm that child processes never run through a command interpreter.",
        "steps": [
            "Find the call that enables the shell.",
            "Pass an argument array and disable the shell.",
            "Validate each argument against an allow-list.",
        ],
        "verification": "A test passes a shell metacharacter and expects it to be treated as data.",
    },
    "RG006": {
        "objective": "Confirm that untrusted data is never deserialized into executable state.",
        "steps": [
            "Locate the deserialization call and its input source.",
            "Switch to a safe loader or a fixed schema.",
            "Reject input that does not match the expected schema.",
        ],
        "verification": "A test feeds a crafted payload and expects a rejection, not execution.",
    },
}


def build_test_plan(findings, max_steps: int = 50) -> list[dict]:
    """Return an ordered local test plan for the given findings.

    The plan is ordered by severity, then by rule, then by location, so the
    same findings always produce the same plan.
    """
    if not isinstance(findings, (list, tuple)):
        return []
    entries = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        rule_id = finding.get("rule_id")
        template = RULE_TEMPLATES.get(rule_id)
        if template is None:
            continue
        path = finding.get("path")
        line = finding.get("line")
        location = f"{path}:{line}" if path and line else str(path or "unknown")
        entries.append(
            {
                "rule_id": rule_id,
                "severity": finding.get("severity", "medium"),
                "location": location,
                "objective": template["objective"],
                "steps": list(template["steps"]),
                "verification": template["verification"],
            }
        )
    entries.sort(key=lambda entry: (SEVERITY_ORDER.get(entry["severity"], 9), entry["rule_id"],
                                     entry["location"]))
    if max_steps is not None and max_steps >= 0:
        entries = entries[:max_steps]
    return entries
