"""D10 验收演练支撑：冻结核对、16项产品任务与插件故障两人独立演练。

本模块只服务验收测试与交付快照生成，不进入产品运行时。
"""
import contextlib
import io
import json
import re
from pathlib import Path

from quackguard import cli
from quackguard.artifact_hash import path_digest, tree_digest
from quackguard.contracts import read_rows
from quackguard.inference import review_task
from quackguard.io_utils import load_json, rows, write_json
from quackguard.regression_checks import run_plugin_faults
from quackguard.regression_facts import GoldGateway

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "O2_AI商品审核官_资料包"
PUBLIC = PACK / "03_公开开发集"
ANNOTATION = PUBLIC / "标注样例"
CONFIG = PACK / "04_最小对接样例" / "config.json"
RULE_FILES = [
    PACK / "01_规则包" / "基础规则版本.jsonl",
    PACK / "01_规则包" / "进阶规则60条.jsonl",
]
TASKS = PUBLIC / "inputs" / "audit_inputs.jsonl"
REFERENCES = PUBLIC / "references" / "audit_gold.jsonl"
ANNOTATION_TASKS = ANNOTATION / "inputs" / "tasks.jsonl"
ANNOTATION_TARGETS = ANNOTATION / "inputs" / "targets.jsonl"
MANIFEST = ROOT / "quackguard" / "plugin_manifest.json"
CACHE_NAMES = {
    ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".venv", "venv",
}
TEMP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak", ".swp"}
SECRET_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}['\"]"
    ),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
)


def write_merged_rules(directory):
    rules = [rule for path in RULE_FILES for rule in read_rows(path)]
    path = Path(directory) / "rules.jsonl"
    path.write_text(
        "".join(json.dumps(rule, ensure_ascii=False) + "\n" for rule in rules),
        encoding="utf-8",
    )
    return path, rules


def verify_artifacts(root):
    """重算 manifest 指向的实际文件/目录哈希，并核对版本与重复条目。"""
    root = Path(root)
    manifest = load_json(root / "artifact_manifest.json")
    policy = load_json(root / "engine" / "policy.json")
    entries = manifest["entries"]
    actual = {}
    hash_mismatch = []
    duplicates = []
    seen = set()
    for entry in entries:
        key = entry["rule_id"] + "@" + entry["rule_version"]
        if key in seen:
            duplicates.append(key)
        seen.add(key)
        relative = entry["path"]
        if relative not in actual:
            actual[relative] = path_digest(root / relative)
        if actual[relative] != entry["sha256"]:
            hash_mismatch.append(key)
    policy_version = policy.get("artifact_version")
    version_mismatch = [
        entry["rule_id"] + "@" + entry["rule_version"]
        for entry in entries
        if entry["artifact_version"] != policy_version
    ]
    keys_match = set(seen) == set(policy.get("rules", {}))
    return {
        "artifact_version": policy_version,
        "entries": len(entries),
        "actual_paths": sorted(actual),
        "hash_mismatch": hash_mismatch,
        "version_mismatch": version_mismatch,
        "duplicates": duplicates,
        "keys_match": keys_match,
        "root_sha256": tree_digest(root),
        "ok": (
            bool(entries)
            and not hash_mismatch
            and not version_mismatch
            and not duplicates
            and keys_match
        ),
    }


