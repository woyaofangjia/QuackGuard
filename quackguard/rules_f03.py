"""F03 字段完整性与格式：R013–R018。"""
import re

from .rule_common import (
    applicable_result,
    as_text,
    carrier_of,
    evidence_doc,
    evidence_material,
    field_raw,
    normalize_text,
    parse_batch,
    parse_date,
    parse_quantity,
    parse_support_url,
)

LABEL_CARRIERS = frozenset({"outer_package", "attached_tag"})


def _evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ]
    return evidence or [evidence_material(material) for material in materials]


def _label_values(materials, key):
    """返回 (values, unreadable, missing_complete, missing_incomplete)。"""
    values = []
    unreadable = missing_complete = missing_incomplete = False
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            status, value = field_raw(document, key)
            if status == "value":
                values.append(value)
            elif status == "unreadable":
                unreadable = True
            elif status == "missing":
                if document.get("inspection_complete") is True:
                    missing_complete = True
                else:
                    missing_incomplete = True
    return values, unreadable, missing_complete, missing_incomplete


def r013(task, materials, params):
    evidence = _evidence(materials)
    values, unreadable, missing_complete, missing_incomplete = _label_values(
        materials, "material_code"
    )
    for value in values:
        if not re.fullmatch(r"QLAB-[0-9]{6}", normalize_text(value)):
            return applicable_result(
                "reject", "rule_violation", "材料编号不是QLAB-加恰好6位数字的格式。", evidence
            )
    if values:
        return applicable_result(
            "pass", "rule_satisfied", "材料编号符合QLAB-加6位数字格式。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "材料编号读不清，无法核对格式。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少材料编号字段。", evidence
        )
    if missing_incomplete:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "检查范围不完整，无法确认材料编号是否缺失。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "未标明材料编号字段。", evidence
    )


def r014(task, materials, params):
    evidence = _evidence(materials)
    values, unreadable, missing_complete, missing_incomplete = _label_values(
        materials, "batch_number"
    )
    for value in values:
        if parse_batch(value) is None:
            return applicable_result(
                "reject",
                "rule_violation",
                "批次号不是真实公历日期加半角连字符加3位流水号格式。",
                evidence,
            )
    if values:
        return applicable_result(
            "pass", "rule_satisfied", "批次号符合YYYYMMDD-NNN格式且日期真实。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "批次号读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少批次号字段。", evidence
        )
    if missing_incomplete:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "检查范围不完整，无法确认批次号是否缺失。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "未标明批次号字段。", evidence
    )


def r015(task, materials, params):
    evidence = _evidence(materials)
    values, unreadable, missing_complete, missing_incomplete = _label_values(
        materials, "quantity"
    )
    for value in values:
        if parse_quantity(value) is None:
            return applicable_result(
                "reject",
                "rule_violation",
                "件数不是不带小数点的正整数后接件的格式。",
                evidence,
            )
    if values:
        return applicable_result(
            "pass", "rule_satisfied", "件数格式符合要求。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "件数字段读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内缺少件数字段。", evidence
        )
    if missing_incomplete:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "检查范围不完整，无法确认件数是否缺失。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "未标明件数字段。", evidence
    )


def r016(task, materials, params):
    evidence = _evidence(materials)
    values, unreadable, missing_complete, missing_incomplete = _label_values(
        materials, "support_url"
    )
    for value in values:
        if parse_support_url(value) is None:
            return applicable_result(
                "reject",
                "rule_violation",
                "支持地址不是https://开头且具有非空主机名的格式。",
                evidence,
            )
    if values:
        return applicable_result(
            "pass", "rule_satisfied", "支持地址协议与主机名格式符合要求。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "支持地址读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject",
            "rule_violation",
            "完整清晰范围内缺少支持地址文字（不能仅用二维码替代）。",
            evidence,
        )
    if missing_incomplete:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "检查范围不完整，无法确认支持地址是否缺失。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "未标明支持地址文字。", evidence
    )


def r017(task, materials, params):
    evidence = _evidence(materials)
    enable_values, enable_unreadable, enable_missing_c, enable_missing_i = _label_values(
        materials, "enable_date"
    )
    expire_values, expire_unreadable, expire_missing_c, expire_missing_i = _label_values(
        materials, "expire_date"
    )
    unreadable = enable_unreadable or expire_unreadable
    missing_complete = enable_missing_c or expire_missing_c
    missing_incomplete = enable_missing_i or expire_missing_i
    if enable_values and expire_values:
        start = parse_date(enable_values[0])
        end = parse_date(expire_values[0])
        if start is None or end is None:
            return applicable_result(
                "reject", "rule_violation", "启用日或截止日不是合法日期。", evidence
            )
        if start > end:
            return applicable_result(
                "reject", "rule_violation", "启用日晚于截止日。", evidence
            )
        return applicable_result(
            "pass", "rule_satisfied", "启用日与截止日均合法且启用日不晚于截止日。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "启用日或截止日读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "只给出启用日或截止日一端，或两端均缺失。", evidence
        )
    if missing_incomplete:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "检查范围不完整，无法确认日期区间。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "未同时给出启用日和截止日。", evidence
    )


def r018(task, materials, params):
    evidence = _evidence(materials)
    language_mode = as_text(task["metadata"].get("language_mode"))
    if language_mode != "双语":
        return applicable_result(
            "pass", "rule_satisfied", "未标为双语版，无需成对双语字段。", evidence
        )
    cn_values, cn_unreadable, cn_missing_c, cn_missing_i = _label_values(
        materials, "product_name"
    )
    en_values, en_unreadable, en_missing_c, en_missing_i = _label_values(
        materials, "product_name_en"
    )
    cn_values = [value for value in cn_values if as_text(value) is not None]
    en_values = [value for value in en_values if as_text(value) is not None]
    if cn_values and en_values:
        return applicable_result(
            "pass", "rule_satisfied", "双语版同时提供产品名称与Product Name字段。", evidence
        )
    if cn_unreadable or en_unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "双语字段读不清。", evidence
        )
    if cn_missing_c or en_missing_c:
        return applicable_result(
            "reject", "rule_violation", "双语版缺少产品名称或Product Name字段。", evidence
        )
    if cn_missing_i or en_missing_i:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "检查范围不完整，无法确认双语字段是否成对。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "双语版未同时提供产品名称与Product Name字段。", evidence
    )


JUDGES = {
    "R013": r013,
    "R014": r014,
    "R015": r015,
    "R016": r016,
    "R017": r017,
    "R018": r018,
}
