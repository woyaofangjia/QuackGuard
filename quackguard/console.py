"""本地可操作控制台：给非技术评审提供统一入口。

只使用标准库；所有动作复用既有 CLI 与产物，不改变审核逻辑。
单一工作区即可完成导入、建设、审核、查证据、比较、恢复与回滚。
"""
import contextlib
import io
import json
import mimetypes
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cli import main as cli_main
from .errors import AuditError
from .io_utils import load_json, numbered_rows_lenient, rows, write_json

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "O2_AI商品审核官_资料包"
PUBLIC = PACK / "03_公开开发集"
TASKS = PUBLIC / "inputs" / "audit_inputs.jsonl"
REFERENCES = PUBLIC / "references" / "audit_gold.jsonl"
ANNOTATION = PUBLIC / "标注样例"
ANNOTATION_TASKS = ANNOTATION / "inputs" / "tasks.jsonl"
ANNOTATION_TARGETS = ANNOTATION / "inputs" / "targets.jsonl"
_DELIVERY_CONFIG = ROOT / "delivery" / "config.json"
CONFIG = (
    _DELIVERY_CONFIG
    if _DELIVERY_CONFIG.exists()
    else PACK / "04_最小对接样例" / "config.json"
)
RULE_FILES = (
    PACK / "01_规则包" / "基础规则版本.jsonl",
    PACK / "01_规则包" / "进阶规则60条.jsonl",
)
MANIFEST = ROOT / "quackguard" / "plugin_manifest.json"


def paths(workspace):
    root = Path(workspace)
    return {
        "root": root,
        "rules": root / "rules.jsonl",
        "artifacts": root / "artifacts",
        "outputs": root / "outputs.jsonl",
        "annotations": root / "annotations.jsonl",
        "candidate": root / "candidate",
        "feedback": root / "feedback.json",
        "evaluation": root / "evaluation.json",
        "compare": root / "compare.json",
        "plugins": root / "plugins.json",
        "activate": root / "activate.json",
        "rollback": root / "rollback.json",
        "export": root / "export.json",
        "db": root / "state.sqlite3",
        "report": root / "report.json",
    }


def _run(argv):
    """在进程内调用既有 CLI，返回 (退出码, 输出载荷, 错误文本)。"""
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main([str(item) for item in argv])
    text = out.getvalue().strip()
    payload = None
    if text:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"raw": text}
    return code, payload, err.getvalue().strip()


def _require_file(path, label):
    if not Path(path).exists():
        raise AuditError("missing_input", f"{label}不存在：{path}")
    return Path(path)


def state(workspace):
    p = paths(workspace)
    version = None
    rules = 0
    policy_path = p["artifacts"] / "engine" / "policy.json"
    if policy_path.exists():
        policy = load_json(policy_path)
        version = policy.get("artifact_version")
        rules = len(policy.get("rules", {}))
    outputs = rows(p["outputs"], required=False) if p["outputs"].exists() else []
    annotations = (
        rows(p["annotations"], required=False)
        if p["annotations"].exists() else []
    )
    errors = sum(row.get("execution_status") == "error" for row in outputs)
    completed = sum(row.get("execution_status") == "completed" for row in outputs)
    plugins = []
    if p["plugins"].exists():
        plugins = load_json(p["plugins"]).get("plugins", [])
    return {
        "workspace": str(p["root"]),
        "artifact_version": version,
        "rule_versions": rules,
        "records": len(outputs),
        "completed": completed,
        "execution_errors": errors,
        "annotations": len(annotations),
        "candidate": p["candidate"].exists(),
        "has_rules": p["rules"].exists(),
        "has_artifacts": p["artifacts"].exists(),
        "outputs": str(p["outputs"]) if p["outputs"].exists() else None,
        "plugins": plugins,
    }


def units(workspace):
    p = paths(workspace)
    if not p["outputs"].exists():
        return {"records": 0, "units": []}
    result = []
    for row in rows(p["outputs"]):
        result.append({
            "audit_unit_id": row.get("audit_unit_id"),
            "rule_id": row.get("rule_id"),
            "rule_version": row.get("rule_version"),
            "label": row.get("label"),
            "applicability": row.get("applicability"),
            "execution_status": row.get("execution_status"),
            "reason_code": row.get("reason_code"),
            "artifact_version": row.get("artifact_version"),
        })
    return {"records": len(result), "units": result}


