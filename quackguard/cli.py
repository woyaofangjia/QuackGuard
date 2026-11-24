import argparse
import json
import os
import sqlite3
import sys

from .annotation import annotate
from .artifacts import build
from .contracts import ContractError
from .errors import AuditError
from .evaluation import evaluate
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
INPUT_PARAMETERS = {
    "build": "rules",
    "infer": "input",
    "annotate": "input",
    "iterate": "base",
    "evaluate": "inputs",
}


def parser():
    result = argparse.ArgumentParser(prog="quackguard")
    result.add_argument(
        "--db",
        default=os.environ.get("QUACKGUARD_DB"),
        help="可选SQLite数据库路径，也可用QUACKGUARD_DB",
    )
    commands = result.add_subparsers(dest="command", required=True)
    for command, parameters in PARAMETERS.items():
        subparser = commands.add_parser(command)
        for parameter in parameters:
            subparser.add_argument("--" + parameter, required=True)
    return result


def execute(args, store):
    if args.command == "build":
        return build(args.rules, args.config, args.out, store=store)
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
    raise AuditError("unsupported_command", args.command)


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
            task_id = store.start_task(args.command)
            input_path = getattr(args, INPUT_PARAMETERS[args.command])
            run_id = store.start_run(
                task_id, args.command, input_path, args.out
            )
        result = execute(args, store)
        if store:
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
