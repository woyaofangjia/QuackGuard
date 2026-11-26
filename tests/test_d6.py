"""补齐展示与平台缺口：

1. 事实bbox进入审核证据，缺失/非法bbox不改变判定；
2. annotate 复用同一批事实与审核结论，label 来自 review_task；
3. build 坏规则行级隔离、--resume 跳过未变版本、--only 规则过滤；
4. status.html 状态页与 review.html 审核结果页（含证据区域）。
"""
import json
import tempfile
import unicodedata
import unittest
from pathlib import Path

from quackguard.annotation import annotate_target
from quackguard.artifacts import build, checked_rules, load_policy
from quackguard.contracts import read_rows
from quackguard.errors import AuditError
from quackguard.inference import review_task
from quackguard.normalization import annotation_values_equal
from quackguard.report import write_review_page
from quackguard.rule_facts import field_unreadable as fu, field_value as fv

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "O2_AI商品审核官_资料包"
CONFIG = PACK / "04_最小对接样例" / "config.json"
RULE_FILE = PACK / "01_规则包" / "进阶规则60条.jsonl"
BASIC_RULES = PACK / "01_规则包" / "基础规则版本.jsonl"
SAMPLE = PACK / "03_公开开发集" / "标注样例"

TASK = {
    "audit_unit_id": "u1",
    "sample_id": "s1",
    "rule_id": "R001",
    "rule_version": "3.0",
    "review_date": "2029-07-12",
    "metadata": {},
    "materials": [{
        "material_id": "m1",
        "role": "package_photos",
        "relative_path": "m1.png",
        "media_type": "image/png",
        "page_index": 1,
    }],
}

BBOX = [0.1, 0.2, 0.5, 0.4]


def rule(rule_id="R001"):
    return next(row for row in read_rows(RULE_FILE) if row["rule_id"] == rule_id)


def document(bbox=None, model="EDU-753", code="QLAB-753826", field_bbox=None):
    model_field = fv(model) if model is not None else fu()
    if field_bbox is not None:
        model_field["bbox"] = field_bbox
    data = {
        "voided": False,
        "inspection_complete": True,
        "carrier": "outer_package",
        "carrier_face": "front",
        "four_edges_visible": True,
        "fields": {
            "model": model_field,
            "material_code": fv(code),
        },
    }
    if bbox is not None:
        data["bbox"] = bbox
    return data


class Gateway:
    """按material_id返回事实信封；单文档场景复用同一文档。"""

    def __init__(self, documents):
        self.documents = documents

    def messages(self, prompt, visual_task, input_path):
        return {"material_id": visual_task["materials"][0]["material_id"]}

    def complete(self, messages):
        documents = self.documents
        if isinstance(documents, dict):
            documents = documents[messages["material_id"]]
        return json.dumps(
            {"status": "clear", "documents": documents},
            ensure_ascii=False,
        )


def _iou(left, right):
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (
        (left[2] - left[0]) * (left[3] - left[1])
        + (right[2] - right[0]) * (right[3] - right[1])
        - intersection
    )
    return intersection / union if union else 0.0


def _fullwidth(value):
    if isinstance(value, str):
        converted = "".join(
            chr(ord(char) + 0xFEE0) if 33 <= ord(char) <= 126 else char
            for char in value
        )
        assert unicodedata.normalize("NFKC", converted) == value
        return converted
    if isinstance(value, list):
        return [_fullwidth(item) for item in value]
    return value


def _b001_documents(task, gold):
    names = {
        "型号": "model",
        "材料编号": "material_code",
        "覆盖型号": "covered_models",
        "有效起始日": "valid_from",
        "有效截止日": "valid_to",
    }
    visible = {
        item["material_id"]: item.get("visible_facts", [])
        for item in gold["evidence"]
    }
    documents = {}
    for material in task["materials"]:
        fields = {}
        for fact in visible.get(material["material_id"], []):
            if "：" not in fact["text"]:
                continue
            name, value = fact["text"].split("：", 1)
            key = names.get(name)
            if key is None:
                continue
            if key == "covered_models":
                item = {"status": "value", "value": [value]}
            elif fact["readable"]:
                item = {"status": "value", "value": value}
            else:
                item = {"status": "unreadable", "value": None}
            if fact.get("bbox"):
                item["bbox"] = fact["bbox"]
            fields[key] = item
        required = (
            ("material_code", "covered_models", "valid_from", "valid_to")
            if material["role"] == "certificate_pages"
            else ("model", "material_code")
        )
        for key in required:
            fields.setdefault(key, {"status": "unreadable", "value": None})
        documents[material["material_id"]] = [{
            "voided": False,
            "inspection_complete": True,
            "carrier": (
                None if material["role"] == "certificate_pages"
                else "outer_package"
            ),
            "attached_to_package": None,
            "fields": fields,
        }]
    return documents


