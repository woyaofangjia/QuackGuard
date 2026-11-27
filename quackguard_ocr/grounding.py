"""本地 OCR 证据定位（可选依赖：rapidocr_onnxruntime + opencv-python）。

完全离线运行；未安装依赖或设置 QUACKGUARD_OCR=0 时返回 None，
调用方继续使用模型框或定向定位，不改变无 OCR 环境的行为。
"""
import os
import re
import threading
import unicodedata

from quackguard.rule_common import valid_bbox

_ENGINE = None
_ENGINE_ERROR = None
_LOCK = threading.Lock()

# 相近字段标签：只命中标签时用于排除“主型号/覆盖型号”等错误行
_OTHER_LABELS = (
    "主型号", "覆盖型号", "材料编号", "批次号", "批次备注", "运输记录",
    "序列号", "有效起始日", "有效截止日", "生产日期", "产品名称", "支持地址",
)


def _normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower()


def _engine():
    global _ENGINE, _ENGINE_ERROR
    if _ENGINE is not None or _ENGINE_ERROR is not None:
        return _ENGINE
    with _LOCK:
        if _ENGINE is None and _ENGINE_ERROR is None:
            try:
                from rapidocr_onnxruntime import RapidOCR

                _ENGINE = RapidOCR()
            except Exception as exc:  # noqa: BLE001
                _ENGINE_ERROR = str(exc)
    return _ENGINE


def available():
    return _engine() is not None


def _decode(data):
    import cv2
    import numpy as np

    array = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def _to_bbox(points, width, height):
    if not points or not width or not height:
        return None
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return valid_bbox([
        round(min(xs) / width, 6),
        round(min(ys) / height, 6),
        round(max(xs) / width, 6),
        round(max(ys) / height, 6),
    ])


def _candidate_score(text, value, label):
    """给 OCR 行打分；值命中优先，只命中标签时排除相近字段。"""
    target = _normalized(text)
    if not target:
        return 0
    expected = _normalized(value) if value else ""
    name = _normalized(label) if label else ""
    score = 0
    if expected:
        if target == expected:
            score = 8
        elif expected in target:
            score = 6
        elif len(target) >= 2 and target in expected:
            score = 3
    if name and name in target:
        score += 2
    if score <= 2 and name:
        # 仅靠标签命中时，排除同一行含“主型号/覆盖型号”等其他字段的情况
        if any(other != label and other in target for other in _OTHER_LABELS):
            return 0
    return score


def _extend_mask(image, bbox):
    """值不可读时，把标签框向右扩展到相邻的灰色遮挡块。"""
    import numpy as np

    height, width = image.shape[:2]
    x1, y1, x2, y2 = bbox
    left = int(x2 * width) + 2
    right = min(width, int(x2 * width) + int(0.2 * width))
    top = max(0, int(y1 * height) - 2)
    bottom = min(height, int(y2 * height) + 2)
    patch = image[top:bottom, left:right]
    if patch.size == 0:
        return bbox
    maximum = patch.max(axis=2)
    minimum = patch.min(axis=2)
    mean = patch.mean(axis=2)
    mask = (maximum - minimum <= 18) & (mean >= 90) & (mean <= 215)
    columns = np.where(mask.sum(axis=0) >= max(2, int(0.4 * (bottom - top))))[0]
    if len(columns) == 0:
        return bbox
    runs = []
    start = previous = int(columns[0])
    for column in columns[1:]:
        column = int(column)
        if column - previous > 3:
            runs.append((start, previous))
            start = column
        previous = column
    runs.append((start, previous))
    start, end = runs[0]
    if end - start < int(0.01 * width):
        return bbox
    rows = np.where(
        mask[:, start:end + 1].sum(axis=1) >= max(2, int(0.3 * (end - start + 1)))
    )[0]
    if len(rows) == 0:
        return bbox
    return valid_bbox([
        round(min(x1, left / width), 6),
        round(min(y1, (top + int(rows.min())) / height), 6),
        round(max(x2, (left + end) / width), 6),
        round(max(y2, (top + int(rows.max())) / height), 6),
    ])


def locate_text(data, value, label, engine=None, annotation_id=None,
                material_id=None, page_index=None):
    """在材料图片中定位字段行，返回 (归一化bbox, trace)。"""
    trace = {
        "kind": "locate_with_ocr",
        "annotation_id": annotation_id,
        "material_id": material_id,
        "page_index": page_index,
        "engine": "rapidocr_onnxruntime",
    }
    if os.environ.get("QUACKGUARD_OCR") == "0":
        trace["error_code"] = "ocr_disabled"
        return None, trace
    engine = engine or _engine()
    if engine is None:
        trace["error_code"] = "ocr_not_available"
        return None, trace
    try:
        image = _decode(data)
    except Exception as exc:  # noqa: BLE001
        trace["error_code"] = "ocr_image_error"
        trace["detail"] = str(exc)
        return None, trace
    if image is None or not getattr(image, "shape", None):
        trace["error_code"] = "ocr_image_error"
        return None, trace
    try:
        result, _ = engine(image)
    except Exception as exc:  # noqa: BLE001
        trace["error_code"] = "ocr_failed"
        trace["detail"] = str(exc)
        return None, trace

    height, width = image.shape[:2]
    best = None
    for item in result or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        box, text, confidence = item[0], item[1], item[2]
        score = _candidate_score(text, value, label)
        if score <= 0:
            continue
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        rank = (score, confidence)
        if best is None or rank > best[0]:
            best = (rank, box, text, confidence)
    if best is None:
        trace["error_code"] = "ocr_no_match"
        return None, trace

    _, box, text, confidence = best
    bbox = _to_bbox(box, width, height)
    if bbox is None:
        trace["error_code"] = "ocr_invalid_bbox"
        return None, trace
    if not value:
        bbox = _extend_mask(image, bbox) or bbox
    trace.update({
        "text": text,
        "confidence": round(confidence, 4),
        "bbox": bbox,
    })
    return bbox, trace
