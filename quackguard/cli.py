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
from .iteration import iterate
from .storage import Store

PARAMETERS = {
    "build": ["rules", "config", "out"],
    "infer": ["input", "artifacts", "out"],
    "annotate": ["input", "targets", "artifacts", "out"],
    "iterate": ["base", "feedback", "input", "out"],
    "evaluate": ["plugin", "inputs", "outputs", "references", "out"],
}
PRODUCT_PARAMETERS = {
    "compare": ["old", "new", "input", "references", "out"],
    "export": ["input", "predictions", "references", "out"],
}
INPUT_PARAMETERS = {
    "build": "rules",
    "infer": "input",
    "annotate": "input",
    "iterate": "base",
    "evaluate": "inputs",
    "compare": "input",
    "export": "input",
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
            subparser.add_argument("--" + parameter, required=True)
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
    retry = commands.add_parser("retry", help="重试失败的旧任务")
    retry.add_argument("--task-id", required=True, help="要重试的失败任务ID")
    retry.add_argument("--out", default=None, help="可选：覆盖原输出路径")
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
            args.artifacts,
            args.out,
            base_url=os.environ.get("OPENAI_BASE_URL"),
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    if args.command == "annotate":
        return annotate(
            args.input,
            args.targets,
            args.artifacts,
            args.out,
            base_url=os.environ.get("OPENAI_BASE_URL"),
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    if args.command == "iterate":
        return iterate(args.base, args.feedback, args.out)
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
        )
    if args.command == "compare":
        return compare_results(
            args.old, args.new, args.input, args.references, args.out
        )
    if args.command == "export":
        return export_results(
            args.input, args.predictions, args.references, args.out
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
        getattr(replay, INPUT_PARAMETERS[task["command"]]),
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
            input_path = getattr(args, INPUT_PARAMETERS[args.command])
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
        print(
            json.dumps(
                {"error_code": code, "detail": str(exc)[:500]},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if store:
            store.close()
