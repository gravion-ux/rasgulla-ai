"""Detection rules for AI/LLM application risks.

The analyzer is a static, local tool: it reads source text and matches
patterns. These tests pin the rules that matter for LLM-backed code —
prompt injection, excessive agency, supply-chain and output-handling risks —
and the metadata (confidence, CWE, OWASP LLM Top 10) attached to every
finding.
"""
import importlib
from pathlib import Path


def _analyzer():
    """Return the analyzer module at runtime.

    Resolving the module inside the test keeps a missing module a test
    failure instead of a collection error.
    """
    return importlib.import_module("rasgulla.analyzer")


def _scan(tmp_path: Path, name: str, text: str):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return _analyzer().analyze_path(tmp_path)


def _rule_ids(findings):
    return {f["rule_id"] for f in findings}


# --- prompt injection -------------------------------------------------------


def test_a_prompt_built_from_user_input_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.py", 'prompt = f"Answer this: {user_input}"\n')  # rasgulla: ignore
    assert "RG007" in _rule_ids(findings)


def test_a_prompt_formatted_from_user_input_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.py", 'prompt = "Answer {}".format(user_input)\n')  # rasgulla: ignore
    assert "RG007" in _rule_ids(findings)


def test_a_prompt_concatenated_with_user_input_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.py", "prompt = system_prompt + user_message\n")  # rasgulla: ignore
    assert "RG007" in _rule_ids(findings)


def test_a_prompt_injection_finding_maps_to_owasp_llm01(tmp_path: Path):
    findings = _scan(tmp_path, "agent.py", 'prompt = f"Answer this: {user_input}"\n')  # rasgulla: ignore
    finding = next(f for f in findings if f["rule_id"] == "RG007")
    assert finding["owasp_llm"] == "LLM01"
    assert finding["owasp_llm_name"] == "Prompt Injection"
    assert finding["confidence"] == "medium"


# --- sensitive information disclosure ---------------------------------------


def test_a_secret_interpolated_into_a_prompt_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "bot.py", 'prompt = f"Check this key {api_key}"\n')  # rasgulla: ignore
    assert "RG008" in _rule_ids(findings)


def test_a_sensitive_data_finding_maps_to_owasp_llm02(tmp_path: Path):
    findings = _scan(tmp_path, "bot.py", 'prompt = f"Check this key {api_key}"\n')  # rasgulla: ignore
    finding = next(f for f in findings if f["rule_id"] == "RG008")
    assert finding["owasp_llm"] == "LLM02"


# --- excessive agency -------------------------------------------------------


def test_an_agent_with_unrestricted_tools_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.py", 'agent = Agent(allow_tools=["all"])\n')  # rasgulla: ignore
    assert "RG009" in _rule_ids(findings)


def test_an_agent_that_skips_confirmation_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.py", "agent = Agent(skip_confirmation=True)\n")  # rasgulla: ignore
    assert "RG009" in _rule_ids(findings)


def test_an_excessive_agency_finding_maps_to_owasp_llm03(tmp_path: Path):
    findings = _scan(tmp_path, "agent.py", 'agent = Agent(allow_tools=["all"])\n')  # rasgulla: ignore
    finding = next(f for f in findings if f["rule_id"] == "RG009")
    assert finding["owasp_llm"] == "LLM03"


# --- supply chain -----------------------------------------------------------


def test_trusting_remote_code_is_detected(tmp_path: Path):
    findings = _scan(
        tmp_path,
        "load.py",
        'model = AutoModel.from_pretrained("acme/model", trust_remote_code=True)\n',  # rasgulla: ignore
    )
    assert "RG010" in _rule_ids(findings)


def test_an_unpinned_model_revision_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "load.py", 'model = AutoModel.from_pretrained("acme/model")\n')  # rasgulla: ignore
    assert "RG011" in _rule_ids(findings)


def test_a_supply_chain_finding_maps_to_owasp_llm04(tmp_path: Path):
    findings = _scan(
        tmp_path,
        "load.py",
        'model = AutoModel.from_pretrained("acme/model", trust_remote_code=True)\n',  # rasgulla: ignore
    )
    finding = next(f for f in findings if f["rule_id"] == "RG010")
    assert finding["owasp_llm"] == "LLM04"


# --- poisoning --------------------------------------------------------------


def test_loading_an_unverified_artifact_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "train.py", "state = torch.load('model.pt')\n")  # rasgulla: ignore
    assert "RG012" in _rule_ids(findings)


def test_a_poisoning_finding_maps_to_owasp_llm05(tmp_path: Path):
    findings = _scan(tmp_path, "train.py", "state = torch.load('model.pt')\n")  # rasgulla: ignore
    finding = next(f for f in findings if f["rule_id"] == "RG012")
    assert finding["owasp_llm"] == "LLM05"


# --- unbounded consumption ---------------------------------------------------


