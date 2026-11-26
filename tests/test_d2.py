import json
import tempfile
import unittest
from pathlib import Path

from quackguard.artifacts import build, checked_rules, load_policy
from quackguard.b001 import review
from quackguard.b001_facts import extract_facts, parse_facts
from quackguard.contracts import prediction_errors, read_rows
from quackguard.errors import AuditError
from quackguard.inference import review_task

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "O2_AI商品审核官_资料包"
PUBLIC = PACK / "03_公开开发集"
CONFIG = PACK / "04_最小对接样例" / "config.json"
RULE_FILES = [
    PACK / "01_规则包" / "基础规则版本.jsonl",
    PACK / "01_规则包" / "进阶规则60条.jsonl",
]


def value_field(value):
    return {"status": "value", "value": value}


def unreadable():
    return {"status": "unreadable", "value": None}


def missing():
    return {"status": "missing", "value": None}


def field(value):
    return value if isinstance(value, dict) else value_field(value)


def label(
    model="EDU-753",
    code="QLAB-753826",
    carrier="outer_package",
    attachment=None,
    voided=False,
    complete=True,
):
    return {
        "voided": voided,
        "inspection_complete": complete,
        "carrier": carrier,
        "attached_to_package": attachment,
        "fields": {"model": field(model), "material_code": field(code)},
    }


def certificate(
    code="QLAB-753826",
    models=None,
    start="2029-01-01",
    end="2029-07-12",
    voided=False,
    complete=True,
):
    return {
        "voided": voided,
        "inspection_complete": complete,
        "carrier": None,
        "attached_to_package": None,
        "fields": {
            "material_code": field(code),
            "covered_models": field(models or ["EDU-753"]),
            "valid_from": field(start),
            "valid_to": field(end),
        },
    }


def material_meta(material_id="p", role="package_photos"):
    return {
        "material_id": material_id,
        "role": role,
        "relative_path": material_id + ".png",
        "media_type": "image/png",
        "page_index": 1,
    }


def visual_material(meta, documents, status="clear"):
    return {
        "material_id": meta["material_id"],
        "page_index": meta["page_index"],
        "role": meta["role"],
        "status": status,
        "documents": documents,
    }


def make_task(review_date="2029-07-12", **metadata_changes):
    metadata = {
        "market": "Q",
        "category": "桌面灯",
        "power_mode": "可充电",
        "declared_model": "EDU-753",
        "package_longest_edge_mm": 150,
    }
    metadata.update(metadata_changes)
    return {
        "audit_unit_id": "unit",
        "sample_id": "sample",
        "rule_id": "B001",
        "rule_version": "3.0",
        "review_date": review_date,
        "metadata": metadata,
        "materials": [
            material_meta("p", "package_photos"),
            material_meta("c", "certificate_pages"),
        ],
    }


def make_facts(task, labels=None, certificates=None, package_status="clear"):
    labels = [label()] if labels is None else labels
    certificates = [certificate()] if certificates is None else certificates
    return [
        visual_material(task["materials"][0], labels, package_status),
        visual_material(task["materials"][1], certificates),
    ]


def payload(document):
    return json.dumps(
        {"status": "clear", "documents": [document]}, ensure_ascii=False
    )


