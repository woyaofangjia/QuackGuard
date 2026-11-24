import shutil
from pathlib import Path

from . import PROTOCOL_VERSION
from .artifact_hash import tree_digest
from .errors import AuditError
from .io_utils import load_json, write_json


def iterate(base_path, feedback_path, out_path):
    packet = load_json(feedback_path)
    if not isinstance(packet, dict) or not isinstance(packet.get("items"), list):
        raise AuditError("invalid_feedback")
    if any(not isinstance(item, dict) for item in packet["items"]):
        raise AuditError("invalid_feedback", "items必须只包含对象")

    base_hash = tree_digest(base_path)
    if packet.get("base_artifact_sha256") != base_hash:
        raise AuditError("feedback_baseline_hash_mismatch")
    out = Path(out_path)
    if out.is_symlink() or out.exists():
        raise AuditError("output_exists", str(out))
    shutil.copytree(base_path, out)

    decisions = []
    for item in packet["items"]:
        decisions.append({
            "packet_id": item.get("packet_id"),
            "decision": "defer",
            "reason": "D1仅保留已冻结基线，不自动采纳教师建议。",
            "rule_citations": [],
        })
    write_json(out / "iteration_report.json", {
        "schema_version": PROTOCOL_VERSION,
        "action": "no_change",
        "base_artifact_sha256": base_hash,
        "decisions": decisions,
    })
    return {
        "command": "iterate",
        "action": "no_change",
        "baseline_unchanged": tree_digest(base_path) == base_hash,
        "out": str(out),
    }
