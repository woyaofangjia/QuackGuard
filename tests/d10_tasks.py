"""D10 两人独立演练：P-TASK-01—16 与插件故障场景。

只服务验收测试与交付快照生成，不进入产品运行时。
"""
import hashlib
import json
from pathlib import Path

from quackguard.artifact_hash import digest_obj, tree_digest
from quackguard.artifacts import build, load_policy
from quackguard.comparison import compare_results
from quackguard.io_utils import load_json, rows, write_json, write_jsonl
from quackguard.iteration import iterate
from quackguard.quality import metric
from quackguard.regression_checks import export_consistency, resume_check
from quackguard.storage import Store
from quackguard.versioning import activate, rollback
from d10_support import (
    CONFIG,
    MANIFEST,
    REFERENCES,
    TASKS,
    _check,
    _cli,
    evidence_report,
    public_gold_run,
    verify_artifacts,
    write_merged_rules,
)


def _payload_hash(*values):
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def run_sixteen(reviewer, work_dir):
    """在独立目录执行 P-TASK-01—16，返回 (记录, 公开输出, 参考)。"""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    rules_path, rules = write_merged_rules(work)
    db_path = work / "acceptance.sqlite3"
    store = Store(db_path)
    baseline = work / "baseline"
    built = build(rules_path, CONFIG, baseline, store=store)
    policy = load_policy(baseline)
    base_hash = tree_digest(baseline)
    tasks = rows(TASKS)
    references = rows(REFERENCES)
    checks = []

    entries = load_json(baseline / "artifact_manifest.json")["entries"]
    keys = {entry["rule_id"] + "@" + entry["rule_version"] for entry in entries}
    r_ids = sorted({
        entry["rule_id"] for entry in entries if entry["rule_id"].startswith("R")
    })
    b_versions = sorted(
        entry["rule_version"] for entry in entries if entry["rule_id"] == "B001"
    )
    checks.append(_check(
        "P-TASK-01", "全部规则导入与版本核对",
        len(entries) == 62
        and r_ids == ["R%03d" % index for index in range(1, 61)]
        and b_versions == ["3.0", "3.1"]
        and len(keys) == 62,
        {
            "entries": len(entries),
            "r_rules": len(r_ids),
            "b001_versions": b_versions,
            "artifact_version": built["artifact_version"],
        },
    ))

    duplicate_rules = work / "rules_duplicate.jsonl"
    duplicate_rules.write_text(
        rules_path.read_text(encoding="utf-8")
        + json.dumps(rules[0], ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    duplicate_root = work / "duplicate"
    duplicate = build(duplicate_rules, CONFIG, duplicate_root)
    duplicate_entries = load_json(
        duplicate_root / "artifact_manifest.json"
    )["entries"]
    duplicate_report = load_json(duplicate_root / "build_report.json")
    duplicate_keys = {
        entry["rule_id"] + "@" + entry["rule_version"]
        for entry in duplicate_entries
    }
    checks.append(_check(
        "P-TASK-02", "重复导入隔离且唯一规则不受影响",
        duplicate["rule_failures"] == 1
        and len(duplicate_entries) == 62
        and len(duplicate_keys) == 62
        and any(
            "重复规则" in failure["error"]
            for failure in duplicate_report["rule_failures"]
        ),
        {
            "rule_failures": duplicate_report["rule_failures"],
            "unique_entries": len(duplicate_keys),
        },
    ))

    bad_rules = work / "rules_bad.jsonl"
    bad_rules.write_text(
        rules_path.read_text(encoding="utf-8")
        + '{"rule_id": \n'
        + json.dumps(
            {"rule_id": "R901", "version": "1.0"}, ensure_ascii=False
        ) + "\n",
        encoding="utf-8",
    )
    bad_root = work / "bad"
    build(bad_rules, CONFIG, bad_root, store=store)
    bad_report = load_json(bad_root / "build_report.json")
    bad_entries = load_json(bad_root / "artifact_manifest.json")["entries"]
    bad_lines = sorted(failure["line"] for failure in bad_report["rule_failures"])
    checks.append(_check(
        "P-TASK-03", "异常导入定位到具体行",
        bad_lines == [63, 64]
        and len(bad_entries) == 62
        and bad_report["status"] == "ready_with_rule_failures",
        {"failed_lines": bad_lines, "rule_failures": bad_report["rule_failures"]},
    ))

    single_root = work / "single"
    single = build(rules_path, CONFIG, single_root, store=store, only=["R005"])
    single_build = store.find_build(single_root)
    single_versions = (
        store.list_versions(single_build["build_id"]) if single_build else []
    )
    checks.append(_check(
        "P-TASK-04", "单条规则真实构建可追溯",
        single["rule_versions"] == 1
        and single_build is not None
        and len(single_versions) == 1
        and single_versions[0]["rule_id"] == "R005"
        and single_versions[0]["artifact_version"] == single["artifact_version"],
        {
            "artifact_version": single["artifact_version"],
            "build_id": single_build["build_id"] if single_build else None,
            "versions": len(single_versions),
        },
    ))

    matrix = load_json(baseline / "engine" / "rule_test_matrix.json")
    status_counts = load_json(baseline / "build_report.json")[
        "implementation_status_counts"
    ]
    baseline_build = store.find_build(baseline)
    baseline_versions = store.list_versions(baseline_build["build_id"])
    checks.append(_check(
        "P-TASK-05", "批量构建状态可核对",
        len(matrix) == 62
        and sum(status_counts.values()) == 62
        and status_counts.get("implemented") == 62
        and len(baseline_versions) == 62,
        {
            "matrix": len(matrix),
            "status_counts": status_counts,
            "db_versions": len(baseline_versions),
        },
    ))

    bad_build = store.find_build(bad_root)
    persisted_failures = store.db.execute(
        "SELECT COUNT(*) AS total FROM build_failures WHERE build_id = ?",
        (bad_build["build_id"],),
    ).fetchone()["total"]
    checks.append(_check(
        "P-TASK-06", "失败记录可过滤且持久化",
        len(bad_report["rule_failures"]) == 2
        and all(
            "line" in failure and "error" in failure
            for failure in bad_report["rule_failures"]
        )
        and persisted_failures == 2,
        {
            "failures": len(bad_report["rule_failures"]),
            "persisted": persisted_failures,
        },
    ))

    gold_outputs, gold_traces = public_gold_run(policy)
    evidence = evidence_report(gold_outputs, tasks)
    checks.append(_check(
        "P-TASK-07", "证据定位到材料与源页",
        evidence["ok"],
        evidence,
    ))

    sources = {
        rule["rule_id"] + "@" + rule["version"]: rule for rule in rules
    }
    text_mismatch = [
        key for key, rule in sources.items()
        if policy["rules"].get(key, {}).get("rule_text") != rule["rule_text"]
    ]
    hash_mismatch = [
        key for key, rule in sources.items()
        if policy["source_hashes"].get(key) != digest_obj(rule)
    ]
    checks.append(_check(
        "P-TASK-08", "规则原文与来源哈希可追溯",
        not text_mismatch
        and not hash_mismatch
        and {"B001@3.0", "B001@3.1"} <= set(policy["rules"]),
        {
            "checked": len(sources),
            "text_mismatch": text_mismatch,
            "hash_mismatch": hash_mismatch,
        },
    ))

    old_path = work / "compare_old.jsonl"
    new_path = work / "compare_new.jsonl"
    write_jsonl(old_path, gold_outputs)
    reference_by_id = {
        reference["audit_unit_id"]: reference for reference in references
    }
    correct_ids = set(metric(references, gold_outputs, tasks)["correct_ids"])
    flipped = sorted(correct_ids)[0] if correct_ids else None
    new_outputs = [dict(output) for output in gold_outputs]
    if flipped is not None:
        for output in new_outputs:
            if output["audit_unit_id"] == flipped:
                output["label"] = (
                    "pass" if reference_by_id[flipped]["label"] != "pass"
                    else "reject"
                )
    write_jsonl(new_path, new_outputs)
    compare_out = work / "compare.json"
    compare_results(old_path, new_path, TASKS, REFERENCES, compare_out)
    comparison = load_json(compare_out)
    checks.append(_check(
        "P-TASK-09", "新旧结果对比按单元计数",
        flipped is not None
        and comparison["counts"] == {
            "fixed": 0, "new_error": 1, "unchanged": 63, "version_changed": 0,
        }
        and comparison["all"] == 64,
        {
            "flipped": flipped,
            "counts": comparison["counts"],
            "all": comparison["all"],
        },
    ))

    failed_task = store.start_task("build")
    failed_run = store.start_run(
        failed_task,
        "build",
        rules_path,
        work / "retry-target",
        arguments={
            "command": "build",
            "rules": str(rules_path),
            "config": str(CONFIG),
            "out": str(work / "retry-target"),
            "only": "",
            "resume": False,
        },
    )
    store.finish(failed_task, failed_run, "failed", "io_error")
    first_retry = _cli([
        "--db", str(db_path), "retry", "--task-id", failed_task,
    ])
    second_retry = _cli([
        "--db", str(db_path), "retry", "--task-id", failed_task,
    ])
    successor = store.retry_successor(failed_task)
    successor_count = store.db.execute(
        "SELECT COUNT(*) AS total FROM tasks WHERE retry_of = ?",
        (failed_task,),
    ).fetchone()["total"]
    checks.append(_check(
        "P-TASK-10", "失败重试幂等且后继唯一",
        first_retry[0] == 0
        and second_retry[0] == 0
        and json.loads(second_retry[1]).get("status") == "existing"
        and successor is not None
        and successor_count == 1,
        {
            "first_status": json.loads(first_retry[1]).get("status"),
            "second_status": json.loads(second_retry[1]).get("status"),
            "successors": successor_count,
        },
    ))

    resume = resume_check(baseline, rules_path, CONFIG, work / "resume.sqlite3")
    checks.append(_check(
        "P-TASK-11", "中断恢复跳过已完成规则",
        resume["ok"] and resume["skipped_rule_versions"] == 62,
        resume,
    ))

    predictions_path = work / "predictions.jsonl"
    write_jsonl(predictions_path, gold_outputs)
    export = export_consistency(
        TASKS, predictions_path, REFERENCES, work / "export"
    )
    checks.append(_check(
        "P-TASK-12", "导出与评估指标一致",
        export["ok"] and export["rows"] == 64,
        export,
    ))

    chosen = tasks[0]
    feedback_path = work / "feedback.json"
    write_json(feedback_path, {
        "schema_version": "1.3.1",
        "feedback_id": reviewer + "-freeze",
        "base_artifact_sha256": base_hash,
        "items": [{
            "packet_id": "p1",
            "audit_unit_id": chosen["audit_unit_id"],
            "teacher_proposal": "确认按原文执行。",
            "teacher_source": chosen["rule_id"] + "@" + chosen["rule_version"],
        }],
    })
    candidate = work / "candidate"
    iteration = iterate(baseline, feedback_path, TASKS, candidate, store=store)
    candidate_check = verify_artifacts(candidate)
    gate_path = work / "gate.json"
    write_json(gate_path, {
        "new_joint_errors": 0,
        "new_errors": 0,
        "invalid": 0,
        "missing": 0,
        "contrast_complete": True,
        "feedback_accuracy_delta": 0,
        "protection_accuracy_delta": 0,
    })
    activation = activate(
        baseline, candidate, store, gate_path=gate_path,
        out_path=work / "activate.json",
    )
    active = store.get_active()
    infer_code, _, _ = _cli([
        "--db", str(db_path), "infer", "--input", str(TASKS),
        "--artifacts", "active", "--out", str(work / "active-outputs.jsonl"),
    ])
    active_versions = {
        row.get("artifact_version")
        for row in rows(work / "active-outputs.jsonl")
    }
    checks.append(_check(
        "P-TASK-13", "候选激活后推理绑定候选版本",
        iteration["action"] == "changed"
        and candidate_check["ok"]
        and activation["status"] == "activated"
        and active["artifact_version"] == iteration["candidate_artifact_version"]
        and infer_code == 0
        and active_versions == {iteration["candidate_artifact_version"]},
        {
            "action": iteration["action"],
            "baseline_version": built["artifact_version"],
            "candidate_version": iteration["candidate_artifact_version"],
            "active_version": active["artifact_version"],
            "candidate_manifest": candidate_check,
        },
    ))

    rollback_result = rollback(
        baseline, store, expect_hash=base_hash, out_path=work / "rollback.json"
    )
    rolled = store.get_active()
    rollback_code, _, _ = _cli([
        "--db", str(db_path), "infer", "--input", str(TASKS),
        "--artifacts", "active", "--out", str(work / "rolled-outputs.jsonl"),
    ])
    rolled_versions = {
        row.get("artifact_version")
        for row in rows(work / "rolled-outputs.jsonl")
    }
    checks.append(_check(
        "P-TASK-14", "回滚恢复基线版本与根哈希",
        rollback_result["status"] == "rolled_back"
        and rolled["artifact_version"] == built["artifact_version"]
        and rolled["root_sha256"] == base_hash
        and rollback_code == 0
        and rolled_versions == {built["artifact_version"]},
        {
            "status": rollback_result["status"],
            "baseline_version": built["artifact_version"],
            "active_version": rolled["artifact_version"],
            "root_sha256": rolled["root_sha256"],
            "expected_sha256": base_hash,
        },
    ))

    eval_paths = (
        str(TASKS), str(predictions_path), str(REFERENCES),
    )
    before_hash = _payload_hash(*[
        hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for path in eval_paths
    ])
    evaluate_first = _cli([
        "--db", str(db_path), "evaluate", "--manifest", str(MANIFEST),
        "--inputs", TASKS, "--outputs", predictions_path,
        "--references", REFERENCES, "--out", work / "evaluate.json",
    ])
    disable = _cli([
        "--db", str(db_path), "plugin-state",
        "--plugin-id", "quackguard.evidence_content",
        "--plugin-version", "0.2.0", "--state", "disabled",
        "--out", work / "plugin-state.json",
    ])
    evaluate_second = _cli([
        "--db", str(db_path), "evaluate", "--manifest", str(MANIFEST),
        "--inputs", TASKS, "--outputs", predictions_path,
        "--references", REFERENCES, "--out", work / "evaluate2.json",
    ])
    second_report = load_json(work / "evaluate2.json")
    plugin_reports = {
        report["plugin_id"]: report for report in second_report["plugins"]
    }
    after_hash = _payload_hash(*[
        hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for path in eval_paths
    ])
    checks.append(_check(
        "P-TASK-15", "禁用插件不调用且健康插件不受影响",
        evaluate_first[0] == 0
        and disable[0] == 0
        and evaluate_second[0] == 0
        and plugin_reports["quackguard.evidence_content"]["status"] == "disabled"
        and plugin_reports["quackguard.evidence_content"]["invocations"] == 0
        and plugin_reports["quackguard.conclusion"]["status"] == "ok"
        and before_hash == after_hash,
        {
            "evidence_status": plugin_reports["quackguard.evidence_content"]["status"],
            "evidence_invocations": plugin_reports["quackguard.evidence_content"]["invocations"],
            "conclusion_status": plugin_reports["quackguard.conclusion"]["status"],
            "input_unchanged": before_hash == after_hash,
        },
    ))

    candidate_row = store.get_candidate(candidate)
    base_build_id = baseline_build["build_id"]
    store.close()
    reopened = Store(db_path)
    try:
        persisted_builds = {row["build_id"] for row in reopened.list_builds()}
        persisted_versions = reopened.list_versions(base_build_id)
        persisted_active = reopened.get_active()
        persisted_candidate = reopened.get_candidate(candidate)
        persisted_plugin = reopened.get_plugin(
            "quackguard.evidence_content", "0.2.0"
        )
    finally:
        reopened.close()
    checks.append(_check(
        "P-TASK-16", "重启后构建/版本/候选/插件持久化",
        base_build_id in persisted_builds
        and len(persisted_versions) == 62
        and persisted_active is not None
        and persisted_candidate is not None
        and candidate_row is not None
        and persisted_candidate["candidate_id"] == candidate_row["candidate_id"]
        and persisted_plugin is not None
        and not bool(persisted_plugin["enabled"]),
        {
            "build_persisted": base_build_id in persisted_builds,
            "versions": len(persisted_versions),
            "active": None if persisted_active is None else persisted_active["artifact_version"],
            "candidate_persisted": persisted_candidate is not None,
            "plugin_disabled": (
                None if persisted_plugin is None
                else not bool(persisted_plugin["enabled"])
            ),
        },
    ))

    record = {
        "reviewer": reviewer,
        "artifact_version": built["artifact_version"],
        "baseline_root_sha256": base_hash,
        "baseline_manifest": verify_artifacts(baseline),
        "tasks": checks,
        "all_passed": all(item["passed"] for item in checks),
    }
    write_json(work / ("reviewer_%s.json" % reviewer), record)
    return record, gold_outputs, references
