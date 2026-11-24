"""构建状态页与审核结果页，只消费已产出的公开结果。

页面为单文件静态HTML，样式内联，不依赖外部资源；证据区域用归一化bbox叠加。
"""
import csv
import html
from pathlib import Path

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

LEDGER_COLUMNS = (
    "rule_id",
    "rule_version",
    "build_status",
    "artifact_version",
    "artifact_sha256",
    "trace_path",
    "shared_capabilities",
    "build_seconds",
    "manual_edit_count",
    "accuracy",
    "machine_review_rate",
)

_STYLE = """
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;
background:#f5f6f8;color:#1f2328;line-height:1.55}
header{background:#1f2937;color:#fff;padding:20px 28px}
header h1{margin:0;font-size:20px}
main{padding:20px 28px 48px;max-width:1200px;margin:0 auto}
section{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:16px 20px;margin-bottom:16px}
h2{font-size:16px;margin:0 0 12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #e3e6ea;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f0f2f5;font-weight:600}
.meta span{display:inline-block;margin-right:20px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px}
.pass{background:#e6f4ea;color:#1a7f37}
.reject{background:#fde8e8;color:#b42318}
.ambiguous{background:#fff4d6;color:#8a5a00}
.error{background:#f2f3f5;color:#57606a}
.muted{color:#6e7781;font-size:12px}
.evidence{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.canvas{position:relative;border:1px solid #d0d7de;border-radius:6px;overflow:hidden;background:#fafafa}
.canvas img{display:block;width:100%;height:auto}
.canvas svg{position:absolute;inset:0;width:100%;height:100%}
.fact{font-family:ui-monospace,Consolas,monospace;font-size:11px;word-break:break-all;
background:#f6f8fa;border-radius:4px;padding:6px;margin-top:6px;white-space:pre-wrap}
details summary{cursor:pointer;font-weight:600;margin-bottom:8px}
"""


def _esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def _badge(label):
    css = label if label in {"pass", "reject", "ambiguous"} else "error"
    return f'<span class="badge {css}">{_esc(label)}</span>'


def write_status_page(path, report, matrix, ledger=None):
    counts = report.get("implementation_status_counts", {})
    failures = report.get("rule_failures") or []
    count_rows = "".join(
        f"<tr><td>{_esc(key)}</td><td>{_esc(value)}</td></tr>"
        for key, value in sorted(counts.items())
    )
    failure_rows = "".join(
        f"<tr><td>{_esc(item.get('line'))}</td><td>{_esc(item.get('error'))}</td></tr>"
        for item in failures
    ) or '<tr><td colspan="2">无</td></tr>'
    matrix_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _esc(row.get("rule_id")),
            _esc(row.get("rule_version")),
            _esc(row.get("implementation_status")),
            _esc(row.get("case_status")),
            _esc(str(row.get("source_sha256", ""))[:12]),
        )
        for row in matrix
    )
    missing = report.get("only_missing") or []
    missing_html = (
        '<section><h2>过滤未命中</h2><p>{}</p></section>'.format(
            _esc(", ".join(str(item) for item in missing))
        )
        if missing else ""
    )
    ledger_html = (
        '<section><h2>建设台账</h2><p><a href="{}">{}</a></p></section>'.format(
            _esc(ledger), _esc(ledger)
        )
        if ledger else ""
    )
    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>构建状态 {_esc(report.get('artifact_version'))}</title>
