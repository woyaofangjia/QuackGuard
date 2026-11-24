"""D10 验收演练：冻结核对、两人16项任务、插件故障与干净目录五类命令。"""
import tempfile
import unittest
from pathlib import Path

from quackguard.artifacts import build, load_policy
from quackguard.io_utils import load_json, rows
from d10_support import (
    CONFIG,
    ROOT,
    TASKS,
    fault_review,
    five_commands_clean,
    public_gold_run,
    scan_delivery,
    verify_artifacts,
    verify_outputs,
    write_merged_rules,
)
from d10_tasks import run_sixteen

DELIVERY_FILES = (
    "README.md",
    "SOP.md",
    "config.json",
    "policy.json",
    "60条建设台账.csv",
    "resources.md",
    "test_report.md",
    "manifest_check.json",
    "selection_freeze.json",
    "artifacts/artifact_manifest.json",
    "artifacts/engine/policy.json",
    "traces/infer.trace.jsonl",
    "traces/public_gold.trace.jsonl",
    "acceptance/reviewer_a.json",
    "acceptance/reviewer_b.json",
    "acceptance/acceptance.md",
)


class D10AcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.rules_path, _rules = write_merged_rules(base)
        cls.artifacts = base / "baseline"
        cls.built = build(cls.rules_path, CONFIG, cls.artifacts)
        cls.policy = load_policy(cls.artifacts)
        cls.outputs, cls.traces = public_gold_run(cls.policy)
        cls.reviewer_a, outputs_a, refs_a = run_sixteen(
            "A", base / "reviewer_a"
        )
        cls.reviewer_b, outputs_b, refs_b = run_sixteen(
            "B", base / "reviewer_b"
        )
        cls.faults_a = fault_review(
            "A", base / "reviewer_a", rows(TASKS), outputs_a, refs_a
        )
        cls.faults_b = fault_review(
            "B", base / "reviewer_b", rows(TASKS), outputs_b, refs_b
        )
        cls.clean = five_commands_clean(base / "clean")
        cls.manifest = verify_artifacts(cls.artifacts)
        cls.output_check = verify_outputs(
            cls.outputs, cls.traces, rows(TASKS), cls.policy
        )
        cls.scan = scan_delivery(ROOT)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_manifest_hash_and_version(self):
        self.assertTrue(self.manifest["ok"], self.manifest)
        self.assertEqual(self.manifest["entries"], 62)
        self.assertEqual(
            self.manifest["artifact_version"], self.built["artifact_version"]
        )
        self.assertEqual(self.manifest["hash_mismatch"], [])
        self.assertEqual(self.manifest["version_mismatch"], [])
        self.assertEqual(self.manifest["duplicates"], [])

    def test_artifact_version_and_trace(self):
        self.assertTrue(self.output_check["ok"], self.output_check)
        self.assertEqual(self.output_check["records"], 64)
        self.assertGreater(self.output_check["trace_rows"], 0)
        self.assertEqual(
            {row["artifact_version"] for row in self.outputs},
            {self.built["artifact_version"]},
        )

    def test_baseline_candidate_freeze(self):
        tasks = {item["id"]: item for item in self.reviewer_a["tasks"]}
        activation = tasks["P-TASK-13"]
        rollback = tasks["P-TASK-14"]
        self.assertTrue(activation["passed"], activation)
        self.assertTrue(rollback["passed"], rollback)
        self.assertNotEqual(
            activation["evidence"]["baseline_version"],
            activation["evidence"]["candidate_version"],
        )
        self.assertEqual(
            rollback["evidence"]["active_version"],
            activation["evidence"]["baseline_version"],
        )
        baseline_manifest = self.reviewer_a["baseline_manifest"]
        self.assertTrue(baseline_manifest["ok"], baseline_manifest)
        self.assertEqual(
            baseline_manifest["root_sha256"],
            self.reviewer_a["baseline_root_sha256"],
        )

    def test_two_reviewers_sixteen_tasks(self):
        for record in (self.reviewer_a, self.reviewer_b):
            self.assertTrue(record["all_passed"], record)
            self.assertEqual(len(record["tasks"]), 16)
            self.assertEqual(
                [item["id"] for item in record["tasks"]],
                ["P-TASK-%02d" % index for index in range(1, 17)],
            )
        self.assertEqual(
            [(item["id"], item["passed"]) for item in self.reviewer_a["tasks"]],
            [(item["id"], item["passed"]) for item in self.reviewer_b["tasks"]],
        )

    def test_plugin_faults_two_reviewers(self):
        for record in (self.faults_a, self.faults_b):
            self.assertTrue(record["all_passed"], record)
            result = record["plugin_faults"]
            self.assertEqual(result["healthy_status"], "ok")
            self.assertEqual(result["crash_status"], "failed")
            self.assertEqual(result["crash_error"], "plugin_failed")
            self.assertEqual(result["timeout_status"], "failed")
            self.assertEqual(result["timeout_error"], "plugin_timeout")
            self.assertEqual(result["disabled_status"], "disabled")
            self.assertEqual(result["disabled_invocations"], 0)
            self.assertTrue(result["input_unchanged"])

    def test_five_commands_clean_dir(self):
        self.assertTrue(self.clean["ok"], self.clean)
        self.assertEqual(
            self.clean["codes"],
            {
                "build": 0,
                "infer": 0,
                "annotate": 0,
                "iterate": 0,
                "evaluate": 0,
            },
        )
        self.assertEqual(self.clean["records"], 64)
        self.assertEqual(self.clean["execution_errors"], 62)
        self.assertEqual(self.clean["completed"], 2)
        self.assertEqual(
            self.clean["completed_reason_codes"], ["rule_not_applicable"]
        )
        self.assertEqual(self.clean["error_codes"], ["model_not_configured"])
        self.assertEqual(self.clean["annotation_records"], 0)
        self.assertEqual(self.clean["annotation_failures"], 3)
        self.assertTrue(all(self.clean["pages"].values()), self.clean["pages"])

    def test_clean_tree_no_secret_cache(self):
        self.assertFalse((ROOT / ".env").exists())
        self.assertTrue(self.scan["ok"], self.scan["issues"])

    def test_delivery_model_config(self):
        config = load_json(ROOT / "delivery" / "config.json")
        self.assertEqual(config["model"], "glm-4v-flash")
        self.assertIn("general", config)
        self.assertIn("supplement", config)
        artifacts = Path(self.temporary.name) / "model_build"
        built = build(self.rules_path, ROOT / "delivery" / "config.json", artifacts)
        policy = load_policy(artifacts)
        self.assertEqual(policy["model"], "glm-4v-flash")
        self.assertEqual(policy["artifact_version"], built["artifact_version"])
        self.assertEqual(len(policy["rules"]), 62)

    def test_delivery_documents_present(self):
        missing = [
            relative for relative in DELIVERY_FILES
            if not (ROOT / "delivery" / relative).is_file()
        ]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
