import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quackguard.annotation import annotate_target
from quackguard.artifact_hash import path_digest, tree_digest
from quackguard.artifacts import load_policy
from quackguard.contracts import input_errors, prediction_errors, read_rows
from quackguard.errors import AuditError
from quackguard.io_utils import material_bytes
from quackguard.model_gateway import ModelGateway
from quackguard.quality import score_annotations

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "O2_AI商品审核官_资料包"
SAMPLE = PACK / "03_公开开发集" / "标注样例"
PUBLIC = PACK / "03_公开开发集"
CONFIG = PACK / "04_最小对接样例" / "config.json"
RULE_FILES = [PACK / "01_规则包" / "基础规则版本.jsonl", PACK / "01_规则包" / "进阶规则60条.jsonl"]


class D1Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quackguard-d1-")
        self.dir = Path(self.temp.name)
        self.rules = self.dir / "rules.jsonl"
        source = [rule for path in RULE_FILES for rule in read_rows(path)]
        self.rules.write_text("".join(json.dumps(rule, ensure_ascii=False) + "\n" for rule in source), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        env = os.environ.copy()
        for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "PLUGIN_TIMEOUT_SECONDS", "QUACKGUARD_DB"):
            env.pop(key, None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "run.py"), *map(str, args)],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def build(self, out, db=None):
        args = []
        if db:
            args += ["--db", db]
        args += ["build", "--rules", self.rules, "--config", CONFIG, "--out", out]
        return self.cli(*args)

    def test_official_contract_files_are_unchanged(self):
        official = PACK / "02_协议与评分" / "校验工具"
        for name in ("contracts.py", "artifact_hash.py", "normalization.py"):
            self.assertEqual((ROOT / "quackguard" / name).read_bytes(), (official / name).read_bytes(), name)

    def test_rule_matrix_and_public_materials(self):
        rules = [rule for path in RULE_FILES for rule in read_rows(path)]
        keys = {rule["rule_id"] + "@" + rule["version"] for rule in rules}
        expected = {"B001@3.0", "B001@3.1"} | {f"R{index:03}@3.0" for index in range(1, 61)}
        self.assertEqual(len(rules), 62)
        self.assertEqual(keys, expected)

        input_path = PUBLIC / "inputs" / "audit_inputs.jsonl"
        tasks = read_rows(input_path)
        self.assertEqual(len(tasks), 64)
        self.assertEqual(len({task["audit_unit_id"] for task in tasks}), 64)
        self.assertEqual(sum(len(task["materials"]) for task in tasks), 93)
        for task in tasks:
            self.assertEqual(input_errors(task), [])
            self.assertIn(task["rule_id"] + "@" + task["rule_version"], keys)
            for material in task["materials"]:
                self.assertTrue(material_bytes(input_path, material).startswith(b"\x89PNG"))

    def test_build_manifest_versions_and_persistence(self):
        database = self.dir / "var" / "quackguard.db"
        result = self.build(self.dir / "baseline", database)
        manifest_path = self.dir / "baseline" / "artifact_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        matrix = json.loads((self.dir / "baseline" / "engine" / "rule_test_matrix.json").read_text(encoding="utf-8"))
        self.assertEqual(result["rule_versions"], 62)
        self.assertEqual(len(manifest["entries"]), 62)
        self.assertEqual(len(matrix), 62)
        self.assertEqual(sum(row["case_status"] == "ready" for row in matrix), 62)
        self.assertEqual(sum(row["case_status"] == "pending" for row in matrix), 0)
        engine_hash = path_digest(self.dir / "baseline" / "engine")
        self.assertTrue(all(row["artifact_version"] == result["artifact_version"] and row["sha256"] == engine_hash for row in manifest["entries"]))

        connection = sqlite3.connect(database)
        for table, expected in (("rules", 62), ("builds", 1), ("versions", 62), ("tasks", 1), ("runs", 1)):
            self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], expected, table)
        connection.close()

        second = self.build(self.dir / "second")
        self.assertEqual(second["artifact_version"], result["artifact_version"])
        self.assertEqual(path_digest(self.dir / "second" / "engine"), engine_hash)

    def test_five_commands_offline_and_reference_isolation(self):
        database = self.dir / "quackguard.db"
        baseline = self.dir / "baseline"
        self.build(baseline, database)
        before = tree_digest(baseline)

        predictions = self.dir / "predictions.jsonl"
        self.cli("--db", database, "infer", "--input", SAMPLE / "inputs" / "tasks.jsonl", "--artifacts", baseline, "--out", predictions)
        tasks = read_rows(SAMPLE / "inputs" / "tasks.jsonl")
        outputs = read_rows(predictions)
        self.assertEqual(len(outputs), 3)
        for output, task in zip(outputs, tasks):
            self.assertEqual(output["execution_status"], "error")
            self.assertEqual(output["error_code"], "model_not_configured")
            self.assertEqual(prediction_errors(output, task), [])

        annotations = self.dir / "annotations.jsonl"
        self.cli("--db", database, "annotate", "--input", SAMPLE / "inputs" / "tasks.jsonl", "--targets", SAMPLE / "inputs" / "targets.jsonl", "--artifacts", baseline, "--out", annotations)
        self.assertEqual(read_rows(annotations), [])
        failures = json.loads(Path(str(annotations) + ".errors.json").read_text(encoding="utf-8"))
        self.assertEqual(len(failures), 3)
        self.assertEqual({row["error_code"] for row in failures}, {"model_not_configured"})

        packet = json.loads((PACK / "04_最小对接样例" / "feedback.example.json").read_text(encoding="utf-8"))
        packet["base_artifact_sha256"] = before
        feedback = self.dir / "feedback.json"
        feedback.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
        candidate = self.dir / "candidate"
        result = self.cli("--db", database, "iterate", "--base", baseline, "--feedback", feedback, "--input", SAMPLE / "inputs" / "tasks.jsonl", "--out", candidate)
        self.assertEqual(result["action"], "no_change")
        self.assertEqual(tree_digest(baseline), before)
        self.assertTrue((candidate / "iteration_report.json").is_file())

        report_path = self.dir / "report.json"
        self.cli("--db", database, "evaluate", "--plugin", "quackguard.conclusion", "--inputs", PUBLIC / "inputs" / "audit_inputs.jsonl", "--outputs", PUBLIC / "reference_predictions.jsonl", "--references", PUBLIC / "references" / "audit_gold.jsonl", "--out", report_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["metrics"]["quality"], 1)
        self.assertEqual(report["errors"], [])

        connection = sqlite3.connect(database)
        self.assertEqual(connection.execute("SELECT count(*) FROM tasks WHERE status='completed'").fetchone()[0], 5)
        self.assertEqual(connection.execute("SELECT count(*) FROM runs WHERE status='completed'").fetchone()[0], 5)
        self.assertEqual(connection.execute("SELECT count(*) FROM plugins").fetchone()[0], 1)
        connection.close()
        for name in ("artifacts.py", "inference.py", "annotation.py", "iteration.py"):
            source = (ROOT / "quackguard" / name).read_text(encoding="utf-8")
            self.assertNotIn("references", source, name)
            self.assertNotIn("audit_gold", source, name)

    def test_annotation_arithmetic_fixtures(self):
        tasks = read_rows(SAMPLE / "inputs" / "tasks.jsonl")
        references = read_rows(SAMPLE / "references" / "annotation_gold.jsonl")
        for name in ("reference_annotations.jsonl", "reference_annotations_nfkc.jsonl"):
            score, details = score_annotations(references, read_rows(SAMPLE / name), tasks)
            self.assertEqual(score, 7)
            self.assertTrue(all(item["correct"] for item in details))

    def test_material_path_rejection_and_model_errors(self):
        root = self.dir / "inputs"
        asset = root / "assets" / "a.png"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"png")
        input_path = root / "tasks.jsonl"
        material = {"relative_path": "assets/a.png"}
        self.assertEqual(material_bytes(input_path, material), b"png")
        for relative in ("../a.png", "assets\\a.png", "assets/./a.png", "assets//a.png", str(asset)):
            with self.subTest(relative=relative):
                with self.assertRaises(AuditError) as caught:
                    material_bytes(input_path, {"relative_path": relative})
                self.assertEqual(caught.exception.code, "unsafe_material_path")
        with self.assertRaises(AuditError) as caught:
            material_bytes(input_path, {"relative_path": "assets/missing.png"})
        self.assertEqual(caught.exception.code, "material_not_found")

        with self.assertRaises(AuditError) as caught:
            ModelGateway("demo", base_url=None).require_config()
        self.assertEqual(caught.exception.code, "gateway_not_configured")
        with self.assertRaises(AuditError) as caught:
            ModelGateway(None, base_url="http://localhost").require_config()
        self.assertEqual(caught.exception.code, "model_not_configured")
        ModelGateway("demo", base_url="http://localhost").require_config()

    def test_annotation_failure_keeps_model_traces(self):
        baseline = self.dir / "baseline"
        self.build(baseline)
        policy = load_policy(baseline)
        task = read_rows(SAMPLE / "inputs" / "tasks.jsonl")[0]
        targets = read_rows(SAMPLE / "inputs" / "targets.jsonl")
        target = next(row for row in targets if row["audit_unit_id"] == task["audit_unit_id"])

        class InvalidGateway:
            def messages(self, prompt, task, input_path):
                return []

            def complete(self, messages):
                return "invalid"

        output, traces, error = annotate_target(
            InvalidGateway(), policy, task, target, SAMPLE / "inputs" / "tasks.jsonl"
        )
        self.assertIsNone(output)
        self.assertEqual(error.code, "invalid_model_output")
        self.assertEqual(len(traces), 2)
        self.assertEqual([row["response"] for row in traces], ["invalid", "invalid"])

    def test_tampered_artifact_is_not_executed(self):
        baseline = self.dir / "baseline"
        self.build(baseline)
        policy = baseline / "engine" / "policy.json"
        policy.write_text(policy.read_text(encoding="utf-8") + " ", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "run.py"), "infer", "--input", str(SAMPLE / "inputs" / "tasks.jsonl"), "--artifacts", str(baseline), "--out", str(self.dir / "bad.jsonl")],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("artifact_hash_mismatch", result.stderr)
        self.assertFalse((self.dir / "bad.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
