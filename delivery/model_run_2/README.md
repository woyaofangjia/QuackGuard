# 第二轮真实模型运行记录（glm-4v-flash）

本目录是 2026-11-26 的第二次独立回跑，代码与产物版本与 `delivery/model_run/` 相同（`av_a489476e2465379a03414735`），用于记录模型侧波动，不覆盖第一轮结果。

## 与第一轮的差异

| 项目 | 第一轮 `model_run/` | 第二轮 `model_run_2/` |
| --- | --- | --- |
| 正确（标签 + 适用性） | 39 / 64 | 38 / 64 |
| A | 0.609375 | 0.593750 |
| U | 0.673077 | 0.673077 |
| Q = 0.7A + 0.3U | 0.628486 | 0.617548 |
| 适用性正确率 | 0.984375 | 0.984375 |
| 机审率 / 机审正确率 | 0.8125 / 0.673077 | 0.8125 / 0.673077 |
| 执行错误 | 1（`invalid_model_output`） | 1（`invalid_model_output`） |
| 标注得分 | 0 / 7 | 0 / 7 |

- 两轮共有 6 个单元结论变化：`uec9d90d…`(R060)、`ub801208…`(R014)、`u9408e5d…`(B001 3.0)、`u463143f…`(B001 3.0)、`uacad350…`(B001 3.1)、`uf2a3e6a…`(B001 3.0)。
- 执行错误从 `uacad350…` 转移到 `uf2a3e6a…`，两轮各 1 条，仍保留在分母。
- 两轮使用相同提示词、`temperature=0`、相同产物；差异来自模型服务端输出波动，不是代码或产物回归。
- 第一轮结果仍保留在 `delivery/model_run/`，未被改写。

## 标注结果（3 条）

- 值、状态、标签、材料、页码 **3/3 正确**：`pass` / `ambiguous` / `reject`；无失败。
- 字段框 0/3 达到 IoU ≥ 0.5：模型把三条文字行分别定位到约 `y=0.45`、`y=0.45–0.52`、`y=0.72–0.78`，参考位置约为 `y=0.18`、`y=0.18`、`y=0.31`，与第一轮相同的系统性纵向偏移。
- 本次严格按字段框输出，未触发 `annotation_bbox_missing`，也未用整帧或文档框替代。

## 运行命令

```powershell
python run.py --db var/live_run2/live.sqlite3 build --rules var/live_run2/rules.jsonl --config delivery/config.json --out var/live_run2/artifacts
python run.py --db var/live_run2/live.sqlite3 infer --input "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --artifacts var/live_run2/artifacts --out var/live_run2/outputs.jsonl
python run.py --db var/live_run2/live.sqlite3 annotate --input "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/tasks.jsonl" --targets "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/targets.jsonl" --artifacts var/live_run2/artifacts --out var/live_run2/annotations.jsonl
python run.py evaluate --manifest quackguard/plugin_manifest.json --inputs "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --outputs var/live_run2/outputs.jsonl --references "O2_AI商品审核官_资料包/03_公开开发集/references/audit_gold.jsonl" --out var/live_run2/evaluation.json
```

## 文件

- `outputs.jsonl` / `outputs.trace.jsonl`：64 条审核输出与事实轨迹。
- `annotations.jsonl` / `annotations.errors.json` / `annotations.trace.jsonl`：3 条标注输出、失败清单与轨迹。
- `evaluation.json` / `plugin_report.html`：两个插件报告（`input_unchanged=true`、`errors=0`）。
- `metrics.json`：本轮指标、混淆矩阵与标注逐条结果。
- `resource_report.json`：本轮调用次数、延迟、插件耗时与产物大小；token/费用未知保持 `null`。
- `review.html`：逐单审核与证据页面。
