"""D9 全量回归编排：公开集、规则分支、对照组、标注、插件故障与可靠性检查。"""
import hashlib
from pathlib import Path

from .annotation import annotate_target
from .artifacts import build, load_policy
from .errors import AuditError
from .inference import OUTPUT_FORMAT, review_task
from .io_utils import load_json, new_directory, rows, write_json, write_jsonl
from .quality import metric, score_annotations
from .regression_checks import (
    audit_dependencies,
    export_consistency,
    integrity_from_metric,
    persistence_check,
    resume_check,
    run_plugin_faults,
    selective_rebuild,
)
from .regression_facts import DictGateway, GoldGateway
from .regression_report import write_regression_page

SELECT_RULES = [
    "B001", "R001", "R005", "R010", "R021",
    "R035", "R041", "R048", "R050", "R060",
]
METRIC_FIELDS = (
    "all", "correct", "accuracy", "label_only_accuracy",
    "applicability_accuracy", "machine_review_rate", "machine_accuracy",
    "correct_decisive_coverage", "quality", "pass", "reject", "ambiguous",
    "error", "invalid", "missing", "extraneous",
)
NAMING = {
    "A": "accuracy",
    "U": "correct_decisive_coverage",
    "Q": "quality",
    "M": "machine_review_rate",
}
BASELINE_NOTE = (
    "参考输出基线：Q=1仅证明公开接口与评分算术一致，不是系统成绩。"
)
CONTROL_NOTE = (
    "对照组验证：全不确定输出用于校验指标算术，不代表系统审核能力。"
)


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _metric_fields(result):
    return {key: result.get(key) for key in METRIC_FIELDS}


def freeze_block(rules_path, config_path, baseline_path, plugin_manifest_path):
    config = load_json(config_path)
    dependencies = audit_dependencies(Path(__file__).resolve().parent)
    freeze = {
        "rules_sha256": _sha256_file(rules_path),
        "config_sha256": _sha256_file(config_path),
        "prompt_sha256": hashlib.sha256(
            OUTPUT_FORMAT.encode("utf-8")
        ).hexdigest(),
        "baseline_sha256": _sha256_file(baseline_path),
        "plugin_manifest_sha256": _sha256_file(plugin_manifest_path),
        "model": config.get("model"),
        "dependencies": dependencies,
    }
    freeze["ok"] = dependencies["ok"]
    return freeze


def _rule_versions(policy):
    versions = {}
    for key in policy["rules"]:
        rule_id, version = key.split("@", 1)
        versions[rule_id] = version
    return versions


def _per_rule(references, result):
    correct_ids = set(result["correct_ids"])
    grouped = {}
    for reference in references:
        key = reference["rule_id"] + "@" + reference["rule_version"]
        group = grouped.setdefault(key, {"all": 0, "correct": 0})
        group["all"] += 1
        if reference["audit_unit_id"] in correct_ids:
            group["correct"] += 1
    for group in grouped.values():
        group["accuracy"] = group["correct"] / group["all"]
    return grouped


def run_public(input_path, references_path, policy):
    tasks = rows(input_path)
    references = rows(references_path)
    gold_by_id = {
        reference["audit_unit_id"]: reference for reference in references
    }
    outputs = []
    for task in tasks:
        gold = gold_by_id.get(task["audit_unit_id"])
        if gold is None:
            raise AuditError(
                "regression_missing_reference", task["audit_unit_id"]
            )
        output, _traces = review_task(
            GoldGateway(task, gold), policy, task, input_path
        )
        outputs.append(output)
    versions = {
        key: policy["artifact_version"] for key in policy["rules"]
    }
    result = metric(references, outputs, tasks, artifact_versions=versions)
    return outputs, {
        "units": len(tasks),
        "metrics": _metric_fields(result),
        "per_rule": _per_rule(references, result),
        "integrity": integrity_from_metric(result),
    }


def reference_baseline(tasks_path, references_path, baseline_path):
    tasks = rows(tasks_path)
    references = rows(references_path)
    baseline = rows(baseline_path)
    result = metric(references, baseline, tasks)
    return {
        "metrics": _metric_fields(result),
        "note": BASELINE_NOTE,
        "counted_as_system_score": False,
    }


