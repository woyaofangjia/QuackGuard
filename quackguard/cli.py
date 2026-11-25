import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .annotation import annotate
from .artifacts import build
from .comparison import compare_results
from .contracts import ContractError
from .errors import AuditError
from .evaluation import evaluate
from .export import export_results
from .inference import infer
from .io_utils import write_json
from .iteration import iterate
from .plugin_registry import load_manifest, register_manifest
from .plugin_report import write_plugins_page
from .regression import regress
from .storage import Store
from .versioning import activate, rollback

PARAMETERS = {
    "build": ["rules", "config", "out"],
    "infer": ["input", "artifacts", "out"],
    "annotate": ["input", "targets", "artifacts", "out"],
    "iterate": ["base", "feedback", "input", "out"],
    "evaluate": ["inputs", "outputs", "references", "out"],
    "plugins": ["out"],
    "plugin-state": ["plugin-id", "plugin-version", "state", "out"],
    "regress": ["rules", "config", "input", "references", "baseline", "out"],
    "console": ["workspace"],
}
ERROR_HINTS = {
    "invalid_jsonl": "检查JSONL行号与JSON字段；合法记录仍会保留。",
    "invalid_rule": "修正规则ID/版本/正文后重新build；合法规则不受影响。",
    "duplicate_rule": "删除重复规则版本后重新build；首次出现行号见状态页。",
    "artifact_hash_mismatch": "产物已被修改；请从干净目录重新build或恢复原产物。",
    "contract_error": "按错误信息校验规则/配置/产物字段；修正后重试，既有产物不受影响。",
    "io_error": "检查文件路径与读写权限后重试；已完成任务记录会保留。",
    "invalid_plugin_manifest": "修正插件清单字段与input_schema_version后重试。",
    "unknown_plugin": "先用plugins --manifest注册插件，再切换启用状态。",
    "regression_failed": "查看regression_report.json中的失败检查项；先修复根因再重跑，勿改基线数据。",
}
PRODUCT_PARAMETERS = {
    "compare": ["old", "new", "input", "references", "out"],
    "export": ["input", "predictions", "references", "out"],
    "activate": ["baseline", "candidate", "out"],
    "rollback": ["to", "out"],
}
INPUT_PARAMETERS = {
    "build": "rules",
    "infer": "input",
    "annotate": "input",
    "iterate": "base",
    "evaluate": "inputs",
    "compare": "input",
    "export": "input",
    "activate": "baseline",
    "rollback": "to",
    "plugins": "manifest",
    "plugin-state": "plugin_id",
    "regress": "rules",
}


def parser():
    result = argparse.ArgumentParser(prog="quackguard")
    result.add_argument(
        "--db",
        default=os.environ.get("QUACKGUARD_DB"),
        help="可选SQLite数据库路径，也可用QUACKGUARD_DB",
    )
    commands = result.add_subparsers(dest="command", required=True)
    for command, parameters in {**PARAMETERS, **PRODUCT_PARAMETERS}.items():
        subparser = commands.add_parser(command)
        for parameter in parameters:
            if command == "plugin-state" and parameter == "state":
                subparser.add_argument(
                    "--state", required=True, choices=["enabled", "disabled"]
                )
            else:
                subparser.add_argument("--" + parameter, required=True)
    commands.choices["evaluate"].add_argument(
        "--plugin", default=None, help="单个插件入口，例如quackguard.conclusion"
    )
    commands.choices["evaluate"].add_argument(
        "--manifest", default=None, help="插件清单JSON；与--plugin二选一"
    )
    commands.choices["plugins"].add_argument(
        "--manifest", default=None, help="可选：先注册清单中的插件再列出"
    )
    commands.choices["build"].add_argument(
        "--only",
        default="",
        help="逗号分隔的规则ID或规则@版本，仅构建指定规则",
    )
    commands.choices["build"].add_argument(
        "--resume",
        action="store_true",
        help="目标产物已存在且哈希未变时跳过构建",
    )
    commands.choices["activate"].add_argument(
        "--gate",
        default=None,
        help="可选：激活门槛证据JSON；提供时任一门槛失败即拒绝激活",
    )
    commands.choices["rollback"].add_argument(
        "--expect-hash",
        default=None,
        help="可选：回滚目标根目录哈希，必须匹配",
    )
    commands.choices["rollback"].add_argument(
        "--reason",
        default="",
        help="可选：回滚原因",
    )
    commands.choices["console"].add_argument(
        "--host", default="127.0.0.1", help="仅本机访问，默认127.0.0.1"
    )
    commands.choices["console"].add_argument(
        "--port", type=int, default=8765, help="监听端口，默认8765"
    )
    regress_parser = commands.choices["regress"]
    regress_parser.add_argument(
        "--branches",
        default=None,
        help="可选：自建规则分支用例JSONL",
    )
    regress_parser.add_argument(
        "--annotation-tasks",
        default=None,
        help="可选：标注任务JSONL",
    )
    regress_parser.add_argument(
        "--annotation-targets",
        default=None,
        help="可选：标注目标JSONL",
    )
    regress_parser.add_argument(
        "--annotation-references",
        default=None,
        help="可选：标注金标JSONL",
    )
    regress_parser.add_argument(
        "--fault-timeout",
        type=float,
        default=1.0,
        help="可选：插件故障测试超时秒数，默认1.0",
    )
    retry = commands.add_parser("retry", help="重试失败的旧任务")
    retry.add_argument("--task-id", required=True, help="要重试的失败任务ID")
    retry.add_argument("--out", default=None, help="可选：覆盖原输出路径")
    return result


