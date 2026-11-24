"""审核结果导出：逐任务 CSV + 与 evaluate 同源的指标 JSON。"""
import csv
from collections import defaultdict
from pathlib import Path

from .io_utils import rows, write_json
from .quality import metric

COLUMNS = (
    "audit_unit_id",
    "rule_id",
    "rule_version",
    "artifact_version",
    "execution_status",
    "label",
    "applicability",
    "reason_code",
    "correct",
    "issues",
)


def _summary(result):
    hidden = {"outcomes", "issues", "correct_ids", "label_confusion_matrix"}
    return {key: value for key, value in result.items() if key not in hidden}


def export_results(tasks_path, predictions_path, references_path, out_path):
    tasks = rows(tasks_path)
    predictions = rows(predictions_path)
    references = rows(references_path)
    metrics = metric(references, predictions, tasks)

    grouped = defaultdict(list)
    for row in predictions:
        if isinstance(row, dict) and row.get("audit_unit_id"):
            grouped[row["audit_unit_id"]].append(row)
    correct_ids = set(metrics["correct_ids"])

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for task in tasks:
            unit_id = task["audit_unit_id"]
            matched = grouped.get(unit_id, [])
            row = matched[0] if len(matched) == 1 else {}
            writer.writerow([
                unit_id,
                task["rule_id"],
                task["rule_version"],
                row.get("artifact_version") or "",
                row.get("execution_status")
                or ("missing" if not matched else "invalid"),
                row.get("label") or "",
                row.get("applicability") or "",
                row.get("reason_code") or "",
                "true" if unit_id in correct_ids else "false",
                "|".join(metrics["issues"].get(unit_id, [])),
            ])

    metrics_path = Path(str(out) + ".metrics.json")
    write_json(metrics_path, metrics)
    return {
        "command": "export",
        "rows": len(tasks),
        "out": str(out),
        "metrics_path": str(metrics_path),
        "metrics": _summary(metrics),
    }
