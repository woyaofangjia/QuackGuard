import json
from pathlib import Path

from .contracts import (
    ContractError,
    read_rows,
    regular_bytes,
    safe_relative,
    strict_loads,
)
from .errors import AuditError


def rows(path, required=True):
    try:
        return read_rows(path, missing_ok=not required)
    except (ContractError, OSError) as exc:
        raise AuditError("invalid_jsonl", str(exc)) from exc


def load_json(path):
    try:
        content = regular_bytes(path).decode("utf-8")
        return strict_loads(content)
    except (ContractError, OSError, UnicodeDecodeError) as exc:
        raise AuditError("invalid_json", str(exc)) from exc


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        value, ensure_ascii=False, indent=2, allow_nan=False
    )
    path.write_text(content + "\n", encoding="utf-8")


def write_jsonl(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        for value in values
    ]
    content = "".join(line + "\n" for line in lines)
    path.write_text(content, encoding="utf-8")


def new_directory(path):
    path = Path(path)
    if path.is_symlink() or path.exists():
        raise AuditError("output_exists", str(path))
    path.mkdir(parents=True)
    return path


def material_bytes(input_path, material):
    relative = material["relative_path"]
    if not safe_relative(relative):
        raise AuditError("unsafe_material_path", relative)

    root = Path(input_path).resolve().parent
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            raise AuditError("unsafe_material_path", relative)
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
        return regular_bytes(resolved)
    except FileNotFoundError as exc:
        raise AuditError("material_not_found", relative) from exc
    except (ContractError, OSError, ValueError) as exc:
        raise AuditError("material_read_failed", relative) from exc
