from pathlib import Path

from . import PROTOCOL_VERSION, __version__
from .artifact_hash import digest_obj, path_digest
from .b001_sop import build_implementation
from .contracts import safe_relative
from .errors import AuditError
from .io_utils import load_json, new_directory, numbered_rows, write_json


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


def build(rules_path, config_path, out_path, store=None):
    rules = checked_rules(rules_path)
    config = load_json(config_path)
    if not isinstance(config, dict):
        raise AuditError("invalid_config")

    source_hashes = {rule_key(rule): digest_obj(rule) for rule in rules}
    version_input = {"protocol": PROTOCOL_VERSION, "rules": rules, "config": config}
    artifact_version = "av_" + digest_obj(version_input)[:24]
    implementations = {}
    for rule in rules:
        if rule["rule_id"] == "B001":
            key = rule_key(rule)
            implementations[key] = build_implementation(
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

    if store:
        store.add_rules(rules, source_hashes)
        build_id = store.add_build(
            artifact_version=artifact_version,
            root_path=out.resolve(),
            rules_sha256=digest_obj(rules),
            config_sha256=digest_obj(config),
        )
        store.add_versions(build_id, entries)
    status_counts = {}
    for row in matrix:
        status = row["implementation_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "command": "build",
        "status": "ready",
        "artifact_version": artifact_version,
        "rule_versions": len(entries),
        "implementation_status_counts": status_counts,
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
