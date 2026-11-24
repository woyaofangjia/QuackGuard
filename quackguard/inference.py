import json
import re
import time
import uuid

from .artifacts import load_policy
from .contracts import input_errors, prediction_errors
from .errors import AuditError
from .facts import Fact
from .io_utils import rows, write_jsonl
from .model_gateway import ModelGateway

OUTPUT_FORMAT = (
    "返回固定行格式，不要JSON，不要代码围栏。"
    "第1行 LABEL: pass/reject/ambiguous 中的一个值；"
    "第2行 APPLICABILITY: applicable/not_applicable/unknown 中的一个值；"
    "第3行 REASON: 简洁具体依据，不换行；第4行 EVIDENCE:；"
    "后续每条证据单独一行，格式为 material_id<TAB>整数page_index<TAB>fact，"
    "三个字段用制表符分隔。没有图像证据时写 NONE。最后一行 END。"
    "引用只使用当前materials的ID及源页；元数据依据写REASON。"
    "明确不适用用pass/not_applicable；仅适用条件未知用ambiguous/unknown，"
    "已经适用但证据不足仍applicable。"
)


def parse_prediction(raw):
    starts = list(re.finditer(r"(?m)^\s*LABEL\s*[:：]", raw))
    if len(starts) != 1:
        raise ValueError("Result block must be unique")

    values = {}
    evidence = []
    inside_evidence = False
    for line in raw[starts[0].start():].splitlines():
        line = line.strip()
        if line == "END":
            break
        match = re.match(
            r"^(LABEL|APPLICABILITY|REASON|EVIDENCE)\s*[:：]\s*(.*)$",
            line,
        )
        if match:
            key, value = match.groups()
            if key in values:
                raise ValueError("Duplicate output field")
            values[key] = value
            inside_evidence = inside_evidence or key == "EVIDENCE"
            continue
        if inside_evidence and line and line != "NONE":
            material_id, page, fact = re.split(
                r"\t+|\s+\|\s+", line, maxsplit=2
            )
            evidence_item = Fact(
                "evidence",
                fact,
                material_id=material_id,
                page_index=int(page),
            )
            evidence.append(evidence_item.to_evidence())

    expected_fields = {"LABEL", "APPLICABILITY", "REASON", "EVIDENCE"}
    if set(values) != expected_fields:
        raise ValueError("Missing result fields")
    return {
        "label": values["LABEL"],
        "applicability": values["APPLICABILITY"],
        "reason": values["REASON"],
        "evidence": evidence,
    }


def audit_prompt(policy, task):
    key = task["rule_id"] + "@" + task["rule_version"]
    rule = policy["rules"].get(key)
    if not rule:
        raise AuditError("rule_artifact_not_built", key)
    sections = [
        "依据公开规则和全部材料执行审核，不猜测不可读事实。"
        "先适用性，再特别冲突或例外、已证实不满足、影响结论的未知、全部满足。",
        OUTPUT_FORMAT,
        policy["general"],
        policy["supplement"],
        rule["rule_text"],
        json.dumps(task, ensure_ascii=False),
    ]
    return "\n".join(sections)


def review_task(gateway, policy, task, input_path):
    started = time.monotonic()
    output = {
        "audit_unit_id": task["audit_unit_id"],
        "rule_id": task["rule_id"],
        "rule_version": task["rule_version"],
        "artifact_version": policy["artifact_version"],
        "trace_id": uuid.uuid4().hex,
        "cost_units": None,
    }
    traces = []
    try:
        prompt = audit_prompt(policy, task)
        for attempt in (1, 2):
            messages = gateway.messages(prompt, task, input_path)
            raw = gateway.complete(messages)
            traces.append({
                "trace_id": output["trace_id"],
                "audit_unit_id": task["audit_unit_id"],
                "attempt": attempt,
                "response": raw,
            })
            try:
                result = parse_prediction(raw)
                if result["applicability"] == "not_applicable":
                    reason_code = "rule_not_applicable"
                else:
                    reason_code = "model_review"
                output.update(
                    result,
                    execution_status="completed",
                    reason_code=reason_code,
                    latency_ms=(time.monotonic() - started) * 1000,
                )
                errors = prediction_errors(
                    output, task, policy["artifact_version"]
                )
                if errors:
                    raise ValueError(str(errors))
                break
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == 2:
                    raise AuditError("invalid_model_output", str(exc)) from exc
                prompt += (
                    "\n【公开协议校验反馈，无标准答案】\n"
                    + str(exc)
                    + "\n请按固定行格式重新完整输出。上次输出：\n"
                    + raw
                )
    except (AuditError, ValueError, KeyError, TypeError, OSError) as exc:
        if isinstance(exc, AuditError):
            error = exc
        else:
            error = AuditError("execution_error", str(exc) or type(exc).__name__)
        output.update(
            execution_status="error",
            label=None,
            applicability=None,
            reason_code="execution_error",
            reason=error.detail,
            error_code=error.code,
            evidence=[],
        )
        traces.append({
            "trace_id": output["trace_id"],
            "audit_unit_id": task["audit_unit_id"],
            "error_code": error.code,
        })

    output["latency_ms"] = (time.monotonic() - started) * 1000
    return output, traces


def infer(input_path, artifacts_path, out_path, base_url, api_key):
    policy = load_policy(artifacts_path)
    gateway = ModelGateway(
        model=policy.get("model"),
        base_url=base_url,
        api_key=api_key,
    )
    tasks = rows(input_path)
    for task in tasks:
        errors = input_errors(task)
        if errors:
            raise AuditError("invalid_input", str(errors))

    outputs = []
    traces = []
    for task in tasks:
        output, task_traces = review_task(gateway, policy, task, input_path)
        outputs.append(output)
        traces.extend(task_traces)
    write_jsonl(out_path, outputs)
    write_jsonl(str(out_path) + ".trace.jsonl", traces)
    error_count = sum(row["execution_status"] == "error" for row in outputs)
    return {
        "command": "infer",
        "records": len(outputs),
        "execution_errors": error_count,
        "out": str(out_path),
    }
