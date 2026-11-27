# QuackGuard 商品审核官 — 运行说明（D10 冻结）

**公开开发集：58/64 正确 · A=0.90625 · U=0.961538 · Q=0.922837 · 执行错误 0**

| 指标 | 结果 |
| --- | --- |
| 正确 / 总数 | 58 / 64 |
| A（标签 + 适用性准确率） | 0.90625 |
| U（正确决定覆盖率） | 0.961538 |
| Q = 0.7A + 0.3U | 0.922837 |
| 执行错误 | 0（64 条全部有结果） |
| 延迟 | 平均 10.90 s，P95 19.32 s，最大 28.50 s |
| 冻结产物 | `av_a489476e2465379a03414735`（62 条规则版本） |

干净环境复跑（2026-11-27）：`python -S -E`、无 `PYTHONPATH`、无模型缓存、真实 API；构建版本与上表一致，64 条 0 执行错误，得 **57/64、A=0.890625、Q=0.906130**。与留档 58/64 的差异是一单 R026（服务端波动，交付为 reject、复跑为 pass、金标 reject）。OCR 标注在同一干净产物上单独跑：3/3 正确、**7.0/7**，IoU 0.7182/0.6187/0.6043（真实 API、无缓存）。证据见 `delivery/clean_run/`。上表与正文的 58/64 仍指留档运行 `model_run_6_p3`，不是多次运行的最好值。

> OCR 依赖提醒：除 `requirements-ocr.txt` 外，必须保留仓库根目录的 `quackguard_ocr/`（或将其加入 `sys.path`）。只安装 RapidOCR 而缺该包时会静默回退模型框，标注回到 0/7。

架构（模型看事实，规则做判决）：

```text
商品材料（图片 / PDF）
        │
        ▼
事实抽取  glm-4v-flash + 严格 JSON（字段 / 文本 / 组件 / 矩阵，逐材料）
        │        失败即 execution_status=error、label=null，不伪造结论
        ▼
冻结规则引擎  <rule_id>@<rule_version>，参数来自建设产物 policy / sop
        │
        ▼
label ∈ {pass, reject, ambiguous} + reason_code / reason
+ 证据（material_id / page_index / bbox）+ trace + artifact_version
```

模型不输出违规标签，只描述“看到了什么”；判定由冻结规则实现完成，单条结论可回查到规则版本与证据。

30 秒离线演示（无需网络、无需密钥）：

```powershell
python demo.py          # 彩色终端回放前 3 条真实结果
python demo.py --all    # 回放全部 64 条
```

## 1. 交付内容