<style>{_STYLE}</style></head><body>
<header><h1>构建状态</h1></header>
<main>
<section class="meta">
<span>状态：{_badge(report.get('status'))}</span>
<span>协议：{_esc(report.get('protocol_version'))}</span>
<span>产物版本：{_esc(report.get('artifact_version'))}</span>
<span>规则版本数：{_esc(report.get('rule_versions'))}</span>
<span>导入失败：{len(failures)}</span>
</section>
<section><h2>实现状态统计</h2>
<table><thead><tr><th>状态</th><th>数量</th></tr></thead>
<tbody>{count_rows}</tbody></table></section>
<section><h2>规则导入失败</h2>
<table><thead><tr><th>行号</th><th>原因</th></tr></thead>
<tbody>{failure_rows}</tbody></table></section>
{missing_html}
{ledger_html}
<section><h2>规则版本矩阵</h2>
<table><thead><tr><th>规则</th><th>版本</th><th>实现</th><th>用例</th><th>源哈希</th></tr></thead>
<tbody>{matrix_rows}</tbody></table></section>
</main></body></html>"""
    Path(path).write_text(document, encoding="utf-8")


def _material_index(task):
    return {
        (material["material_id"], material["page_index"]): material
        for material in task.get("materials", [])
    }


def _resolve_material(input_path, material):
    relative = material.get("relative_path")
    if not isinstance(relative, str) or not relative:
        return None
    candidate = (Path(input_path).resolve().parent / relative).resolve()
    return candidate if candidate.is_file() else None


def _canvas(material, bboxes, input_path):
    source = _resolve_material(input_path, material)
    name = _esc(material.get("relative_path"))
    if source is None:
        return f'<div class="canvas"><p class="muted" style="padding:8px">未找到图片：{name}</p></div>'
    if source.suffix.lower() not in _IMAGE_SUFFIXES:
        return f'<div class="canvas"><p class="muted" style="padding:8px">材料：{name}</p></div>'
    rects = "".join(
        '<rect x="{:.4f}" y="{:.4f}" width="{:.4f}" height="{:.4f}" '
        'fill="rgba(220,38,38,0.12)" stroke="#dc2626" stroke-width="0.006"/>'.format(
            bbox[0], bbox[1], max(bbox[2] - bbox[0], 0.001), max(bbox[3] - bbox[1], 0.001)
        )
        for bbox in bboxes
        if isinstance(bbox, list) and len(bbox) == 4
    )
    return (
        '<div class="canvas">'
        f'<img src="{_esc(source.as_uri())}" alt="{name}">'
        '<svg viewBox="0 0 1 1" preserveAspectRatio="none">'
        f'{rects}</svg></div>'
    )


def _evidence_html(input_path, task, evidence):
    if not evidence:
        return '<p class="muted">无图像证据</p>'
    index = _material_index(task)
    grouped = []
    for item in evidence:
        key = (item.get("material_id"), item.get("page_index"))
        material = index.get(key)
        if material is None:
            continue
        bbox = item.get("bbox") if isinstance(item.get("bbox"), list) else []
        grouped.append((material, bbox, item.get("fact")))
    cards = []
    for material, bbox, fact in grouped:
        cards.append(
            "<div>"
            + _canvas(material, [bbox] if bbox else [], input_path)
            + f'<div class="fact">{_esc(fact)}</div>'
            + "</div>"
        )
    return '<div class="evidence">' + "".join(cards) + "</div>"


def write_review_page(path, policy, tasks, outputs, input_path):
    task_index = {task["audit_unit_id"]: task for task in tasks}
    sections = []
    for output in outputs:
        task = task_index.get(output.get("audit_unit_id"), {"materials": []})
        key = str(output.get("rule_id")) + "@" + str(output.get("rule_version"))
        rule = policy.get("rules", {}).get(key, {})
        label = output.get("label")
        badge = _badge(label if label else output.get("execution_status"))
        sections.append(
            "<section>"
            f"<h2>{_esc(key)} · {_esc(output.get('audit_unit_id'))} {badge}</h2>"
            '<p class="meta">'
            f"<span>适用性：{_esc(output.get('applicability'))}</span>"
            f"<span>原因码：{_esc(output.get('reason_code'))}</span>"
            "</p>"
            f"<p>审核依据：{_esc(output.get('reason'))}</p>"
            "<details><summary>规则原文</summary>"
            f'<div class="fact">{_esc(rule.get("rule_text"))}</div></details>'
            f"{_evidence_html(input_path, task, output.get('evidence') or [])}"
            "</section>"
        )
    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>审核结果</title>
<style>{_STYLE}</style></head><body>
<header><h1>审核结果与证据</h1></header>
<main>{''.join(sections) or '<section><p>无审核记录</p></section>'}</main>
</body></html>"""
    Path(path).write_text(document, encoding="utf-8")


def write_ledger(path, entries):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(LEDGER_COLUMNS)
        for entry in entries:
            writer.writerow([entry.get(column, "") for column in LEDGER_COLUMNS])


def _compare_badge(status):
    css = {
        "fixed": "pass",
        "new_error": "reject",
        "version_changed": "ambiguous",
    }.get(status, "error")
    return f'<span class="badge {css}">{_esc(status)}</span>'


def write_compare_page(path, result):
    counts = result.get("counts", {})
    count_rows = "".join(
        f"<tr><td>{_esc(key)}</td><td>{_esc(counts.get(key, 0))}</td></tr>"
        for key in ("fixed", "new_error", "unchanged", "version_changed")
    )
    group_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _esc(key),
            _esc(group.get("all")),
            _esc(group.get("fixed")),
            _esc(group.get("new_error")),
            _esc(group.get("unchanged")),
            _esc(group.get("version_changed")),
        )
        for key, group in sorted(result.get("by_rule_version", {}).items())
    )
    unit_rows = "".join(
        "<tr><td>{}</td><td>{}@{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _esc(row.get("audit_unit_id")),
            _esc(row.get("rule_id")),
            _esc(row.get("rule_version")),
            _esc(row.get("old_outcome")),
            _esc(row.get("new_outcome")),
            _compare_badge(row.get("status")),
        )
        for row in result.get("rows", [])
    )
    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>版本结果对比</title>
<style>{_STYLE}</style></head><body>
<header><h1>版本结果对比</h1></header>
<main>
<section class="meta">
<span>对比单元：{_esc(result.get('all'))}</span>
<span>旧结果：{_esc(result.get('old'))}</span>
<span>新结果：{_esc(result.get('new'))}</span>
</section>
<section><h2>对比数量</h2>
<table><thead><tr><th>状态</th><th>数量</th></tr></thead>
<tbody>{count_rows}</tbody></table></section>
<section><h2>按规则版本统计（不混算）</h2>
<table><thead><tr><th>规则@版本</th><th>总数</th><th>修复</th><th>新增错误</th><th>未变化</th><th>版本不一致</th></tr></thead>
<tbody>{group_rows}</tbody></table></section>
<section><h2>逐单元结果</h2>
<table><thead><tr><th>审核单元</th><th>规则@版本</th><th>旧结论</th><th>新结论</th><th>状态</th></tr></thead>
<tbody>{unit_rows}</tbody></table></section>
</main></body></html>"""
    Path(path).write_text(document, encoding="utf-8")
