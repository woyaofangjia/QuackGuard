from .io_utils import rows, write_json
from .plugin_runner import run_plugin


def evaluate(
    plugin,
    inputs_path,
    outputs_path,
    references_path,
    out_path,
    plugin_timeout=None,
    store=None,
):
    report = run_plugin(
        entry=plugin,
        inputs=rows(inputs_path),
        outputs=rows(outputs_path, required=False),
        references=rows(references_path),
        context={"schema_version": "1.3.1", "purpose": "public_development"},
        timeout=plugin_timeout,
    )
    write_json(out_path, report)
    if store and not report["errors"]:
        store.register_plugin(
            report["plugin_id"], report["plugin_version"], plugin
        )
    return {
        "command": "evaluate",
        "plugin": plugin,
        "errors": len(report["errors"]),
        "out": str(out_path),
    }
