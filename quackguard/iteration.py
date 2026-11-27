"""安全反馈迭代：冻结baseline、逐条决定并生成独立candidate。"""
import copy
import csv
import re
import shutil
import unicodedata
from pathlib import Path

from . import PROTOCOL_VERSION
from .artifact_hash import digest_obj, inventory, path_digest, tree_digest
from .artifacts import LEDGER_NAME, load_policy
from .contracts import input_errors
from .errors import AuditError
from .io_utils import load_json, rows, write_json
from .report import write_iteration_page, write_status_page

PACKET_FIELDS = {
    "schema_version", "feedback_id", "base_artifact_sha256", "items"
}
ITEM_FIELDS = {"packet_id", "audit_unit_id", "teacher_proposal", "teacher_source"}
METADATA_FILES = ("iteration_report.json", "iteration.html")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RULE_KEY = re.compile(r"([RB]\d{3})@(\d+(?:\.\d+)*)")
RULE_ID = re.compile(r"([RB]\d{3})")
NUMBER = re.compile(r"\d+(?:\.\d+)?")

STRONG_CHANGE_MARKERS = (
    "改为", "改成", "调整为", "调整到", "应设", "放宽", "收紧", "统一改",
    "增加", "删除", "移除",
)
NORMATIVE_MARKERS = ("应为", "必须", "禁止", "允许", "不得")
VERIFY_MARKERS = ("核查", "核对", "复核", "确认一下", "请检查", "请确认")
CONFIRM_MARKERS = ("确认按原文", "按原文执行", "采纳原文", "确认原文")
KEEP_MARKERS = ("保持现状", "维持现状", "无需修改", "不需修改", "保持不变")

LABEL_DIRECTIVE = re.compile(
    r"(?:应判为|应判|判为|判定为|结论应为|结论为|改为|改成|应为)"
    r"\s*[：:]?\s*(pass|reject|ambiguous|通过|合格|拒绝|不合格|不明确|无法判断|存疑)",
    re.IGNORECASE,
)
APPLICABILITY_DIRECTIVE = re.compile(
    r"(?:适用性|applicability)\s*(?:应为|为|是|改成|改为)?\s*[：:]?\s*"
    r"(not_applicable|applicable|unknown|不适用|适用|未知)",
    re.IGNORECASE,
)
LABELS = {
    "pass": "pass", "通过": "pass", "合格": "pass",
    "reject": "reject", "拒绝": "reject", "不合格": "reject",
    "ambiguous": "ambiguous", "不明确": "ambiguous",
    "无法判断": "ambiguous", "存疑": "ambiguous",
}
APPLICABILITIES = {
    "applicable": "applicable", "适用": "applicable",
    "not_applicable": "not_applicable", "不适用": "not_applicable",
    "unknown": "unknown", "未知": "unknown",
}


def _directive_patch(proposal):
    """解析教师明确给出的结论更正；仅接受可枚举的标签/适用性。"""
    label = LABEL_DIRECTIVE.search(proposal)
    applicability = APPLICABILITY_DIRECTIVE.search(proposal)
    if label is None and applicability is None:
        return None
    patch = {
        "reason": "采纳教师建议：" + proposal,
        "reason_code": "teacher_feedback",
    }
    if label is not None:
        patch["label"] = LABELS[label.group(1).lower()]
    if applicability is not None:
        patch["applicability"] = APPLICABILITIES[applicability.group(1).lower()]
        if patch["applicability"] == "unknown":
            patch.setdefault("label", "ambiguous")
        elif patch["applicability"] == "not_applicable":
            patch.setdefault("label", "pass")
            patch["reason_code"] = "rule_not_applicable"
    return patch


def _checked_packet(packet):
    if not isinstance(packet, dict):
        raise AuditError("invalid_feedback", "反馈包必须是对象")
    if set(packet) != PACKET_FIELDS:
        raise AuditError("invalid_feedback", "反馈包字段不正确")
    if packet.get("schema_version") != PROTOCOL_VERSION:
        raise AuditError("invalid_feedback", "schema_version不匹配")
    if not isinstance(packet["feedback_id"], str) or not packet["feedback_id"].strip():
        raise AuditError("invalid_feedback", "feedback_id缺失")
    if not isinstance(packet.get("base_artifact_sha256"), str) or not HEX64.match(
        packet["base_artifact_sha256"]
    ):
        raise AuditError("invalid_feedback", "base_artifact_sha256非法")
    items = packet.get("items")
    if not isinstance(items, list):
        raise AuditError("invalid_feedback", "items必须为数组")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
            raise AuditError("invalid_feedback", "反馈项字段不正确")
        for field in ITEM_FIELDS:
            if not isinstance(item[field], str) or not item[field].strip():
                raise AuditError("invalid_feedback", f"{field}缺失")
        if item["packet_id"] in seen:
            raise AuditError("duplicate_packet_id", item["packet_id"])
        seen.add(item["packet_id"])
    return packet


