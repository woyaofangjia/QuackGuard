"""D8 插件与产品功能补齐验收。

覆盖：插件注册与持久化、版本报告、只读输入哈希、进程隔离与超时/崩溃、
禁用插件零调用、证据内容核验、插件视图，以及版本比较不混算规则版本。
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quackguard.plugins import evidence

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "O2_AI商品审核官_资料包" / "03_公开开发集"
INPUTS = PUBLIC / "inputs" / "audit_inputs.jsonl"
OUTPUTS = PUBLIC / "reference_predictions.jsonl"
REFERENCES = PUBLIC / "references" / "audit_gold.jsonl"
MANIFEST = ROOT / "quackguard" / "plugin_manifest.json"

FAULT_MODULE = '''
import time


def mutate(inputs, outputs, references, context):
    inputs.append({"audit_unit_id": "injected"})
    return {
        "plugin_id": "fault.mutate",
        "plugin_version": "0.0.1",
        "input_schema_version": "1.3.1",
        "metrics": {},
        "diagnostics": [],
        "errors": [],
    }


def crash(inputs, outputs, references, context):
    raise RuntimeError("boom")


def slow(inputs, outputs, references, context):
    time.sleep(5)
    return {
        "plugin_id": "fault.slow",
        "plugin_version": "0.0.1",
        "input_schema_version": "1.3.1",
        "metrics": {},
        "diagnostics": [],
        "errors": [],
    }
'''


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def task(unit_id):
    return {
        "audit_unit_id": unit_id,
        "sample_id": "s-" + unit_id,
        "rule_id": "R005",
        "rule_version": "1.0.0",
        "review_date": "2026-06-15",
        "metadata": {},
        "materials": [{
            "material_id": "m1",
            "role": "package_photos",
            "relative_path": "m1.png",
            "media_type": "image/png",
            "page_index": 1,
        }],
    }


def prediction(unit_id, fact, bbox):
    return {
        "audit_unit_id": unit_id,
        "rule_id": "R005",
        "rule_version": "1.0.0",
        "execution_status": "completed",
        "label": "pass",
        "applicability": "applicable",
        "reason_code": "ok",
        "reason": "测试",
        "evidence": [{
            "material_id": "m1",
            "page_index": 1,
            "fact": fact,
            "bbox": bbox,
        }],
        "artifact_version": "v1",
        "trace_id": "t-" + unit_id,
        "latency_ms": 1,
        "cost_units": 0,
    }


def reference(unit_id):
    return {
        "audit_unit_id": unit_id,
        "rule_id": "R005",
        "rule_version": "1.0.0",
        "label": "pass",
        "applicability": "applicable",
        "evidence": [{
            "material_id": "m1",
            "page_index": 1,
            "visible_facts": [{
                "text": "型号：A",
                "readable": True,
                "bbox": [0.1, 0.1, 0.2, 0.2],
            }],
            "shape": {},
            "cut_coords": None,
        }],
    }


class D8PluginTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quackguard-d8-")
        self.dir = Path(self.temp.name)
        self.db = self.dir / "quackguard.db"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, env_extra=None):
        env = os.environ.copy()
        for key in (
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
            "PLUGIN_TIMEOUT_SECONDS",
            "QUACKGUARD_DB",
        ):
            env.pop(key, None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "run.py"), *map(str, args)],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

    def cli(self, *args, env_extra=None):
        result = self.run_cli(*args, env_extra=env_extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def evaluate(self, out_name="report.json", manifest=MANIFEST, env_extra=None):
        return self.cli(
            "--db", self.db,
            "evaluate",
            "--manifest", manifest,
            "--inputs", INPUTS,
            "--outputs", OUTPUTS,
            "--references", REFERENCES,
            "--out", self.dir / out_name,
            env_extra=env_extra,
        )

    def report(self, name="report.json"):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def test_registration_and_persistence(self):
        first = self.cli(
            "--db", self.db,
            "plugins",
            "--manifest", MANIFEST,
            "--out", self.dir / "plugins.json",
        )
        self.assertEqual(first["plugin_count"], 2)
        identifiers = {
            item["plugin_id"] + "@" + item["plugin_version"]
            for item in first["plugins"]
        }
        self.assertEqual(identifiers, {
            "quackguard.conclusion@0.1.0",
            "quackguard.evidence_content@0.2.0",
        })
        self.assertTrue(all(item["enabled"] == 1 for item in first["plugins"]))
        self.assertTrue((self.dir / "plugins.html").exists())

        second = self.cli(
            "--db", self.db,
            "plugins",
            "--out", self.dir / "plugins_again.json",
        )
        self.assertEqual(
            [item["plugin_id"] for item in second["plugins"]],
            [item["plugin_id"] for item in first["plugins"]],
        )

    def test_manifest_report_and_read_only_inputs(self):
        before = {path.name: sha256(path) for path in (INPUTS, OUTPUTS, REFERENCES)}
        summary = self.evaluate()
        self.assertEqual(summary["errors"], 0)
        self.assertTrue(summary["input_unchanged"])
        self.assertTrue((self.dir / "plugin_report.html").exists())

        report = self.report()
        self.assertEqual(report["plugin_count"], 2)
        self.assertEqual(report["invocations"], 2)
        self.assertEqual(report["input_hashes"]["before"], report["input_hashes"]["after"])
        after = {path.name: sha256(path) for path in (INPUTS, OUTPUTS, REFERENCES)}
        self.assertEqual(before, after)

        plugins = {item["plugin_id"]: item for item in report["plugins"]}
        conclusion = plugins["quackguard.conclusion"]
        self.assertEqual(conclusion["status"], "ok")
        self.assertEqual(conclusion["plugin_version"], "0.1.0")
        self.assertEqual(conclusion["input_schema_version"], "1.3.1")
        self.assertEqual(conclusion["metrics"]["quality"], 1.0)

        content = plugins["quackguard.evidence_content"]
        self.assertEqual(content["status"], "ok")
        self.assertEqual(content["plugin_version"], "0.2.0")
        self.assertEqual(content["metrics"]["reference_units"], 64)
        self.assertGreater(content["metrics"]["grounded_fact_rate"], 0.9)

    def test_disabled_plugin_is_not_invoked(self):
        self.cli(
            "--db", self.db,
            "plugins",
            "--manifest", MANIFEST,
            "--out", self.dir / "plugins.json",
        )
        state = self.cli(
            "--db", self.db,
            "plugin-state",
            "--plugin-id", "quackguard.evidence_content",
            "--plugin-version", "0.2.0",
            "--state", "disabled",
            "--out", self.dir / "state.json",
        )
        self.assertFalse(state["enabled"])

        self.evaluate("disabled.json")
        report = self.report("disabled.json")
        plugins = {item["plugin_id"]: item for item in report["plugins"]}
        self.assertEqual(plugins["quackguard.evidence_content"]["status"], "disabled")
        self.assertEqual(plugins["quackguard.evidence_content"]["invocations"], 0)
        self.assertEqual(plugins["quackguard.conclusion"]["status"], "ok")
        self.assertEqual(plugins["quackguard.conclusion"]["invocations"], 1)
        self.assertEqual(plugins["quackguard.conclusion"]["metrics"]["quality"], 1.0)

        listed = self.cli(
            "--db", self.db,
            "plugins",
            "--out", self.dir / "plugins_after.json",
        )
        rows = {item["plugin_id"]: item for item in listed["plugins"]}
        self.assertEqual(rows["quackguard.evidence_content"]["enabled"], 0)

    def test_crash_and_timeout_are_isolated(self):
        (self.dir / "d8_fault_plugins.py").write_text(FAULT_MODULE, encoding="utf-8")
        manifest = {
            "plugins": [
                {
                    "plugin_id": "fault.mutate",
                    "plugin_version": "0.0.1",
                    "input_schema_version": "1.3.1",
                    "entrypoint": "d8_fault_plugins:mutate",
                },
                {
                    "plugin_id": "quackguard.conclusion",
                    "plugin_version": "0.1.0",
                    "input_schema_version": "1.3.1",
                    "entrypoint": "quackguard.plugins:conclusion",
                },
                {
                    "plugin_id": "fault.crash",
                    "plugin_version": "0.0.1",
                    "input_schema_version": "1.3.1",
                    "entrypoint": "d8_fault_plugins:crash",
                },
                {
                    "plugin_id": "fault.slow",
                    "plugin_version": "0.0.1",
                    "input_schema_version": "1.3.1",
                    "entrypoint": "d8_fault_plugins:slow",
                },
            ]
        }
        manifest_path = self.dir / "fault_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        self.evaluate(
            "fault.json",
            manifest=manifest_path,
            env_extra={
                "PYTHONPATH": str(self.dir),
                "PLUGIN_TIMEOUT_SECONDS": "1",
            },
        )
        report = self.report("fault.json")
        plugins = {item["plugin_id"]: item for item in report["plugins"]}

        mutate = plugins["fault.mutate"]
        self.assertEqual(mutate["status"], "failed")
        self.assertEqual(mutate["errors"][0]["code"], "plugin_mutated_input")

        self.assertEqual(plugins["quackguard.conclusion"]["status"], "ok")
        self.assertEqual(plugins["quackguard.conclusion"]["metrics"]["quality"], 1.0)

        crash = plugins["fault.crash"]
        self.assertEqual(crash["status"], "failed")
        self.assertEqual(crash["errors"][0]["code"], "plugin_failed")

        slow = plugins["fault.slow"]
        self.assertEqual(slow["status"], "failed")
        self.assertEqual(slow["errors"][0]["code"], "plugin_timeout")

        page = (self.dir / "plugin_report.html").read_text(encoding="utf-8")
        self.assertIn("异常操作提示", page)
        self.assertIn("fault.crash", page)
        self.assertIn("plugin_timeout", page)

    def test_evidence_content_detects_unsupported_fact(self):
        inputs = [task("u-supported"), task("u-unsupported")]
        outputs = [
            prediction("u-supported", "型号：A", [0.1, 0.1, 0.2, 0.2]),
            prediction("u-unsupported", "编造事实", [0.1, 0.1, 0.2, 0.2]),
        ]
        references = [reference("u-supported"), reference("u-unsupported")]
        report = evidence(inputs, outputs, references, {})

        self.assertEqual(report["plugin_id"], "quackguard.evidence_content")
        self.assertEqual(report["metrics"]["evidence_units"], 2)
        self.assertEqual(report["metrics"]["grounded_units"], 1)
        self.assertEqual(report["metrics"]["unsupported_fact_count"], 1)
        self.assertEqual(report["metrics"]["grounded_fact_rate"], 0.5)
        diagnostics = {item["audit_unit_id"]: item for item in report["diagnostics"]}
        self.assertEqual(
            diagnostics["u-unsupported"]["unsupported_facts"], ["编造事实"]
        )
        self.assertEqual(diagnostics["u-supported"]["unsupported_facts"], [])

    def test_evidence_reports_reference_errors(self):
        missing = prediction("u-missing", "型号：A", [0.1, 0.1, 0.2, 0.2])
        missing["evidence"][0]["material_id"] = "m2"
        invalid = prediction("u-bbox", "型号：A", [-0.1, 0.1, 0.2, 0.2])
        report = evidence(
            [task("u-missing"), task("u-bbox")], [missing, invalid], [], {}
        )
        diagnostics = {item["audit_unit_id"]: item for item in report["diagnostics"]}
        self.assertIn(
            "evidence_material_page_not_in_input",
            diagnostics["u-missing"]["issues"],
        )
        self.assertIn("invalid_bbox", diagnostics["u-bbox"]["issues"])

    def test_compare_keeps_rule_versions_separate(self):
        rows = [
            json.loads(line)
            for line in OUTPUTS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows[0]["rule_version"] = "9.9.9"
        new_path = self.dir / "new_predictions.jsonl"
        new_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        out = self.dir / "compare.json"
        self.cli(
            "compare",
            "--old", OUTPUTS,
            "--new", new_path,
            "--input", INPUTS,
            "--references", REFERENCES,
            "--out", out,
        )
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(report["counts"]["version_changed"], 1)
        self.assertEqual(report["counts"]["unchanged"], report["all"])
        changed = report["version_changed"][0]["audit_unit_id"]
        self.assertNotIn(changed, report["fixed"])
        self.assertNotIn(changed, report["new_error"])
        self.assertNotIn(changed, report["unchanged"])


if __name__ == "__main__":
    unittest.main()
