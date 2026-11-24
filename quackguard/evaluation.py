import hashlib
from pathlib import Path

from .errors import AuditError
from .io_utils import rows, write_json
from .plugin_registry import load_manifest, register_manifest, run_manifest
from .plugin_report import write_plugins_page
from .plugin_runner import run_plugin

CONTEXT = {"schema_version": "1.3.1", "purpose": "public_development"}


def _file_sha256(path):
    file = Path(path)
    if not file.exists():
        return None
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()
    except OSError as exc:
        raise AuditError("io_error", str(exc)) from exc


def _input_hashes(inputs_path, outputs_path, references_path):
    return {
        "inputs": _file_sha256(inputs_path),
        "outputs": _file_sha256(outputs_path),
        "references": _file_sha256(references_path),
    }


def _registrations(store, entries):
    if store is not None:
        return store.list_plugins()
    return [
        {
            "plugin_id": entry["plugin_id"],
            "plugin_version": entry["plugin_version"],
            "schema_version": entry["input_schema_version"],
            "entrypoint": entry["entrypoint"],
            "enabled": 1,
        }
        for entry in entries
    ]


def evaluate(
    plugin,
    inputs_path,
    outputs_path,
    references_path,
    out_path,
    plugin_timeout=None,
    store=None,
    manifest=None,
):
    if bool(plugin) == bool(manifest):
        raise AuditError(
            "plugin_or_manifest_required", "必须且只能提供--plugin或--manifest"
        )

    before = _input_hashes(inputs_path, outputs_path, references_path)
    inputs = rows(inputs_path)
    outputs = rows(outputs_path, required=False)
    references = rows(references_path)
    context = dict(CONTEXT)

    entries = None
    if manifest:
        entries = load_manifest(manifest)
        register_manifest(store, entries)
        report = run_manifest(
            entries,
            inputs,
            outputs,
            references,
            context,
            timeout=plugin_timeout,
            store=store,
        )
        report["manifest"] = str(manifest)
    else:
        report = run_plugin(
            plugin,
            inputs,
            outputs,
            references,
            context,
            timeout=plugin_timeout,
        )
        if store and not report["errors"]:
            store.register_plugin(
                report["plugin_id"], report["plugin_version"], plugin
            )

    after = _input_hashes(inputs_path, outputs_path, references_path)
    report["input_hashes"] = {"before": before, "after": after}
    report["input_unchanged"] = before == after
    report["input_error"] = (
        None
        if report["input_unchanged"]
        else {"code": "input_modified", "detail": "评估输入文件哈希发生变化"}
    )
    write_json(out_path, report)

    summary = {
        "command": "evaluate",
        "plugin": plugin,
        "manifest": str(manifest) if manifest else None,
        "errors": (
            report["errors"] if entries is not None else len(report["errors"])
        ),
        "input_unchanged": report["input_unchanged"],
        "out": str(out_path),
    }
    if entries is not None:
        page = Path(out_path).with_name("plugin_report.html")
        write_plugins_page(page, _registrations(store, entries), report)
        summary["page"] = str(page)
    return summary
