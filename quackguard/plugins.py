"""内置评估插件：结论质量与证据内容核验。"""

import json
import unicodedata

from .contracts import iou, prediction_errors
from .quality import metric


def conclusion(inputs, outputs, references, context):
    result = metric(references, outputs, inputs)
    metric_keys = (
        "accuracy",
        "machine_review_rate",
        "machine_accuracy",
        "applicability_accuracy",
        "label_only_accuracy",
        "quality",
    )
    return {
        "plugin_id": "quackguard.conclusion",
        "plugin_version": "0.1.0",
        "input_schema_version": "1.3.1",
        "metrics": {key: result[key] for key in metric_keys},
        "diagnostics": [],
        "errors": [],
    }


def _normalized(text):
    return unicodedata.normalize("NFKC", text).strip()


def _reference_facts(references):
    """按审核单元和材料页归集参考可见事实，用于内容核验。"""
    index = {}
    for reference in references:
        if not isinstance(reference, dict):
            continue
        unit_id = reference.get("audit_unit_id")
        if not isinstance(unit_id, str):
            continue
        pages = index.setdefault(unit_id, {})
        for item in reference.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            key = (item.get("material_id"), item.get("page_index"))
            facts = pages.setdefault(key, [])
            for fact in item.get("visible_facts") or []:
                if not isinstance(fact, dict):
                    continue
                text = fact.get("text")
                if isinstance(text, str) and text.strip():
                    pages.setdefault(key, []).append(fact)
    return index


def _claims(evidence):
    """把evidence.fact拆成可核验文本断言；结构化事实取fields中status=value的值。"""
    fact = evidence.get("fact")
    if not isinstance(fact, str) or not fact.strip():
        return []
    text = fact.strip()
    payload = None
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    if isinstance(payload, dict) and isinstance(payload.get("fields"), dict):
        claims = []
        for field in payload["fields"].values():
            if not isinstance(field, dict) or field.get("status") != "value":
                continue
            value = field.get("value")
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                    continue
                item_text = str(item).strip()
                if not item_text:
                    continue
                claims.append({
                    "fact": item_text,
                    "bbox": field.get("bbox"),
                    "material_id": evidence.get("material_id"),
                    "page_index": evidence.get("page_index"),
                })
        return claims
    return [{
        "fact": fact,
        "bbox": evidence.get("bbox"),
        "material_id": evidence.get("material_id"),
        "page_index": evidence.get("page_index"),
    }]


def _classify_claim(claim, facts_by_page):
    """判定断言：supported、misaligned（文本命中但区域不符）、unverifiable或None。"""
    target = _normalized(claim["fact"])
    if not target:
        return None
    key = (claim.get("material_id"), claim.get("page_index"))
    facts = facts_by_page.get(key, [])
    matched = []
    for fact in facts:
        if fact.get("readable") is False:
            continue
        text = fact.get("text")
        if isinstance(text, str) and target in _normalized(text):
            matched.append(fact)
    if matched:
        output_bbox = claim.get("bbox")
        reference_boxes = [fact.get("bbox") for fact in matched if fact.get("bbox")]
        if output_bbox and reference_boxes:
            if max(iou(output_bbox, box) for box in reference_boxes) < 0.5:
                return "misaligned"
        return "supported"
    for fact in facts:
        if fact.get("readable") is not False:
            continue
        text = fact.get("text")
        if isinstance(text, str) and target in _normalized(text):
            return "unverifiable"
    if key in facts_by_page and not any(
        fact.get("readable") is not False for fact in facts
    ):
        # 参考仅覆盖该页区域、未给出可读文本（如形状类证据）。
        return "unverifiable"
    return "unsupported"


def evidence(inputs, outputs, references, context):
    tasks = {
        task["audit_unit_id"]: task
        for task in inputs
        if isinstance(task, dict) and isinstance(task.get("audit_unit_id"), str)
    }
    reference_facts = _reference_facts(references)
    diagnostics = []
    valid_structure_count = 0
    evidence_units = 0
    reference_units = 0
    grounded_units = 0
    supported_facts = 0
    misaligned_fact_count = 0
    unsupported_facts = 0

    for output in outputs:
        if not isinstance(output, dict):
            continue
        unit_id = output.get("audit_unit_id")
        task = tasks.get(unit_id)
        if task is None:
            diagnostics.append({
                "audit_unit_id": unit_id,
                "issues": ["unknown_audit_unit"],
                "evidence_facts": 0,
                "supported_facts": 0,
                "unsupported_facts": [],
                "unverifiable_facts": [],
            })
            continue

        issues = prediction_errors(output, task)
        if not issues:
            valid_structure_count += 1
        rows = output.get("evidence")
        rows = rows if isinstance(rows, list) else []
        if rows:
            evidence_units += 1

        facts_by_page = reference_facts.get(unit_id) or {}
        has_reference = bool(facts_by_page)
        if has_reference:
            reference_units += 1

        supported = 0
        misaligned = []
        unsupported = []
        unverifiable = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            for claim in _claims(item):
                outcome = _classify_claim(claim, facts_by_page)
                if outcome == "supported":
                    supported += 1
                elif outcome == "misaligned":
                    misaligned.append(claim["fact"])
                elif outcome == "unverifiable":
                    unverifiable.append(claim["fact"])
                elif outcome == "unsupported":
                    unsupported.append(claim["fact"])
        supported_facts += supported
        misaligned_fact_count += len(misaligned)
        unsupported_facts += len(unsupported)

        if (
            has_reference
            and output.get("execution_status") == "completed"
            and not issues
            and rows
            and not unsupported
            and (supported or misaligned)
        ):
            grounded_units += 1

        diagnostics.append({
            "audit_unit_id": unit_id,
            "issues": issues,
            "evidence_facts": len(rows),
            "supported_facts": supported,
            "misaligned_facts": misaligned,
            "unsupported_facts": unsupported,
            "unverifiable_facts": unverifiable,
        })

    graded_facts = supported_facts + misaligned_fact_count + unsupported_facts
    content_supported = supported_facts + misaligned_fact_count
    return {
        "plugin_id": "quackguard.evidence_content",
        "plugin_version": "0.2.0",
        "input_schema_version": "1.3.1",
        "metrics": {
            "valid_structure_count": valid_structure_count,
            "evidence_units": evidence_units,
            "reference_units": reference_units,
            "grounded_units": grounded_units,
            "grounded_fact_rate": (
                content_supported / graded_facts if graded_facts else None
            ),
            "localized_fact_rate": (
                supported_facts / content_supported if content_supported else None
            ),
            "misaligned_fact_count": misaligned_fact_count,
            "unsupported_fact_count": unsupported_facts,
        },
        "diagnostics": diagnostics,
        "errors": [],
        "limitation": (
            "内容核验匹配参考可见事实（材料页与文本），结构化事实按其字段值逐项核验；"
            "文本命中但区域IoU<0.5单独记为misaligned，并以localized_fact_rate报告；"
            "参考标注为不可读的区域记为unverifiable，不声称已完成图像OCR认证。"
        ),
    }
