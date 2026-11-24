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
from .report import (
    write_ledger,
    write_status_page,
    write_versions_page,
)

REQUIRED_RULE_FIELDS = ("rule_id", "version", "rule_text")
LEDGER_NAME = "60条建设台账.csv"
SEMANTIC_GAPS = {
    "R059": (
        "同级要求冲突：所有练习盒展示空心圆与蓝色练习盒不得出现空心圆，"
        "原文未指定例外或优先级；冲突时返回 ambiguous。"
    ),
    "R060": (
        "原文未定义“较小”的尺寸范围或判别方法；"
        "附签方案无法唯一判定时返回 ambiguous。"
    ),
}


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


def _dependencies(rule, implementation):
    dependencies = {"quackguard.rule_common"}
    artifact = implementation.get("artifact", {})
    for value in artifact.get("dependencies", {}).values():
        if not isinstance(value, str) or not value.startswith("quackguard."):
            continue
        if value.startswith("quackguard.rules_f"):
            continue
        dependencies.add(value)
    if rule["rule_id"].startswith("R"):
        family = (rule.get("rule_family_id") or "").lower()
        if family:
            dependencies.add("quackguard.rules_" + family)
    else:
        dependencies.update({"quackguard.b001", "quackguard.b001_facts"})
    return sorted(dependencies)


def _dependency_graph(rules, implementations, artifact_version):
    graph = {
        "artifact_version": artifact_version,
        "rules": {},
        "modules": {},
    }
    for rule in rules:
        key = rule_key(rule)
        implementation = implementations.get(key)
        dependencies = (
            _dependencies(rule, implementation) if implementation else []
        )
        graph["rules"][key] = {
            "rule_id": rule["rule_id"],
            "rule_version": rule["version"],
            "family": (
                implementation.get("sop", {}).get("family")
                if implementation else None
            ),
            "dependencies": dependencies,
        }
        for module in dependencies:
            graph["modules"].setdefault(module, []).append(key)
    for keys in graph["modules"].values():
        keys.sort()
    return graph


def _capability_reuse(graph, implementations, artifact_version):
    modules = {
        module: {"rule_count": len(keys), "rules": keys}
        for module, keys in graph["modules"].items()
    }
    decisions = {}
    for implementation in implementations.values():
        policy = implementation.get("build_policy", {})
        for name, value in policy.get("reuse_decisions", {}).items():
            decisions.setdefault(name, set()).add(str(value))
    return {
        "artifact_version": artifact_version,
        "modules": modules,
        "reuse_decisions": {
            name: sorted(values) for name, values in sorted(decisions.items())
        },
    }


def _shared_capabilities(rule, implementation):
    if implementation is None:
        return ""
    return ";".join(_dependencies(rule, implementation))


def _ledger_entries(rules, implementations, artifact_version, engine_hash):
    entries = []
    for rule in rules:
        if not rule["rule_id"].startswith("R"):
            continue
        key = rule_key(rule)
        implementation = implementations.get(key)
        entries.append({
            "rule_id": rule["rule_id"],
            "rule_version": rule["version"],
            "build_status": "built" if implementation else "adapter_only",
            "artifact_version": artifact_version,
            "artifact_sha256": engine_hash,
            "trace_path": "engine/rule_test_matrix.json",
            "shared_capabilities": _shared_capabilities(rule, implementation),
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
    semantic_gaps = []
    for rule in rules:
        gap = SEMANTIC_GAPS.get(rule["rule_id"], "")
        if gap:
            semantic_gaps.append({
                "rule_id": rule["rule_id"],
                "rule_version": rule["version"],
                "gap": gap,
            })
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
            "semantic_gap": gap,
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
    graph = _dependency_graph(rules, implementations, artifact_version)
    write_json(out / "engine" / "dependency_graph.json", graph)
    write_json(
        out / "engine" / "shared_capabilities.json",
        _capability_reuse(graph, implementations, artifact_version),
    )
    write_json(out / "engine" / "semantic_gaps.json", semantic_gaps)
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
        builds = store.list_builds()
        versions = store.list_versions(build_id)
    else:
        build_id = None
        builds = [{
            "build_id": "-",
            "artifact_version": artifact_version,
            "status": "built",
            "rule_count": len(entries),
            "active_count": 0,
            "created_at": "",
        }]
        versions = [
            {
                "rule_id": entry["rule_id"],
                "rule_version": entry["rule_version"],
                "artifact_version": entry["artifact_version"],
                "sha256": entry["sha256"],
                "is_active": 0,
            }
            for entry in entries
        ]
    write_versions_page(
        out / "versions.html", builds, versions, artifact_version
    )

    status_counts = {}
    for row in matrix:
        status = row["implementation_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    status = "ready" if not rule_failures else "ready_with_rule_failures"
    links = {
        "版本追踪": "versions.html",
        "依赖图": "engine/dependency_graph.json",
        "共享能力复用记录": "engine/shared_capabilities.json",
        "语义缺口": "engine/semantic_gaps.json",
    }
    if ledger:
        links["建设台账"] = LEDGER_NAME
    report = {
        "status": status,
        "protocol_version": PROTOCOL_VERSION,
        "artifact_version": artifact_version,
        "rule_versions": len(entries),
        "implementation_status_counts": status_counts,
        "rule_failures": rule_failures,
        "only_missing": only_missing,
        "ledger": LEDGER_NAME if ledger else None,
        "versions_page": "versions.html",
        "dependency_graph": "engine/dependency_graph.json",
        "shared_capabilities": "engine/shared_capabilities.json",
        "semantic_gaps": semantic_gaps,
        "build_id": build_id,
    }
    write_json(out / "build_report.json", report)
    write_status_page(out / "status.html", report, matrix, links=links)
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
