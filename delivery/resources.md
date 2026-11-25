# 资源报告

## 1. 依赖

- 运行时：Python 3.13（标准库）。
- 第三方依赖：无。`regression_checks.audit_dependencies` 扫描 `quackguard/*.py` 的导入根，`third_party=[]`。
- 无 `requirements.txt`、`pyproject.toml`、`package.json`；无需联网安装。
- 交付包不含虚拟环境、缓存、临时文件或密钥。

## 2. 模型与配置

- 开发与运行使用智谱 AI 免费视觉模型 `glm-4v-flash`；`delivery/config.json` 在官方样例配置基础上增加 `"model": "glm-4v-flash"`，不含密钥。
- 网关与密钥通过环境变量注入：`OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4`、`OPENAI_API_KEY`。
- 未配置模型时，推理按协议输出 `execution_status=error`、`error_code=model_not_configured`，不伪造标签。
- 冻结快照 `delivery/artifacts/` 为离线构建（无模型），公开集 64 条中 62 条因此显式报错，2 条 B001 因材料不适配可直接判 `not_applicable`。

## 3. 计量口径

- `cost_units`：`null`（自报口径未填写，不声明组织方预算或节省量）。
- 不把公开参考基线 Q=1 或公开金标演练轨迹写成系统正式成绩。
- 本交付的公开集验收指标（GoldGateway 可见事实）：
  - 单元数 64；A=0.90625；U=0.903846；Q=0.905529；M=0.796875；机审正确率=0.921569。
  - 缺失、重复、额外、非法均为 0。
- 真实模型运行（`glm-4v-flash`，见 `delivery/model_run/`）：A=0.609375（39/64）；U=0.673077；Q=0.628486；适用性正确率 0.984375；执行错误 1。
- 标注公开样例真实运行：值/状态/标签/材料/页码 3/3 正确，但模型框坐标系统性偏移导致 IoU 不达标，得分 0/7。
- 上述数字是公开开发集验收结果，用于核对接口与评分算术，不等同隐藏集成绩。

## 4. 轨迹与冻结

- `delivery/traces/public_gold.trace.jsonl`：公开金标可见事实的抽取轨迹，含 `trace_id`、材料与事实；用于证据定位验收。
- `delivery/traces/infer.trace.jsonl`：真实 CLI 推理轨迹；未配置模型时以显式 error 记录。
- `delivery/annotations/`：标注输出、失败清单与事实轨迹；由公开金标可见事实驱动同一 `annotate` 实现，公开样例 3/3 正确、得分 7/7，用于验证标注链路，不计入正式成绩。
- `delivery/evaluation.json` 与 `delivery/plugin_report.html`：两个评估插件的实际运行报告；`input_unchanged=true`、`errors=0`，输入只读与进程隔离可见。
- `trace_id` 为运行标识，每次运行不同；冻结记录保留本次实际值，不承诺逐字节复现。
- `delivery/artifacts/` 为本次 build 的冻结产物；`delivery/manifest_check.json` 记录 manifest 与实际哈希核对结果。

## 5. 运行开销

- 构建 62 个规则版本为纯本地 JSON 读写；公开集 64 条验收为纯 Python 计算。
- 插件通过独立进程运行，默认超时 `PLUGIN_TIMEOUT_SECONDS`，故障隔离不阻塞健康插件。
- 无 GPU、无外部数据库、无网络依赖（模型推理除外）。
