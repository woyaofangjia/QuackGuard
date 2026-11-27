"""R 系列统一视觉事实提取信封。

按材料独立调用视觉模型，输出严格 JSON；解析器注入 material_id/page_index/role，
防止证据伪造来源。对可选字段采用容错填充，根结构非法时触发重试。
"""
import time

from .errors import AuditError
from .rule_common import MISSING, load_model_json, valid_bbox

LIST_FIELDS = frozenset({"compatible_models", "covered_models", "fill_percentages"})
# 这些是短文案键，模型偶尔误放进fields；统一挪回texts。
TEXT_KEYS = frozenset({
    "warning_practice", "indoor_only", "outdoor_longterm", "step1", "step2",
    "step3", "keep_dry_cn", "keep_dry_en", "do_not_invert", "invert_phrase",
    "removable_in_storage", "non_removable_in_use", "charge_reading",
    "contents_list",
})
# 模型偶尔按图中中文标签输出字段名，统一映射到规则使用的键。
FIELD_ALIASES = {
    "型号": "model",
    "主型号": "main_model",
    "兼容型号": "compatible_models",
    "覆盖型号": "covered_models",
    "材料编号": "material_code",
    "批次号": "batch_number",
    "序列号": "serial_number",
    "生产日期": "production_date",
    "签发日": "issued_date",
    "有效起始日": "valid_from",
    "有效截止日": "valid_to",
    "净重": "net_weight",
    "毛重": "gross_weight",
    "标称重量": "net_weight",
}
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
只要图中存在标签、图形或证明页，就必须输出对应的document；不得因部分字段模糊而省略document，也不得把局部遮挡写成整图unreadable。
每个document结构：
{"voided":true|false|null,"inspection_complete":true|false,
 "carrier":"outer_package|attached_tag|product_body|instruction|certificate_page|null",
 "carrier_face":"front|back|side|top|bottom|null","is_standard_front_view":true|false|null,
 "attached_to_package":true|false|null,"four_edges_visible":true|false|null, "cut_line_crosses_label":true|false|null,"fields_on_same_label":true|false|null,
 "center_y_ratio":数字|null,
 "bbox":[x1,y1,x2,y2]|null,
 "fields":{...},"texts":{...},"shapes":{...},
 "components":[],"matrix_pairs":[],"pages":[]}。
bbox是该document内容区域在整页内的归一化坐标，未知时null，不猜测。
fields_on_same_label仅在同一张可辨认标签边框同时包含型号与材料编号时为true；两个字段分属独立边框时为false；标签边界看不清时为null。
标签印在盒体表面、与盒体共面时carrier=outer_package；标签是独立纸片或挂牌、轮廓在盒体之外、通过细线或绳与盒体连接时为attached_tag。
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
只输出本图实际识别到的字段：字段读不清时写unreadable（value=null），只有document完整清晰且确认无此字段时才省略该键，不得用missing或无关整图判断代替。
被灰色遮挡块、马赛克或贴纸覆盖的字段必须写unreadable，不得省略该键。
main_model只取“主型号”字段的值；主型号被遮挡、模糊或缺失时写unreadable，绝不能用“兼容型号”的值代替。
同一包装正反面分别标注重量时，按材料面分别输出front_weight或back_weight，两个面都要提取。
texts记录关键短文案是否出现，键如 warning_practice, indoor_only, outdoor_longterm,
step1, step2, step3, keep_dry_cn, keep_dry_en, do_not_invert, invert_phrase,
removable_in_storage, non_removable_in_use, charge_reading, contents_list, old_model_relation；
出现为value+原文；完整清晰范围内确认未出现则省略该键；看不清为unreadable。
shapes记录图形，键如 hollow_circle, triangle_border, dot_inside, star, circle_icon,
square_icon, blue_dot, yellow_dot；值为对象含 present(true|false|null)及count/center_x/center_y/height。
只输出本图中出现的图形，未出现或无法辨认时省略该键。
components记录证明组件表逐项配对"组件→型号"，每项{"component_id":..,"model":..,"status":..}。
matrix_pairs记录从矩阵恢复的配对，结构同components。pages记录证明页码，每项{"printed_page_number":整数,"status":..}。
model_specs记录证明中每个型号对应的规格，每项{"model":..,"spec":..,"status":..}。
当前材料角色：{role}。"""

MATRIX_PROMPT = """只读取当前图像中的“组件编号与型号对应关系”，不审核，不解释，只输出严格JSON。
可能有三种形式：
1) 每行一条“组件编号 → 型号”（例：a → EDU-753），逐行输出一对；
2) 矩阵：顶行是型号列标题，左侧每行是一个组件编号，交叉处黑点表示该组件对应此型号；
3) 组件表：组件编号与型号按行或按列对应。
输出：{"status":"clear|unreadable","pairs":[{"component_id":"T1","model":"P279","status":"value"}]}
component_id和model都保留图中原文，不猜测。
矩阵中某行对应格是斜纹、灰色遮挡或看不清时，为该组件输出status=unreadable且model为null；
整图没有组件对应关系或无法检查时status=unreadable且pairs=[]。"""

KEEP_DRY_PROMPT = """只判断当前图像中是否出现以下两句原文，不审核，不解释，只输出严格JSON：
{"keep_dry_cn":{"status":"value|missing|unreadable","value":...},
 "keep_dry_en":{"status":"value|missing|unreadable","value":...}}
