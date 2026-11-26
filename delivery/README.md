# QuackGuard 商品审核官 — 运行说明（D10 冻结）

## 1. 交付内容

- `quackguard/`：产品源码（Python 标准库，无第三方依赖）。
- `run.py`：命令行入口；`python run.py console --workspace var/console` 启动本地操作控制台。
- `delivery/artifacts/`：冻结产物（`engine/`、`artifact_manifest.json`、`status.html`、`versions.html`、`60条建设台账.csv`）。
- `delivery/traces/`：推理与公开集验收轨迹。
- `delivery/annotations/`：标注输出、标注失败记录、事实轨迹与公开样例评分（`annotations.jsonl`、`annotations.errors.json`、`annotations.trace.jsonl`、`score.json`、`meta.json`）。
- `delivery/evaluation.json`：两个评估插件的实际运行报告；同目录 `plugin_report.html` 为对应页面。
- `delivery/model_run/`：一次真实 `glm-4v-flash` 在线运行记录（构建版本 `av_a489476e2465379a03414735`、公开集 64 条输出、标注输出、轨迹、插件报告、指标与逐单元变化），与离线冻结快照分开保存。
- `delivery/model_run_2/`：2026-11-26 第二次独立回跑记录，命名与第一轮分开；同产物版本下 A=0.59375、Q=0.617548，6 个单元结论变化，含本轮标注、轨迹、插件报告、指标与资源报告。
- `delivery/model_run_3_p0/`：P0 修复后的第三轮回跑；R026 提升到 4/4、B001 结构错误消除，A=0.671875、U=0.711538、Q=0.683774，含输出、标注、轨迹、插件报告、指标与资源报告。
- `delivery/acceptance/`：两轮独立操作的 16 项验收记录与插件故障记录（自测证据，非产品内置评审模式）。
- `delivery/config.json`：官方样例配置 + `"model": "glm-4v-flash"`，不含任何密钥。
- `delivery/requirements.txt`：依赖锁定说明（运行时仅 Python 3.13 标准库；仅测试使用 `pytest==9.1.1`）。
- `delivery/resource_report.json`：真实模型运行的机读资源报告（调用次数、逐单延迟、插件耗时、产物大小；token 与费用未知保持 null）。
- `delivery/policy.json`：B001 3.0/3.1 的 SOP、build policy 与产物描述。
- `delivery/manifest_check.json`：manifest 与实际文件哈希、输出 `artifact_version`、trace 核对结果。
- `delivery/selection_freeze.json`：baseline/candidate 选择与回滚冻结。
- `O2_AI商品审核官_资料包/`：赛题提供的只读资料包（公开发布集、规则包、协议），不随源码修改。

## 2. 环境

- Python 3.13（仅标准库，无需 `pip install`）。
- PowerShell 5 下不要使用 `&&`；分步执行命令。
- 不需要 `.env`；仓库与交付包内无任何密钥，密钥只从环境变量读取。

## 3. 五类命令

以下命令均在仓库根目录执行；`--db` 可省略，但持久化、版本激活、重试需要 SQLite。

```powershell
# 0) 合并规则（资料包只读，合并到本地）
Get-Content "O2_AI商品审核官_资料包/01_规则包/基础规则版本.jsonl","O2_AI商品审核官_资料包/01_规则包/进阶规则60条.jsonl" | Set-Content -Encoding UTF8 rules.jsonl

# 1) 构建：合并 B001 3.0/3.1 与 R001–R060（使用自带 glm-4v-flash 的交付配置）
python run.py build --rules rules.jsonl --config delivery/config.json --out artifacts --db quackguard.sqlite3

# 2) 推理
python run.py infer --input "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --artifacts artifacts --out outputs.jsonl --db quackguard.sqlite3

# 3) 标注
python run.py annotate --input "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/tasks.jsonl" --targets "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/targets.jsonl" --artifacts artifacts --out annotations.jsonl --db quackguard.sqlite3

# 4) 迭代（feedback 为协议 JSON）
python run.py iterate --base artifacts --feedback feedback.json --input "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --out candidate --db quackguard.sqlite3

# 5) 评估（注册并运行两个独立插件）
python run.py evaluate --manifest quackguard/plugin_manifest.json --inputs "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --outputs outputs.jsonl --references "O2_AI商品审核官_资料包/03_公开开发集/references/audit_gold.jsonl" --out evaluation.json --db quackguard.sqlite3
```

`合并规则.jsonl` 由 `基础规则版本.jsonl` 与 `进阶规则60条.jsonl` 按行拼接得到；冻结演示也可直接使用 `delivery/artifacts`。

版本与插件管理：

```powershell
python run.py activate --baseline artifacts --candidate candidate --out activate.json --db quackguard.sqlite3 --gate gate.json
python run.py rollback --to artifacts --out rollback.json --db quackguard.sqlite3 --expect-hash <baseline-root-sha256>
python run.py plugins --out plugins.json --db quackguard.sqlite3 --manifest quackguard/plugin_manifest.json
python run.py plugin-state --plugin-id quackguard.evidence_content --plugin-version 0.2.0 --state disabled --out plugin-state.json --db quackguard.sqlite3
```

`regress` 为 D9 全量回归命令，参数见 `docs`/`--help`。

## 4. 产品页面与操作控制台

完全不懂技术的评审可直接启动本地控制台：