def _task_index(input_path):
    index = {}
    for task in rows(input_path):
        if isinstance(task, dict) and isinstance(task.get("audit_unit_id"), str):
            index[task["audit_unit_id"]] = task
    return index


def _cited_rule(text):
    match = RULE_KEY.search(text)
    if match:
        return match.group(1), match.group(2)
    match = RULE_ID.search(text)
    if match:
        return match.group(1), None
    return None


def _source_state(source, task):
    """返回 True=来源匹配，False=来源冲突，None=来源未引用具体规则。"""
    cited = _cited_rule(source)
    if cited is None:
        return None
    rule_id, rule_version = cited
    if rule_id != task["rule_id"]:
        return False
    if rule_version is not None and rule_version != task["rule_version"]:
        return False
    return True


def _has(markers, text):
    return any(marker in text for marker in markers)


def _proposal_numbers(text):
    """规则ID中的数字不是建议口径，先剔除再比较。"""
    cleaned = RULE_KEY.sub(" ", text)
    cleaned = RULE_ID.sub(" ", cleaned)
    return set(NUMBER.findall(cleaned))


def _citation(rule):
    return {
        "rule_id": rule["rule_id"],
        "rule_version": rule["version"],
        "quote": rule["rule_text"],
    }


def _decide(item, task, rule):
    proposal = unicodedata.normalize("NFKC", item["teacher_proposal"]).strip()
    source = unicodedata.normalize("NFKC", item["teacher_source"]).strip()
    if task is None:
        return "defer", "反馈单元不在REPAIR_JSONL中，无法解析规则并引用原文。", [], None
    errors = input_errors(task)
    if errors:
        return "defer", "反馈单元输入不合法：" + ",".join(errors), [], None
    if rule is None:
        return "defer", "反馈单元指定的规则不在baseline产物中。", [], None
    citation = _citation(rule)
    source_state = _source_state(source, task)
    if source_state is False:
        return (
            "reject",
            "建议引用的规则与审核单元规则 {}@{} 不一致，不能进入活动策略。".format(
                task["rule_id"], task["rule_version"]
            ),
            [citation],
            None,
        )
    proposal_numbers = _proposal_numbers(proposal)
    rule_numbers = set(NUMBER.findall(rule["rule_text"]))
    unsupported = proposal_numbers - rule_numbers
    if unsupported:
        return (
            "reject",
            "建议中的数值与规则原文不一致：{}；拒绝以保持baseline。".format(
                ",".join(sorted(unsupported))
            ),
            [citation],
            None,
        )
    patch = _directive_patch(proposal)
    if patch is not None and source_state is not False:
        return (
            "accept",
            "教师明确给出结论更正，采纳为可回滚的单元级补丁；其他单元不受影响。",
            [citation],
            patch,
        )
    if _has(CONFIRM_MARKERS, proposal) and source_state is True:
        return (
            "accept",
            "建议引用同一规则原文并确认按原文执行，采纳为澄清记录，不改变判定逻辑。",
            [citation],
            None,
        )
    if _has(VERIFY_MARKERS, proposal):
        return (
            "defer",
            "建议仅要求核查，未给出可执行改动或判定依据，待教师澄清。",
            [citation],
            None,
        )
    if _has(STRONG_CHANGE_MARKERS, proposal) or _has(NORMATIVE_MARKERS, proposal):
        return (
            "reject",
            "建议要求改动规则口径，但规则原文未支持该改动；拒绝以保持baseline。",
            [citation],
            None,
        )
    if _has(KEEP_MARKERS, proposal):
        return "reject", "建议维持现状，baseline已按规则原文执行，无需改动。", [citation], None
    return (
        "defer",
        "建议无法由规则原文和可核验材料唯一确认，保留baseline等待澄清。",
        [citation],
        None,
    )


def _changed_files(base_root, candidate_root):
    baseline = inventory(base_root)
    candidate = inventory(candidate_root, exclude=METADATA_FILES)
    return sorted(
        relative
        for relative in set(baseline) | set(candidate)
        if baseline.get(relative) != candidate.get(relative)
    )


