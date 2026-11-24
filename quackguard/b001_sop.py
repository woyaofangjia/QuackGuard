import re

from .errors import AuditError


def threshold_from_rule(rule):
    matches = re.findall(
        r"包装最长边不小于\s*([0-9]+(?:\.[0-9]+)?)\s*毫米",
        rule["rule_text"],
    )
    if len(matches) != 1:
        raise AuditError(
            "invalid_rule",
            rule["rule_id"] + "@" + rule["version"] + "必须有唯一附签阈值",
        )
    value = matches[0]
    return float(value) if "." in value else int(value)


def _rule_quotes(rule_text):
    paragraphs = rule_text.split("\n\n")
    return {
        "applicability": paragraphs[0],
        "requirements": {
            str(index): paragraph
            for index, paragraph in enumerate(paragraphs[1:], 1)
        },
    }


def _general_quotes(general):
    quotes = {}
    for number in ("1", "2", "3", "4", "5"):
        match = re.search(
            rf"^## {number}\..*?(?=^## [1-9]\.|\Z)",
            general,
            flags=re.MULTILINE | re.DOTALL,
        )
        if match:
            quotes[number] = match.group(0).strip()
    return quotes


def build_implementation(rule, source_hash, artifact_version, general):
    threshold = threshold_from_rule(rule)
    sop = {
        "source_quotes": {
            "rule": _rule_quotes(rule["rule_text"]),
            "general": _general_quotes(general),
        },
        "applicability": {
            "all_of": {
                "market": "Q",
                "category": "桌面灯",
                "power_mode": "可充电",
            },
            "known_false": "pass/not_applicable",
            "unknown_without_known_false": "ambiguous/unknown",
            "do_not_infer_from_images": True,
        },
        "facts_to_check": {
            "label": ["model", "material_code", "carrier", "attachment"],
            "certificate": [
                "material_code",
                "covered_models",
                "valid_from",
                "valid_to",
            ],
            "document_state": ["voided", "inspection_complete", "readability"],
        },
        "evidence_requirements": {
            "same_label_for_model_and_code": True,
            "outer_package_threshold_mm": threshold,
            "attached_tag_requires_visible_connection": True,
            "certificate_dates_include_both_endpoints": True,
            "voided_documents_cannot_satisfy_alone": True,
            "provenance": "parser_injects_material_id_page_index_and_role",
        },
        "normalization": {
            "model": "NFKC, trim, ignore Latin case, ASCII spaces and hyphens",
            "model_not_equivalent": ["O/0", "I/1"],
            "material_code": "NFKC and trim only",
            "material_code_format": r"^QLAB-[0-9]{6}$",
            "dates": "strict YYYY-MM-DD Gregorian dates",
        },
        "decision_order": [
            "applicability",
            "defined_material_conflicts",
            "proven_reject",
            "outcome_changing_unknown",
            "pass",
        ],
        "label_conditions": {
            "pass": (
                "all applicable requirements are satisfied without proven "
                "violations or outcome-changing unknowns"
            ),
            "reject": (
                "at least one applicable requirement is proven unsatisfied "
                "after defined conflicts"
            ),
            "ambiguous": (
                "defined conflict or outcome-changing evidence is unknown"
            ),
        },
        "clarifications": [
            "package size, declared model and review date are evidence "
            "inputs, not applicability conditions",
            "package/certificate code mismatch is reject, not the defined "
            "same-package label conflict",
            "missing fields require a complete clear inspection scope; "
            "unreadable is not missing",
            "do not measure package dimensions from images or select a "
            "version from dates",
        ],
    }
    build_policy = {
        "atomic_capabilities": [
            "applicability_evaluation",
            "independent_material_fact_extraction",
            "business_normalization",
            "conflict_detection",
            "deterministic_pairwise_decision",
            "evidence_and_trace_validation",
        ],
        "reuse_decisions": {
            "official_contracts": "reuse_without_modification",
            "model_gateway": "reuse_for_single_material_calls",
            "generic_model_review": "retain_for_other_rule_families",
            "decision_logic": "pure_python_no_model_call",
        },
        "data_requirements": {
            "task_fields": ["review_date", "metadata", "materials"],
            "visual_facts": "strict_json_per_material",
            "answer_data_in_production": False,
        },
        "labeling_plan": {
            "development_cases": "public_rule_and_visible_fact_regression",
            "independent_cases": (
                "boundary_conflict_void_and_version_isolation_tests"
            ),
            "hidden_set_quality_claim": False,
        },
        "model_and_tool_config": {
            "visual_attempts_per_material": 2,
            "temperature": 0,
            "decision_engine": "quackguard.b001.review",
        },
        "evaluation_plugins": ["quackguard.conclusion"],
        "quality_gates": {
            "contract_errors": 0,
            "execution_errors": 0,
            "required_tests": [
                "79_80_99_100_mm",
                "date_boundaries_and_invalid_dates",
                "voided_and_conflicting_documents",
                "version_isolation",
            ],
        },
        "resource_budget": {
            "maximum_visual_calls": "two_per_relevant_material",
            "decision_model_calls": 0,
        },
        "promotion_conditions": [
            "artifact_hash_and_prediction_contracts_pass",
            "all_unit_and_public_regression_tests_pass",
        ],
        "rollback_conditions": [
            "version_cross_contamination",
            "untraced_visual_facts",
            "contract_or_regression_failure",
        ],
    }
    return {
        "processor": "b001",
        "sop": sop,
        "build_policy": build_policy,
        "artifact": {
            "version": artifact_version,
            "source_hash": source_hash,
            "dependencies": {
                "decision_module": "quackguard.b001",
                "extraction_module": "quackguard.b001_facts",
                "protocol": "1.3.1",
            },
        },
    }
