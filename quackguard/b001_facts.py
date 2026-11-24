from .contracts import strict_loads
from .errors import AuditError

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
 "attached_to_package":true|false|null,"fields":{...}}。
仅清楚标注作废时voided=true；清楚未标注作废时false；无法确定时null。
inspection_complete表示该文档规定检查区域是否完整清晰。
包装或附签材料只提取标签，fields固定为model和material_code；
证明文件fields固定为material_code、covered_models、valid_from、valid_to。
每个字段结构为{"status":"value|unreadable|missing","value":...}。非value状态value必须为null。
value状态保留字段值原文，不填字段名。covered_models是完整型号字符串数组，其余为字符串。缺失仅在完整清晰范围内确认不存在时使用。
外包装上的标签carrier=outer_package；附签carrier=attached_tag。
仅附签用attached_to_package表示是否同时清楚展示其与包装的固定连接；其他情况为null。
证明文件carrier和attached_to_package均为null。
当前材料角色：{role}。"""


def _validate_field(name, value):
    if not isinstance(value, dict) or set(value) != {"status", "value"}:
        raise ValueError(f"Invalid field structure: {name}")
    if value["status"] not in {"value", "unreadable", "missing"}:
        raise ValueError(f"Invalid field status: {name}")
    if value["status"] != "value":
        if value["value"] is not None:
            raise ValueError(f"Non-value field must be null: {name}")
        return
    if name == "covered_models":
        if not isinstance(value["value"], list) or not all(
            isinstance(item, str) and item.strip() for item in value["value"]
        ):
            raise ValueError("covered_models must be a string array")
    elif not isinstance(value["value"], str) or not value["value"].strip():
        raise ValueError(f"Field value must be non-empty text: {name}")


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
    for document in value["documents"]:
        if (
            not isinstance(document, dict)
            or set(document) != required_document_fields
        ):
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
        for name, field in document["fields"].items():
            _validate_field(name, field)

    return {
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "role": role,
        "status": value["status"],
        "documents": value["documents"],
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
                    raise AuditError("invalid_model_output", str(exc)) from exc
                prompt += (
                    "\n【严格JSON结构校验反馈，无标准答案】\n"
                    + str(exc)
                    + "\n请重新完整输出当前材料的JSON。上次输出：\n"
                    + raw
                )
    return extracted, traces
