# contracts/v2 — TripPal 垂直带来的字段缺口

规格「不许改」第 1 条：**不修改 `contracts/v1/` 里已经提交的 schema。字段不够就新建
`contracts/v2/`，并在 blocker 里说明，本期实现仍以 v1 为准。**

所以这个目录**只是把缺口落成可执行的定义，本期不启用**：

- `task.schema.json`（v2）在 v1 之上允许第三个 `task_type`：`artifact_card`，
  并给出 `artifact.dimensions`（信息 / 工具 / 语言 / 支付 / 身份核验）。
- `scenario.schema.json`（v2）在 v1 之上允许 `origin`、`vertical`、
  `trigger_timing`、`success_criteria`、`pain_point_ids`。

## 为什么需要它们

TripPal 的 12 个场景是按「外国人来华的决策时点」手工切的，每个都自带
**触发时点 / 用户目标 / 成功判据 / 卡点编号**（见 `TripPal-main/research/pain_points.md` §6）。
v1 的 `scenario` 只装得下 `pool / query_cluster / evidence_record_ids`，
这些策展字段没有正式位置。同样，好些场景（移动支付开通、景区抢票、语言支持）
的重心不是「查一个事实」也不是「判一个条件」，而是**产出一份可照着做的清单/步骤**，
v1 的 `task_type` 枚举（只有 `fact_lookup` / `conditional_decision`）装不下。

## 本期怎么落到 v1

- 任务卡仍**只产出 v1 的两种 `task_type`**，16 张卡全部通过 `contracts/v1/task.schema.json`。
- 策展字段以**非 schema 附加字段**的形式挂在 `scenario_catalog.jsonl` 的
  `origin` / `tripal` 下（v1 没有 `additionalProperties: false`，不违反 v1）。
- 等 v2 获批后再把 `tripal.*` 提升为正式字段、把需要产物的卡改判 `artifact_card`。

证据见 `integration/blockers/b1-contracts-v2-trippal.md` 与 `integration/deviations.jsonl` 的 dev-008。