- `quackguard/`：产品源码（Python 标准库，无第三方依赖）。
- `run.py`：命令行入口；`python run.py console --workspace var/console` 启动本地操作控制台。
- `demo.py`：零依赖离线演示；回放 `model_run_6_p3` 的真实结果，彩色打印标签、规则、模型事实与证据框。
- `delivery/artifacts/`：冻结产物（`engine/`、`artifact_manifest.json`、`status.html`、`versions.html`、`60条建设台账.csv`）。
- `delivery/60条建设台账.csv`：交付清单要求的台账副本，与 `artifacts/60条建设台账.csv` 内容一致。公开集只覆盖 B001 与 10 条 R 规则（共 64 个单元）；其余 50 条没有公开单元，`accuracy` 与 `machine_review_rate` 标记为 `n/a`，不填估算值。
- `delivery/iteration_public/`：公开 6 条 Bad Case 的反馈包、`decision_patches.json`、候选输出与前后指标，用于复核“迭代确实改判定、保护集不降”。
- `manifest_check.json`：`public_*` 是金标可见事实回放的哈希/轨迹一致性检查，不代表模型准确率；真实模型结果与指标以 `real_run` 指向的 `model_run_6_p3` 为准。
- `delivery/traces/`：推理与公开集验收轨迹。
- `delivery/annotations/`：标注输出、标注失败记录、事实轨迹与公开样例评分（`annotations.jsonl`、`annotations.errors.json`、`annotations.trace.jsonl`、`score.json`、`meta.json`）。
- `delivery/evaluation.json`：两个评估插件的实际运行报告；同目录 `plugin_report.html` 为对应页面。
- `delivery/model_run/`：一次真实 `glm-4v-flash` 在线运行记录（构建版本 `av_a489476e2465379a03414735`、公开集 64 条输出、标注输出、轨迹、插件报告、指标与逐单元变化），与离线冻结快照分开保存。
- `delivery/model_run_2/`：2026-11-26 第二次独立回跑记录，命名与第一轮分开；同产物版本下 A=0.59375、Q=0.617548，6 个单元结论变化，含本轮标注、轨迹、插件报告、指标与资源报告。
- `delivery/model_run_3_p0/`：P0 修复后的第三轮回跑；R026 提升到 4/4、B001 结构错误消除，A=0.671875、U=0.711538、Q=0.683774，含输出、标注、轨迹、插件报告、指标与资源报告。
- `delivery/model_run_4_p1/`：P1 修复后的第四轮完整回跑；载体回退与分层缺失提示生效，A=0.75、U=0.826923、Q=0.773077、执行错误 0；网格定位与组件二次提取经实测无效已回退，过程记录在该目录 README。
- `delivery/model_run_5_p2/`：P2 模型选型内部横评（非正式产物）。thinking 全量 64 条 44/64、Q=0.700481（JSON 修复回放后 45/64、Q=0.717187），平均延迟 19.90 s，不敌 Flash 基线 48/64、Q=0.773077、10.06 s；定向定位补丁试点 IoU 0.4888/0.2802，得分 0/7。正式产物保持 Flash。
- `delivery/model_run_6_p3/`：P3 事实通道、定向复核与稳定性；全量 58/64、A=0.90625、U=0.961538、Q=0.922837、执行错误 0、平均延迟 10.90 s；修复 11 条、退化 1 条（R014 批次遮挡）。交付产物已同步重建为 `av_a489476e2465379a03414735`，`manifest_check.json` 为 ok。
- `delivery/demo_cache/`：105 条真实模型响应缓存（约 0.09 MB），按 `model + messages + max_tokens` 哈希存放；断网或限流时直接命中，演示见第 4 节。
- `delivery/acceptance/`：两轮独立操作的 16 项验收记录与插件故障记录（自测证据，非产品内置评审模式）。
- `delivery/config.json`：官方样例配置 + `"model": "glm-4v-flash"`，不含任何密钥。
- `delivery/requirements.txt`：依赖锁定说明（运行时仅 Python 3.13 标准库；仅测试使用 `pytest==9.1.1`）。
- `delivery/resource_report.json`：真实模型运行的机读资源报告（调用次数、逐单延迟、插件耗时、产物大小；token 与费用未知保持 null）。
- `delivery/policy.json`：B001 3.0/3.1 的 SOP、build policy 与产物描述。
- `delivery/manifest_check.json`：manifest 与实际文件哈希、输出 `artifact_version`、trace 核对结果。
- `delivery/selection_freeze.json`：baseline/candidate 选择与回滚冻结。
- `O2_AI商品审核官_资料包/`：赛题提供的只读资料包（公开发布集、规则包、协议），不随源码修改。

`model_run` → `model_run_6_p3` 按时间构成“基线 → P0 → P1 → P2 → P3”的完整证据链，便于复核每一步改动。关于规则数量：`60条建设台账.csv` 沿用赛题命名，只登记 R001–R060 共 60 条业务规则；B001 3.0/3.1 属 SOP 型规则，记录在 `policy.json` 与 `artifact_manifest.json`，因此冻结产物共 62 条规则版本。

## 2. 环境

- Python 3.13（默认仅标准库，无需 `pip install`）。
- PowerShell 5 下不要使用 `&&`；分步执行命令。
- 不需要 `.env`；仓库与交付包内无任何密钥，密钥只从环境变量读取。
- 可选：本地 OCR 标注定位（完全离线、免费）。安装 `pip install -r requirements-ocr.txt` 后，`annotate` 会优先用 RapidOCR 识别字段行并以其框替换模型框；未安装或 `QUACKGUARD_OCR=0` 时自动回退，行为与之前一致。

## 3. 核心命令

以下命令均在仓库根目录执行。`--db` 是全局参数，必须写在子命令之前（如 `python run.py --db quackguard.sqlite3 build ...`）；可省略，但持久化、版本激活、回滚与重试需要 SQLite。