def test_a_request_without_a_timeout_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "api.py", "response = requests.post(url, json=payload)\n")  # rasgulla: ignore
    assert "RG013" in _rule_ids(findings)


def test_a_request_with_a_timeout_is_not_reported(tmp_path: Path):
    findings = _scan(tmp_path, "api.py", "response = requests.post(url, json=payload, timeout=10)\n")
    assert "RG013" not in _rule_ids(findings)


def test_an_llm_call_without_an_output_cap_is_detected(tmp_path: Path):
    findings = _scan(
        tmp_path,
        "api.py",
        'reply = client.chat.completions.create(model="m", messages=msgs)\n',  # rasgulla: ignore
    )
    assert "RG014" in _rule_ids(findings)


def test_an_unbounded_consumption_finding_maps_to_owasp_llm06(tmp_path: Path):
    findings = _scan(
        tmp_path,
        "api.py",
        'reply = client.chat.completions.create(model="m", messages=msgs)\n',  # rasgulla: ignore
    )
    finding = next(f for f in findings if f["rule_id"] == "RG014")
    assert finding["owasp_llm"] == "LLM06"


# --- improper output handling -----------------------------------------------


def test_model_output_passed_to_an_interpreter_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "run.py", "exec(completion)\n")  # rasgulla: ignore
    assert "RG015" in _rule_ids(findings)


def test_model_output_passed_to_a_shell_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "run.py", "subprocess.run(model_output, shell=True)\n")  # rasgulla: ignore
    assert "RG015" in _rule_ids(findings)


def test_an_improper_output_handling_finding_maps_to_owasp_llm10(tmp_path: Path):
    findings = _scan(tmp_path, "run.py", "exec(completion)\n")  # rasgulla: ignore
    finding = next(f for f in findings if f["rule_id"] == "RG015")
    assert finding["owasp_llm"] == "LLM10"


def test_a_query_built_by_interpolation_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "db.py", 'cursor.execute(f"SELECT * FROM t WHERE name = {answer}")\n')  # rasgulla: ignore
    assert "RG016" in _rule_ids(findings)


# --- hidden context exposure ------------------------------------------------


def test_a_logged_system_prompt_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "bot.py", "logging.info(system_prompt)\n")  # rasgulla: ignore
    assert "RG017" in _rule_ids(findings)


def test_a_hidden_context_finding_maps_to_owasp_llm08(tmp_path: Path):
    findings = _scan(tmp_path, "bot.py", "logging.info(system_prompt)\n")  # rasgulla: ignore
    finding = next(f for f in findings if f["rule_id"] == "RG017")
    assert finding["owasp_llm"] == "LLM08"


# --- vector and embedding weaknesses ----------------------------------------


def test_vector_retrieval_without_a_filter_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "rag.py", "docs = vectorstore.similarity_search(query)\n")  # rasgulla: ignore
    assert "RG018" in _rule_ids(findings)


def test_a_vector_finding_maps_to_owasp_llm09(tmp_path: Path):
    findings = _scan(tmp_path, "rag.py", "docs = vectorstore.similarity_search(query)\n")  # rasgulla: ignore
    finding = next(f for f in findings if f["rule_id"] == "RG018")
    assert finding["owasp_llm"] == "LLM09"


# --- the metadata contract --------------------------------------------------


def test_every_finding_carries_a_confidence_and_a_cwe(tmp_path: Path):
    findings = _scan(tmp_path, "app.py", "eval(user_input)\n")  # rasgulla: ignore
    for finding in findings:
        assert finding["confidence"] in {"high", "medium", "low"}
        assert finding["cwe"].startswith("CWE-")


def test_rule_metadata_is_exposed():
    module = _analyzer()
    by_id = {rule.rule_id: rule for rule in module.RULES}
    assert by_id["RG007"].owasp_llm == "LLM01"
    assert by_id["RG015"].owasp_llm == "LLM10"
    assert by_id["RG001"].owasp_llm == ""
    assert by_id["RG001"].cwe == "CWE-95"


def test_owasp_llm_identifiers_are_valid():
    module = _analyzer()
    valid = {f"LLM{index:02d}" for index in range(1, 11)}
    for rule in module.RULES:
        if rule.owasp_llm:
            assert rule.owasp_llm in valid, rule.rule_id


def test_rule_ids_are_unique():
    module = _analyzer()
    ids = [rule.rule_id for rule in module.RULES]
    assert len(ids) == len(set(ids))


def test_a_rule_still_unpacks_as_the_original_five_values():
    """The rule table grew metadata; the legacy five-value unpacking that
    earlier versions of the tool relied on keeps working."""
    module = _analyzer()
    for rule in module.RULES:
        rule_id, severity, pattern, message, recommendation = rule
        assert rule_id.startswith("RG")
        assert severity in {"high", "medium", "low", "info"}
        assert pattern.search is not None
        assert message
        assert recommendation


