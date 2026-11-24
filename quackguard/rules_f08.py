"""F08 证明文件结构与时效：R043–R048。"""
from .rule_common import (
    applicable_result,
    as_text,
    evidence_doc,
    evidence_material,
    field_raw,
    is_active,
    normalize_model,
    normalize_text,
    parse_date,
)


def _evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ]
    return evidence or [evidence_material(material) for material in materials]


def _certificate_documents(materials, active=False):
    return [
        (material, document)
        for material in materials
        if material.get("role") == "certificate_pages"
        for document in material.get("documents", [])
        if (not active) or is_active(document)
    ]


def r043(task, materials, params):
    evidence = _evidence(materials)
    first_page_docs = [
        (material, document)
        for material, document in _certificate_documents(materials)
        if material.get("page_index") == 1
    ]
    if not first_page_docs:
        return applicable_result(
            "reject", "rule_violation", "未提交证明文件首页。", evidence
        )
    found = unreadable = missing_complete = False
    for _, document in first_page_docs:
        status, value = field_raw(document, "material_code")
        if status == "value" and isinstance(value, str) and value.strip():
            found = True
        elif status == "unreadable":
            unreadable = True
        elif status == "missing" and document.get("inspection_complete") is True:
            missing_complete = True
    if found:
        return applicable_result(
            "pass", "rule_satisfied", "证明文件首页有非空材料编号字段。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "证明首页材料编号读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "证明首页缺少材料编号字段。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "证明首页未标明材料编号字段。", evidence
    )


def r044(task, materials, params):
    evidence = _evidence(materials)
    start = end = None
    unreadable = invalid = missing_complete = False
    review = parse_date(task.get("review_date"))
    for material, document in _certificate_documents(materials):
        for key, target in (("valid_from", "start"), ("valid_to", "end")):
            status, value = field_raw(document, key)
            if status == "value":
                parsed = parse_date(value)
                if parsed is None:
                    invalid = True
                elif target == "start":
                    start = parsed
                else:
                    end = parsed
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if review is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "审核基准日期缺失或非法。", evidence
        )
    if invalid:
        return applicable_result(
            "reject", "rule_violation", "证明有效起始日或截止日不是合法日期。", evidence
        )
    if start is not None and end is not None:
        if start > end:
            return applicable_result(
                "reject", "rule_violation", "证明有效期起始日晚于截止日。", evidence
            )
        if start <= review <= end:
            return applicable_result(
                "pass", "rule_satisfied", "审核基准日期位于证明有效期闭区间内。", evidence
            )
        return applicable_result(
            "reject", "rule_violation", "审核基准日期不在证明有效期闭区间内。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "证明有效期日期读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "证明缺少有效起始日或截止日。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "证明未标明有效起始日和截止日。", evidence
    )


def r045(task, materials, params):
    evidence = _evidence(materials)
    declared = as_text(task["metadata"].get("declared_model"))
    covered = None
    unreadable = missing_complete = False
    for material, document in _certificate_documents(materials):
        status, value = field_raw(document, "covered_models")
        if status == "value" and isinstance(value, list):
            covered = [str(item) for item in value]
        elif status == "unreadable":
            unreadable = True
        elif status == "missing" and document.get("inspection_complete") is True:
            missing_complete = True
    if declared is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "declared_model缺失，无法核对覆盖型号。", evidence
        )
    if covered is not None:
        normalized = {normalize_model(item) for item in covered}
        has_wildcard = any(
            "*" in item or "全部" in item or "系列" in item for item in covered
        )
        if normalize_model(declared) in normalized and not has_wildcard:
            return applicable_result(
                "pass", "rule_satisfied", "证明覆盖型号逐项列出并包含申报型号。", evidence
            )
        return applicable_result(
            "reject",
            "rule_violation",
            "证明覆盖型号未包含申报型号，或使用了通配符/系列名/全部型号。",
            evidence,
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "证明覆盖型号读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "证明缺少覆盖型号字段。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "证明未标明覆盖型号字段。", evidence
    )


def r046(task, materials, params):
    evidence = _evidence(materials)
    voided_clear = voided_fuzzy = False
    for material, document in _certificate_documents(materials):
        flag = document.get("voided")
        if flag is True:
            voided_clear = True
        elif flag is None:
            voided_fuzzy = True
    if voided_clear:
        return applicable_result(
            "reject", "rule_violation", "证明文件出现清晰作废标记，不论有效期均判为不满足。", evidence
        )
    if voided_fuzzy:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "无法确认是否为作废标记。", evidence
        )
    return applicable_result(
        "pass", "rule_satisfied", "证明文件未出现作废标记。", evidence
    )


def r047(task, materials, params):
    evidence = _evidence(materials)
    required = as_text(task["metadata"].get("required_document_version"))
    found = unreadable = missing_complete = False
    for material, document in _certificate_documents(materials):
        status, value = field_raw(document, "document_version")
        if status == "value":
            found = True
            if normalize_text(str(value)) != normalize_text(required or ""):
                return applicable_result(
                    "reject", "rule_violation", "证明版本与required_document_version不一致。", evidence
                )
        elif status == "unreadable":
            unreadable = True
        elif status == "missing" and document.get("inspection_complete") is True:
            missing_complete = True
    if found:
        return applicable_result(
            "pass", "rule_satisfied", "证明版本与required_document_version完全一致。", evidence
        )
    if required is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "required_document_version缺失。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "证明版本读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "证明缺少版本字段。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "证明未标明版本字段。", evidence
    )


def r048(task, materials, params):
    evidence = _evidence(materials)
    review = parse_date(task.get("review_date"))
    withdrawal = parse_date(task["metadata"].get("withdrawal_date"))
    issued = None
    issued_unreadable = False
    for material, document in _certificate_documents(materials):
        status, value = field_raw(document, "issued_date")
        if status == "value":
            parsed = parse_date(value)
            if parsed is not None:
                issued = parsed
        elif status == "unreadable":
            issued_unreadable = True
    if review is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "审核基准日期缺失或非法。", evidence
        )
    if withdrawal is None:
        return applicable_result(
            "pass", "rule_satisfied", "任务未提供撤回日期，按有效期判断。", evidence
        )
    if issued is not None and withdrawal < issued:
        return applicable_result(
            "ambiguous",
            "evidence_insufficient",
            "撤回日合法但早于签发日，撤回关系异常。",
            evidence,
        )
    if issued_unreadable and issued is None:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "签发日读不清，无法核对撤回关系。", evidence
        )
    if review >= withdrawal:
        return applicable_result(
            "reject", "rule_violation", "审核基准日期在撤回日当天或之后，证明已撤回。", evidence
        )
    return applicable_result(
        "pass", "rule_satisfied", "审核基准日期早于撤回日，可继续按有效期判断。", evidence
    )


JUDGES = {
    "R043": r043,
    "R044": r044,
    "R045": r045,
    "R046": r046,
    "R047": r047,
    "R048": r048,
}
