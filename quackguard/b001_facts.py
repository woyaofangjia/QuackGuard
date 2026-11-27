from .errors import AuditError
from .rule_common import load_model_json, valid_bbox

LABEL_FIELDS = frozenset({"model", "material_code"})
CERTIFICATE_FIELDS = frozenset({
    "material_code",
    "covered_models",
    "valid_from",
    "valid_to",
})
RELEVANT_ROLES = frozenset({
    "package_photos",
    "tag_photos",
    "certificate_pages",
})

FACT_PROMPT = """只提取当前一张材料图像中的可见事实，不审核，不猜测，不使用申报型号补全。
每个有独立边框的标签或每页证明文件分别输出一个document。只输出严格JSON，不要代码围栏或解释。
顶层结构：{"status":"clear|unreadable","documents":[...]}。
整图无法检查时status=unreadable且documents=[]；清晰但无目标文档时documents=[]。
每个document结构：
{"voided":true|false|null,"inspection_complete":true|false,
 "carrier":"outer_package|attached_tag|null",
 "attached_to_package":true|false|null,"bbox":[x1,y1,x2,y2]|null,"fields":{...}}。
bbox是该document内容区域在整页内的归一化坐标，未知时null，不猜测。
仅清楚标注作废时voided=true；清楚未标注作废时false；无法确定时null。
inspection_complete表示该文档规定检查区域是否完整清晰。
包装或附签材料只提取标签，fields固定为model和material_code；
证明文件fields固定为material_code、covered_models、valid_from、valid_to。
每个字段结构为{"status":"value|unreadable|missing","value":...,"bbox":[x1,y1,x2,y2]}。非value状态value必须为null。
每个字段都必须给出bbox：框住该字段文字行；不可读时框住遮挡、模糊或裁切区域；不得用整张图片、整个标签或证明文件区域代替。确实无法定位时才省略bbox。
value状态保留字段值原文，不填字段名。covered_models是完整型号字符串数组，其余为字符串。
只有在该文档完整清晰、确认字段根本不存在时才用missing；被遮挡、裁切、模糊或无法读清一律用unreadable，不得用missing代替。
外包装上的标签carrier=outer_package；附签carrier=attached_tag。
判断依据：标签印在包装盒表面、与盒体共面时为outer_package；
标签是独立纸片/挂牌、轮廓在盒体之外、通过细线或绳与盒体连接时为attached_tag。
仅附签用attached_to_package表示是否同时清楚展示其与包装的固定连接；其他情况为null。
证明文件carrier和attached_to_package均为null。
当前材料角色：{role}。"""


def _validate_field(name, value):
    if not isinstance(value, dict):
        raise ValueError(f"Invalid field structure: {name}")
    if not {"status", "value"} <= set(value):
        raise ValueError(f"Invalid field structure: {name}")
    if set(value) - {"status", "value", "bbox"}:
        raise ValueError(f"Invalid field structure: {name}")
    if value["status"] not in {"value", "unreadable", "missing"}:
        raise ValueError(f"Invalid field status: {name}")
    if value["status"] != "value":
        if value["value"] is not None:
            raise ValueError(f"Non-value field must be null: {name}")
        return
    if name == "covered_models":
        if isinstance(value["value"], str) and value["value"].strip():
            value["value"] = [value["value"].strip()]
        if not isinstance(value["value"], list) or not all(
            isinstance(item, str) and item.strip() for item in value["value"]
        ):
            raise ValueError("covered_models must be a string array")
    elif not isinstance(value["value"], str) or not value["value"].strip():
        raise ValueError(f"Field value must be non-empty text: {name}")


def _normalize_field(name, value):
    """把模型常见的等价结构归一为 {status, value}。

    视觉模型可能把列表字段写成字段对象数组，或对无值字段直接给 null。
    """
    if value is None:
        return {"status": "missing", "value": None}
    if isinstance(value, dict):
        if "status" in value and "value" not in value:
            value = dict(value)
            value["value"] = None
        return value
    if not isinstance(value, list):
        return value
    values = []
    for item in value:
        if isinstance(item, dict):
            if item.get("status") != "value" or not isinstance(
                item.get("value"), str
            ):
                return {"status": "missing", "value": None}
            values.append(item["value"])
        elif isinstance(item, str) and item.strip():
            values.append(item)
        else:
            return {"status": "missing", "value": None}
    if name == "covered_models":
        return {"status": "value", "value": values}
    if len(values) == 1:
        return {"status": "value", "value": values[0]}
    return {"status": "missing", "value": None}


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

    role = material["role"]
    expected_fields = (
        CERTIFICATE_FIELDS if role == "certificate_pages" else LABEL_FIELDS
    )
    required_document_fields = {
        "voided",
        "inspection_complete",
        "carrier",
        "attached_to_package",
        "fields",
    }
    documents = []
    for document in value["documents"]:
        allowed = required_document_fields | {"bbox"}
        if (
            not isinstance(document, dict)
            or not {"voided", "inspection_complete", "fields"} <= set(document)
            or set(document) - allowed
        ):
            raise ValueError("Invalid document structure")
        if role == "certificate_pages":
            # 证明页的载体与连接字段按契约必须为 null，模型省略时补 null。
            document = dict(document)
            document.setdefault("carrier", None)
            document.setdefault("attached_to_package", None)
        if not required_document_fields <= set(document):
            raise ValueError("Invalid document structure")
        if (
            document["voided"] is not None
            and type(document["voided"]) is not bool
        ):
            raise ValueError("Invalid voided state")
        if type(document["inspection_complete"]) is not bool:
            raise ValueError("inspection_complete must be boolean")
        if document["carrier"] not in ("outer_package", "attached_tag", None):
            raise ValueError("Invalid carrier")
        if (
            document["attached_to_package"] is not None
            and type(document["attached_to_package"]) is not bool
        ):
            raise ValueError("Invalid attachment state")
        if role == "certificate_pages":
            if (
                document["carrier"] is not None
                or document["attached_to_package"] is not None
            ):
                raise ValueError("Certificate carrier fields must be null")
        if (
            not isinstance(document["fields"], dict)
            or set(document["fields"]) != expected_fields
        ):
            raise ValueError("Document fields do not match material role")
        fields = {}
        for name, raw_field in document["fields"].items():
            field = _normalize_field(name, raw_field)
            _validate_field(name, field)
            item = {"status": field["status"], "value": field["value"]}
            bbox = valid_bbox(field.get("bbox"))
            if bbox is not None:
                item["bbox"] = bbox
            fields[name] = item
        normalized = {name: document[name] for name in required_document_fields}
        normalized["fields"] = fields
        normalized["bbox"] = valid_bbox(document.get("bbox"))
        documents.append(normalized)

    return {
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "role": role,
        "status": value["status"],
        "documents": documents,
    }


def extract_facts(gateway, task, input_path, trace_id):
    extracted = []
    traces = []
    for material in task["materials"]:
        if material["role"] not in RELEVANT_ROLES:
            continue
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
