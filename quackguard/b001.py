import json
import math
import re

from .rule_common import (
    normalize_material_code,
    normalize_model,
    normalize_text,
    parse_date,
)

LABEL_ROLES = frozenset({"package_photos", "tag_photos"})
CERTIFICATE_ROLES = frozenset({"certificate_pages"})


def applicability(metadata):
    conditions = {
        "market": "Q",
        "category": "桌面灯",
        "power_mode": "可充电",
    }
    unknown = []
    for field, expected in conditions.items():
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            unknown.append(field)
        elif normalize_text(value) != expected:
            return {
                "label": "pass",
                "applicability": "not_applicable",
                "reason_code": "rule_not_applicable",
                "reason": f"申报条件{field}明确不符合B001适用范围。",
                "evidence": [],
            }
    if unknown:
        return {
            "label": "ambiguous",
            "applicability": "unknown",
            "reason_code": "applicability_unknown",
            "reason": "适用条件未知：" + "、".join(unknown) + "。",
            "evidence": [],
        }
    return None


def _result(label, reason_code, reason, evidence):
    unique = []
    seen = set()
    for item in evidence:
        encoded = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if encoded not in seen:
            seen.add(encoded)
            unique.append(item)
    return {
        "label": label,
        "applicability": "applicable",
        "reason_code": reason_code,
        "reason": reason,
        "evidence": unique[:1000],
    }


def _document_evidence(document):
    material, value, index = document
    fact = {
        "document_index": index,
        "role": material["role"],
        "voided": value["voided"],
        "inspection_complete": value["inspection_complete"],
        "carrier": value["carrier"],
        "attached_to_package": value["attached_to_package"],
        "fields": value["fields"],
    }
    return {
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "fact": json.dumps(fact, ensure_ascii=False),
    }


def _material_evidence(material):
    fact = {
        "role": material["role"],
        "status": material["status"],
        "document_count": len(material["documents"]),
    }
    return {
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "fact": json.dumps(fact, ensure_ascii=False),
    }


def _documents(materials, roles, active=False):
    result = []
    for material in materials:
        if material["role"] not in roles:
            continue
        for index, document in enumerate(material["documents"], 1):
            if active and document["voided"] is not False:
                continue
            result.append((material, document, index))
    return result


def _unknown_evidence(materials, roles):
    result = []
    for material in materials:
        if material["role"] not in roles:
            continue
        if material["status"] == "unreadable":
            result.append(_material_evidence(material))
        for index, document in enumerate(material["documents"], 1):
            if document["voided"] is None:
                result.append(_document_evidence((material, document, index)))
    return result


def _has_unknown_documents(materials, roles):
    return bool(_unknown_evidence(materials, roles))


def _field_state(document, field, problems, unknowns, label):
    value = document["fields"][field]
    if value["status"] == "value":
        return value["value"]
    description = f"{label}字段{field}"
    if value["status"] == "missing" and document["inspection_complete"]:
        problems.append(description + "缺失")
    else:
        unknowns.append(description + "不可确认")
    return None


def _conflict(materials):
    labels = _documents(materials, LABEL_ROLES, active=True)
    codes = {}
    for label in labels:
        value = label[1]["fields"]["material_code"]
        if value["status"] == "value":
            code = normalize_material_code(value["value"])
            codes.setdefault(code, []).append(label)
    if len(codes) > 1:
        evidence = [
            _document_evidence(label)
            for group in codes.values()
            for label in group
        ]
        return _result(
            "ambiguous",
            "material_conflict",
            "同一包装的多份未作废标签存在不同材料编号，按冲突优先处理。",
            evidence,
        )

    certificates = _documents(materials, CERTIFICATE_ROLES, active=True)
    by_code = {}
    for certificate in certificates:
        fields = certificate[1]["fields"]
        code_value = fields["material_code"]
        if code_value["status"] != "value":
            continue
        code = normalize_material_code(code_value["value"])
        by_code.setdefault(code, []).append(certificate)
    for group in by_code.values():
        periods = set()
        for certificate in group:
            fields = certificate[1]["fields"]
            start = fields["valid_from"]
            end = fields["valid_to"]
            if start["status"] == "value" and end["status"] == "value":
                periods.add((
                    normalize_text(start["value"]),
                    normalize_text(end["value"]),
                ))
        if len(periods) > 1:
            return _result(
                "ambiguous",
                "material_conflict",
                "未作废的同号证明文件存在不同有效期，按冲突优先处理。",
                [_document_evidence(item) for item in group],
            )
    return None


def _package_edge(metadata):
    value = metadata.get("package_longest_edge_mm")
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return value


