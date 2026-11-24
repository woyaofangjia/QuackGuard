"""D3：R001–R060 结构化规则实现验收。

覆盖：
1. 构建矩阵 62 条全部 implemented/ready；
2. 60 条规则的合成判定分支（pass/reject/ambiguous/not_applicable/unknown）；
3. 公开开发集 8 条规则 32 例可复现标签的精确回归；
4. R021/R026/R060 共 12 例仅做合约校验（标签边界/形状/载体在像素中，不可从事实复现）；
5. 适用性提前判定不调用视觉网关；
6. 生产模块与公开集/答案物理隔离。
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from quackguard.artifacts import build, load_policy
from quackguard.contracts import prediction_errors, read_rows
from quackguard.inference import review_task
from quackguard.rule_engine import REGISTRY, applicability
from quackguard.rule_facts import (
    field_missing as fm,
    field_unreadable as fu,
    field_value as fv,
)

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "O2_AI商品审核官_资料包"
PUBLIC = PACK / "03_公开开发集"
CONFIG = PACK / "04_最小对接样例" / "config.json"
RULE_FILES = [
    PACK / "01_规则包" / "基础规则版本.jsonl",
    PACK / "01_规则包" / "进阶规则60条.jsonl",
]

# 可从 visible_facts/shape 确定性复现 gold 标签的规则
DETERMINISTIC_RULES = {"R005", "R010", "R014", "R035", "R041", "R048", "R050"}
CONTRACT_ONLY_RULES = {"R021", "R026", "R060"}


def M(mid, role="package_photos", page=1):
    return {
        "material_id": mid,
        "role": role,
        "relative_path": mid + ".png",
        "media_type": "image/png",
        "page_index": page,
    }


def doc(fields=None, texts=None, shapes=None, components=None,
        matrix_pairs=None, pages=None, model_specs=None, **kw):
    document = {"inspection_complete": True, "voided": False}
    if fields:
        document["fields"] = fields
    if texts:
        document["texts"] = texts
    if shapes:
        document["shapes"] = shapes
    if components:
        document["components"] = components
    if matrix_pairs:
        document["matrix_pairs"] = matrix_pairs
    if pages:
        document["pages"] = pages
    if model_specs:
        document["model_specs"] = model_specs
    document.update(kw)
    return document


def sh(present=None, **extra):
    value = {"present": present}
    value.update(extra)
    return value


def page(number, status="value"):
    return {"printed_page_number": number, "status": status}


def pair(component_id, model, status="value"):
    return {"component_id": component_id, "model": model, "status": status}


def spec(model, value, status="value"):
    return {"model": model, "spec": value, "status": status}


class DictGateway:
    """按 material_id 返回预置视觉事实信封。"""

    def __init__(self, documents, statuses=None):
        self.documents = documents
        self.statuses = statuses or {}

    def messages(self, prompt, visual_task, input_path):
        material = visual_task["materials"][0]
        return {
            "material_id": material["material_id"],
            "page_index": material["page_index"],
        }

    def complete(self, messages):
        mid = messages["material_id"]
        status = self.statuses.get(mid, "clear")
        raw = self.documents.get(mid)
        if raw is None:
            documents = []
        elif isinstance(raw, list):
            documents = raw
        else:
            documents = [raw]
        if status == "unreadable":
            documents = []
        return json.dumps(
            {"status": status, "documents": documents}, ensure_ascii=False
        )


def envelope(document):
    return json.dumps({"status": "clear", "documents": [document]}, ensure_ascii=False)


FIELD_MAP = {
    "型号": "model",
    "主型号": "main_model",
    "材料编号": "material_code",
    "批次号": "batch_number",
    "签发日": "issued_date",
    "有效起始日": "valid_from",
    "有效截止日": "valid_to",
}


def recover_matrix_pairs(shape):
    cells = (shape or {}).get("matrix_cells") or {}
    rows = {}
    for cell in cells.values():
        rows.setdefault(cell["component_id"], []).append(cell)
    pairs = []
    for component_id, row in rows.items():
        hit = next((c for c in row if c.get("value") == "有记录"), None)
        if hit is not None:
            pairs.append(pair(component_id, hit["model"], "value"))
        elif any(c.get("status") == "unreadable" for c in row):
            pairs.append(pair(component_id, None, "unreadable"))
    return pairs


def public_document(task, material, evidence):
    fields = {}
    texts = {}
    components = []
    matrix_pairs = recover_matrix_pairs(evidence.get("shape"))
    context = task["metadata"].get("material_context", {}).get(
        material["material_id"], {}
    )
    face = context.get("face_id")
    for item in evidence.get("visible_facts") or []:
        text = item["text"]
        readable = item["readable"]
        if "：" in text:
            name, value = text.split("：", 1)
            if name == "标称重量":
                if face == "front":
                    key = "front_weight"
                elif face == "back":
                    key = "back_weight"
                else:
                    key = None
            elif name == "兼容型号":
                if readable:
                    fields["compatible_models"] = fv(
                        [v.strip() for v in value.split("、") if v.strip()]
                    )
                else:
                    fields["compatible_models"] = fu()
                continue
            else:
                key = FIELD_MAP.get(name)
            if key is None:
                continue
            fields[key] = fv(value) if readable else fu()
            continue
        match = re.match(r"^\s*(\S+)\s*→\s*(\S+)\s*$", text)
        if match:
            component_id = match.group(1)
            model = match.group(2)
            if readable and model != "{MASK}":
                components.append(pair(component_id, model, "value"))
            else:
                components.append(pair(component_id, None, "unreadable"))
            continue
        if not readable:
            if text == "保持干燥":
                texts["keep_dry_cn"] = fu()
            elif "dry" in text.lower():
                texts["keep_dry_en"] = fu()
            continue
        if text == "保持干燥":
            texts["keep_dry_cn"] = fv(text)
        elif "dry" in text.lower():
            texts["keep_dry_en"] = fv(text)
    return doc(
        fields=fields or None,
        texts=texts or None,
        components=components or None,
        matrix_pairs=matrix_pairs or None,
    )


class PublicGateway:
    def __init__(self, task, gold):
        self.task = task
        self.evidence = {
            (item["material_id"], item["page_index"]): item
            for item in gold["evidence"]
        }

    def messages(self, prompt, visual_task, input_path):
        material = visual_task["materials"][0]
        return {
            "material_id": material["material_id"],
            "page_index": material["page_index"],
        }

    def complete(self, messages):
        key = (messages["material_id"], messages["page_index"])
        evidence = self.evidence.get(key)
        if evidence is None:
            document = doc()
        else:
            material = next(
                m
                for m in self.task["materials"]
                if m["material_id"] == messages["material_id"]
                and m["page_index"] == messages["page_index"]
            )
            document = public_document(self.task, material, evidence)
        return envelope(document)


# ---- 合成判定分支：每条规则至少覆盖 pass 与 reject，并尽量覆盖 ambiguous / not_applicable / unknown
CASES = [
    # F01 实拍证据完整性
    {"rule": "R001", "materials": [M("m1")], "docs": {"m1": doc(four_edges_visible=True)}, "label": "pass", "appl": "applicable", "note": "主视图四边界可见"},
    {"rule": "R001", "materials": [M("m1")], "docs": {"m1": doc(four_edges_visible=False)}, "label": "reject", "appl": "applicable", "note": "主视图裁去边界"},
    {"rule": "R001", "materials": [M("m1")], "docs": {"m1": doc()}, "context": {"m1": {"face_id": None, "source_type": "photo", "is_standard_front_view": True}}, "label": "ambiguous", "appl": "applicable", "note": "面信息未知，无法确认是否提交主视图"},
    {"rule": "R001", "materials": [M("m1")], "docs": {"m1": doc()}, "label": "ambiguous", "appl": "applicable", "note": "边界不可确认"},

    {"rule": "R002", "materials": [M("m1")], "metadata": {"required_faces": ["front"]}, "label": "pass", "appl": "applicable", "note": "检查面已覆盖"},
    {"rule": "R002", "materials": [M("m1")], "metadata": {"required_faces": ["front", "back"]}, "label": "reject", "appl": "applicable", "note": "缺少背面"},
    {"rule": "R002", "materials": [M("m1")], "label": "ambiguous", "appl": "unknown", "note": "required_faces缺失"},

    {"rule": "R003", "materials": [M("c", "certificate_pages")], "metadata": {"certificate_total_pages": 2}, "docs": {"c": doc(pages=[page(1), page(2)])}, "label": "pass", "appl": "applicable", "note": "页码齐全"},
    {"rule": "R003", "materials": [M("c", "certificate_pages")], "metadata": {"certificate_total_pages": 2}, "docs": {"c": doc(pages=[page(1)])}, "label": "reject", "appl": "applicable", "note": "证明缺页"},
    {"rule": "R003", "materials": [M("c", "certificate_pages")], "metadata": {"certificate_total_pages": 2}, "docs": {"c": doc(pages=[page(1, "unreadable")])}, "label": "ambiguous", "appl": "applicable", "note": "页码不可读"},
    {"rule": "R003", "materials": [M("c", "certificate_pages")], "label": "ambiguous", "appl": "unknown", "note": "总页数缺失"},

    {"rule": "R004", "materials": [M("m1")], "label": "pass", "appl": "applicable", "note": "无附签"},
    {"rule": "R004", "materials": [M("m1")], "docs": {"m1": doc(carrier="attached_tag", attached_to_package=True)}, "label": "pass", "appl": "applicable", "note": "附签连接可见"},
    {"rule": "R004", "materials": [M("m1")], "docs": {"m1": doc(carrier="attached_tag", attached_to_package=False)}, "label": "reject", "appl": "applicable", "note": "附签脱离载体"},
    {"rule": "R004", "materials": [M("m1")], "docs": {"m1": doc(carrier="attached_tag")}, "label": "ambiguous", "appl": "applicable", "note": "连接关系不可确认"},

    {"rule": "R005", "materials": [M("a"), M("b")], "metadata": {"target_field": "型号"}, "docs": {"a": doc(fields={"model": fu(), "material_code": fv("QLAB-1")}), "b": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")})}, "label": "pass", "appl": "applicable", "note": "同编号补图"},
    {"rule": "R005", "materials": [M("a"), M("b")], "metadata": {"target_field": "型号"}, "docs": {"a": doc(fields={"model": fu(), "material_code": fv("QLAB-1")}), "b": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-2")})}, "label": "reject", "appl": "applicable", "note": "补图编号不一致"},
    {"rule": "R005", "materials": [M("a"), M("b")], "metadata": {"target_field": "型号"}, "docs": {"a": doc(fields={"model": fv("EDU-753"), "material_code": fu()}), "b": doc(fields={"model": fu(), "material_code": fv("QLAB-1")})}, "label": "ambiguous", "appl": "applicable", "note": "关联键不清"},
    {"rule": "R005", "materials": [M("a")], "metadata": {"target_field": "型号"}, "docs": {"a": doc(fields={"model": fv("EDU-753")})}, "label": "pass", "appl": "applicable", "note": "无需补图"},

    {"rule": "R006", "materials": [M("m1")], "context": {"m1": {"face_id": "front", "source_type": "photo", "is_standard_front_view": True}}, "label": "pass", "appl": "applicable", "note": "实拍来源"},
    {"rule": "R006", "materials": [M("m1")], "context": {"m1": {"face_id": "front", "source_type": "render", "is_standard_front_view": True}}, "label": "reject", "appl": "applicable", "note": "仅渲染图"},
    {"rule": "R006", "materials": [M("m1")], "context": {"m1": {"face_id": "front", "source_type": "unknown", "is_standard_front_view": True}}, "label": "ambiguous", "appl": "applicable", "note": "来源未知"},

    # F02 商品身份与型号
    {"rule": "R007", "materials": [M("m1")], "metadata": {"declared_model": "EDU-753"}, "docs": {"m1": doc(fields={"model": fv("EDU-753")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R007", "materials": [M("m1")], "metadata": {"declared_model": "EDU-753"}, "docs": {"m1": doc(fields={"model": fv("EDU-268")})}, "label": "reject", "appl": "applicable"},
    {"rule": "R007", "materials": [M("m1")], "metadata": {"declared_model": "EDU-753"}, "docs": {"m1": doc(fields={"model": fu()})}, "label": "ambiguous", "appl": "applicable"},
    {"rule": "R007", "materials": [M("m1")], "docs": {"m1": doc(fields={"model": fv("EDU-753")})}, "label": "ambiguous", "appl": "applicable", "note": "declared_model缺失"},

    {"rule": "R008", "materials": [M("c", "certificate_pages")], "metadata": {"declared_component_models": ["A", "B"]}, "docs": {"c": [doc(fields={"component_models": fv("A")}), doc(fields={"component_models": fv("B")})]}, "label": "pass", "appl": "applicable"},
    {"rule": "R008", "materials": [M("c", "certificate_pages")], "metadata": {"declared_component_models": ["A", "B"]}, "docs": {"c": [doc(fields={"component_models": fv("A")})]}, "label": "reject", "appl": "applicable", "note": "缺少一个型号"},
    {"rule": "R008", "materials": [M("c", "certificate_pages")], "metadata": {"declared_component_models": ["A", "B"]}, "docs": {"c": doc(fields={"component_models": fu()})}, "label": "ambiguous", "appl": "applicable"},
    {"rule": "R008", "materials": [M("c", "certificate_pages")], "label": "ambiguous", "appl": "unknown", "note": "declared_component_models缺失"},

    {"rule": "R009", "materials": [M("p", "product_photos"), M("q", "product_photos")], "metadata": {"declared_quantity": 2}, "docs": {"p": doc(fields={"serial_number": fv("S1")}), "q": doc(fields={"serial_number": fv("S2")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R009", "materials": [M("p"), M("q")], "metadata": {"declared_quantity": 2}, "docs": {"p": doc(fields={"serial_number": fv("S1")}), "q": doc(fields={"serial_number": fv("S1")})}, "label": "reject", "appl": "applicable", "note": "重复序列号"},
    {"rule": "R009", "materials": [M("p"), M("q")], "metadata": {"declared_quantity": 2}, "docs": {"p": doc(fields={"serial_number": fv("S1")}), "q": doc(fields={"serial_number": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "部分不可读"},

    {"rule": "R010", "materials": [M("m1")], "metadata": {"declared_model": "EDU-753"}, "docs": {"m1": doc(fields={"main_model": fv("EDU-753"), "compatible_models": fv(["EDU-268"])})}, "label": "pass", "appl": "applicable"},
    {"rule": "R010", "materials": [M("m1")], "metadata": {"declared_model": "EDU-753"}, "docs": {"m1": doc(fields={"main_model": fv("EDU-268"), "compatible_models": fv(["EDU-753"])})}, "label": "reject", "appl": "applicable", "note": "兼容不替代"},
    {"rule": "R010", "materials": [M("m1")], "metadata": {"declared_model": "EDU-753"}, "docs": {"m1": doc(fields={"main_model": fu(), "compatible_models": fv(["EDU-753"])})}, "label": "ambiguous", "appl": "applicable", "note": "主型号不清"},

    {"rule": "R011", "materials": [M("m1"), M("c", "certificate_pages")], "metadata": {"declared_model": "NEW-1"}, "docs": {"m1": doc(fields={"old_model": fv("OLD-1")}), "c": doc(fields={"old_model_relation": fv("OLD-1替代为NEW-1")}, voided=False)}, "label": "pass", "appl": "applicable", "note": "新旧映射存在"},
    {"rule": "R011", "materials": [M("m1"), M("c", "certificate_pages")], "metadata": {"declared_model": "NEW-1"}, "docs": {"m1": doc(fields={"old_model": fv("OLD-1")}), "c": doc(voided=False)}, "label": "reject", "appl": "applicable", "note": "无映射"},
    {"rule": "R011", "materials": [M("m1")], "docs": {"m1": doc()}, "label": "pass", "appl": "applicable", "note": "未用旧型号"},

    {"rule": "R012", "materials": [M("m1")], "metadata": {"declared_model": "EDU-753"}, "docs": {"m1": doc(fields={"model": fv("EDU-753")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R012", "materials": [M("m1")], "metadata": {"declared_model": "EDO-753"}, "docs": {"m1": doc(fields={"model": fv("ED0-753")})}, "label": "reject", "appl": "applicable", "note": "相似字符不一致"},
    {"rule": "R012", "materials": [M("m1")], "metadata": {"declared_model": "ED0-753"}, "docs": {"m1": doc(fields={"model": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "含0/1不可读"},

    # F03 字段完整性与格式
    {"rule": "R013", "materials": [M("m1")], "docs": {"m1": doc(fields={"material_code": fv("QLAB-753826")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R013", "materials": [M("m1")], "docs": {"m1": doc(fields={"material_code": fv("QLAB-75382")})}, "label": "reject", "appl": "applicable"},
    {"rule": "R013", "materials": [M("m1")], "docs": {"m1": doc(fields={"material_code": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R014", "materials": [M("m1")], "docs": {"m1": doc(fields={"batch_number": fv("20320229-753")})}, "label": "pass", "appl": "applicable", "note": "闰日批次"},
    {"rule": "R014", "materials": [M("m1")], "docs": {"m1": doc(fields={"batch_number": fv("20310229-753")})}, "label": "reject", "appl": "applicable", "note": "非闰日"},
    {"rule": "R014", "materials": [M("m1")], "docs": {"m1": doc(fields={"batch_number": fv("20320229-75")})}, "label": "reject", "appl": "applicable", "note": "流水号不足"},
    {"rule": "R014", "materials": [M("m1")], "docs": {"m1": doc(fields={"batch_number": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "批次遮挡"},

    {"rule": "R015", "materials": [M("m1")], "docs": {"m1": doc(fields={"quantity": fv("2件")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R015", "materials": [M("m1")], "docs": {"m1": doc(fields={"quantity": fv("2.5件")})}, "label": "reject", "appl": "applicable"},
    {"rule": "R015", "materials": [M("m1")], "docs": {"m1": doc(fields={"quantity": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R016", "materials": [M("m1")], "docs": {"m1": doc(fields={"support_url": fv("https://example.com")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R016", "materials": [M("m1")], "docs": {"m1": doc(fields={"support_url": fv("http://example.com")})}, "label": "reject", "appl": "applicable"},
    {"rule": "R016", "materials": [M("m1")], "docs": {"m1": doc(fields={"support_url": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R017", "materials": [M("m1")], "docs": {"m1": doc(fields={"enable_date": fv("2029-01-01"), "expire_date": fv("2029-12-31")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R017", "materials": [M("m1")], "docs": {"m1": doc(fields={"enable_date": fv("2029-12-31"), "expire_date": fv("2029-01-01")})}, "label": "reject", "appl": "applicable"},
    {"rule": "R017", "materials": [M("m1")], "docs": {"m1": doc(fields={"enable_date": fv("2029-01-01"), "expire_date": fu()})}, "label": "ambiguous", "appl": "applicable"},
    {"rule": "R017", "materials": [M("m1")], "docs": {"m1": doc(fields={"enable_date": fv("2029-01-01")})}, "label": "reject", "appl": "applicable", "note": "只给一端"},

    {"rule": "R018", "materials": [M("m1")], "metadata": {"language_mode": "中文"}, "label": "pass", "appl": "applicable", "note": "非双语未触发"},
    {"rule": "R018", "materials": [M("m1")], "metadata": {"language_mode": "双语"}, "docs": {"m1": doc(fields={"product_name": fv("灯"), "product_name_en": fv("Lamp")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R018", "materials": [M("m1")], "metadata": {"language_mode": "双语"}, "docs": {"m1": doc(fields={"product_name": fv("灯")})}, "label": "reject", "appl": "applicable", "note": "缺英文"},
    {"rule": "R018", "materials": [M("m1")], "metadata": {"language_mode": "双语"}, "docs": {"m1": doc(fields={"product_name": fv("灯"), "product_name_en": fu()})}, "label": "ambiguous", "appl": "applicable"},

    # F04 标签位置与载体
    {"rule": "R019", "materials": [M("m1")], "docs": {"m1": doc(fields={"main_model": fv("EDU-753")})}, "label": "pass", "appl": "applicable", "note": "主型号在正面"},
    {"rule": "R019", "materials": [M("front"), M("back")], "context": {"front": {"face_id": "front", "source_type": "photo", "is_standard_front_view": True}, "back": {"face_id": "back", "source_type": "photo", "is_standard_front_view": True}}, "docs": {"front": doc(), "back": doc(fields={"main_model": fv("EDU-753")})}, "label": "reject", "appl": "applicable", "note": "主型号仅在背面"},
    {"rule": "R019", "materials": [M("m1")], "context": {"m1": {"face_id": None, "source_type": "photo", "is_standard_front_view": True}}, "label": "ambiguous", "appl": "applicable", "note": "无正面视图"},
    {"rule": "R019", "materials": [M("m1")], "docs": {"m1": doc(fields={"main_model": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "正面主型号不清"},

    {"rule": "R020", "materials": [M("p", "product_photos")], "docs": {"p": doc(fields={"serial_number": fv("S1")})}, "label": "pass", "appl": "applicable", "note": "序列号在本体"},
    {"rule": "R020", "materials": [M("m1")], "docs": {"m1": doc(fields={"serial_number": fv("S1")})}, "label": "reject", "appl": "applicable", "note": "序列号在外包装"},
    {"rule": "R020", "materials": [M("p", "product_photos")], "docs": {"p": doc(fields={"serial_number": fu()})}, "label": "ambiguous", "appl": "applicable"},
    {"rule": "R020", "materials": [M("p", "product_photos")], "label": "reject", "appl": "applicable", "note": "无序列号"},

    {"rule": "R021", "materials": [M("m1")], "docs": {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, fields_on_same_label=True)}, "label": "pass", "appl": "applicable", "note": "同标签"},
    {"rule": "R021", "materials": [M("m1")], "docs": {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, fields_on_same_label=False)}, "label": "reject", "appl": "applicable", "note": "分标签"},
    {"rule": "R021", "materials": [M("m1")], "docs": {"m1": doc(fields={"model": fu(), "material_code": fv("QLAB-1")})}, "label": "ambiguous", "appl": "applicable", "note": "读不清"},

    {"rule": "R022", "materials": [M("m1")], "metadata": {"package_longest_edge_mm": 100}, "docs": {"m1": doc(carrier="attached_tag", fields={"material_code": fv("QLAB-1")})}, "label": "reject", "appl": "applicable", "note": "大包装附签"},
    {"rule": "R022", "materials": [M("m1")], "metadata": {"package_longest_edge_mm": 70}, "docs": {"m1": doc(carrier="attached_tag", fields={"material_code": fv("QLAB-1")})}, "label": "pass", "appl": "applicable", "note": "小包装例外"},
    {"rule": "R022", "materials": [M("m1")], "docs": {"m1": doc(fields={"material_code": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "尺寸缺失"},
    {"rule": "R022", "materials": [M("m1")], "metadata": {"package_longest_edge_mm": 100}, "docs": {"m1": doc(fields={"material_code": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "编号不可读"},

    {"rule": "R023", "materials": [M("m1")], "docs": {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, cut_line_crosses_label=False)}, "label": "pass", "appl": "applicable"},
    {"rule": "R023", "materials": [M("m1")], "docs": {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, cut_line_crosses_label=True)}, "label": "reject", "appl": "applicable"},
    {"rule": "R023", "materials": [M("m1")], "docs": {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")})}, "label": "ambiguous", "appl": "applicable", "note": "切线未知"},

    {"rule": "R024", "materials": [M("m1")], "docs": {"m1": doc(center_y_ratio=0.3)}, "label": "pass", "appl": "applicable", "note": "标志上半区"},
    {"rule": "R024", "materials": [M("m1")], "docs": {"m1": doc(center_y_ratio=0.7)}, "label": "reject", "appl": "applicable", "note": "标志下半区"},
    {"rule": "R024", "materials": [M("m1")], "docs": {"m1": doc()}, "label": "ambiguous", "appl": "applicable", "note": "中心点不可确认"},
    {"rule": "R024", "materials": [M("m1")], "context": {"m1": {"face_id": "front", "source_type": "photo", "is_standard_front_view": False}}, "label": "ambiguous", "appl": "applicable", "note": "无标准正视图"},

    # F05 图形与视觉关系
    {"rule": "R025", "materials": [M("m1")], "metadata": {"declared_icon": "圆标"}, "docs": {"m1": doc(shapes={"hollow_circle": sh(True, interior_has_text=False)})}, "label": "pass", "appl": "applicable"},
    {"rule": "R025", "materials": [M("m1")], "metadata": {"declared_icon": "圆标"}, "docs": {"m1": doc(shapes={"hollow_circle": sh(True, interior_has_text=True)})}, "label": "reject", "appl": "applicable", "note": "圆内有文字"},
    {"rule": "R025", "materials": [M("m1")], "metadata": {"declared_icon": "圆标"}, "docs": {"m1": doc(shapes={"hollow_circle": sh(None)})}, "label": "ambiguous", "appl": "applicable"},
    {"rule": "R025", "materials": [M("m1")], "label": "pass", "appl": "applicable", "note": "未申报圆标"},

    {"rule": "R026", "materials": [M("m1")], "metadata": {"declared_icon": "三角点标"}, "docs": {"m1": doc(shapes={"triangle_border": sh(True), "dot_inside": sh(True, count=1)})}, "label": "pass", "appl": "applicable"},
    {"rule": "R026", "materials": [M("m1")], "metadata": {"declared_icon": "三角点标"}, "docs": {"m1": doc(shapes={"triangle_border": sh(True), "dot_inside": sh(True, count=2)})}, "label": "reject", "appl": "applicable", "note": "点数不符"},
    {"rule": "R026", "materials": [M("m1")], "metadata": {"declared_icon": "三角点标"}, "docs": {"m1": doc(shapes={"triangle_border": sh(None)})}, "label": "ambiguous", "appl": "applicable"},
    {"rule": "R026", "materials": [M("m1")], "label": "pass", "appl": "applicable", "note": "未申报三角点标"},

    {"rule": "R027", "materials": [M("m1")], "docs": {"m1": doc(shapes={"circle_icon": sh(True, center_x=0.2), "square_icon": sh(True, center_x=0.5)})}, "label": "pass", "appl": "applicable", "note": "圆在方左"},
    {"rule": "R027", "materials": [M("m1")], "docs": {"m1": doc(shapes={"circle_icon": sh(True, center_x=0.5), "square_icon": sh(True, center_x=0.2)})}, "label": "reject", "appl": "applicable", "note": "圆在方右"},
    {"rule": "R027", "materials": [M("m1")], "docs": {"m1": doc(shapes={"circle_icon": sh(False)})}, "label": "reject", "appl": "applicable", "note": "图标缺失"},
    {"rule": "R027", "materials": [M("m1")], "docs": {"m1": doc(shapes={"circle_icon": sh(True), "square_icon": sh(True)})}, "label": "ambiguous", "appl": "applicable", "note": "横坐标不可确认"},

    {"rule": "R028", "materials": [M("m1")], "metadata": {"declared_color_name": "蓝色版"}, "docs": {"m1": doc(shapes={"blue_dot": sh(True)})}, "label": "pass", "appl": "applicable"},
    {"rule": "R028", "materials": [M("m1")], "metadata": {"declared_color_name": "蓝色版"}, "docs": {"m1": doc(shapes={"blue_dot": sh(False)})}, "label": "reject", "appl": "applicable"},
    {"rule": "R028", "materials": [M("m1")], "metadata": {"declared_color_name": "蓝色版"}, "docs": {"m1": doc(shapes={"blue_dot": sh(None)})}, "label": "ambiguous", "appl": "applicable"},
    {"rule": "R028", "materials": [M("m1")], "metadata": {"declared_color_name": "红色版"}, "label": "pass", "appl": "applicable", "note": "非蓝黄未触发"},

    {"rule": "R029", "materials": [M("m1")], "docs": {"m1": doc(shapes={"star": sh(True, count=3)})}, "label": "pass", "appl": "applicable"},
    {"rule": "R029", "materials": [M("m1")], "docs": {"m1": doc(shapes={"star": sh(True, count=2)})}, "label": "reject", "appl": "applicable"},
    {"rule": "R029", "materials": [M("m1")], "docs": {"m1": doc(shapes={}, inspection_complete=False)}, "label": "ambiguous", "appl": "applicable", "note": "范围不完整"},

    {"rule": "R030", "materials": [M("m1")], "docs": {"m1": doc(shapes={"mark_height": sh(None, height=16), "model_text_height": sh(None, height=10)})}, "label": "pass", "appl": "applicable", "note": "高度比达标"},
    {"rule": "R030", "materials": [M("m1")], "docs": {"m1": doc(shapes={"mark_height": sh(None, height=10), "model_text_height": sh(None, height=10)})}, "label": "reject", "appl": "applicable", "note": "高度比不足"},
    {"rule": "R030", "materials": [M("m1")], "docs": {"m1": doc(shapes={"mark_height": sh(None)})}, "label": "ambiguous", "appl": "applicable", "note": "高度不可测"},
    {"rule": "R030", "materials": [M("m1")], "context": {"m1": {"face_id": "front", "source_type": "photo", "is_standard_front_view": False}}, "label": "ambiguous", "appl": "applicable", "note": "无标准正视图"},

    # F06 警示与说明内容
    {"rule": "R031", "materials": [M("m1")], "docs": {"m1": doc(texts={"warning_practice": fv("仅供练习使用")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R031", "materials": [M("m1")], "docs": {"m1": doc(texts={"warning_practice": fv("仅供练习")})}, "label": "reject", "appl": "applicable"},
    {"rule": "R031", "materials": [M("m1")], "docs": {"m1": doc(texts={"warning_practice": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R032", "materials": [M("m1")], "docs": {"m1": doc(texts={"indoor_only": fv("仅室内"), "outdoor_longterm": fv("适合室外长期")})}, "label": "reject", "appl": "applicable", "note": "文案矛盾"},
    {"rule": "R032", "materials": [M("m1")], "docs": {"m1": doc(texts={"indoor_only": fv("仅室内")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R032", "materials": [M("m1")], "docs": {"m1": doc(texts={"indoor_only": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R033", "materials": [M("m1")], "docs": {"m1": doc(texts={"step1": fv("1"), "step2": fv("2"), "step3": fv("3")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R033", "materials": [M("m1")], "docs": {"m1": doc(texts={"step1": fv("1"), "step2": fv("2")})}, "label": "reject", "appl": "applicable", "note": "步骤缺失"},
    {"rule": "R033", "materials": [M("m1")], "docs": {"m1": doc(texts={"step1": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R034", "materials": [M("m1")], "metadata": {"declared_detachable_in_storage": True}, "docs": {"m1": doc(texts={"non_removable_in_use": fv("使用时不可拆卸"), "removable_in_storage": fv("收纳时可拆卸")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R034", "materials": [M("m1")], "metadata": {"declared_detachable_in_storage": True}, "docs": {"m1": doc(texts={"non_removable_in_use": fv("收纳时可拆卸"), "removable_in_storage": fv("使用时不可拆卸")})}, "label": "reject", "appl": "applicable", "note": "条件互换"},
    {"rule": "R034", "materials": [M("m1")], "label": "pass", "appl": "applicable", "note": "未申报可拆卸"},
    {"rule": "R034", "materials": [M("m1")], "metadata": {"declared_detachable_in_storage": True}, "docs": {"m1": doc(texts={"non_removable_in_use": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R035", "materials": [M("m1")], "metadata": {"language_mode": "双语"}, "docs": {"m1": doc(texts={"keep_dry_cn": fv("保持干燥"), "keep_dry_en": fv("Keep dry")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R035", "materials": [M("m1")], "metadata": {"language_mode": "英文"}, "docs": {"m1": doc(texts={"keep_dry_en": fv("keep dry")})}, "label": "reject", "appl": "applicable", "note": "大小写错误"},
    {"rule": "R035", "materials": [M("m1")], "metadata": {"language_mode": "英文"}, "docs": {"m1": doc(texts={"keep_dry_cn": fv("保持干燥")})}, "label": "reject", "appl": "applicable", "note": "缺英文"},
    {"rule": "R035", "materials": [M("m1")], "metadata": {"language_mode": "中文"}, "docs": {"m1": doc(texts={"keep_dry_cn": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R036", "materials": [M("m1")], "docs": {"m1": doc(texts={"do_not_invert": fv("请勿倒置")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R036", "materials": [M("m1")], "docs": {"m1": doc(texts={"invert_phrase": fv("倒置")})}, "label": "reject", "appl": "applicable", "note": "缺否定词"},
    {"rule": "R036", "materials": [M("m1")], "docs": {"m1": doc(texts={"do_not_invert": fu()})}, "label": "ambiguous", "appl": "applicable"},

    # F07 参数与数值关系
    {"rule": "R037", "materials": [M("m1")], "metadata": {"declared_capacity_ml": 500}, "docs": {"m1": doc(fields={"capacity": fv("0.5 L")})}, "label": "pass", "appl": "applicable", "note": "单位换算"},
    {"rule": "R037", "materials": [M("m1")], "metadata": {"declared_capacity_ml": 500}, "docs": {"m1": doc(fields={"capacity": fv("600 mL")})}, "label": "reject", "appl": "applicable"},
    {"rule": "R037", "materials": [M("m1")], "docs": {"m1": doc(fields={"capacity": fv("500 mL")})}, "label": "ambiguous", "appl": "applicable", "note": "申报容量缺失"},
    {"rule": "R037", "materials": [M("m1")], "metadata": {"declared_capacity_ml": 500}, "docs": {"m1": doc(fields={"capacity": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R038", "materials": [M("m1")], "docs": {"m1": doc(fields={"net_weight": fv("100 g"), "gross_weight": fv("200 g")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R038", "materials": [M("m1")], "docs": {"m1": doc(fields={"net_weight": fv("200 g"), "gross_weight": fv("100 g")})}, "label": "reject", "appl": "applicable", "note": "净重大于总重"},
    {"rule": "R038", "materials": [M("m1")], "docs": {"m1": doc(fields={"net_weight": fv("100 g"), "gross_weight": fu()})}, "label": "ambiguous", "appl": "applicable"},
    {"rule": "R038", "materials": [M("m1")], "docs": {"m1": doc(fields={"net_weight": fv("100 g")})}, "label": "reject", "appl": "applicable", "note": "缺总重"},

    {"rule": "R039", "materials": [M("m1")], "metadata": {"declared_height_cm": 50}, "docs": {"m1": doc(fields={"height_min": fv("40 cm"), "height_max": fv("60 cm")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R039", "materials": [M("m1")], "metadata": {"declared_height_cm": 70}, "docs": {"m1": doc(fields={"height_min": fv("40 cm"), "height_max": fv("60 cm")})}, "label": "reject", "appl": "applicable"},
    {"rule": "R039", "materials": [M("m1")], "docs": {"m1": doc(fields={"height_min": fv("40 cm"), "height_max": fv("60 cm")})}, "label": "ambiguous", "appl": "applicable", "note": "申报高度缺失"},
    {"rule": "R039", "materials": [M("m1")], "metadata": {"declared_height_cm": 50}, "docs": {"m1": doc(fields={"height_min": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R040", "materials": [M("m1")], "docs": {"m1": doc(fields={"fill_percentages": fv([30, 30, 40])})}, "label": "pass", "appl": "applicable"},
    {"rule": "R040", "materials": [M("m1")], "docs": {"m1": doc(fields={"fill_percentages": fv([30, 30, 30])})}, "label": "reject", "appl": "applicable", "note": "合计非100"},
    {"rule": "R040", "materials": [M("m1")], "docs": {"m1": doc(fields={"fill_percentages": fv([30, 70])})}, "label": "reject", "appl": "applicable", "note": "非三项"},
    {"rule": "R040", "materials": [M("m1")], "docs": {"m1": doc(fields={"fill_percentages": fu()})}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R041", "materials": [M("f"), M("b")], "context": {"f": {"face_id": "front", "source_type": "photo", "is_standard_front_view": True}, "b": {"face_id": "back", "source_type": "photo", "is_standard_front_view": True}}, "docs": {"f": doc(fields={"front_weight": fv("750 g")}), "b": doc(fields={"back_weight": fv("742.5 g")})}, "label": "pass", "appl": "applicable", "note": "端点容差"},
    {"rule": "R041", "materials": [M("f"), M("b")], "context": {"f": {"face_id": "front", "source_type": "photo", "is_standard_front_view": True}, "b": {"face_id": "back", "source_type": "photo", "is_standard_front_view": True}}, "docs": {"f": doc(fields={"front_weight": fv("750 g")}), "b": doc(fields={"back_weight": fv("725 g")})}, "label": "reject", "appl": "applicable", "note": "差距过大"},
    {"rule": "R041", "materials": [M("f"), M("b")], "context": {"f": {"face_id": "front", "source_type": "photo", "is_standard_front_view": True}, "b": {"face_id": "back", "source_type": "photo", "is_standard_front_view": True}}, "docs": {"f": doc(fields={"front_weight": fv("750 g")}), "b": doc(fields={"back_weight": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "背面未知"},

    {"rule": "R042", "materials": [M("m1")], "review_date": "2026-06-15", "docs": {"m1": doc(fields={"production_date": fv("2026-06-10")})}, "label": "pass", "appl": "applicable", "note": "在30天内"},
    {"rule": "R042", "materials": [M("m1")], "review_date": "2026-06-15", "docs": {"m1": doc(fields={"production_date": fv("2026-01-01")})}, "label": "reject", "appl": "applicable", "note": "超出窗口"},
    {"rule": "R042", "materials": [M("m1")], "review_date": "2026-06-15", "docs": {"m1": doc(fields={"production_date": fu()})}, "label": "ambiguous", "appl": "applicable"},

    # F08 证明文件结构与时效
    {"rule": "R043", "materials": [M("c", "certificate_pages")], "docs": {"c": doc(fields={"material_code": fv("QLAB-1")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R043", "materials": [M("c", "certificate_pages")], "docs": {"c": doc(fields={"material_code": fu()})}, "label": "ambiguous", "appl": "applicable"},
    {"rule": "R043", "materials": [M("c", "certificate_pages", 2)], "docs": {"c": doc(fields={"material_code": fv("QLAB-1")})}, "label": "reject", "appl": "applicable", "note": "未提交首页"},

    {"rule": "R044", "materials": [M("c", "certificate_pages")], "review_date": "2029-06-01", "docs": {"c": doc(fields={"valid_from": fv("2029-01-01"), "valid_to": fv("2029-12-31")}, voided=False)}, "label": "pass", "appl": "applicable"},
    {"rule": "R044", "materials": [M("c", "certificate_pages")], "review_date": "2030-01-01", "docs": {"c": doc(fields={"valid_from": fv("2029-01-01"), "valid_to": fv("2029-12-31")}, voided=False)}, "label": "reject", "appl": "applicable", "note": "不在有效期"},
    {"rule": "R044", "materials": [M("c", "certificate_pages")], "review_date": "bad", "label": "ambiguous", "appl": "applicable", "note": "审核日期非法"},

    {"rule": "R045", "materials": [M("c", "certificate_pages")], "metadata": {"declared_model": "EDU-753"}, "docs": {"c": doc(fields={"covered_models": fv(["EDU-753"])}, voided=False)}, "label": "pass", "appl": "applicable"},
    {"rule": "R045", "materials": [M("c", "certificate_pages")], "metadata": {"declared_model": "EDU-753"}, "docs": {"c": doc(fields={"covered_models": fv(["全部型号"])}, voided=False)}, "label": "reject", "appl": "applicable", "note": "通配符"},
    {"rule": "R045", "materials": [M("c", "certificate_pages")], "docs": {"c": doc(fields={"covered_models": fu()}, voided=False)}, "label": "ambiguous", "appl": "applicable", "note": "申报型号缺失"},
    {"rule": "R045", "materials": [M("c", "certificate_pages")], "metadata": {"declared_model": "EDU-753"}, "docs": {"c": doc(fields={"covered_models": fu()}, voided=False)}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R046", "materials": [M("c", "certificate_pages")], "docs": {"c": doc(voided=False)}, "label": "pass", "appl": "applicable"},
    {"rule": "R046", "materials": [M("c", "certificate_pages")], "docs": {"c": doc(voided=True)}, "label": "reject", "appl": "applicable", "note": "作废"},
    {"rule": "R046", "materials": [M("c", "certificate_pages")], "docs": {"c": doc(voided=None)}, "label": "ambiguous", "appl": "applicable", "note": "作废标记模糊"},

    {"rule": "R047", "materials": [M("c", "certificate_pages")], "metadata": {"required_document_version": "V2"}, "docs": {"c": doc(fields={"document_version": fv("V2")}, voided=False)}, "label": "pass", "appl": "applicable"},
    {"rule": "R047", "materials": [M("c", "certificate_pages")], "metadata": {"required_document_version": "V2"}, "docs": {"c": doc(fields={"document_version": fv("V1")}, voided=False)}, "label": "reject", "appl": "applicable"},
    {"rule": "R047", "materials": [M("c", "certificate_pages")], "docs": {"c": doc(voided=False)}, "label": "ambiguous", "appl": "applicable", "note": "required版本缺失"},

    {"rule": "R048", "materials": [M("c", "certificate_pages")], "review_date": "2029-07-11", "metadata": {"withdrawal_date": "2029-07-12"}, "docs": {"c": doc(fields={"issued_date": fv("2029-01-01")}, voided=False)}, "label": "pass", "appl": "applicable", "note": "撤回前"},
    {"rule": "R048", "materials": [M("c", "certificate_pages")], "review_date": "2029-07-12", "metadata": {"withdrawal_date": "2029-07-12"}, "docs": {"c": doc(fields={"issued_date": fv("2029-01-01")}, voided=False)}, "label": "reject", "appl": "applicable", "note": "撤回当天"},
    {"rule": "R048", "materials": [M("c", "certificate_pages")], "review_date": "2029-07-11", "metadata": {"withdrawal_date": "2028-12-31"}, "docs": {"c": doc(fields={"issued_date": fv("2029-01-01")}, voided=False)}, "label": "ambiguous", "appl": "applicable", "note": "撤回早于签发"},

    # F09 跨材料关系核验
    {"rule": "R049", "materials": [M("p"), M("c", "certificate_pages")], "metadata": {"declared_material_code": "QLAB-1"}, "docs": {"p": doc(fields={"material_code": fv("QLAB-1")}), "c": doc(fields={"material_code": fv("QLAB-1")}, voided=False)}, "label": "pass", "appl": "applicable", "note": "三方一致"},
    {"rule": "R049", "materials": [M("p"), M("c", "certificate_pages")], "metadata": {"declared_material_code": "QLAB-1"}, "docs": {"p": doc(fields={"material_code": fv("QLAB-1")}), "c": doc(fields={"material_code": fv("QLAB-2")}, voided=False)}, "label": "reject", "appl": "applicable", "note": "证明不一致"},
    {"rule": "R049", "materials": [M("p"), M("c", "certificate_pages")], "metadata": {"declared_material_code": "QLAB-1"}, "docs": {"p": doc(fields={"material_code": fv("QLAB-1")}), "c": doc(fields={"material_code": fu()}, voided=False)}, "label": "ambiguous", "appl": "applicable", "note": "证明编号不清"},

    {"rule": "R050", "materials": [M("c", "certificate_pages")], "metadata": {"declared_components": [{"component_id": "a", "model": "EDU-753"}, {"component_id": "b", "model": "EDU-268"}]}, "docs": {"c": doc(components=[pair("a", "EDU-753"), pair("b", "EDU-268")], voided=False)}, "label": "pass", "appl": "applicable", "note": "正确配对"},
    {"rule": "R050", "materials": [M("c", "certificate_pages")], "metadata": {"declared_components": [{"component_id": "a", "model": "EDU-753"}, {"component_id": "b", "model": "EDU-268"}]}, "docs": {"c": doc(components=[pair("a", "EDU-268"), pair("b", "EDU-753")], voided=False)}, "label": "reject", "appl": "applicable", "note": "配对错误"},
    {"rule": "R050", "materials": [M("c", "certificate_pages")], "metadata": {"declared_components": [{"component_id": "a", "model": "EDU-753"}, {"component_id": "b", "model": "EDU-268"}]}, "docs": {"c": doc(components=[pair("a", "EDU-753"), pair("b", None, "unreadable")], voided=False)}, "label": "ambiguous", "appl": "applicable", "note": "一项不可读"},
    {"rule": "R050", "materials": [M("c", "certificate_pages")], "metadata": {"declared_components": [{"component_id": "a", "model": "EDU-753"}]}, "docs": {"c": doc(components=[pair("a", "EDU-753"), pair("b", "EDU-268")], voided=False)}, "label": "reject", "appl": "applicable", "note": "多出组件"},

    {"rule": "R051", "materials": [M("p"), M("c", "certificate_pages")], "docs": {"p": doc(fields={"batch_number": fv("150000")}), "c": doc(fields={"batch_min": fv("100000"), "batch_max": fv("200000")}, voided=False)}, "label": "pass", "appl": "applicable", "note": "批次在区间"},
    {"rule": "R051", "materials": [M("p"), M("c", "certificate_pages")], "docs": {"p": doc(fields={"batch_number": fv("999999")}), "c": doc(fields={"batch_min": fv("100000"), "batch_max": fv("200000")}, voided=False)}, "label": "reject", "appl": "applicable", "note": "批次越界"},
    {"rule": "R051", "materials": [M("p"), M("c", "certificate_pages")], "docs": {"p": doc(fields={"batch_number": fv("150000")}), "c": doc(fields={"batch_min": fu()}, voided=False)}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R052", "materials": [M("p"), M("c", "certificate_pages")], "metadata": {"declared_model": "EDU-753"}, "docs": {"p": doc(fields={"package_spec": fv("S1")}), "c": doc(model_specs=[spec("EDU-753", "S1")], voided=False)}, "label": "pass", "appl": "applicable", "note": "规格一致"},
    {"rule": "R052", "materials": [M("p"), M("c", "certificate_pages")], "metadata": {"declared_model": "EDU-753"}, "docs": {"p": doc(fields={"package_spec": fv("S1")}), "c": doc(model_specs=[spec("EDU-753", "S2")], voided=False)}, "label": "reject", "appl": "applicable", "note": "规格不一致"},
    {"rule": "R052", "materials": [M("p"), M("c", "certificate_pages")], "metadata": {"declared_model": "EDU-753"}, "docs": {"p": doc(fields={"package_spec": fv("S1")}), "c": doc(model_specs=[{"model": None, "spec": None, "status": "unreadable"}], voided=False)}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R053", "materials": [M("p"), M("c", "certificate_pages")], "docs": {"p": doc(fields={"main_model": fv("EDU-753"), "material_code": fv("QLAB-1")}), "c": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, voided=False)}, "label": "pass", "appl": "applicable", "note": "附图匹配"},
    {"rule": "R053", "materials": [M("p"), M("c", "certificate_pages")], "docs": {"p": doc(fields={"main_model": fv("EDU-753"), "material_code": fv("QLAB-1")}), "c": doc(fields={"model": fv("EDU-268"), "material_code": fv("QLAB-1")}, voided=False)}, "label": "reject", "appl": "applicable", "note": "附图型号不符"},
    {"rule": "R053", "materials": [M("p"), M("c", "certificate_pages")], "docs": {"p": doc(fields={"main_model": fv("EDU-753"), "material_code": fv("QLAB-1")}), "c": doc(fields={"model": fu()}, voided=False)}, "label": "ambiguous", "appl": "applicable"},

    {"rule": "R054", "materials": [M("c", "certificate_pages")], "docs": {"c": [doc(fields={"material_code": fv("QLAB-1"), "contact_name": fv("A")}, voided=False), doc(fields={"material_code": fv("QLAB-1"), "contact_name": fv("B")}, voided=False)]}, "label": "ambiguous", "appl": "applicable", "note": "同编号不同主体冲突"},
    {"rule": "R054", "materials": [M("c", "certificate_pages")], "docs": {"c": [doc(fields={"material_code": fv("QLAB-1"), "contact_name": fv("A")}, voided=False), doc(fields={"material_code": fv("QLAB-1"), "contact_name": fv("A")}, voided=False)]}, "label": "pass", "appl": "applicable", "note": "同主体"},

    # F10 适用条件与规则边界
    {"rule": "R055", "materials": [M("m1")], "metadata": {"category": "桌面灯", "power_mode": "可充电"}, "docs": {"m1": doc(texts={"charge_reading": fv("充电前阅读")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R055", "materials": [M("m1")], "metadata": {"category": "桌面灯", "power_mode": "可充电"}, "label": "reject", "appl": "applicable", "note": "缺充电说明"},
    {"rule": "R055", "materials": [M("m1")], "metadata": {"category": "其他"}, "label": "pass", "appl": "not_applicable", "note": "类别不符"},
    {"rule": "R055", "materials": [M("m1")], "metadata": {"category": "桌面灯"}, "label": "ambiguous", "appl": "unknown", "note": "供电方式未知"},

    {"rule": "R056", "materials": [M("m1")], "metadata": {"declared_quantity": 1}, "label": "pass", "appl": "not_applicable", "note": "单件不触发"},
    {"rule": "R056", "materials": [M("m1")], "metadata": {"declared_quantity": 2}, "docs": {"m1": doc(texts={"contents_list": fv("内含物")})}, "label": "pass", "appl": "applicable"},
    {"rule": "R056", "materials": [M("m1")], "metadata": {"declared_quantity": 2}, "label": "reject", "appl": "applicable", "note": "缺内含物清单"},
    {"rule": "R056", "materials": [M("m1")], "label": "ambiguous", "appl": "unknown", "note": "数量未知"},

    {"rule": "R057", "materials": [M("m1")], "docs": {"m1": doc(carrier="outer_package", fields={"material_code": fv("QLAB-1")})}, "label": "pass", "appl": "applicable", "note": "外包装有编号"},
    {"rule": "R057", "materials": [M("m1")], "docs": {"m1": doc(carrier="attached_tag", attached_to_package=False, fields={"material_code": fv("QLAB-1")})}, "label": "reject", "appl": "applicable", "note": "附签脱离且外包装无编号"},
    {"rule": "R057", "materials": [M("m1")], "docs": {"m1": doc(carrier="attached_tag", attached_to_package=True, fields={"material_code": fv("QLAB-1")})}, "label": "pass", "appl": "applicable", "note": "固定附签连接明确"},
    {"rule": "R057", "materials": [M("m1")], "docs": {"m1": doc(fields={"material_code": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "编号不清"},

    {"rule": "R058", "materials": [M("m1")], "metadata": {"declared_model": "OLD-X"}, "docs": {"m1": doc(fields={"production_date": fv("2025-06-01")})}, "label": "pass", "appl": "applicable", "note": "旧前缀"},
    {"rule": "R058", "materials": [M("m1")], "metadata": {"declared_model": "NEW-X"}, "docs": {"m1": doc(fields={"production_date": fv("2025-06-01")})}, "label": "reject", "appl": "applicable", "note": "前缀不符"},
    {"rule": "R058", "materials": [M("m1")], "metadata": {"declared_model": "NEW-X"}, "docs": {"m1": doc(fields={"production_date": fv("2026-06-01")})}, "label": "pass", "appl": "applicable", "note": "新前缀"},
    {"rule": "R058", "materials": [M("m1")], "docs": {"m1": doc(fields={"production_date": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "生产日期不清"},

    {"rule": "R059", "materials": [M("m1")], "metadata": {"declared_color_name": "蓝色练习盒"}, "label": "ambiguous", "appl": "applicable", "note": "蓝色同级冲突"},
    {"rule": "R059", "materials": [M("m1")], "metadata": {"declared_color_name": "红色"}, "docs": {"m1": doc(shapes={"hollow_circle": sh(True)})}, "label": "pass", "appl": "applicable", "note": "展示空心圆"},
    {"rule": "R059", "materials": [M("m1")], "metadata": {"declared_color_name": "红色"}, "docs": {"m1": doc(shapes={"hollow_circle": sh(False)})}, "label": "reject", "appl": "applicable", "note": "未展示空心圆"},

    {"rule": "R060", "materials": [M("m1")], "docs": {"m1": doc(carrier="outer_package", fields={"model": fv("EDU-753")})}, "label": "pass", "appl": "applicable", "note": "型号在外包装"},
    {"rule": "R060", "materials": [M("m1")], "docs": {"m1": doc(carrier="attached_tag", fields={"model": fv("EDU-753")})}, "label": "ambiguous", "appl": "applicable", "note": "型号仅在附签"},
    {"rule": "R060", "materials": [M("m1")], "docs": {"m1": doc(fields={"model": fu()})}, "label": "ambiguous", "appl": "applicable", "note": "型号不清"},
]


class D3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quackguard-d3-")
        cls.dir = Path(cls.temp.name)
        cls.rules_path = cls.dir / "rules.jsonl"
        rules = [rule for path in RULE_FILES for rule in read_rows(path)]
        cls.rules_path.write_text(
            "".join(json.dumps(rule, ensure_ascii=False) + "\n" for rule in rules),
            encoding="utf-8",
        )
        cls.artifacts = cls.dir / "artifacts"
        cls.build_result = build(cls.rules_path, CONFIG, cls.artifacts)
        cls.policy = load_policy(cls.artifacts)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    # 辅助：运行单条规则
    def run_rule(self, rule_id, materials, documents, context=None,
                 metadata=None, review_date="2026-06-15", statuses=None):
        ctx = {}
        for material in materials:
            mid = material["material_id"]
            ctx[mid] = {
                "face_id": "front",
                "source_type": "photo",
                "is_standard_front_view": True,
            }
        if context:
            ctx.update(context)
        meta = {"material_context": ctx}
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
        gateway = DictGateway(documents, statuses)
        output, traces = review_task(gateway, self.policy, task, "unused.jsonl")
        return output, traces, task

    def test_build_matrix_all_rules_ready(self):
        self.assertEqual(self.build_result["status"], "ready")
        self.assertEqual(
            self.build_result["implementation_status_counts"],
            {"implemented": 62},
        )
        matrix_path = self.artifacts / "engine" / "rule_test_matrix.json"
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        self.assertEqual(len(matrix), 62)
        self.assertTrue(all(row["case_status"] == "ready" for row in matrix))
        self.assertTrue(
            all(row["implementation_status"] == "implemented" for row in matrix)
        )
        r_rules = {row["rule_id"] for row in matrix if row["rule_id"].startswith("R")}
        self.assertEqual(len(r_rules), 60)

    def test_registry_covers_sixty_rules(self):
        self.assertEqual(len(REGISTRY), 60)
        self.assertEqual(set(REGISTRY), {"R%03d" % i for i in range(1, 61)})
        # 每条规则在 policy 中都有 r_rule 实现
        for rule_id in REGISTRY:
            key = rule_id + "@3.0"
            impl = self.policy["implementations"][key]
            self.assertEqual(impl["processor"], "r_rule")
            self.assertTrue(impl["sop"]["family"].startswith("F"))

    def test_synthetic_decision_branches(self):
        for case in CASES:
            with self.subTest(rule=case["rule"], note=case.get("note", "")):
                output, _traces, task = self.run_rule(
                    case["rule"],
                    case["materials"],
                    case.get("docs", {}),
                    context=case.get("context"),
                    metadata=case.get("metadata"),
                    review_date=case.get("review_date", "2026-06-15"),
                )
                self.assertEqual(
                    output["execution_status"], "completed", output.get("reason")
                )
                self.assertEqual(
                    (output["label"], output["applicability"]),
                    (case["label"], case["appl"]),
                    output.get("reason"),
                )
                self.assertEqual(
                    prediction_errors(output, task, self.policy["artifact_version"]),
                    [],
                )

    def test_early_applicability_skips_visual_gateway(self):
        class FailingGateway:
            def messages(self, prompt, task, input_path):
                raise AssertionError("gateway must not be called")

            def complete(self, messages):
                raise AssertionError("gateway must not be called")

        # R055 不适用
        task = {
            "audit_unit_id": "u", "sample_id": "s", "rule_id": "R055",
            "rule_version": "3.0", "review_date": "2026-06-15",
            "metadata": {"category": "其他", "power_mode": "可充电",
                         "material_context": {}},
            "materials": [M("m1")],
        }
        output, traces = review_task(FailingGateway(), self.policy, task, "x.jsonl")
        self.assertEqual(output["applicability"], "not_applicable")
        self.assertEqual(output["label"], "pass")
        self.assertEqual(traces, [])
        self.assertEqual(prediction_errors(output, task), [])

        # R056 单件不适用
        task = dict(task, rule_id="R056",
                    metadata={"declared_quantity": 1, "material_context": {}})
        output, traces = review_task(FailingGateway(), self.policy, task, "x.jsonl")
        self.assertEqual(output["applicability"], "not_applicable")
        self.assertEqual(traces, [])

        # R055 适用条件未知
        task = dict(task, rule_id="R055",
                    metadata={"category": "桌面灯", "material_context": {}})
        output, traces = review_task(FailingGateway(), self.policy, task, "x.jsonl")
        self.assertEqual(output["applicability"], "unknown")
        self.assertEqual(output["label"], "ambiguous")
        self.assertEqual(traces, [])
        self.assertEqual(prediction_errors(output, task), [])

    def test_public_regression_deterministic_labels(self):
        input_path = PUBLIC / "inputs" / "audit_inputs.jsonl"
        tasks = [t for t in read_rows(input_path) if t["rule_id"] in DETERMINISTIC_RULES]
        golds = {
            g["audit_unit_id"]: g
            for g in read_rows(PUBLIC / "references" / "audit_gold.jsonl")
            if g["rule_id"] in DETERMINISTIC_RULES
        }
        self.assertEqual(len(tasks), 32)
        for task in tasks:
            gold = golds[task["audit_unit_id"]]
            with self.subTest(rule=task["rule_id"], case=gold["case_name"]):
                gateway = PublicGateway(task, gold)
                output, traces = review_task(
                    gateway, self.policy, task, input_path
                )
                self.assertEqual(output["execution_status"], "completed")
                self.assertEqual(output["label"], gold["label"], gold["case_name"])
                self.assertEqual(
                    output["applicability"], gold["applicability"], gold["case_name"]
                )
                self.assertEqual(prediction_errors(output, task), [])
                self.assertTrue(all("parsed_facts" in t for t in traces))

    def test_public_regression_contract_only(self):
        input_path = PUBLIC / "inputs" / "audit_inputs.jsonl"
        tasks = [t for t in read_rows(input_path) if t["rule_id"] in CONTRACT_ONLY_RULES]
        golds = {
            g["audit_unit_id"]: g
            for g in read_rows(PUBLIC / "references" / "audit_gold.jsonl")
            if g["rule_id"] in CONTRACT_ONLY_RULES
        }
        self.assertEqual(len(tasks), 12)
        for task in tasks:
            gold = golds[task["audit_unit_id"]]
            with self.subTest(rule=task["rule_id"], case=gold["case_name"]):
                gateway = PublicGateway(task, gold)
                output, _traces = review_task(
                    gateway, self.policy, task, input_path
                )
                self.assertEqual(output["execution_status"], "completed")
                self.assertIn(output["label"], {"pass", "reject", "ambiguous"})
                self.assertEqual(output["applicability"], "applicable")
                self.assertEqual(prediction_errors(output, task), [])

    def test_no_execution_errors_across_all_public_r_cases(self):
        input_path = PUBLIC / "inputs" / "audit_inputs.jsonl"
        tasks = [t for t in read_rows(input_path) if t["rule_id"].startswith("R")]
        gold_rows = {
            g["audit_unit_id"]: g
            for g in read_rows(PUBLIC / "references" / "audit_gold.jsonl")
            if g["rule_id"].startswith("R")
        }
        self.assertEqual(len(tasks), 44)
        for task in tasks:
            gold = gold_rows[task["audit_unit_id"]]
            with self.subTest(unit=task["audit_unit_id"]):
                output, _traces = review_task(
                    PublicGateway(task, gold), self.policy, task, input_path
                )
                self.assertEqual(output["execution_status"], "completed")
                self.assertNotIn(output.get("reason_code"), {None, "execution_error"})

    def test_production_modules_isolated_from_public_data(self):
        forbidden = ("audit_gold", "references", "03_公开开发集", "case_name")
        modules = [
            "rule_common.py", "rule_facts.py", "rule_engine.py", "rule_sop.py",
            "rules_f01.py", "rules_f02.py", "rules_f03.py", "rules_f04.py",
            "rules_f05.py", "rules_f06.py", "rules_f07.py", "rules_f08.py",
            "rules_f09.py", "rules_f10.py", "artifacts.py", "inference.py",
        ]
        for name in modules:
            source = (ROOT / "quackguard" / name).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, name)


if __name__ == "__main__":
    unittest.main()
