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


def _classify_fact(evidence, facts_by_page):
    """判定证据事实：supported、unverifiable（参考标注不可读）或None。"""
    if not isinstance(evidence.get("fact"), str):
        return None
    target = _normalized(evidence["fact"])
    if not target:
        return None
    key = (evidence.get("material_id"), evidence.get("page_index"))
    facts = facts_by_page.get(key, [])
    for fact in facts:
        if fact.get("readable") is False:
            continue
        if _normalized(fact["text"]) != target:
            continue
        reference_bbox = fact.get("bbox")
        output_bbox = evidence.get("bbox")
        if reference_bbox and output_bbox and iou(output_bbox, reference_bbox) < 0.5:
            continue
        return "supported"
    for fact in facts:
        if fact.get("readable") is not False:
            continue
        reference_bbox = fact.get("bbox")
        output_bbox = evidence.get("bbox")
        if reference_bbox and output_bbox and iou(output_bbox, reference_bbox) < 0.5:
            continue
        return "unverifiable"
    if key in facts_by_page and not any(
        fact.get("readable") is not False for fact in facts
    ):
        # 参考仅覆盖该页区域、未给出可读文本（如形状类证据）。
        return "unverifiable"
    return None


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
        unsupported = []
        unverifiable = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            outcome = _classify_fact(item, facts_by_page)
            if outcome == "supported":
                supported += 1
            elif outcome == "unverifiable":
                unverifiable.append(item.get("fact"))
            else:
                unsupported.append(item.get("fact"))
        supported_facts += supported
        unsupported_facts += len(unsupported)

        if (
            has_reference
            and output.get("execution_status") == "completed"
            and not issues
            and rows
            and not unsupported
        ):
            grounded_units += 1

        diagnostics.append({
            "audit_unit_id": unit_id,
            "issues": issues,
            "evidence_facts": len(rows),
            "supported_facts": supported,
            "unsupported_facts": unsupported,
            "unverifiable_facts": unverifiable,
        })

    graded_facts = supported_facts + unsupported_facts
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
                supported_facts / graded_facts if graded_facts else None
            ),
            "unsupported_fact_count": unsupported_facts,
        },
        "diagnostics": diagnostics,
        "errors": [],
        "limitation": (
            "内容核验匹配参考可见事实（材料页、文本与可选区域）；"
            "参考标注为不可读的区域单独记为unverifiable，"
            "不声称已完成图像OCR认证。"
        ),
    }
