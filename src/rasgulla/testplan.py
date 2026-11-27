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
    "RG007": {
        "objective": "Confirm that untrusted input cannot act as an instruction to the model.",
        "steps": [
            "List every place where user-controlled text is placed into a prompt.",
            "Separate the data from the instructions with an explicit delimiter.",
            "Add a check that rejects a prompt whose instruction section was altered.",
        ],
        "verification": "A test sends an injection payload and expects the model to treat it as data.",
    },
    "RG008": {
        "objective": "Confirm that no secret or personal record is sent to the model.",
        "steps": [
            "Find every value that reaches the prompt and name its origin.",
            "Redact credentials and personal data before the call, or resolve them server-side.",
            "Check the provider's retention settings for the data that is still sent.",
        ],
        "verification": "A test asserts that the recorded request contains no secret material.",
    },
    "RG009": {
        "objective": "Confirm that the agent can only act inside its granted authority.",
        "steps": [
            "List the tools the agent can reach.",
            "Restrict them to an allow-list and require confirmation for privileged ones.",
            "Check that the confirmation step cannot be skipped by configuration.",
        ],
        "verification": "A test asks the agent to call a tool outside the allow-list and expects a refusal.",
    },
    "RG010": {
        "objective": "Confirm that model loading never executes unreviewed remote code.",
        "steps": [
            "Find every model load that trusts remote code.",
            "Review the repository that code comes from, or turn the option off.",
            "Pin the revision the load resolves to.",
        ],
        "verification": "A test loads the model with remote code disabled and expects it to work.",
    },
    "RG011": {
        "objective": "Confirm that every fetched model and dataset is pinned.",
        "steps": [
            "List the model and dataset identifiers the build downloads.",
            "Pin each one to an immutable revision or a verified checksum.",
            "Record the pins so a dependency update cannot change them silently.",
        ],
        "verification": "A build with the network disabled resolves every pinned artifact from cache.",
    },
    "RG012": {
        "objective": "Confirm that no artifact is loaded before it has been verified.",
        "steps": [
            "Find where the artifact comes from.",
            "Verify its checksum or signature before it is loaded.",
            "Reject the load when the verification fails.",
        ],
        "verification": "A test loads a tampered artifact and expects the load to be refused.",
    },
    "RG013": {
        "objective": "Confirm that every outbound request is bounded in time.",
        "steps": [
            "List the outbound requests the service makes.",
            "Give each one an explicit timeout that matches its budget.",
            "Decide what happens when the timeout is reached.",
        ],
        "verification": "A test points the client at a hanging endpoint and expects a timeout error.",
    },
    "RG014": {
        "objective": "Confirm that no single model call can consume an unbounded budget.",
        "steps": [
            "Find the inference calls that set neither an output cap nor a timeout.",
            "Cap the generated tokens and the call duration on each one.",
            "Apply a per-request quota in front of the provider.",
        ],
        "verification": "A test asks for an unbounded completion and expects it to stop at the cap.",
    },
    "RG015": {
        "objective": "Confirm that model output never reaches an interpreter unchecked.",
        "steps": [
            "Trace the model's answer to every consumer.",
            "Validate the answer against a fixed schema before it is used.",
            "Keep the answer out of shells, interpreters and source files.",
        ],
        "verification": "A test returns a hostile model answer and expects it to be rejected.",
    },
    "RG016": {
        "objective": "Confirm that no query is built from a value by concatenation.",
        "steps": [
            "Find every query that interpolates a value into SQL text.",
            "Rewrite it as a parameterized query.",
            "Check that identifiers, which cannot be parameterized, come from an allow-list.",
        ],
        "verification": "A test sends a quote character as a value and expects the query to stay intact.",
    },
    "RG017": {
        "objective": "Confirm that the system prompt and the conversation stay out of the logs.",
        "steps": [
            "Find every logger that receives the prompt or the message list.",
            "Remove the value, or redact it before it is written.",
            "Check that the existing log storage is covered too.",
        ],
        "verification": "A test runs a conversation and asserts that the log holds no prompt text.",
    },
    "RG018": {
        "objective": "Confirm that vector retrieval only returns records the caller may read.",
        "steps": [
            "Find every retrieval call that runs without a filter.",
            "Scope the query to the records of the current tenant or user.",
            "Log the retrieval so an unintended match can be traced.",
        ],
        "verification": "A test retrieves as a second tenant and expects none of the first tenant's records.",
    },
    "RG019": {
        "objective": "Confirm that the agent cannot reach a code or shell interpreter.",
        "steps": [
            "List the tools the agent is given and mark the ones that execute code.",
            "Remove those tools, or put them behind a confirmation the model cannot skip.",
            "Check that no other tool can reach a shell by another route.",
        ],
        "verification": "A test asks the agent to run a command and expects the call to be refused.",
    },
    "RG020": {
        "objective": "Confirm that every agent loop has a budget it cannot exceed.",
        "steps": [
            "Find the loop the model call sits in.",
            "Bound it with a step counter, a timeout or an explicit stop condition.",
            "Decide what the agent does when the budget is spent.",
        ],
        "verification": "A test lets the agent answer twice and expects the third call to be refused.",
    },
    "RG021": {
        "objective": "Confirm that no credential reaches the model through a tool result.",
        "steps": [
            "List the fields every tool returns.",
            "Remove the credential fields, or resolve the secret server-side.",
            "Check the logs the tool result is written to as well.",
        ],
        "verification": "A test calls the tool and expects no credential in the recorded result.",
    },
    "RG022": {
        "objective": "Confirm that no tool description carries hidden instructions.",
        "steps": [
            "Read every tool description and every manifest the agent loads.",
            "Remove the directives that talk to the model instead of describing the tool.",
            "Treat the description as untrusted input when it is reviewed.",
        ],
        "verification": "A test loads the tool and expects the hidden directive to be absent.",
    },
    "RG023": {
        "objective": "Confirm that no provider key is written into an MCP configuration file.",
        "steps": [
            "Find the key in the configuration file.",
            "Replace it with an environment variable reference or a managed secret.",
            "Rotate the key, because it was in a file that may have been shared.",
        ],
        "verification": "A repository scan reports no credential in any MCP configuration file.",
    },
    "RG024": {
        "objective": "Confirm that no hook runs a script it has not read.",
        "steps": [
            "Find the download that is piped into a shell.",
            "Download the script to a file, verify its checksum, and read it.",
            "Run it as a separate step with its own approval.",
        ],
        "verification": "A test points the hook at a recording endpoint and expects no script to run.",
    },
    "RG025": {
        "objective": "Confirm that the hook decides, rather than waves every call through.",
        "steps": [
            "List the tools and arguments the hook allows.",
            "Check them against an allow-list and deny everything else.",
            "Make sure the deny path cannot be reached by an error in the check.",
        ],
        "verification": "A test offers the hook a tool it does not know and expects a denial.",
    },
    "RG026": {
        "objective": "Confirm that no hook carries data out through a name lookup.",
        "steps": [
            "Find the lookup whose host name is built from a value.",
            "Use a static host name, or move the data to an audited channel.",
            "Check that no other command smuggles data into a query.",
        ],
        "verification": "A test runs the hook and expects no lookup of a generated host name.",
    },
    "RG027": {
        "objective": "Confirm that no skill sends the agent to a credential file.",
        "steps": [
            "Read the file-access directives of the skill.",
            "Remove them, or scope them to the paths the user supplies.",
            "Check the other manifests the agent loads for the same directive.",
        ],
        "verification": "A test loads the skill and expects no credential file to be read.",
    },
    "RG028": {
        "objective": "Confirm that no skill sends data to an endpoint it does not own.",
        "steps": [
            "Find the endpoint the skill names and who controls it.",
            "Remove the directive, or replace it with an approved internal endpoint.",
            "Audit the origin of the skill before it is allowed into the agent's context.",
        ],
        "verification": "A test loads the skill and expects no request to the named endpoint.",
    },
    "RG029": {
        "objective": "Confirm that no skill smuggles an encoded payload past review.",
        "steps": [
            "Find the encoded blob and decode it.",
            "Write the instruction in plain text, or remove it.",
            "Check the decoded value before anything runs it.",
        ],
        "verification": "A test reviews the manifest and expects no encoded payload.",
    },
    "RG030": {
        "objective": "Confirm that no agent configuration hides characters in the text.",
        "steps": [
            "Find every zero-width and bidirectional character in the file.",
            "Remove them and compare the file with the reviewed version.",
            "Add the check to the review of every rules file the project accepts.",
        ],
        "verification": "A test reads the file and expects no invisible character in it.",
    },
    "RG031": {
        "objective": "Confirm that a project cannot start MCP servers on its own.",
        "steps": [
            "Find the setting that enables every server of the project.",
            "Remove it, or list the servers the user has approved.",
            "Check the settings of every other project the machine opens.",
        ],
        "verification": "A test opens a project with a new server and expects it not to start.",
    },
    "RG032": {
        "objective": "Confirm that the agent runs with its permission checks in place.",
        "steps": [
            "Find the settings that switch off the permission or the sandbox.",
            "Restore them and keep the narrowest authority the task needs.",
            "Check that no other settings file turns them off again.",
        ],
        "verification": "A test asks the agent for a privileged action and expects the permission check.",
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
                "confidence": finding.get("confidence", "medium"),
                "cwe": finding.get("cwe", ""),
                "owasp_llm": finding.get("owasp_llm", ""),
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
