import os
import re
import struct

from .errors import AuditError
from .io_utils import material_bytes
from .model_gateway import ModelGateway
from .rule_common import load_model_json, valid_bbox

LOCATE_PROMPT = """只做一件事：在图像中定位目标字段所在的那一行文字，输出它的边界框。
只输出严格JSON：{"bbox":[x1,y1,x2,y2]}，坐标为0到1，原点在左上角。
只框“字段名：值”这一行本身，不得框入同一页上下相邻的其他字段行、整张标签、整页或整张图。
目标字段行：{target}"""

_GROUNDING_RE = re.compile(
    r"<\|begin_of_box\|>\s*\[\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,"
    r"\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]\]"
)
_ARRAY_BOX_RE = re.compile(
    r"\[\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]\]"
)


def _image_size(data):
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        if width and height:
            return width, height
    return None


def _pixel_box(raw, input_path, material):
    match = _GROUNDING_RE.search(raw) or _ARRAY_BOX_RE.search(raw)
    if not match:
        return None
    try:
        data = material_bytes(input_path, material)
    except (AuditError, OSError):
        return None
    size = _image_size(data[:33])
    if size is None:
        return None
    width, height = size
    x1, y1, x2, y2 = (float(value) for value in match.groups())
    return [
        round(x1 / width, 6),
        round(y1 / height, 6),
        round(x2 / width, 6),
        round(y2 / height, 6),
    ]


def locate_with_thinking(
    material,
    target,
    input_path,
    base_url,
    api_key,
    model=None,
    gateway=None,
):
    """用带定位能力的模型定向补齐缺失的标注框；失败时不伪造坐标。"""
    model = model or os.environ.get(
        "OPENAI_LOCATE_MODEL", "glm-4.1v-thinking-flash"
    )
    trace = {
        "kind": "locate_with_thinking",
        "annotation_id": target.get("annotation_id"),
        "material_id": material["material_id"],
        "page_index": material["page_index"],
        "model": model,
    }
    if gateway is None:
        if not base_url or not api_key:
            trace["error_code"] = "gateway_not_configured"
            return None, trace
        gateway = ModelGateway(model, base_url, api_key)
    prompt = LOCATE_PROMPT.replace("{target}", str(target.get("target") or ""))
    try:
        raw = gateway.complete(
            gateway.messages(prompt, {"materials": [material]}, input_path)
        )
    except AuditError as exc:
        trace["error_code"] = exc.code
        return None, trace
    trace["response"] = raw
    box = None
    try:
        value = load_model_json(raw)
        box = valid_bbox(value.get("bbox")) if isinstance(value, dict) else None
    except (ValueError, TypeError):
        box = None
    if box is None:
        box = _pixel_box(raw, input_path, material)
    if box is None:
        trace["rejected"] = "invalid_bbox"
        return None, trace
    if (box[2] - box[0]) * (box[3] - box[1]) > 0.25:
        trace["rejected"] = "bbox_too_large"
        return None, trace
    trace["bbox"] = box
    return box, trace
