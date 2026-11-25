"""标注复用同一套视觉事实与审核结论。

label 取自 review_task 的规则判定，value/status/bbox 取自同一批事实文档，
不再单独让模型对整条规则下结论，避免事实与标注脱节。
"""
from .artifacts import load_policy
from .contracts import annotation_errors, input_errors
from .errors import AuditError
from .inference import review_task
from .io_utils import rows, write_json, write_jsonl
from .model_gateway import ModelGateway
from .rule_common import (
    normalize_text,
    parse_batch,
    parse_date,
    parse_quantity,
    parse_support_url,
    valid_bbox,
)

# 目标关键词 -> (事实字段, 回填原文时使用的字段名)，长关键词优先匹配
TARGET_FIELDS = (
    ("主型号", "main_model", "主型号"),
    ("型号", "model", "型号"),
    ("材料编号", "material_code", "材料编号"),
    ("批次号", "batch_number", "批次号"),
    ("序列号", "serial_number", "序列号"),
    ("支持地址", "support_url", "支持地址"),
    ("件数", "quantity", "件数"),
    ("生产日期", "production_date", "生产日期"),
    ("有效起始日", "valid_from", "有效起始日"),
    ("有效截止日", "valid_to", "有效截止日"),
    ("产品名称英文", "product_name_en", "产品名称英文"),
    ("产品名称", "product_name", "产品名称"),
)

_STRICT_FORMATS = {
    "material_code": lambda text: _material_code_ok(text),
    "quantity": lambda text: parse_quantity(text) is not None,
    "support_url": lambda text: parse_support_url(text) is not None,
    "batch_number": lambda text: parse_batch(text) is not None,
    "production_date": lambda text: parse_date(text) is not None,
    "valid_from": lambda text: parse_date(text) is not None,
    "valid_to": lambda text: parse_date(text) is not None,
}


def _material_code_ok(text):
    if not isinstance(text, str):
        return False
    value = normalize_text(text)
    return (
        value.startswith("QLAB-")
        and value[5:].isdigit()
        and len(value[5:]) == 6
    )


def target_field(target):
    text = target.get("target")
    if not isinstance(text, str):
        return None, None
    for keyword, field, label in TARGET_FIELDS:
        if keyword in text:
            return field, label
    return None, None


def _select_document(facts, target, field):
    for fact in facts:
        if (
            fact.get("material_id") != target.get("material_id")
            or fact.get("page_index") != target.get("page_index")
        ):
            continue
        documents = fact.get("documents")
        if not isinstance(documents, list) or not documents:
            return None
        for document in documents:
            fields = document.get("fields")
            if isinstance(fields, dict) and field in fields:
                return document
        return documents[0]
    return None


def _field_value(document, field):
    fields = document.get("fields")
    item = fields.get(field) if isinstance(fields, dict) else None
    if not isinstance(item, dict):
        return None, "unreadable", None
    bbox = valid_bbox(item.get("bbox"))
    status = item.get("status")
    if status == "value":
        raw = item.get("value")
        if isinstance(raw, list):
            text = "、".join(str(value) for value in raw)
        elif isinstance(raw, str):
            text = raw
        else:
            return None, "unreadable", bbox
        check = _STRICT_FORMATS.get(field)
        if check is not None and not check(text):
            return text, "invalid_value", bbox
        return text, "value", bbox
    return None, "unreadable", bbox


def build_annotation_output(target, document, review):
    field, label = target_field(target)
    if field:
        text, status, bbox = _field_value(document, field)
    else:
        text, status, bbox = None, "unreadable", None
    include_name = "包含字段名" in (target.get("target") or "")
    if text is not None and include_name:
        text = label + "：" + text
    if bbox is None:
        # 字段未给框时退回该文档自身的可见区域；仍无框则显式失败，不用整帧冒充。
        bbox = valid_bbox(document.get("bbox"))
    return {
        "annotation_id": target["annotation_id"],
        "audit_unit_id": target["audit_unit_id"],
        "material_id": target["material_id"],
        "page_index": target["page_index"],
        "value": text,
        "status": status,
        "label": review["label"],
        "bbox": bbox,
    }


def annotate_target(gateway, policy, task, target, input_path, review=None):
    if review is None:
        review = review_task(gateway, policy, task, input_path)
    review_output, traces = review
    fact_traces = [trace for trace in traces if "response" in trace]
    if review_output.get("execution_status") != "completed":
        error = AuditError(
            review_output.get("error_code") or "execution_error",
            review_output.get("reason"),
        )
        return None, fact_traces, error
    facts = [
        trace["parsed_facts"]
        for trace in traces
        if isinstance(trace.get("parsed_facts"), dict)
    ]
    field, _ = target_field(target)
    if field is None:
        return None, fact_traces, AuditError(
            "unknown_annotation_target", target.get("target")
        )
    document = _select_document(facts, target, field)
    if document is None:
        return None, fact_traces, AuditError(
            "fact_target_missing", target.get("material_id")
        )
    output = build_annotation_output(target, document, review_output)
    if output["bbox"] is None:
        return None, fact_traces, AuditError(
            "annotation_bbox_missing",
            "模型未给出可核验的标注区域，不用整帧冒充",
        )
    errors = annotation_errors(output, target, task)
    if errors:
        return None, fact_traces, AuditError("invalid_annotation_output", str(errors))
    return output, fact_traces, None


def _optional_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def annotate(input_path, targets_path, artifacts_path, out_path, base_url, api_key):
    policy = load_policy(artifacts_path)
    gateway = ModelGateway(
        model=policy.get("model"),
        base_url=base_url,
        api_key=api_key,
    )
    tasks = {}
    for task in rows(input_path):
        errors = input_errors(task)
        if errors:
            raise AuditError("invalid_input", str(errors))
        if task["audit_unit_id"] in tasks:
            raise AuditError("invalid_input", "audit_unit_id重复")
        tasks[task["audit_unit_id"]] = task

    outputs = []
    failures = []
    traces = []
    reviews = {}
    for target in rows(targets_path):
        if not isinstance(target, dict) or not all(
            target.get(key) not in (None, "")
            for key in ("annotation_id", "audit_unit_id", "material_id")
        ) or not _optional_int(target.get("page_index")):
            failures.append({
                "annotation_id": (
                    target.get("annotation_id")
                    if isinstance(target, dict)
                    else None
                ),
                "error_code": "invalid_annotation_target",
            })
            continue
        annotation_id = target["annotation_id"]
        task = tasks.get(target["audit_unit_id"])
        if not task:
            failures.append({
                "annotation_id": annotation_id,
                "error_code": "unknown_audit_unit_id",
            })
            continue
        if task["audit_unit_id"] not in reviews:
            reviews[task["audit_unit_id"]] = review_task(
                gateway, policy, task, input_path
            )
            traces.extend(reviews[task["audit_unit_id"]][1])
        output, _, error = annotate_target(
            gateway, policy, task, target, input_path,
            review=reviews[task["audit_unit_id"]],
        )
        if error:
            failures.append({
                "annotation_id": annotation_id,
                "error_code": error.code,
                "detail": error.detail,
            })
        else:
            outputs.append(output)

    write_jsonl(out_path, outputs)
    write_json(str(out_path) + ".errors.json", failures)
    write_jsonl(str(out_path) + ".trace.jsonl", traces)
    return {
        "command": "annotate",
        "records": len(outputs),
        "failures": len(failures),
        "out": str(out_path),
    }
