"""F07 参数与数值关系：R037–R042。"""
from .rule_common import (
    add_days,
    applicable_result,
    as_positive_number,
    evidence_doc,
    evidence_material,
    face_of,
    field_raw,
    parse_date,
    parse_measurement,
    to_cm,
    to_grams,
    to_ml,
)


def _evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ]
    return evidence or [evidence_material(material) for material in materials]


def _grams(field_status, field_value):
    if field_status != "value":
        return None, field_status
    parsed = parse_measurement(field_value)
    if parsed is None:
        return None, "invalid"
    grams = to_grams(parsed[0], parsed[1])
    if grams is None or grams <= 0:
        return None, "invalid"
    return grams, "value"


def r037(task, materials, params):
    evidence = _evidence(materials)
    declared = as_positive_number(task["metadata"].get("declared_capacity_ml"))
    values = []
    unreadable = invalid = missing_complete = False
    for material in materials:
        for document in material.get("documents", []):
            status, value = field_raw(document, "capacity")
            if status == "value":
                parsed = parse_measurement(value)
                if parsed is None:
                    invalid = True
                else:
                    ml = to_ml(parsed[0], parsed[1])
                    if ml is None:
                        invalid = True
                    else:
                        values.append(ml)
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if declared is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "declared_capacity_ml缺失或非法。", evidence
        )
    if invalid:
        return applicable_result(
            "reject", "rule_violation", "容量单位不是L或mL，或数值非法。", evidence
        )
    for ml in values:
        if abs(ml - declared) > 1e-9:
            return applicable_result(
                "reject", "rule_violation", "包装容量与申报容量不一致。", evidence
            )
    if values:
        return applicable_result(
            "pass", "rule_satisfied", "包装容量与申报容量一致（允许L/mL换算）。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "容量读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少容量字段。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "未提交可核对的容量视图。", evidence
    )


def r038(task, materials, params):
    evidence = _evidence(materials)
    net = gross = None
    unreadable = invalid = missing_complete = False
    for material in materials:
        for document in material.get("documents", []):
            for key, target in (("net_weight", "net"), ("gross_weight", "gross")):
                status, value = field_raw(document, key)
                grams, state = _grams(status, value)
                if state == "value":
                    if target == "net":
                        net = grams
                    else:
                        gross = grams
                elif state == "unreadable":
                    unreadable = True
                elif state == "invalid":
                    invalid = True
                elif status == "missing" and document.get("inspection_complete") is True:
                    missing_complete = True
    if invalid:
        return applicable_result(
            "reject", "rule_violation", "净重或总重不是正数或单位不是g/kg。", evidence
        )
    if net is not None and gross is not None:
        if net > gross:
            return applicable_result(
                "reject", "rule_violation", "净重大于总重。", evidence
            )
        return applicable_result(
            "pass", "rule_satisfied", "净重与总重均为正数且净重不大于总重。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "净重或总重读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "缺少净重或总重字段。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "未提交可核对净重与总重的视图。", evidence
    )


def r039(task, materials, params):
    evidence = _evidence(materials)
    declared = as_positive_number(task["metadata"].get("declared_height_cm"))
    low = high = None
    unreadable = invalid = missing_complete = False
    for material in materials:
        for document in material.get("documents", []):
            for key, target in (("height_min", "low"), ("height_max", "high")):
                status, value = field_raw(document, key)
                if status == "value":
                    parsed = parse_measurement(value)
                    if parsed is None:
                        invalid = True
                    else:
                        cm = to_cm(parsed[0], parsed[1])
                        if cm is None:
                            invalid = True
                        elif target == "low":
                            low = cm
                        else:
                            high = cm
                elif status == "unreadable":
                    unreadable = True
                elif status == "missing" and document.get("inspection_complete") is True:
                    missing_complete = True
    if declared is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "declared_height_cm缺失或非法。", evidence
        )
    if invalid:
        return applicable_result(
            "reject", "rule_violation", "高度区间单位不是cm或mm，或数值非法。", evidence
        )
    if low is not None and high is not None:
        if low <= declared <= high:
            return applicable_result(
                "pass", "rule_satisfied", "申报高度位于包装声明区间内（端点允许等于）。", evidence
            )
        return applicable_result(
            "reject", "rule_violation", "申报高度不在包装声明区间内。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "高度区间读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "缺少高度区间端点。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "未提交可核对高度区间的视图。", evidence
    )


