"""D7 安全反馈迭代验收。

覆盖：baseline冻结与哈希校验、教师建议逐条accept/reject/defer、独立candidate、
有依据no_change、候选比较、激活门槛、活动版本指针与回滚后推理重绑。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quackguard.artifact_hash import tree_digest
from quackguard.artifacts import build, load_policy
from quackguard.errors import AuditError
from quackguard.iteration import iterate
from quackguard.versioning import activate, rollback
from quackguard.storage import Store
from test_d3 import CONFIG, PACK, RULE_FILES

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = PACK / "03_公开开发集" / "标注样例" / "inputs" / "tasks.jsonl"


def material(mid="m1"):
    return {
        "material_id": mid,
        "role": "package_photos",
        "relative_path": mid + ".png",
        "media_type": "image/png",
        "page_index": 1,
    }


def task(uid, rule_id, version, metadata=None):
    return {
        "audit_unit_id": uid,
        "sample_id": "s-" + uid,
        "rule_id": rule_id,
        "rule_version": version,
        "review_date": "2026-06-15",
        "metadata": metadata or {},
        "materials": [material()],
    }


def feedback_item(packet_id, unit_id, proposal, source):
    return {
        "packet_id": packet_id,
        "audit_unit_id": unit_id,
        "teacher_proposal": proposal,
        "teacher_source": source,
    }


def gate(new_errors=0, new_joint_errors=0, invalid=0, missing=0,
         contrast_complete=True, feedback_delta=0, protection_delta=0):
    return {
        "new_joint_errors": new_joint_errors,
        "new_errors": new_errors,
        "invalid": invalid,
        "missing": missing,
        "contrast_complete": contrast_complete,
        "feedback_accuracy_delta": feedback_delta,
        "protection_accuracy_delta": protection_delta,
    }


class D7IterationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quackguard-d7-")
        cls.dir = Path(cls.temp.name)
        rules = [rule for path in RULE_FILES for rule in rules_rows(path)]
        cls.rules_path = cls.dir / "rules.jsonl"
        cls.rules_path.write_text(
            "".join(
                json.dumps(rule, ensure_ascii=False) + "\n" for rule in rules
            ),
            encoding="utf-8",
        )
        cls.baseline = cls.dir / "baseline"
        build(cls.rules_path, CONFIG, cls.baseline)
        cls.policy = load_policy(cls.baseline)
        cls.base_hash = tree_digest(cls.baseline)
        cls.tasks = {
            "u-r007a": task("u-r007a", "R007", cls.version("R007")),
            "u-r007b": task("u-r007b", "R007", cls.version("R007")),
            "u-r007c": task("u-r007c", "R007", cls.version("R007")),
            "u-r022": task(
                "u-r022", "R022", cls.version("R022"),
                {"package_longest_edge_mm": 80},
            ),
            "u-b001": task("u-b001", "B001", cls.version("B001")),
        }
        cls.tasks_path = cls.dir / "repair.jsonl"
        cls.tasks_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False) + "\n"
                for item in cls.tasks.values()
            ),
            encoding="utf-8",
        )

    @classmethod
    def version(cls, rule_id):
        prefix = rule_id + "@"
        return next(
            key.split("@", 1)[1]
            for key in cls.policy["rules"]
            if key.startswith(prefix)
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.store = Store(self.dir / (self._testMethodName + ".db"))

    def tearDown(self):
        self.store.close()

    def packet(self, name, items, base_hash=None):
        path = self.dir / (name + ".feedback.json")
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.3.1",
                    "feedback_id": "fb-" + name,
                    "base_artifact_sha256": base_hash or self.base_hash,
                    "items": items,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def iterate(self, name, items):
        path = self.packet(name, items)
        candidate = self.dir / name
        result = iterate(
            self.baseline, path, self.tasks_path, candidate, store=self.store
        )
        report = json.loads(
            (candidate / "iteration_report.json").read_text(encoding="utf-8")
        )
        return candidate, result, report

    def changed_candidate(self, name="changed"):
        return self.iterate(name, [
            feedback_item(
                "p-accept", "u-r007a",
                "确认 R007 按原文执行型号归一化。",
                "R007@" + self.version("R007"),
            ),
            feedback_item(
                "p-reject", "u-r022",
                "小包装阈值应调整为 90mm。",
                "R022@" + self.version("R022"),
            ),
            feedback_item(
                "p-defer", "u-r007b",
                "请核查型号归一化约定。",
                "public_demo",
            ),
        ])

    def test_baseline_hash_mismatch_rejected(self):
        path = self.packet(
            "wrong", [feedback_item("p1", "u-r007a", "请核查。", "public_demo")],
            base_hash="0" * 64,
        )
        with self.assertRaises(AuditError) as ctx:
            iterate(
                self.baseline, path, self.tasks_path, self.dir / "wrong-candidate"
            )
        self.assertEqual(ctx.exception.code, "feedback_baseline_hash_mismatch")
        self.assertFalse((self.dir / "wrong-candidate").exists())

    def test_invalid_feedback_rejected(self):
        duplicate = self.packet("dup", [
            feedback_item("same", "u-r007a", "请核查。", "public_demo"),
            feedback_item("same", "u-r007b", "请核查。", "public_demo"),
        ])
        with self.assertRaises(AuditError) as ctx:
            iterate(
                self.baseline, duplicate, self.tasks_path, self.dir / "dup-candidate"
            )
        self.assertEqual(ctx.exception.code, "duplicate_packet_id")
        wrong_schema = self.packet("schema", [
            feedback_item("p1", "u-r007a", "请核查。", "public_demo"),
        ])
        packet = json.loads(wrong_schema.read_text(encoding="utf-8"))
        packet["schema_version"] = "0.0.0"
        wrong_schema.write_text(json.dumps(packet), encoding="utf-8")
        with self.assertRaises(AuditError) as ctx:
            iterate(
                self.baseline,
                wrong_schema,
                self.tasks_path,
                self.dir / "schema-candidate",
            )
        self.assertEqual(ctx.exception.code, "invalid_feedback")

    def test_decisions_citations_and_independent_candidate(self):
        candidate, result, report = self.iterate("decisions", [
            feedback_item(
                "p-accept", "u-r007a",
                "确认 R007 按原文执行型号归一化。",
                "R007@" + self.version("R007"),
            ),
            feedback_item(
                "p-reject-number", "u-r022",
                "小包装阈值应调整为 90mm。",
                "R022@" + self.version("R022"),
            ),
            feedback_item(
                "p-reject-change", "u-r007c",
                "建议改为忽略大小写。",
                "R007@" + self.version("R007"),
            ),
            feedback_item(
                "p-reject-source", "u-r007b",
                "请按其他规则执行。",
                "R999@" + self.version("R007"),
            ),
            feedback_item(
                "p-defer", "u-r007b",
                "请核查型号归一化约定。",
                "public_demo",
            ),
        ])
        self.assertEqual(result["action"], "changed")
        self.assertEqual(
            report["decision_counts"], {"accept": 1, "reject": 3, "defer": 1}
        )
        decisions = {item["packet_id"]: item for item in report["decisions"]}
        self.assertEqual(decisions["p-accept"]["decision"], "accept")
        self.assertEqual(decisions["p-defer"]["decision"], "defer")
        for packet_id in ("p-reject-number", "p-reject-change", "p-reject-source"):
            item = decisions[packet_id]
            self.assertEqual(item["decision"], "reject")
            self.assertTrue(item["rule_citations"])
            self.assertEqual(
                item["rule_citations"][0]["quote"],
                self.policy["rules"][
                    item["rule_id"] + "@" + item["rule_version"]
                ]["rule_text"],
            )
        self.assertIn("90", decisions["p-reject-number"]["reason"])
        self.assertIn("不一致", decisions["p-reject-source"]["reason"])

        self.assertEqual(tree_digest(self.baseline), self.base_hash)
        self.assertTrue(result["baseline_unchanged"])
        page = (candidate / "iteration.html").read_text(encoding="utf-8")
        self.assertIn("改动规则与原因", page)
        self.assertIn("候选比较", page)
        self.assertIn(result["candidate_artifact_version"], page)
        policy = load_policy(candidate)
        self.assertNotEqual(
            policy["artifact_version"], self.policy["artifact_version"]
        )
        self.assertEqual(
            policy["iteration"]["accepted_packet_ids"], ["p-accept"]
        )
        encoded = json.dumps(policy, ensure_ascii=False)
        for packet_id in ("p-reject-number", "p-reject-change", "p-reject-source", "p-defer"):
            self.assertNotIn(packet_id, encoded)
        self.assertIn("engine/policy.json", report["comparison"]["changed_files"])
        self.assertTrue(report["changed_rules"])

    def test_no_change_is_evidence_backed(self):
        candidate, result, report = self.iterate("nochange", [
            feedback_item(
                "p-reject", "u-r022",
                "小包装阈值应调整为 90mm。",
                "R022@" + self.version("R022"),
            ),
            feedback_item(
                "p-defer", "u-r007a",
                "请核查型号归一化约定。",
                "public_demo",
            ),
        ])
        self.assertEqual(result["action"], "no_change")
        self.assertEqual(report["decision_counts"], {"accept": 0, "reject": 1, "defer": 1})
        self.assertTrue(report["no_change_basis"]["engine_identical"])
        self.assertTrue(report["no_change_basis"]["baseline_unchanged"])
        self.assertEqual(report["comparison"]["changed_files"], [])
        self.assertEqual(report["candidate_artifact_sha256"], self.base_hash)
        policy = load_policy(candidate)
        self.assertEqual(
            policy["artifact_version"], self.policy["artifact_version"]
        )
        self.assertNotIn("iteration", policy)
        self.assertEqual(tree_digest(self.baseline), self.base_hash)
        page = (candidate / "iteration.html").read_text(encoding="utf-8")
        self.assertIn("无改动规则", page)
        self.assertIn("no_change 依据", page)

    def test_empty_feedback_is_no_change(self):
        candidate, result, report = self.iterate("empty", [])
        self.assertEqual(result["action"], "no_change")
        self.assertEqual(report["decision_counts"], {"accept": 0, "reject": 0, "defer": 0})
        self.assertEqual(report["decisions"], [])
        self.assertTrue(report["no_change_basis"]["engine_identical"])

    def test_gate_failure_blocks_activation(self):
        candidate, _, _ = self.changed_candidate("gate-candidate")
        bad_gate = self.dir / "bad-gate.json"
        bad_gate.write_text(
            json.dumps(gate(new_errors=1), ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(AuditError) as ctx:
            activate(
                self.baseline,
                candidate,
                self.store,
                gate_path=bad_gate,
                out_path=self.dir / "bad-activation.json",
            )
        self.assertEqual(ctx.exception.code, "activation_gate_failed")
        self.assertIsNone(self.store.get_active())

        good_gate = self.dir / "good-gate.json"
        good_gate.write_text(
            json.dumps(gate(), ensure_ascii=False), encoding="utf-8"
        )
        result = activate(
            self.baseline,
            candidate,
            self.store,
            gate_path=good_gate,
            out_path=self.dir / "activation.json",
        )
        self.assertEqual(result["status"], "activated")
        active = self.store.get_active()
        self.assertEqual(active["root_path"], str(candidate.resolve()))
        self.assertEqual(active["artifact_version"], load_policy(candidate)["artifact_version"])
        self.assertEqual(active["root_sha256"], tree_digest(candidate))
        self.assertTrue((self.dir / "activation.html").exists())
        policy_text = json.dumps(load_policy(candidate), ensure_ascii=False)
        self.assertNotIn("p-reject", policy_text)
        built = self.store.find_build(candidate)
        self.assertIsNotNone(built)
        versions = self.store.list_versions(built["build_id"])
        self.assertTrue(versions)
        self.assertTrue(all(row["is_active"] == 1 for row in versions))

    def test_active_pointer_persists_after_reopen(self):
        candidate, _, _ = self.changed_candidate("persist-candidate")
        gate_path = self.dir / "persist-gate.json"
        gate_path.write_text(json.dumps(gate()), encoding="utf-8")
        activate(
            self.baseline,
            candidate,
            self.store,
            gate_path=gate_path,
            out_path=self.dir / "persist-activation.json",
        )
        reopened = Store(self.store.path)
        try:
            active = reopened.get_active()
            self.assertEqual(active["root_path"], str(candidate.resolve()))
            self.assertEqual(
                active["artifact_version"],
                load_policy(candidate)["artifact_version"],
            )
            built = reopened.find_build(candidate)
            self.assertTrue(
                all(row["is_active"] == 1
                    for row in reopened.list_versions(built["build_id"]))
            )
        finally:
            reopened.close()

    def test_no_change_candidate_keeps_baseline_active(self):
        candidate, result, _ = self.iterate("keep", [
            feedback_item(
                "p-defer", "u-r007a",
                "请核查型号归一化约定。",
                "public_demo",
            ),
        ])
        self.assertEqual(result["action"], "no_change")
        good_gate = self.dir / "keep-gate.json"
        good_gate.write_text(json.dumps(gate()), encoding="utf-8")
        activated = activate(
            self.baseline,
            candidate,
            self.store,
            gate_path=good_gate,
            out_path=self.dir / "keep-activation.json",
        )
        self.assertEqual(activated["status"], "kept_baseline")
        active = self.store.get_active()
        self.assertEqual(active["root_path"], str(self.baseline.resolve()))
        self.assertEqual(
            active["artifact_version"], self.policy["artifact_version"]
        )

    def test_rollback_rebinds_inference_to_baseline(self):
        candidate, _, _ = self.changed_candidate("rollback-candidate")
        good_gate = self.dir / "rollback-gate.json"
        good_gate.write_text(json.dumps(gate()), encoding="utf-8")
        activate(
            self.baseline,
            candidate,
            self.store,
            gate_path=good_gate,
            out_path=self.dir / "rollback-activation.json",
        )
        rolled = rollback(
            self.baseline,
            self.store,
            expect_hash=self.base_hash,
            out_path=self.dir / "rollback.json",
            reason="验证回滚重绑",
        )
        self.assertEqual(rolled["status"], "rolled_back")
        active = self.store.get_active()
        self.assertEqual(active["root_path"], str(self.baseline.resolve()))
        self.assertEqual(active["artifact_version"], self.policy["artifact_version"])
        self.assertEqual(rolled["root_sha256"], self.base_hash)

        out = self.dir / "rollback-predictions.jsonl"
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ("OPENAI_BASE_URL", "OPENAI_API_KEY")
        }
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "run.py"),
                "--db",
                str(self.store.path),
                "infer",
                "--input",
                str(SAMPLE),
                "--artifacts",
                "active",
                "--out",
                str(out),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        predictions = [
            json.loads(line)
            for line in out.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(predictions)
        for prediction in predictions:
            self.assertEqual(
                prediction["artifact_version"], self.policy["artifact_version"]
            )

    def test_rollback_hash_mismatch_rejected(self):
        with self.assertRaises(AuditError) as ctx:
            rollback(
                self.baseline,
                self.store,
                expect_hash="f" * 64,
                out_path=self.dir / "mismatch.json",
            )
        self.assertEqual(ctx.exception.code, "rollback_hash_mismatch")
        self.assertIsNone(self.store.get_active())


def rules_rows(path):
    from quackguard.io_utils import rows

    return rows(path)


if __name__ == "__main__":
    unittest.main()