```powershell
# 0) 合并规则（资料包只读，合并到本地；用 Python 写入以避免 PowerShell 5 的 UTF-8 BOM 破坏解析）
python -c "from pathlib import Path; d=Path('O2_AI商品审核官_资料包/01_规则包'); t=(d/'基础规则版本.jsonl').read_text(encoding='utf-8').strip()+'\n'+(d/'进阶规则60条.jsonl').read_text(encoding='utf-8').strip()+'\n'; Path('rules.jsonl').write_text(t, encoding='utf-8')"

# 1) 构建：合并 B001 3.0/3.1 与 R001–R060（使用自带 glm-4v-flash 的交付配置）
python run.py --db quackguard.sqlite3 build --rules rules.jsonl --config delivery/config.json --out artifacts

# 2) 推理
python run.py --db quackguard.sqlite3 infer --input "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --artifacts artifacts --out outputs.jsonl

# 3) 标注
python run.py --db quackguard.sqlite3 annotate --input "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/tasks.jsonl" --targets "O2_AI商品审核官_资料包/03_公开开发集/标注样例/inputs/targets.jsonl" --artifacts artifacts --out annotations.jsonl

# 4) 迭代（feedback 为协议 JSON）
python run.py --db quackguard.sqlite3 iterate --base artifacts --feedback feedback.json --input "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --out candidate

# 5) 评估（注册并运行两个独立插件）
python run.py --db quackguard.sqlite3 evaluate --manifest quackguard/plugin_manifest.json --inputs "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --outputs outputs.jsonl --references "O2_AI商品审核官_资料包/03_公开开发集/references/audit_gold.jsonl" --out evaluation.json
```

`rules.jsonl` 由 `基础规则版本.jsonl` 与 `进阶规则60条.jsonl` 按行拼接得到；冻结演示也可直接使用 `delivery/artifacts`。

版本与插件管理：

```powershell
python run.py --db quackguard.sqlite3 activate --baseline artifacts --candidate candidate --gate gate.json --out activate.json
python run.py --db quackguard.sqlite3 rollback --to artifacts --expect-hash <baseline-root-sha256> --out rollback.json
python run.py --db quackguard.sqlite3 plugins --manifest quackguard/plugin_manifest.json --out plugins.json
python run.py --db quackguard.sqlite3 plugin-state --plugin-id quackguard.evidence_content --plugin-version 0.2.0 --state disabled --out plugin-state.json
```

`regress` 为 D9 全量回归命令，参数见 `docs`/`--help`。

## 4. 产品页面与操作控制台

完全不懂技术的评审可直接启动本地控制台：

```powershell
python run.py console --workspace var/console
# 浏览器打开输出里的 http://127.0.0.1:8765/
```

控制台为单页、仅标准库、只监听本机，单一工作区即可完成全部操作。左侧固定入口覆盖：规则导入（含逐行错误定位）、建设、审核、证据定位、标注、迭代、版本比较/激活/回滚、插件配置与失败重试；审核页每一行可打开规则原文、理由、材料与区域框、产物版本与 trace。

### 离线演示（无网络、无密钥、无第三方依赖）

```powershell
python demo.py                      # 彩色终端回放前 3 条真实结果
python demo.py --unit uec9d90d134    # 指定单元
python demo.py --all                # 全部 64 条
```

每条展示：最终标签（PASS / REJECT / AMBIGUOUS）、命中规则与版本、理由、模型抽取的事实字段，以及规则引用的证据图片区域框。

### 断网 / 限流缓存演示

`delivery/demo_cache/` 预置 105 条真实模型响应。用交付配置重建产物后，只设置网关地址与缓存目录（不设 `OPENAI_API_KEY`），即可完整跑通 64 条：

```powershell
python run.py --db var/demo.sqlite3 build --rules rules.jsonl --config delivery/config.json --out var/demo_artifacts
$env:OPENAI_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
$env:QUACKGUARD_MODEL_CACHE = "delivery/demo_cache"
python run.py --db var/demo.sqlite3 infer --input "O2_AI商品审核官_资料包/03_公开开发集/inputs/audit_inputs.jsonl" --artifacts var/demo_artifacts --out var/demo_outputs.jsonl
```

缓存键只由模型名与请求内容（含图片 base64）决定，与机器路径无关，可随交付包分发；命中时零网络往返，实测 64 条 0 执行错误、约 5.2 s。未命中则正常请求，429/5xx 自动退避重试。

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

- 五类命令均返回 0，并生成上列 HTML；- `delivery/manifest_check.json` 的 `ok=true`；
- 公开集输出 64 条，ID 无缺失/重复，`artifact_version` 与产物一致，trace 可回查；
- 仓库根无 `.env`、`__pycache__`、`.pytest_cache`、`*.tmp`。

