"""F01 实拍证据完整性：R001–R006。"""
from .rule_common import (
    applicable_result,
    carrier_of,
    decide,
    evidence_doc,
    evidence_material,
    face_of,
    field_raw,
    normalize_material_code,
    source_of,
    unknown_result,
)

_TARGET_KEYS = {
    "型号": "model",
    "主型号": "main_model",
    "材料编号": "material_code",
    "批次号": "batch_number",
    "序列号": "serial_number",
    "支持地址": "support_url",
    "件数": "quantity",
}


def _all_evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ]
    return evidence or [evidence_material(material) for material in materials]


def r001(task, materials, params):
    faces = [(material, face_of(material, task)) for material in materials]
    fronts = [material for material, face in faces if face == "front"]
    evidence = _all_evidence(materials)
    if not fronts:
        if any(face is None for _, face in faces):
            return applicable_result(
                "ambiguous",
                "evidence_insufficient",
                "材料检查面信息未知，无法确认是否提交包装主视图。",
                evidence,
            )
        return applicable_result(
            "reject", "rule_violation", "未提交可见包装主面的主视图。", evidence
        )
    values = [
        document.get("four_edges_visible")
        for material in fronts
        for document in material.get("documents", [])
    ]
    if any(value is True for value in values):
        return applicable_result(
            "pass", "rule_satisfied", "已提交可见包装主面四边界的主视图。", evidence
        )
    problems = []
    unknowns = []
    if any(value is False for value in values):
        problems.append("主视图明确裁去包装主面边界")
    else:
        unknowns.append("主视图边界因遮挡不可确认")
    return decide(problems, unknowns, "已提交可见包装主面四边界的主视图。", evidence)


def r002(task, materials, params):
    required = task["metadata"].get("required_faces")
    evidence = [evidence_material(material) for material in materials] or _all_evidence(
        materials
    )
    if (
        not isinstance(required, list)
        or not required
        or not all(isinstance(face, str) and face for face in required)
    ):
        return unknown_result("required_faces缺失或非法，无法判断检查面覆盖。")
    submitted = {face_of(material, task) for material in materials}
    submitted.discard(None)
    unknown_face = any(face is None for face in (face_of(material, task) for material in materials))
    missing = [face for face in required if face not in submitted]
    if missing:
        if unknown_face:
            return applicable_result(
                "ambiguous",
                "evidence_insufficient",
                "存在检查面信息未知的材料，无法确认是否缺少检查面："
                + "、".join(missing)
                + "。",
                evidence,
            )
        return applicable_result(
            "reject",
            "rule_violation",
            "缺少检查面实拍图：" + "、".join(missing) + "。",
            evidence,
        )
    return applicable_result(
        "pass", "rule_satisfied", "required_faces中每个检查面均已提交实拍图。", evidence
    )


def r003(task, materials, params):
    total = task["metadata"].get("certificate_total_pages")
    evidence = [evidence_material(material) for material in materials]
    if type(total) is not int or total < 1:
        return unknown_result("证明文件总页数N缺失或非法，无法核对页码齐全性。")
    pages = {}
    unreadable = False
    for material in materials:
        if material.get("role") != "certificate_pages":
            continue
        for document in material.get("documents", []):
            evidence.append(evidence_doc(material, document))
            for page in document.get("pages", []):
                number = page.get("printed_page_number")
                if page.get("status") == "value" and type(number) is int:
                    pages[number] = material
                elif page.get("status") == "unreadable":
                    unreadable = True
    expected = set(range(1, total + 1))
    present = set(pages)
    if present - expected:
        return applicable_result(
            "reject", "rule_violation", "证明页码超出1至N范围。", evidence
        )
    missing = expected - present
    if missing:
        if unreadable:
            return applicable_result(
                "ambiguous",
                "evidence_insufficient",
                "存在不可读页码，无法确认缺页："
                + "、".join(str(number) for number in sorted(missing))
                + "。",
                evidence,
            )
        return applicable_result(
            "reject",
            "rule_violation",
            "证明缺页：缺少第"
            + "、".join(str(number) for number in sorted(missing))
            + "页。",
            evidence,
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "证明页码不可读，无法核对齐全性。", evidence
        )
    return applicable_result(
        "pass", "rule_satisfied", "证明第1至N页均已提交且页码可核对。", evidence
    )


