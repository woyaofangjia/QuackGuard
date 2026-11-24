"""R 系列统一视觉事实提取信封。

按材料独立调用视觉模型，输出严格 JSON；解析器注入 material_id/page_index/role，
防止证据伪造来源。对可选字段采用容错填充，根结构非法时触发重试。
"""
from .contracts import strict_loads
from .errors import AuditError
from .rule_common import MISSING, valid_bbox

LIST_FIELDS = frozenset({"compatible_models", "covered_models", "fill_percentages"})
ALLOWED_CARRIERS = frozenset({
    "outer_package",
    "attached_tag",
    "product_body",
    "instruction",
    "certificate_page",
    None,
})
ALLOWED_FACES = frozenset({"front", "back", "side", "top", "bottom", None})

FACT_PROMPT = """只提取当前一张材料图像中的可见事实，不审核，不猜测，不使用申报信息补全。
每个有独立边框的标签、每个图形区域、每页证明文件分别输出一个document。
只输出严格JSON，不要代码围栏或解释。顶层结构：{"status":"clear|unreadable","documents":[...]}。
整图无法检查时status=unreadable且documents=[]；清晰但无目标内容时documents=[]。
每个document结构：
{"voided":true|false|null,"inspection_complete":true|false,
 "carrier":"outer_package|attached_tag|product_body|instruction|certificate_page|null",
 "carrier_face":"front|back|side|top|bottom|null","is_standard_front_view":true|false|null,
 "attached_to_package":true|false|null,"four_edges_visible":true|false|null,
 "cut_line_crosses_label":true|false|null,"fields_on_same_label":true|false|null,
 "center_y_ratio":数字|null,
 "bbox":[x1,y1,x2,y2]|null,
 "fields":{...},"texts":{...},"shapes":{...},
 "components":[],"matrix_pairs":[],"pages":[]}。
bbox是该document内容区域在整页内的归一化坐标，未知时null，不猜测。
仅清楚标注作废时voided=true；清楚未作废false；无法确定null。
inspection_complete表示该文档规定检查区域是否完整清晰；不完整或不确定时填false。
fields记录带字段名的取值，可用键：model, main_model, compatible_models, material_code,
serial_number, batch_number, quantity, support_url, capacity, net_weight, gross_weight,
front_weight, back_weight, height_min, height_max, fill_percentages, document_version,
contact_name, withdrawal_date, production_date, old_model, package_model, product_name,
product_name_en, enable_date, expire_date, issued_date, valid_from, valid_to, covered_models,
package_spec, certificate_spec, model_text_height, mark_height, circle_interior_has_text。
每个字段结构{"status":"value|unreadable|missing","value":...}，非value时value必须为null；
可加"bbox":[x1,y1,x2,y2]给出该字段原文或遮挡区域的归一化框，未知则省略，不猜测。
value保留原文不填字段名；compatible_models、covered_models和fill_percentages为数组，其余为字符串。
缺失仅在完整清晰范围内确认不存在时用missing，否则用unreadable。
texts记录关键短文案是否出现，键如 warning_practice, indoor_only, outdoor_longterm,
step1, step2, step3, keep_dry_cn, keep_dry_en, do_not_invert, invert_phrase,
removable_in_storage, non_removable_in_use, charge_reading, contents_list, old_model_relation；
出现为value+原文，未出现且范围清晰为missing，看不清为unreadable。
shapes记录图形，键如 hollow_circle, triangle_border, dot_inside, star, circle_icon,
square_icon, blue_dot, yellow_dot；值为对象含 present(true|false|null)及count/center_x/center_y/height。
components记录证明组件表逐项配对"组件→型号"，每项{"component_id":..,"model":..,"status":..}。
matrix_pairs记录从矩阵恢复的配对，结构同components。pages记录证明页码，每项{"printed_page_number":整数,"status":..}。
model_specs记录证明中每个型号对应的规格，每项{"model":..,"spec":..,"status":..}。
当前材料角色：{role}。"""


def _valid_field(value, list_field):
    if not isinstance(value, dict):
        return None
    if not {"status", "value"} <= set(value):
        return None
    if set(value) - {"status", "value", "bbox"}:
        return None
    status = value["status"]
    if status not in {"value", "unreadable", "missing"}:
        return None
    if status != "value":
        if value["value"] is not None:
            return None
        result = {"status": status, "value": None}
    else:
        raw = value["value"]
        if list_field:
            if not isinstance(raw, list) or not raw:
                return None
            if not all(isinstance(item, (str, int, float)) for item in raw):
                return None
        elif not isinstance(raw, str) or not raw.strip():
            return None
        result = {"status": "value", "value": raw}
    bbox = valid_bbox(value.get("bbox"))
    if bbox is not None:
        result["bbox"] = bbox
    return result


def _norm_fields(raw, list_fields):
    if not isinstance(raw, dict):
        return {}
    result = {}
    for name, value in raw.items():
        if not isinstance(name, str):
            continue
        field = _valid_field(value, name in list_fields)
        if field is not None:
            result[name] = field
    return result


