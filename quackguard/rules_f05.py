"""F05 图形与视觉关系：R025–R030。"""
from .rule_common import (
    applicable_result,
    as_text,
    carrier_of,
    evidence_doc,
    evidence_material,
    face_of,
    is_active,
    is_standard_front,
    shval,
)

LABEL_CARRIERS = frozenset({"outer_package", "attached_tag"})


def _evidence(materials):
    evidence = [
        evidence_doc(material, document)
        for material in materials
        for document in material.get("documents", [])
    ]
    return evidence or [evidence_material(material) for material in materials]


def _front_docs(materials, task):
    return [
        (material, document)
        for material in materials
        if face_of(material, task) == "front"
        for document in material.get("documents", [])
        if is_active(document)
    ]


def _present(shape, key):
    value = shval(shape, key)
    present = value.get("present")
    return present if type(present) is bool else None


def r025(task, materials, params):
    evidence = _evidence(materials)
    declared = as_text(task["metadata"].get("declared_icon"))
    if declared != "圆标":
        return applicable_result(
            "pass", "rule_satisfied", "未申报使用圆标，本条未触发。", evidence
        )
    found_pass = found_reject = found_unknown = False
    front_docs = _front_docs(materials, task)
    if not front_docs:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "正面视图未提交，无法核对圆标。", evidence
        )
    for material, document in front_docs:
        present = _present(document, "hollow_circle")
        has_text = shval(document, "hollow_circle").get("interior_has_text")
        if present is True and has_text is False:
            found_pass = True
        elif present is True and has_text is True:
            found_reject = True
        elif present is False:
            found_reject = True
        else:
            found_unknown = True
    if found_pass:
        return applicable_result(
            "pass", "rule_satisfied", "展示了闭合空心圆且圆内无文字。", evidence
        )
    if found_reject:
        return applicable_result(
            "reject", "rule_violation", "圆标不是闭合空心圆，或圆内存在文字。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "圆标因遮挡无法确认是否闭合空心。", evidence
    )


def _inside_triangle(triangle, dot):
    values = [
        triangle.get("center_x"),
        triangle.get("center_y"),
        triangle.get("height"),
        dot.get("center_x"),
        dot.get("center_y"),
    ]
    if any(type(value) not in (int, float) for value in values):
        return None
    half = triangle["height"] / 2
    return (
        abs(dot["center_x"] - triangle["center_x"]) <= half
        and abs(dot["center_y"] - triangle["center_y"]) <= half
    )


def r026(task, materials, params):
    evidence = _evidence(materials)
    declared = as_text(task["metadata"].get("declared_icon"))
    if declared is not None and declared != "三角点标":
        return applicable_result(
            "pass", "rule_satisfied", "未申报使用三角点标，本条未触发。", evidence
        )
    found_pass = found_reject = found_unknown = False
    front_docs = _front_docs(materials, task)
    if not front_docs:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "正面视图未提交，无法核对三角点标。", evidence
        )
    for material, document in front_docs:
        if document.get("inspection_complete") is not True:
            found_unknown = True
            continue
        triangle_present = _present(document, "triangle_border")
        dot_present = _present(document, "dot_inside")
        if triangle_present is True and dot_present is True:
            triangle = shval(document, "triangle_border")
            dot = shval(document, "dot_inside")
            dot_count = dot.get("count")
            if type(dot_count) is int and dot_count != 1:
                found_reject = True
                continue
            inside = dot.get("inside")
            if type(inside) is not bool:
                inside = _inside_triangle(triangle, dot)
            if inside is True or inside is None:
                # 结构与点数正确；坐标不可得时不额外猜测位置。
                found_pass = True
            else:
                found_reject = True
        elif triangle_present is True or dot_present is True:
            # 三角边框与实心点缺一不可
            found_reject = True
        else:
            found_unknown = True
    if found_pass:
        return applicable_result(
            "pass", "rule_satisfied", "三角点标由闭合三角形边框与框内一个实心点组成。", evidence
        )
    if found_reject:
        return applicable_result(
            "reject",
            "rule_violation",
            "三角点标缺少三角形边框或框内实心点，或点数量/位置不符。",
            evidence,
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "三角点标结构因遮挡或不可辨认无法确认。", evidence
    )


