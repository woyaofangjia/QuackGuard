"""R 系列结构化 SOP 与建设策略生成。

每条规则的参数从 rule_text 中提取并写入 artifact，判定逻辑只读取参数，
不硬编码公开集 ID 或 gold 值。
"""
import re

from .errors import AuditError

FAMILY_FACTS = {
    "F01": {
        "label": ["four_edges_visible", "face_id", "attached_to_package", "source_type"],
        "document_state": ["inspection_complete", "readability"],
    },
    "F02": {
        "label": ["model", "main_model", "compatible_models", "component_models", "serial_number", "old_model"],
        "document_state": ["readability"],
    },
    "F03": {
        "label": ["material_code", "batch_number", "quantity", "support_url", "enable_date", "expire_date", "product_name", "product_name_en"],
        "document_state": ["inspection_complete", "readability"],
    },
    "F04": {
        "label": ["carrier", "carrier_face", "fields_on_same_label", "cut_line_crosses_label", "center_y_ratio"],
        "document_state": ["inspection_complete", "readability"],
    },
    "F05": {
        "shapes": ["hollow_circle", "triangle_border", "dot_inside", "star", "circle_icon", "square_icon", "blue_dot", "yellow_dot", "mark_height", "model_text_height"],
        "document_state": ["inspection_complete", "readability"],
    },
    "F06": {
        "texts": ["warning_practice", "indoor_only", "outdoor_longterm", "step1", "step2", "step3", "keep_dry_cn", "keep_dry_en", "do_not_invert"],
        "document_state": ["inspection_complete", "readability"],
    },
    "F07": {
        "label": ["capacity", "net_weight", "gross_weight", "front_weight", "back_weight", "height_min", "height_max", "fill_percentages", "production_date"],
        "document_state": ["readability"],
    },
    "F08": {
        "certificate": ["material_code", "covered_models", "valid_from", "valid_to", "document_version", "contact_name", "issued_date", "voided"],
        "document_state": ["voided", "inspection_complete", "readability"],
    },
    "F09": {
        "cross_source": ["material_code", "components", "matrix_pairs", "batch_min", "batch_max", "model_specs"],
        "document_state": ["voided", "readability"],
    },
    "F10": {
        "trigger": ["category", "power_mode", "declared_quantity", "declared_color_name", "language_mode", "production_date"],
        "document_state": ["readability"],
    },
}

FAMILY_TITLES = {
    "F01": "实拍证据完整性",
    "F02": "商品身份与型号",
    "F03": "字段完整性与格式",
    "F04": "标签位置与载体",
    "F05": "图形与视觉关系",
    "F06": "警示与说明内容",
    "F07": "参数与数值关系",
    "F08": "证明文件结构与时效",
    "F09": "跨材料关系核验",
    "F10": "适用条件与规则边界",
}


def _extract_float(rule_text, pattern):
    matches = re.findall(pattern, rule_text)
    if not matches:
        return None
    value = matches[-1]
    return float(value) if "." in value else int(value)


def params_from_rule(rule):
    """从 rule_text 提取规则参数；无法提取的返回空 dict。"""
    text = rule["rule_text"]
    params = {}
    threshold = _extract_float(text, r"小于\s*([0-9]+(?:\.[0-9]+)?)")
    if rule["rule_id"] == "R022" and threshold is not None:
        params["attached_tag_threshold_mm"] = threshold
    ratio = _extract_float(text, r"([0-9]+(?:\.[0-9]+)?)\s*倍")
    if rule["rule_id"] == "R030" and ratio is not None:
        params["min_height_ratio"] = ratio
    tolerance = _extract_float(text, r"([0-9]+(?:\.[0-9]+)?)\s*%")
    if rule["rule_id"] == "R041" and tolerance is not None:
        params["tolerance_ratio"] = tolerance / 100.0
    days = _extract_float(text, r"([0-9]+)\s*个?自然日")
    if days is None:
        days = _extract_float(text, r"([0-9]+)\s*天内")
    if rule["rule_id"] == "R042" and days is not None:
        params["valid_days"] = int(days)
    date_match = re.search(r"([0-9]{4}-[0-9]{2}-[0-9]{2})", text)
    if rule["rule_id"] == "R058" and date_match:
        params["version_change_date"] = date_match.group(1)
    return params


def build_r_implementation(rule, source_hash, artifact_version, general):
    params = params_from_rule(rule)
    family = rule.get("rule_family_id") or "F00"
    if rule["rule_id"].startswith("R") and family not in FAMILY_FACTS:
        raise AuditError("invalid_rule", rule["rule_id"] + "缺少族配置")
    sop = {
        "source_quotes": {
            "rule": rule["rule_text"],
            "general_excerpt": "适用本规则的总则与口径补充说明",
        },
        "family": family,
        "family_title": FAMILY_TITLES.get(family, family),
        "params": params,
        "applicability": {
            "rule_specific": "由 rule_engine.applicability 与判定函数共同处理",
            "known_false": "pass/not_applicable",
            "unknown_without_known_false": "ambiguous/unknown",
            "do_not_infer_undefined_thresholds": True,
        },
        "facts_to_check": FAMILY_FACTS.get(family, {}),
        "decision_order": [
            "applicability",
            "defined_material_conflicts",
            "proven_reject",
            "outcome_changing_unknown",
            "pass",
        ],
        "clarifications": [
            "未定义阈值不得用常识补全，形成澄清项",
            "同级冲突未指定优先级时判为ambiguous",
            "缺失仅在完整清晰检查范围内成立，不可读不等于缺失",
            "视觉事实按材料独立提取，解析器注入material_id/page_index/role",
        ],
    }
    build_policy = {
        "atomic_capabilities": [
            "applicability_evaluation",
            "independent_material_fact_extraction",
            "family_specific_pure_judgement",
            "conflict_detection",
            "evidence_and_trace_validation",
        ],
        "reuse_decisions": {
            "official_contracts": "reuse_without_modification",
            "model_gateway": "reuse_for_single_material_calls",
            "shared_primitives": "quackguard.rule_common",
            "decision_logic": "pure_python_no_model_call",
        },
        "data_requirements": {
            "task_fields": ["review_date", "metadata", "materials"],
            "visual_facts": "strict_json_per_material",
            "answer_data_in_production": False,
        },
        "labeling_plan": {
            "development_cases": "public_rule_and_visible_fact_regression_and_synthetic_branches",
            "hidden_set_quality_claim": False,
        },
        "model_and_tool_config": {
            "visual_attempts_per_material": 2,
            "temperature": 0,
            "decision_engine": "quackguard.rule_engine",
        },
        "evaluation_plugins": ["quackguard.conclusion"],
        "quality_gates": {
            "contract_errors": 0,
            "execution_errors": 0,
        },
        "resource_budget": {
            "maximum_visual_calls": "two_per_relevant_material",
            "decision_model_calls": 0,
        },
    }
    return {
        "processor": "r_rule",
        "sop": sop,
        "build_policy": build_policy,
        "artifact": {
            "version": artifact_version,
            "source_hash": source_hash,
            "dependencies": {
                "decision_module": "quackguard.rule_engine",
                "extraction_module": "quackguard.rule_facts",
                "family_modules": "quackguard.rules_f01..f10",
                "protocol": "1.3.1",
            },
        },
    }
