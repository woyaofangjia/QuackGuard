import base64
import json
import urllib.error
import urllib.request

from .errors import AuditError
from .io_utils import material_bytes


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
        self.require_config()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 4096,
            "enable_thinking": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
            return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            raise AuditError("model_http_error", f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AuditError("model_call_failed", str(exc)) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AuditError("invalid_model_response", str(exc)) from exc
