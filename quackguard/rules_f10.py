"""F10 适用条件与规则边界：R055–R060。

R055/R056 的适用性与触发条件由 rule_engine.applicability 提前判定；
本模块判定函数仅在规则适用时运行。
"""
from .rule_common import (
    applicable_result,
    as_text,
    carrier_of,
    evidence_doc,
    evidence_material,
    face_of,
    field_raw,
    normalize_model,
    normalize_text,
    parse_date,
    shval,
    text_raw,
)

LABEL_CARRIERS = frozenset({"outer_package", "attached_tag"})


def _evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ]
    return evidence or [evidence_material(material) for material in materials]


def r055(task, materials, params):
    evidence = _evidence(materials)
    found = unreadable = missing_complete = False
    for material in materials:
        for document in material.get("documents", []):
            status, _ = text_raw(document, "charge_reading")
            if status == "value":
                found = True
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if found:
        return applicable_result(
            "pass", "rule_satisfied", "包装包含充电前阅读说明。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "充电前阅读说明读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少充电前阅读说明。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "可充电桌面灯包装未包含充电前阅读说明。", evidence
    )


def r056(task, materials, params):
    evidence = _evidence(materials)
    found = unreadable = missing_complete = False
    for material in materials:
        for document in material.get("documents", []):
            status, _ = text_raw(document, "contents_list")
            if status == "value":
                found = True
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if found:
        return applicable_result(
            "pass", "rule_satisfied", "多件装包装包含内含物清单。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "内含物清单读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少内含物清单。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "多件装包装未包含内含物清单。", evidence
    )


def r057(task, materials, params):
    evidence = _evidence(materials)
    outer_code = attached_code = None
    attached_connection = None
    unreadable = missing_complete = False
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            status, _ = field_raw(document, "material_code")
            carrier = carrier_of(document, material)
            if status == "value":
                if carrier == "outer_package":
                    outer_code = True
                elif carrier == "attached_tag":
                    attached_code = True
                    attached_connection = document.get("attached_to_package")
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if outer_code:
        return applicable_result(
            "pass", "rule_satisfied", "材料编号在包装上可见，方案完整满足。", evidence
        )
    if attached_code and attached_connection is True:
        return applicable_result(
            "pass", "rule_satisfied", "材料编号在固定附签上可见且连接关系明确。", evidence
        )
    if attached_code and attached_connection is False:
        return applicable_result(
            "reject",
            "rule_violation",
            "附签脱离载体且外包装无编号，不得拼接两个不完整方案。",
            evidence,
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "材料编号读不清，无法判断方案是否完整。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少材料编号。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "未标明材料编号，两种方案均不完整。", evidence
    )


def r058(task, materials, params):
    evidence = _evidence(materials)
    declared = as_text(task["metadata"].get("declared_model"))
    change_date = parse_date(params.get("version_change_date", "2026-01-01"))
    production = None
    unreadable = missing_complete = False
    for material in materials:
        for document in material.get("documents", []):
            status, value = field_raw(document, "production_date")
            if status == "value":
                parsed = parse_date(value)
                if parsed is not None:
                    production = parsed
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if production is None:
        if unreadable:
            return applicable_result(
                "ambiguous", "evidence_insufficient", "生产日期读不清，无法确定版本要求。", evidence
            )
        if missing_complete:
            return applicable_result(
                "reject", "rule_violation", "完整清晰范围内缺少生产日期。", evidence
            )
        return applicable_result(
            "reject", "rule_violation", "未标明生产日期，无法确定版本要求。", evidence
        )
    prefix = "OLD-" if production < change_date else "NEW-"
    if declared is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "declared_model缺失，无法核对版本前缀。", evidence
        )
    if normalize_text(declared).startswith(prefix):
        return applicable_result(
            "pass",
            "rule_satisfied",
            f"生产日期决定型号前缀为{prefix}，与申报一致。",
            evidence,
        )
    return applicable_result(
        "reject",
        "rule_violation",
        f"生产日期要求型号前缀为{prefix}，申报型号不符。",
        evidence,
    )


def r059(task, materials, params):
    evidence = _evidence(materials)
    color = as_text(task["metadata"].get("declared_color_name"))
    if color is not None and "蓝" in color:
        return applicable_result(
            "ambiguous",
            "applicability_conflict",
            "蓝色练习盒正面同时要求展示空心圆与不得出现空心圆，两项为同级冲突且未指定优先级。",
            evidence,
        )
    found_pass = found_reject = found_unknown = False
    for material in materials:
        if face_of(material, task) != "front":
            continue
        for document in material.get("documents", []):
            present = shval(document, "hollow_circle").get("present")
            if present is True:
                found_pass = True
            elif present is False:
                found_reject = True
            else:
                found_unknown = True
    if found_pass:
        return applicable_result(
            "pass", "rule_satisfied", "练习盒正面展示了空心圆。", evidence
        )
    if found_reject:
        return applicable_result(
            "reject", "rule_violation", "练习盒正面未展示空心圆。", evidence
        )
    if found_unknown:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "空心圆因遮挡无法确认。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "练习盒正面未展示空心圆。", evidence
    )


def r060(task, materials, params):
    evidence = _evidence(materials)
    outer_model = attached_model = False
    unreadable = missing_complete = False
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            for key in ("main_model", "model"):
                status, _ = field_raw(document, key)
                if status == "value":
                    if carrier_of(document, material) == "outer_package":
                        outer_model = True
                    else:
                        attached_model = True
                elif status == "unreadable":
                    unreadable = True
                elif status == "missing" and document.get("inspection_complete") is True:
                    missing_complete = True
    if outer_model:
        return applicable_result(
            "pass", "rule_satisfied", "型号位于外包装，满足非较小练习盒要求。", evidence
        )
    if attached_model:
        return applicable_result(
            "ambiguous",
            "applicability_conflict",
            "型号仅位于附签；规则未定义较小练习盒的尺寸范围或判别方法，无法确认是否允许。",
            evidence,
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "型号读不清，无法核对位置。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少型号字段。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "外包装与附签均未标明型号。", evidence
    )


JUDGES = {
    "R055": r055,
    "R056": r056,
    "R057": r057,
    "R058": r058,
    "R059": r059,
    "R060": r060,
}
