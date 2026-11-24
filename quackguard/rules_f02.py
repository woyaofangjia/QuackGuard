"""F02 商品身份与型号：R007–R012。"""
from .rule_common import (
    applicable_result,
    as_text,
    carrier_of,
    evidence_doc,
    evidence_material,
    field_raw,
    normalize_model,
    normalize_text,
    unknown_result,
)

LABEL_CARRIERS = frozenset({"outer_package", "attached_tag"})


def _label_evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
        if carrier_of(document, material) in LABEL_CARRIERS
    ]
    return evidence or [evidence_material(material) for material in materials]


def r007(task, materials, params):
    declared = as_text(task["metadata"].get("declared_model"))
    evidence = _label_evidence(materials)
    found = mismatch = unreadable = False
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            status, value = field_raw(document, "model")
            if status == "value":
                found = True
                if declared is not None and normalize_model(value) != normalize_model(
                    declared
                ):
                    mismatch = True
            elif status == "unreadable":
                unreadable = True
    if declared is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "declared_model缺失，无法核对包装型号。", evidence
        )
    if mismatch:
        return applicable_result(
            "reject", "rule_violation", "包装型号与申报型号不一致。", evidence
        )
    if not found:
        if unreadable:
            return applicable_result(
                "ambiguous", "evidence_insufficient", "包装型号读不清。", evidence
            )
        return applicable_result(
            "reject", "rule_violation", "完整型号区域内未标明型号。", evidence
        )
    return applicable_result(
        "pass", "rule_satisfied", "包装型号与申报型号一致。", evidence
    )


def r008(task, materials, params):
    declared = task["metadata"].get("declared_component_models")
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ] or [evidence_material(material) for material in materials]
    if (
        not isinstance(declared, list)
        or not declared
        or not all(isinstance(item, str) for item in declared)
    ):
        return unknown_result("declared_component_models缺失或非法，无法核对套装覆盖。")
    expected = {normalize_model(item) for item in declared}
    seen = set()
    incomplete = False
    for material in materials:
        for document in material.get("documents", []):
            for key in ("component_models", "compatible_models", "model"):
                status, value = field_raw(document, key)
                if status == "value":
                    items = value if isinstance(value, list) else [value]
                    for item in items:
                        seen.add(normalize_model(str(item)))
                elif status == "unreadable":
                    incomplete = True
    if incomplete:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "组件型号列表不完整可见。", evidence
        )
    if seen != expected:
        missing = expected - seen
        extra = seen - expected
        parts = []
        if missing:
            parts.append("缺少" + str(len(missing)) + "个型号")
        if extra:
            parts.append("多出" + str(len(extra)) + "个型号")
        return applicable_result(
            "reject",
            "rule_violation",
            "套装组件型号集合与申报不一致：" + "、".join(parts) + "。",
            evidence,
        )
    return applicable_result(
        "pass", "rule_satisfied", "套装组件型号集合与申报一致。", evidence
    )


def r009(task, materials, params):
    quantity = task["metadata"].get("declared_quantity")
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ] or [evidence_material(material) for material in materials]
    serials = []
    unreadable = False
    for material in materials:
        for document in material.get("documents", []):
            status, value = field_raw(document, "serial_number")
            if status == "value":
                serials.append(normalize_text(value))
            elif status == "unreadable":
                unreadable = True
    if len(serials) != len(set(serials)):
        return applicable_result(
            "reject", "rule_violation", "存在重复序列号。", evidence
        )
    if type(quantity) is int and quantity > 1 and len(serials) < quantity:
        if unreadable:
            return applicable_result(
                "ambiguous", "evidence_insufficient", "部分序列号读不清，无法确认唯一性。", evidence
            )
        return applicable_result(
            "reject", "rule_violation", "多件商品缺少可读序列号。", evidence
        )
    if unreadable and not serials:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "序列号因图像质量无法确认。", evidence
        )
    return applicable_result(
        "pass", "rule_satisfied", "序列号可读且互不相同。", evidence
    )