def run_branches(cases, policy):
    versions = _rule_versions(policy)
    per_rule = {}
    failures = []
    for index, case in enumerate(cases, 1):
        rule_id = case["rule"]
        version = versions.get(rule_id)
        if version is None:
            failures.append({"case": index, "rule": rule_id, "error": "unknown_rule"})
            continue
        materials = case["materials"]
        context = {
            material["material_id"]: {
                "face_id": "front",
                "source_type": "photo",
                "is_standard_front_view": True,
            }
            for material in materials
        }
        context.update(case.get("context") or {})
        metadata = {"material_context": context}
        metadata.update(case.get("metadata") or {})
        task = {
            "audit_unit_id": "branch-%03d" % index,
            "sample_id": "branch-%03d" % index,
            "rule_id": rule_id,
            "rule_version": version,
            "review_date": case.get("review_date", "2026-06-15"),
            "metadata": metadata,
            "materials": materials,
        }
        gateway = DictGateway(case.get("docs") or {}, case.get("statuses"))
        output, _traces = review_task(gateway, policy, task, "branches.jsonl")
        passed = (
            output.get("label") == case["label"]
            and output.get("applicability") == case["appl"]
        )
        group = per_rule.setdefault(rule_id, {"all": 0, "passed": 0})
        group["all"] += 1
        if passed:
            group["passed"] += 1
        else:
            failures.append({
                "case": index,
                "rule": rule_id,
                "note": case.get("note"),
                "expected": [case["label"], case["appl"]],
                "actual": [output.get("label"), output.get("applicability")],
            })
    passed_count = sum(group["passed"] for group in per_rule.values())
    return {
        "cases": len(cases),
        "passed": passed_count,
        "failed": len(cases) - passed_count,
        "per_rule": per_rule,
        "failures": failures,
        "ok": len(cases) > 0 and not failures,
    }


def control_group(tasks_path, references_path, baseline_path):
    tasks = rows(tasks_path)
    references = rows(references_path)
    predictions = []
    for row in rows(baseline_path):
        changed = dict(row)
        changed["label"] = "ambiguous"
        changed["applicability"] = "applicable"
        changed["reason_code"] = "model_review"
        predictions.append(changed)
    result = metric(references, predictions, tasks)
    measured = _metric_fields(result)
    total = len(references)
    ambiguous = sum(1 for row in references if row["label"] == "ambiguous")
    accuracy = ambiguous / total if total else 0.0
    expected = {
        "all": total,
        "correct": ambiguous,
        "invalid": 0,
        "accuracy": accuracy,
        "machine_review_rate": 0,
        "machine_accuracy": None,
        "correct_decisive_coverage": 0,
        "quality": 0.7 * accuracy,
    }
    matched = True
    for key, value in expected.items():
        actual = measured.get(key)
        if isinstance(value, float):
            matched = matched and actual is not None and abs(actual - value) <= 1e-12
        else:
            matched = matched and actual == value
    return {
        "name": "L-FUNC-02 全不确定输出",
        "expected": expected,
        "measured": measured,
        "matched": matched,
        "note": CONTROL_NOTE,
        "counted_as_system_score": False,
        "ok": matched,
    }


def run_annotations(
    tasks_path, targets_path, references_path, gold_path, policy
):
    tasks = rows(tasks_path)
    targets = rows(targets_path)
    references = rows(references_path)
    gold_by_id = {
        row["audit_unit_id"]: row for row in rows(gold_path)
    }
    task_by_id = {task["audit_unit_id"]: task for task in tasks}
    reviews = {}
    outputs = []
    failures = []
    for target in targets:
        task = task_by_id.get(target["audit_unit_id"])
        gold = gold_by_id.get(target["audit_unit_id"])
        if task is None or gold is None:
            failures.append({
                "annotation_id": target["annotation_id"],
                "error": "missing_task_or_gold",
            })
            continue
        gateway = GoldGateway(task, gold)
        review = reviews.get(task["audit_unit_id"])
        if review is None:
            review = review_task(gateway, policy, task, tasks_path)
            reviews[task["audit_unit_id"]] = review
        output, _traces, error = annotate_target(
            gateway, policy, task, target, tasks_path, review=review
        )
        if error is not None:
            failures.append({
                "annotation_id": target["annotation_id"],
                "error": error.code,
            })
        else:
            outputs.append(output)
    score, details = score_annotations(references, outputs, tasks)
    correct = sum(item["correct"] for item in details)
    return {
        "targets": len(references),
        "produced": len(outputs),
        "correct": correct,
        "score": score,
        "failures": failures,
        "ok": score == 7.0 and not failures,
    }


