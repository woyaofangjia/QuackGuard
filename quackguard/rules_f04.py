"""F04 标签位置与载体：R019–R024。"""
from .rule_common import (
    applicable_result,
    as_positive_number,
    carrier_of,
    evidence_doc,
    evidence_material,
    face_of,
    field_raw,
    is_standard_front,
)

LABEL_CARRIERS = frozenset({"outer_package", "attached_tag"})


def _evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ]
    return evidence or [evidence_material(material) for material in materials]


def _has_model(document):
    for key in ("main_model", "model"):
        if field_raw(document, key)[0] == "value":
            return True
    return False


def r019(task, materials, params):
    evidence = _evidence(materials)
    fronts = {id(material) for material in materials if face_of(material, task) == "front"}
    if not fronts:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "正面视图未提交，无法判断主型号位置。", evidence
        )
    found_front = found_other = unreadable_front = False
    for material in materials:
        for document in material.get("documents", []):
            if _has_model(document):
                if id(material) in fronts:
                    found_front = True
                else:
                    found_other = True
            elif (
                field_raw(document, "main_model")[0] == "unreadable"
                or (
                    field_raw(document, "model")[0] == "unreadable"
                    and id(material) in fronts
                )
            ):
                unreadable_front = True
    if found_front:
        return applicable_result(
            "pass", "rule_satisfied", "主型号位于包装正面。", evidence
        )
    if found_other:
        return applicable_result(
            "reject", "rule_violation", "主型号仅出现在背面或侧面，未在正面。", evidence
        )
    if unreadable_front:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "正面主型号读不清。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "正面未发现主型号字段。", evidence
    )


def r020(task, materials, params):
    evidence = _evidence(materials)
    found_body = found_other = unreadable = False
    for material in materials:
        for document in material.get("documents", []):
            status, _ = field_raw(document, "serial_number")
            carrier = carrier_of(document, material)
            if status == "value":
                if carrier == "product_body" or material.get("role") == "product_photos":
                    found_body = True
                else:
                    found_other = True
            elif status == "unreadable":
                unreadable = True
    if found_body:
        return applicable_result(
            "pass", "rule_satisfied", "序列号位于商品本体。", evidence
        )
    if found_other:
        return applicable_result(
            "reject",
            "rule_violation",
            "序列号仅出现在外包装或说明纸上，未位于商品本体。",
            evidence,
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "序列号位置读不清。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "未发现序列号字段。", evidence
    )


def r021(task, materials, params):
    evidence = _evidence(materials)
    same_label_docs = []
    model_docs = []
    code_docs = []
    unreadable = missing_incomplete = False
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            model_status = field_raw(document, "model")[0]
            code_status = field_raw(document, "material_code")[0]
            if model_status == "value" and code_status == "value":
                same_label_docs.append(document)
            if model_status == "value":
                model_docs.append(document)
            if code_status == "value":
                code_docs.append(document)
            if "unreadable" in (model_status, code_status):
                unreadable = True
            if (
                "missing" in (model_status, code_status)
                and document.get("inspection_complete") is not True
            ):
                missing_incomplete = True
    if same_label_docs:
        flags = [document.get("fields_on_same_label") for document in same_label_docs]
        if any(flag is True for flag in flags):
            return applicable_result(
                "pass", "rule_satisfied", "型号与材料编号位于同一标签。", evidence
            )
        if any(flag is False for flag in flags):
            return applicable_result(
                "reject", "rule_violation", "型号与材料编号分散在两张标签上。", evidence
            )
        return applicable_result(
            "ambiguous", "evidence_insufficient", "无法确认两个字段是否位于同一标签。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "型号或材料编号读不清，无法判断是否同标签。", evidence
        )
    if model_docs and code_docs:
        return applicable_result(
            "reject", "rule_violation", "型号与材料编号分散在两张标签上。", evidence
        )
    if missing_incomplete:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "检查范围不完整，无法确认是否缺少字段。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "型号与材料编号未同时标明，或不在同一标签。", evidence
    )


def r022(task, materials, params):
    evidence = _evidence(materials)
    edge = as_positive_number(task["metadata"].get("package_longest_edge_mm"))
    if edge is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "package_longest_edge_mm缺失或非法，无法判断附签例外。", evidence
        )
    threshold = params.get("attached_tag_threshold_mm", 80)
    code_value = code_unreadable = code_missing_complete = False
    carrier = None
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            status, _ = field_raw(document, "material_code")
            if status == "value":
                code_value = True
                carrier = carrier_of(document, material)
            elif status == "unreadable":
                code_unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                code_missing_complete = True
    if code_value:
        if edge >= threshold and carrier == "attached_tag":
            return applicable_result(
                "reject",
                "rule_violation",
                f"包装最长边{edge}毫米不小于{threshold}毫米，材料编号须位于外包装。",
                evidence,
            )
        return applicable_result(
            "pass", "rule_satisfied", "材料编号位置符合包装尺寸例外规则。", evidence
        )
    if code_unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "材料编号读不清，无法核对位置。", evidence
        )
    if code_missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少材料编号字段。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "未标明材料编号字段。", evidence
    )


def r023(task, materials, params):
    evidence = _evidence(materials)
    relevant = []
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            if (
                field_raw(document, "model")[0] == "value"
                and field_raw(document, "material_code")[0] == "value"
            ):
                relevant.append(document)
    if not relevant:
        return applicable_result(
            "pass", "rule_satisfied", "没有同时承载型号与材料编号的标签，切线约束未触发。", evidence
        )
    flags = [document.get("cut_line_crosses_label") for document in relevant]
    if any(flag is True for flag in flags):
        return applicable_result(
            "reject", "rule_violation", "型号或材料编号被开封切线穿过。", evidence
        )
    if any(flag is None for flag in flags):
        return applicable_result(
            "ambiguous", "evidence_insufficient", "无法确认切线是否穿过承载字段的标签。", evidence
        )
    return applicable_result(
        "pass", "rule_satisfied", "开封切线未穿过同时承载型号与材料编号的字段。", evidence
    )


def r024(task, materials, params):
    evidence = _evidence(materials)
    standard = [
        material for material in materials if is_standard_front(material, task) is True
    ]
    if not standard:
        return applicable_result(
            "ambiguous",
            "evidence_insufficient",
            "未提交或无法确认标准正视图，不能判定标志中心位置比例。",
            evidence,
        )
    found_below = found_above = missing = False
    for material in standard:
        for document in material.get("documents", []):
            ratio = document.get("center_y_ratio")
            if type(ratio) in (int, float):
                if ratio <= 0.5:
                    found_above = True
                else:
                    found_below = True
            else:
                missing = True
    if found_above and not found_below:
        return applicable_result(
            "pass", "rule_satisfied", "练习标志中心位于展示面上半区。", evidence
        )
    if found_below:
        return applicable_result(
            "reject", "rule_violation", "练习标志中心位于展示面下半区。", evidence
        )
    if missing:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "标志中心点位置不可确认。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "标准正视图中未发现练习标志中心点。", evidence
    )


JUDGES = {
    "R019": r019,
    "R020": r020,
    "R021": r021,
    "R022": r022,
    "R023": r023,
    "R024": r024,
}
