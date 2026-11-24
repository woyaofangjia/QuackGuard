# 测试报告（D10）

## 1. 环境

- Windows / PowerShell 5；Python 3.13.5。
- 仅标准库；无第三方包。
- 测试命令统一设置 `PYTHONDONTWRITEBYTECODE=1`，避免生成缓存。

## 2. 全量测试

```powershell
python -m pytest tests -q
```

- 结果：**90 passed，580 subtests passed**（D10 用例 9 个）。
- 覆盖 D1–D10：构建、推理、标注、迭代、评估、比较、导出、插件、版本、回归与验收冻结。

## 3. 干净虚拟环境

```powershell
python -m venv <临时目录>
<临时目录>\Scripts\python.exe -m pip list --format=freeze
<临时目录>\Scripts\python.exe -m unittest discover -s tests -q
```

- `pip list` 仅有 `pip` 自身，未安装任何第三方包。
- `unittest discover` 运行 **90 tests OK**。

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

- `ok=true`；`artifact_version=av_2275824ec1ba861a0c0018cb`。
- manifest 条目 62；实际文件哈希不匹配 0；版本不匹配 0；重复条目 0。
- 公开集输出 64 条；缺失 0、重复 0、额外 0、版本不匹配 0。
- trace 缺失 0；2 条 B001 `rule_not_applicable` 无需事实轨迹，已单独记录；其余输出均可按 `trace_id` 回查，轨迹行 89。

## 6. 两人独立验收

- 评审 A、B 各自独立目录与数据库，16 项产品任务均 **16/16 通过**，通过向量一致。
- 插件故障：健康插件 `ok`；崩溃 `plugin_failed`；超时 `plugin_timeout`；禁用 `disabled` 且调用 0；输入哈希不变。
- 详见 `delivery/acceptance/acceptance.md`。

## 7. 五类命令干净运行

- 空目录依次运行 build/infer/annotate/iterate/evaluate，退出码均为 0。
- 生成 `status.html`、`versions.html`、`review.html`、`iteration.html`、`plugin_report.html`。
- 未配置模型：64 条中 62 条 `model_not_configured`，2 条 B001 `rule_not_applicable`；标注 3 条均显式失败，未产生伪标注。

## 8. 干净交付检查

- 仓库根无 `.env`；`delivery/` 与源码无密钥赋值。
- `delivery/` 无 `__pycache__`、`.pytest_cache`、`*.pyc`、`*.tmp`。
- 交付包不含虚拟环境、数据库或临时输出。

## 9. 冻结选择

- 交付活动版本：baseline `av_2275824ec1ba861a0c0018cb`。
- 候选 `av_a80de7291b9b55c33274c8e9` 仅为澄清记录，门槛全零；验收后回滚 baseline。
- 详见 `delivery/selection_freeze.json`。

## 10. 模型配置（glm-4v-flash）

- `delivery/config.json` 在官方样例配置上增加 `"model": "glm-4v-flash"`，不含密钥；网关与密钥通过 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 注入。
- `test_delivery_model_config` 校验该配置可被 build 解析：产物 `policy.model=glm-4v-flash`、规则 62 条、`artifact_version` 与 build 返回值一致。
- 测试环境未注入密钥，离线用例仍走 `model_not_configured` 显式错误路径，不产生伪造标签。
- 冻结快照 `delivery/artifacts/` 为离线构建；用模型配置重建会得到新 `artifact_version`，需重新核对 manifest。

## 11. glm-4v-flash 全量真实推理

- 环境变量：`OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4`、`OPENAI_API_KEY`；只写入本机用户环境，未进入仓库或交付包。
- 用 `delivery/config.json` 重建（`artifact_version=av_a489476e2465379a03414735`），对公开集 64 条全量真实推理，结果全部完成、0 错误：
  - accuracy=0.609375（39/64）
  - machine_review_rate=0.875（56/64）
  - machine_accuracy=0.660714
  - applicability_accuracy=1.0
  - quality=0.640024
- 兼容性修复：
  1. `glm-4v-flash` 的 `max_tokens` 上限为 1024（原 4096 返回 HTTP 400）。
  2. 模型整体包裹的 Markdown 代码围栏在网关注入前剥离。
  3. 列表字段归一：接受模型返回的字段对象数组、`null`。
  4. 无值字段省略 `value` 时补 `null`。
  5. 容忍 JSON 尾逗号。
  6. R 系列事实提示只输出识别到或无法辨认的键，缺失键省略，避免 1024 输出上限截断。
- 证据插件：结构校验 64/64 通过；内容核验仅 6/64（模型事实文本与参考事实不同，属预期，不影响结论指标）。
- 该次构建版本与冻结快照不同，仅用于验证模型链路；冻结快照 `delivery/artifacts/` 仍为离线构建。
