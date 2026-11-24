"""D9 可靠性检查：依赖审计、完整性、重建、持久化、恢复、导出与插件故障。"""
import ast
import csv
import hashlib
import json
import sys
from pathlib import Path

from .artifact_hash import path_digest
from .artifacts import build
from .export import export_results
from .io_utils import load_json, new_directory, rows
from .plugin_registry import load_manifest, register_manifest, run_manifest
from .quality import metric
from .storage import Store

VERSION_ISSUES = {
    "wrong_unit_rule_or_version",
    "artifact_version_not_executed",
}
PLUGIN_CONTEXT = {"schema_version": "1.3.1", "purpose": "public_development"}


def _import_roots(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
        ):
            roots.add(node.module.split(".")[0])
    return roots


def audit_dependencies(package_dir):
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    found = set()
    for path in sorted(Path(package_dir).glob("*.py")):
        found.update(_import_roots(path))
    third_party = sorted(found - stdlib - {"quackguard"})
    return {
        "third_party": third_party,
        "install": "仅Python标准库，无需pip安装",
        "ok": not third_party,
    }


def integrity_from_metric(result):
    issues = result.get("issues") or {}
    duplicate = 0
    wrong_version = 0
    for errors in issues.values():
        if "duplicate_output" in errors:
            duplicate += 1
        elif VERSION_ISSUES.intersection(errors):
            wrong_version += 1
    invalid = result.get("invalid", 0)
    counts = {
        "missing": result.get("missing", 0),
        "duplicate": duplicate,
        "extraneous": result.get("extraneous", 0),
        "unidentified": result.get("unidentified_invalid_records", 0),
        "wrong_version": wrong_version,
        "other_invalid": invalid - duplicate - wrong_version,
    }
    counts["all"] = result.get("all", 0)
    counts["ok"] = counts["all"] > 0 and all(
        value == 0
        for key, value in counts.items()
        if key not in {"all", "ok"}
    )
    return counts


def selective_rebuild(rules_path, config_path, work_path, rule_ids):
    work = Path(work_path)
    work.mkdir(parents=True, exist_ok=True)
    full_root = work / "full"
    only_root = work / "only"
    full = build(rules_path, config_path, full_root)
    only = build(rules_path, config_path, only_root, only=rule_ids)

    full_policy = load_json(full_root / "engine" / "policy.json")
    only_policy = load_json(only_root / "engine" / "policy.json")
    entries = load_json(only_root / "artifact_manifest.json")["entries"]
    built = sorted({entry["rule_id"] for entry in entries})
    expected = set(rule_ids)
    full_hashes = full_policy.get("source_hashes", {})
    only_hashes = only_policy.get("source_hashes", {})
    source_hash_match = set(only_hashes) <= set(full_hashes) and all(
        full_hashes[key] == only_hashes[key] for key in only_hashes
    )
    result = {
        "requested": len(expected),
        "built": len(built),
        "missing": sorted(expected - set(built)),
        "extra": sorted(set(built) - expected),
        "source_hash_match": source_hash_match,
        "full_artifact_version": full["artifact_version"],
        "only_artifact_version": only["artifact_version"],
    }
    result["ok"] = (
        result["built"] == len(expected)
        and not result["missing"]
        and not result["extra"]
        and source_hash_match
    )
    return result


def persistence_check(db_path, rules_path, config_path, work_path):
    root = Path(work_path)
    store = Store(db_path)
    built = build(rules_path, config_path, root, store=store)
    store.register_plugin(
        "regression.probe", "0.1.0", "quackguard.regression_faults:crash"
    )
    store.set_plugin_enabled("regression.probe", "0.1.0", False)
    store.set_active(
        root,
        built["artifact_version"],
        path_digest(root),
        "regression",
    )
    store.close()

    reopened = Store(db_path)
    builds = reopened.list_builds()
    active = reopened.get_active()
    plugin = reopened.get_plugin("regression.probe", "0.1.0")
    reopened.close()

    build_persisted = any(
        row["artifact_version"] == built["artifact_version"] for row in builds
    )
    active_persisted = (
        active is not None
        and active["artifact_version"] == built["artifact_version"]
    )
    plugin_disabled_persisted = plugin is not None and not bool(plugin["enabled"])
    return {
        "build_persisted": build_persisted,
        "active_persisted": active_persisted,
        "plugin_disabled_persisted": plugin_disabled_persisted,
        "ok": build_persisted and active_persisted and plugin_disabled_persisted,
    }


def resume_check(build_root, rules_path, config_path, db_path):
    resumed = build(rules_path, config_path, build_root, resume=True)
    skipped = resumed.get("skipped_rule_versions", 0)
    expected_skipped = resumed.get("rule_versions", 0)

    store = Store(db_path)
    failed_task = store.start_task("build")
    failed_run = store.start_run(
        failed_task, "build", rules_path, build_root,
        arguments={"command": "build"},
    )
    store.finish(failed_task, failed_run, "failed", "io_error")
    successor = store.start_task("build", retry_of=failed_task)
    successor_run = store.start_run(
        successor, "build", rules_path, build_root,
        arguments={"command": "build", "resume": True},
    )
    store.finish(successor, successor_run, "completed")
    linked = store.retry_successor(failed_task)
    store.close()

    retry_links_to_successor = (
        linked is not None and linked["task_id"] == successor
    )
    result = {
        "status": resumed.get("status"),
        "expected_skipped": expected_skipped,
        "skipped_rule_versions": skipped,
        "failed_task_recorded": True,
        "retry_successor_linked": retry_links_to_successor,
    }
    result["ok"] = (
        result["status"] == "resumed"
        and expected_skipped > 0
        and skipped == expected_skipped
        and retry_links_to_successor
    )
    return result


