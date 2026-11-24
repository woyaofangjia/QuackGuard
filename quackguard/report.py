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


def write_status_page(path, report, matrix, links=None):
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
    link_rows = "".join(
        '<li><a href="{}">{}</a></li>'.format(_esc(href), _esc(label))
        for label, href in sorted((links or {}).items())
    )
    links_html = (
        "<section><h2>建设记录</h2><ul>{}</ul></section>".format(link_rows)
        if link_rows else ""
    )
    gaps = report.get("semantic_gaps") or []
    gap_rows = "".join(
        "<tr><td>{}@{}</td><td>{}</td></tr>".format(
            _esc(item.get("rule_id")),
            _esc(item.get("rule_version")),
            _esc(item.get("gap")),
        )
        for item in gaps
    ) or '<tr><td colspan="2">无</td></tr>'
    gaps_html = (
        "<section><h2>语义缺口记录</h2>"
        "<table><thead><tr><th>规则@版本</th><th>缺口</th></tr></thead>"
        "<tbody>{}</tbody></table></section>".format(gap_rows)
        if gaps else ""
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
{links_html}
{gaps_html}
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


def _row_value(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return ""


def write_versions_page(path, builds, versions, artifact_version):
    build_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _esc(_row_value(row, "build_id")),
            _esc(_row_value(row, "artifact_version")),
            _esc(_row_value(row, "status")),
            _esc(_row_value(row, "rule_count")),
            _esc(_row_value(row, "active_count")),
            _esc(_row_value(row, "created_at")),
        )
        for row in builds
    ) or '<tr><td colspan="6">无构建记录</td></tr>'
    version_rows = "".join(
        "<tr><td>{}@{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _esc(_row_value(row, "rule_id")),
            _esc(_row_value(row, "rule_version")),
            _esc(_row_value(row, "artifact_version")),
            _esc(str(_row_value(row, "sha256"))[:12]),
            _esc("是" if _row_value(row, "is_active") else "否"),
        )
        for row in versions
    ) or '<tr><td colspan="4">无规则版本</td></tr>'
    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>版本追踪 {_esc(artifact_version)}</title>