干净环境实测（2026-11-27）：

- 新建 `var/clean_venv`，只执行 `pip install -r delivery/requirements.txt`；`build` 成功，产物 `av_a489476e2465379a03414735`、62 条规则版本、0 条失败。
- 不配置 `OPENAI_API_KEY`，仅设置 `OPENAI_BASE_URL` 与 `QUACKGUARD_MODEL_CACHE=var/model_cache`，64 条推理全部有结果、0 执行错误、约 5.2 s，证明缓存可离线复现。
- 全仓库密钥扫描（`sk-`、`OPENAI_API_KEY`、`api_key`）0 命中；`.env` 不存在且已在 `.gitignore` 中。

## 7. 冻结与已知限制

- 活动版本冻结为 baseline：`artifact_version=av_a489476e2465379a03414735`，`root_sha256=7f943888587c61090aa9611bf9905c91ae415aa81a13549aa4e50f1f634747af`；候选只记录澄清、不改变判定，验收后已回滚，见 `selection_freeze.json`。
- 冻结快照 `delivery/artifacts/` 为离线构建：未配置模型时，64 条公开任务中 62 条显式 `model_not_configured`，2 条 B001 因材料不适配可直接判定 `not_applicable`。需要真实审核时按第 5 节配置 `glm-4v-flash` 并用 `delivery/config.json` 重建。
- `delivery/annotations/` 用公开金标可见事实驱动同一 `annotate` 实现，验证值/状态/标签/材料/页码/IoU 联合链路；公开样例得分为 7/7，但属于链路校验，不是系统正式标注成绩。配置模型后重跑 `annotate` 即得到真实标注输出。
- `delivery/evaluation.json` 基于 `delivery/model_run_6_p3/outputs.jsonl`（真实模型运行）运行两个插件：输入哈希前后一致、故障 0；conclusion 准确率 0.90625、机器可复核率 0.875、Q=0.922837；evidence 覆盖 62/64 条有证据单元，结构化字段值核验 `grounded_fact_rate=0.9677`，区域定位 `localized_fact_rate=0.5111`（88 条文本命中但 IoU<0.5，与标注 0/7 的模型框偏移一致）。`plugin_report.html` 为同一份报告对应的页面。
- 真实模型结果见 `delivery/model_run/`：公开集 A=0.609375、U=0.673077、Q=0.628486，执行错误 1；标注的值/状态/标签 3/3 正确，但 `glm-4v-flash` 返回的框存在系统性坐标偏移，IoU 不达标，标注得分 0/7。该限制已如实记录，未用整帧或文档框冒充。
- 可选本地 OCR 定位：安装 `requirements-ocr.txt` 后，`annotate` 会用 RapidOCR 识别字段行并以其框替换模型框；公开 3 个标注目标的框 IoU 为 0.7182/0.6187/0.6043，均达到 0.5 线。OCR 完全离线、免费，未安装或 `QUACKGUARD_OCR=0` 时自动回退，不影响原有 0/7 的历史记录。离线复核：设置 `QUACKGUARD_MODEL_CACHE=delivery/demo_cache` 后重跑第 3 步 `annotate`，`score_annotations` 为 **7.0/7**。
- `annotate` 只接受字段自身的文字行框；模型未给出字段框时显式失败 `annotation_bbox_missing`，不退回文档区域或整帧。
- 第二轮独立回跑见 `delivery/model_run_2/`：38/64、A=0.59375、U=0.673077、Q=0.617548，标记 3/3 正确但框 IoU 仍不达标；与第一轮差 1 条正确，6 个单元结论变化，属模型服务端波动，不是代码回归。
- P0 修复后第三轮见 `delivery/model_run_3_p0/`：R026 公开集 4/4、B001 证明页缺载体字段不再报结构错误，43/64、A=0.671875、U=0.711538、Q=0.683774；剩余 1 条错误为 R050 重复 JSON 键，留待 P1 容错。
- P1 修复后第四轮见 `delivery/model_run_4_p1/`：48/64、A=0.75、U=0.826923、Q=0.773077、执行错误 0；R010/R014/B001/R035 改善，R041/R048 各回退 1 条；标注框 IoU 仍 0/3，待 P2 定位型模型。
- P2 模型选型见 `delivery/model_run_5_p2/`：`glm-4.1v-thinking-flash` 全量 44/64、Q=0.700481（JSON 修复回放后 45/64、Q=0.717187），平均延迟 19.90 s、P95 33.60 s，4 条非法 JSON；相较 Flash 48/64、Q=0.773077、10.06 s、0 错误，不切正式产物。
- P3 事实通道见 `delivery/model_run_6_p3/`：58/64、A=0.90625、U=0.961538、Q=0.922837、0 错误、10.90 s；R050 4/8→7/8，R005/R010/R041/R048 全对，R021 2/4，B001 19/20，R035 4/4。剩余错项为 R014 批次遮挡、R021 空间边界 2、R050 不可读行、B001 90mm 附签、R060 各 1。
- 稳定性：model_gateway 对 429/5xx/网络错误按 1→2→4→8 s 退避重试；设置 `QUACKGUARD_MODEL_CACHE` 可启用“模型+messages+max_tokens”哈希的本地缓存，默认关闭。
- `--locate-fallback` 定向定位默认关闭：3 条标注补出框，值/状态/标签全对，但探测 IoU 0.4852/0.197/约 0.05 未达 0.5，标注得分仍 0/7；仅保留为评测工具，不进入正式链路。
- JSON 修复为最后兜底：尾逗号、括号补齐之后，再取第一个完整 JSON 值并容忍重复键；仍不伪造标签，语义非法（如字段 `status="clear"`）继续如实报 `invalid_model_output` 并计入分母。
- R021/R026/R060 的参考输出与契约存在已知语义差异，只影响 A/U/Q，不属于非法输出。
- 公开参考基线 Q=1 仅证明评分算术，不计入系统成绩。

