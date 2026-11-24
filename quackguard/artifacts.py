from pathlib import Path

from . import PROTOCOL_VERSION, __version__
from .artifact_hash import digest_obj, path_digest
from .b001_sop import build_implementation
from .contracts import safe_relative
from .rule_sop import build_r_implementation
from .errors import AuditError
from .io_utils import (
    load_json,
    new_directory,
    numbered_rows,
    numbered_rows_lenient,
    write_json,
)
from .report import write_ledger, write_status_page

REQUIRED_RULE_FIELDS = ("rule_id", "version", "rule_text")
LEDGER_NAME = "60条建设台账.csv"


def rule_key(rule):
    return rule["rule_id"] + "@" + rule["version"]


def checked_rules(path):
    rules = numbered_rows(path)
    seen = {}
    required_fields = ("rule_id", "version", "rule_text")
    for line_number, rule in rules:
        if not isinstance(rule, dict):
            raise AuditError("invalid_rule", f"第{line_number}行：规则必须是JSON对象")
        for field in required_fields:
            if not isinstance(rule.get(field), str) or not rule[field].strip():
                raise AuditError(
                    "invalid_rule", f"第{line_number}行：{field}必须为非空字符串"
                )
        key = rule_key(rule)
        if key in seen:
            raise AuditError(
                "duplicate_rule",
                f"第{line_number}行：{key}，首次出现于第{seen[key]}行",
            )
        seen[key] = line_number
    return sorted(
        (rule for _, rule in rules),
        key=lambda rule: (rule["rule_id"], rule["version"]),
    )


def split_rules(path):
    """逐行隔离坏规则：返回 (合法规则, 失败清单)。

    坏行、缺字段行和重复版本只记入失败清单，不影响同文件中的合法规则。
    """
    records, failures = numbered_rows_lenient(path)
    seen = {}
    valid = []
    for line_number, rule in records:
        if not isinstance(rule, dict):
            failures.append({
                "line": line_number,
                "error": "规则必须是JSON对象",
            })
            continue
        missing = [
            field
            for field in REQUIRED_RULE_FIELDS
            if not isinstance(rule.get(field), str) or not rule[field].strip()
        ]
        if missing:
            failures.append({
                "line": line_number,
                "error": "缺少非空字段：" + ", ".join(missing),
            })
            continue
        key = rule_key(rule)
        if key in seen:
            failures.append({
                "line": line_number,
                "error": f"重复规则{key}，首次出现于第{seen[key]}行",
            })
            continue
        seen[key] = line_number
        valid.append(rule)
    failures.sort(key=lambda item: item["line"])
    return (
        sorted(valid, key=lambda rule: (rule["rule_id"], rule["version"])),
        failures,
    )


def _resume_entries(root, artifact_version, rules):
    """现有产物与当前规则完全一致时返回版本数，否则返回None。"""
    try:
        manifest = load_json(root / "artifact_manifest.json")
        entries = manifest["entries"]
        if not isinstance(entries, list):
            return None
        keys = set()
        for entry in entries:
            if entry.get("artifact_version") != artifact_version:
                return None
            keys.add(entry["rule_id"] + "@" + entry["rule_version"])
    except (AuditError, KeyError, TypeError, OSError):
        return None
    if keys != {rule_key(rule) for rule in rules}:
        return None
    return len(entries)


def _filter_rules(rules, only):
    if not only:
        return rules, []
    wanted = set(only)
    kept = [
        rule
        for rule in rules
        if rule["rule_id"] in wanted or rule_key(rule) in wanted
    ]
    present = set()
    for rule in kept:
        present.add(rule["rule_id"])
        present.add(rule_key(rule))
    return kept, [item for item in only if item not in present]


def _shared_capabilities(rule):
    parts = [
        "quackguard.rule_common",
        "quackguard.rule_facts",
        "quackguard.rule_engine",
        "quackguard.rule_sop",
    ]
    family = (rule.get("rule_family_id") or "").lower()
    if family:
        parts.append("quackguard.rules_" + family)
    return ";".join(parts)


def _ledger_entries(rules, implementations, artifact_version, engine_hash):
    entries = []
    for rule in rules:
        if not rule["rule_id"].startswith("R"):
            continue
        key = rule_key(rule)
        built = key in implementations
        entries.append({
            "rule_id": rule["rule_id"],
            "rule_version": rule["version"],
            "build_status": "built" if built else "adapter_only",
            "artifact_version": artifact_version,
            "artifact_sha256": engine_hash,
            "trace_path": "engine/rule_test_matrix.json",
            "shared_capabilities": _shared_capabilities(rule) if built else "",
            "build_seconds": "",
            "manual_edit_count": "0",
            "accuracy": "",
            "machine_review_rate": "",
        })
    return entries