def resolve_artifacts(path, store):
    """允许 --artifacts active，从活动版本指针解析产物目录。"""
    if path != "active":
        return path
    if store is None:
        raise AuditError("active_requires_db")
    active = store.get_active()
    if active is None:
        raise AuditError("no_active_version")
    return active["root_path"]


def list_registered_plugins(manifest, out_path, store):
    if store is None:
        raise AuditError("plugins_requires_db")
    if manifest:
        register_manifest(store, load_manifest(manifest))
    plugins = store.list_plugins()
    result = {
        "command": "plugins",
        "plugin_count": len(plugins),
        "plugins": [dict(row) for row in plugins],
        "out": str(out_path),
    }
    write_json(out_path, result)
    page = Path(out_path).with_name("plugins.html")
    write_plugins_page(page, plugins, None)
    result["page"] = str(page)
    return result


def change_plugin_state(plugin_id, plugin_version, state, out_path, store):
    if store is None:
        raise AuditError("plugin_state_requires_db")
    row = store.set_plugin_enabled(
        plugin_id, plugin_version, state == "enabled"
    )
    result = {
        "command": "plugin-state",
        "plugin_id": plugin_id,
        "plugin_version": plugin_version,
        "enabled": bool(row["enabled"]),
        "out": str(out_path),
    }
    write_json(out_path, result)
    page = Path(out_path).with_name("plugins.html")
    write_plugins_page(page, store.list_plugins(), None)
    result["page"] = str(page)
    return result