def _summary(result):
    hidden = {"outcomes", "issues", "correct_ids", "label_confusion_matrix"}
    return {key: value for key, value in result.items() if key not in hidden}


def _payload_hash(inputs, outputs, references):
    encoded = json.dumps(
        {"inputs": inputs, "outputs": outputs, "references": references},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def run_plugin_faults(work_path, timeout, inputs, outputs, references):
    new_directory(work_path)
    manifest = load_manifest(
        Path(__file__).with_name("plugin_manifest.json")
    )
    healthy = manifest[0]
    entries = [
        healthy,
        {
            "plugin_id": "regression.crash",
            "plugin_version": "0.1.0",
            "input_schema_version": "1.3.1",
            "entrypoint": "quackguard.regression_faults:crash",
        },
        {
            "plugin_id": "regression.slow",
            "plugin_version": "0.1.0",
            "input_schema_version": "1.3.1",
            "entrypoint": "quackguard.regression_faults:slow",
        },
    ]
    before = _payload_hash(inputs, outputs, references)
    store = Store(Path(work_path) / "faults.sqlite3")
    register_manifest(store, entries)
    first = run_manifest(
        entries,
        inputs,
        outputs,
        references,
        dict(PLUGIN_CONTEXT),
        timeout=timeout,
        store=store,
    )
    store.set_plugin_enabled("regression.slow", "0.1.0", False)
    second = run_manifest(
        entries,
        inputs,
        outputs,
        references,
        dict(PLUGIN_CONTEXT),
        timeout=timeout,
        store=store,
    )
    store.close()
    after = _payload_hash(inputs, outputs, references)

    reports = {report["plugin_id"]: report for report in first["plugins"]}
    disabled = {report["plugin_id"]: report for report in second["plugins"]}
    healthy_status = reports[healthy["plugin_id"]]["status"]
    crash_errors = [item["code"] for item in reports["regression.crash"]["errors"]]
    slow_errors = [item["code"] for item in reports["regression.slow"]["errors"]]
    result = {
        "healthy": healthy["plugin_id"],
        "healthy_status": healthy_status,
        "crash_status": reports["regression.crash"]["status"],
        "crash_error": crash_errors[0] if crash_errors else None,
        "timeout_status": reports["regression.slow"]["status"],
        "timeout_error": slow_errors[0] if slow_errors else None,
        "disabled_status": disabled["regression.slow"]["status"],
        "disabled_invocations": disabled["regression.slow"]["invocations"],
        "healthy_second_status": disabled[healthy["plugin_id"]]["status"],
        "input_unchanged": before == after,
    }
    result["ok"] = (
        healthy_status == "ok"
        and result["crash_status"] == "failed"
        and result["crash_error"] == "plugin_failed"
        and result["timeout_status"] == "failed"
        and result["timeout_error"] == "plugin_timeout"
        and result["disabled_status"] == "disabled"
        and result["disabled_invocations"] == 0
        and result["healthy_second_status"] == "ok"
        and result["input_unchanged"]
    )
    return result


def export_consistency(tasks_path, predictions_path, references_path, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    first = export_results(
        tasks_path, predictions_path, references_path, out / "first.csv"
    )
    second = export_results(
        tasks_path, predictions_path, references_path, out / "second.csv"
    )
    first_csv = Path(first["out"]).read_bytes()
    second_csv = Path(second["out"]).read_bytes()
    first_metrics = Path(first["metrics_path"]).read_bytes()
    second_metrics = Path(second["metrics_path"]).read_bytes()
    repeat_identical = (
        first_csv == second_csv and first_metrics == second_metrics
    )

    tasks = rows(tasks_path)
    expected_metric = metric(
        rows(references_path), rows(predictions_path), tasks
    )
    exported_metric = load_json(first["metrics_path"])
    metric_match = exported_metric == expected_metric

    with Path(first["out"]).open(encoding="utf-8", newline="") as handle:
        exported_rows = list(csv.DictReader(handle))
    identifiers = [row["audit_unit_id"] for row in exported_rows]
    rows_ok = (
        len(identifiers) == len(tasks)
        and len(set(identifiers)) == len(identifiers)
    )
    correct_ids = set(expected_metric["correct_ids"])
    flags_ok = all(
        row["correct"] == ("true" if row["audit_unit_id"] in correct_ids else "false")
        for row in exported_rows
    )
    result = {
        "rows": len(exported_rows),
        "repeat_identical": repeat_identical,
        "metric_match": metric_match,
        "unique_ids": rows_ok,
        "correct_flags": flags_ok,
        "summary": _summary(expected_metric),
    }
    result["ok"] = repeat_identical and metric_match and rows_ok and flags_ok
    return result
