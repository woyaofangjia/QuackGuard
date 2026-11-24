"""F09 跨材料关系核验：R049–R054。"""
from .rule_common import (
    applicable_result,
    as_text,
    carrier_of,
    evidence_doc,
    evidence_material,
    field_raw,
    is_active,
    normalize_material_code,
    normalize_model,
    normalize_text,
    parse_date,
)

LABEL_CARRIERS = frozenset({"outer_package", "attached_tag"})


def _evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ]
    return evidence or [evidence_material(material) for material in materials]


def _label_value(materials, key):
    for material in materials:
        for document in material.get("documents", []):
            if carrier_of(document, material) not in LABEL_CARRIERS:
                continue
            status, value = field_raw(document, key)
            if status == "value":
                return value
    return None


def _certificate_docs(materials, active=False):
    return [
        document
        for material in materials
        if material.get("role") == "certificate_pages"
        for document in material.get("documents", [])
        if (not active) or is_active(document)
    ]


def r049(task, materials, params):
    evidence = _evidence(materials)
    declared = as_text(task["metadata"].get("declared_material_code"))
    package = _label_value(materials, "material_code")
    certificate = None
    cert_unreadable = cert_missing = False
    for document in _certificate_docs(materials):
        status, value = field_raw(document, "material_code")
        if status == "value":
            certificate = value
        elif status == "unreadable":
            cert_unreadable = True
        elif status == "missing" and document.get("inspection_complete") is True:
            cert_missing = True
    if declared is None or package is None or certificate is None:
        if cert_unreadable:
            return applicable_result(
                "ambiguous", "evidence_insufficient", "证明首页材料编号读不清。", evidence
            )
        missing = []
        if declared is None:
            missing.append("申报材料编号")
        if package is None:
            missing.append("包装材料编号")
        if certificate is None and cert_missing:
            missing.append("证明材料编号")
        if missing:
            return applicable_result(
                "reject",
                "rule_violation",
                "材料编号三方不一致或缺失：" + "、".join(missing) + "。",
                evidence,
            )
        return applicable_result(
            "ambiguous", "evidence_insufficient", "材料编号三方无法全部确认。", evidence
        )
    values = {
        normalize_material_code(declared),
        normalize_material_code(package),
        normalize_material_code(certificate),
    }
    if len(values) == 1:
        return applicable_result(
            "pass", "rule_satisfied", "包装、证明首页与申报材料编号三方一致。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "包装、证明首页与申报材料编号未三方一致。", evidence
    )


def _observed_pairs(materials):
    observed = {}
    for document in _certificate_docs(materials):
        for pair in document.get("components", []) + document.get("matrix_pairs", []):
            cid = pair["component_id"]
            current = observed.get(cid)
            if pair["status"] == "value":
                observed[cid] = (pair["model"], "value")
            elif current is None or current[1] != "value":
                observed[cid] = (pair["model"], pair["status"])
    return observed


def r050(task, materials, params):
    evidence = _evidence(materials)
    declared = task["metadata"].get("declared_components")
    if (
        not isinstance(declared, list)
        or not declared
        or not all(isinstance(item, dict) for item in declared)
    ):
        from .rule_common import unknown_result

        return unknown_result("declared_components缺失或非法，无法核对组件配对。")
    declared_map = {}
    for item in declared:
        cid = item.get("component_id")
        model = item.get("model")
        if isinstance(cid, str) and cid and isinstance(model, str) and model:
            declared_map[cid] = model
    observed = _observed_pairs(materials)
    conflict = unknown = False
    for cid, model in declared_map.items():
        observation = observed.get(cid)
        if observation is None:
            conflict = True
        elif observation[1] == "value":
            if normalize_model(observation[0]) != normalize_model(model):
                conflict = True
        else:
            unknown = True
    extra = [cid for cid in observed if cid not in declared_map]
    if extra:
        conflict = True
    if conflict:
        return applicable_result(
            "reject",
            "rule_violation",
            "证明组件表component_id与型号配对和申报清单不一致，或有缺失/多出。",
            evidence,
        )
    if unknown:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "部分组件配对不可读，无法逐项核对。", evidence
        )
    return applicable_result(
        "pass", "rule_satisfied", "证明组件表配对与申报清单逐项一致。", evidence
    )


def _int_batch(value):
    if not isinstance(value, str):
        return None
    digits = normalize_text(value)
    if digits.isdigit():
        return int(digits)
    return None


