import json
import re

from .artifacts import load_policy
from .contracts import annotation_errors, input_errors, strict_loads
from .errors import AuditError
from .io_utils import rows, write_json, write_jsonl
from .model_gateway import ModelGateway

ANNOTATION_FORMAT = (
    "只输出四行，不要代码围栏：第1行VALUE_JSON: JSON字符串或null；"
    "第2行STATUS: value/unreadable/invalid_value；"
    "第3行LABEL: pass/reject/ambiguous；"
    "第4行BBOX: [x1,y1,x2,y2]。label是整条指定规则的审核结论。"
    "value提取target要求的完整行，包括字段名；不可读必须null和unreadable。"
    "BBOX为0至1归一化坐标。"
)


def parse_annotation(raw, target):
    values = {}
    pattern = r"^\s*(VALUE_JSON|STATUS|LABEL|BBOX)\s*[:：]\s*(.*?)\s*$"
    for line in raw.splitlines():
        match = re.match(pattern, line)
        if match:
            key, value = match.groups()
            if key in values:
                raise ValueError("Duplicate annotation field")
            values[key] = value

    if set(values) != {"VALUE_JSON", "STATUS", "LABEL", "BBOX"}:
        raise ValueError("Missing annotation fields")
    output = {
        key: target[key]
        for key in ("annotation_id", "audit_unit_id", "material_id", "page_index")
    }
    output.update(
        value=strict_loads(values["VALUE_JSON"]),
        status=values["STATUS"],
        label=values["LABEL"],
        bbox=strict_loads(values["BBOX"]),
    )
    return output


def annotation_prompt(policy, task, target):
    key = task["rule_id"] + "@" + task["rule_version"]
    rule = policy["rules"].get(key)
    if not rule:
        raise AuditError("rule_artifact_not_built", key)
    payload = json.dumps(
        {"task": task, "annotation_target": target}, ensure_ascii=False
    )
    return "\n".join([
        policy["general"],
        policy["supplement"],
        rule["rule_text"],
        payload,
        ANNOTATION_FORMAT,
    ])


def annotate_target(gateway, policy, task, target, input_path):
    traces = []
    try:
        prompt = annotation_prompt(policy, task, target)
        for attempt in (1, 2):
            messages = gateway.messages(prompt, task, input_path)
            raw = gateway.complete(messages)
            traces.append({
                "annotation_id": target["annotation_id"],
                "attempt": attempt,
                "response": raw,
            })
            try:
                output = parse_annotation(raw, target)
                errors = annotation_errors(output, target, task)
                if errors:
                    raise ValueError(str(errors))
                return output, traces, None
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == 2:
                    raise AuditError("invalid_model_output", str(exc)) from exc
                prompt += (
                    "\n【公开格式校验反馈，无标准答案】\n"
                    + str(exc)
                    + "\n请重新输出指定四行。上次输出：\n"
                    + raw
                )
    except (AuditError, ValueError, KeyError, TypeError, OSError) as exc:
        if isinstance(exc, AuditError):
            error = exc
        else:
            error = AuditError("execution_error", str(exc) or type(exc).__name__)
        return None, traces, error


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
    for target in rows(targets_path):
        if not isinstance(target, dict):
            failures.append({
                "annotation_id": None,
                "error_code": "invalid_annotation_target",
            })
            continue
        annotation_id = target.get("annotation_id")
        task = tasks.get(target.get("audit_unit_id"))
        if not task:
            failures.append({
                "annotation_id": annotation_id,
                "error_code": "unknown_audit_unit_id",
            })
            continue
        output, target_traces, error = annotate_target(
            gateway, policy, task, target, input_path
        )
        traces.extend(target_traces)
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