出现且可读时status=value且value保留原文（中文“保持干燥”、英文“Keep dry”大小写按原文）；
图片完整清晰且确认不存在时status=missing且value=null；被遮挡、模糊或裁切时status=unreadable且value=null。"""

LABEL_BOUNDARY_PROMPT = """只判断当前图像：字段“型号”和“材料编号”是否被同一个可辨认的标签边框包围。
只输出严格JSON：{"verdict":"same|separate|unclear"}
两个字段在同一个完整标签边框内为same；两个字段各自在独立边框内、即使同图同框也为separate；
标签边框被裁切、遮挡或模糊导致看不全时为unclear。不要解释。"""


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
        name = FIELD_ALIASES.get(name, name)
        field = _valid_field(value, name in list_fields)
        if field is None:
            continue
        existing = result.get(name)
        if existing is None or (
            existing.get("status") != "value" and field["status"] == "value"
        ):
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
    fields = _norm_fields(raw.get("fields"), LIST_FIELDS)
    texts = _norm_fields(raw.get("texts"), frozenset())
    for name in list(fields):
        if name in TEXT_KEYS:
            texts.setdefault(name, fields.pop(name))
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
        "fields": fields,
        "texts": texts,
        "shapes": raw.get("shapes") if isinstance(raw.get("shapes"), dict) else {},
        "components": _norm_components(raw.get("components")),
        "matrix_pairs": _norm_components(raw.get("matrix_pairs")),
        "pages": _norm_pages(raw.get("pages")),
        "model_specs": _norm_model_specs(raw.get("model_specs")),
    }


def parse_facts(raw, material):
    value = load_model_json(raw)
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


def parse_matrix(raw):
    """解析组件对应关系抽取结果，返回组件配对列表。"""
    value = load_model_json(raw)
    if isinstance(value, list):
        return _norm_components(value)
    if not isinstance(value, dict) or not isinstance(value.get("pairs"), list):
        raise ValueError("Invalid matrix root")
    status = value.get("status", "clear")
    if status not in {"clear", "unreadable"}:
        raise ValueError("Invalid matrix status")
    if status == "unreadable":
        return []
    pairs = _norm_components(value["pairs"])
    seen = {pair["component_id"] for pair in pairs}
    rows = value.get("unreadable_rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, str) and row.strip() and row.strip() not in seen:
                pairs.append({
                    "component_id": row.strip(),
                    "model": None,
                    "status": "unreadable",
                })
    return pairs


def _merge_matrix(facts, pairs):
    """把矩阵配对合并到面积最大的document，避免多页重复。"""
    documents = facts.get("documents") or []
    if not documents or not pairs:
        return False

    def area(document):
        bbox = document.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return -1
        return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])

    target = max(documents, key=area)
    existing = {
        pair["component_id"]
        for pair in target.get("components", [])
        if pair["status"] == "value"
    }
    merged = False
    for pair in pairs:
        if pair["status"] == "value" and pair["component_id"] in existing:
            continue
        target.setdefault("components", []).append(pair)
        if pair["status"] == "value":
            existing.add(pair["component_id"])
        merged = True
    return merged


def extract_matrix(gateway, material, input_path, trace_id):
    """R050 专用：二次读取组件对应关系，失败不阻断主流程。"""
    visual_task = {"materials": [material]}
    trace = {
        "trace_id": trace_id,
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "kind": "matrix_extraction",
    }
    last_error = None
    delays = (0, 5, 10)
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            messages = gateway.messages(MATRIX_PROMPT, visual_task, input_path)
            raw = gateway.complete(messages)
            trace["response"] = raw
            trace["attempt"] = attempt
            pairs = parse_matrix(raw)
            trace["parsed_pairs"] = pairs
            return pairs, trace
        except (AuditError, ValueError, KeyError, TypeError, OSError) as exc:
            last_error = exc
            # 只有网络/接口错误值得退避重试；解析错误立即返回，避免无意义等待。
            if not isinstance(exc, AuditError):
                break
    trace["error"] = str(last_error)
    return [], trace


def parse_label_verdict(raw):
    value = load_model_json(raw)
    if not isinstance(value, dict) or set(value) != {"verdict"}:
        raise ValueError("Invalid label verdict root")
    if value["verdict"] not in {"same", "separate", "unclear"}:
        raise ValueError("Invalid label verdict")
    return value["verdict"]


def extract_label_verdict(gateway, material, input_path, trace_id):
    """R021 专用：定向判断两个字段是否同一标签，失败不阻断主流程。"""
    visual_task = {"materials": [material]}
    trace = {
        "trace_id": trace_id,
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "kind": "label_boundary",
    }
    last_error = None
    delays = (0, 5, 10)
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            messages = gateway.messages(
                LABEL_BOUNDARY_PROMPT, visual_task, input_path
            )
            raw = gateway.complete(messages)
            trace["response"] = raw
            trace["attempt"] = attempt
            verdict = parse_label_verdict(raw)
            trace["verdict"] = verdict
            return verdict, trace
        except (AuditError, ValueError, KeyError, TypeError, OSError) as exc:
            last_error = exc
            if not isinstance(exc, AuditError):
                break
    trace["error"] = str(last_error)
    return None, trace


def parse_keep_dry(raw):
    value = load_model_json(raw)
    if not isinstance(value, dict) or set(value) != {"keep_dry_cn", "keep_dry_en"}:
        raise ValueError("Invalid keep dry root")
    fields = {}
    for name in ("keep_dry_cn", "keep_dry_en"):
        item = value[name]
        if not isinstance(item, dict) or set(item) != {"status", "value"}:
            raise ValueError("Invalid keep dry field: " + name)
        if item["status"] not in {"value", "missing", "unreadable"}:
            raise ValueError("Invalid keep dry status: " + name)
        if item["status"] == "value":
            if not isinstance(item["value"], str) or not item["value"].strip():
                raise ValueError("Invalid keep dry value: " + name)
        elif item["value"] is not None:
            raise ValueError("Non-value keep dry must be null: " + name)
        fields[name] = item
    return fields


def _merge_texts(facts, values):
    documents = facts.get("documents") or []
    if not documents or not values:
        return False

    def area(document):
        bbox = document.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return -1
        return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])

    texts = max(documents, key=area).setdefault("texts", {})
    merged = False
    for name, item in values.items():
        current = texts.get(name)
        if current and current.get("status") == "value":
            continue
        texts[name] = item
        merged = True
    return merged


def extract_keep_dry(gateway, material, input_path, trace_id):
    """R035 专用：定向读取中英文“保持干燥”，失败不阻断主流程。"""
    visual_task = {"materials": [material]}
    trace = {
        "trace_id": trace_id,
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "kind": "keep_dry",
    }
    last_error = None
    for attempt, delay in enumerate((0, 5, 10), 1):
        if delay:
            time.sleep(delay)
        try:
            messages = gateway.messages(KEEP_DRY_PROMPT, visual_task, input_path)
            raw = gateway.complete(messages)
            trace["response"] = raw
            trace["attempt"] = attempt
            values = parse_keep_dry(raw)
            trace["parsed"] = values
            return values, trace
        except (AuditError, ValueError, KeyError, TypeError, OSError) as exc:
            last_error = exc
            if not isinstance(exc, AuditError):
                break
    trace["error"] = str(last_error)
    return None, trace


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
        if task.get("rule_id") == "R050" and material["role"] == "certificate_pages":
            pairs, matrix_trace = extract_matrix(
                gateway, material, input_path, trace_id
            )
            matrix_trace["audit_unit_id"] = task["audit_unit_id"]
            traces.append(matrix_trace)
            if pairs:
                _merge_matrix(facts, pairs)
        if task.get("rule_id") == "R021" and material["role"] in (
            "package_photos", "tag_photos"
        ):
            verdict, boundary_trace = extract_label_verdict(
                gateway, material, input_path, trace_id
            )
            boundary_trace["audit_unit_id"] = task["audit_unit_id"]
            traces.append(boundary_trace)
            if verdict is not None:
                facts["same_label_verdict"] = verdict
        if task.get("rule_id") == "R035":
            values, keep_dry_trace = extract_keep_dry(
                gateway, material, input_path, trace_id
            )
            keep_dry_trace["audit_unit_id"] = task["audit_unit_id"]
            traces.append(keep_dry_trace)
            if values:
                _merge_texts(facts, values)
    return extracted, traces


# 供测试与转换器构造字段的便捷常量
def field_value(value):
    return {"status": "value", "value": value}


def field_unreadable():
    return {"status": "unreadable", "value": None}


def field_missing():
    return dict(MISSING)
