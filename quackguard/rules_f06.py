"""F06 警示与说明内容：R031–R036。"""
from .rule_common import (
    applicable_result,
    as_text,
    evidence_doc,
    evidence_material,
    normalize_text,
    text_raw,
)


def _evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ]
    return evidence or [evidence_material(material) for material in materials]


def _compact(value):
    return normalize_text(value).replace(" ", "").replace("\n", "")


def r031(task, materials, params):
    evidence = _evidence(materials)
    target = "仅供练习使用"
    found = wrong = unreadable = missing_complete = doc_seen = False
    for material in materials:
        for document in material.get("documents", []):
            doc_seen = True
            status, value = text_raw(document, "warning_practice")
            if status == "value":
                if target in _compact(str(value)):
                    found = True
                else:
                    wrong = True
            elif status == "unreadable":
                unreadable = True
            elif status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if found:
        return applicable_result(
            "pass", "rule_satisfied", "完整出现仅供练习使用六个汉字。", evidence
        )
    if wrong:
        return applicable_result(
            "reject", "rule_violation", "未完整出现仅供练习使用，或缺字/同音/简写。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "警示文案读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "完整清晰范围内未出现仅供练习使用。", evidence
        )
    if not doc_seen:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "未提交可核对的包装视图。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "检查范围不完整，无法确认警示文案。", evidence
    )


def r032(task, materials, params):
    evidence = _evidence(materials)
    indoor = outdoor = unreadable = False
    for material in materials:
        for document in material.get("documents", []):
            if text_raw(document, "indoor_only")[0] == "value":
                indoor = True
            if text_raw(document, "outdoor_longterm")[0] == "value":
                outdoor = True
            if (
                text_raw(document, "indoor_only")[0] == "unreadable"
                or text_raw(document, "outdoor_longterm")[0] == "unreadable"
            ):
                unreadable = True
    if indoor and outdoor:
        return applicable_result(
            "reject",
            "rule_violation",
            "同一包装同时出现仅供室内使用与适合室外长期放置，文案自相矛盾。",
            evidence,
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "限制性文案读不清，无法判断是否矛盾。", evidence
        )
    return applicable_result(
        "pass", "rule_satisfied", "未出现相互矛盾的限制性文案。", evidence
    )


def r033(task, materials, params):
    evidence = _evidence(materials)
    expected = {
        "step1": "连接底座",
        "step2": "固定主体",
        "step3": "检查连接",
    }
    steps = {key: False for key in expected}
    wrong = unreadable = missing_complete = doc_seen = False
    for material in materials:
        for document in material.get("documents", []):
            doc_seen = True
            for key, phrase in expected.items():
                status, value = text_raw(document, key)
                if status == "value":
                    if phrase in _compact(str(value)):
                        steps[key] = True
                    else:
                        wrong = True
                elif status == "unreadable":
                    unreadable = True
                elif status == "missing" and document.get("inspection_complete") is True:
                    missing_complete = True
    if all(steps.values()) and not wrong:
        return applicable_result(
            "pass", "rule_satisfied", "组装说明按顺序标明三个步骤。", evidence
        )
    if wrong:
        return applicable_result(
            "reject", "rule_violation", "组装步骤内容与规定不符，或次序颠倒。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "步骤说明读不清，无法核对顺序。", evidence
        )
    absent = [key for key, present in steps.items() if not present]
    if absent and missing_complete:
        return applicable_result(
            "reject", "rule_violation", "组装步骤缺失、重复或次序颠倒。", evidence
        )
    if not doc_seen:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "未提交可核对的组装说明视图。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "检查范围不完整，无法核对组装步骤。", evidence
    )


