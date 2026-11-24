import importlib
import json
import subprocess
import sys
import traceback
from pathlib import Path


class InputMutationError(Exception):
    pass


class InvalidReportError(Exception):
    pass


def failure(entry, code, detail=""):
    return {
        "plugin_id": entry,
        "plugin_version": "unknown",
        "input_schema_version": "1.3.1",
        "metrics": {},
        "diagnostics": [],
        "errors": [{"code": code, "detail": detail[-1000:]}],
    }


def run_plugin(entry, inputs, outputs, references, context, timeout=None):
    payload = {
        "entry": entry,
        "inputs": inputs,
        "outputs": outputs,
        "references": references,
        "context": context,
    }
    try:
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--worker"],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode:
            return failure(entry, "plugin_failed", result.stderr)
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or not isinstance(report.get("metrics"), dict):
            raise ValueError("Invalid plugin report")
        return report
    except subprocess.TimeoutExpired:
        return failure(entry, "plugin_timeout")
    except OSError as exc:
        return failure(entry, "plugin_start_failed", str(exc))
    except (ValueError, TypeError) as exc:
        return failure(entry, "plugin_invalid_output", str(exc))


def _execute(entry, arguments):
    target = entry
    if ":" not in target and target.startswith("quackguard."):
        target = "quackguard.plugins:" + target.rsplit(".", 1)[-1]
    module, name = target.rsplit(":", 1)
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    function = getattr(importlib.import_module(module), name)
    before = json.dumps(arguments, sort_keys=True)
    result = function(*arguments)
    if json.dumps(arguments, sort_keys=True) != before:
        raise InputMutationError("Plugin mutated read-only input objects")
    if not isinstance(result, dict) or not isinstance(result.get("metrics"), dict):
        raise InvalidReportError("Plugin report must contain a metrics object")
    return result


def worker():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    entry = payload["entry"]
    arguments = [
        payload[key] for key in ("inputs", "outputs", "references", "context")
    ]
    try:
        report = _execute(entry, arguments)
    except InputMutationError as exc:
        report = failure(entry, "plugin_mutated_input", str(exc))
    except InvalidReportError as exc:
        report = failure(entry, "plugin_invalid_output", str(exc))
    except Exception:
        report = failure(entry, "plugin_failed", traceback.format_exc())
    try:
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        print(
            json.dumps(
                failure(entry, "plugin_invalid_output", str(exc)),
                ensure_ascii=False,
            )
        )


if __name__ == "__main__" and "--worker" in sys.argv:
    worker()
