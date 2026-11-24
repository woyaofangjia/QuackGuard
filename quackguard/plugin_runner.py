import importlib
import json
import subprocess
import sys
from pathlib import Path


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


def worker():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    entry = payload["entry"]
    if entry.startswith("quackguard."):
        entry = "quackguard.plugins:" + entry.rsplit(".", 1)[-1]
    module, name = entry.rsplit(":", 1)
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    function = getattr(importlib.import_module(module), name)
    arguments = [
        payload[key] for key in ("inputs", "outputs", "references", "context")
    ]
    before = json.dumps(arguments, sort_keys=True)
    result = function(*arguments)
    if json.dumps(arguments, sort_keys=True) != before:
        raise ValueError("Plugin mutated read-only input objects")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__" and "--worker" in sys.argv:
    worker()
