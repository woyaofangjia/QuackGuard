"""D9 全量回归：公开集、规则分支、对照组、标注、故障与可靠性检查。"""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from quackguard import cli
from quackguard.contracts import read_rows
from quackguard.regression import regress

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "O2_AI商品审核官_资料包"
PUBLIC = PACK / "03_公开开发集"
CONFIG = PACK / "04_最小对接样例" / "config.json"
RULE_FILES = [
    PACK / "01_规则包" / "基础规则版本.jsonl",
    PACK / "01_规则包" / "进阶规则60条.jsonl",
]
ANNOTATION = PUBLIC / "标注样例"


def load_cases():
    spec = importlib.util.spec_from_file_location(
        "quackguard_test_d3_cases", Path(__file__).with_name("test_d3.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CASES


class RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quackguard-d9-")
        cls.dir = Path(cls.temp.name)
        rules = [rule for path in RULE_FILES for rule in read_rows(path)]
        cls.rules_path = cls.dir / "rules.jsonl"
        cls.rules_path.write_text(
            "".join(
                json.dumps(rule, ensure_ascii=False) + "\n" for rule in rules
            ),
            encoding="utf-8",
        )
        cls.cases = load_cases()
        cls.branches_path = cls.dir / "branches.jsonl"
        cls.branches_path.write_text(
            "".join(
                json.dumps(case, ensure_ascii=False) + "\n"
                for case in cls.cases
            ),
            encoding="utf-8",
        )
        cls.out = cls.dir / "regression"
        cls.result = regress(
            cls.rules_path,
            CONFIG,
            PUBLIC / "inputs" / "audit_inputs.jsonl",
            PUBLIC / "references" / "audit_gold.jsonl",
            PUBLIC / "reference_predictions.jsonl",
            cls.out,
            branches_path=cls.branches_path,
            annotation_tasks_path=ANNOTATION / "inputs" / "tasks.jsonl",
            annotation_targets_path=ANNOTATION / "inputs" / "targets.jsonl",
            annotation_references_path=(
                ANNOTATION / "references" / "annotation_gold.jsonl"
            ),
            fault_timeout=1.0,
        )
        cls.report = json.loads(
            (cls.out / "regression_report.json").read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_public_integrity_and_scores(self):
        public = self.report["public_set"]
        self.assertEqual(public["units"], 64)
        integrity = public["integrity"]
        for key in (
            "missing", "duplicate", "extraneous",
            "unidentified", "wrong_version", "other_invalid",
        ):
            self.assertEqual(integrity[key], 0, key)
        self.assertTrue(integrity["ok"])
        metrics = public["metrics"]
        for key in ("accuracy", "correct_decisive_coverage", "quality",
                    "machine_review_rate"):
            self.assertIsNotNone(metrics[key], key)
        self.assertEqual(len(public["per_rule"]), 12)

    def test_reference_baseline_not_counted_as_system_score(self):
        baseline = self.report["reference_baseline"]
        self.assertFalse(baseline["counted_as_system_score"])
        self.assertIn("不是系统成绩", baseline["note"])
        self.assertEqual(baseline["metrics"]["quality"], 1.0)

    def test_branches_and_control_group(self):
        branches = self.report["branches"]
        self.assertEqual(branches["cases"], len(self.cases))
        self.assertEqual(branches["failed"], 0, branches["failures"])
        self.assertTrue(branches["ok"])
        control = self.report["control_group"]
        self.assertTrue(control["matched"])
        self.assertFalse(control["counted_as_system_score"])
        self.assertEqual(control["measured"]["accuracy"], 0.1875)
        self.assertEqual(control["measured"]["machine_review_rate"], 0)

    def test_annotations_and_plugin_faults(self):
        annotations = self.report["annotations"]
        self.assertEqual(annotations["score"], 7.0)
        self.assertEqual(annotations["failures"], [])
        faults = self.report["plugin_faults"]
        self.assertEqual(faults["healthy_status"], "ok")
        self.assertEqual(faults["crash_error"], "plugin_failed")
        self.assertEqual(faults["timeout_error"], "plugin_timeout")
        self.assertEqual(faults["disabled_invocations"], 0)
        self.assertTrue(faults["input_unchanged"])
        self.assertTrue(faults["ok"])

    def test_reliability_checks(self):
        selective = self.report["selective_rebuild"]
        self.assertEqual(selective["built"], 10)
        self.assertEqual(selective["missing"], [])
        self.assertEqual(selective["extra"], [])
        self.assertTrue(selective["source_hash_match"])
        persistence = self.report["persistence"]
        self.assertTrue(persistence["build_persisted"])
        self.assertTrue(persistence["active_persisted"])
        self.assertTrue(persistence["plugin_disabled_persisted"])
        resume = self.report["resume"]
        self.assertEqual(resume["status"], "resumed")
        self.assertEqual(resume["skipped_rule_versions"], 62)
        self.assertTrue(resume["retry_successor_linked"])
        export = self.report["export_consistency"]
        self.assertEqual(export["rows"], 64)
        self.assertTrue(export["repeat_identical"])
        self.assertTrue(export["metric_match"])
        self.assertTrue(export["unique_ids"])
        self.assertTrue(export["correct_flags"])

    def test_freeze_and_overall_status(self):
        freeze = self.report["freeze"]
        self.assertEqual(freeze["dependencies"]["third_party"], [])
        self.assertIsNone(freeze["model"])
        for key in (
            "rules_sha256", "config_sha256", "prompt_sha256",
            "baseline_sha256", "plugin_manifest_sha256",
        ):
            self.assertEqual(len(freeze[key]), 64, key)
        self.assertEqual(self.result["status"], "passed")
        self.assertTrue(all(self.report["checks"].values()))

    def test_cli_regress_command(self):
        out = self.dir / "cli"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main([
                "regress",
                "--rules", str(self.rules_path),
                "--config", str(CONFIG),
                "--input", str(PUBLIC / "inputs" / "audit_inputs.jsonl"),
                "--references", str(PUBLIC / "references" / "audit_gold.jsonl"),
                "--baseline", str(PUBLIC / "reference_predictions.jsonl"),
                "--branches", str(self.branches_path),
                "--annotation-tasks", str(ANNOTATION / "inputs" / "tasks.jsonl"),
                "--annotation-targets", str(ANNOTATION / "inputs" / "targets.jsonl"),
                "--annotation-references",
                str(ANNOTATION / "references" / "annotation_gold.jsonl"),
                "--fault-timeout", "1.0",
                "--out", str(out),
            ])
        self.assertEqual(code, 0, stdout.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "passed")
        self.assertTrue((out / "regression_report.json").exists())
        self.assertTrue((out / "regression.html").exists())


if __name__ == "__main__":
    unittest.main()
