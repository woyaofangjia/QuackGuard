"""D5：R019–R042 逐条校准验收。

覆盖 F04（标签位置与载体）、F05（图形与视觉关系）、F06（警示与说明内容）、
F07（参数与数值关系）：

1. 每条规则覆盖 pass/reject，并覆盖 ambiguous 与 unknown；
2. 已证实违反优先于不可确认；
3. 无证据（未提交可核对视图）不得直接判 reject；
4. 构建矩阵 R019–R042 全部 implemented/ready，SOP 原文引用一致；
5. 边界：80mm 附签、中线、1.5倍、1% 容差、100% 合计、30 天期限。
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quackguard.artifacts import build, load_policy
from quackguard.comparison import compare_results
from quackguard.contracts import prediction_errors, read_rows
from quackguard.export import export_results
from quackguard.inference import review_task
from quackguard.rule_facts import (
    extract_facts,
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
JUDGED_RULES = ["R%03d" % number for number in range(19, 43)]


def M(mid, role="package_photos", page=1):
    return {
        "material_id": mid,
        "role": role,
        "relative_path": mid + ".png",
        "media_type": "image/png",
        "page_index": page,
    }


def doc(fields=None, texts=None, shapes=None, **kw):
    document = {"inspection_complete": True, "voided": False}
    if fields:
        document["fields"] = fields
    if texts:
        document["texts"] = texts
    if shapes:
        document["shapes"] = shapes
    document.update(kw)
    return document


def sh(present=None, **extra):
    value = {"present": present}
    value.update(extra)
    return value


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


CASES = [
    # R019 主型号须在正面
    ("R019", [M("m1")], {"m1": doc(fields={"main_model": fv("EDU-753")})}, None, None, True, "pass", "applicable", "正面有主型号"),
    ("R019", [M("f"), M("b")], {"f": doc(), "b": doc(fields={"main_model": fv("EDU-753")})}, {"f": "front", "b": "back"}, None, True, "reject", "applicable", "仅在背面"),
    ("R019", [M("m1")], {"m1": doc()}, None, None, True, "reject", "applicable", "正面完整视图明确缺失"),
    ("R019", [M("m1")], {"m1": doc()}, {"m1": "back"}, None, True, "ambiguous", "applicable", "未提交正面视图"),
    ("R019", [M("f"), M("u")], {"f": doc(), "u": doc(fields={"main_model": fv("EDU-753")})}, {"f": "front", "u": None}, None, True, "ambiguous", "applicable", "面信息未知"),
    ("R019", [M("m1")], {"m1": doc(fields={"main_model": fu()})}, None, None, True, "ambiguous", "applicable", "正面主型号读不清"),

    # R020 序列号须在本体
    ("R020", [M("p", "product_photos")], {"p": doc(fields={"serial_number": fv("S1")})}, None, None, True, "pass", "applicable", "本体有序列号"),
    ("R020", [M("m1")], {"m1": doc(fields={"serial_number": fv("S1")})}, None, None, True, "reject", "applicable", "仅外包装有序列号"),
    ("R020", [M("p", "product_photos")], {"p": doc()}, None, None, True, "reject", "applicable", "本体完整视图缺失序列号"),
    ("R020", [M("p", "product_photos")], {"p": doc(fields={"serial_number": fu()})}, None, None, True, "ambiguous", "applicable", "序列号读不清"),
    ("R020", [M("m1")], {"m1": doc()}, None, None, True, "ambiguous", "applicable", "未提交本体视图"),

    # R021 型号与材料编号同标签
    ("R021", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, fields_on_same_label=True)}, None, None, True, "pass", "applicable", "同标签"),
    ("R021", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, fields_on_same_label=False)}, None, None, True, "reject", "applicable", "同框但分标签"),
    ("R021", [M("m1")], {"m1": doc(carrier="product_body", fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, fields_on_same_label=True)}, None, None, True, "pass", "applicable", "包装照片被误判为 product_body 时按载体回退"),
    ("R021", [M("a"), M("b")], {"a": doc(fields={"model": fv("EDU-753")}), "b": doc(fields={"material_code": fv("QLAB-1")})}, None, None, True, "reject", "applicable", "分散在两张标签"),
    ("R021", [M("m1")], {"m1": doc(fields={"model": fu(), "material_code": fv("QLAB-1")})}, None, None, True, "ambiguous", "applicable", "字段读不清"),
    ("R021", [M("m1", "product_photos")], {"m1": doc()}, None, None, True, "ambiguous", "applicable", "无标签视图"),

    # R022 附签尺寸例外
    ("R022", [M("t", "tag_photos")], {"t": doc(carrier="attached_tag", fields={"material_code": fv("QLAB-1")})}, None, None, True, "pass", "applicable", "小包装可用附签"),
    ("R022", [M("m1")], {"m1": doc(carrier="outer_package", fields={"material_code": fv("QLAB-1")})}, None, None, True, "pass", "applicable", "大包装在外包装"),
    ("R022", [M("t", "tag_photos")], {"t": doc(carrier="attached_tag", fields={"material_code": fv("QLAB-1")})}, None, None, True, "reject", "applicable", "大包装仅在附签"),
    ("R022", [M("m1")], {"m1": doc(fields={"material_code": fu()})}, None, None, True, "ambiguous", "applicable", "编号读不清"),
    ("R022", [M("m1", "product_photos")], {"m1": doc()}, None, None, True, "ambiguous", "applicable", "无标签视图"),
    ("R022", [M("t", "tag_photos")], {"t": doc(carrier="attached_tag", fields={"material_code": fv("QLAB-1")})}, None, None, True, "reject", "applicable", "边界80毫米附签"),
    ("R022", [M("m1")], {"m1": doc(carrier="outer_package", fields={"material_code": fv("QLAB-1")})}, None, None, True, "pass", "applicable", "边界80毫米外包装"),

    # R023 开封切线不得穿过字段
    ("R023", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, cut_line_crosses_label=False)}, None, None, True, "pass", "applicable", "切线未穿过"),
    ("R023", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")}, cut_line_crosses_label=True)}, None, None, True, "reject", "applicable", "切线穿过标签"),
    ("R023", [M("m1")], {"m1": doc(fields={"model": fv("EDU-753"), "material_code": fv("QLAB-1")})}, None, None, True, "ambiguous", "applicable", "切线位置未知"),
    ("R023", [M("m1")], {"m1": doc(fields={"model": fu(), "material_code": fv("QLAB-1")})}, None, None, True, "ambiguous", "applicable", "字段读不清"),
    ("R023", [M("m1", "product_photos")], {"m1": doc()}, None, None, True, "ambiguous", "applicable", "无标签视图"),

    # R024 标志中心位于上半区
    ("R024", [M("m1")], {"m1": doc(center_y_ratio=0.3)}, None, None, True, "pass", "applicable", "中心在上半区"),
    ("R024", [M("m1")], {"m1": doc(center_y_ratio=0.5)}, None, None, True, "pass", "applicable", "正好中线允许"),
    ("R024", [M("m1")], {"m1": doc(center_y_ratio=0.7)}, None, None, True, "reject", "applicable", "中心在下半区"),
    ("R024", [M("m1")], {"m1": doc()}, None, None, True, "ambiguous", "applicable", "中心点不可测"),
    ("R024", [M("m1")], {"m1": doc(center_y_ratio=0.3)}, None, None, False, "ambiguous", "applicable", "非标准正视图"),

    # R025 圆标
    ("R025", [M("m1")], {"m1": doc(shapes={"hollow_circle": sh(True, interior_has_text=False)})}, None, None, True, "pass", "applicable", "空心圆无字"),
    ("R025", [M("m1")], {"m1": doc(shapes={"hollow_circle": sh(True, interior_has_text=True)})}, None, None, True, "reject", "applicable", "圆内有文字"),
    ("R025", [M("m1")], {"m1": doc(shapes={"hollow_circle": sh(False)})}, None, None, True, "reject", "applicable", "实心或未闭合"),
    ("R025", [M("m1")], {"m1": doc(shapes={"hollow_circle": sh(None)})}, None, None, True, "ambiguous", "applicable", "遮挡不可确认"),
    ("R025", [M("m1", "product_photos")], {"m1": doc()}, None, None, True, "ambiguous", "applicable", "无正面视图"),

    # R026 三角点标
    ("R026", [M("m1")], {"m1": doc(shapes={"triangle_border": sh(True), "dot_inside": sh(True, count=1)})}, None, None, True, "pass", "applicable", "一边框一点"),
    ("R026", [M("m1")], {"m1": doc(shapes={"triangle_border": sh(True), "dot_inside": sh(True, count=2)})}, None, None, True, "reject", "applicable", "多个点"),
    ("R026", [M("m1")], {"m1": doc(shapes={"triangle_border": sh(True), "dot_inside": sh(True, count=1, inside=False)})}, None, None, True, "reject", "applicable", "点在框外"),
    ("R026", [M("m1")], {"m1": doc(shapes={"triangle_border": sh(True, center_x=0.5, center_y=0.5, height=0.2), "dot_inside": sh(True, count=1, center_x=0.8, center_y=0.5)})}, None, None, True, "reject", "applicable", "坐标显示点在框外"),
    ("R026", [M("m1")], {"m1": doc(shapes={"triangle_border": sh(True, center_x=0.5, center_y=0.5, height=0.2)})}, None, None, True, "reject", "applicable", "缺少实心点"),
    ("R026", [M("m1")], {"m1": doc(shapes={"triangle_border": sh(None)})}, None, None, True, "ambiguous", "applicable", "边框不可确认"),
    ("R026", [M("m1", "product_photos")], {"m1": doc()}, None, None, True, "ambiguous", "applicable", "无正面视图"),

    # R027 圆标在方标左侧
    ("R027", [M("m1")], {"m1": doc(shapes={"circle_icon": sh(True, center_x=0.2), "square_icon": sh(True, center_x=0.5)})}, None, None, True, "pass", "applicable", "圆在左"),
    ("R027", [M("m1")], {"m1": doc(shapes={"circle_icon": sh(True, center_x=0.5), "square_icon": sh(True, center_x=0.5)})}, None, None, True, "reject", "applicable", "上下排列同横坐标"),
    ("R027", [M("m1")], {"m1": doc(shapes={"circle_icon": sh(False)})}, None, None, True, "reject", "applicable", "缺圆标"),
    ("R027", [M("m1")], {"m1": doc(shapes={"circle_icon": sh(True), "square_icon": sh(True)})}, None, None, True, "ambiguous", "applicable", "横坐标不可确认"),

    # R028 颜色圆点
    ("R028", [M("m1")], {"m1": doc(shapes={"blue_dot": sh(True)})}, None, None, True, "pass", "applicable", "蓝版有蓝点"),
    ("R028", [M("m1")], {"m1": doc(shapes={"blue_dot": sh(False)})}, None, None, True, "reject", "applicable", "蓝版无蓝点"),
    ("R028", [M("m1")], {"m1": doc(shapes={"blue_dot": sh(None)})}, None, None, True, "ambiguous", "applicable", "颜色不可判断"),
    ("R028", [M("m1")], {"m1": doc(shapes={"yellow_dot": sh(True)})}, None, None, True, "pass", "applicable", "黄版有黄点"),
    ("R028", [M("m1", "product_photos")], {"m1": doc()}, None, None, True, "ambiguous", "applicable", "无正面视图"),

    # R029 星形数量恰好三个
    ("R029", [M("m1")], {"m1": doc(shapes={"star": sh(True, count=3)})}, None, None, True, "pass", "applicable", "恰好三个"),
    ("R029", [M("m1")], {"m1": doc(shapes={"star": sh(True, count=2)})}, None, None, True, "reject", "applicable", "两枚"),
    ("R029", [M("m1")], {"m1": doc(shapes={"star": sh(True, count=4)})}, None, None, True, "reject", "applicable", "四枚"),
    ("R029", [M("m1")], {"m1": doc(shapes={"star": sh(True, count=3)}, inspection_complete=False)}, None, None, True, "ambiguous", "applicable", "主面未完整展示"),
    ("R029", [M("m1", "product_photos")], {"m1": doc()}, None, None, True, "ambiguous", "applicable", "无正面视图"),

    # R030 标志高度比例
    ("R030", [M("m1")], {"m1": doc(shapes={"mark_height": sh(None, height=15), "model_text_height": sh(None, height=10)})}, None, None, True, "pass", "applicable", "恰好1.5倍"),
    ("R030", [M("m1")], {"m1": doc(shapes={"mark_height": sh(None, height=14), "model_text_height": sh(None, height=10)})}, None, None, True, "reject", "applicable", "不足1.5倍"),
    ("R030", [M("m1")], {"m1": doc(shapes={"mark_height": sh(None)})}, None, None, True, "ambiguous", "applicable", "高度不可测"),
    ("R030", [M("m1")], {"m1": doc(shapes={"mark_height": sh(None, height=15), "model_text_height": sh(None, height=10)})}, None, None, False, "ambiguous", "applicable", "非标准正视图"),

    # R031 仅供练习使用
    ("R031", [M("m1")], {"m1": doc(texts={"warning_practice": fv("仅供练习使用")})}, None, None, True, "pass", "applicable", "完整六字"),
    ("R031", [M("m1")], {"m1": doc(texts={"warning_practice": fv("仅供练习")})}, None, None, True, "reject", "applicable", "缺字"),
    ("R031", [M("m1")], {"m1": doc(texts={"warning_practice": fu()})}, None, None, True, "ambiguous", "applicable", "读不清"),
    ("R031", [M("m1")], {"m1": doc()}, None, None, True, "reject", "applicable", "完整视图缺失"),
    ("R031", [M("m1", "product_photos")], {}, None, None, True, "ambiguous", "applicable", "无视图"),

    # R032 室内外文案矛盾
    ("R032", [M("m1")], {"m1": doc(texts={"indoor_only": fv("仅室内"), "outdoor_longterm": fv("适合室外长期")})}, None, None, True, "reject", "applicable", "文案矛盾"),
    ("R032", [M("m1")], {"m1": doc(texts={"indoor_only": fv("仅室内")})}, None, None, True, "pass", "applicable", "仅室内"),
    ("R032", [M("m1")], {"m1": doc(texts={"indoor_only": fu()})}, None, None, True, "ambiguous", "applicable", "文案读不清"),

    # R033 组装三步骤
    ("R033", [M("m1")], {"m1": doc(texts={"step1": fv("1 连接底座"), "step2": fv("2 固定主体"), "step3": fv("3 检查连接")})}, None, None, True, "pass", "applicable", "三步齐全"),
    ("R033", [M("m1")], {"m1": doc(texts={"step1": fv("1 连接底座"), "step2": fv("2 检查连接"), "step3": fv("3 固定主体")})}, None, None, True, "reject", "applicable", "次序颠倒"),
    ("R033", [M("m1")], {"m1": doc(texts={"step1": fv("1 连接底座"), "step2": fv("2 固定主体")})}, None, None, True, "reject", "applicable", "缺步骤"),
    ("R033", [M("m1")], {"m1": doc(texts={"step1": fu()})}, None, None, True, "ambiguous", "applicable", "读不清"),
    ("R033", [M("m1", "product_photos")], {}, None, None, True, "ambiguous", "applicable", "无视图"),

    # R034 收纳可拆卸例外
    ("R034", [M("m1")], {"m1": doc(texts={"non_removable_in_use": fv("使用时不可拆卸"), "removable_in_storage": fv("收纳时可拆卸")})}, None, None, True, "pass", "applicable", "两个条件齐全"),
    ("R034", [M("m1")], {"m1": doc(texts={"non_removable_in_use": fv("收纳时可拆卸"), "removable_in_storage": fv("使用时不可拆卸")})}, None, None, True, "reject", "applicable", "条件互换"),
    ("R034", [M("m1")], {"m1": doc(texts={"non_removable_in_use": fu()})}, None, None, True, "ambiguous", "applicable", "读不清"),
    ("R034", [M("m1", "product_photos")], {}, None, None, True, "ambiguous", "applicable", "无视图"),

    # R035 关键句语言一致
    ("R035", [M("m1")], {"m1": doc(texts={"keep_dry_cn": fv("保持干燥"), "keep_dry_en": fv("Keep dry")})}, None, None, True, "pass", "applicable", "双语齐全"),
    ("R035", [M("m1")], {"m1": doc(texts={"keep_dry_en": fv("keep dry")})}, None, None, True, "reject", "applicable", "英文大小写错误"),
    ("R035", [M("m1")], {"m1": doc(texts={"keep_dry_cn": fv("保持干燥")})}, None, None, True, "reject", "applicable", "缺英文"),
    ("R035", [M("m1")], {"m1": doc(texts={"keep_dry_cn": fu()})}, None, None, True, "ambiguous", "applicable", "读不清"),

    # R036 请勿倒置
    ("R036", [M("m1")], {"m1": doc(texts={"do_not_invert": fv("请勿倒置")})}, None, None, True, "pass", "applicable", "含否定词"),
    ("R036", [M("m1")], {"m1": doc(texts={"invert_phrase": fv("倒置")})}, None, None, True, "reject", "applicable", "仅倒置无否定"),
    ("R036", [M("m1")], {"m1": doc(texts={"do_not_invert": fu(), "invert_phrase": fv("倒置")})}, None, None, True, "ambiguous", "applicable", "否定词被遮挡"),
    ("R036", [M("m1")], {"m1": doc()}, None, None, True, "reject", "applicable", "完整视图明确缺失"),

    # R037 包装容量与申报容量一致（1 L = 1000 mL，不允许额外误差）
    ("R037", [M("m1")], {"m1": doc(fields={"capacity": fv("1 L")})}, None, None, True, "pass", "applicable", "1L等于1000mL"),
    ("R037", [M("m1")], {"m1": doc(fields={"capacity": fv("1000 mL")})}, None, None, True, "pass", "applicable", "毫升直接一致"),
    ("R037", [M("m1")], {"m1": doc(fields={"capacity": fv("1.000001 L")})}, None, None, True, "reject", "applicable", "额外误差不允许"),
    ("R037", [M("m1")], {"m1": doc(fields={"capacity": fv("1000 cc")})}, None, None, True, "reject", "applicable", "非L/mL单位"),
    ("R037", [M("m1")], {"m1": doc(fields={"capacity": fu()})}, None, None, True, "ambiguous", "applicable", "容量读不清"),
    ("R037", [M("m1", "product_photos")], {}, None, None, True, "ambiguous", "applicable", "无容量视图"),
    ("R037", [M("m1")], {"m1": doc()}, None, None, True, "reject", "applicable", "完整视图缺失容量"),

    # R038 净重与总重（仅 g/kg，净重不大于总重）
    ("R038", [M("m1")], {"m1": doc(fields={"net_weight": fv("500 g"), "gross_weight": fv("0.6 kg")})}, None, None, True, "pass", "applicable", "净重500克总重600克"),
    ("R038", [M("m1")], {"m1": doc(fields={"net_weight": fv("500 g"), "gross_weight": fv("500 g")})}, None, None, True, "pass", "applicable", "净重等于总重允许"),
    ("R038", [M("m1")], {"m1": doc(fields={"net_weight": fv("0.8 kg"), "gross_weight": fv("700 g")})}, None, None, True, "reject", "applicable", "净重大于总重"),
    ("R038", [M("m1")], {"m1": doc(fields={"net_weight": fv("1 lb"), "gross_weight": fv("600 g")})}, None, None, True, "reject", "applicable", "单位非g/kg"),
    ("R038", [M("m1")], {"m1": doc(fields={"net_weight": fu(), "gross_weight": fv("600 g")})}, None, None, True, "ambiguous", "applicable", "净重读不清"),
    ("R038", [M("m1", "product_photos")], {}, None, None, True, "ambiguous", "applicable", "无视图"),
    ("R038", [M("m1")], {"m1": doc(fields={"net_weight": fv("500 g")})}, None, None, True, "reject", "applicable", "完整视图缺总重"),

    # R039 可调高度区间（端点允许等于，cm/mm 换算）
    ("R039", [M("m1")], {"m1": doc(fields={"height_min": fv("90 cm"), "height_max": fv("110 cm")})}, None, None, True, "pass", "applicable", "区间包含申报高度"),
    ("R039", [M("m1")], {"m1": doc(fields={"height_min": fv("100 cm"), "height_max": fv("110 cm")})}, None, None, True, "pass", "applicable", "端点等于允许"),
    ("R039", [M("m1")], {"m1": doc(fields={"height_min": fv("900 mm"), "height_max": fv("1100 mm")})}, None, None, True, "pass", "applicable", "毫米换算"),
    ("R039", [M("m1")], {"m1": doc(fields={"height_min": fv("101 cm"), "height_max": fv("110 cm")})}, None, None, True, "reject", "applicable", "低于区间下限"),
    ("R039", [M("m1")], {"m1": doc(fields={"height_min": fv("90 cm"), "height_max": fv("1 m")})}, None, None, True, "reject", "applicable", "单位非cm/mm"),
    ("R039", [M("m1")], {"m1": doc(fields={"height_min": fu(), "height_max": fv("110 cm")})}, None, None, True, "ambiguous", "applicable", "高度读不清"),
    ("R039", [M("m1", "product_photos")], {}, None, None, True, "ambiguous", "applicable", "无视图"),

    # R040 三类填充物百分比（恰好三项、0–100、合计100）
    ("R040", [M("m1")], {"m1": doc(fields={"fill_percentages": fv([50, 30, 20])})}, None, None, True, "pass", "applicable", "合计100"),
    ("R040", [M("m1")], {"m1": doc(fields={"fill_percentages": fv([50, 30, 30])})}, None, None, True, "reject", "applicable", "合计110"),
    ("R040", [M("m1")], {"m1": doc(fields={"fill_percentages": fv([-10, 60, 50])})}, None, None, True, "reject", "applicable", "含负数"),
    ("R040", [M("m1")], {"m1": doc(fields={"fill_percentages": fv([60, 40])})}, None, None, True, "reject", "applicable", "仅两类"),
    ("R040", [M("m1")], {"m1": doc(fields={"fill_percentages": fu()})}, None, None, True, "ambiguous", "applicable", "读不清"),
    ("R040", [M("m1", "product_photos")], {}, None, None, True, "ambiguous", "applicable", "无视图"),

    # R041 正反面标称重量差不超过较大值1%（恰好1%允许）
    ("R041", [M("f"), M("b")], {"f": doc(fields={"front_weight": fv("750 g")}), "b": doc(fields={"back_weight": fv("742.5 g")})}, {"f": "front", "b": "back"}, None, True, "pass", "applicable", "恰好1%允许"),
    ("R041", [M("f"), M("b")], {"f": doc(fields={"front_weight": fv("750 g")}), "b": doc(fields={"back_weight": fv("742.4 g")})}, {"f": "front", "b": "back"}, None, True, "reject", "applicable", "超过1%"),
    ("R041", [M("f"), M("b")], {"f": doc(fields={"front_weight": fv("1 kg")}), "b": doc(fields={"back_weight": fv("1000 g")})}, {"f": "front", "b": "back"}, None, True, "pass", "applicable", "千克换算一致"),
    ("R041", [M("f"), M("b")], {"f": doc(fields={"front_weight": fv("750 g")}), "b": doc(fields={"back_weight": fv("742.5 mL")})}, {"f": "front", "b": "back"}, None, True, "reject", "applicable", "单位非g/kg"),
    ("R041", [M("f"), M("b")], {"f": doc(fields={"front_weight": fu()}), "b": doc(fields={"back_weight": fv("750 g")})}, {"f": "front", "b": "back"}, None, True, "ambiguous", "applicable", "正面读不清"),
    ("R041", [M("f")], {"f": doc(fields={"front_weight": fv("750 g")}, inspection_complete=False)}, {"f": "front"}, None, True, "ambiguous", "applicable", "背面未提交"),
    ("R041", [M("f"), M("b")], {"f": doc(fields={"front_weight": fv("750 g")}), "b": doc()}, {"f": "front", "b": "back"}, None, True, "reject", "applicable", "完整背面视图缺重量"),

    # R042 生产后30天内（端点允许，审核日不早于生产日）
    ("R042", [M("m1")], {"m1": doc(fields={"production_date": fv("2026-06-15")})}, None, None, True, "pass", "applicable", "审核当日"),
    ("R042", [M("m1")], {"m1": doc(fields={"production_date": fv("2026-05-16")})}, None, None, True, "pass", "applicable", "第30天端点允许"),
    ("R042", [M("m1")], {"m1": doc(fields={"production_date": fv("2026-05-15")})}, None, None, True, "reject", "applicable", "超过30天"),
    ("R042", [M("m1")], {"m1": doc(fields={"production_date": fv("2026-06-16")})}, None, None, True, "reject", "applicable", "生产日晚于审核日"),
    ("R042", [M("m1")], {"m1": doc(fields={"production_date": fv("2026-02-30")})}, None, None, True, "reject", "applicable", "非法生产日期"),
    ("R042", [M("m1")], {"m1": doc(fields={"production_date": fu()})}, None, None, True, "ambiguous", "applicable", "生产日期读不清"),
    ("R042", [M("m1", "product_photos")], {}, None, None, True, "ambiguous", "applicable", "无视图"),
    ("R042", [M("m1")], {"m1": doc(fields={"production_date": fv("2026-06-15")})}, None, None, True, "ambiguous", "applicable", "审核日期非法"),
]

# 每条用例的元数据（按规则索引，避免重复书写）
METADATA = {
    "R019": None,
    "R020": None,
    "R021": None,
    "R022": {"package_longest_edge_mm": 70},
    "R024": None,
    "R025": {"declared_icon": "圆标"},
    "R026": {"declared_icon": "三角点标"},
    "R028": {"declared_color_name": "蓝色版"},
    "R031": None,
    "R032": None,
    "R033": None,
    "R034": {"declared_detachable_in_storage": True},
    "R035": {"language_mode": "双语"},
    "R036": None,
    "R037": {"declared_capacity_ml": 1000},
    "R039": {"declared_height_cm": 100},
}
# R022 需要按用例覆盖尺寸，这里单独指定
R022_EDGE = {
    "小包装可用附签": 70,
    "大包装在外包装": 100,
    "大包装仅在附签": 100,
    "边界80毫米附签": 80,
    "边界80毫米外包装": 80,
}
# R042 需要按用例覆盖审核基准日期
REVIEW_DATE = {"审核日期非法": "2026-02-30"}


class D5Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quackguard-d5-")
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

    def run_rule(self, rule_id, materials, documents, faces=None, sources=None,
                 standard=True, metadata=None, review_date="2026-06-15"):
        context = {}
        for material in materials:
            mid = material["material_id"]
            face = "front" if faces is None else faces.get(mid, "front")
            source = "photo" if sources is None else sources.get(mid, "photo")
            info = {"is_standard_front_view": standard}
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
        with mock.patch("quackguard.rule_facts.time.sleep"):
            return review_task(DictGateway(documents), self.policy, task, "unused.jsonl") + (task,)

    def _metadata(self, rule_id, note):
        if rule_id == "R022":
            edge = R022_EDGE.get(note, 70)
            return {"package_longest_edge_mm": edge}
        if rule_id == "R028" and note == "黄版有黄点":
            return {"declared_color_name": "黄色版"}
        return METADATA.get(rule_id)

    def test_matrix_f04_f07_ready(self):
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
            expected_family = "F04" if rule_id <= "R024" else (
                "F05" if rule_id <= "R030" else (
                    "F06" if rule_id <= "R036" else "F07"
                )
            )
            self.assertEqual(implementation["sop"]["family"], expected_family, rule_id)
            quotes = json.dumps(
                implementation["sop"]["source_quotes"], ensure_ascii=False
            )
            self.assertIn(self.rules[rule_id]["rule_text"], quotes)

    def test_rule_cases(self):
        for rule_id, materials, documents, faces, sources, standard, label, appl, note in CASES:
            with self.subTest(rule=rule_id, note=note):
                output, traces, task = self.run_rule(
                    rule_id, materials, documents, faces, sources, standard,
                    self._metadata(rule_id, note),
                    REVIEW_DATE.get(note, "2026-06-15"),
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
                main_traces = [
                    trace for trace in traces
                    if trace.get("kind") not in (
                        "matrix_extraction", "label_boundary", "keep_dry"
                    )
                ]
                self.assertEqual(len(main_traces), len(materials))

    def test_every_judged_rule_has_pass_reject_ambiguous(self):
        labels = {}
        for case in CASES:
            labels.setdefault(case[0], set()).add(case[6])
        for rule_id in JUDGED_RULES:
            self.assertIn("pass", labels[rule_id], rule_id)
            self.assertIn("reject", labels[rule_id], rule_id)
            self.assertIn("ambiguous", labels[rule_id], rule_id)


class FieldAliasTest(unittest.TestCase):
    def test_chinese_field_names_are_normalized(self):
        from quackguard.rule_facts import parse_facts

        raw = json.dumps({
            "status": "clear",
            "documents": [{
                "voided": False,
                "inspection_complete": True,
                "carrier": "certificate_page",
                "attached_to_package": None,
                "fields": {
                    "签发日": {"status": "value", "value": "2029-01-01"},
                    "有效起始日": {"status": "value", "value": "2029-01-01"},
                    "有效截止日": {"status": "value", "value": "2029-12-31"},
                },
            }],
        }, ensure_ascii=False)
        facts = parse_facts(raw, M("c", "certificate_pages"))
        fields = facts["documents"][0]["fields"]
        self.assertIn("issued_date", fields)
        self.assertIn("valid_from", fields)
        self.assertIn("valid_to", fields)
        self.assertNotIn("签发日", fields)

    def test_text_keys_in_fields_are_moved(self):
        from quackguard.rule_facts import parse_facts

        raw = json.dumps({
            "status": "clear",
            "documents": [{
                "voided": False,
                "inspection_complete": True,
                "carrier": "outer_package",
                "attached_to_package": None,
                "fields": {"keep_dry_cn": {"status": "value", "value": "保持干燥"}},
                "texts": {},
            }],
        }, ensure_ascii=False)
        facts = parse_facts(raw, M("p", "package_photos"))
        document = facts["documents"][0]
        self.assertNotIn("keep_dry_cn", document["fields"])
        self.assertEqual(document["texts"]["keep_dry_cn"]["value"], "保持干燥")

    def test_label_verdict_is_parsed_and_used(self):
        from quackguard.rule_facts import parse_label_verdict
        from quackguard.rules_f04 import r021

        self.assertEqual(parse_label_verdict('{"verdict":"same"}'), "same")
        with self.assertRaises(ValueError):
            parse_label_verdict('{"verdict":"maybe"}')
        materials = [{
            "material_id": "m1",
            "page_index": 1,
            "role": "package_photos",
            "documents": [],
            "same_label_verdict": "separate",
        }]
        result = r021({}, materials, {})
        self.assertEqual(result["label"], "reject")


class KeepDryTest(unittest.TestCase):
    class Gateway:
        def messages(self, prompt, visual_task, input_path):
            return {
                "prompt": prompt,
                "material_id": visual_task["materials"][0]["material_id"],
            }

        def complete(self, messages):
            if messages["prompt"].startswith("只判断当前图像中是否出现"):
                return (
                    '{"keep_dry_cn":{"status":"value","value":"保持干燥"},'
                    '"keep_dry_en":{"status":"value","value":"Keep dry"}}'
                )
            return json.dumps({
                "status": "clear",
                "documents": [{
                    "voided": False,
                    "inspection_complete": True,
                    "carrier": "outer_package",
                    "attached_to_package": None,
                    "fields": {},
                }],
            }, ensure_ascii=False)

    def test_keep_dry_texts_are_merged(self):
        task = {
            "audit_unit_id": "unit",
            "rule_id": "R035",
            "materials": [M("p", "package_photos")],
        }
        facts, _ = extract_facts(self.Gateway(), task, "unused", "t")
        texts = facts[0]["documents"][0]["texts"]
        self.assertEqual(texts["keep_dry_cn"]["value"], "保持干燥")
        self.assertEqual(texts["keep_dry_en"]["value"], "Keep dry")


class MatrixExtractionTest(unittest.TestCase):
    class Gateway:
        def __init__(self, matrix):
            self.matrix = matrix

        def messages(self, prompt, visual_task, input_path):
            return {
                "prompt": prompt,
                "material_id": visual_task["materials"][0]["material_id"],
            }

        def complete(self, messages):
            if messages["prompt"].startswith("只读取当前图像中的"):
                return self.matrix
            return json.dumps({
                "status": "clear",
                "documents": [{
                    "voided": False,
                    "inspection_complete": True,
                    "carrier": "certificate_page",
                    "attached_to_package": None,
                    "fields": {},
                }],
            }, ensure_ascii=False)

    def run_extract(self, matrix):
        task = {
            "audit_unit_id": "unit",
            "rule_id": "R050",
            "materials": [M("c", "certificate_pages")],
        }
        return extract_facts(self.Gateway(matrix), task, "unused", "t")

    def test_matrix_pairs_are_merged(self):
        facts, traces = self.run_extract(
            '{"status":"clear","pairs":'
            '[{"component_id":"T1","model":"P279","status":"value"}]}'
        )
        components = facts[0]["documents"][0]["components"]
        self.assertEqual(components, [
            {"component_id": "T1", "model": "P279", "status": "value"},
        ])
        kinds = [trace.get("kind") for trace in traces]
        self.assertEqual(kinds.count("matrix_extraction"), 1)

    def test_matrix_failure_is_non_blocking(self):
        from unittest import mock

        with mock.patch("quackguard.rule_facts.time.sleep"):
            facts, traces = self.run_extract("not json")
        self.assertEqual(facts[0]["documents"][0]["components"], [])
        matrix_trace = next(
            trace for trace in traces if trace.get("kind") == "matrix_extraction"
        )
        self.assertIn("error", matrix_trace)

    def test_unreadable_rows_become_unreadable_pairs(self):
        facts, _ = self.run_extract(
            '{"status":"clear","pairs":'
            '[{"component_id":"T1","model":"P279","status":"value"}],'
            '"unreadable_rows":["T3"]}'
        )
        self.assertEqual(facts[0]["documents"][0]["components"], [
            {"component_id": "T1", "model": "P279", "status": "value"},
            {"component_id": "T3", "model": None, "status": "unreadable"},
        ])


class ComparisonExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="quackguard-d5-compare-")
        cls.dir = Path(cls.temp.name)
        cls.tasks_path = PUBLIC / "inputs" / "audit_inputs.jsonl"
        cls.references_path = PUBLIC / "references" / "audit_gold.jsonl"
        tasks = read_rows(cls.tasks_path)
        old = read_rows(PUBLIC / "reference_predictions.jsonl")
        wrong = next(
            row for row in old if row["applicability"] == "applicable"
        )
        remaining = [
            row for row in old
            if row["audit_unit_id"] != wrong["audit_unit_id"]
        ]
        missing = remaining[0]
        version = next(
            row for row in remaining
            if row["audit_unit_id"] != missing["audit_unit_id"]
        )

        cls.old = [
            dict(row, rule_version="9.9")
            if row["audit_unit_id"] == version["audit_unit_id"]
            else row
            for row in old
        ]
        cls.new = [
            dict(row, label="ambiguous")
            if row["audit_unit_id"] == wrong["audit_unit_id"]
            else row
            for row in old
            if row["audit_unit_id"] != missing["audit_unit_id"]
        ]
        cls.old_path = cls.dir / "old.jsonl"
        cls.new_path = cls.dir / "new.jsonl"
        cls.old_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cls.old),
            encoding="utf-8",
        )
        cls.new_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cls.new),
            encoding="utf-8",
        )
        cls.version_unit = version["audit_unit_id"]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_compare_partitions_and_isolates_versions(self):
        out = self.dir / "compare.json"
        result = compare_results(
            self.old_path,
            self.new_path,
            self.tasks_path,
            self.references_path,
            out,
        )
        self.assertEqual(result["all"], 63)
        self.assertEqual(result["counts"]["fixed"], 0)
        self.assertEqual(result["counts"]["new_error"], 2)
        self.assertEqual(result["counts"]["unchanged"], 61)
        self.assertEqual(result["counts"]["version_changed"], 1)
        self.assertEqual(
            result["version_changed"][0]["audit_unit_id"], self.version_unit
        )
        self.assertTrue(Path(result["page"]).is_file())
        self.assertIn("版本结果对比", Path(result["page"]).read_text(encoding="utf-8"))
        self.assertEqual(
            sum(group["all"] for group in result["by_rule_version"].values()), 64
        )

    def test_export_matches_backend_metrics(self):
        out = self.dir / "export.csv"
        result = export_results(
            self.tasks_path,
            PUBLIC / "reference_predictions.jsonl",
            self.references_path,
            out,
        )
        tasks = read_rows(self.tasks_path)
        with out.open(encoding="utf-8", newline="") as handle:
            exported = list(csv.DictReader(handle))
        self.assertEqual(len(exported), len(tasks))
        self.assertEqual(
            [row["audit_unit_id"] for row in exported],
            [task["audit_unit_id"] for task in tasks],
        )
        self.assertEqual(
            [(row["rule_id"], row["rule_version"]) for row in exported],
            [(task["rule_id"], task["rule_version"]) for task in tasks],
        )
        self.assertTrue(all(row["correct"] == "true" for row in exported))
        self.assertEqual(result["rows"], len(tasks))
        self.assertEqual(result["metrics"]["quality"], 1.0)
        self.assertTrue(Path(result["metrics_path"]).is_file())

    def test_cli_compare_and_export(self):
        env = os.environ.copy()
        for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "QUACKGUARD_DB"):
            env.pop(key, None)
        database = self.dir / "compare.db"
        compare_out = self.dir / "cli_compare.json"
        compare = subprocess.run(
            [
                sys.executable, "-B", str(ROOT / "run.py"), "--db", str(database),
                "compare", "--old", str(self.old_path), "--new", str(self.new_path),
                "--input", str(self.tasks_path),
                "--references", str(self.references_path),
                "--out", str(compare_out),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertEqual(compare.returncode, 0, compare.stderr)
        self.assertEqual(json.loads(compare.stdout)["counts"]["new_error"], 2)

        export_out = self.dir / "cli_export.csv"
        export = subprocess.run(
            [
                sys.executable, "-B", str(ROOT / "run.py"), "--db", str(database),
                "export", "--input", str(self.tasks_path),
                "--predictions", str(PUBLIC / "reference_predictions.jsonl"),
                "--references", str(self.references_path),
                "--out", str(export_out),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertEqual(export.returncode, 0, export.stderr)
        self.assertEqual(json.loads(export.stdout)["rows"], 64)


if __name__ == "__main__":
    unittest.main()