def verify_outputs(outputs, traces, tasks, policy):
    """核对输出ID、artifact_version与trace回查关系。"""
    task_by_id = {task["audit_unit_id"]: task for task in tasks}
    output_ids = [row.get("audit_unit_id") for row in outputs]
    missing = sorted(set(task_by_id) - set(output_ids))
    extraneous = sorted(set(output_ids) - set(task_by_id))
    duplicated = sorted({
        unit_id for unit_id in output_ids if output_ids.count(unit_id) > 1
    })
    expected_version = policy["artifact_version"]
    version_mismatch = [
        row.get("audit_unit_id")
        for row in outputs
        if row.get("artifact_version") != expected_version
    ]
    trace_units = {}
    for trace in traces:
        trace_units.setdefault(trace.get("trace_id"), set()).add(
            trace.get("audit_unit_id")
        )
    trace_missing = []
    trace_mismatch = []
    trace_optional = []
    for output in outputs:
        trace_id = output.get("trace_id")
        unit_ids = trace_units.get(trace_id)
        if unit_ids is None:
            if output.get("reason_code") == "rule_not_applicable":
                trace_optional.append(output.get("audit_unit_id"))
            else:
                trace_missing.append(output.get("audit_unit_id"))
        elif output.get("audit_unit_id") not in unit_ids:
            trace_mismatch.append(output.get("audit_unit_id"))
    return {
        "records": len(outputs),
        "missing": missing,
        "extraneous": extraneous,
        "duplicated": duplicated,
        "version_mismatch": version_mismatch,
        "trace_missing": trace_missing,
        "trace_optional": trace_optional,
        "trace_mismatch": trace_mismatch,
        "trace_rows": len(traces),
        "ok": (
            len(outputs) == len(tasks)
            and not missing
            and not extraneous
            and not duplicated
            and not version_mismatch
            and not trace_missing
            and not trace_mismatch
        ),
    }


def public_gold_run(policy):
    """用公开金标可见事实产出确定性输出与轨迹，仅用于验收演练。"""
    tasks = rows(TASKS)
    gold = {row["audit_unit_id"]: row for row in rows(REFERENCES)}
    outputs = []
    traces = []
    for task in tasks:
        output, task_traces = review_task(
            GoldGateway(task, gold[task["audit_unit_id"]]),
            policy,
            task,
            TASKS,
        )
        outputs.append(output)
        traces.extend(task_traces)
    return outputs, traces


def evidence_report(outputs, tasks):
    tasks_by_id = {task["audit_unit_id"]: task for task in tasks}
    completed = 0
    items = 0
    violations = []
    for output in outputs:
        if output.get("execution_status") != "completed":
            continue
        completed += 1
        task = tasks_by_id.get(output.get("audit_unit_id")) or {}
        materials = {
            (material["material_id"], material.get("page_index", 1))
            for material in task.get("materials", [])
        }
        for item in output.get("evidence") or []:
            items += 1
            key = (item.get("material_id"), item.get("page_index"))
            if key not in materials:
                violations.append({
                    "audit_unit_id": output.get("audit_unit_id"),
                    "material_id": item.get("material_id"),
                    "page_index": item.get("page_index"),
                })
    return {
        "completed": completed,
        "evidence_items": items,
        "violations": violations,
        "ok": completed > 0 and items > 0 and not violations,
    }


def _cli(argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(item) for item in argv])
    return code, out.getvalue(), err.getvalue()


def _check(task_id, name, passed, evidence):
    return {
        "id": task_id,
        "name": name,
        "passed": bool(passed),
        "evidence": evidence,
    }



