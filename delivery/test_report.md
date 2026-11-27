# 测试报告（D10）

## 1. 环境

- Windows / PowerShell 5；Python 3.13.5。
- 仅标准库；无第三方包。
- 测试命令统一设置 `PYTHONDONTWRITEBYTECODE=1`，避免生成缓存。

## 2. 全量测试

```powershell
python -m pytest tests -q
```

- 结果：**118 passed，583 subtests passed**（D10 冻结时为 109；此后新增 OCR 定位、结构化事实与迭代补丁测试 9 项）。
- 覆盖 D1–D10：构建、推理、标注、迭代、评估、比较、导出、插件、版本、回归与验收冻结；以及本地控制台的导入/建设/审核联动、A/B 隔离、证据接口与路径限制。

## 3. 干净虚拟环境

```powershell
python -m venv <临时目录>
<临时目录>\Scripts\python.exe -m pip list --format=freeze
<临时目录>\Scripts\python.exe -m unittest discover -s tests -q
```

- `pip list` 仅有 `pip` 自身，未安装任何第三方包。
- `unittest discover` 运行 **118 tests OK**（当前仓库；D10 冻结时为 109）。

## 4. 官方 smoke

```powershell
python "O2_AI商品审核官_资料包/04_最小对接样例/smoke.py"
```

- `passed=true`，覆盖 `build / infer / annotate / iterate / evaluate`。
- `offline_model_errors_explicit=true`：未配置模型时显式 error，不伪造标签。
- `reference_annotation_score=7`、`reference_audit_quality=1` 为参考输出算术校验，不是系统成绩。
- `baseline_unchanged=true`、`hash_vector_match=true`。

## 5. D10 冻结核对

来自 `delivery/manifest_check.json`：

- `ok=true`；`artifact_version=av_a489476e2465379a03414735`。D10 冻结时记录为 `av_2275…`；最终交付按 `delivery/config.json` 重建为 `av_a489…`，本节数字按当前 `manifest_check.json` 更新，历史阶段以 `model_run_*` 为准。
- manifest 条目 62；实际文件哈希不匹配 0；版本不匹配 0；重复条目 0。
- 公开集输出 64 条；缺失 0、重复 0、额外 0、版本不匹配 0。
- trace 缺失 0；2 条 B001 `rule_not_applicable` 无需事实轨迹，已单独记录；其余输出均可按 `trace_id` 回查，轨迹行 89。

## 6. 两人独立验收

- 评审 A、B 为同一冻结规则下的程序化双轮（各自独立目录与数据库，非外部真人），16 项产品任务均 **16/16 通过**，通过向量一致。
- 插件故障：健康插件 `ok`；崩溃 `plugin_failed`；超时 `plugin_timeout`；禁用 `disabled` 且调用 0；输入哈希不变。
- 详见 `delivery/acceptance/acceptance.md`。

## 7. 五类命令干净运行

- 空目录依次运行 build/infer/annotate/iterate/evaluate，退出码均为 0。
- 生成 `status.html`、`versions.html`、`review.html`、`iteration.html`、`plugin_report.html`。
- 未配置模型：64 条中 62 条 `model_not_configured`，2 条 B001 `rule_not_applicable`；标注 3 条均显式失败，未产生伪标注。

## 7.1 本地操作控制台

- `python run.py console --workspace <目录>` 启动后，浏览器访问 `http://127.0.0.1:<端口>/`。
- 页面含 8 个固定入口（规则导入、建设、审核、证据、标注、迭代、版本/回滚、插件）。
- 实测：页面加载无 JavaScript 错误；审核表 64 行；点击任一行可显示规则原文、结论理由、材料图片与区域框、产物版本、trace 与规则源哈希。
- A/B 工作区、SQLite、产物目录完全独立；`/media` 与 `/report` 只允许访问当前工作区或赛题材料目录内的文件。

## 8. 干净交付检查

- 仓库根无 `.env`；`delivery/` 与源码无密钥赋值。
- `delivery/` 无 `__pycache__`、`.pytest_cache`、`*.pyc`、`*.tmp`。
- 交付包不含虚拟环境、数据库或临时输出。

## 9. 冻结选择

- 交付活动版本：baseline `av_a489476e2465379a03414735`。
- 候选 `av_a80de7291b9b55c33274c8e9` 仅为澄清记录，门槛全零；验收后回滚 baseline。
- 详见 `delivery/selection_freeze.json`。

## 10. 模型配置（glm-4v-flash）

- `delivery/config.json` 在官方样例配置上增加 `"model": "glm-4v-flash"`，不含密钥；网关与密钥通过 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 注入。
- `test_delivery_model_config` 校验该配置可被 build 解析：产物 `policy.model=glm-4v-flash`、规则 62 条、`artifact_version` 与 build 返回值一致。
- 测试环境未注入密钥，离线用例仍走 `model_not_configured` 显式错误路径，不产生伪造标签。
- 冻结快照 `delivery/artifacts/` 为离线构建；用模型配置重建会得到新 `artifact_version`，需重新核对 manifest。

## 11. glm-4v-flash 全量真实推理

