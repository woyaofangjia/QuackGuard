"""共享纯函数与判定原语，供 B001 与 R 系列复用。

不包含 IO、网络、时间或随机；所有结论只依赖显式入参。
"""
import json
import math
import re
import unicodedata
from datetime import date, timedelta

MISSING = {"status": "missing", "value": None}


def normalize_text(value):
    return unicodedata.normalize("NFKC", value).strip()


def normalize_model(value):
    return normalize_text(value).lower().replace(" ", "").replace("-", "")


def normalize_material_code(value):
    return normalize_text(value)


_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def parse_date(value):
    if not isinstance(value, str):
        return None
    value = normalize_text(value)
    if not _DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def as_text(value):
    """返回规范化后的非空字符串，否则 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    return normalize_text(value)


def as_positive_number(value):
    if type(value) not in (int, float):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def fval(document, name):
    fields = document.get("fields") if isinstance(document, dict) else None
    if not isinstance(fields, dict):
        return MISSING
    value = fields.get(name)
    if not isinstance(value, dict) or set(value) != {"status", "value"}:
        return MISSING
    return value


def tval(document, name):
    texts = document.get("texts") if isinstance(document, dict) else None
    if not isinstance(texts, dict):
        return MISSING
    value = texts.get(name)
    if not isinstance(value, dict) or set(value) != {"status", "value"}:
        return MISSING
    return value


def shval(document, name):
    shapes = document.get("shapes") if isinstance(document, dict) else None
    if not isinstance(shapes, dict):
        return {}
    value = shapes.get(name)
    return value if isinstance(value, dict) else {}


def field_raw(document, name):
    """返回 (status, value)，status 为 value/unreadable/missing。"""
    field = fval(document, name)
    return field["status"], field["value"]


def text_raw(document, name):
    field = tval(document, name)
    return field["status"], field["value"]


def is_active(document):
    return document.get("voided") is False


def iter_docs(materials, roles=None, active=False):
    for material in materials:
        if roles is not None and material.get("role") not in roles:
            continue
        for document in material.get("documents", []):
            if active and not is_active(document):
                continue
            yield material, document


def carrier_of(document, material):
    carrier = document.get("carrier")
    if isinstance(carrier, str) and carrier:
        return carrier
    role = material.get("role")
    if role == "tag_photos":
        return "attached_tag"
    if role == "certificate_pages":
        return "certificate_page"
    if role == "product_photos":
        return "product_body"
    if role in ("package_photos", "instruction_photos"):
        return "outer_package"
    return None


def face_of(material, task):
    context = task.get("metadata", {}).get("material_context", {})
    if isinstance(context, dict):
        info = context.get(material.get("material_id"), {})
        if isinstance(info, dict) and isinstance(info.get("face_id"), str):
            return info["face_id"]
    face = document_face(material)
    return face


def document_face(material):
    for document in material.get("documents", []):
        face = document.get("carrier_face")
        if isinstance(face, str) and face:
            return face
    return None


def source_of(material, task):
    context = task.get("metadata", {}).get("material_context", {})
    if isinstance(context, dict):
        info = context.get(material.get("material_id"), {})
        if isinstance(info, dict) and isinstance(info.get("source_type"), str):
            return info["source_type"]
    source = material.get("source_type")
    return source if isinstance(source, str) else None


def is_standard_front(material, task):
    context = task.get("metadata", {}).get("material_context", {})
    if isinstance(context, dict):
        info = context.get(material.get("material_id"), {})
        if isinstance(info, dict) and isinstance(info.get("is_standard_front_view"), bool):
            return info["is_standard_front_view"]
    for document in material.get("documents", []):
        value = document.get("is_standard_front_view")
        if isinstance(value, bool):
            return value
    return None


def _dedupe(evidence):
    unique = []
    seen = set()
    for item in evidence:
        encoded = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if encoded not in seen:
            seen.add(encoded)
            unique.append(item)
    return unique[:1000]


def evidence_doc(material, document, fact=None):
    payload = {
        "role": material.get("role"),
        "carrier": document.get("carrier"),
        "carrier_face": document.get("carrier_face"),
        "voided": document.get("voided"),
        "fields": document.get("fields"),
        "texts": document.get("texts"),
        "shapes": document.get("shapes"),
    }
    if fact:
        payload["fact"] = fact
    return {
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "fact": json.dumps(payload, ensure_ascii=False),
    }


def evidence_material(material, fact=None):
    payload = {
        "role": material.get("role"),
        "status": material.get("status"),
        "document_count": len(material.get("documents", [])),
    }
    if fact:
        payload["fact"] = fact
    return {
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "fact": json.dumps(payload, ensure_ascii=False),
    }


def applicable_result(label, reason_code, reason, evidence):
    return {
        "label": label,
        "applicability": "applicable",
        "reason_code": reason_code,
        "reason": reason,
        "evidence": _dedupe(evidence),
    }


def not_applicable_result(reason):
    return {
        "label": "pass",
        "applicability": "not_applicable",
        "reason_code": "rule_not_applicable",
        "reason": reason,
        "evidence": [],
    }


def unknown_result(reason, reason_code="applicability_unknown"):
    return {
        "label": "ambiguous",
        "applicability": "unknown",
        "reason_code": reason_code,
        "reason": reason,
        "evidence": [],
    }


def decide(problems, unknowns, pass_reason, evidence):
    """按 总则 决策顺序：已证实不满足 → 影响结论的未知 → 全部满足。"""
    if problems:
        return applicable_result(
            "reject", "rule_violation", "；".join(problems) + "。", evidence
        )
    if unknowns:
        return applicable_result(
            "ambiguous",
            "evidence_insufficient",
            "；".join(unknowns) + "。",
            evidence,
        )
    return applicable_result("pass", "rule_satisfied", pass_reason, evidence)


_MEASURE_RE = re.compile(
    r"^\s*([0-9]+(?:\.[0-9]+)?)\s*"
    r"(kg|g|l|ml|mm|cm|千克|克|升|毫升|毫米|厘米)\s*$",
    re.IGNORECASE,
)


def parse_measurement(text):
    if not isinstance(text, str):
        return None
    match = _MEASURE_RE.match(normalize_text(text))
    if not match:
        return None
    return float(match.group(1)), match.group(2).lower()


def to_grams(value, unit):
    if unit in ("g", "克"):
        return value
    if unit in ("kg", "千克"):
        return value * 1000.0
    return None


def to_ml(value, unit):
    if unit in ("ml", "毫升"):
        return value
    if unit in ("l", "升"):
        return value * 1000.0
    return None


def to_cm(value, unit):
    if unit in ("cm", "厘米"):
        return value
    if unit in ("mm", "毫米"):
        return value / 10.0
    return None


_BATCH_RE = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})-([0-9]{3})$")


def parse_batch(text):
    if not isinstance(text, str):
        return None
    value = normalize_text(text)
    match = _BATCH_RE.fullmatch(value)
    if not match:
        return None
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return value


def parse_quantity(text):
    if not isinstance(text, str):
        return None
    value = normalize_text(text)
    if re.fullmatch(r"[1-9][0-9]*件", value):
        return value
    return None


def parse_support_url(text):
    if not isinstance(text, str):
        return None
    value = normalize_text(text)
    match = re.fullmatch(r"https://([^/\s]+)(/.*)?", value)
    if not match:
        return None
    host = match.group(1)
    if not host:
        return None
    return value


def add_days(value, days):
    parsed = parse_date(value)
    if parsed is None:
        return None
    return parsed + timedelta(days=days)
