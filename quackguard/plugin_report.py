"""插件配置与运行报告页；只消费字典，不读取数据库。"""

import html
from pathlib import Path

_STYLE = """
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;
background:#f5f6f8;color:#1f2328;line-height:1.55}
header{background:#1f2937;color:#fff;padding:20px 28px}
header h1{margin:0;font-size:20px}
main{padding:20px 28px 48px;max-width:1100px;margin:0 auto}
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
.fact{font-family:ui-monospace,Consolas,monospace;font-size:12px;word-break:break-all;
background:#f6f8fa;border-radius:4px;padding:6px;margin-top:6px;white-space:pre-wrap}
"""


def _esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def _value(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return ""


def _badge(status):
    css = {
        "ok": "pass",
        "failed": "reject",
        "partial": "ambiguous",
        "disabled": "error",
    }.get(status, "error")
    return f'<span class="badge {css}">{_esc(status)}</span>'


def _metrics_text(metrics):
    if not isinstance(metrics, dict) or not metrics:
        return '<span class="muted">无指标</span>'
    parts = " ".join(f"{key}={value}" for key, value in sorted(metrics.items()))
    return f'<span class="fact">{_esc(parts)}</span>'


def _registration_rows(plugins):
    rows = []
    for plugin in plugins:
        enabled = bool(_value(plugin, "enabled"))
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _esc(_value(plugin, "plugin_id")),
                _esc(_value(plugin, "plugin_version")),
                _esc(_value(plugin, "schema_version")),
                _esc(_value(plugin, "entrypoint")),
                _esc("启用" if enabled else "禁用"),
            )
        )
    return "".join(rows) or '<tr><td colspan="5">尚未注册插件</td></tr>'


def _error_text(errors):
    return "<br>".join(
        "{}: {}".format(_esc(item.get("code")), _esc(item.get("detail")))
        for item in errors
    )


def _result_rows(evaluation):
    rows = []
    for plugin in evaluation.get("plugins", []):
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _esc(plugin.get("plugin_id")),
                _esc(plugin.get("plugin_version")),
                _badge(plugin.get("status")),
                _esc(plugin.get("invocations")),
                _metrics_text(plugin.get("metrics")),
                _error_text(plugin.get("errors") or [])
                or '<span class="muted">无</span>',
            )
        )
    return "".join(rows)


def _failure_guidance(evaluation):
    failed = [
        plugin
        for plugin in evaluation.get("plugins", [])
        if plugin.get("status") == "failed"
    ]
    if not failed:
        return ""
    rows = "".join(
        "<tr><td>{}@{}</td><td>{}</td>"
        "<td>该插件失败不影响其他插件；检查输出契约后重试，或禁用该插件继续评估。</td></tr>".format(
            _esc(item.get("plugin_id")),
            _esc(item.get("plugin_version")),
            _error_text(item.get("errors") or []),
        )
        for item in failed
    )
    return (
        "<section><h2>异常操作提示</h2>"
        "<table><thead><tr><th>失败对象</th><th>原因</th><th>可继续的操作</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></section>"
    )


def write_plugins_page(path, plugins, evaluation=None, title="插件配置"):
    if evaluation is None:
        meta = (
            f"<span>已注册：{_esc(len(plugins))}</span>"
            "<span>最近一次评估：未运行</span>"
        )
        result_html = (
            '<section><h2>运行报告</h2>'
            '<p class="muted">运行 evaluate --manifest 后在此显示各插件状态。</p></section>'
        )
        guidance = ""
    else:
        meta = (
            f"<span>状态：{_badge(evaluation.get('status'))}</span>"
            f"<span>插件数：{_esc(evaluation.get('plugin_count'))}</span>"
            f"<span>启用：{_esc(evaluation.get('enabled_count'))}</span>"
            f"<span>禁用：{_esc(evaluation.get('disabled_count'))}</span>"
            f"<span>调用次数：{_esc(evaluation.get('invocations'))}</span>"
            f"<span>错误数：{_esc(evaluation.get('errors'))}</span>"
        )
        result_html = (
            "<section><h2>运行报告</h2>"
            "<table><thead><tr><th>插件</th><th>版本</th><th>状态</th>"
            "<th>调用次数</th><th>指标</th><th>错误</th></tr></thead>"
            f"<tbody>{_result_rows(evaluation)}</tbody></table></section>"
        )
        guidance = _failure_guidance(evaluation)

    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{_esc(title)}</title>
<style>{_STYLE}</style></head><body>
<header><h1>{_esc(title)}</h1></header>
<main>
<section class="meta">{meta}</section>
<section><h2>插件注册</h2>
<table><thead><tr><th>插件ID</th><th>版本</th><th>输入协议</th>
<th>入口</th><th>状态</th></tr></thead>
<tbody>{_registration_rows(plugins)}</tbody></table></section>
{result_html}
{guidance}
</main></body></html>"""
    Path(path).write_text(document, encoding="utf-8")