def execute(args, store):
    if args.command == "build":
        only = [item.strip() for item in args.only.split(",") if item.strip()]
        return build(
            args.rules,
            args.config,
            args.out,
            store=store,
            resume=args.resume,
            only=only or None,
        )
    if args.command == "infer":
        return infer(
            args.input,
            resolve_artifacts(args.artifacts, store),
            args.out,
            base_url=os.environ.get("OPENAI_BASE_URL"),
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    if args.command == "annotate":
        return annotate(
            args.input,
            args.targets,
            resolve_artifacts(args.artifacts, store),
            args.out,
            base_url=os.environ.get("OPENAI_BASE_URL"),
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    if args.command == "iterate":
        return iterate(args.base, args.feedback, args.input, args.out, store=store)
    if args.command == "evaluate":
        timeout_value = os.environ.get("PLUGIN_TIMEOUT_SECONDS")
        timeout = float(timeout_value) if timeout_value else None
        return evaluate(
            args.plugin,
            args.inputs,
            args.outputs,
            args.references,
            args.out,
            plugin_timeout=timeout,
            store=store,
            manifest=args.manifest,
        )
    if args.command == "plugins":
        return list_registered_plugins(args.manifest, args.out, store)
    if args.command == "plugin-state":
        return change_plugin_state(
            args.plugin_id,
            args.plugin_version,
            args.state,
            args.out,
            store,
        )
    if args.command == "compare":
        return compare_results(
            args.old, args.new, args.input, args.references, args.out
        )
    if args.command == "export":
        return export_results(
            args.input, args.predictions, args.references, args.out
        )
    if args.command == "activate":
        return activate(
            args.baseline,
            args.candidate,
            store,
            gate_path=args.gate,
            out_path=args.out,
        )
    if args.command == "rollback":
        return rollback(
            args.to,
            store,
            expect_hash=args.expect_hash,
            out_path=args.out,
            reason=args.reason,
        )
    if args.command == "regress":
        return regress(
            args.rules,
            args.config,
            args.input,
            args.references,
            args.baseline,
            args.out,
            branches_path=args.branches,
            annotation_tasks_path=args.annotation_tasks,
            annotation_targets_path=args.annotation_targets,
            annotation_references_path=args.annotation_references,
            fault_timeout=args.fault_timeout,
            store=store,
        )
    raise AuditError("unsupported_command", args.command)


def arguments(args):
    return {key: value for key, value in vars(args).items() if key != "db"}


def prepare_retry(args, store):
    """校验失败任务并创建唯一重试后继；重复重试返回已有后继。"""
    if store is None:
        raise AuditError("retry_requires_db")
    task = store.get_task(args.task_id)
    if task is None:
        raise AuditError("unknown_task", args.task_id)
    successor = store.retry_successor(args.task_id)
    if successor is not None:
        return {
            "command": "retry",
            "status": "existing",
            "retried_task_id": args.task_id,
            "task_id": successor["task_id"],
            "task_status": successor["status"],
        }
    if task["status"] != "failed":
        raise AuditError("task_not_retryable", task["status"])
    run = store.latest_run(args.task_id)
    if run is None or not run["arguments_json"]:
        raise AuditError("retry_arguments_missing")
    try:
        params = json.loads(run["arguments_json"])
    except (TypeError, ValueError) as exc:
        raise AuditError("retry_arguments_invalid", str(exc)) from exc
    if params.get("command") != task["command"]:
        raise AuditError("retry_arguments_mismatch")
    if args.out:
        params["out"] = args.out
    elif params.get("command") == "build" and params.get("out"):
        if Path(params["out"]).exists():
            params["resume"] = True
    params["db"] = args.db
    replay = argparse.Namespace(**params)
    new_task = store.start_task(task["command"], retry_of=args.task_id)
    new_run = store.start_run(
        new_task,
        task["command"],
        getattr(replay, INPUT_PARAMETERS[task["command"]], None) or replay.out,
        replay.out,
        arguments=arguments(replay),
    )
    return replay, new_task, new_run


def error_code(exc):
    if isinstance(exc, AuditError):
        return exc.code
    if isinstance(exc, ContractError):
        return "contract_error"
    if isinstance(exc, sqlite3.Error):
        return "storage_error"
    if isinstance(exc, OSError):
        return "io_error"
    return "invalid_operation"


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "console":
        from .console import serve

        serve(args.workspace, args.host, args.port)
        return 0
    store = None
    task_id = None
    run_id = None
    try:
        if args.db:
            store = Store(args.db)
        if args.command == "retry":
            prepared = prepare_retry(args, store)
            if isinstance(prepared, dict):
                print(json.dumps(prepared, ensure_ascii=False))
                return 0
            args, task_id, run_id = prepared
        elif store:
            task_id = store.start_task(args.command)
            input_path = (
                getattr(args, INPUT_PARAMETERS[args.command], None) or args.out
            )
            run_id = store.start_run(
                task_id,
                args.command,
                input_path,
                args.out,
                arguments=arguments(args),
            )
        result = execute(args, store)
        if store and task_id:
            store.finish(task_id, run_id, "completed")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (AuditError, ContractError, sqlite3.Error, OSError, ValueError) as exc:
        code = error_code(exc)
        if store and task_id and run_id:
            store.finish(task_id, run_id, "failed", code)
        payload = {"error_code": code, "detail": str(exc)[:500]}
        if code in ERROR_HINTS:
            payload["hint"] = ERROR_HINTS[code]
        print(
            json.dumps(payload, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    finally:
        if store:
            store.close()
