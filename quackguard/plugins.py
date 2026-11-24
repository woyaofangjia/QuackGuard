from .contracts import prediction_errors
from .quality import metric


def conclusion(inputs, outputs, references, context):
    result = metric(references, outputs, inputs)
    metric_keys = (
        "accuracy",
        "machine_review_rate",
        "machine_accuracy",
        "applicability_accuracy",
        "label_only_accuracy",
        "quality",
    )
    return {
        "plugin_id": "quackguard.conclusion",
        "plugin_version": "0.1.0",
        "input_schema_version": "1.3.1",
        "metrics": {key: result[key] for key in metric_keys},
        "diagnostics": [],
        "errors": [],
    }


def evidence(inputs, outputs, references, context):
    tasks = {task["audit_unit_id"]: task for task in inputs}
    diagnostics = []
    for output in outputs:
        task = tasks.get(output.get("audit_unit_id"))
        if task:
            diagnostics.append({
                "audit_unit_id": task["audit_unit_id"],
                "issues": prediction_errors(output, task),
            })
    valid_count = sum(not item["issues"] for item in diagnostics)
    return {
        "plugin_id": "quackguard.evidence_structure",
        "plugin_version": "0.1.0",
        "input_schema_version": "1.3.1",
        "metrics": {"valid_structure_count": valid_count},
        "diagnostics": diagnostics,
        "errors": [],
        "limitation": "D1仅检查引用结构；证据内容语义核验在后续阶段实现。",
    }
