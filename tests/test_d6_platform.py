"""D6 平台交付验收：依赖图、共享能力复用、语义缺口、版本追踪与选择性重建。

R043–R060 的判定逻辑已在 test_d3/test_d4 中逐条覆盖；本文件只验证平台层：
1. engine/dependency_graph.json 覆盖全部 62 个规则版本；
2. engine/shared_capabilities.json 记录共享模块复用计数与复用决策；
3. R059/R060 记录语义缺口，但可判断分支仍输出合法 completed；
4. --only 选择性重建只产出请求的 10 条规则；
5. versions.html 与数据库版本追踪一致；
6. 公开开发集 64 例全部 completed。
"""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from quackguard.artifacts import build, load_policy
from quackguard.contracts import prediction_errors, read_rows
from quackguard.inference import review_task
from quackguard.storage import Store
from test_d3 import PublicGateway
from test_d6 import (
    BASIC_RULES,
    CONFIG,
    PACK,
    RULE_FILE,
    Gateway,
    _b001_documents,
    document as base_document,
)


class D6PlatformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quackguard-d6-platform-")
        cls.dir = Path(cls.temp.name)
        rules = [
            rule
            for path in (BASIC_RULES, RULE_FILE)
            for rule in read_rows(path)
        ]
        cls.rules_path = cls.dir / "rules.jsonl"
        cls.rules_path.write_text(
            "".join(json.dumps(rule, ensure_ascii=False) + "\n" for rule in rules),
            encoding="utf-8",
        )
        cls.store = Store(cls.dir / "state.db")
        cls.artifacts = cls.dir / "artifacts"
        cls.result = build(cls.rules_path, CONFIG, cls.artifacts, store=cls.store)
        cls.policy = load_policy(cls.artifacts)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.temp.cleanup()

    def load(self, relative):
        return json.loads((self.artifacts / relative).read_text(encoding="utf-8"))

    def test_dependency_graph_covers_all_rules(self):
        graph = self.load("engine/dependency_graph.json")
        self.assertEqual(graph["artifact_version"], self.result["artifact_version"])
        self.assertEqual(len(graph["rules"]), 62)
        self.assertIn(
            "quackguard.rules_f08", graph["rules"]["R043@3.0"]["dependencies"]
        )
        self.assertIn(
            "quackguard.rules_f10", graph["rules"]["R060@3.0"]["dependencies"]
        )
        self.assertIn(
            "quackguard.b001_facts", graph["rules"]["B001@3.0"]["dependencies"]
        )
        self.assertIn(
            "quackguard.rule_common", graph["rules"]["B001@3.0"]["dependencies"]
        )

    def test_shared_capability_reuse_record(self):
        reuse = self.load("engine/shared_capabilities.json")
        self.assertEqual(reuse["artifact_version"], self.result["artifact_version"])
        self.assertEqual(
            reuse["modules"]["quackguard.rule_common"]["rule_count"], 62
        )
        self.assertEqual(reuse["modules"]["quackguard.rules_f09"]["rule_count"], 6)
        self.assertEqual(
            reuse["reuse_decisions"]["official_contracts"],
            ["reuse_without_modification"],
        )
        self.assertIn("model_gateway", reuse["reuse_decisions"])

    def test_semantic_gaps_recorded_for_r059_r060(self):
        gaps = {
            item["rule_id"]: item["gap"]
            for item in self.load("engine/semantic_gaps.json")
        }
        self.assertEqual(set(gaps), {"R059", "R060"})
        self.assertIn("同级要求冲突", gaps["R059"])
        self.assertIn("较小", gaps["R060"])

        matrix = {
            row["rule_id"]: row
            for row in self.load("engine/rule_test_matrix.json")
        }
        for rule_id in ("R043", "R048", "R059", "R060"):
            self.assertEqual(matrix[rule_id]["implementation_status"], "implemented")
            self.assertEqual(matrix[rule_id]["case_status"], "ready")
        for rule_id in ("R059", "R060"):
            self.assertTrue(matrix[rule_id]["semantic_gap"], rule_id)
        self.assertEqual(matrix["R043"]["semantic_gap"], "")

        status = (self.artifacts / "status.html").read_text(encoding="utf-8")
        self.assertIn("语义缺口记录", status)
        self.assertIn("R059", status)

    def review_rule(self, rule_id, metadata, page):
        task = {
            "audit_unit_id": "unit",
            "sample_id": "sample",
            "rule_id": rule_id,
            "rule_version": "3.0",
            "review_date": "2026-06-15",
            "metadata": {
                "material_context": {
                    "m1": {
                        "face_id": "front",
                        "source_type": "photo",
                        "is_standard_front_view": True,
                    }
                },
                **metadata,
            },
            "materials": [{
                "material_id": "m1",
                "role": "package_photos",
                "relative_path": "m1.png",
                "media_type": "image/png",
                "page_index": 1,
            }],
        }
        output, _traces = review_task(
            Gateway({"m1": [page]}), self.policy, task, "unused.jsonl"
        )
        return output, task

    def test_r059_r060_adjudicable_branches_still_execute(self):
        cases = [
            ("R059", {"declared_color_name": "红色"},
             {"shapes": {"hollow_circle": {"present": True}}}, "pass"),
            ("R059", {"declared_color_name": "红色"},
             {"shapes": {"hollow_circle": {"present": False}}}, "reject"),
            ("R059", {"declared_color_name": "蓝色练习盒"}, {}, "ambiguous"),
            ("R060", {}, base_document(), "pass"),
            ("R060", {}, {**base_document(), "carrier": "attached_tag"}, "ambiguous"),
        ]
        for rule_id, metadata, extra, expected in cases:
            with self.subTest(rule=rule_id, expected=expected):
                page = {
                    "voided": False,
                    "inspection_complete": True,
                    **extra,
                }
                output, task = self.review_rule(rule_id, metadata, page)
                self.assertEqual(output["execution_status"], "completed")
                self.assertEqual(output["label"], expected, output.get("reason"))
                self.assertEqual(prediction_errors(output, task), [])

    def test_selective_rebuild_only_requested_rules(self):
        only = ["R%03d" % number for number in range(43, 53)]
        out = self.dir / "subset"
        result = build(self.rules_path, CONFIG, out, only=only)
        self.assertEqual(result["only_missing"], [])
        self.assertEqual(result["rule_versions"], 10)

        manifest = json.loads(
            (out / "artifact_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest["entries"]), 10)

        graph = json.loads(
            (out / "engine" / "dependency_graph.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(graph["rules"]), {rule_id + "@3.0" for rule_id in only}
        )

        matrix = json.loads(
            (out / "engine" / "rule_test_matrix.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(matrix), 10)

        with (out / "60条建设台账.csv").open(encoding="utf-8", newline="") as handle:
            ledger = list(csv.DictReader(handle))
        self.assertEqual([row["rule_id"] for row in ledger], only)

    def test_version_tracking_view_lists_builds_and_rule_versions(self):
        report = self.load("build_report.json")
        build_id = report["build_id"]
        self.assertTrue(build_id)

        versions = self.store.list_versions(build_id)
        self.assertEqual(len(versions), 62)
        self.assertTrue(
            all(
                row["artifact_version"] == self.result["artifact_version"]
                for row in versions
            )
        )
        builds = self.store.list_builds()
        self.assertEqual(builds[0]["build_id"], build_id)
        self.assertEqual(builds[0]["rule_count"], 62)
        self.assertEqual(builds[0]["active_count"], 0)

        page = (self.artifacts / report["versions_page"]).read_text(encoding="utf-8")
        self.assertIn("构建历史", page)
        self.assertIn("规则版本追踪", page)
        self.assertIn(self.result["artifact_version"], page)
        status = (self.artifacts / "status.html").read_text(encoding="utf-8")
        self.assertIn("versions.html", status)
        self.assertIn("共享能力复用记录", status)

    def test_public_sixty_four_cases_completed(self):
        input_path = PACK / "03_公开开发集" / "inputs" / "audit_inputs.jsonl"
        tasks = read_rows(input_path)
        golds = {
            row["audit_unit_id"]: row
            for row in read_rows(
                PACK / "03_公开开发集" / "references" / "audit_gold.jsonl"
            )
        }
        self.assertEqual(len(tasks), 64)

        counts = {"B001": 0, "R": 0}
        for task in tasks:
            gold = golds[task["audit_unit_id"]]
            if task["rule_id"] == "B001":
                gateway = Gateway(_b001_documents(task, gold))
                counts["B001"] += 1
            else:
                gateway = PublicGateway(task, gold)
                counts["R"] += 1
            output, _traces = review_task(gateway, self.policy, task, input_path)
            self.assertEqual(
                output["execution_status"], "completed", task["audit_unit_id"]
            )
            self.assertEqual(
                prediction_errors(
                    output, task, self.policy["artifact_version"]
                ),
                [],
                task["audit_unit_id"],
            )
        self.assertEqual(counts, {"B001": 20, "R": 44})


if __name__ == "__main__":
    unittest.main()