def r051(task, materials, params):
    evidence = _evidence(materials)
    package_batch = _label_value(materials, "batch_number")
    low = high = None
    unreadable = invalid = missing_complete = False
    for document in _certificate_docs(materials):
        for key, target in (("batch_min", "low"), ("batch_max", "high")):
            status, value = field_raw(document, key)
            if status == "value":
                number = _int_batch(value)
                if number is None:
                    invalid = True
                elif target == "low":
                    low = number
                else:
                    high = number
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if package_batch is not None:
        batch = _int_batch(package_batch)
        if batch is None:
            invalid = True
    else:
        return applicable_result(
            "reject", "rule_violation", "包装未标明数字批次号。", evidence
        )
    if invalid:
        return applicable_result(
            "reject", "rule_violation", "批次号或区间端点不是非负整数。", evidence
        )
    if low is not None and high is not None:
        if low <= batch <= high:
            return applicable_result(
                "pass", "rule_satisfied", "包装批次号位于证明给出的数字闭区间内。", evidence
            )
        return applicable_result(
            "reject", "rule_violation", "包装批次号不在证明给出的数字闭区间内。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "证明批次区间读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "证明缺少批次区间端点。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "证明未给出批次数字区间。", evidence
    )


def r052(task, materials, params):
    evidence = _evidence(materials)
    declared = as_text(task["metadata"].get("declared_model"))
    package_spec = _label_value(materials, "package_spec")
    cert_spec = None
    unreadable = missing_complete = False
    for document in _certificate_docs(materials):
        for item in document.get("model_specs", []):
            if item["status"] != "value" or declared is None:
                if item["status"] == "unreadable":
                    unreadable = True
                continue
            if normalize_model(item["model"]) == normalize_model(declared):
                cert_spec = item["spec"]
    if declared is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "declared_model缺失。", evidence
        )
    if package_spec is None or cert_spec is None:
        if unreadable:
            return applicable_result(
                "ambiguous", "evidence_insufficient", "规格字段读不清。", evidence
            )
        return applicable_result(
            "reject",
            "rule_violation",
            "缺少包装规格或证明中declared_model对应的规格。",
            evidence,
        )
    if normalize_text(package_spec) == normalize_text(cert_spec):
        return applicable_result(
            "pass", "rule_satisfied", "declared_model对应规格与包装规格一致。", evidence
        )
    return applicable_result(
        "reject",
        "rule_violation",
        "declared_model对应规格与包装规格不一致，或借用了其他型号规格。",
        evidence,
    )


def r053(task, materials, params):
    evidence = _evidence(materials)
    package_model = _label_value(materials, "main_model") or _label_value(materials, "model")
    package_code = _label_value(materials, "material_code")
    figure_model = figure_code = None
    unreadable = False
    for document in _certificate_docs(materials):
        for key, target in (("model", "figure_model"), ("material_code", "figure_code")):
            status, value = field_raw(document, key)
            if status == "value":
                if target == "figure_model":
                    figure_model = value
                else:
                    figure_code = value
            elif status == "unreadable":
                unreadable = True
    if package_model is None or package_code is None:
        return applicable_result(
            "reject", "rule_violation", "包装实拍图缺少主型号或材料编号。", evidence
        )
    if figure_model is None or figure_code is None:
        if unreadable:
            return applicable_result(
                "ambiguous", "evidence_insufficient", "证明附图字段读不清。", evidence
            )
        return applicable_result(
            "reject", "rule_violation", "证明附图未同时可见主型号与材料编号。", evidence
        )
    if normalize_model(package_model) == normalize_model(figure_model) and (
        normalize_material_code(package_code) == normalize_material_code(figure_code)
    ):
        return applicable_result(
            "pass",
            "rule_satisfied",
            "包装主型号与材料编号同时与证明附图可见字段匹配。",
            evidence,
        )
    return applicable_result(
        "reject",
        "rule_violation",
        "包装主型号或材料编号与证明附图可见字段不匹配。",
        evidence,
    )


def r054(task, materials, params):
    evidence = _evidence(materials)
    groups = {}
    for document in _certificate_docs(materials, active=True):
        status, code = field_raw(document, "material_code")
        if status != "value":
            continue
        contact_status, contact = field_raw(document, "contact_name")
        key = normalize_material_code(str(code))
        groups.setdefault(key, set()).add(
            normalize_text(str(contact)) if contact_status == "value" else None
        )
    for code, contacts in groups.items():
        non_none = {c for c in contacts if c is not None}
        if len(non_none) >= 2:
            return applicable_result(
                "ambiguous",
                "applicability_conflict",
                f"两份未作废且材料编号相同（{code}）的证明联系方名称不同，存在同级冲突。",
                evidence,
            )
    return applicable_result(
        "pass", "rule_satisfied", "未发现同编号不同主体的未作废证明冲突。", evidence
    )


JUDGES = {
    "R049": r049,
    "R050": r050,
    "R051": r051,
    "R052": r052,
    "R053": r053,
    "R054": r054,
}
