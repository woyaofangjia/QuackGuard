"""D4：R001–R018 逐条校准验收。

在 D3 的统一框架上，对 F01（实拍证据完整性）、F02（商品身份与型号）、
F03（字段完整性与格式）共 18 条规则逐条对齐规则原文：

1. 每条规则至少覆盖 pass/reject，并覆盖 ambiguous 与 unknown；
2. 已证实的违反优先于不可确认（总则决策顺序）；
3. 未知信息（面信息、来源、字段缺失范围）不得直接判 reject；
4. 构建矩阵 R001–R018 全部 implemented/ready，SOP 原文引用与规则文件一致。
"""
import json
import tempfile
import unittest
from pathlib import Path

from quackguard.artifacts import build, load_policy
from quackguard.contracts import prediction_errors, read_rows
from quackguard.inference import review_task
from quackguard.rule_facts import field_unreadable as fu, field_value as fv

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "O2_AI商品审核官_资料包"
CONFIG = PACK / "04_最小对接样例" / "config.json"
RULE_FILES = [
    PACK / "01_规则包" / "基础规则版本.jsonl",
    PACK / "01_规则包" / "进阶规则60条.jsonl",
]
JUDGED_RULES = ["R%03d" % number for number in range(1, 19)]


def M(mid, role="package_photos", page=1):
    return {
        "material_id": mid,
        "role": role,
        "relative_path": mid + ".png",
        "media_type": "image/png",
        "page_index": page,
    }


def doc(fields=None, texts=None, shapes=None, pages=None, **kw):
    document = {"inspection_complete": True, "voided": False}
    if fields:
        document["fields"] = fields
    if texts:
        document["texts"] = texts
    if shapes:
        document["shapes"] = shapes
    if pages:
        document["pages"] = pages
    document.update(kw)
    return document


def page(number, status="value"):
    return {"printed_page_number": number, "status": status}


class DictGateway:
    def __init__(self, documents):
        self.documents = documents

    def messages(self, prompt, visual_task, input_path):
        material = visual_task["materials"][0]
        return {
            "material_id": material["material_id"],
            "page_index": material["page_index"],
        }

    def complete(self, messages):
        raw = self.documents.get(messages["material_id"])
        documents = raw if isinstance(raw, list) else ([raw] if raw else [])
        return json.dumps(
            {"status": "clear", "documents": documents}, ensure_ascii=False
        )