- 环境变量：`OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4`、`OPENAI_API_KEY`；只写入本机用户环境，未进入仓库或交付包。
- 用 `delivery/config.json` 重建（`artifact_version=av_a489476e2465379a03414735`），对公开集 64 条全量真实推理：
  - accuracy=0.609375（39/64）
  - machine_review_rate=0.8125（52/64）
  - machine_accuracy=0.673077
  - applicability_accuracy=0.984375
  - correct_decisive_coverage=0.673077
  - quality=0.628486
  - 执行错误 1（`invalid_model_output`），invalid/missing/extraneous 均为 0
- 标注样例真实运行：3 条的值、状态、标签、材料、页码均正确；`glm-4v-flash` 返回的字段框存在系统性纵向偏移，IoU 0/3 达标，标注得分 0/7。该限制如实保留，未用整帧或文档框冒充。
- 上述真实运行产物见 `delivery/model_run/`，提示词修正前后对比见该目录 `metrics.json`（第一轮 Q=0.606611，修正后 Q=0.628486，7 个单元结论变化）。
- 第二次独立回跑（2026-11-26，`delivery/model_run_2/`）：accuracy=0.59375（38/64）、machine_review_rate=0.8125、machine_accuracy=0.673077、quality=0.617548，执行错误 1；6 个单元结论变化，属模型服务端输出波动；标注值/状态/标签 3/3 正确，框 IoU 0/3。
- P0 修复后第三轮（`delivery/model_run_3_p0/`）：R026 公开集 4/4、B001 证明页缺载体字段可解析；accuracy=0.671875（43/64）、U=0.711538、quality=0.683774，执行错误 1（R050 重复 JSON 键）；标注仍为 0/7。
- P1 修复后第四轮（`delivery/model_run_4_p1/`）：accuracy=0.75（48/64）、U=0.826923、quality=0.773077，执行错误 0；标注值/状态/标签 3/3 正确，框 IoU 0/3。3×3 网格与 R050 组件二次提取经实测无效，已回退。
- P2 模型选型内部横评（`delivery/model_run_5_p2/`，非正式产物）：`glm-4.1v-thinking-flash` 全量 44/64、A=0.6875、U=0.730769、Q=0.700481、执行错误 4；平均延迟 19.90 s、P50 19.87 s、P95 33.60 s、最大 60.86 s。JSON 硬兜底回放后 45/64、Q=0.717187、执行错误 1（B001 字段 `status="clear"` 语义非法）。均低于 Flash 基线 48/64、Q=0.773077、10.06 s、0 错误，故正式产物保持 `glm-4v-flash`。
- `--locate-fallback` 定向定位（默认关闭）：3 条目标补出 2 条框，值/状态/标签正确，IoU 0.4888、0.2802 未达 0.5，标注得分 0/7；保留为评测工具。
- P3 事实通道修复（`delivery/model_run_6_p3/`）：58/64、A=0.90625、U=0.961538、Q=0.922837、执行错误 0、平均延迟 10.90 s；修复 11 条、退化 1 条（R014 批次遮挡）。R050 4/8→7/8，R005/R010/R041/R048 子集全对，R021 2/4，B001 19/20，R035 4/4。定向定位探测 IoU 最高 0.4852，标注仍 0/7。
- 兼容性修复：
  1. `glm-4v-flash` 的 `max_tokens` 上限为 1024（原 4096 返回 HTTP 400）。
  2. 模型整体包裹的 Markdown 代码围栏在网关注入前剥离。
  3. 列表字段归一：接受模型返回的字段对象数组、`null`。
  4. 无值字段省略 `value` 时补 `null`。
  5. 容忍 JSON 尾逗号。
  6. R 系列事实提示只输出识别到或无法辨认的键，缺失键省略，避免 1024 输出上限截断。
  7. B001 事实提示要求每个字段给出文字行 bbox，并区分 `unreadable` 与 `missing`（遮挡/模糊/裁切一律 `unreadable`）。修正后公开集正确数 37→39，标注第 2 条结论由 `reject` 纠正为 `ambiguous`。
  8. JSON 最后兜底：尾逗号、括号补齐后仍失败时，取第一个完整 JSON 值并容忍重复键；不伪造标签，语义非法仍如实报错并计入分母。
  9. 事实键归一：中文键（签发日/有效起始日/有效截止日/型号/主型号等）映射到规则键；texts 短文案误入 fields 时挪回，`old_model_relation` 保留在 fields。
  10. R050 组件关系定向抽取（行式/矩阵/组件表三种形式），R021 标签边界定向复核；结果合并到事实且失败不阻断主流程。
  11. R035 定向读取“保持干燥/Keep dry”，公开集该规则 4/4；短文案误入 fields 时挪回 texts。
  12. 模型网关稳定性：429/5xx/网络错误按 1→2→4→8 s 退避，最多重试 4 次；可选本地缓存（`QUACKGUARD_MODEL_CACHE`）按模型+messages+max_tokens 哈希复用，默认关闭。
  13. 交付产物重建同步为 `av_a489476e2465379a03414735`，`manifest_check.json` 为 ok（产物只含规则与配置，代码在 `quackguard/`）。
- 证据插件：结构校验 64/64 通过；内容核验仅 6/64（模型事实文本与参考事实不同，属预期，不影响结论指标）。
- 该次构建版本与冻结快照不同，仅用于验证模型链路；冻结快照 `delivery/artifacts/` 仍为离线构建。
