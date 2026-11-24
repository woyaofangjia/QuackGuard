"""回归专用故障插件入口：验证崩溃与超时不会影响健康插件。"""
import time


def crash(inputs, outputs, references, context):
    raise RuntimeError("intentional regression crash")


def slow(inputs, outputs, references, context):
    time.sleep(5)
    return {
        "plugin_id": "regression.slow",
        "plugin_version": "0.1.0",
        "input_schema_version": "1.3.1",
        "metrics": {},
        "diagnostics": [],
        "errors": [],
    }
