"""两个审核结果集的版本对比。

以公开任务与真值为基准，把每个审核单元归入修复、新增错误、未变化或
版本不一致。规则版本不一致的单元不参与跨版本统计，避免把不同规则版本
的结论混算成同一规则的变化。
"""
from collections import defaultdict
from pathlib import Path

from .io_utils import rows, write_json
from .quality import metric
from .report import write_compare_page


def _summary(result):
    hidden = {"outcomes", "issues", "correct_ids", "label_confusion_matrix"}
    return {
        key: value
        for key, value in result.items()
        if key not in hidden
    }


def _groups(predictions, units):
    grouped = defaultdict(list)
    for row in predictions:
        if isinstance(row, dict) and row.get("audit_unit_id") in units:
            grouped[row["audit_unit_id"]].append(row)
    return grouped


def _versions(grouped, unit_id):
    return {
        (row.get("rule_id"), row.get("rule_version"))
        for row in grouped.get(unit_id, [])
    }


def compare_results(old_path, new_path, tasks_path, references_path, out_path):
    tasks = rows(tasks_path)
    references = rows(references_path)
    old_predictions = rows(old_path)
    new_predictions = rows(new_path)
    old_metric = metric(references, old_predictions, tasks)
    new_metric = metric(references, new_predictions, tasks)

    task_index = {task["audit_unit_id"]: task for task in tasks}
    old_rows = _groups(old_predictions, task_index)
    new_rows = _groups(new_predictions, task_index)

    comparable = []
    version_changed = []
    for unit_id, task in task_index.items():
        expected = {(task["rule_id"], task["rule_version"])}
        old_versions = _versions(old_rows, unit_id)
        new_versions = _versions(new_rows, unit_id)
        if (old_versions - expected) or (new_versions - expected):
            version_changed.append({
                "audit_unit_id": unit_id,
                "rule_id": task["rule_id"],
                "rule_version": task["rule_version"],
                "old_versions": sorted(
                    old_versions - expected, key=lambda item: (str(item[0]), str(item[1]))
                ),
                "new_versions": sorted(
                    new_versions - expected, key=lambda item: (str(item[0]), str(item[1]))
                ),
                "reason": "规则版本不一致，不参与跨版本统计",
            })
        else:
            comparable.append(unit_id)

    comparable_set = set(comparable)
    old_correct = set(old_metric["correct_ids"]) & comparable_set
    new_correct = set(new_metric["correct_ids"]) & comparable_set
    fixed = sorted(new_correct - old_correct)
    new_error = sorted(old_correct - new_correct)
    unchanged = sorted(comparable_set - set(fixed) - set(new_error))

    status_of = {}
    for unit_id in fixed:
        status_of[unit_id] = "fixed"
    for unit_id in new_error:
        status_of[unit_id] = "new_error"
    for unit_id in unchanged:
        status_of[unit_id] = "unchanged"
    for item in version_changed:
        status_of[item["audit_unit_id"]] = "version_changed"

    by_rule_version = {}
    details = []
    for task in tasks:
        unit_id = task["audit_unit_id"]
        key = task["rule_id"] + "@" + task["rule_version"]
        group = by_rule_version.setdefault(
            key,
            {"all": 0, "fixed": 0, "new_error": 0, "unchanged": 0, "version_changed": 0},
        )
        status = status_of[unit_id]
        group["all"] += 1
        group[status] += 1
        details.append({
            "audit_unit_id": unit_id,
            "rule_id": task["rule_id"],
            "rule_version": task["rule_version"],
            "status": status,
            "old_outcome": old_metric["outcomes"].get(unit_id),
            "new_outcome": new_metric["outcomes"].get(unit_id),
            "old_correct": unit_id in old_metric["correct_ids"],
            "new_correct": unit_id in new_metric["correct_ids"],
            "old_labels": [row.get("label") for row in old_rows.get(unit_id, [])],
            "new_labels": [row.get("label") for row in new_rows.get(unit_id, [])],
        })

    result = {
        "command": "compare",
        "old": str(old_path),
        "new": str(new_path),
        "input": str(tasks_path),
        "references": str(references_path),
        "all": len(comparable),
        "counts": {
            "fixed": len(fixed),
            "new_error": len(new_error),
            "unchanged": len(unchanged),
            "version_changed": len(version_changed),
        },
        "fixed": fixed,
        "new_error": new_error,
        "unchanged": unchanged,
        "version_changed": version_changed,
        "by_rule_version": by_rule_version,
        "rows": details,
        "old_metrics": _summary(old_metric),
        "new_metrics": _summary(new_metric),
    }
    write_json(out_path, result)
    page = Path(str(out_path)).with_suffix(".html")
    write_compare_page(page, result)
    result["page"] = str(page)
    result["out"] = str(out_path)
    hidden = {"rows", "fixed", "new_error", "unchanged"}
    return {key: value for key, value in result.items() if key not in hidden}