class GoldVisualGateway:
    def __init__(self, tasks, gold_rows):
        self.materials = {
            material["material_id"]: material
            for task in tasks
            for material in task["materials"]
        }
        self.evidence = {
            item["material_id"]: item
            for gold in gold_rows
            for item in gold["evidence"]
        }

    def messages(self, prompt, task, input_path):
        return {"material_id": task["materials"][0]["material_id"]}

    def complete(self, messages):
        material_id = messages["material_id"]
        material = self.materials[material_id]
        visible = self.evidence[material_id]["visible_facts"]
        if material["role"] == "certificate_pages":
            fields = {
                "material_code": missing(),
                "covered_models": missing(),
                "valid_from": missing(),
                "valid_to": missing(),
            }
            document = certificate()
        else:
            fields = {"model": missing(), "material_code": missing()}
            attached = any(
                item.get("bbox") and item["bbox"][0] > 0.4 for item in visible
            )
            document = label(
                carrier="attached_tag" if attached else "outer_package",
                attachment=True if attached else None,
            )
        names = {
            "型号": "model",
            "材料编号": "material_code",
            "覆盖型号": "covered_models",
            "有效起始日": "valid_from",
            "有效截止日": "valid_to",
        }
        for item in visible:
            name, value = item["text"].split("：", 1)
            if not item["readable"]:
                fields[names[name]] = unreadable()
            elif names[name] == "covered_models":
                fields[names[name]] = value_field(value.split("、"))
            else:
                fields[names[name]] = value_field(value)
        document["fields"] = fields
        return payload(document)