def _apply_accepts(candidate_root, policy, base_hash, feedback_id, accepted):
    notes = [
        {
            "packet_id": item["packet_id"],
            "audit_unit_id": item["audit_unit_id"],
            "rule_id": item["rule_id"],
            "rule_version": item["rule_version"],
            "proposal": item["teacher_proposal"],
            "rule_quote": item["rule_citations"][0]["quote"],
            "change": item.get("change", "clarification_only"),
        }
        for item in accepted
    ]
    patches = [
        {
            "audit_unit_id": item["audit_unit_id"],
            "packet_id": item["packet_id"],
            "rule_id": item["rule_id"],
            "rule_version": item["rule_version"],
            "teacher_proposal": item["teacher_proposal"],
            "rule_quote": item["rule_citations"][0]["quote"],
            "label": item["patch"].get("label"),
            "applicability": item["patch"].get("applicability"),
            "reason_code": item["patch"]["reason_code"],
            "reason": item["patch"]["reason"],
        }
        for item in accepted
        if item.get("change") == "decision_patch"
    ]
    notes_doc = {
        "schema_version": PROTOCOL_VERSION,
        "feedback_id": feedback_id,
        "base_artifact_sha256": base_hash,
        "notes": notes,
    }
    write_json(candidate_root / "engine" / "iteration_notes.json", notes_doc)
    if patches:
        write_json(
            candidate_root / "engine" / "decision_patches.json",
            {
                "schema_version": PROTOCOL_VERSION,
                "feedback_id": feedback_id,
                "base_artifact_sha256": base_hash,
                "patches": patches,
            },
        )

    candidate_version = "av_" + digest_obj({
        "base_artifact_version": policy["artifact_version"],
        "base_artifact_sha256": base_hash,
        "accepted_packet_ids": [item["packet_id"] for item in accepted],
        "notes": notes,
        "patches": patches,
    })[:24]
    updated = copy.deepcopy(policy)
    updated["artifact_version"] = candidate_version
    updated["iteration"] = {
        "feedback_id": feedback_id,
        "base_artifact_sha256": base_hash,
        "accepted_packet_ids": [item["packet_id"] for item in accepted],
        "notes_file": "engine/iteration_notes.json",
    }
    write_json(candidate_root / "engine" / "policy.json", updated)
    for name in ("dependency_graph.json", "shared_capabilities.json"):
        path = candidate_root / "engine" / name
        if path.exists():
            data = load_json(path)
            if isinstance(data, dict):
                data["artifact_version"] = candidate_version
                write_json(path, data)

    engine_hash = path_digest(candidate_root / "engine")
    manifest = load_json(candidate_root / "artifact_manifest.json")
    for entry in manifest["entries"]:
        entry["artifact_version"] = candidate_version
        entry["sha256"] = engine_hash
    write_json(candidate_root / "artifact_manifest.json", manifest)

    ledger_path = candidate_root / LEDGER_NAME
    if ledger_path.exists():
        with ledger_path.open("r", encoding="utf-8", newline="") as handle:
            ledger = list(csv.DictReader(handle))
        for row in ledger:
            row["artifact_version"] = candidate_version
            row["artifact_sha256"] = engine_hash
        if ledger:
            with ledger_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(ledger[0]))
                writer.writeheader()
                writer.writerows(ledger)

    build_report_path = candidate_root / "build_report.json"
    if build_report_path.exists():
        build_report = load_json(build_report_path)
        build_report["base_artifact_version"] = build_report.get("artifact_version")
        build_report["artifact_version"] = candidate_version
        build_report["candidate_of"] = base_hash
        build_report["iteration_feedback_id"] = feedback_id
        write_json(build_report_path, build_report)
        matrix_path = candidate_root / "engine" / "rule_test_matrix.json"
        if matrix_path.exists():
            links = {
                "迭代报告": "iteration.html",
                "依赖图": "engine/dependency_graph.json",
                "共享能力复用记录": "engine/shared_capabilities.json",
                "语义缺口": "engine/semantic_gaps.json",
            }
            if ledger_path.exists():
                links["建设台账"] = LEDGER_NAME
            write_status_page(
                candidate_root / "status.html",
                build_report,
                load_json(matrix_path),
                links=links,
            )
    return candidate_version, engine_hash


def _report_decisions(decisions):
    counts = {"accept": 0, "reject": 0, "defer": 0}
    for item in decisions:
        counts[item["decision"]] += 1
    return counts