def r040(task, materials, params):
    evidence = _evidence(materials)
    unreadable = missing_complete = False
    values = None
    for material in materials:
        for document in material.get("documents", []):
            status, value = field_raw(document, "fill_percentages")
            if status == "value":
                if isinstance(value, list):
                    values = value
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if values is not None:
        if not all(isinstance(item, (int, float)) for item in values) or len(values) != 3:
            return applicable_result(
                "reject", "rule_violation", "三类填充物百分比不是恰好三个数值。", evidence
            )
        if any(item < 0 or item > 100 for item in values):
            return applicable_result(
                "reject", "rule_violation", "填充物百分比不在0至100之间。", evidence
            )
        if abs(sum(values) - 100) > 1e-9:
            return applicable_result(
                "reject", "rule_violation", "三类填充物百分比合计不是恰好100%。", evidence
            )
        return applicable_result(
            "pass", "rule_satisfied", "三类填充物百分比均在0至100且合计为100%。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "填充物百分比读不清，不能把缺失当作零。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "缺少填充物百分比字段，不能把缺失当作零。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "未提交可核对填充物百分比的视图。", evidence
    )


def r041(task, materials, params):
    evidence = _evidence(materials)
    tolerance = params.get("tolerance_ratio", 0.01)
    front = back = None
    unreadable = invalid = missing_complete = False
    for material in materials:
        face = face_of(material, task)
        for document in material.get("documents", []):
            for key in ("front_weight", "back_weight", "net_weight", "weight"):
                status, value = field_raw(document, key)
                grams, state = _grams(status, value)
                if state == "unreadable":
                    unreadable = True
                    continue
                if state == "invalid":
                    invalid = True
                    continue
                if state == "missing":
                    if document.get("inspection_complete") is True:
                        missing_complete = True
                    continue
                if key == "front_weight" or (key == "weight" and face == "front"):
                    front = grams
                elif key == "back_weight" or (key == "weight" and face == "back"):
                    back = grams
                elif key == "net_weight":
                    if face == "front":
                        front = grams
                    elif face == "back":
                        back = grams
                elif face == "front":
                    front = grams
                elif face == "back":
                    back = grams
    if invalid:
        return applicable_result(
            "reject", "rule_violation", "标称重量不是正数或单位不是g/kg。", evidence
        )
    if front is not None and back is not None:
        larger = max(front, back)
        if abs(front - back) <= larger * tolerance + 1e-9:
            return applicable_result(
                "pass",
                "rule_satisfied",
                f"正面与背面标称重量相差不超过较大值的{int(tolerance*100)}%。",
                evidence,
            )
        return applicable_result(
            "reject",
            "rule_violation",
            f"正面与背面标称重量相差超过较大值的{int(tolerance*100)}%。",
            evidence,
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "正面或背面标称重量读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少正面或背面标称重量。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "未同时取得正面与背面标称重量。", evidence
    )


def r042(task, materials, params):
    evidence = _evidence(materials)
    days = params.get("valid_days", 30)
    production = None
    unreadable = invalid = missing_complete = False
    for material in materials:
        for document in material.get("documents", []):
            status, value = field_raw(document, "production_date")
            if status == "value":
                parsed = parse_date(value)
                if parsed is None:
                    invalid = True
                else:
                    production = parsed
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    review = parse_date(task.get("review_date"))
    if review is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "审核基准日期缺失或非法。", evidence
        )
    if invalid:
        return applicable_result(
            "reject", "rule_violation", "生产日期不是合法日期。", evidence
        )
    if production is not None:
        end = add_days(production.isoformat(), days)
        if production <= review <= end:
            return applicable_result(
                "pass",
                "rule_satisfied",
                f"审核基准日期位于生产日至生产后{days}天内。",
                evidence,
            )
        return applicable_result(
            "reject",
            "rule_violation",
            f"审核基准日期不在生产日至生产后{days}天内。",
            evidence,
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "生产日期读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少生产日期字段。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "未提交可核对生产日期的视图。", evidence
    )


JUDGES = {
    "R037": r037,
    "R038": r038,
    "R039": r039,
    "R040": r040,
    "R041": r041,
    "R042": r042,
}