class D2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quackguard-d2-")
        cls.dir = Path(cls.temp.name)
        cls.rules_path = cls.dir / "rules.jsonl"
        rules = [rule for path in RULE_FILES for rule in read_rows(path)]
        cls.rules_path.write_text(
            "".join(
                json.dumps(rule, ensure_ascii=False) + "\n" for rule in rules
            ),
            encoding="utf-8",
        )
        cls.artifacts = cls.dir / "artifacts"
        cls.build_result = build(cls.rules_path, CONFIG, cls.artifacts)
        cls.policy = load_policy(cls.artifacts)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_build_sop_thresholds_and_status(self):
        implementations = self.policy["implementations"]
        b001_keys = {"B001@3.0", "B001@3.1"}
        self.assertTrue(b001_keys.issubset(implementations))
        r_keys = {key for key in implementations if key.startswith("R")}
        self.assertEqual(len(r_keys), 60)
        thresholds = {
            key: value["sop"]["evidence_requirements"][
                "outer_package_threshold_mm"
            ]
            for key, value in implementations.items()
            if key.startswith("B001")
        }
        self.assertEqual(thresholds, {"B001@3.0": 100, "B001@3.1": 80})
        sop_fields = {
            "source_quotes",
            "applicability",
            "facts_to_check",
            "evidence_requirements",
            "normalization",
            "decision_order",
            "label_conditions",
            "clarifications",
        }
        policy_fields = {
            "atomic_capabilities",
            "reuse_decisions",
            "data_requirements",
            "labeling_plan",
            "model_and_tool_config",
            "evaluation_plugins",
            "quality_gates",
            "resource_budget",
            "promotion_conditions",
            "rollback_conditions",
        }
        r_sop_fields = {
            "source_quotes",
            "family",
            "family_title",
            "params",
            "applicability",
            "facts_to_check",
            "decision_order",
            "clarifications",
        }
        for key, implementation in implementations.items():
            self.assertEqual(
                set(implementation["artifact"]),
                {"version", "source_hash", "dependencies"},
            )
            if key.startswith("B001"):
                self.assertEqual(implementation["processor"], "b001")
                self.assertEqual(sop_fields, set(implementation["sop"]))
                self.assertEqual(policy_fields, set(implementation["build_policy"]))
            else:
                self.assertEqual(implementation["processor"], "r_rule")
                self.assertEqual(r_sop_fields, set(implementation["sop"]))
                self.assertTrue(implementation["sop"]["family"].startswith("F"))
        self.assertEqual(self.build_result["status"], "ready")
        self.assertEqual(
            self.build_result["implementation_status_counts"],
            {"implemented": 62},
        )

    def test_rule_errors_include_jsonl_line_numbers(self):
        rule = {"rule_id": "B001", "version": "3.0", "rule_text": "x"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.jsonl"
            path.write_text(
                json.dumps(rule) + "\n\n" + json.dumps(rule) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(AuditError) as caught:
                checked_rules(path)
            self.assertEqual(caught.exception.code, "duplicate_rule")
            self.assertIn("第3行", caught.exception.detail)
            self.assertIn("第1行", caught.exception.detail)

            invalid = dict(rule, version="")
            path.write_text(
                json.dumps(rule) + "\n" + json.dumps(invalid) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(AuditError) as caught:
                checked_rules(path)
            self.assertIn("第2行", caught.exception.detail)

            path.write_text(json.dumps(rule) + "\n{bad}\n", encoding="utf-8")
            with self.assertRaises(AuditError) as caught:
                checked_rules(path)
            self.assertIn("rules.jsonl:2", caught.exception.detail)

    def test_applicability_is_independent_from_visual_evidence(self):
        task = make_task(market=None, category="其他", power_mode=None)
        result = review(task, [], 100)
        self.assertEqual((result["label"], result["applicability"]),
                         ("pass", "not_applicable"))
        task = make_task(market=None)
        result = review(task, [], 100)
        self.assertEqual((result["label"], result["applicability"]),
                         ("ambiguous", "unknown"))
        self.assertIn("market", result["reason"])

    def test_model_and_material_code_normalization(self):
        task = make_task()
        facts = make_facts(task, labels=[label(model="ｅｄｕ － ７５３")])
        self.assertEqual(review(task, facts, 100)["label"], "pass")
        facts = make_facts(task, labels=[label(code="ＱＬＡＢ－７５３８２６")])
        self.assertEqual(review(task, facts, 100)["label"], "pass")
        for code in ("qlab-753826", "QLAB- 753826", "QLAB-75382"):
            with self.subTest(code=code):
                facts = make_facts(task, labels=[label(code=code)])
                self.assertEqual(review(task, facts, 100)["label"], "reject")
        for declared, visible in (("EDO-753", "ED0-753"),
                                  ("EDI-753", "ED1-753")):
            with self.subTest(declared=declared):
                task = make_task(declared_model=declared)
                facts = make_facts(
                    task,
                    labels=[label(model=visible)],
                    certificates=[certificate(models=[declared])],
                )
                self.assertEqual(review(task, facts, 100)["label"], "reject")

    def test_79_80_90_99_100_and_attachment_boundaries(self):
        for threshold in (80, 100):
            for edge in (79, 80, 90, 99, 100):
                with self.subTest(threshold=threshold, edge=edge):
                    task = make_task(package_longest_edge_mm=edge)
                    facts = make_facts(task, labels=[label(
                        carrier="attached_tag", attachment=True
                    )])
                    expected = "pass" if edge < threshold else "reject"
                    self.assertEqual(review(task, facts, threshold)["label"], expected)
        task = make_task(package_longest_edge_mm=79)
        for attachment, expected in ((None, "ambiguous"), (False, "reject")):
            facts = make_facts(task, labels=[label(
                carrier="attached_tag", attachment=attachment
            )])
            self.assertEqual(review(task, facts, 100)["label"], expected)
        task = make_task(package_longest_edge_mm=None)
        self.assertEqual(review(task, facts, 100)["applicability"], "applicable")

    def test_date_closed_interval_invalid_missing_and_unreadable(self):
        for review_date in ("2029-01-01", "2029-07-12"):
            task = make_task(review_date=review_date)
            self.assertEqual(review(task, make_facts(task), 100)["label"], "pass")
        task = make_task(review_date="２０２９－０７－１２")
        facts = make_facts(task, certificates=[certificate(end="２０２９－０７－１２")])
        self.assertEqual(review(task, facts, 100)["label"], "pass")
        for review_date in ("2028-12-31", "2029-07-13"):
            task = make_task(review_date=review_date)
            self.assertEqual(review(task, make_facts(task), 100)["label"], "reject")
        task = make_task()
        invalid_certificates = [
            certificate(start="2029-02-30"),
            certificate(end="2029-02-30"),
            certificate(start="2029-08-01"),
            certificate(end=missing()),
        ]
        for document in invalid_certificates:
            with self.subTest(document=document["fields"]):
                facts = make_facts(task, certificates=[document])
                self.assertEqual(review(task, facts, 100)["label"], "reject")
        facts = make_facts(task, certificates=[certificate(end=unreadable())])
        self.assertEqual(review(task, facts, 100)["label"], "ambiguous")
        task = make_task(review_date="2029-02-30")
        self.assertEqual(review(task, make_facts(task), 100)["label"], "ambiguous")

    def test_voided_documents_conflicts_and_cross_source_mismatch(self):
        task = make_task()
        cases = [
            ([label(voided=True)], [certificate()], "reject"),
            ([label()], [certificate(voided=True)], "reject"),
            ([label(voided=None)], [certificate()], "ambiguous"),
            ([label(), label(code="QLAB-999999", voided=True)],
             [certificate()], "pass"),
            ([label(), label(code="QLAB-753827")], [certificate()], "ambiguous"),
            ([label()], [certificate(), certificate(end="2029-08-01")], "ambiguous"),
            ([label()], [certificate(code="QLAB-753827")], "reject"),
            ([label(), label(voided=None)], [certificate()], "ambiguous"),
            ([label(), label(model=unreadable())], [certificate()], "ambiguous"),
            ([label(), label(model="OTHER")], [certificate()], "reject"),
            ([label(model="OTHER"), label(model=unreadable())],
             [certificate()], "reject"),
        ]
        for labels, certificates, expected in cases:
            with self.subTest(expected=expected, labels=labels, certificates=certificates):
                result = review(task, make_facts(task, labels, certificates), 100)
                self.assertEqual(result["label"], expected)
        conflict = review(
            task,
            make_facts(task, [label(), label(code="bad")], [certificate()]),
            100,
        )
        self.assertEqual(conflict["reason_code"], "material_conflict")
        mismatch = review(
            task,
            make_facts(task, [label()], [certificate(code="QLAB-753827")]),
            100,
        )
        self.assertEqual(mismatch["reason_code"], "rule_violation")

    def test_missing_requires_complete_clear_scope(self):
        task = make_task()
        cases = [
            ([label(model=missing())], "clear", "reject"),
            ([label(model=unreadable())], "clear", "ambiguous"),
            ([label(model=missing(), complete=False)], "clear", "ambiguous"),
            ([], "clear", "reject"),
            ([], "unreadable", "ambiguous"),
        ]
        for labels, status, expected in cases:
            with self.subTest(labels=labels, status=status):
                facts = make_facts(task, labels=labels, package_status=status)
                self.assertEqual(review(task, facts, 100)["label"], expected)

    def test_model_output_structure_tolerance(self):
        task = make_task()
        meta = task["materials"][1]
        document = certificate()
        document["fields"]["covered_models"] = [
            {"status": "value", "value": "EDU-753"}
        ]
        facts = parse_facts(payload(document), meta)
        self.assertEqual(
            facts["documents"][0]["fields"]["covered_models"],
            {"status": "value", "value": ["EDU-753"]},
        )
        document = certificate()
        document["fields"]["covered_models"] = None
        facts = parse_facts(payload(document), meta)
        self.assertEqual(
            facts["documents"][0]["fields"]["covered_models"],
            {"status": "missing", "value": None},
        )
        document = certificate()
        document.pop("carrier")
        document.pop("attached_to_package")
        facts = parse_facts(payload(document), meta)
        self.assertIsNone(facts["documents"][0]["carrier"])
        self.assertIsNone(facts["documents"][0]["attached_to_package"])
        self.assertEqual(
            facts["documents"][0]["fields"]["covered_models"]["value"],
            ["EDU-753"],
        )
        trailing = payload(certificate())
        parsed = parse_facts(trailing[:-1] + ",}", meta)
        self.assertEqual(parsed["status"], "clear")
        document = certificate()
        document["fields"]["valid_from"] = {"status": "missing"}
        facts = parse_facts(payload(document), meta)
        self.assertEqual(
            facts["documents"][0]["fields"]["valid_from"],
            {"status": "missing", "value": None},
        )

    def test_strict_parser_retry_and_provenance(self):
        task = make_task()
        meta = task["materials"][0]
        facts = parse_facts(payload(label()), meta)
        self.assertEqual(facts["material_id"], "p")
        self.assertEqual(facts["page_index"], 1)
        self.assertEqual(facts["role"], "package_photos")
        with self.assertRaises(ValueError):
            parse_facts('{"status":"clear","documents":[],"material_id":"fake"}', meta)
        invalid = label(model=unreadable())
        invalid["fields"]["model"]["value"] = "forged"
        with self.assertRaises(ValueError):
            parse_facts(payload(invalid), meta)

        class QueueGateway:
            def __init__(self):
                self.responses = ["not-json", payload(label())]
                self.visual_tasks = []

            def messages(self, prompt, visual_task, input_path):
                self.visual_tasks.append(visual_task)
                return []

            def complete(self, messages):
                return self.responses.pop(0)

        gateway = QueueGateway()
        single_task = dict(task, materials=[meta])
        extracted, traces = extract_facts(gateway, single_task, "unused", "trace")
        self.assertEqual(len(extracted), 1)
        self.assertEqual(len(traces), 2)
        self.assertNotIn("parsed_facts", traces[0])
        self.assertEqual(traces[1]["parsed_facts"], extracted[0])
        self.assertTrue(all(set(item) == {"materials"} for item in gateway.visual_tasks))

    def test_not_applicable_skips_gateway_and_reference_isolation(self):
        class FailingGateway:
            def messages(self, prompt, task, input_path):
                raise AssertionError("gateway must not be called")

        task = make_task(category="其他")
        output, traces = review_task(
            FailingGateway(), self.policy, task, "unused.jsonl"
        )
        self.assertEqual(output["execution_status"], "completed")
        self.assertEqual(output["applicability"], "not_applicable")
        self.assertEqual(traces, [])
        self.assertEqual(prediction_errors(output, task), [])
        for name in ("b001.py", "b001_facts.py", "b001_sop.py", "inference.py"):
            source = (ROOT / "quackguard" / name).read_text(encoding="utf-8")
            self.assertNotIn("audit_gold", source)
            self.assertNotIn("references", source)

    def test_public_20_cases_with_visual_test_double(self):
        input_path = PUBLIC / "inputs" / "audit_inputs.jsonl"
        tasks = [task for task in read_rows(input_path) if task["rule_id"] == "B001"]
        gold_rows = [
            row for row in read_rows(PUBLIC / "references" / "audit_gold.jsonl")
            if row["rule_id"] == "B001"
        ]
        self.assertEqual(len(tasks), 20)
        self.assertEqual(len(gold_rows), 20)
        gold_by_id = {row["audit_unit_id"]: row for row in gold_rows}
        gateway = GoldVisualGateway(tasks, gold_rows)
        outputs = []
        for task in tasks:
            with self.subTest(case=gold_by_id[task["audit_unit_id"]]["case_name"],
                              version=task["rule_version"]):
                output, traces = review_task(
                    gateway, self.policy, task, input_path
                )
                gold = gold_by_id[task["audit_unit_id"]]
                self.assertEqual(output["execution_status"], "completed")
                self.assertEqual(output["label"], gold["label"])
                self.assertEqual(output["applicability"], gold["applicability"])
                self.assertEqual(prediction_errors(output, task), [])
                if output["applicability"] == "applicable":
                    self.assertEqual(len(traces), len(task["materials"]))
                    self.assertTrue(all("parsed_facts" in trace for trace in traces))
                else:
                    self.assertEqual(traces, [])
                outputs.append(output)
        self.assertEqual({row["execution_status"] for row in outputs}, {"completed"})
        self.assertEqual(sum(row["label"] == "reject" for row in outputs), 9)
        self.assertEqual(sum(row["label"] == "ambiguous" for row in outputs), 2)


if __name__ == "__main__":
    unittest.main()