def r027(task, materials, params):
    evidence = _evidence(materials)
    found_pass = found_reject = found_unknown = False
    seen_any = False
    for material, document in _front_docs(materials, task):
        circle = shval(document, "circle_icon")
        square = shval(document, "square_icon")
        circle_present = circle.get("present")
        square_present = square.get("present")
        if circle_present is True and square_present is True:
            seen_any = True
            cx = circle.get("center_x")
            sx = square.get("center_x")
            if type(cx) in (int, float) and type(sx) in (int, float):
                if cx < sx:
                    found_pass = True
                else:
                    found_reject = True
            else:
                found_unknown = True
        elif circle_present is False or square_present is False:
            seen_any = True
            found_reject = True
        elif circle_present is None or square_present is None:
            seen_any = True
            found_unknown = True
    if not seen_any:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "正面视图未提交或图标不可确认。", evidence
        )
    if found_pass and not found_reject:
        return applicable_result(
            "pass", "rule_satisfied", "圆标中心位于方标中心左侧。", evidence
        )
    if found_reject:
        return applicable_result(
            "reject", "rule_violation", "圆标与方标未同时出现，或圆标不在方标左侧。", evidence
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "图标中心横坐标不可确认。", evidence
    )


def r028(task, materials, params):
    evidence = _evidence(materials)
    color = as_text(task["metadata"].get("declared_color_name"))
    if color is None:
        return applicable_result(
            "pass", "rule_satisfied", "未声明颜色版本，本条未触发。", evidence
        )
    if any(token in color for token in ("蓝",)):
        key = "blue_dot"
    elif any(token in color for token in ("黄",)):
        key = "yellow_dot"
    else:
        return applicable_result(
            "pass", "rule_satisfied", "声明颜色非蓝/黄版，本条未触发。", evidence
        )
    found_pass = found_reject = found_unknown = False
    front_docs = _front_docs(materials, task)
    if not front_docs:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "正面视图未提交，无法核对标识区圆点。", evidence
        )
    for material, document in front_docs:
        present = _present(document, key)
        if present is True:
            found_pass = True
        elif present is False:
            found_reject = True
        else:
            found_unknown = True
    if found_pass:
        return applicable_result(
            "pass", "rule_satisfied", "标识区包含声明颜色对应的圆点。", evidence
        )
    if found_unknown:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "照片无法判断圆点颜色。", evidence
        )
    return applicable_result(
        "reject", "rule_violation", "标识区未包含声明颜色对应的圆点。", evidence
    )


def r029(task, materials, params):
    evidence = _evidence(materials)
    front_docs = _front_docs(materials, task)
    if not front_docs:
        return applicable_result(
            "ambiguous", "evidence_insufficient", "正面视图未提交，无法核对星形数量。", evidence
        )
    complete_counts = {}
    incomplete_count = unknown = False
    for material, document in front_docs:
        star = shval(document, "star")
        present = star.get("present")
        count = star.get("count")
        complete = document.get("inspection_complete") is True
        key = id(material)
        if present is True and type(count) is int:
            if complete:
                complete_counts[key] = max(complete_counts.get(key, 0), count)
            else:
                incomplete_count = True
        elif present is False and complete:
            complete_counts[key] = max(complete_counts.get(key, 0), 0)
        else:
            unknown = True
            if not complete:
                incomplete_count = True
    counts = set(complete_counts.values())
    if counts == {3}:
        return applicable_result(
            "pass", "rule_satisfied", "正面恰好出现三个未作废实心星形标志。", evidence
        )
    if 3 in counts or incomplete_count or unknown:
        return applicable_result(
            "ambiguous",
            "evidence_insufficient",
            "主面未完整展示或数量不可确认，不能认定星形数量。",
            evidence,
        )
    total = max(complete_counts.values(), default=0)
    return applicable_result(
        "reject",
        "rule_violation",
        f"正面实心星形标志数量为{total}，不是恰好三个。",
        evidence,
    )


def r030(task, materials, params):
    evidence = _evidence(materials)
    ratio = params.get("min_height_ratio", 1.5)
    standard = [
        material
        for material in materials
        if is_standard_front(material, task) is True
    ]
    if not standard:
        return applicable_result(
            "ambiguous",
            "evidence_insufficient",
            "未提交或无法确认标准正视图，不能测量标志相对高度。",
            evidence,
        )
    found_pass = found_reject = found_unknown = False
    for material in standard:
        for document in material.get("documents", []):
            mark = shval(document, "mark_height").get("height")
            text = shval(document, "model_text_height").get("height")
            if type(mark) in (int, float) and type(text) in (int, float) and text > 0:
                if mark >= ratio * text:
                    found_pass = True
                else:
                    found_reject = True
            else:
                found_unknown = True
    if found_pass and not found_reject:
        return applicable_result(
            "pass",
            "rule_satisfied",
            f"练习标志高度不小于型号文字高度的{ratio}倍。",
            evidence,
        )
    if found_reject:
        return applicable_result(
            "reject",
            "rule_violation",
            f"练习标志高度小于型号文字高度的{ratio}倍。",
            evidence,
        )
    return applicable_result(
        "ambiguous", "evidence_insufficient", "透视或遮挡影响标志高度测量。", evidence
    )


JUDGES = {
    "R025": r025,
    "R026": r026,
    "R027": r027,
    "R028": r028,
    "R029": r029,
    "R030": r030,
}