## 8. 迭代过程与取舍（答辩速览）

| 阶段 | 关键改动 | 正确/64 | Q | 平均延迟 | 执行错误 |
| --- | --- | --- | --- | --- | --- |
| 基线 `model_run` | 首轮真实运行 | 39 | 0.628486 | 约 10 s | 1 |
| 复跑 `model_run_2` | 独立回跑，量化服务端波动 | 38 | 0.617548 | 约 10.75 s | 1 |
| P0 `model_run_3_p0` | 结构错误、载体与必填字段 | 43 | 0.683774 | 约 10 s | 1 |
| P1 `model_run_4_p1` | 载体回退、分层缺失提示 | 48 | 0.773077 | 10.06 s | 0 |
| P2 `model_run_5_p2` | thinking 模型横评（未采用） | 44 | 0.700481 | 19.90 s | 4 条非法 JSON |
| P3 `model_run_6_p3` | 事实通道、定向复核、退避与缓存 | 58 | 0.922837 | 10.90 s | 0 |

- 数据驱动演进：每一轮都有完整输出、轨迹与指标留档，改动前后可逐单对比，不靠主观感觉调参。
- 迭代不是只写澄清：教师明确给出结论时生成单元级 `engine/decision_patches.json`（含 `packet_id`、规则原文引用），候选推理按单元覆盖标签/适用性，baseline 保持逐字节不变。公开 6 条 Bad Case 自测：反馈集 A 0/6→6/6，保护集 58/58 不降，0 执行错误；证据见 `delivery/iteration_public/`。
- 为什么放弃 thinking 模型：延迟接近翻倍（10.06 → 19.90 s，P95 33.60 s），全量 44/64 反低于 Flash 的 48/64，且出现 4 条非法 JSON、定位框随机漂移；结论是不迷信大模型，按工程性价比决策。
- 模型看事实、规则做判决：模型只输出“字段/文本/组件/矩阵”，不直接给违规标签，避免 LLM 幻觉标签；判定由冻结规则实现完成，结论可回查规则版本、证据与 trace。
- 工程鲁棒性：JSON 硬兜底（尾逗号、括号补齐、取首个完整值、容忍重复键）+ 429/5xx 指数退避（1→2→4→8 s）+ 哈希缓存，最终全量 0 执行错误。
- 诚实取舍：默认模型框标注为 0/7，唯一原因是框精度；`glm-4v-flash` 有系统性偏移，thinking 定向定位最高 IoU 0.4852。接入可选本地 RapidOCR 后公开 3 个目标 IoU 0.7182/0.6187/0.6043，均过 0.5 线。
- 剩余 6 条 Bad Case 与路线：R014 遮挡 1、R021 空间边界 2、R050 不可读行 1、B001 90mm 附签 1、R060 1；对应“重拍/多图引导、更高分辨率输入、专用 BBox 检测或 OCR 辅助、升级 glm-4.5v”四条路径。