def _evaluate_pair(label, certificate, task, threshold_mm):
    problems = []
    unknowns = []
    label_document = label[1]
    certificate_document = certificate[1]
    metadata = task["metadata"]
    declared_value = metadata.get("declared_model")
    declared = None
    if isinstance(declared_value, str) and declared_value.strip():
        declared = normalize_model(declared_value)
    else:
        unknowns.append("申报型号缺失或不可确认")

    review_value = task.get("review_date")
    review_date = parse_date(review_value)
    if review_date is None:
        unknowns.append("审核基准日期缺失或非法")

    model = _field_state(
        label_document, "model", problems, unknowns, "包装标签"
    )
    label_code = _field_state(
        label_document, "material_code", problems, unknowns, "包装标签"
    )
    if model is not None and declared is not None:
        if normalize_model(model) != declared:
            problems.append("包装标签型号与申报型号不一致")
    normalized_label_code = None
    if label_code is not None:
        normalized_label_code = normalize_material_code(label_code)
        if not re.fullmatch(r"QLAB-[0-9]{6}", normalized_label_code):
            problems.append("包装标签材料编号不是QLAB-加6位数字的严格格式")

    carrier = label_document["carrier"]
    if carrier == "attached_tag":
        edge = _package_edge(metadata)
        if edge is None:
            unknowns.append("包装最长边申报值缺失或非法，无法判断附签位置")
        elif edge >= threshold_mm:
            problems.append(
                f"包装最长边{edge}毫米不小于{threshold_mm}毫米，附签应位于外包装"
            )
        else:
            attached = label_document["attached_to_package"]
            if attached is False:
                problems.append("单独附签未展示与包装的固定连接关系")
            elif attached is None:
                unknowns.append("附签与包装的固定连接关系不可确认")
    elif carrier != "outer_package":
        unknowns.append("标签载体是外包装还是附签不可确认")

    certificate_code = _field_state(
        certificate_document, "material_code", problems, unknowns, "证明文件"
    )
    covered_models = _field_state(
        certificate_document, "covered_models", problems, unknowns, "证明文件"
    )
    valid_from = _field_state(
        certificate_document, "valid_from", problems, unknowns, "证明文件"
    )
    valid_to = _field_state(
        certificate_document, "valid_to", problems, unknowns, "证明文件"
    )

    if certificate_code is not None and normalized_label_code is not None:
        if normalize_material_code(certificate_code) != normalized_label_code:
            problems.append("证明文件材料编号与包装标签不一致")
    if covered_models is not None and declared is not None:
        if not any(
            normalize_model(item) == declared for item in covered_models
        ):
            problems.append("证明文件覆盖型号不包含申报型号")

    start_date = parse_date(valid_from) if valid_from is not None else None
    end_date = parse_date(valid_to) if valid_to is not None else None
    if valid_from is not None and start_date is None:
        problems.append("有效起始日不是合法YYYY-MM-DD公历日期")
    if valid_to is not None and end_date is None:
        problems.append("有效截止日不是合法YYYY-MM-DD公历日期")
    if start_date is not None and end_date is not None:
        if start_date > end_date:
            problems.append("有效起始日晚于有效截止日")
        elif (
            review_date is not None
            and not start_date <= review_date <= end_date
        ):
            problems.append("审核基准日期不在证明文件有效期闭区间内")

    evidence = [_document_evidence(label), _document_evidence(certificate)]
    if problems:
        return "reject", "；".join(problems) + "。", evidence
    if unknowns:
        return "ambiguous", "；".join(unknowns) + "。", evidence
    return "pass", "包装标签与关联证明文件满足全部适用要求。", evidence


def _missing_document(materials, roles, description):
    unknown = _unknown_evidence(materials, roles)
    if unknown:
        return _result(
            "ambiguous",
            "evidence_insufficient",
            f"{description}是否存在不可确认，不能以不可读材料推定缺失。",
            unknown,
        )
    documents = _documents(materials, roles)
    evidence = [_document_evidence(item) for item in documents]
    if not evidence:
        evidence = [_material_evidence(material) for material in materials]
    return _result(
        "reject",
        "rule_violation",
        f"未提交可用的{description}；作废材料不能单独满足要求。",
        evidence,
    )


def review(task, materials, threshold_mm):
    not_applicable = applicability(task["metadata"])
    if not_applicable is not None:
        return not_applicable

    conflict = _conflict(materials)
    if conflict is not None:
        return conflict

    labels = _documents(materials, LABEL_ROLES, active=True)
    certificates = _documents(materials, CERTIFICATE_ROLES, active=True)
    if not labels:
        return _missing_document(materials, LABEL_ROLES, "未作废包装标签")
    if not certificates:
        return _missing_document(materials, CERTIFICATE_ROLES, "未作废关联证明文件")

    outcomes = []
    for label in labels:
        for certificate in certificates:
            outcomes.append(
                _evaluate_pair(label, certificate, task, threshold_mm)
            )

    for status, reason, evidence in outcomes:
        if status == "reject":
            return _result("reject", "rule_violation", reason, evidence)

    unknown_evidence = _unknown_evidence(materials, LABEL_ROLES)
    unknown_evidence += _unknown_evidence(materials, CERTIFICATE_ROLES)
    if unknown_evidence:
        return _result(
            "ambiguous",
            "evidence_insufficient",
            "其他材料的可见内容或作废状态不可确认，仍存在待核验组合。",
            unknown_evidence,
        )
    for status, reason, evidence in outcomes:
        if status == "ambiguous":
            return _result(
                "ambiguous", "evidence_insufficient", reason, evidence
            )
    _, reason, evidence = outcomes[0]
    return _result("pass", "rule_satisfied", reason, evidence)
