# D10 验收记录（评审 A / 评审 B）

## 方式

- 同一份冻结规则（B001 3.0/3.1 + R001–R060）、同一份配置与公开集。
- 评审 A、评审 B 各自使用独立的临时目录与 SQLite 数据库，互不读取对方的构建状态。
- 每轮依次执行 16 项产品任务与 3 类插件故障场景；记录写入 `reviewer_a.json`、`reviewer_b.json`、`reviewer_a_faults.json`、`reviewer_b_faults.json`。
- 该演练为程序化独立双轮，用于验证可重复性；人工验收可按同一张表逐项勾选。

## 16 项产品任务

| 编号 | 验收项 | A | B | 关键证据 |
| --- | --- | --- | --- | --- |
| P-TASK-01 | 全部规则导入与版本核对 | 通过 | 通过 | 62 个版本；R001–R060；B001 3.0/3.1；产物 `av_2275824ec1ba861a0c0018cb` |
| P-TASK-02 | 重复导入隔离且唯一规则不受影响 | 通过 | 通过 | 重复行 63 被记录；唯一版本仍 62 |
| P-TASK-03 | 异常导入定位到具体行 | 通过 | 通过 | 第 63 行 JSON 错误、第 64 行缺少 `rule_text`；合法 62 条保留 |
| P-TASK-04 | 单条规则真实构建可追溯 | 通过 | 通过 | `R005` 单条产物 `av_c029011c620688a20de8a14a`，DB 版本 1 条 |
| P-TASK-05 | 批量构建状态可核对 | 通过 | 通过 | 矩阵 62；`implemented=62`；DB 版本 62 |
| P-TASK-06 | 失败记录可过滤且持久化 | 通过 | 通过 | 失败 2 条；DB `build_failures=2` |
| P-TASK-07 | 证据定位到材料与源页 | 通过 | 通过 | 64 单完成；证据 89 条；越界 0 |
| P-TASK-08 | 规则原文与来源哈希可追溯 | 通过 | 通过 | 62 条原文/来源哈希一致 |
| P-TASK-09 | 新旧结果对比按单元计数 | 通过 | 通过 | fixed=0、new_error=1、unchanged=63、version_changed=0 |
| P-TASK-10 | 失败重试幂等且后继唯一 | 通过 | 通过 | 首次 ready、二次 existing；后继 1 条 |
| P-TASK-11 | 中断恢复跳过已完成规则 | 通过 | 通过 | resumed；跳过 62；失败任务与后继关联 |
| P-TASK-12 | 导出与评估指标一致 | 通过 | 通过 | 64 行；重复导出一致；指标一致；ID 唯一 |
| P-TASK-13 | 候选激活后推理绑定候选版本 | 通过 | 通过 | baseline `av_2275…`；candidate `av_a80d…`；活动=candidate；输出=candidate |
| P-TASK-14 | 回滚恢复基线版本与根哈希 | 通过 | 通过 | rolled_back；活动 `av_2275…`；根哈希与记录一致 |
| P-TASK-15 | 禁用插件不调用且健康插件不受影响 | 通过 | 通过 | evidence `disabled`/0 次；conclusion `ok`；输入哈希不变 |
| P-TASK-16 | 重启后构建/版本/候选/插件持久化 | 通过 | 通过 | 构建、62 版本、活动、候选、禁用插件均恢复 |

结果：A、B 均为 16/16 通过，通过向量完全一致。

## 插件故障场景

| 场景 | A | B | 证据 |
| --- | --- | --- | --- |
| 健康插件 | 通过 | 通过 | `quackguard.conclusion` 状态 `ok`；禁用故障插件后仍 `ok` |
| 崩溃插件 | 通过 | 通过 | 状态 `failed`，`error_code=plugin_failed` |
| 超时插件 | 通过 | 通过 | 状态 `failed`，`error_code=plugin_timeout` |
| 禁用插件 | 通过 | 通过 | 状态 `disabled`，调用次数 0 |
| 输入只读 | 通过 | 通过 | 故障前后输入哈希不变 |

## 冻结结论

- 选择 baseline 作为交付活动版本：`av_2275824ec1ba861a0c0018cb`。
- 候选 `av_a80de7291b9b55c33274c8e9` 仅记录澄清，不改变判定；门槛全零；验收后回滚 baseline。
- A、B 的 `artifact_version` 相同；根哈希因每次构建在 `build_report` 中记录不同 build ID 而不同，属于已记录的非确定性内容，不影响 `artifact_version` 与回滚校验。
- 详细证据见同目录 `reviewer_a.json`、`reviewer_b.json` 与 `*_faults.json`。