def r010(task, materials, params):
    declared = as_text(task["metadata"].get("declared_model"))
    evidence = _label_evidence(materials)
    main_value = None
    main_unreadable = False
    main_present = False
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            status, value = field_raw(document, "main_model")
            if status == "value":
                main_value = value
                main_present = True
            elif status == "unreadable":
                main_unreadable = True
                main_present = True
    if declared is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "declared_model缺失，无法核对主型号。", evidence
        )
    if main_value is not None:
        if normalize_model(main_value) != normalize_model(declared):
            return applicable_result(
                "reject",
                "rule_violation",
                "主型号与申报型号不一致；兼容型号不能替代主型号。",
                evidence,
            )
        return applicable_result(
            "pass", "rule_satisfied", "主型号与申报型号一致。", evidence
        )
    if main_unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "主型号读不清，兼容型号不能替代。", evidence
        )
    if main_present:
        return applicable_result(
            "reject", "rule_violation", "主型号字段缺失，仅兼容型号不满足要求。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "未标明主型号字段，仅兼容型号不满足要求。", evidence
    )


def r011(task, materials, params):
    declared = as_text(task["metadata"].get("declared_model"))
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ] or [evidence_material(material) for material in materials]
    old_used = None
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            status, value = field_raw(document, "old_model")
            if status == "value":
                old_used = value
    if old_used is None:
        return applicable_result(
            "pass", "rule_satisfied", "包装未使用旧型号，无需新旧映射核验。", evidence
        )
    if declared is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "declared_model缺失，无法核验新旧映射。", evidence
        )
    found = unclear = False
    for material in materials:
        if material.get("role") != "certificate_pages":
            continue
        for document in material.get("documents", []):
            relation_status, relation = field_raw(document, "old_model_relation")
            if relation_status == "value" and isinstance(relation, str):
                normalized = normalize_model(relation)
                if (
                    normalize_model(old_used) in normalized
                    and normalize_model(declared) in normalized
                ):
                    found = True
            elif relation_status == "unreadable":
                unclear = True
    if found:
        return applicable_result(
            "pass", "rule_satisfied", "证明文件明确写出旧型号到申报型号的替代关系。", evidence
        )
    if unclear:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "证明中替代关系表述不清。", evidence
        )
    return applicable_result(
        "reject",
        "rule_violation",
        "证明文件未明确写出旧型号到申报型号的一对一替代关系。",
        evidence,
    )


def r012(task, materials, params):
    declared = as_text(task["metadata"].get("declared_model"))
    evidence = _label_evidence(materials)
    value = None
    unreadable = False
    present = False
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            status, raw = field_raw(document, "model")
            if status == "value":
                value = raw
                present = True
            elif status == "unreadable":
                unreadable = True
                present = True
    if declared is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "declared_model缺失，无法核对相似字符。", evidence
        )
    normalized_declared = normalize_text(declared)
    sensitive = "0" in normalized_declared or "1" in normalized_declared
    if value is not None:
        if normalize_model(value) != normalize_model(declared):
            return applicable_result(
                "reject",
                "rule_violation",
                "包装型号与申报型号按字符身份不一致（含0/1相似字符）。",
                evidence,
            )
        return applicable_result(
            "pass", "rule_satisfied", "包装型号与申报型号按字符身份一致。", evidence
        )
    if unreadable and sensitive:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "型号含0或1且图像无法区分相似字符。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "包装型号读不清。", evidence
        )
    if not present:
        return applicable_result(
            "reject", "rule_violation", "未标明型号字段。", evidence
        )
    return applicable_result(
        "pass", "rule_satisfied", "包装型号与申报型号按字符身份一致。", evidence
    )


JUDGES = {
    "R007": r007,
    "R008": r008,
    "R009": r009,
    "R010": r010,
    "R011": r011,
    "R012": r012,
}
