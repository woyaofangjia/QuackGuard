# 第三轮运行记录（P0 修复后，glm-4v-flash）

本目录是 P0 修复后的真实回跑，产物版本仍为 `av_a489476e2465379a03414735`，用于与第二轮对照，不改写历史记录。

## P0 改动

1. **R026**：去掉“`declared_icon` 不为三角点标就跳过”的提前返回；改为始终检查正面视图，并按三角形与实心点的存在性、点数、中心坐标判断。三角有而点缺失、点数量不为 1、点中心明显在三角范围外均判 `reject`。
2. **B001**：证明页缺少 `carrier` / `attached_to_package` 时补 `null`，不再因结构缺键直接报 `Invalid document structure`。

## 结果对比

| 指标 | 第二轮 `model_run_2/` | 第三轮 `model_run_3_p0/` |
| --- | --- | --- |
| 正确（标签 + 适用性） | 38 / 64 | **43 / 64** |
| A | 0.593750 | **0.671875** |
| U | 0.673077 | **0.711538** |
| Q = 0.7A + 0.3U | 0.617548 | **0.683774** |
| 适用性正确率 | 0.984375 | 0.984375 |
| 机审率 / 机审正确率 | 0.8125 / 0.673077 | 0.765625 / 0.755102 |
| 执行错误 | 1 | 1 |

- R026 公开集 4 条从 1/4 提升到 **4/4**（离线重放同样得到 +3）。
- B001 上一轮的执行错误单元 `uf2a3e6a…` 本轮为 `ambiguous`，与 gold 一致。
- 第三轮整体比“仅 R026 重放”的 41/64 多 2 条：1 条来自 B001 结构容错，1 条来自模型输出波动。
- 剩余 1 条错误为 R050 `invalid_model_output`：模型返回了重复 JSON 键 `cut_line_crosses_label`，当前解析器拒绝重复键。属下一阶段 P1 的输出容错项。

## 标注结果

- 3 条全部产出、0 失败；值、状态、标签、材料、页码 3/3 正确。
- 框 IoU 仍 0/3：模型继续把文字行定位到 y≈0.45、0.72，未解决坐标偏移；本轮未用文档框或整帧替代。
- 标注得分仍 0/7，等待 P1 的两阶段框定位。

## 文件

- `outputs.jsonl` / `outputs.trace.jsonl`：64 条审核输出与事实轨迹。
- `annotations.jsonl` / `annotations.errors.json` / `annotations.trace.jsonl`：3 条标注输出、失败清单与轨迹。
- `evaluation.json` / `plugin_report.html`：两个插件报告（`input_unchanged=true`、`errors=0`）。
- `metrics.json`：本轮指标与混淆矩阵。
- `resource_report.json`：92 次模型调用、延迟合计约 704.3 秒（均值 11.0 秒、最大 38.8 秒）。
- `review.html`：逐单审核与证据页面。

## 复现

```powershell
python run.py --db var/live_run3/live.sqlite3 infer --input "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --artifacts var/live_run2/artifacts --out var/live_run3/outputs.jsonl
python run.py --db var/live_run3/live.sqlite3 annotate --input "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/tasks.jsonl" --targets "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/targets.jsonl" --artifacts var/live_run2/artifacts --out var/live_run3/annotations.jsonl
```