# --- the CWE mapping policy -------------------------------------------------

# The CWE identifiers a rule may map to. Every entry was checked against the
# "Vulnerability Mapping Notes" on cwe.mitre.org, which classifies an
# identifier as ALLOWED, ALLOWED-WITH-REVIEW or DISCOURAGED ("this CWE ID
# should not be used to map to real-world vulnerabilities"). A rule table is
# exactly the kind of mapping those notes warn about, so identifiers MITRE
# discourages — CWE-74, CWE-200, CWE-400, CWE-668 among them — are not used.
#
# A new identifier has to be reviewed against those notes and added here
# deliberately, so it can never slip in unnoticed.
APPROVED_CWE = {
    "CWE-89",  # SQL Injection                                       (allowed)
    "CWE-94",  # Improper Control of Generation of Code             (allowed with review)
    "CWE-95",  # Eval Injection                                      (allowed)
    "CWE-78",  # OS Command Injection                                (allowed)
    "CWE-176",  # Improper Handling of Unicode Encoding              (allowed)
    "CWE-201",  # Insertion of Sensitive Information Into Sent Data  (allowed)
    "CWE-250",  # Execution with Unnecessary Privileges              (allowed)
    "CWE-295",  # Improper Certificate Validation                    (allowed)
    "CWE-502",  # Deserialization of Untrusted Data                  (allowed)
    "CWE-506",  # Embedded Malicious Code                            (allowed)
    "CWE-522",  # Insufficiently Protected Credentials               (allowed)
    "CWE-532",  # Insertion of Sensitive Information into Log File   (allowed)
    "CWE-770",  # Allocation of Resources Without Limits or Throttling (allowed)
    "CWE-798",  # Use of Hard-coded Credentials                      (allowed with review)
    "CWE-829",  # Inclusion of Functionality from Untrusted Control Sphere (allowed)
    "CWE-835",  # Loop with Unreachable Exit Condition               (allowed)
    "CWE-862",  # Missing Authorization                               (allowed with review)
    "CWE-1427",  # Improper Neutralization of Input Used for LLM Prompting (allowed)
}


def test_no_rule_maps_to_a_cwe_mitre_discourages():
    module = _analyzer()
    for rule in module.RULES:
        assert rule.cwe in APPROVED_CWE, f"{rule.rule_id} maps to {rule.cwe}"


def test_the_llm_rules_map_to_the_most_specific_cwe():
    """Each LLM rule maps to the CWE that names its weakness most precisely,
    including the two identifiers MITRE wrote for this class of problem."""
    module = _analyzer()
    by_id = {rule.rule_id: rule for rule in module.RULES}
    assert by_id["RG007"].cwe == "CWE-1427"
    assert by_id["RG008"].cwe == "CWE-201"
    assert by_id["RG009"].cwe == "CWE-250"
    assert by_id["RG013"].cwe == "CWE-770"
    assert by_id["RG014"].cwe == "CWE-770"
    assert by_id["RG018"].cwe == "CWE-862"


# --- configuration files carry the same settings as code ---------------------


def test_an_unrestricted_tool_list_in_a_json_config_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.json", '{"allow_tools": ["all"]}\n')  # rasgulla: ignore
    assert "RG009" in _rule_ids(findings)


def test_an_agent_that_skips_confirmation_in_a_json_config_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.json", '{"skip_confirmation": true}\n')  # rasgulla: ignore
    assert "RG009" in _rule_ids(findings)


def test_a_config_that_drops_the_approval_requirement_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.yaml", "require_approval: false\n")  # rasgulla: ignore
    assert "RG009" in _rule_ids(findings)


def test_a_config_that_keeps_the_approval_requirement_is_not_reported(tmp_path: Path):
    findings = _scan(tmp_path, "agent.yaml", "require_approval: true\n")
    assert "RG009" not in _rule_ids(findings)


def test_a_json_config_that_keeps_confirmation_is_not_reported(tmp_path: Path):
    findings = _scan(tmp_path, "agent.json", '{"skip_confirmation": false}\n')
    assert "RG009" not in _rule_ids(findings)


# --- a secret must never reach the report, whichever rule found it -----------


def test_a_secret_is_redacted_from_every_finding_on_the_same_line(tmp_path: Path):
    """The evidence of a line is shared by every rule that matched it, so the
    redaction has to apply to all of them and not only to the secret rule."""
    findings = _scan(
        tmp_path,
        "app.py",
        'api_key = "sk-live-9f8e7d6c5b4a"; prompt = f"Use {user_input}"\n',  # rasgulla: ignore
    )
    assert findings
    for finding in findings:
        assert "sk-live-9f8e7d6c5b4a" not in finding["evidence"]
    assert any(finding["rule_id"] == "RG007" for finding in findings)