def build(rules_path, config_path, out_path, store=None, resume=False, only=None):
    rules, rule_failures = split_rules(rules_path)
    rules, only_missing = _filter_rules(rules, only)
    if not rules:
        raise AuditError("invalid_rule", "没有可构建的规则")
    config = load_json(config_path)
    if not isinstance(config, dict):
        raise AuditError("invalid_config")

    source_hashes = {rule_key(rule): digest_obj(rule) for rule in rules}
    version_input = {"protocol": PROTOCOL_VERSION, "rules": rules, "config": config}
    artifact_version = "av_" + digest_obj(version_input)[:24]
    if resume:
        root = Path(out_path)
        if not root.exists():
            raise AuditError("output_missing", "resume目标不存在")
        skipped = _resume_entries(root, artifact_version, rules)
        if skipped is None:
            raise AuditError("output_mismatch", "resume目标与当前规则或配置不一致")
        return {
            "command": "build",
            "status": "resumed",
            "artifact_version": artifact_version,
            "rule_versions": skipped,
            "skipped_rule_versions": skipped,
            "rule_failures": len(rule_failures),
            "out": str(root),
        }
    implementations = {}
    for rule in rules:
        key = rule_key(rule)
        if rule["rule_id"] == "B001":
            implementations[key] = build_implementation(
                rule,
                source_hashes[key],
                artifact_version,
                config.get("general", ""),
            )
        elif rule["rule_id"].startswith("R"):
            implementations[key] = build_r_implementation(
                rule,
                source_hashes[key],
                artifact_version,
                config.get("general", ""),
            )
    policy = {
        "protocol_version": PROTOCOL_VERSION,
        "adapter_version": __version__,
        "artifact_version": artifact_version,
        "rules": {rule_key(rule): rule for rule in rules},
        "source_hashes": source_hashes,
        "general": config.get("general", ""),
        "supplement": config.get("supplement", ""),
        "model": config.get("model"),
        "implementations": implementations,
    }
    matrix = []
    for rule in rules:
        matrix.append({
            "rule_id": rule["rule_id"],
            "rule_version": rule["version"],
            "title": rule.get("title", ""),
            "rule_family_id": rule.get("rule_family_id"),
            "source_sha256": source_hashes[rule_key(rule)],
            "implementation_status": (
                "implemented" if rule_key(rule) in implementations
                else "baseline_adapter_only"
            ),
            "case_status": (
                "ready" if rule_key(rule) in implementations else "pending"
            ),
            "planned_checks": [
                "valid_artifact",
                "source_trace",
                "version_selection",
                "decision_branches",
                "contrast_regression",
            ],
        })

    out = new_directory(out_path)
    write_json(out / "engine" / "policy.json", policy)
    write_json(out / "engine" / "rule_test_matrix.json", matrix)
    engine_hash = path_digest(out / "engine")
    ledger = _ledger_entries(rules, implementations, artifact_version, engine_hash)
    if ledger:
        write_ledger(out / LEDGER_NAME, ledger)
    entries = []
    for rule in rules:
        entries.append({
            "rule_id": rule["rule_id"],
            "rule_version": rule["version"],
            "artifact_version": artifact_version,
            "path": "engine",
            "sha256": engine_hash,
        })
    write_json(out / "artifact_manifest.json", {"entries": entries})

    status_counts = {}
    for row in matrix:
        status = row["implementation_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    status = "ready" if not rule_failures else "ready_with_rule_failures"
    report = {
        "status": status,
        "protocol_version": PROTOCOL_VERSION,
        "artifact_version": artifact_version,
        "rule_versions": len(entries),
        "implementation_status_counts": status_counts,
        "rule_failures": rule_failures,
        "only_missing": only_missing,
        "ledger": LEDGER_NAME if ledger else None,
    }
    write_json(out / "build_report.json", report)
    write_status_page(
        out / "status.html",
        report,
        matrix,
        ledger=LEDGER_NAME if ledger else None,
    )

    if store:
        store.add_rules(rules, source_hashes)
        build_id = store.add_build(
            artifact_version=artifact_version,
            root_path=out.resolve(),
            rules_sha256=digest_obj(rules),
            config_sha256=digest_obj(config),
        )
        store.add_versions(build_id, entries)
        if rule_failures:
            store.add_build_failures(build_id, rule_failures)
    return {
        "command": "build",
        "status": status,
        "artifact_version": artifact_version,
        "rule_versions": len(entries),
        "implementation_status_counts": status_counts,
        "rule_failures": len(rule_failures),
        "only_missing": only_missing,
        "out": str(out),
    }


def load_policy(artifacts_path):
    root = Path(artifacts_path)
    manifest = load_json(root / "artifact_manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("entries"), list):
        raise AuditError("invalid_artifact_manifest")

    entries = {}
    hashes = {}
    try:
        for entry in manifest["entries"]:
            relative = entry["path"]
            if not safe_relative(relative):
                raise AuditError("invalid_artifact_manifest", relative)
            candidate = root / relative
            candidate.resolve().relative_to(root.resolve())
            if relative not in hashes:
                hashes[relative] = path_digest(candidate)
            if hashes[relative] != entry["sha256"]:
                raise AuditError("artifact_hash_mismatch", relative)
            key = entry["rule_id"] + "@" + entry["rule_version"]
            if key in entries:
                raise AuditError("duplicate_artifact_entry", key)
            entries[key] = entry
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise AuditError("invalid_artifact_manifest", str(exc)) from exc

    policy = load_json(root / "engine" / "policy.json")
    if not isinstance(policy, dict) or not isinstance(policy.get("rules"), dict):
        raise AuditError("invalid_artifact_policy")
    if set(policy["rules"]) != set(entries):
        raise AuditError("artifact_version_mismatch")
    for entry in entries.values():
        if entry["artifact_version"] != policy.get("artifact_version"):
            raise AuditError("artifact_version_mismatch")
    return policy
