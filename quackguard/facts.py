import json
from dataclasses import dataclass
from typing import Any

from .errors import AuditError


@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    status: str = "value"
    material_id: str | None = None
    page_index: int | None = None
    bbox: tuple[float, float, float, float] | None = None

    def to_evidence(self):
        if self.material_id is None or self.page_index is None:
            raise AuditError("invalid_fact_provenance", self.key)
        if isinstance(self.value, str):
            text = self.value
        else:
            text = json.dumps(self.value, ensure_ascii=False)
        result = {
            "material_id": self.material_id,
            "page_index": self.page_index,
            "fact": text,
        }
        if self.bbox is not None:
            result["bbox"] = list(self.bbox)
        return result