def _as_bool(value):
    return value if type(value) is bool else None


def _norm_components(raw):
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        component_id = item.get("component_id")
        model = item.get("model")
        status = item.get("status")
        if not isinstance(component_id, str) or not component_id.strip():
            continue
        if status not in {"value", "unreadable", "missing"}:
            continue
        if status == "value" and (not isinstance(model, str) or not model.strip()):
            continue
        if status != "value" and model is not None:
            continue
        result.append({
            "component_id": component_id.strip(),
            "model": model,
            "status": status,
        })
    return result


def _norm_pages(raw):
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        number = item.get("printed_page_number")
        if status not in {"value", "unreadable", "missing"}:
            continue
        if status == "value" and type(number) is not int:
            continue
        result.append({"printed_page_number": number, "status": status})
    return result


def _norm_model_specs(raw):
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        model = item.get("model")
        spec = item.get("spec")
        if status not in {"value", "unreadable", "missing"}:
            continue
        if status == "value" and (
            not isinstance(model, str)
            or not model.strip()
            or not isinstance(spec, str)
            or not spec.strip()
        ):
            continue
        if status != "value" and (model is not None or spec is not None):
            continue
        result.append({"model": model, "spec": spec, "status": status})
    return result


def _normalize_document(raw):
    if not isinstance(raw, dict):
        raise ValueError("document must be an object")
    carrier = raw.get("carrier")
    if carrier not in ALLOWED_CARRIERS:
        carrier = None
    face = raw.get("carrier_face")
    if face not in ALLOWED_FACES:
        face = None
    center_y = raw.get("center_y_ratio")
    if type(center_y) not in (int, float):
        center_y = None
    return {
        "voided": _as_bool(raw.get("voided")),
        "inspection_complete": _as_bool(raw.get("inspection_complete")),
        "bbox": valid_bbox(raw.get("bbox")),
        "carrier": carrier,
        "carrier_face": face,
        "is_standard_front_view": _as_bool(raw.get("is_standard_front_view")),
        "attached_to_package": _as_bool(raw.get("attached_to_package")),
        "four_edges_visible": _as_bool(raw.get("four_edges_visible")),
        "cut_line_crosses_label": _as_bool(raw.get("cut_line_crosses_label")),
        "fields_on_same_label": _as_bool(raw.get("fields_on_same_label")),
        "center_y_ratio": center_y,
        "fields": _norm_fields(raw.get("fields"), LIST_FIELDS),
        "texts": _norm_fields(raw.get("texts"), frozenset()),
        "shapes": raw.get("shapes") if isinstance(raw.get("shapes"), dict) else {},
        "components": _norm_components(raw.get("components")),
        "matrix_pairs": _norm_components(raw.get("matrix_pairs")),
        "pages": _norm_pages(raw.get("pages")),
        "model_specs": _norm_model_specs(raw.get("model_specs")),
    }


def parse_facts(raw, material):
    value = strict_loads(raw)
    if not isinstance(value, dict) or set(value) != {"status", "documents"}:
        raise ValueError("Invalid visual fact root")
    if value["status"] not in {"clear", "unreadable"}:
        raise ValueError("Invalid material status")
    if not isinstance(value["documents"], list):
        raise ValueError("documents must be an array")
    if value["status"] == "unreadable" and value["documents"]:
        raise ValueError("Unreadable material cannot contain documents")
    documents = [_normalize_document(item) for item in value["documents"]]
    return {
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "role": material["role"],
        "status": value["status"],
        "documents": documents,
    }


def extract_facts(gateway, task, input_path, trace_id):
    extracted = []
    traces = []
    for material in task["materials"]:
        prompt = FACT_PROMPT.replace("{role}", material["role"])
        visual_task = {"materials": [material]}
        for attempt in (1, 2):
            messages = gateway.messages(prompt, visual_task, input_path)
            raw = gateway.complete(messages)
            trace = {
                "trace_id": trace_id,
                "audit_unit_id": task["audit_unit_id"],
                "material_id": material["material_id"],
                "page_index": material["page_index"],
                "attempt": attempt,
                "response": raw,
            }
            traces.append(trace)
            try:
                facts = parse_facts(raw, material)
                trace["parsed_facts"] = facts
                extracted.append(facts)
                break
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == 2:
                    error = AuditError("invalid_model_output", str(exc))
                    error.fact_traces = traces
                    raise error from exc
                prompt += (
                    "\n【严格JSON结构校验反馈，无标准答案】\n"
                    + str(exc)
                    + "\n请重新完整输出当前材料的JSON。上次输出：\n"
                    + raw
                )
    return extracted, traces


# 供测试与转换器构造字段的便捷常量
def field_value(value):
    return {"status": "value", "value": value}


def field_unreadable():
    return {"status": "unreadable", "value": None}


def field_missing():
    return dict(MISSING)
