"""R 系列规则引擎：适用性判定、视觉事实提取与按族路由。

核心判定为纯函数，仅在边界调用视觉网关；执行异常由 inference 层捕获。
"""
from .errors import AuditError
from .rule_common import as_text, normalize_text, not_applicable_result, unknown_result
from .rule_facts import extract_facts
from .rules_f01 import JUDGES as F01
from .rules_f02 import JUDGES as F02
from .rules_f03 import JUDGES as F03
from .rules_f04 import JUDGES as F04
from .rules_f05 import JUDGES as F05
from .rules_f06 import JUDGES as F06
from .rules_f07 import JUDGES as F07
from .rules_f08 import JUDGES as F08
from .rules_f09 import JUDGES as F09
from .rules_f10 import JUDGES as F10

REGISTRY = {}
for family in (F01, F02, F03, F04, F05, F06, F07, F08, F09, F10):
    REGISTRY.update(family)


def applicability(task):
    """返回提前结论（not_applicable/unknown），适用时返回 None。"""
    rule_id = task["rule_id"]
    metadata = task["metadata"]
    if rule_id == "R055":
        category = as_text(metadata.get("category"))
        power_mode = as_text(metadata.get("power_mode"))
        if category is not None and normalize_text(category) != "桌面灯":
            return not_applicable_result("申报类别明确不是桌面灯，R055不适用。")
        if power_mode is not None and normalize_text(power_mode) != "可充电":
            return not_applicable_result("申报供电方式明确不是可充电，R055不适用。")
        if category is None or power_mode is None:
            return unknown_result("R055适用条件类别或供电方式未知。")
        return None
    if rule_id == "R056":
        quantity = metadata.get("declared_quantity")
        if type(quantity) is int and quantity == 1:
            return not_applicable_result("单件商品不触发R056多件内含物核对。")
        if type(quantity) is int and quantity > 1:
            return None
        return unknown_result("declared_quantity缺失或不是正整数，R056触发条件未知。")
    return None


def review_rule(gateway, implementation, task, input_path, trace_id):
    early = applicability(task)
    if early is not None:
        return early, []
    judge = REGISTRY.get(task["rule_id"])
    if judge is None:
        raise AuditError("unsupported_rule_processor", task["rule_id"])
    params = implementation.get("sop", {}).get("params", {})
    materials, traces = extract_facts(gateway, task, input_path, trace_id)
    result = judge(task, materials, params)
    return result, traces
