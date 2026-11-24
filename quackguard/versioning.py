"""候选激活与回滚：门槛校验通过后才移动活动版本指针。"""
from pathlib import Path

from .artifact_hash import digest_obj, inventory, tree_digest
from .artifacts import load_policy
from .errors import AuditError
from .io_utils import load_json, write_json
from .iteration import METADATA_FILES
from .report import write_active_page


def _gate_failures(gate):
    if not isinstance(gate, dict):
        return ["门槛证据必须是JSON对象"]
    failures = []
    for field in ("new_joint_errors", "new_errors", "invalid", "missing"):
        value = gate.get(field)
        if type(value) not in (int, float):
            failures.append(f"缺少或非法的门槛字段:{field}")
        elif value != 0:
            failures.append(f"{field}={value}，必须为0")
    if gate.get("contrast_complete") is not True:
        failures.append("对照不完整")
    for field in ("feedback_accuracy_delta", "protection_accuracy_delta"):
        value = gate.get(field)
        if type(value) not in (int, float):
            failures.append(f"缺少或非法的门槛字段:{field}")
        elif value < 0:
            failures.append(f"{field}={value}，准确率不得下降")
    return failures


def _ensure_candidate_build(store, baseline_root, candidate_root, policy):
    existing = store.find_build(candidate_root)
    if existing is not None:
        return existing["build_id"]
    base_build = store.find_build(baseline_root)
    if base_build is not None:
        rules_sha256 = base_build["rules_sha256"]
        config_sha256 = base_build["config_sha256"]
    else:
        rules_sha256 = digest_obj(policy["rules"])
        config_sha256 = digest_obj({
            "general": policy["general"],
            "supplement": policy["supplement"],
            "model": policy.get("model"),
        })
    build_id = store.add_build(
        policy["artifact_version"],
        candidate_root,
        rules_sha256,
        config_sha256,
        status="candidate",
    )
    manifest = load_json(candidate_root / "artifact_manifest.json")
    store.add_versions(build_id, manifest["entries"])
    return build_id


def _write_active_report(out_path, report):
    if out_path:
        out = Path(out_path)
        write_json(out, report)
        write_active_page(str(out.with_suffix(".html")), report)


def activate(baseline_path, candidate_path, store, gate_path=None, out_path=None):
    if store is None:
        raise AuditError("activation_requires_db")
    baseline_root = Path(baseline_path)
    candidate_root = Path(candidate_path)
    base_hash = tree_digest(baseline_root)
    report = load_json(candidate_root / "iteration_report.json")
    if not isinstance(report, dict) or report.get("action") not in (
        "changed", "no_change"
    ):
        raise AuditError("invalid_iteration_report")
    if report.get("base_artifact_sha256") != base_hash:
        raise AuditError("candidate_baseline_mismatch")
    if tree_digest(baseline_root) != base_hash:
        raise AuditError("baseline_modified")
    candidate_artifact_hash = digest_obj(
        inventory(candidate_root, exclude=METADATA_FILES)
    )
    if report.get("candidate_artifact_sha256") != candidate_artifact_hash:
        raise AuditError("candidate_hash_mismatch")
    policy = load_policy(candidate_root)
    if policy.get("artifact_version") != report.get("candidate_artifact_version"):
        raise AuditError("candidate_version_mismatch")
    accepted = set(report.get("accepted_packet_ids", []))
    rejected = set(report.get("rejected_packet_ids", [])) | set(
        report.get("deferred_packet_ids", [])
    )
    iteration_ids = set(policy.get("iteration", {}).get("accepted_packet_ids", []))
    if iteration_ids != accepted:
        raise AuditError("policy_decision_mismatch")
    notes_path = candidate_root / "engine" / "iteration_notes.json"
    note_ids = set()
    if notes_path.exists():
        notes = load_json(notes_path)
        note_ids = {note.get("packet_id") for note in notes.get("notes", [])}
        if not note_ids <= accepted:
            raise AuditError("policy_decision_mismatch")
    leaked = sorted((iteration_ids | note_ids) & rejected)
    if leaked:
        raise AuditError("rejected_proposal_in_policy", ",".join(leaked))

    gate = load_json(gate_path) if gate_path else None
    failures = _gate_failures(gate) if gate is not None else []
    if failures:
        raise AuditError("activation_gate_failed", ";".join(failures))

    previous = store.get_active()
    previous_info = dict(previous) if previous is not None else {}
    candidate_root_hash = tree_digest(candidate_root)
    candidate_id = store.add_candidate(
        base_hash,
        candidate_root,
        policy["artifact_version"],
        candidate_root_hash,
        report.get("action"),
        report.get("feedback_id"),
    )
    if report.get("action") == "no_change":
        status = "kept_baseline"
        baseline_build = store.find_build(baseline_root)
        active_build_id = baseline_build["build_id"] if baseline_build else None
        active = store.set_active(
            baseline_root,
            load_policy(baseline_root)["artifact_version"],
            base_hash,
            "baseline_no_change",
            candidate_id,
            base_hash,
        )
        reason = "候选为有依据的no_change，活动版本继续指向baseline。"
    else:
        status = "activated"
        active_build_id = _ensure_candidate_build(
            store, baseline_root, candidate_root, policy
        )
        active = store.set_active(
            candidate_root,
            policy["artifact_version"],
            candidate_root_hash,
            "candidate",
            candidate_id,
            base_hash,
        )
        reason = "门槛通过，活动版本切换到候选产物。"
    store.mark_build_active(active_build_id)
    result = {
        "command": "activate",
        "status": status,
        "artifact_version": active["artifact_version"],
        "root_path": active["root_path"],
        "root_sha256": active["root_sha256"],
        "source": active["source"],
        "gate": gate,
        "gate_failures": [],
        "previous": previous_info,
        "reason": reason,
    }
    _write_active_report(out_path, result)
    return {
        "command": "activate",
        "status": status,
        "artifact_version": active["artifact_version"],
        "root_sha256": active["root_sha256"],
        "root_path": active["root_path"],
    }


def rollback(to_path, store, expect_hash=None, out_path=None, reason=""):
    if store is None:
        raise AuditError("rollback_requires_db")
    target = Path(to_path)
    policy = load_policy(target)
    root_hash = tree_digest(target)
    if expect_hash is not None and expect_hash != root_hash:
        raise AuditError("rollback_hash_mismatch")
    previous = store.get_active()
    previous_info = dict(previous) if previous is not None else {}
    active = store.set_active(
        target,
        policy["artifact_version"],
        root_hash,
        "rollback",
        None,
        previous_info.get("root_sha256"),
    )
    target_build = store.find_build(target)
    store.mark_build_active(target_build["build_id"] if target_build else None)
    result = {
        "command": "rollback",
        "status": "rolled_back",
        "artifact_version": active["artifact_version"],
        "root_path": active["root_path"],
        "root_sha256": active["root_sha256"],
        "source": active["source"],
        "gate": None,
        "gate_failures": [],
        "previous": previous_info,
        "reason": reason or "回滚到指定基线版本，推理重新绑定该产物。",
    }
    _write_active_report(out_path, result)
    return {
        "command": "rollback",
        "status": "rolled_back",
        "artifact_version": active["artifact_version"],
        "root_sha256": active["root_sha256"],
        "root_path": active["root_path"],
    }