def _task_index():
    return {task["audit_unit_id"]: task for task in rows(TASKS)}


def unit_detail(workspace, unit_id):
    p = paths(workspace)
    if not p["outputs"].exists():
        raise AuditError("missing_outputs", "尚未运行审核")
    output = next(
        (row for row in rows(p["outputs"]) if row.get("audit_unit_id") == unit_id),
        None,
    )
    if output is None:
        raise AuditError("unknown_unit", str(unit_id))
    task = _task_index().get(unit_id)
    policy_path = p["artifacts"] / "engine" / "policy.json"
    policy = load_json(policy_path) if policy_path.exists() else {}
    key = str(output.get("rule_id")) + "@" + str(output.get("rule_version"))
    rule = policy.get("rules", {}).get(key, {})
    materials = []
    for material in (task or {}).get("materials", []):
        materials.append({
            "material_id": material["material_id"],
            "page_index": material["page_index"],
            "role": material["role"],
            "relative_path": material["relative_path"],
        })
    return {
        "output": output,
        "rule": {
            "rule_id": rule.get("rule_id"),
            "version": rule.get("version"),
            "rule_text": rule.get("rule_text"),
            "source_sha256": rule.get("source_sha256"),
        },
        "materials": materials,
        "artifact_version": output.get("artifact_version"),
        "trace_id": output.get("trace_id"),
    }


def import_rules(workspace, text, use_pack):
    p = paths(workspace)
    p["root"].mkdir(parents=True, exist_ok=True)
    if use_pack:
        text = "".join(
            path.read_text(encoding="utf-8") for path in RULE_FILES
        )
    if not isinstance(text, str) or not text.strip():
        raise AuditError("empty_rules", "没有可导入的规则文本")
    p["rules"].write_text(text, encoding="utf-8")
    records, failures = numbered_rows_lenient(p["rules"])
    seen = {}
    duplicates = []
    for line, rule in records:
        if not isinstance(rule, dict):
            failures.append({"line": line, "error": "规则不是JSON对象"})
            continue
        key = str(rule.get("rule_id")) + "@" + str(rule.get("version"))
        if key in seen:
            duplicates.append({"line": line, "rule": key, "first_line": seen[key]})
        else:
            seen[key] = line
    return {
        "records": len(records),
        "unique": len(seen),
        "failures": failures,
        "duplicates": duplicates,
        "rules_path": str(p["rules"]),
    }


def _feedback_packet(workspace, payload):
    p = paths(workspace)
    unit_id = payload.get("audit_unit_id")
    task = _task_index().get(unit_id)
    if task is None:
        raise AuditError("unknown_unit", str(unit_id))
    base_hash = _tree_hash(p["artifacts"])
    packet = {
        "schema_version": "1.3.1",
        "feedback_id": payload.get("feedback_id") or "console-feedback",
        "base_artifact_sha256": base_hash,
        "items": [],
    }
    packet["items"].append({
        "packet_id": "console-1",
        "audit_unit_id": unit_id,
        "teacher_proposal": payload.get("proposal") or "确认按原文执行。",
        "teacher_source": payload.get("source")
        or task["rule_id"] + "@" + task["rule_version"],
    })
    write_json(p["feedback"], packet)
    return p["feedback"]


def _tree_hash(root):
    from .artifact_hash import tree_digest

    return tree_digest(root)