class D6Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.rules = self.dir / "rules.jsonl"
        self.rules.write_text(
            json.dumps(rule(), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.artifacts = self.dir / "artifacts"
        self.result = build(self.rules, CONFIG, self.artifacts)
        self.policy = load_policy(self.artifacts)

    def tearDown(self):
        self.tmp.cleanup()

    def review(self, documents):
        return review_task(Gateway(documents), self.policy, TASK, self.rules)

    def target(self, text, annotation_id="a1"):
        return {
            "annotation_id": annotation_id,
            "audit_unit_id": "u1",
            "material_id": "m1",
            "page_index": 1,
            "target": text,
        }

    def test_bbox_reaches_evidence_without_changing_decision(self):
        with_box = self.review([document(BBOX)])[0]
        without = self.review([document()])[0]
        reversed_box = self.review([document([0.5, 0.4, 0.1, 0.2])])[0]

        self.assertEqual(with_box["execution_status"], "completed")
        self.assertEqual(with_box["evidence"][0]["bbox"], BBOX)
        self.assertEqual(without["label"], with_box["label"])
        self.assertEqual(without["evidence"][0].get("bbox"), None)
        self.assertEqual(reversed_box["label"], with_box["label"])
        self.assertEqual(reversed_box["evidence"][0].get("bbox"), None)

    def test_annotate_requires_field_bbox_not_document_region(self):
        output, traces, error = annotate_target(
            Gateway([document(BBOX)]), self.policy, TASK,
            self.target("提取指定材料中的型号完整文本行，包含字段名。"),
            self.rules,
        )
        self.assertIsNone(output)
        self.assertEqual(error.code, "annotation_bbox_missing")
        self.assertTrue(traces)

    def test_field_bbox_used_for_evidence_and_annotation(self):
        line_box = [0.11, 0.18, 0.28, 0.23]
        review = self.review([document(field_bbox=line_box)])[0]
        self.assertEqual(review["evidence"][0]["bbox"], line_box)

        output, _, error = annotate_target(
            Gateway([document(field_bbox=line_box)]), self.policy, TASK,
            self.target("提取指定材料中的型号完整文本行，包含字段名。"),
            self.rules,
        )
        self.assertIsNone(error)
        self.assertEqual(output["bbox"], line_box)

    def test_annotate_invalid_value_and_unreadable_keep_review_label(self):
        invalid_doc = document(BBOX, code="QLAB-75382")
        invalid_doc["fields"]["material_code"]["bbox"] = BBOX
        invalid, _, error = annotate_target(
            Gateway([invalid_doc]), self.policy, TASK,
            self.target("提取指定材料中的材料编号完整文本行，包含字段名。"),
            self.rules,
        )
        self.assertIsNone(error)
        self.assertEqual(invalid["status"], "invalid_value")
        self.assertEqual(invalid["value"], "材料编号：QLAB-75382")

        unreadable_doc = document(BBOX, model=None)
        unreadable_doc["fields"]["model"]["bbox"] = BBOX
        unreadable, _, error = annotate_target(
            Gateway([unreadable_doc]), self.policy, TASK,
            self.target("提取指定材料中的型号完整文本行，包含字段名。"),
            self.rules,
        )
        self.assertIsNone(error)
        self.assertEqual(unreadable["status"], "unreadable")
        self.assertIsNone(unreadable["value"])
        self.assertEqual(unreadable["label"], invalid["label"])

    def test_build_isolates_bad_rules_and_resumes(self):
        mixed = self.dir / "mixed.jsonl"
        mixed.write_text(
            "\n".join([
                json.dumps(rule(), ensure_ascii=False),
                "{broken json",
                json.dumps({"rule_id": "R002", "version": "3.0"}, ensure_ascii=False),
                json.dumps(rule(), ensure_ascii=False),
            ]) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(AuditError):
            checked_rules(mixed)

        out = self.dir / "mixed"
        result = build(mixed, CONFIG, out)
        self.assertEqual(result["status"], "ready_with_rule_failures")
        self.assertEqual(result["rule_versions"], 1)
        self.assertEqual(result["rule_failures"], 3)
        self.assertTrue((out / "status.html").is_file())
        report = json.loads((out / "build_report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["rule_failures"]), 3)
        self.assertIn("R001", (out / "status.html").read_text(encoding="utf-8"))

        resumed = build(mixed, CONFIG, out, resume=True)
        self.assertEqual(resumed["status"], "resumed")
        self.assertEqual(resumed["skipped_rule_versions"], 1)

        only_out = self.dir / "only"
        filtered = build(mixed, CONFIG, only_out, only=["R001"])
        self.assertEqual(filtered["rule_versions"], 1)

    def test_public_annotations_reproduce_gold_from_shared_facts(self):
        artifacts = self.dir / "b001"
        build(BASIC_RULES, CONFIG, artifacts)
        policy = load_policy(artifacts)
        tasks = read_rows(SAMPLE / "inputs" / "tasks.jsonl")
        targets = read_rows(SAMPLE / "inputs" / "targets.jsonl")
        gold_rows = read_rows(SAMPLE / "references" / "annotation_gold.jsonl")
        gold = {row["annotation_id"]: row for row in gold_rows}
        audit = {
            row["audit_unit_id"]: row
            for row in read_rows(
                PACK / "03_公开开发集" / "references" / "audit_gold.jsonl"
            )
        }
        by_unit = {task["audit_unit_id"]: task for task in tasks}
        input_path = SAMPLE / "inputs" / "tasks.jsonl"

        for target in targets:
            task = by_unit[target["audit_unit_id"]]
            gateway = Gateway(_b001_documents(task, audit[task["audit_unit_id"]]))
            review = review_task(gateway, policy, task, input_path)
            output, _, error = annotate_target(
                gateway, policy, task, target, input_path, review=review
            )
            self.assertIsNone(error)
            expected = gold[target["annotation_id"]]
            self.assertEqual(output["value"], expected["expected_value"])
            self.assertEqual(output["status"], expected["expected_status"])
            self.assertEqual(output["label"], expected["expected_label"])
            self.assertGreaterEqual(
                _iou(output["bbox"], expected["expected_bbox"]), 0.5
            )

    def test_public_annotations_nfkc_equivalent(self):
        artifacts = self.dir / "b001_nfkc"
        build(BASIC_RULES, CONFIG, artifacts)
        policy = load_policy(artifacts)
        tasks = read_rows(SAMPLE / "inputs" / "tasks.jsonl")
        targets = read_rows(SAMPLE / "inputs" / "targets.jsonl")
        gold = {
            row["annotation_id"]: row
            for row in read_rows(SAMPLE / "references" / "annotation_gold.jsonl")
        }
        audit = {
            row["audit_unit_id"]: row
            for row in read_rows(
                PACK / "03_公开开发集" / "references" / "audit_gold.jsonl"
            )
        }
        by_unit = {task["audit_unit_id"]: task for task in tasks}
        input_path = SAMPLE / "inputs" / "tasks.jsonl"

        for target in targets:
            task = by_unit[target["audit_unit_id"]]
            documents = _b001_documents(task, audit[task["audit_unit_id"]])
            for pages in documents.values():
                for page in pages:
                    for item in page.get("fields", {}).values():
                        if item.get("status") == "value":
                            item["value"] = _fullwidth(item["value"])
            gateway = Gateway(documents)
            review = review_task(gateway, policy, task, input_path)
            output, _, error = annotate_target(
                gateway, policy, task, target, input_path, review=review
            )
            self.assertIsNone(error)
            expected = gold[target["annotation_id"]]
            self.assertEqual(output["status"], expected["expected_status"])
            self.assertTrue(
                annotation_values_equal(
                    output["value"], expected["expected_value"]
                ),
                (output["value"], expected["expected_value"]),
            )
            self.assertEqual(output["label"], expected["expected_label"])
            self.assertGreaterEqual(
                _iou(output["bbox"], expected["expected_bbox"]), 0.5
            )

    def test_review_page_contains_rule_text_and_evidence_region(self):
        (self.dir / "m1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        output = self.review([document(BBOX)])[0]
        page = self.dir / "review.html"
        write_review_page(page, self.policy, [TASK], [output], self.rules)
        text = page.read_text(encoding="utf-8")
        self.assertIn("<rect", text)
        snippet = self.policy["rules"]["R001@3.0"]["rule_text"][:8]
        self.assertIn(snippet, text)


if __name__ == "__main__":
    unittest.main()