# 每条用例：(规则, 材料, 文档, 面信息, 来源, 元数据, 期望标签, 期望适用性, 说明)
CASES = [
    # R001 主视图四边界
    ("R001", [M("m1")], {"m1": doc(four_edges_visible=True)}, None, None, None, "pass", "applicable", "四边界可见"),
    ("R001", [M("m1")], {"m1": doc(four_edges_visible=False)}, None, None, None, "reject", "applicable", "明确裁去边界"),
    ("R001", [M("m1")], {"m1": doc()}, {"m1": "back"}, None, None, "reject", "applicable", "已知面信息但未提交主面"),
    ("R001", [M("m1")], {"m1": doc()}, {"m1": None}, None, None, "ambiguous", "applicable", "面信息未知"),
    ("R001", [M("m1")], {"m1": doc()}, None, None, None, "ambiguous", "applicable", "边界被遮挡不可确认"),

    # R002 检查面覆盖
    ("R002", [M("m1"), M("m2")], {}, {"m1": "front", "m2": "back"}, None, {"required_faces": ["front", "back"]}, "pass", "applicable", "检查面齐全"),
    ("R002", [M("m1")], {}, {"m1": "front"}, None, {"required_faces": ["front", "back"]}, "reject", "applicable", "明确缺少背面"),
    ("R002", [M("m1"), M("m2")], {}, {"m1": "front", "m2": None}, None, {"required_faces": ["front", "back"]}, "ambiguous", "applicable", "存在面信息未知材料"),
    ("R002", [M("m1")], {}, None, None, None, "ambiguous", "unknown", "required_faces缺失"),

    # R003 证明页码
    ("R003", [M("c", "certificate_pages")], {"c": doc(pages=[page(1), page(2)])}, None, None, {"certificate_total_pages": 2}, "pass", "applicable", "页码齐全"),
    ("R003", [M("c", "certificate_pages")], {"c": doc(pages=[page(1)])}, None, None, {"certificate_total_pages": 2}, "reject", "applicable", "明确缺页"),
    ("R003", [M("c", "certificate_pages")], {"c": doc(pages=[page(1), page(3)])}, None, None, {"certificate_total_pages": 2}, "reject", "applicable", "页码超范围"),
    ("R003", [M("c", "certificate_pages")], {"c": doc(pages=[page(1), page(None, "unreadable")])}, None, None, {"certificate_total_pages": 2}, "ambiguous", "applicable", "不可读页码导致缺页不明"),
    ("R003", [M("c", "certificate_pages")], {"c": doc(pages=[page(1)])}, None, None, None, "ambiguous", "unknown", "总页数缺失"),

    # R004 附签载体取证
    ("R004", [M("m1")], {}, None, None, None, "pass", "applicable", "未使用附签"),
    ("R004", [M("t1", "tag_photos")], {"t1": doc(carrier="attached_tag", attached_to_package=True)}, None, None, None, "pass", "applicable", "连接关系可见"),
    ("R004", [M("t1", "tag_photos")], {"t1": doc(carrier="attached_tag", attached_to_package=False)}, None, None, None, "reject", "applicable", "附签脱离载体"),
    ("R004", [M("t1", "tag_photos")], {"t1": doc(carrier="attached_tag")}, None, None, None, "ambiguous", "applicable", "连接关系不可确认"),

    # R005 遮挡补图
    ("R005", [M("a")], {"a": doc(fields={"model": fv("EDU-753")})}, None, None, {"target_field": "型号"}, "pass", "applicable", "未遮挡"),
    ("R005", [M("a"), M("b")], {"a": doc(fields={"model": fu(), "material_code": fv("QLAB-1")}), "b": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")})}, None, None, {"target_field": "型号"}, "pass", "applicable", "同编号补图"),
    ("R005", [M("a"), M("b")], {"a": doc(fields={"model": fu(), "material_code": fv("QLAB-1")}), "b": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-2")})}, None, None, {"target_field": "型号"}, "reject", "applicable", "补图编号不一致"),
    ("R005", [M("a")], {"a": doc(fields={"model": fu(), "material_code": fv("QLAB-1")})}, None, None, {"target_field": "型号"}, "reject", "applicable", "遮挡且无补图"),
    ("R005", [M("a"), M("b")], {"a": doc(fields={"model": fu(), "material_code": fu()}), "b": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")})}, None, None, {"target_field": "型号"}, "ambiguous", "applicable", "关联键不清"),
    ("R005", [M("a")], {"a": doc()}, None, None, None, "ambiguous", "unknown", "target_field缺失"),

    # R006 实拍来源
    ("R006", [M("m1")], {}, None, {"m1": "photo"}, None, "pass", "applicable", "实拍图"),
    ("R006", [M("m1")], {}, None, {"m1": "render"}, None, "reject", "applicable", "仅渲染图"),
    ("R006", [M("m1")], {}, None, {"m1": "unknown"}, None, "ambiguous", "applicable", "来源未知"),
    ("R006", [M("m1"), M("m2")], {}, None, {"m1": "photo", "m2": "design"}, None, "pass", "applicable", "同时存在实拍与设计稿"),
    ("R006", [M("m1"), M("m2")], {}, None, {"m1": "photo", "m2": None}, None, "ambiguous", "applicable", "部分来源未知"),

    # R007 包装型号一致性
    ("R007", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753")})}, None, None, {"declared_model": "EDU-753"}, "pass", "applicable", "型号一致"),
    ("R007", [M("m1")], {"m1": doc(fields={"model": fv("EDU-268")})}, None, None, {"declared_model": "EDU-753"}, "reject", "applicable", "型号不一致"),
    ("R007", [M("m1")], {"m1": doc(fields={"model": fu()})}, None, None, {"declared_model": "EDU-753"}, "ambiguous", "applicable", "型号读不清"),
    ("R007", [M("m1")], {"m1": doc()}, None, None, {"declared_model": "EDU-753"}, "reject", "applicable", "完整区域明确缺失"),
    ("R007", [M("m1", "product_photos")], {"m1": doc()}, None, None, {"declared_model": "EDU-753"}, "ambiguous", "applicable", "无包装标签视图"),
    ("R007", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753")})}, None, None, None, "ambiguous", "applicable", "申报型号缺失"),

    # R008 套装组件型号集合
    ("R008", [M("c", "certificate_pages")], {"c": [doc(fields={"component_models": fv("A")}), doc(fields={"component_models": fv("B")})]}, None, None, {"declared_component_models": ["A", "B"]}, "pass", "applicable", "集合相等"),
    ("R008", [M("c", "certificate_pages")], {"c": [doc(fields={"component_models": fv("A")}), doc(fields={"component_models": fv("C")})]}, None, None, {"declared_component_models": ["A", "B"]}, "reject", "applicable", "多出型号且缺少B"),
    ("R008", [M("c", "certificate_pages")], {"c": [doc(fields={"component_models": fv("A")}), doc(fields={"component_models": fv("B")}), doc(fields={"component_models": fv("C")}), doc(fields={"component_models": fu()})]}, None, None, {"declared_component_models": ["A", "B"]}, "reject", "applicable", "多出型号已证实优先"),
    ("R008", [M("c", "certificate_pages")], {"c": [doc(fields={"component_models": fv("A")})]}, None, None, {"declared_component_models": ["A", "B"]}, "reject", "applicable", "完整视图缺少型号"),
    ("R008", [M("c", "certificate_pages")], {"c": [doc(fields={"component_models": fv("A")}), doc(fields={"component_models": fu()})]}, None, None, {"declared_component_models": ["A", "B"]}, "ambiguous", "applicable", "列表不完整可见"),
    ("R008", [M("c", "certificate_pages")], {"c": [doc(fields={"component_models": fv("A")})]}, None, None, None, "ambiguous", "unknown", "申报组件缺失"),

    # R009 多件序列号
    ("R009", [M("p", "product_photos")], {"p": doc(fields={"serial_number": fv("S1")})}, None, None, None, "ambiguous", "applicable", "件数未知"),
    ("R009", [M("p", "product_photos")], {"p": doc(fields={"serial_number": fv("S1")})}, None, None, {"declared_quantity": 1}, "pass", "applicable", "单件不触发"),
    ("R009", [M("p", "product_photos"), M("q", "product_photos")], {"p": doc(fields={"serial_number": fv("S1")}), "q": doc(fields={"serial_number": fv("S2")})}, None, None, {"declared_quantity": 2}, "pass", "applicable", "序列号互不相同"),
    ("R009", [M("p", "product_photos"), M("q", "product_photos")], {"p": doc(fields={"serial_number": fv("S1")}), "q": doc(fields={"serial_number": fv("S1")})}, None, None, {"declared_quantity": 2}, "reject", "applicable", "序列号重复"),
    ("R009", [M("p", "product_photos")], {"p": doc(fields={"serial_number": fv("S1")})}, None, None, {"declared_quantity": 2}, "reject", "applicable", "缺少可读序列号"),
    ("R009", [M("p", "product_photos"), M("q", "product_photos")], {"p": doc(fields={"serial_number": fv("S1")}), "q": doc(fields={"serial_number": fu()})}, None, None, {"declared_quantity": 2}, "ambiguous", "applicable", "部分序列号不可读"),

    # R010 主型号与兼容型号
    ("R010", [M("m1")], {"m1": doc(fields={"main_model": fv("EDU-753"), "compatible_models": fv(["EDU-268"])})}, None, None, {"declared_model": "EDU-753"}, "pass", "applicable", "主型号匹配"),
    ("R010", [M("m1")], {"m1": doc(fields={"main_model": fv("EDU-268"), "compatible_models": fv(["EDU-753"])})}, None, None, {"declared_model": "EDU-753"}, "reject", "applicable", "兼容型号不能替代"),
    ("R010", [M("m1")], {"m1": doc(fields={"compatible_models": fv(["EDU-753"])})}, None, None, {"declared_model": "EDU-753"}, "reject", "applicable", "缺失主型号字段"),
    ("R010", [M("m1")], {"m1": doc(fields={"main_model": fu()})}, None, None, {"declared_model": "EDU-753"}, "ambiguous", "applicable", "主型号读不清"),
    ("R010", [M("m1", "product_photos")], {"m1": doc()}, None, None, {"declared_model": "EDU-753"}, "ambiguous", "applicable", "无包装标签视图"),

    # R011 新旧型号映射
    ("R011", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753")})}, None, None, {"declared_model": "EDU-753"}, "pass", "applicable", "未使用旧型号"),
    ("R011", [M("m1"), M("c", "certificate_pages")], {"m1": doc(fields={"old_model": fv("OLD-1")}), "c": doc(fields={"old_model_relation": fv("OLD-1替代为NEW-1")})}, None, None, {"declared_model": "NEW-1"}, "pass", "applicable", "替代关系明确"),
    ("R011", [M("m1"), M("c", "certificate_pages")], {"m1": doc(fields={"old_model": fv("OLD-1")}), "c": doc(fields={"old_model_relation": fv("OLD-1 NEW-1")})}, None, None, {"declared_model": "NEW-1"}, "reject", "applicable", "只并列无替代关系"),
    ("R011", [M("m1"), M("c", "certificate_pages")], {"m1": doc(fields={"old_model": fv("OLD-1")}), "c": doc(fields={"old_model_relation": fu()})}, None, None, {"declared_model": "NEW-1"}, "ambiguous", "applicable", "替代关系读不清"),
    ("R011", [M("m1"), M("c", "certificate_pages")], {"m1": doc(fields={"old_model": fv("OLD-1")}), "c": doc(fields={"old_model_relation": fv("OLD-1替代为NEW-1")})}, None, None, None, "ambiguous", "applicable", "申报型号缺失"),

    # R012 0/1 字符身份
    ("R012", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753")})}, None, None, {"declared_model": "EDU-753"}, "pass", "applicable", "字符一致"),
    ("R012", [M("m1")], {"m1": doc(fields={"model": fv("ED0-753")})}, None, None, {"declared_model": "EDO-753"}, "reject", "applicable", "0与O不互换"),
    ("R012", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753")})}, None, None, {"declared_model": "EDU-268"}, "reject", "applicable", "型号不一致"),
    ("R012", [M("m1")], {"m1": doc(fields={"model": fu()})}, None, None, {"declared_model": "ED0-753"}, "ambiguous", "applicable", "含0/1且不可读"),

    # R013 材料编号格式
    ("R013", [M("m1")], {"m1": doc(fields={"material_code": fv("QLAB-123456")})}, None, None, None, "pass", "applicable", "格式正确"),
    ("R013", [M("m1")], {"m1": doc(fields={"material_code": fv("qlab-123456")})}, None, None, None, "reject", "applicable", "前缀非大写"),
    ("R013", [M("m1")], {"m1": doc(fields={"material_code": fv("QLAB-12345")})}, None, None, None, "reject", "applicable", "数字不足6位"),
    ("R013", [M("m1")], {"m1": doc(fields={"material_code": fu()})}, None, None, None, "ambiguous", "applicable", "编号读不清"),
    ("R013", [M("m1")], {"m1": doc()}, None, None, None, "reject", "applicable", "完整区域缺失"),

    # R014 批次号格式与合法日期
    ("R014", [M("m1")], {"m1": doc(fields={"batch_number": fv("20320229-753")})}, None, None, None, "pass", "applicable", "闰日合法"),
    ("R014", [M("m1")], {"m1": doc(fields={"batch_number": fv("20310229-753")})}, None, None, None, "reject", "applicable", "非闰日非法"),
    ("R014", [M("m1")], {"m1": doc(fields={"batch_number": fv("20320229-75")})}, None, None, None, "reject", "applicable", "流水号仅2位"),
    ("R014", [M("m1")], {"m1": doc(fields={"batch_number": fu()})}, None, None, None, "ambiguous", "applicable", "批次号读不清"),
    ("R014", [M("m1")], {"m1": doc()}, None, None, None, "reject", "applicable", "完整区域缺失"),

    # R015 件数格式
    ("R015", [M("m1")], {"m1": doc(fields={"quantity": fv("2件")})}, None, None, None, "pass", "applicable", "正整数件"),
    ("R015", [M("m1")], {"m1": doc(fields={"quantity": fv("2.5件")})}, None, None, None, "reject", "applicable", "小数点件数"),
    ("R015", [M("m1")], {"m1": doc(fields={"quantity": fv("一套")})}, None, None, None, "reject", "applicable", "仅写一套"),
    ("R015", [M("m1")], {"m1": doc(fields={"quantity": fv("0件")})}, None, None, None, "reject", "applicable", "零件"),
    ("R015", [M("m1")], {"m1": doc(fields={"quantity": fu()})}, None, None, None, "ambiguous", "applicable", "件数读不清"),

    # R016 支持地址格式
    ("R016", [M("m1")], {"m1": doc(fields={"support_url": fv("https://example.com/x")})}, None, None, None, "pass", "applicable", "https主机非空"),
    ("R016", [M("m1")], {"m1": doc(fields={"support_url": fv("http://example.com")})}, None, None, None, "reject", "applicable", "非https"),
    ("R016", [M("m1")], {"m1": doc(fields={"support_url": fv("https://")})}, None, None, None, "reject", "applicable", "主机名为空"),
    ("R016", [M("m1")], {"m1": doc(fields={"support_url": fu()})}, None, None, None, "ambiguous", "applicable", "地址读不清"),
    ("R016", [M("m1")], {"m1": doc()}, None, None, None, "reject", "applicable", "完整区域缺失"),

    # R017 启用日与截止日
    ("R017", [M("m1")], {"m1": doc(fields={"enable_date": fv("2029-01-01"), "expire_date": fv("2029-12-31")})}, None, None, None, "pass", "applicable", "日期合法且有序"),
    ("R017", [M("m1")], {"m1": doc(fields={"enable_date": fv("2029-12-31"), "expire_date": fv("2029-01-01")})}, None, None, None, "reject", "applicable", "启用晚于截止"),
    ("R017", [M("m1")], {"m1": doc(fields={"enable_date": fv("2029-01-01")})}, None, None, None, "reject", "applicable", "仅一端日期"),
    ("R017", [M("m1")], {"m1": doc(fields={"enable_date": fv("2029-01-01"), "expire_date": fu()})}, None, None, None, "ambiguous", "applicable", "截止日读不清"),

    # R018 双语字段成对
    ("R018", [M("m1")], {}, None, None, {"language_mode": "中文"}, "pass", "applicable", "非双语版"),
    ("R018", [M("m1")], {"m1": doc(fields={"product_name": fv("灯"), "product_name_en": fv("Lamp")})}, None, None, {"language_mode": "双语"}, "pass", "applicable", "双语成对"),
    ("R018", [M("m1")], {"m1": doc(fields={"product_name": fv("灯")})}, None, None, {"language_mode": "双语"}, "reject", "applicable", "缺英文字段"),
    ("R018", [M("m1")], {"m1": doc(fields={"product_name": fv("灯"), "product_name_en": fv("")})}, None, None, {"language_mode": "双语"}, "reject", "applicable", "英文字段为空"),
    ("R018", [M("m1")], {"m1": doc(fields={"product_name": fv("灯"), "product_name_en": fu()})}, None, None, {"language_mode": "双语"}, "ambiguous", "applicable", "英文字段读不清"),
]


class D4Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quackguard-d4-")
        cls.dir = Path(cls.temp.name)
        rules_path = cls.dir / "rules.jsonl"
        rules = [rule for path in RULE_FILES for rule in read_rows(path)]
        rules_path.write_text(
            "".join(json.dumps(rule, ensure_ascii=False) + "\n" for rule in rules),
            encoding="utf-8",
        )
        cls.artifacts = cls.dir / "artifacts"
        cls.build_result = build(rules_path, CONFIG, cls.artifacts)
        cls.policy = load_policy(cls.artifacts)
        cls.rules = {rule["rule_id"]: rule for rule in rules}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_rule(self, rule_id, materials, documents, faces=None,
                 sources=None, metadata=None, review_date="2026-06-15"):
        context = {}
        for material in materials:
            mid = material["material_id"]
            face = "front" if faces is None else faces.get(mid, "front")
            source = "photo" if sources is None else sources.get(mid, "photo")
            info = {}
            if isinstance(face, str):
                info["face_id"] = face
            if isinstance(source, str):
                info["source_type"] = source
            context[mid] = info
        meta = {"material_context": context}
        if metadata:
            meta.update(metadata)
        task = {
            "audit_unit_id": "unit",
            "sample_id": "sample",
            "rule_id": rule_id,
            "rule_version": "3.0",
            "review_date": review_date,
            "metadata": meta,
            "materials": materials,
        }
        return review_task(DictGateway(documents), self.policy, task, "unused.jsonl") + (task,)

    def test_matrix_f01_f03_ready(self):
        self.assertEqual(self.build_result["status"], "ready")
        matrix = json.loads(
            (self.artifacts / "engine" / "rule_test_matrix.json").read_text(
                encoding="utf-8"
            )
        )
        rows = {row["rule_id"]: row for row in matrix}
        for rule_id in JUDGED_RULES:
            self.assertIn(rule_id, rows)
            self.assertEqual(rows[rule_id]["case_status"], "ready")
            self.assertEqual(rows[rule_id]["implementation_status"], "implemented")

    def test_sop_quotes_original_rule_text(self):
        for rule_id in JUDGED_RULES:
            implementation = self.policy["implementations"][rule_id + "@3.0"]
            self.assertEqual(implementation["processor"], "r_rule")
            family = implementation["sop"]["family"]
            expected_family = "F01" if rule_id <= "R006" else (
                "F02" if rule_id <= "R012" else "F03"
            )
            self.assertEqual(family, expected_family, rule_id)
            quotes = implementation["sop"]["source_quotes"]
            self.assertIn(self.rules[rule_id]["rule_text"], json.dumps(quotes, ensure_ascii=False))

    def test_rule_cases(self):
        for rule_id, materials, documents, faces, sources, metadata, label, appl, note in CASES:
            with self.subTest(rule=rule_id, note=note):
                output, traces, task = self.run_rule(
                    rule_id, materials, documents, faces, sources, metadata
                )
                self.assertEqual(
                    output["execution_status"], "completed", output.get("reason")
                )
                self.assertEqual(
                    (output["label"], output["applicability"]),
                    (label, appl),
                    output.get("reason"),
                )
                self.assertEqual(prediction_errors(output, task), [])
                self.assertTrue(traces or appl != "applicable" or not documents)

    def test_every_judged_rule_has_both_pass_and_reject(self):
        labels = {}
        for case in CASES:
            labels.setdefault(case[0], set()).add(case[6])
        for rule_id in JUDGED_RULES:
            self.assertIn("pass", labels[rule_id], rule_id)
            self.assertIn("reject", labels[rule_id], rule_id)
            self.assertIn("ambiguous", labels[rule_id], rule_id)


if __name__ == "__main__":
    unittest.main()
