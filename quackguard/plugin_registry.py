"""插件清单注册与逐插件独立运行。"""

import time

from .errors import AuditError
from .io_utils import load_json
from .plugin_runner import failure, run_plugin

SCHEMA_VERSION = "1.3.1"
MANIFEST_FIELDS = (
    "plugin_id",
    "plugin_version",
    "input_schema_version",
    "entrypoint",
)


def load_manifest(path):
    data = load_json(path)
    entries = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise AuditError("invalid_plugin_manifest", "plugins必须为非空数组")
    result = []
    seen = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise AuditError("invalid_plugin_manifest", f"第{index}项必须是对象")
        for field in MANIFEST_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise AuditError(
                    "invalid_plugin_manifest", f"第{index}项缺少{field}"
                )
        if entry["input_schema_version"] != SCHEMA_VERSION:
            raise AuditError(
                "invalid_plugin_manifest",
                f"第{index}项input_schema_version必须为{SCHEMA_VERSION}",
            )
        key = (entry["plugin_id"], entry["plugin_version"])
        if key in seen:
            raise AuditError(
                "invalid_plugin_manifest",
                f"重复插件{entry['plugin_id']}@{entry['plugin_version']}",
            )
        seen.add(key)
        result.append(dict(entry))
    return result


def register_manifest(store, entries):
    if store is not None:
        store.register_plugins(entries)


def _is_enabled(store, entry):
    if store is None:
        return True
    return store.plugin_enabled(entry["plugin_id"], entry["plugin_version"])


def _identity_errors(entry, report):
    errors = []
    if report.get("plugin_id") != entry["plugin_id"]:
        errors.append({
            "code": "plugin_identity_mismatch",
            "detail": "报告plugin_id与清单不一致",
        })
    if report.get("plugin_version") != entry["plugin_version"]:
        errors.append({
            "code": "plugin_identity_mismatch",
            "detail": "报告plugin_version与清单不一致",
        })
    if report.get("input_schema_version") != entry["input_schema_version"]:
        errors.append({
            "code": "plugin_identity_mismatch",
            "detail": "报告input_schema_version与清单不一致",
        })
    return errors


def _disabled_report(entry):
    return {
        "plugin_id": entry["plugin_id"],
        "plugin_version": entry["plugin_version"],
        "input_schema_version": entry["input_schema_version"],
        "metrics": {},
        "diagnostics": [],
        "errors": [],
        "status": "disabled",
        "invocations": 0,
        "duration_ms": 0,
    }


def _run_one(entry, inputs, outputs, references, context, timeout):
    started = time.monotonic()
    try:
        report = run_plugin(
            entry["entrypoint"],
            inputs,
            outputs,
            references,
            context,
            timeout=timeout,
        )
    except Exception as exc:  # 编排层兜底，单插件异常不得中断其他插件
        report = failure(entry["entrypoint"], "plugin_failed", str(exc))
    report["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    report["invocations"] = 1
    if report["errors"]:
        report["plugin_id"] = entry["plugin_id"]
        report["plugin_version"] = entry["plugin_version"]
        report["input_schema_version"] = entry["input_schema_version"]
        report["status"] = "failed"
    else:
        report["errors"] = _identity_errors(entry, report)
        report["status"] = "failed" if report["errors"] else "ok"
    return report


def run_manifest(
    entries,
    inputs,
    outputs,
    references,
    context,
    timeout=None,
    store=None,
):
    reports = []
    for entry in entries:
        if not _is_enabled(store, entry):
            reports.append(_disabled_report(entry))
            continue
        reports.append(
            _run_one(entry, inputs, outputs, references, context, timeout)
        )

    enabled_count = sum(report["status"] != "disabled" for report in reports)
    failed_count = sum(report["status"] == "failed" for report in reports)
    if not failed_count:
        status = "ok"
    elif failed_count == enabled_count:
        status = "failed"
    else:
        status = "partial"
    return {
        "command": "evaluate",
        "status": status,
        "plugin_count": len(entries),
        "enabled_count": enabled_count,
        "disabled_count": len(entries) - enabled_count,
        "invocations": sum(report["invocations"] for report in reports),
        "errors": sum(len(report["errors"]) for report in reports),
        "plugins": reports,
    }