def iterate(base_path, feedback_path, input_path, out_path, store=None):
    packet = _checked_packet(load_json(feedback_path))
    base_root = Path(base_path)
    if not base_root.is_dir() or base_root.is_symlink():
        raise AuditError("invalid_baseline", str(base_root))
    base_hash = tree_digest(base_root)
    if packet["base_artifact_sha256"] != base_hash:
        raise AuditError(
            "feedback_baseline_hash_mismatch",
            "反馈包基线哈希与当前baseline不一致",
        )
    policy = load_policy(base_root)
    tasks = _task_index(input_path)

    decisions = []
    accepted = []
    for item in packet["items"]:
        task = tasks.get(item["audit_unit_id"])
        rule = None
        if task is not None:
            key = task["rule_id"] + "@" + task["rule_version"]
            rule = policy["rules"].get(key)
        decision, reason, citations, patch = _decide(item, task, rule)
        record = {
            "packet_id": item["packet_id"],
            "audit_unit_id": item["audit_unit_id"],
            "rule_id": task["rule_id"] if task else None,
            "rule_version": task["rule_version"] if task else None,
            "teacher_source": item["teacher_source"],
            "teacher_proposal": item["teacher_proposal"],
            "decision": decision,
            "reason": reason,
            "rule_citations": citations,
            "change": "decision_patch" if patch else "clarification_only",
        }
        if patch:
            record["patch"] = patch
        decisions.append(record)
        if decision == "accept":
            accepted.append(record)

    out = Path(out_path)
    if out.is_symlink() or out.exists():
        raise AuditError("output_exists", str(out))
    shutil.copytree(base_root, out)
    base_engine_hash = path_digest(base_root / "engine")

    if accepted:
        action = "changed"
        candidate_version, engine_hash = _apply_accepts(
            out, policy, base_hash, packet["feedback_id"], accepted
        )
    else:
        action = "no_change"
        candidate_version = policy["artifact_version"]
        engine_hash = path_digest(out / "engine")

    counts = _report_decisions(decisions)
    accepted_ids = [item["packet_id"] for item in accepted]
    rejected_ids = [
        item["packet_id"] for item in decisions if item["decision"] == "reject"
    ]
    deferred_ids = [
        item["packet_id"] for item in decisions if item["decision"] == "defer"
    ]
    baseline_unchanged = tree_digest(base_root) == base_hash
    candidate_artifact_hash = digest_obj(inventory(out, exclude=METADATA_FILES))
    report = {
        "schema_version": PROTOCOL_VERSION,
        "action": action,
        "feedback_id": packet["feedback_id"],
        "base_artifact_sha256": base_hash,
        "baseline_frozen": True,
        "baseline_unchanged": baseline_unchanged,
        "parent_artifact_version": policy["artifact_version"],
        "candidate_artifact_version": candidate_version,
        "candidate_artifact_sha256": candidate_artifact_hash,
        "baseline_engine_sha256": base_engine_hash,
        "candidate_engine_sha256": engine_hash,
        "decisions": decisions,
        "decision_counts": counts,
        "accepted_packet_ids": accepted_ids,
        "rejected_packet_ids": rejected_ids,
        "deferred_packet_ids": deferred_ids,
        "changed_rules": [
            {
                "rule_id": item["rule_id"],
                "rule_version": item["rule_version"],
                "packet_id": item["packet_id"],
                "reason": item["reason"],
            }
            for item in accepted
        ],
        "comparison": {
            "base_artifact_version": policy["artifact_version"],
            "candidate_artifact_version": candidate_version,
            "base_engine_sha256": base_engine_hash,
            "candidate_engine_sha256": engine_hash,
            "changed_files": _changed_files(base_root, out),
            "decision_counts": counts,
        },
        "input": str(input_path),
    }
    if action == "no_change":
        report["no_change_basis"] = {
            "reason": "全部建议被拒绝或待澄清，候选engine与baseline逐字节一致。",
            "engine_identical": engine_hash == base_engine_hash,
            "baseline_unchanged": baseline_unchanged,
            "artifact_changes": [],
            "cited_decisions": sum(
                1 for item in decisions if item["rule_citations"]
            ),
        }
    write_json(out / "iteration_report.json", report)
    write_iteration_page(out / "iteration.html", report)
    if store is not None:
        store.add_candidate(
            base_hash,
            out,
            candidate_version,
            tree_digest(out),
            action,
            packet["feedback_id"],
        )
    return {
        "command": "iterate",
        "action": action,
        "feedback_id": packet["feedback_id"],
        "decisions": counts,
        "accepted_packet_ids": accepted_ids,
        "baseline_unchanged": baseline_unchanged,
        "candidate_artifact_version": candidate_version,
        "out": str(out),
    }