# --- the same weakness in another language -----------------------------------


def test_a_javascript_template_literal_prompt_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.js", "const prompt = `Answer ${userInput}`;\n")  # rasgulla: ignore
    assert "RG007" in _rule_ids(findings)


def test_a_javascript_concatenated_prompt_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.js", 'const prompt = "Answer " + userInput;\n')  # rasgulla: ignore
    assert "RG007" in _rule_ids(findings)


def test_a_javascript_prompt_carrying_a_secret_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "agent.js", "const prompt = `Your key is ${apiKey}`;\n")  # rasgulla: ignore
    assert "RG008" in _rule_ids(findings)


def test_a_javascript_prompt_without_untrusted_input_is_not_reported(tmp_path: Path):
    findings = _scan(tmp_path, "agent.js", "const prompt = `Answer the question`;\n")
    assert "RG007" not in _rule_ids(findings)


# --- a guard that is present must silence the rule that looks for its absence


def test_a_model_call_with_a_nested_output_cap_is_not_reported(tmp_path: Path):
    findings = _scan(
        tmp_path,
        "api.py",
        'reply = client.chat.completions.create(model="m", extra_body={"max_tokens": 10})\n',
    )
    assert "RG014" not in _rule_ids(findings)


def test_a_model_call_with_an_output_cap_is_not_reported(tmp_path: Path):
    findings = _scan(
        tmp_path,
        "api.py",
        'reply = client.chat.completions.create(model="m", messages=msgs, max_tokens=10)\n',
    )
    assert "RG014" not in _rule_ids(findings)


def test_vector_retrieval_with_a_filters_argument_is_not_reported(tmp_path: Path):
    findings = _scan(
        tmp_path,
        "rag.py",
        'docs = vectorstore.similarity_search(query, filters={"tenant": tenant})\n',
    )
    assert "RG018" not in _rule_ids(findings)


def test_vector_retrieval_with_a_filter_is_not_reported(tmp_path: Path):
    findings = _scan(
        tmp_path,
        "rag.py",
        'docs = vectorstore.similarity_search(query, filter={"tenant": tenant})\n',
    )
    assert "RG018" not in _rule_ids(findings)


def test_a_model_output_passed_to_a_parser_is_not_reported(tmp_path: Path):
    findings = _scan(tmp_path, "run.py", "data = json.loads(completion)\n")
    assert "RG015" not in _rule_ids(findings)


def test_a_parameterized_query_is_not_reported(tmp_path: Path):
    findings = _scan(
        tmp_path,
        "db.py",
        'cursor.execute("SELECT * FROM t WHERE name = ?", (user_name,))\n',
    )
    assert "RG016" not in _rule_ids(findings)


# --- a hard-coded secret in a JSON file --------------------------------------


def test_a_secret_under_a_quoted_json_key_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "settings.json", '{"api_key": "s3cr3t-value-here"}\n')  # rasgulla: ignore
    assert "RG004" in _rule_ids(findings)


def test_a_secret_under_an_uppercase_key_is_detected(tmp_path: Path):
    findings = _scan(tmp_path, "settings.py", 'API_KEY = "s3cr3t-value-here"\n')  # rasgulla: ignore
    assert "RG004" in _rule_ids(findings)


def test_a_secret_under_a_quoted_json_key_is_never_copied_into_the_report(tmp_path: Path):
    findings = _scan(tmp_path, "settings.json", '{"api_key": "s3cr3t-value-here"}\n')  # rasgulla: ignore
    evidence = " ".join(finding["evidence"] for finding in findings)
    assert "s3cr3t-value-here" not in evidence


# --- the rule object keeps its original shape --------------------------------


def test_a_rule_still_indexes_like_the_original_five_value_tuple():
    """The rule table grew metadata; a caller that indexed a rule as the
    five-value tuple it used to be keeps working."""
    module = _analyzer()
    rule = module.RULES[0]
    assert rule[0] == rule.rule_id
    assert rule[1] == rule.severity
    assert rule[2] is rule.pattern
    assert rule[3] == rule.message
    assert rule[4] == rule.recommendation
    assert len(rule) == 5


def test_a_rule_index_outside_the_five_values_raises():
    import pytest

    module = _analyzer()
    with pytest.raises(IndexError):
        module.RULES[0][5]


# --- the published OWASP taxonomy ---------------------------------------------


EXPECTED_OWASP_TITLES = {
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


def test_the_owasp_titles_are_the_published_ones():
    assert _analyzer().OWASP_LLM_TOP_10 == EXPECTED_OWASP_TITLES


def test_every_rule_carries_the_published_title_of_its_category():
    module = _analyzer()
    for rule in module.RULES:
        if rule.owasp_llm:
            assert rule.owasp_llm_name == EXPECTED_OWASP_TITLES[rule.owasp_llm], rule.rule_id