def five_commands_clean(work_dir):
    """在空目录依次运行 build/infer/annotate/iterate/evaluate 并核对产品页。"""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    rules_path, _rules = write_merged_rules(work)
    db_path = work / "clean.sqlite3"
    artifacts = work / "artifacts"
    outputs = work / "outputs.jsonl"
    annotations = work / "annotations.jsonl"
    feedback_path = work / "feedback.json"
    chosen = rows(TASKS)[0]

    steps = {
        "build": _cli([
            "--db", str(db_path), "build", "--rules", rules_path,
            "--config", CONFIG, "--out", artifacts,
        ]),
    }
    steps["infer"] = _cli([
        "--db", str(db_path), "infer", "--input", TASKS,
        "--artifacts", artifacts, "--out", outputs,
    ])
    steps["annotate"] = _cli([
        "--db", str(db_path), "annotate", "--input", ANNOTATION_TASKS,
        "--targets", ANNOTATION_TARGETS, "--artifacts", artifacts,
        "--out", annotations,
    ])
    write_json(feedback_path, {
        "schema_version": "1.3.1",
        "feedback_id": "clean-run",
        "base_artifact_sha256": tree_digest(artifacts),
        "items": [{
            "packet_id": "p1",
            "audit_unit_id": chosen["audit_unit_id"],
            "teacher_proposal": "确认按原文执行。",
            "teacher_source": chosen["rule_id"] + "@" + chosen["rule_version"],
        }],
    })
    steps["iterate"] = _cli([
        "--db", str(db_path), "iterate", "--base", artifacts,
        "--feedback", feedback_path, "--input", TASKS,
        "--out", work / "candidate",
    ])
    steps["evaluate"] = _cli([
        "--db", str(db_path), "evaluate", "--manifest", MANIFEST,
        "--inputs", TASKS, "--outputs", outputs,
        "--references", REFERENCES, "--out", work / "evaluation.json",
    ])

    infer_rows = rows(outputs)
    completed_rows = [
        row for row in infer_rows if row.get("execution_status") == "completed"
    ]
    annotation_rows = rows(annotations)
    annotation_failures = load_json(str(annotations) + ".errors.json")
    pages = {
        "status": artifacts / "status.html",
        "versions": artifacts / "versions.html",
        "review": work / "review.html",
        "iteration": work / "candidate" / "iteration.html",
        "plugins": work / "plugin_report.html",
    }
    return {
        "codes": {name: step[0] for name, step in steps.items()},
        "stderr": {name: step[2] for name, step in steps.items()},
        "records": len(infer_rows),
        "execution_errors": sum(
            row.get("execution_status") == "error" for row in infer_rows
        ),
        "completed": len(completed_rows),
        "completed_reason_codes": sorted({
            row.get("reason_code") for row in completed_rows
            if row.get("reason_code")
        }),
        "error_codes": sorted({
            row.get("error_code")
            for row in infer_rows
            if row.get("error_code")
        }),
        "annotation_records": len(annotation_rows),
        "annotation_failures": len(annotation_failures),
        "pages": {name: path.exists() for name, path in pages.items()},
        "page_paths": {name: str(path) for name, path in pages.items()},
        "ok": (
            all(step[0] == 0 for step in steps.values())
            and len(infer_rows) == 64
            and len(annotation_rows) == 0
            and len(annotation_failures) == 3
            and all(path.exists() for path in pages.values())
        ),
    }


def scan_delivery(root):
    """检查交付目录无密钥、缓存与临时文件；源码文件无密钥赋值。"""
    root = Path(root)
    issues = []
    delivery = root / "delivery"
    if delivery.exists():
        for path in sorted(delivery.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.name in CACHE_NAMES or path.suffix in TEMP_SUFFIXES:
                issues.append({"path": relative, "issue": "cache_or_temp"})
            elif path.is_file() and path.stat().st_size < 2_000_000:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for pattern in SECRET_PATTERNS:
                    if pattern.search(text):
                        issues.append({"path": relative, "issue": "secret_pattern"})
                        break
    if (root / ".env").exists():
        issues.append({"path": ".env", "issue": "secret_file"})
    source_files = [
        *sorted((root / "quackguard").glob("**/*.py")),
        *sorted((root / "tests").glob("**/*.py")),
        root / "run.py",
    ]
    for path in source_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                issues.append({
                    "path": path.relative_to(root).as_posix(),
                    "issue": "secret_pattern",
                })
                break
    return {"issues": issues, "ok": not issues}


def fault_review(reviewer, work_dir, inputs, outputs, references):
    result = run_plugin_faults(
        Path(work_dir) / "faults", 1.0, inputs, outputs, references
    )
    record = {
        "reviewer": reviewer,
        "plugin_faults": result,
        "all_passed": result["ok"],
    }
    write_json(Path(work_dir) / ("faults_%s.json" % reviewer), record)
    return record
