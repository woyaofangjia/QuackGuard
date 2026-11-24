import json
from pathlib import Path

from .contracts import (
    MAX_LINE_BYTES,
    MAX_RECORDS,
    ContractError,
    regular_bytes,
    safe_relative,
    strict_loads,
)
from .errors import AuditError


def numbered_rows(path, required=True):
    path = Path(path)
    try:
        if not path.exists():
            if path.is_symlink():
                raise ContractError("Broken symlink")
            if not required:
                return []
            raise ContractError("Required file missing")
        try:
            text = regular_bytes(path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("JSONL must be UTF-8") from exc
        result = []
        for line_number, line in enumerate(text.split("\n"), 1):
            line = line.rstrip("\r")
            if not line.strip():
                continue
            if len(line.encode()) > MAX_LINE_BYTES:
                raise ContractError("Oversized JSONL line")
            try:
                value = strict_loads(line)
            except ContractError as exc:
                raise ContractError(
                    f"{path.name}:{line_number}: {exc}"
                ) from exc
            result.append((line_number, value))
            if len(result) > MAX_RECORDS:
                raise ContractError("Too many records")
        return result
    except (ContractError, OSError) as exc:
        raise AuditError("invalid_jsonl", str(exc)) from exc


def rows(path, required=True):
    return [value for _, value in numbered_rows(path, required)]


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