def r004(task, materials, params):
    evidence = _all_evidence(materials)
    attached = [
        (material, document)
        for material in materials
        for document in material.get("documents", [])
        if carrier_of(document, material) == "attached_tag"
    ]
    if not attached:
        return applicable_result(
            "pass",
            "rule_satisfied",
            "未使用附签承载审核字段，无需附签连接取证。",
            evidence,
        )
    if any(document.get("attached_to_package") is True for _, document in attached):
        return applicable_result(
            "pass",
            "rule_satisfied",
            "已提交同时显示附签、连接件与相连包装的取证图。",
            evidence,
        )
    if any(document.get("attached_to_package") is False for _, document in attached):
        return applicable_result(
            "reject",
            "rule_violation",
            "只提交脱离载体的附签照片，未显示连接关系。",
            evidence,
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "附签与包装的连接关系不可确认。", evidence
    )


def r005(task, materials, params):
    target = _TARGET_KEYS.get(task["metadata"].get("target_field"))
    evidence = _all_evidence(materials)
    if target is None:
        return unknown_result("target_field缺失或无法识别，无法判断遮挡补证。")
    info = []
    for material in materials:
        target_status = target_value = code_status = code_value = None
        for document in material.get("documents", []):
            if target_status is None:
                target_status, target_value = field_raw(document, target)
            if code_status is None:
                code_status, code_value = field_raw(document, "material_code")
        if target_status is not None:
            info.append((target_status, target_value, code_status, code_value, material))
    occluded = [item for item in info if item[0] == "unreadable"]
    if not occluded:
        return applicable_result(
            "pass", "rule_satisfied", "主视图待核验字段未被遮挡，无需补图。", evidence
        )
    conflict = unclear = uncovered = False
    for _, _, _, occluded_code, occluded_material in occluded:
        supplements = [
            item for item in info if item[0] == "value" and item[4] is not occluded_material
        ]
        if not supplements:
            uncovered = True
            continue
        matched = False
        for _, _, _, supplement_code, _ in supplements:
            if occluded_code is not None and supplement_code is not None:
                if normalize_material_code(supplement_code) == normalize_material_code(
                    occluded_code
                ):
                    matched = True
                else:
                    conflict = True
            else:
                unclear = True
        if not matched and not conflict and not unclear:
            uncovered = True
    if conflict:
        return applicable_result(
            "reject",
            "rule_violation",
            "补图材料编号与被遮挡视图不一致，不属于同一字段补图。",
            evidence,
        )
    if uncovered:
        return applicable_result(
            "reject", "rule_violation", "待核验字段已明确遮挡且未提供有效补图。", evidence
        )
    if unclear:
        return applicable_result(
            "ambiguous",
            "evidence_insufficient",
            "补图是否属于同一字段不能确认（关联键不清）。",
            evidence,
        )
    return applicable_result(
        "pass", "rule_satisfied", "已提供同一字段无遮挡的补充视图。", evidence
    )


def r006(task, materials, params):
    evidence = [evidence_material(material) for material in materials]
    sources = [source_of(material, task) for material in materials]
    known = [source for source in sources if source and source != "unknown"]
    if sources and not known:
        return applicable_result(
            "ambiguous",
            "evidence_insufficient",
            "材料来源类型未知，无法确认是否为实拍证据。",
            evidence,
        )
    if known and all(source in ("design", "render") for source in known):
        return applicable_result(
            "reject",
            "rule_violation",
            "材料仅包含设计稿或渲染图，批次号/序列号不能作为证据。",
            evidence,
        )
    if any(source is None or source == "unknown" for source in sources):
        return applicable_result(
            "ambiguous",
            "evidence_insufficient",
            "部分材料来源类型未知，不能确认全部为实拍证据。",
            evidence,
        )
    return applicable_result(
        "pass", "rule_satisfied", "批次号与序列号证据来自实拍图。", evidence
    )


JUDGES = {
    "R001": r001,
    "R002": r002,
    "R003": r003,
    "R004": r004,
    "R005": r005,
    "R006": r006,
}