def _checks(report):
    return {
        "dependencies": report["freeze"]["ok"],
        "public_integrity": report["public_set"]["integrity"]["ok"],
        "branches": report["branches"]["ok"],
        "control_group": report["control_group"]["ok"],
        "annotations": report["annotations"]["ok"],
        "plugin_faults": report["plugin_faults"]["ok"],
        "selective_rebuild": report["selective_rebuild"]["ok"],
        "persistence": report["persistence"]["ok"],
        "resume": report["resume"]["ok"],
        "export": report["export_consistency"]["ok"],
    }


def regress(
    rules_path,
    config_path,
    input_path,
    references_path,
    baseline_path,
    out_path,
    branches_path=None,
    annotation_tasks_path=None,
    annotation_targets_path=None,
    annotation_references_path=None,
    fault_timeout=1.0,
    store=None,
):
    root = new_directory(out_path)
    artifacts = root / "artifacts"
    build_result = build(rules_path, config_path, artifacts, store=store)
    policy = load_policy(artifacts)

    freeze = freeze_block(
        rules_path,
        config_path,
        baseline_path,
        Path(__file__).with_name("plugin_manifest.json"),
    )
    outputs, public = run_public(input_path, references_path, policy)
    predictions_path = root / "predictions.jsonl"
    write_jsonl(predictions_path, outputs)
    references = rows(references_path)

    if branches_path:
        branches = run_branches(rows(branches_path, required=False), policy)
    else:
        branches = {
            "cases": 0, "passed": 0, "failed": 0, "per_rule": {},
            "failures": [], "ok": True, "status": "skipped",
        }

    annotations = {
        "targets": 0, "produced": 0, "correct": 0, "score": 0,
        "failures": [], "ok": True, "status": "skipped",
    }
    if annotation_tasks_path and annotation_targets_path and annotation_references_path:
        annotations = run_annotations(
            annotation_tasks_path,
            annotation_targets_path,
            annotation_references_path,
            references_path,
            policy,
        )

    faults = run_plugin_faults(
        root / "faults", fault_timeout, rows(input_path), outputs, references
    )
    selective = selective_rebuild(
        rules_path, config_path, root / "selective", SELECT_RULES
    )
    persistence = persistence_check(
        root / "persistence.sqlite3",
        rules_path,
        config_path,
        root / "persistence",
    )
    resume = resume_check(
        artifacts, rules_path, config_path, root / "resume.sqlite3"
    )
    export = export_consistency(
        input_path, predictions_path, references_path, root / "export"
    )

    report = {
        "command": "regress",
        "artifact_version": build_result["artifact_version"],
        "rule_versions": build_result["rule_versions"],
        "freeze": freeze,
        "naming": NAMING,
        "public_set": public,
        "reference_baseline": reference_baseline(
            input_path, references_path, baseline_path
        ),
        "branches": branches,
        "control_group": control_group(
            input_path, references_path, baseline_path
        ),
        "annotations": annotations,
        "plugin_faults": faults,
        "selective_rebuild": selective,
        "persistence": persistence,
        "resume": resume,
        "export_consistency": export,
    }
    report["checks"] = _checks(report)
    report["status"] = (
        "passed" if all(report["checks"].values()) else "failed"
    )
    write_json(root / "regression_report.json", report)
    page = root / "regression.html"
    write_regression_page(page, report)
    if report["status"] != "passed":
        failed = [
            key for key, value in report["checks"].items() if not value
        ]
        raise AuditError("regression_failed", ",".join(failed))
    return {
        "command": "regress",
        "status": report["status"],
        "artifact_version": report["artifact_version"],
        "accuracy": public["metrics"]["accuracy"],
        "decisive_coverage": public["metrics"]["correct_decisive_coverage"],
        "quality": public["metrics"]["quality"],
        "machine_review_rate": public["metrics"]["machine_review_rate"],
        "checks": report["checks"],
        "out": str(root),
        "page": str(page),
        "report": str(root / "regression_report.json"),
    }
