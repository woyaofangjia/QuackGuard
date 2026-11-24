"""D9 回归报告页：只渲染已落盘的回归结果字典。"""
import html
from pathlib import Path

from .report import _STYLE


def _esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def _table(headers, rows):
    head = "".join(f"<th>{_esc(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(value)}</td>" for value in row) + "</tr>"
        for row in rows
    ) or f'<tr><td colspan="{len(headers)}">无</td></tr>'
    return (
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


def _metrics_table(metrics):
    labels = (
        ("A 联合准确率", "accuracy"),
        ("U 决定性覆盖", "correct_decisive_coverage"),
        ("Q 质量", "quality"),
        ("M 机审率", "machine_review_rate"),
        ("机审准确率", "machine_accuracy"),
        ("合法数", "correct"),
        ("总数", "all"),
        ("缺失", "missing"),
        ("非法", "invalid"),
        ("额外ID", "extraneous"),
    )
    return _table(("指标", "值"), [(name, metrics.get(key)) for name, key in labels])


def _freeze_rows(freeze):
    return [
        ("规则文件哈希", str(freeze.get("rules_sha256"))[:16]),
        ("配置哈希", str(freeze.get("config_sha256"))[:16]),
        ("提示哈希", str(freeze.get("prompt_sha256"))[:16]),
        ("参考基线哈希", str(freeze.get("baseline_sha256"))[:16]),
        ("插件清单哈希", str(freeze.get("plugin_manifest_sha256"))[:16]),
        ("模型", freeze.get("model")),
        ("第三方依赖", freeze.get("dependencies", {}).get("third_party")),
    ]


def _reliability_rows(report):
    selective = report["selective_rebuild"]
    persistence = report["persistence"]
    resume = report["resume"]
    export = report["export_consistency"]
    return [
        (
            "选择性重建",
            f"请求{selective['requested']}条/构建{selective['built']}条；"
            f"缺失{len(selective['missing'])}，额外{len(selective['extra'])}；"
            f"源哈希一致={selective['source_hash_match']}",
        ),
        (
            "重启持久化",
            f"构建={persistence['build_persisted']}，"
            f"活动版本={persistence['active_persisted']}，"
            f"插件禁用={persistence['plugin_disabled_persisted']}",
        ),
        (
            "中断恢复",
            f"状态={resume['status']}，跳过={resume['skipped_rule_versions']}，"
            f"重试后继={resume['retry_successor_linked']}",
        ),
        (
            "导出一致性",
            f"行数{export['rows']}；重复一致={export['repeat_identical']}，"
            f"指标一致={export['metric_match']}，"
            f"ID唯一={export['unique_ids']}，"
            f"正确标记={export['correct_flags']}",
        ),
    ]


def write_regression_page(path, report):
    public = report["public_set"]
    integrity = public["integrity"]
    baseline = report["reference_baseline"]
    control = report["control_group"]
    branches = report["branches"]
    annotations = report["annotations"]
    faults = report["plugin_faults"]
    checks = report["checks"]

    check_rows = [
        (name, "通过" if value else "失败")
        for name, value in checks.items()
    ]
    branch_rows = [
        (rule, group["passed"], group["all"])
        for rule, group in sorted(branches.get("per_rule", {}).items())
    ]
    integrity_rows = [
        ("缺失", integrity["missing"]),
        ("重复", integrity["duplicate"]),
        ("额外ID", integrity["extraneous"]),
        ("未识别记录", integrity["unidentified"]),
        ("错版本", integrity["wrong_version"]),
        ("其他非法", integrity["other_invalid"]),
        ("清零", integrity["ok"]),
    ]
    control_rows = [
        ("期望", control["expected"]),
        ("实测", {key: control["measured"].get(key) for key in control["expected"]}),
        ("匹配", control["matched"]),
    ]
    annotation_rows = [
        ("目标数", annotations.get("targets")),
        ("产出数", annotations.get("produced")),
        ("正确数", annotations.get("correct")),
        ("得分", annotations.get("score")),
        ("失败数", len(annotations.get("failures") or [])),
    ]
    fault_rows = [
        ("健康插件", faults["healthy_status"]),
        ("崩溃插件", f"{faults['crash_status']} / {faults['crash_error']}"),
        ("超时插件", f"{faults['timeout_status']} / {faults['timeout_error']}"),
        ("禁用状态", faults["disabled_status"]),
        ("禁用调用数", faults["disabled_invocations"]),
        ("输入哈希不变", faults["input_unchanged"]),
    ]

    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>D9 全量回归 {_esc(report.get('status'))}</title>
<style>{_STYLE}</style></head><body>
<header><h1>D9 全量回归与干净环境验证</h1></header>
<main>
<section class="meta">
<span>状态：{_esc(report.get('status'))}</span>
<span>产物版本：{_esc(report.get('artifact_version'))}</span>
<span>规则版本数：{_esc(report.get('rule_versions'))}</span>
</section>
<section><h2>冻结配置</h2>{_table(("项", "值"), _freeze_rows(report['freeze']))}</section>
<section><h2>系统输出（确定性事实回归，非端到端视觉成绩）</h2>
{_metrics_table(public['metrics'])}</section>
<section><h2>完整性清零</h2>
{_table(("项", "值"), integrity_rows)}</section>
<section><h2>参考输出基线（不计入系统成绩）</h2>
<p class="muted">{_esc(baseline['note'])}</p>
{_metrics_table(baseline['metrics'])}</section>
<section><h2>对照组（L-FUNC-02，不计入系统成绩）</h2>
<p class="muted">{_esc(control['note'])}</p>
{_table(("项", "值"), control_rows)}</section>
<section><h2>自建规则分支</h2>
<p>用例={_esc(branches['cases'])}，通过={_esc(branches['passed'])}，
失败={_esc(branches['failed'])}</p>
{_table(("规则", "通过", "总数"), branch_rows)}</section>
<section><h2>标注样例</h2>
{_table(("项", "值"), annotation_rows)}</section>
<section><h2>插件故障隔离</h2>
{_table(("项", "值"), fault_rows)}</section>
<section><h2>可靠性检查</h2>
{_table(("检查", "结果"), _reliability_rows(report))}</section>
<section><h2>检查汇总</h2>
{_table(("检查", "结果"), check_rows)}</section>
</main></body></html>"""
    Path(path).write_text(document, encoding="utf-8")
