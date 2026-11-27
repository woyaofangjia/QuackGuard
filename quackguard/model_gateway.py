import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .errors import AuditError
from .io_utils import material_bytes

# 429/网络抖动退避：1s → 2s → 4s → 8s，最多重试 4 次。
RETRY_DELAYS = (1, 2, 4, 8)


def _cache_file(payload):
    root = os.environ.get("QUACKGUARD_MODEL_CACHE")
    if not root:
        return None
    key = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return Path(root) / key[:2] / (key[2:] + ".json")


def _read_cache(path):
    if path is None or not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    content = value.get("content") if isinstance(value, dict) else None
    return content if isinstance(content, str) else None


def _write_cache(path, content):
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"content": content}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _strip_code_fence(text):
    """去掉思考段与模型整体包裹的 Markdown 代码围栏，保留内部原文。"""
    stripped = text.strip()
    if "<answer>" in stripped:
        stripped = stripped.split("<answer>", 1)[1]
        if "</answer>" in stripped:
            stripped = stripped.split("</answer>", 1)[0]
        stripped = stripped.strip()
    elif stripped.startswith("<think>") and "<｜end▁of▁thinking｜>" in stripped:
        stripped = stripped.split("<｜end▁of▁thinking｜>", 1)[1].strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


class ModelGateway:
    def __init__(self, model, base_url, api_key=None, timeout=300):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def require_config(self):
        if not self.model:
            raise AuditError("model_not_configured")
        if not self.base_url:
            raise AuditError("gateway_not_configured")

    def messages(self, prompt, task, input_path):
        self.require_config()
        content = [{"type": "text", "text": prompt}]
        for material in task["materials"]:
            media_type = material["media_type"]
            if media_type not in ("image/png", "image/jpeg", "application/pdf"):
                raise AuditError("unsupported_media_type", media_type)
            data = material_bytes(input_path, material)
            content.append({
                "type": "text",
                "text": (
                    f"material_id={material['material_id']} "
                    f"page_index={material['page_index']}"
                ),
            })
            encoded = base64.b64encode(data).decode("ascii")
            if media_type == "application/pdf":
                # PDF 交给兼容文档输入的视觉模型，避免在网关层直接拒绝
                filename = str(material.get("relative_path", "material.pdf")).rsplit("/", 1)[-1]
                content.append({
                    "type": "file",
                    "file": {
                        "filename": filename,
                        "file_data": f"data:application/pdf;base64,{encoded}",
                    },
                })
            else:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                })
        return [{"role": "user", "content": content}]

    def complete(self, messages):
        if not self.model:
            raise AuditError("model_not_configured")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": int(os.environ.get("OPENAI_MAX_TOKENS", "1024")),
            "enable_thinking": False,
        }
        cache_path = _cache_file(payload)
        cached = _read_cache(cache_path)
        if cached is not None:
            return cached
        self.require_config()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )
        last_error = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    result = json.load(response)
                content = _strip_code_fence(
                    result["choices"][0]["message"]["content"]
                )
                _write_cache(cache_path, content)
                return content
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:200]
                last_error = AuditError(
                    "model_http_error", f"HTTP {exc.code} {detail}"
                )
                if exc.code != 429 and exc.code < 500:
                    raise last_error from exc
                if attempt == len(RETRY_DELAYS):
                    raise last_error from exc
                time.sleep(RETRY_DELAYS[attempt])
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = AuditError("model_call_failed", str(exc))
                if attempt == len(RETRY_DELAYS):
                    raise last_error from exc
                time.sleep(RETRY_DELAYS[attempt])
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise AuditError("invalid_model_response", str(exc)) from exc
        raise last_error
