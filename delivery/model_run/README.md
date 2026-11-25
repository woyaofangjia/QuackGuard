# 真实模型运行记录（glm-4v-flash）

本目录是一次真实在线模型运行的冻结记录，与 `delivery/artifacts/`（离线构建）分开保存，不改变冻结产物与 `manifest_check.json`。

## 运行配置

- 服务商：智谱 AI；模型：`glm-4v-flash`；端点：`https://open.bigmodel.cn/api/paas/v4`。
- 密钥只从 `OPENAI_API_KEY` 环境变量读取，未写入仓库、交付包或本目录。
- 构建配置：`delivery/config.json`；规则：B001 3.0/3.1 + R001–R060，共 62 个规则版本。
- 产物版本：`av_a489476e2465379a03414735`。

## 运行命令

```powershell
python run.py --db var/live_run/live.sqlite3 build --rules var/live_run/rules.jsonl --config delivery/config.json --out var/live_run/artifacts
python run.py --db var/live_run/live.sqlite3 infer --input "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --artifacts var/live_run/artifacts --out var/live_run/outputs2.jsonl
python run.py --db var/live_run/live.sqlite3 annotate --input "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/tasks.jsonl" --targets "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/targets.jsonl" --artifacts var/live_run/artifacts --out var/live_run/annotations2.jsonl
python run.py evaluate --manifest quackguard/plugin_manifest.json --inputs "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --outputs var/live_run/outputs2.jsonl --references "O2_AI商品审核官_资料包/03_公开开发集/references/audit_gold.jsonl" --out var/live_run/evaluation2.json
```

## 公开集结果（64 条）

| 指标 | 值 |
| --- | --- |
| 正确（标签与适用性联合） | 39 / 64 |
| A（accuracy） | 0.609375 |
| U（pass/reject 真值组覆盖率） | 0.673077 |
| Q = 0.7A + 0.3U | 0.628486 |
| 适用性正确率 | 0.984375 |
| 机审率 / 机审正确率 | 0.8125 / 0.673077 |
| 执行错误 | 1（`invalid_model_output`） |
| invalid / missing / extraneous | 0 / 0 / 0 |

- 与首轮（提示词修正前）相比：7 个单元结论变化，正确数 37 → 39，Q 0.606611 → 0.628486。
- 两个评估插件均 `status=ok`、`input_unchanged=true`、`errors=0`；证据内容插件 `grounded_units=6`、`unsupported_fact_count=79`，说明模型事实文本与参考可见事实仍有差距。

## 标注结果（公开样例 3 条）

- 值、状态、标签、材料、页码 3/3 正确；`label` 分别为 pass / ambiguous / reject，与参考一致。
- 标注框 0/3 达到 IoU ≥ 0.5：`glm-4v-flash` 返回的框在包装图上存在系统性纵向偏移（把标签内容定位到约 y=0.45，实际在约 y=0.18），属模型坐标可靠性问题，不是判定逻辑问题。
- 因此公开样例标注得分为 0/7。若需提高标注分，应改用带 grounded detection 的视觉模型或补充框定位后处理；当前交付如实保留该限制。

## 文件

- `outputs.jsonl` / `outputs.trace.jsonl`：64 条审核输出与事实轨迹。
- `annotations.jsonl` / `annotations.errors.json` / `annotations.trace.jsonl`：3 条标注输出、失败清单与轨迹。
- `evaluation.json` / `plugin_report.html`：两个评估插件报告。
- `metrics.json`：两轮指标、混淆矩阵与逐单元变化。
- `review.html`：逐单审核与证据页面（含产物版本、trace、规则源哈希）。