```powershell
python run.py console --workspace var/console
# 浏览器打开输出里的 http://127.0.0.1:8765/
```

控制台为单页、仅标准库、只监听本机，单一工作区即可完成全部操作。左侧固定入口覆盖：规则导入（含逐行错误定位）、建设、审核、证据定位、标注、迭代、版本比较/激活/回滚、插件配置与失败重试；审核页每一行可打开规则原文、理由、材料与区域框、产物版本与 trace。

命令行也会在输出目录生成可直接打开的 HTML：

- `artifacts/status.html`：构建状态、规则失败行号。
- `artifacts/versions.html`：规则版本与产物版本。
- `outputs.jsonl` 同目录 `review.html`：逐单审核、证据、规则源哈希、产物版本与 trace。
- `candidate/iteration.html`：候选差异与门槛。
- `plugin_report.html`：插件运行、输入哈希与故障隔离。
- `plugins.html`：插件注册与启用状态。

## 5. 模型配置（智谱 glm-4v-flash）

- 开发与运行统一使用智谱 AI 免费视觉模型 `glm-4v-flash`；`delivery/config.json` 已写入 `"model": "glm-4v-flash"`。
- 网关地址与密钥只从环境变量读取，不写入仓库和交付包：

```powershell
$env:OPENAI_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
$env:OPENAI_API_KEY = Read-Host "请输入智谱 API Key"
```

- `OPENAI_BASE_URL` 指向 OpenAI 兼容端点，程序会自动追加 `/chat/completions`；智谱该端点接受 `data:image/png;base64,...` 形式的图片输入。
- 未配置 `model` 或地址时，推理按协议输出 `execution_status=error`、`error_code=model_not_configured` / `gateway_not_configured`，不伪造标签。
- `model` 参与 `artifact_version` 计算，因此用 `delivery/config.json` 重建会得到新版本，需重新核对 manifest；`delivery/artifacts/` 是离线构建的冻结快照（无模型），只用于离线验收与证据核对。
- 模型只负责从图片材料抽取事实，标签仍由冻结的规则实现判定。
- 建设产物实际约束推理，不是展示文档：`policy.implementations["<rule_id>@<version>"]` 决定使用哪个处理器；B001 的阈值取自 `sop.evidence_requirements.outer_package_threshold_mm`；R001–R060 的参数取自 `sop.params` 并传入规则判定函数；产物缺少对应键时直接报 `rule_artifact_not_built`。

## 6. 干净目录验证

```powershell
$env:PYTHONDONTWRITEBYTECODE="1"
python -m pytest tests/test_d10.py -q
python -m pytest tests -q
python "O2_AI商品审核官_资料包/04_最小对接样例/smoke.py"
```

干净目录判定：

- 五类命令均返回 0，并生成上列 HTML；
- `delivery/manifest_check.json` 的 `ok=true`；
- 公开集输出 64 条，ID 无缺失/重复，`artifact_version` 与产物一致，trace 可回查；
- 仓库根无 `.env`、`__pycache__`、`.pytest_cache`、`*.tmp`。

## 7. 冻结与已知限制

- 活动版本冻结为 baseline：`artifact_version=av_2275824ec1ba861a0c0018cb`，候选仅记录澄清，不改变判定，验收后已回滚。
- 冻结快照 `delivery/artifacts/` 为离线构建：未配置模型时，64 条公开任务中 62 条显式 `model_not_configured`，2 条 B001 因材料不适配可直接判定 `not_applicable`。需要真实审核时按第 5 节配置 `glm-4v-flash` 并用 `delivery/config.json` 重建。
- `delivery/annotations/` 用公开金标可见事实驱动同一 `annotate` 实现，验证值/状态/标签/材料/页码/IoU 联合链路；公开样例得分为 7/7，但属于链路校验，不是系统正式标注成绩。配置模型后重跑 `annotate` 即得到真实标注输出。
- `delivery/evaluation.json` 基于 `traces/infer.outputs.jsonl` 运行两个插件：输入哈希前后一致、故障 0；指标反映离线快照（多数为显式 error），不代表模型能力。
- 真实模型结果见 `delivery/model_run/`：公开集 A=0.609375、U=0.673077、Q=0.628486，执行错误 1；标注的值/状态/标签 3/3 正确，但 `glm-4v-flash` 返回的框存在系统性坐标偏移，IoU 不达标，标注得分 0/7。该限制已如实记录，未用整帧或文档框冒充。
- `annotate` 只接受字段自身的文字行框；模型未给出字段框时显式失败 `annotation_bbox_missing`，不退回文档区域或整帧。
- 第二轮独立回跑见 `delivery/model_run_2/`：38/64、A=0.59375、U=0.673077、Q=0.617548，标记 3/3 正确但框 IoU 仍不达标；与第一轮差 1 条正确，6 个单元结论变化，属模型服务端波动，不是代码回归。
- P0 修复后第三轮见 `delivery/model_run_3_p0/`：R026 公开集 4/4、B001 证明页缺载体字段不再报结构错误，43/64、A=0.671875、U=0.711538、Q=0.683774；剩余 1 条错误为 R050 重复 JSON 键，留待 P1 容错。
- R021/R026/R060 的参考输出与契约存在已知语义差异，只影响 A/U/Q，不属于非法输出。
- 公开参考基线 Q=1 仅证明评分算术，不计入系统成绩。
