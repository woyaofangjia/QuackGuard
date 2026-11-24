"""回归用视觉事实网关：把公开集金标证据或合成分支文档还原为固定信封。

只供 regression 边界层调用；审核引擎本身不读取公开标签。
"""
import json
import re

from .rule_facts import field_unreadable, field_value

FIELD_MAP = {
    "型号": "model",
    "主型号": "main_model",
    "材料编号": "material_code",
    "批次号": "batch_number",
    "签发日": "issued_date",
    "有效起始日": "valid_from",
    "有效截止日": "valid_to",
}
B001_NAMES = {
    "型号": "model",
    "材料编号": "material_code",
    "覆盖型号": "covered_models",
    "有效起始日": "valid_from",
    "有效截止日": "valid_to",
}
B001_FIELDS = {
    "package_photos": ("model", "material_code"),
    "certificate_pages": (
        "material_code", "covered_models", "valid_from", "valid_to",
    ),
}


def document(fields=None, texts=None, components=None, matrix_pairs=None):
    value = {"inspection_complete": True, "voided": False}
    if fields:
        value["fields"] = fields
    if texts:
        value["texts"] = texts
    if components:
        value["components"] = components
    if matrix_pairs:
        value["matrix_pairs"] = matrix_pairs
    return value


def component_pair(component_id, model, status="value"):
    return {"component_id": component_id, "model": model, "status": status}


def recover_matrix_pairs(shape):
    cells = (shape or {}).get("matrix_cells") or {}
    rows = {}
    for cell in cells.values():
        rows.setdefault(cell["component_id"], []).append(cell)
    pairs = []
    for component_id, row in rows.items():
        hit = next((cell for cell in row if cell.get("value") == "有记录"), None)
        if hit is not None:
            pairs.append(component_pair(component_id, hit["model"], "value"))
        elif any(cell.get("status") == "unreadable" for cell in row):
            pairs.append(component_pair(component_id, None, "unreadable"))
    return pairs


def public_document(task, material, evidence):
    fields = {}
    texts = {}
    components = []
    matrix_pairs = recover_matrix_pairs(evidence.get("shape"))
    context = task["metadata"].get("material_context", {}).get(
        material["material_id"], {}
    )
    face = context.get("face_id")
    for item in evidence.get("visible_facts") or []:
        text = item["text"]
        readable = item["readable"]
        if "：" in text:
            name, value = text.split("：", 1)
            if name == "标称重量":
                if face == "front":
                    key = "front_weight"
                elif face == "back":
                    key = "back_weight"
                else:
                    key = None
            elif name == "兼容型号":
                if readable:
                    models = [item.strip() for item in value.split("、") if item.strip()]
                    fields["compatible_models"] = field_value(models)
                else:
                    fields["compatible_models"] = field_unreadable()
                continue
            else:
                key = FIELD_MAP.get(name)
            if key is None:
                continue
            fields[key] = field_value(value) if readable else field_unreadable()
            continue
        match = re.match(r"^\s*(\S+)\s*→\s*(\S+)\s*$", text)
        if match:
            component_id, model = match.groups()
            if readable and model != "{MASK}":
                components.append(component_pair(component_id, model, "value"))
            else:
                components.append(component_pair(component_id, None, "unreadable"))
            continue
        if not readable:
            if text == "保持干燥":
                texts["keep_dry_cn"] = field_unreadable()
            elif "dry" in text.lower():
                texts["keep_dry_en"] = field_unreadable()
            continue
        if text == "保持干燥":
            texts["keep_dry_cn"] = field_value(text)
        elif "dry" in text.lower():
            texts["keep_dry_en"] = field_value(text)
    return document(
        fields=fields or None,
        texts=texts or None,
        components=components or None,
        matrix_pairs=matrix_pairs or None,
    )


def b001_document(material, evidence):
    """B001 事实结构更严格：角色决定字段集合，缺省字段必须显式missing。"""
    role = material["role"]
    names = B001_FIELDS.get(role, B001_FIELDS["package_photos"])
    fields = {
        name: {"status": "missing", "value": None} for name in names
    }
    document = {
        "voided": False,
        "inspection_complete": True,
        "carrier": None,
        "attached_to_package": None,
    }
    visible = evidence.get("visible_facts") or []
    if role != "certificate_pages":
        attached = any(
            item.get("bbox") and item["bbox"][0] > 0.4 for item in visible
        )
        document["carrier"] = "attached_tag" if attached else "outer_package"
        document["attached_to_package"] = True if attached else None
    for item in visible:
        text = item["text"]
        if "：" not in text:
            continue
        name, value = text.split("：", 1)
        field = B001_NAMES.get(name)
        if field not in fields:
            continue
        if not item.get("readable"):
            entry = field_unreadable()
        elif field == "covered_models":
            entry = field_value(
                [part.strip() for part in value.split("、") if part.strip()]
            )
        else:
            entry = field_value(value)
        if item.get("bbox") is not None:
            entry["bbox"] = item["bbox"]
        fields[field] = entry
    document["fields"] = fields
    return document


class GoldGateway:
    """按 (material_id, page_index) 返回公开集金标证据对应的视觉事实。"""

    def __init__(self, task, gold):
        self.task = task
        self.evidence = {
            (item["material_id"], item["page_index"]): item
            for item in gold.get("evidence") or []
        }

    def messages(self, prompt, visual_task, input_path):
        material = visual_task["materials"][0]
        return {
            "material_id": material["material_id"],
            "page_index": material["page_index"],
        }

    def complete(self, messages):
        key = (messages["material_id"], messages["page_index"])
        evidence = self.evidence.get(key)
        if evidence is None:
            return json.dumps(
                {"status": "clear", "documents": [document()]},
                ensure_ascii=False,
            )
        material = next(
            material
            for material in self.task["materials"]
            if material["material_id"] == messages["material_id"]
            and material["page_index"] == messages["page_index"]
        )
        if self.task["rule_id"] == "B001":
            document = b001_document(material, evidence)
        else:
            document = public_document(self.task, material, evidence)
        return json.dumps(
            {"status": "clear", "documents": [document]},
            ensure_ascii=False,
        )


class DictGateway:
    """按 material_id 返回合成分支用例预置的文档与读取状态。"""

    def __init__(self, documents, statuses=None):
        self.documents = documents
        self.statuses = statuses or {}

    def messages(self, prompt, visual_task, input_path):
        material = visual_task["materials"][0]
        return {
            "material_id": material["material_id"],
            "page_index": material["page_index"],
        }

    def complete(self, messages):
        material_id = messages["material_id"]
        status = self.statuses.get(material_id, "clear")
        raw = self.documents.get(material_id)
        if raw is None or status == "unreadable":
            documents = []
        elif isinstance(raw, list):
            documents = raw
        else:
            documents = [raw]
        return json.dumps(
            {"status": status, "documents": documents},
            ensure_ascii=False,
        )