def _action(workspace, action, payload):
    p = paths(workspace)
    p["root"].mkdir(parents=True, exist_ok=True)
    db = p["db"]
    base = ["--db", db]

    if action == "import":
        return import_rules(
            workspace, payload.get("text"), payload.get("use_pack")
        )
    if action == "build":
        _require_file(p["rules"], "规则文件")
        argv = base + [
            "build", "--rules", p["rules"], "--config", CONFIG,
            "--out", p["artifacts"],
        ]
        if payload.get("only"):
            argv += ["--only", payload["only"]]
        if payload.get("resume") or p["artifacts"].exists():
            argv.append("--resume")
        return _result(argv)
    if action == "infer":
        _require_file(p["artifacts"] / "engine" / "policy.json", "构建产物")
        input_path = payload.get("input") or str(TASKS)
        _require_file(input_path, "审核输入")
        return _result(base + [
            "infer", "--input", input_path, "--artifacts", p["artifacts"],
            "--out", p["outputs"],
        ])
    if action == "annotate":
        _require_file(p["artifacts"] / "engine" / "policy.json", "构建产物")
        return _result(base + [
            "annotate",
            "--input", payload.get("input") or str(ANNOTATION_TASKS),
            "--targets", payload.get("targets") or str(ANNOTATION_TARGETS),
            "--artifacts", p["artifacts"], "--out", p["annotations"],
        ])
    if action == "iterate":
        _require_file(p["artifacts"] / "engine" / "policy.json", "构建产物")
        if p["candidate"].exists() and payload.get("reset_candidate"):
            shutil.rmtree(p["candidate"])
        feedback = _feedback_packet(workspace, payload)
        return _result(base + [
            "iterate", "--base", p["artifacts"], "--feedback", feedback,
            "--input", payload.get("input") or str(TASKS),
            "--out", p["candidate"],
        ])
    if action == "evaluate":
        _require_file(p["outputs"], "审核输出")
        return _result(base + [
            "evaluate", "--manifest", MANIFEST,
            "--inputs", payload.get("inputs") or str(TASKS),
            "--outputs", p["outputs"],
            "--references", payload.get("references") or str(REFERENCES),
            "--out", p["evaluation"],
        ])
    if action == "compare":
        _require_file(p["artifacts"] / "engine" / "policy.json", "baseline产物")
        _require_file(p["candidate"] / "engine" / "policy.json", "候选产物")
        return _result([
            "compare", "--old", p["artifacts"], "--new", p["candidate"],
            "--input", payload.get("input") or str(TASKS),
            "--references", payload.get("references") or str(REFERENCES),
            "--out", p["compare"],
        ])
    if action == "activate":
        _require_file(p["candidate"] / "engine" / "policy.json", "候选产物")
        return _result(base + [
            "activate", "--baseline", p["artifacts"],
            "--candidate", p["candidate"], "--out", p["activate"],
        ])
    if action == "rollback":
        _require_file(p["artifacts"] / "engine" / "policy.json", "基线产物")
        argv = base + [
            "rollback", "--to", p["artifacts"], "--out", p["rollback"],
        ]
        if payload.get("expect_hash"):
            argv += ["--expect-hash", payload["expect_hash"]]
        return _result(argv)
    if action == "plugins":
        return _result(base + [
            "plugins", "--manifest", MANIFEST, "--out", p["plugins"],
        ])
    if action == "plugin_state":
        if not payload.get("plugin_id") or not payload.get("plugin_version"):
            raise AuditError("missing_plugin", "需要plugin_id与plugin_version")
        return _result(base + [
            "plugin-state",
            "--plugin-id", payload["plugin_id"],
            "--plugin-version", payload["plugin_version"],
            "--state", payload.get("state") or "disabled",
            "--out", p["plugins"],
        ])
    if action == "retry":
        if not payload.get("task_id"):
            raise AuditError("missing_task", "需要task_id")
        return _result(base + ["retry", "--task-id", payload["task_id"]])
    if action == "export":
        _require_file(p["outputs"], "审核输出")
        return _result([
            "export", "--input", payload.get("input") or str(TASKS),
            "--predictions", p["outputs"],
            "--references", payload.get("references") or str(REFERENCES),
            "--out", p["export"],
        ])
    raise AuditError("unknown_action", str(action))


def _result(argv):
    code, payload, err = _run(argv)
    return {"code": code, "payload": payload, "error": err}


PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QuackGuard 商品审核官 · 控制台</title>
<style>
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
background:#f4f6f8;color:#1f2328}
header{background:#111827;color:#fff;padding:14px 24px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
header h1{font-size:17px;margin:0}
header .sp{flex:1}
.badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:12px;background:#374151}
button{font:inherit;padding:6px 12px;border:1px solid #c9ced6;background:#fff;border-radius:7px;cursor:pointer}
button:hover{border-color:#93a0b4}
button.primary{background:#1d4ed8;border-color:#1d4ed8;color:#fff}
button.primary:hover{background:#1e40af}
button:disabled{opacity:.55;cursor:not-allowed}
.wrap{display:flex;gap:16px;padding:16px 24px 60px;align-items:flex-start}
nav{width:170px;flex:none;display:flex;flex-direction:column;gap:6px;position:sticky;top:16px}
nav button{text-align:left}
nav button.on{background:#e8efff;border-color:#1d4ed8;color:#1d4ed8;font-weight:600}
main{flex:1;min-width:0}
section.panel{background:#fff;border:1px solid #e0e4ea;border-radius:10px;padding:16px 18px;margin-bottom:14px}
section.panel h2{font-size:15px;margin:0 0 4px}
.muted{color:#6b7280;font-size:12px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
input[type=text],input[type=number],textarea,select{font:inherit;padding:6px 9px;border:1px solid #ccd2da;border-radius:7px;background:#fff;max-width:100%}
textarea{width:100%;min-height:96px;font-family:ui-monospace,Consolas,monospace;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
th,td{border:1px solid #e3e6ea;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f3f5f8;position:sticky;top:0}
tr.click{cursor:pointer}
tr.click:hover td{background:#f8fafc}
.lab{display:inline-block;padding:1px 8px;border-radius:9px;font-size:12px}
.pass{background:#e6f4ea;color:#1a7f37}.reject{background:#fde8e8;color:#b42318}
.ambiguous{background:#fff4d6;color:#8a5a00}.error{background:#eef0f3;color:#57606a}
pre{background:#0f172a;color:#e5e7eb;padding:12px;border-radius:8px;overflow:auto;max-height:280px;font-size:12px}
.msg{margin-top:8px;font-size:13px}
.msg.err{color:#b42318}.msg.ok{color:#1a7f37}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.card{border:1px solid #e0e4ea;border-radius:8px;overflow:hidden;background:#fafbfc}
.card .canvas{position:relative;background:#fff}
.card img{display:block;width:100%;height:auto}
.card svg{position:absolute;inset:0;width:100%;height:100%}
.card .cap{padding:6px 8px;font-size:12px;color:#4b5563}
code{background:#f1f3f6;padding:1px 5px;border-radius:4px}
details summary{cursor:pointer;font-weight:600}
.kv span{margin-right:18px;display:inline-block}
</style></head><body>
<header>
  <h1>QuackGuard · AI 商品审核官控制台</h1>
  <span class="badge" id="ver-badge">产物未构建</span>
  <span class="badge" id="cnt-badge">0 条输出</span>
  <span class="sp"></span>
  <button onclick="refresh()">刷新状态</button>
</header>
<div class="wrap">
<nav>
  <button data-p="p1" class="on">① 规则导入</button>
  <button data-p="p2">② 建设</button>
  <button data-p="p3">③ 审核</button>
  <button data-p="p4">④ 证据定位</button>
  <button data-p="p5">⑤ 标注</button>
  <button data-p="p6">⑥ 迭代</button>
  <button data-p="p7">⑦ 版本 / 回滚</button>
  <button data-p="p8">⑧ 插件</button>
</nav>
<main>
<section class="panel" id="p1"><h2>① 规则导入</h2>
<p class="muted">导入赛题规则（B001 3.0/3.1 + R001–R060），逐行校验并定位坏记录。重复导入不会产生重复活动版本。</p>
<div class="row">
  <button class="primary" onclick="run('import',{use_pack:true})">载入赛题全部规则</button>
  <input type="file" id="rulefile" accept=".jsonl,.json,text/plain">
  <button onclick="importFile()">导入所选文件</button>
</div>
<textarea id="ruletxt" placeholder="也可以直接粘贴 JSONL 规则文本后点“校验并保存”"></textarea>
<div class="row"><button onclick="importText()">校验并保存</button></div>
<div id="msg-import" class="msg"></div></section>

<section class="panel" id="p2" hidden><h2>② 建设</h2>
<p class="muted">把规则转成可执行产物。构建失败会保留合法规则并给出行号。</p>
<div class="row">
  <button class="primary" onclick="run('build',{})">全量构建</button>
  <input type="text" id="only" placeholder="单条规则，如 R005@3.0">
  <button onclick="run('build',{only:val('only')})">单条构建</button>
  <button onclick="run('build',{resume:true})">中断恢复</button>
  <a href="/report?name=artifacts/status.html" target="_blank"><button type="button">构建状态页</button></a>
  <a href="/report?name=artifacts/versions.html" target="_blank"><button type="button">版本页</button></a>
</div>
<div id="msg-build" class="msg"></div></section>

<section class="panel" id="p3" hidden><h2>③ 审核</h2>
<p class="muted">按任务指定规则版本推理；调用失败记为 error，不伪造标签。</p>
<div class="row">
  <button class="primary" onclick="run('infer',{})">运行公开集审核</button>
  <button onclick="run('evaluate',{})">运行两个评估插件</button>
  <button onclick="run('export',{})">导出结果</button>
  <a href="/report?name=review.html" target="_blank"><button type="button">审核结果页</button></a>
  <a href="/report?name=plugin_report.html" target="_blank"><button type="button">插件评估页</button></a>
</div>
<div id="msg-infer" class="msg"></div>
<table><thead><tr><th>审核单元</th><th>规则</th><th>结论</th><th>适用性</th><th>状态</th></tr></thead>
<tbody id="units"></tbody></table></section>

<section class="panel" id="p4" hidden><h2>④ 证据定位</h2>
<p class="muted">规则原文、结论理由、材料/源页与区域、产物版本与 trace 一屏可查。</p>
<div id="detail"><p class="muted">在“审核”页点击任意一行查看。</p></div></section>

<section class="panel" id="p5" hidden><h2>⑤ 标注</h2>
<p class="muted">标注框必填；无可用区域时显式失败，不用整帧冒充。</p>
<div class="row"><button class="primary" onclick="run('annotate',{})">运行标注样例</button></div>
<div id="msg-annotate" class="msg"></div></section>

<section class="panel" id="p6" hidden><h2>⑥ 迭代</h2>
<p class="muted">教师建议逐条对照原文；错误建议不会进入活动策略，候选需通过门槛才能激活。</p>
<div class="row">
  <input type="text" id="iter-unit" placeholder="审核单元 ID" size="40">
  <input type="text" id="iter-proposal" placeholder="教师建议，如：确认按原文执行。" size="40">
</div>
<div class="row">
  <button class="primary" onclick="run('iterate',{audit_unit_id:val('iter-unit'),proposal:val('iter-proposal'),reset_candidate:true})">运行迭代</button>
  <a href="/report?name=candidate/iteration.html" target="_blank"><button type="button">迭代报告</button></a>
</div>
<div id="msg-iterate" class="msg"></div></section>

<section class="panel" id="p7" hidden><h2>⑦ 版本 / 比较 / 回滚</h2>
<div class="row">
  <button onclick="run('compare',{})">比较 baseline 与候选</button>
  <button class="primary" onclick="run('activate',{})">激活候选</button>
  <button onclick="run('rollback',{})">回滚到 baseline</button>
  <a href="/report?name=activate.html" target="_blank"><button type="button">激活记录</button></a>
</div>
<div class="row"><input type="text" id="task-id" placeholder="失败任务 ID（重试用）" size="40">
  <button onclick="run('retry',{task_id:val('task-id')})">重试失败任务</button></div>
<div id="msg-version" class="msg"></div></section>

<section class="panel" id="p8" hidden><h2>⑧ 插件</h2>
<p class="muted">两个评估插件独立进程运行；禁用后调用次数为 0，健康插件不受影响。</p>
<div class="row">
  <button onclick="run('plugins',{})">注册并列出插件</button>
  <a href="/report?name=plugins.html" target="_blank"><button type="button">插件配置页</button></a>
</div>
<table><thead><tr><th>插件</th><th>版本</th><th>入口</th><th>状态</th><th>操作</th></tr></thead>
<tbody id="plugins"></tbody></table>
<div id="msg-plugins" class="msg"></div></section>
</main></div>
<script>
const $=id=>document.getElementById(id);
const val=id=>($(id).value||'').trim();
function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x===b));
  ['p1','p2','p3','p4','p5','p6','p7','p8'].forEach(p=>$(p).hidden=(p!==b.dataset.p));
});
async function api(path,body){
  const opt=body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
  const res=await fetch(path,opt);
  return res.json();
}
function show(id,obj,ok){
  const box=$(id);
  const text=JSON.stringify(obj,null,2);
  box.className='msg '+(ok===false?'err':'ok');
  box.innerHTML='<details><summary>操作结果</summary><pre>'+esc(text)+'</pre></details>';
}
async function refresh(){
  const s=await api('/api/state');
  $('ver-badge').textContent=s.artifact_version?('产物 '+s.artifact_version):'产物未构建';
  $('cnt-badge').textContent=s.records+' 条输出 · 标注 '+s.annotations;
  if(s.plugins&&s.plugins.length)renderPlugins(s.plugins);
  loadUnits();
}
async function loadUnits(){
  const data=await api('/api/units');
  const body=$('units');
  if(!data.units.length){body.innerHTML='<tr><td colspan="5" class="muted">暂无输出，先运行审核。</td></tr>';return;}
  body.innerHTML=data.units.map(u=>'<tr class="click" onclick="openUnit(\\''+esc(u.audit_unit_id)+'\\')">'
    +'<td><code>'+esc(u.audit_unit_id)+'</code></td>'
    +'<td>'+esc(u.rule_id)+'@'+esc(u.rule_version)+'</td>'
    +'<td><span class="lab '+(u.label||'error')+'">'+esc(u.label||u.execution_status)+'</span></td>'
    +'<td>'+esc(u.applicability)+'</td><td>'+esc(u.execution_status)+'</td></tr>').join('');
}
async function openUnit(id){
  document.querySelector('nav button[data-p="p4"]').click();
  const d=await api('/api/unit?id='+encodeURIComponent(id));
  if(d.error_code){$('detail').innerHTML='<p class="msg err">'+esc(d.detail)+'</p>';return;}
  const o=d.output||{};
  const r=d.rule||{};
  const mats=(d.materials||[]).map(m=>'<div class="card"><div class="canvas"><img src="/media?unit='+encodeURIComponent(id)
    +'&material='+encodeURIComponent(m.material_id)+'&page='+m.page_index+'">'
    +'<svg id="bx-'+esc(m.material_id)+'"></svg></div><div class="cap">'+esc(m.role)+' · 第 '+m.page_index+' 页</div></div>').join('');
  let evHtml='';
  const items=(o.evidence||[]).map(e=>'<div class="card"><div class="cap"><code>'+esc(e.material_id)+'</code> 第 '+esc(e.page_index)+' 页</div>'
    +'<div class="cap">'+esc(e.fact)+'</div></div>').join('');
  if(items)evHtml='<h3 style="font-size:14px;margin:14px 0 6px">证据事实</h3><div class="grid">'+items+'</div>';
  $('detail').innerHTML='<section class="kv" style="margin:4px 0 10px">'
    +'<span>规则：<b>'+esc(o.rule_id)+'@'+esc(o.rule_version)+'</b></span>'
    +'<span>结论：<span class="lab '+(o.label||'error')+'">'+esc(o.label||o.execution_status)+'</span></span>'
    +'<span>适用性：'+esc(o.applicability)+'</span>'
    +'<span>产物版本：<code>'+esc(d.artifact_version)+'</code></span>'
    +'<span>trace：<code>'+esc(d.trace_id)+'</code></span>'
    +'<span>规则源哈希：<code>'+esc(r.source_sha256||'-')+'</code></span></section>'
    +'<p>审核理由：'+esc(o.reason)+'</p>'
    +'<details><summary>规则原文</summary><pre>'+esc(r.rule_text)+'</pre></details>'
    +'<h3 style="font-size:14px;margin:14px 0 6px">材料与可用区域</h3><div class="grid">'+mats+'</div>'+evHtml;
  (o.evidence||[]).forEach(e=>{
    const svg=$('bx-'+e.material_id);if(!svg||!e.bbox)return;
    const b=e.bbox;const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('x',b[0]);rect.setAttribute('y',b[1]);
    rect.setAttribute('width',Math.max(b[2]-b[0],0.001));rect.setAttribute('height',Math.max(b[3]-b[1],0.001));
    rect.setAttribute('fill','rgba(220,38,38,.12)');rect.setAttribute('stroke','#dc2626');
    rect.setAttribute('stroke-width','0.006');svg.appendChild(rect);
  });
}
function importFile(){
  const f=$('rulefile').files[0];
  if(!f){show('msg-import',{error:'请选择文件'},false);return;}
  const reader=new FileReader();
  reader.onload=()=>run('import',{text:reader.result});
  reader.readAsText(f,'utf-8');
}
function importText(){run('import',{text:val('ruletxt')});}
async function run(action,payload){
  const map={activate:'msg-version',rollback:'msg-version',retry:'msg-version',compare:'msg-version',plugins:'msg-plugins',plugin_state:'msg-plugins',evaluate:'msg-infer',export:'msg-infer'};
  const box=map[action]||('msg-'+action);
  try{
    const res=await api('/api/action',{action,payload:payload||{}});
    show(box,res,res.code===0);
    await refresh();
    if(action==='plugins'||action==='plugin_state')guardPlugins();
  }catch(e){show(box,{error:String(e)},false);}
}
function renderPlugins(list){
  $('plugins').innerHTML=list.map(p=>'<tr><td>'+esc(p.plugin_id)+'</td><td>'+esc(p.plugin_version)+'</td>'
    +'<td><code>'+esc(p.entrypoint)+'</code></td><td>'+(p.enabled?'启用':'禁用')+'</td>'
    +'<td><button onclick="togglePlugin(\\''+esc(p.plugin_id)+'\\',\\''+esc(p.plugin_version)+'\\','+(p.enabled?'disabled':'enabled')+')">'
    +(p.enabled?'禁用':'启用')+'</button></td></tr>').join('');
}
async function togglePlugin(id,version,state){
  await api('/api/action',{action:'plugin_state',payload:{plugin_id:id,plugin_version:version,state}});
  await run('plugins',{});
}
async function guardPlugins(){const s=await api('/api/state');if(s.plugins)renderPlugins(s.plugins);}
refresh();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "QuackGuardConsole/1.0"
    workspace = "var/console"

    def log_message(self, format, *args):  # noqa: A003 - silence default logging
        return

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        parsed = urlparse(self.path)
        query = {key: value[0] for key, value in parse_qs(parsed.query).items()}
        return parsed.path, query

    def do_GET(self):  # noqa: N802 - http.server API
        path, query = self._query()
        try:
            if path == "/":
                self._send(200, PAGE, "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send(200, state(self.workspace))
            elif path == "/api/units":
                self._send(200, units(self.workspace))
            elif path == "/api/unit":
                self._send(200, unit_detail(
                    self.workspace, query.get("id", "")
                ))
            elif path == "/media":
                self._send_media(query)
            elif path == "/report":
                self._send_report(query)
            else:
                self._send(404, {"error_code": "not_found"})
        except AuditError as exc:
            self._send(400, {"error_code": exc.code, "detail": exc.detail})
        except (OSError, ValueError) as exc:
            self._send(500, {"error_code": "console_error", "detail": str(exc)})

    def do_POST(self):  # noqa: N802 - http.server API
        path, _query = self._query()
        if path != "/api/action":
            self._send(404, {"error_code": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 16 * 1024 * 1024:
                raise AuditError("payload_too_large", "请求体超过16MB")
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = _action(
                self.workspace,
                body.get("action"),
                body.get("payload") or {},
            )
            self._send(200, result)
        except AuditError as exc:
            self._send(400, {"error_code": exc.code, "detail": exc.detail})
        except (OSError, ValueError) as exc:
            self._send(500, {"error_code": "console_error", "detail": str(exc)})

    def _send_media(self, query):
        unit_id = query.get("unit", "")
        material_id = query.get("material", "")
        page = query.get("page", "1")
        task = _task_index().get(unit_id)
        if task is None:
            raise AuditError("unknown_unit", unit_id)
        material = next(
            (
                item for item in task["materials"]
                if item["material_id"] == material_id
                and str(item["page_index"]) == str(page)
            ),
            None,
        )
        if material is None:
            raise AuditError("unknown_material", material_id)
        root = TASKS.resolve().parent
        target = (root / material["relative_path"]).resolve()
        if root not in target.parents or not target.is_file():
            raise AuditError("unsafe_material_path", material["relative_path"])
        content_type = (
            mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
        self._send(200, target.read_bytes(), content_type)

    def _send_report(self, query):
        p = paths(self.workspace)
        name = query.get("name", "")
        target = (p["root"] / name).resolve()
        if p["root"].resolve() not in target.parents or not target.is_file():
            raise AuditError("unknown_report", name)
        if target.suffix.lower() not in (".html", ".json", ".csv"):
            raise AuditError("unsupported_report", name)
        content_type = (
            "text/html; charset=utf-8"
            if target.suffix.lower() == ".html"
            else "text/plain; charset=utf-8"
        )
        self._send(200, target.read_bytes(), content_type)


def serve(workspace, host="127.0.0.1", port=8765):
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    Handler.workspace = str(root)
    server = ThreadingHTTPServer((host, port), Handler)
    address = "http://{}:{}/".format(host, server.server_address[1])
    print(json.dumps({
        "command": "console",
        "url": address,
        "workspace": str(root),
    }, ensure_ascii=False))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {"command": "console", "url": address, "stopped": True}
