"""离线演示：回放一次真实 glm-4v-flash 审核，零网络、零第三方依赖。

用法：
    python demo.py                 # 默认展示前 3 条
    python demo.py --unit u73820032c
    python demo.py --all
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "delivery" / "model_run_6_p3"
OUTPUTS = RUN / "outputs.jsonl"
TRACES = RUN / "outputs.trace.jsonl"
METRICS = RUN / "metrics.json"

COLORS = {
    "pass": "\033[92m",
    "reject": "\033[91m",
    "ambiguous": "\033[93m",
    "dim": "\033[90m",
    "cyan": "\033[96m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def paint(text, color):
    if not sys.stdout.isatty() and os.name != "nt":
        return text
    return COLORS.get(color, "") + str(text) + COLORS["reset"]


def load_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def field_text(field):
    status = field.get("status")
    if status == "value":
        return str(field.get("value"))
    return "[%s]" % status


def show_facts(traces, unit_id):
    facts = [
        trace for trace in traces
        if trace.get("audit_unit_id") == unit_id and trace.get("parsed_facts")
    ]
    if not facts:
        print(paint("  事实：无（该单元走规则短路或仅不适用）", "dim"))
        return
    for fact in facts:
        print("  %s %s 状态=%s" % (
            paint("材料", "cyan"),
            fact["material_id"][:10],
            fact["parsed_facts"].get("status"),
        ))
        for document in fact["parsed_facts"].get("documents", []):
            print("    文档 carrier=%s 完整=%s" % (
                document.get("carrier"), document.get("inspection_complete"),
            ))
            fields = document.get("fields") or {}
            if fields:
                print("    字段：" + "，".join(
                    "%s=%s" % (name, field_text(field)) for name, field in fields.items()
                ))
            texts = document.get("texts") or {}
            if texts:
                print("    文本：" + "，".join(
                    "%s=%s" % (name, field_text(field)) for name, field in texts.items()
                ))
            components = document.get("components") or []
            if components:
                print("    组件：" + "，".join(
                    "%s→%s" % (item.get("component_id"), field_text(item))
                    for item in components
                ))


def show_output(output, traces):
    label = output.get("label") or "-"
    color = label if label in COLORS else "dim"
    print()
    print(paint("=" * 60, "dim"))
    print("%s  %s@%s" % (
        paint(label.upper(), color),
        output.get("rule_id"), output.get("rule_version"),
    ))
    print("单元：%s" % output.get("audit_unit_id"))
    print("适用性：%s  执行：%s  耗时：%.2fs" % (
        output.get("applicability"),
        output.get("execution_status"),
        (output.get("latency_ms") or 0) / 1000,
    ))
    print("理由：%s" % paint(output.get("reason_code"), "bold"))
    print("      %s" % output.get("reason"))
    print(paint("— 模型事实（只提取，不判决）", "dim"))
    show_facts(traces, output.get("audit_unit_id"))
    evidence = output.get("evidence") or []
    if evidence:
        print(paint("— 证据（规则引用 + 图片来源 + 区域框）", "dim"))
        for item in evidence[:3]:
            print("   %s 页%s 框%s" % (
                item.get("material_id", "")[:10],
                item.get("page_index"),
                item.get("bbox"),
            ))


def main():
    parser = argparse.ArgumentParser(description="QuackGuard 离线审核演示")
    parser.add_argument("--unit", default=None, help="只演示指定 audit_unit_id 前缀")
    parser.add_argument("--all", action="store_true", help="演示全部 64 条")
    parser.add_argument("--limit", type=int, default=3, help="默认演示条数")
    args = parser.parse_args()
    if os.name == "nt":
        os.system("")

    outputs = load_rows(OUTPUTS)
    traces = load_rows(TRACES)
    metrics = json.loads(METRICS.read_text(encoding="utf-8")) if METRICS.exists() else None
    print(paint("QuackGuard 商品审核官 · 离线回放演示", "bold"))
    if metrics:
        audit = metrics["p3"]
        print("正式产物 %s  正确 %d/%d  A=%.4f  U=%.4f  Q=%.4f  执行错误 %d" % (
            audit.get("artifact_version", "av_a489476e2465379a03414735"),
            audit["correct"], audit["records"],
            audit["accuracy"], audit["correct_decisive_coverage"], audit["quality"],
            audit["error"],
        ))
    print(paint("数据来源：delivery/model_run_6_p3（真实模型运行，无网络回放）", "dim"))

    if args.unit:
        selected = [
            row for row in outputs
            if row["audit_unit_id"].startswith(args.unit)
        ]
    elif args.all:
        selected = outputs
    else:
        selected = outputs[: args.limit]
    for output in selected:
        show_output(output, traces)
    print()
    print(paint("演示结束：模型只产出事实，规则引擎负责唯一结论。", "cyan"))


if __name__ == "__main__":
    main()
