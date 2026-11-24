"""Protocol 1.3.1 scoring arithmetic for development evaluation."""

from collections import defaultdict

from .contracts import (
    APPLICABILITY,
    LABELS,
    ContractError,
    annotation_errors,
    input_errors,
    iou,
    nonempty,
    prediction_errors,
)
from .normalization import annotation_values_equal


def index_inputs(tasks):
    indexed = {}
    for task in tasks:
        errors = input_errors(task)
        if errors:
            raise ContractError("Invalid organizer input: " + str(errors))
        unit_id = task["audit_unit_id"]
        if unit_id in indexed:
            raise ContractError("Duplicate organizer input ID")
        indexed[unit_id] = task
    return indexed


def metric(references, predictions, tasks, artifact_versions=None):
    inputs = index_inputs(tasks) if isinstance(tasks, list) else tasks
    references_by_id = {}
    prediction_groups = defaultdict(list)
    extraneous = 0
    unidentified = 0

    for reference in references:
        if not isinstance(reference, dict):
            raise ContractError("Invalid reference")
        if not nonempty(reference.get("audit_unit_id"), 256):
            raise ContractError("Invalid reference")
        if reference["label"] not in LABELS:
            raise ContractError("Invalid reference")
        if reference["applicability"] not in APPLICABILITY:
            raise ContractError("Invalid reference")
        unit_id = reference["audit_unit_id"]
        if unit_id in references_by_id or unit_id not in inputs:
            raise ContractError("Reference/input mismatch or duplicate")
        for key in ("rule_id", "rule_version"):
            if reference[key] != inputs[unit_id][key]:
                raise ContractError("Reference rule/version mismatch")
        references_by_id[unit_id] = reference

    for prediction in predictions:
        if not isinstance(prediction, dict):
            unidentified += 1
            continue
        if not nonempty(prediction.get("audit_unit_id"), 256):
            unidentified += 1
            continue
        unit_id = prediction["audit_unit_id"]
        if unit_id not in references_by_id:
            extraneous += 1
            continue
        prediction_groups[unit_id].append(prediction)

    counts = {
        "all": len(references),
        "correct": 0,
        "label_only_correct": 0,
        "applicability_correct": 0,
        "binary_correct": 0,
        "binary_truth": sum(
            reference["label"] in ("pass", "reject")
            for reference in references
        ),
        "pass": 0,
        "reject": 0,
        "ambiguous": 0,
        "error": 0,
        "invalid": 0,
        "missing": 0,
        "extraneous": extraneous,
        "unidentified_invalid_records": unidentified,
    }
    outcomes = {}
    correct_ids = []
    issues = {}
    confusion = {
        truth: {label: 0 for label in LABELS + ("error", "invalid", "missing")}
        for truth in LABELS
    }

    for unit_id, reference in references_by_id.items():
        rows = prediction_groups.get(unit_id, [])
        errors = []
        if not rows:
            outcome = "missing"
        elif len(rows) > 1:
            outcome = "invalid"
            errors = ["duplicate_output"]
        else:
            expected_version = None
            if artifact_versions is not None:
                key = reference["rule_id"] + "@" + reference["rule_version"]
                expected_version = artifact_versions.get(key)
                if expected_version is None:
                    errors = ["rule_artifact_not_built"]
            errors += prediction_errors(
                rows[0], inputs[unit_id], expected_version
            )
            if errors:
                outcome = "invalid"
            elif rows[0]["execution_status"] == "error":
                outcome = "error"
            else:
                outcome = rows[0]["label"]

        outcomes[unit_id] = outcome
        counts[outcome] += 1
        confusion[reference["label"]][outcome] += 1
        if errors:
            issues[unit_id] = errors
        if outcome not in LABELS:
            continue

        label_correct = outcome == reference["label"]
        applicability_correct = (
            rows[0]["applicability"] == reference["applicability"]
        )
        if label_correct:
            counts["label_only_correct"] += 1
        if applicability_correct:
            counts["applicability_correct"] += 1
        if label_correct and applicability_correct:
            counts["correct"] += 1
            correct_ids.append(unit_id)
            if outcome in ("pass", "reject"):
                counts["binary_correct"] += 1

    total = counts["all"]
    accuracy = counts["correct"] / total if total else None
    if counts["binary_truth"]:
        decisive_coverage = counts["binary_correct"] / counts["binary_truth"]
    else:
        decisive_coverage = accuracy
    binary_outputs = counts["pass"] + counts["reject"]
    machine_accuracy = None
    if binary_outputs:
        machine_accuracy = counts["binary_correct"] / binary_outputs
    quality = None
    if total:
        quality = 0.7 * accuracy + 0.3 * decisive_coverage

    counts.update(
        accuracy=accuracy,
        label_only_accuracy=(
            counts["label_only_correct"] / total if total else None
        ),
        applicability_accuracy=(
            counts["applicability_correct"] / total if total else None
        ),
        machine_review_rate=binary_outputs / total if total else None,
        machine_accuracy=machine_accuracy,
        correct_decisive_coverage=decisive_coverage,
        quality=quality,
        correct_ids=correct_ids,
        outcomes=outcomes,
        issues=issues,
        label_confusion_matrix=confusion,
        protocol_version="1.3.1",
    )
    return counts


def score_annotations(references, predictions, tasks):
    inputs = index_inputs(tasks) if isinstance(tasks, list) else tasks
    prediction_groups = defaultdict(list)
    for prediction in predictions:
        if not isinstance(prediction, dict):
            continue
        if nonempty(prediction.get("annotation_id"), 256):
            prediction_groups[prediction["annotation_id"]].append(prediction)

    details = []
    seen = set()
    for reference in references:
        annotation_id = reference["annotation_id"]
        if annotation_id in seen or reference["audit_unit_id"] not in inputs:
            raise ContractError("Bad annotation reference")
        seen.add(annotation_id)
        rows = prediction_groups.get(annotation_id, [])
        if len(rows) != 1:
            issues = ["missing_or_duplicate_annotation"]
            correct = False
        else:
            prediction = rows[0]
            issues = annotation_errors(
                prediction, reference, inputs[reference["audit_unit_id"]]
            )
            correct = (
                not issues
                and prediction["page_index"] == reference["page_index"]
                and prediction["label"] == reference["expected_label"]
                and prediction["status"] == reference["expected_status"]
                and annotation_values_equal(
                    prediction["value"], reference["expected_value"]
                )
                and (
                    iou(prediction["bbox"], reference["expected_bbox"])
                    >= reference["bbox_iou_min"]
                )
            )
        details.append({
            "annotation_id": annotation_id,
            "correct": bool(correct),
            "issues": issues,
        })

    if not references:
        raise ContractError("Empty annotation reference group")
    score = 7 * sum(item["correct"] for item in details) / len(references)
    return score, details