def r034(task, materials, params):
    evidence = _evidence(materials)
    if task["metadata"].get("declared_detachable_in_storage") is not True:
        return applicable_result(
            "pass", "rule_satisfied", "未申报收纳状态可拆卸，本条未触发。", evidence
        )
    use_ok = storage_ok = wrong = unreadable = missing_complete = doc_seen = False
    for material in materials:
        for document in material.get("documents", []):
            doc_seen = True
            use_status, use_value = text_raw(document, "non_removable_in_use")
            storage_status, storage_value = text_raw(document, "removable_in_storage")
            if use_status == "value":
                if "使用时不可拆卸" in _compact(str(use_value)):
                    use_ok = True
                else:
                    wrong = True
            if storage_status == "value":
                if "收纳时可拆卸" in _compact(str(storage_value)):
                    storage_ok = True
                else:
                    wrong = True
            if use_status == "unreadable" or storage_status == "unreadable":
                unreadable = True
            if (
                use_status == "missing" or storage_status == "missing"
            ) and document.get("inspection_complete") is True:
                missing_complete = True
    if use_ok and storage_ok and not wrong:
        return applicable_result(
            "pass", "rule_satisfied", "说明同时给出使用时不可拆卸与收纳时可拆卸。", evidence
        )
    if wrong:
        return applicable_result(
            "reject", "rule_violation", "例外条件缺失或将两条件互换。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "例外说明读不清。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "缺少使用时不可拆卸或收纳时可拆卸说明。", evidence
        )
    if not doc_seen:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "未提交可核对的说明视图。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "检查范围不完整，无法核对拆卸例外说明。", evidence
    )


def r035(task, materials, params):
    evidence = _evidence(materials)
    mode = as_text(task["metadata"].get("language_mode"))
    if mode not in ("中文", "英文", "双语"):
        return applicable_result(
            "pass", "rule_satisfied", "language_mode未指定中文/英文/双语，本条未触发。", evidence
        )
    cn_ok = en_ok = cn_wrong = en_wrong = unreadable = missing_complete = doc_seen = False
    for material in materials:
        for document in material.get("documents", []):
            doc_seen = True
            cn_status, cn_value = text_raw(document, "keep_dry_cn")
            en_status, en_value = text_raw(document, "keep_dry_en")
            if cn_status == "value":
                if normalize_text(str(cn_value)) == "保持干燥":
                    cn_ok = True
                else:
                    cn_wrong = True
            if en_status == "value":
                if normalize_text(str(en_value)) == "Keep dry":
                    en_ok = True
                else:
                    en_wrong = True
            if cn_status == "unreadable" or en_status == "unreadable":
                unreadable = True
            if (
                cn_status == "missing" or en_status == "missing"
            ) and document.get("inspection_complete") is True:
                missing_complete = True
    need_cn = mode in ("中文", "双语")
    need_en = mode in ("英文", "双语")
    if (not need_cn or cn_ok) and (not need_en or en_ok):
        if (need_cn and cn_wrong) or (need_en and en_wrong):
            return applicable_result(
                "reject", "rule_violation", "关键句与语言声明不一致或英文大小写错误。", evidence
            )
        return applicable_result(
            "pass", "rule_satisfied", "关键句与语言声明一致。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "关键句读不清。", evidence
        )
    if cn_wrong or en_wrong:
        return applicable_result(
            "reject", "rule_violation", "关键句与语言声明不一致或英文大小写错误。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "缺少语言声明要求的关键句。", evidence
        )
    if not doc_seen:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "未提交可核对的关键句视图。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "检查范围不完整，无法核对关键句。", evidence
    )


def r036(task, materials, params):
    evidence = _evidence(materials)
    found = invert_only = unreadable = missing_complete = doc_seen = False
    for material in materials:
        for document in material.get("documents", []):
            doc_seen = True
            not_status, not_value = text_raw(document, "do_not_invert")
            invert_status, invert_value = text_raw(document, "invert_phrase")
            if not_status == "value" and "请勿倒置" in _compact(str(not_value)):
                found = True
            if invert_status == "value" and not found:
                invert_only = True
            if not_status == "unreadable":
                unreadable = True
            if not_status == "missing" and document.get("inspection_complete") is True:
                missing_complete = True
    if found:
        return applicable_result(
            "pass", "rule_satisfied", "完整出现请勿倒置，否定词保留。", evidence
        )
    if unreadable:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "否定词被遮挡，无法辨认。", evidence
        )
    if invert_only:
        return applicable_result(
            "reject", "rule_violation", "仅显示请倒置或倒置，缺少否定词。", evidence
        )
    if missing_complete:
        return applicable_result(
            "reject", "rule_violation", "明确缺少请勿倒置否定词。", evidence
        )
    if not doc_seen:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "未提交可核对的警示视图。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "检查范围不完整，无法确认否定词。", evidence
    )


JUDGES = {
    "R031": r031,
    "R032": r032,
    "R033": r033,
    "R034": r034,
    "R035": r035,
    "R036": r036,
}