<style>{_STYLE}</style></head><body>
<header><h1>版本追踪</h1></header>
<main>
<section class="meta">
<span>当前产物版本：{_esc(artifact_version)}</span>
<span>构建数：{_esc(len(builds))}</span>
<span>规则版本数：{_esc(len(versions))}</span>
</section>
<section><h2>构建历史</h2>
<table><thead><tr><th>构建ID</th><th>产物版本</th><th>状态</th><th>规则数</th><th>活动版本</th><th>创建时间</th></tr></thead>
<tbody>{build_rows}</tbody></table></section>
<section><h2>规则版本追踪</h2>
<table><thead><tr><th>规则@版本</th><th>产物版本</th><th>产物哈希</th><th>活动</th></tr></thead>
<tbody>{version_rows}</tbody></table></section>
</main></body></html>"""
    Path(path).write_text(document, encoding="utf-8")


def _compare_badge(status):
    css = {
        "fixed": "pass",
        "new_error": "reject",
        "version_changed": "ambiguous",
    }.get(status, "error")
    return f'<span class="badge {css}">{_esc(status)}</span>'


def _decision_badge(decision):
    css = {"accept": "pass", "reject": "reject"}.get(decision, "ambiguous")
    return f'<span class="badge {css}">{_esc(decision)}</span>'


def write_iteration_page(path, report):
    counts = report.get("decision_counts", {})
    count_rows = "".join(
        f"<tr><td>{_esc(key)}</td><td>{_esc(value)}</td></tr>"
        for key, value in sorted(counts.items())
    ) or '<tr><td colspan="2">无</td></tr>'
    decision_rows = []
    for item in report.get("decisions", []):
        citations = "".join(
            '<div class="fact">{}@{} · {}</div>'.format(
                _esc(citation.get("rule_id")),
                _esc(citation.get("rule_version")),
                _esc(citation.get("quote")),
            )
            for citation in item.get("rule_citations", [])
        ) or '<span class="muted">无规则引用</span>'
        decision_rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}@{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td></tr>".format(
                _esc(item.get("packet_id")),
                _esc(item.get("audit_unit_id")),
                _esc(item.get("rule_id")),
                _esc(item.get("rule_version")),
                _decision_badge(item.get("decision")),
                _esc(item.get("teacher_proposal")),
                _esc(item.get("reason")) + citations,
            )
        )
    decision_html = "".join(decision_rows) or '<tr><td colspan="6">无反馈项</td></tr>'
    comparison = report.get("comparison", {})
    changed = comparison.get("changed_files", [])
    changed_html = (
        "<ul>{}</ul>".format(
            "".join(f"<li>{_esc(item)}</li>" for item in changed)
        )
        if changed else '<p class="muted">产物逐字节未变化（仅新增迭代报告）</p>'
    )
    changed_rules = report.get("changed_rules", [])
    changed_rule_html = (
        "<section><h2>改动规则与原因</h2>"
        "<table><thead><tr><th>反馈项</th><th>规则@版本</th><th>原因</th></tr></thead>"
        "<tbody>{}</tbody></table></section>".format(
            "".join(
                "<tr><td>{}</td><td>{}@{}</td><td>{}</td></tr>".format(
                    _esc(item.get("packet_id")),
                    _esc(item.get("rule_id")),
                    _esc(item.get("rule_version")),
                    _esc(item.get("reason")),
                )
                for item in changed_rules
            )
        )
        if changed_rules
        else '<section><h2>改动规则与原因</h2>'
        '<p class="muted">无改动规则（no_change）</p></section>'
    )
    basis = report.get("no_change_basis")
    basis_html = (
        "<section><h2>no_change 依据</h2>"
        "<p>{}</p><table><thead><tr><th>检查项</th><th>结果</th></tr></thead>"
        "<tbody>{}</tbody></table></section>".format(
            _esc(basis.get("reason")),
            "".join(
                f"<tr><td>{_esc(key)}</td><td>{_esc(value)}</td></tr>"
                for key, value in basis.items()
                if key != "reason"
            ),
        )
        if basis else ""
    )
    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>迭代报告 {_esc(report.get('feedback_id'))}</title>
<style>{_STYLE}</style></head><body>
<header><h1>安全反馈迭代报告</h1></header>
<main>
<section class="meta">
<span>动作：{_esc(report.get('action'))}</span>
<span>反馈包：{_esc(report.get('feedback_id'))}</span>
<span>baseline哈希：{_esc(report.get('base_artifact_sha256'))}</span>
<span>父版本：{_esc(report.get('parent_artifact_version'))}</span>
<span>候选版本：{_esc(report.get('candidate_artifact_version'))}</span>
<span>baseline未改：{_esc(report.get('baseline_unchanged'))}</span>
</section>
<section><h2>决定统计</h2>
<table><thead><tr><th>决定</th><th>数量</th></tr></thead>
<tbody>{count_rows}</tbody></table></section>
<section><h2>候选比较：改动原因与版本哈希</h2>
<p class="muted">baseline引擎：{_esc(comparison.get('base_engine_sha256'))}
候选引擎：{_esc(comparison.get('candidate_engine_sha256'))}</p>
{changed_html}</section>
{changed_rule_html}
{basis_html}
<section><h2>教师建议逐条处理</h2>
<table><thead><tr><th>反馈项</th><th>审核单元</th><th>规则@版本</th>
<th>决定</th><th>教师建议</th><th>理由与规则依据</th></tr></thead>
<tbody>{decision_html}</tbody></table></section>
</main></body></html>"""
    Path(path).write_text(document, encoding="utf-8")


def write_active_page(path, report):
    gate_rows = "".join(
        f"<tr><td>{_esc(key)}</td><td>{_esc(value)}</td></tr>"
        for key, value in sorted((report.get("gate") or {}).items())
    ) or '<tr><td colspan="2">未提供门槛证据（仅执行结构校验）</td></tr>'
    failures = report.get("gate_failures") or []
    failure_html = (
        '<section><h2>门槛失败</h2><ul>{}</ul></section>'.format(
            "".join(f"<li>{_esc(item)}</li>" for item in failures)
        )
        if failures else ""
    )
    previous = report.get("previous") or {}
    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>活动版本 {_esc(report.get('status'))}</title>
<style>{_STYLE}</style></head><body>
<header><h1>活动版本指针</h1></header>
<main>
<section class="meta">
<span>状态：{_esc(report.get('status'))}</span>
<span>来源：{_esc(report.get('source'))}</span>
<span>产物版本：{_esc(report.get('artifact_version'))}</span>
<span>根目录哈希：{_esc(report.get('root_sha256'))}</span>
<span>活动目录：{_esc(report.get('root_path'))}</span>
<span>上一版本：{_esc(previous.get('artifact_version'))}</span>
<span>上一哈希：{_esc(previous.get('root_sha256'))}</span>
</section>
<section><h2>激活门槛证据</h2>
<table><thead><tr><th>检查项</th><th>值</th></tr></thead>
<tbody>{gate_rows}</tbody></table></section>
{failure_html}
<section><h2>说明</h2><p>{_esc(report.get('reason'))}</p></section>
</main></body></html>"""
    Path(path).write_text(document, encoding="utf-8")


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
