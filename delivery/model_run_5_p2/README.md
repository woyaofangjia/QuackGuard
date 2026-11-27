# model_run_5_p2 — 模型选型内部横评（非正式产物）

本目录只记录 P2 的模型选型证据与定向定位补丁评估。**正式交付产物不变**：仍为
`delivery/config.json` + `glm-4v-flash` 构建的 `av_a489476e2465379a03414735`，
公开集基线为 `model_run_4_p1/`（48/64，A=0.75，U=0.826923，Q=0.773077，执行错误 0）。

## 1. 结论

| 指标 | glm-4v-flash（正式） | glm-4.1v-thinking-flash（内部） |
| --- | --- | --- |
| 正确数 / 64 | 48 | 44（JSON 修复回放后 45） |
| A（标签+适用性） | 0.75 | 0.6875（0.703125） |
| U（正确决定覆盖） | 0.826923 | 0.730769（0.75） |
| Q=0.7A+0.3U | 0.773077 | 0.700481（0.717187） |
| 执行错误 | 0 | 4 条 `invalid_model_output`（回放修复后 1 条语义错误） |
| 平均延迟 | 10.06 s | 19.90 s |
| P50 / P95 / 最大 | 9.84 / 18.65 / 20.99 s | 19.87 / 33.60 / 60.86 s |

- thinking 模型在 8 条试点上只多对 1 条（6/8 vs 5/8），全量 64 条反而少对 4 条：
  B001/R005/R010/R021 共修好 6 条，R026/R041/R060/B001/R010/R035 共退化 10 条。
- 延迟约 2 倍、P95 超 33 s、最大 60 s，且 4 条非法 JSON，不满足电商审核链路可用性。
- 因此正式产物保持 `glm-4v-flash`；thinking 仅作为“能力上限探针”与定向补框工具。

## 2. JSON 硬兜底

`quackguard/rule_common.py::load_model_json` 在尾逗号、括号补齐之后新增最后一层修复：

- 对象后跟解释文字、重复输出第二份对象：只取第一个完整 JSON 值；
- 重复键：按键后值覆盖；
- 仍拒绝非有限常量（NaN/Infinity），不做任何标签伪造。

回放 4 条失败单元（`replay_result.json`）：

- R010：修复后 completed，判定 `pass` 与金标一致；
- R026：修复后 completed，判定 `ambiguous`，金标 `reject`，仍错；
- R060：修复后 completed，判定 `ambiguous`，金标 `pass`，仍错；
- B001：模型把字段 `status` 写成 `clear`，属语义错误，保持 `execution_status=error`、
  `label=null` 并计入分母，不降级为安全默认结论。

修复后 thinking 全量：45/64、A=0.703125、U=0.75、Q=0.717187、执行错误 1。

## 3. 定向定位补丁（默认关闭）

- `quackguard/grounding.py::locate_with_thinking()`：仅在事实提取没有字段框时补一次调用；
- CLI 开关：`python run.py annotate ... --locate-fallback`，默认不开启；
- 实测（`annotations_locate_fallback.jsonl`、`annotation_score.json`）：
  - 3 条目标中 2 条补出框，1 条仍无框；
  - 值/状态/标签均正确，但 IoU 为 0.4888、0.2802，均未达 0.5，标注得分 0/7；
  - 补框能缩小与金标的差距，但尚不足以直接得分，因此不进入正式链路。

## 4. 文件

- `thinking_outputs.jsonl` / `.trace.jsonl`：thinking 全量 64 条原始输出与模型原始响应；
- `thinking_outputs_repaired.jsonl`：按新 JSON 修复规则回放后的输出（仅 4 条受影响单元被替换）；
- `thinking_metrics.json`：Flash 与 thinking 的逐规则对比、修复/退化单元清单、延迟分布；
- `replay_result.json`：4 条失败单元的离线回放结果与修复前后指标；
- `annotations_locate_fallback.*` / `annotation_score.json`：定向定位试点输出、失败记录、轨迹与 IoU 评分。
