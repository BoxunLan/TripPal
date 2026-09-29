# blocker: b1-contracts-v2-trippal

## 触发

用户要求「把 TripPal 的场景整进去」。TripPal 的需求证据与规格默认场景**不是同一个数据源**
（后来用户进一步要求：主题就是「外国人来华」，日本线整体移除，TripPal 成为唯一垂直）：

| | 后来的主线（TripPal） | 已移除的旧线 |
| --- | --- | --- |
| 人群 | 外国人来华（`geo=GLOBAL`，`language=en`） | ~~中国出境游客（`geo=CN`，`language=zh-CN`）~~ |
| 证据 | 566 条真实 Stack Exchange 提问（`views`）+ 7 条 Trends 词条（`geo=US`，全年均值） | ~~Google Trends 快照（手工导出 CSV）~~ |
| 场景边界 | 已策展：`pain_points.md` §6 的 12 个场景，带触发时点/用户目标/成功判据/卡点编号 | ~~用 bigram Jaccard ≥ 0.6 聚类切出来~~ |

规格「不许改」里写明**不要发明新数据源或新模块**，规则里也没有「导入一个策展场景目录」这条退路。
所以这里停下报 blocker，而不是把 TripPal 硬塞进 v1 的字段里。

## 规格上真正卡住的两点

1. **`task.task_type` 枚举在 v1 里只有 `fact_lookup` / `conditional_decision`。**
   而 S03（移动支付开通）、S04（景区抢票）、S12（语言支持）这类场景的重心是
   **产出一份能照着做的清单/步骤**，既不是「查一个事实」，也不是「判一个条件」。
   需要一个 `artifact_card`，并声明它落在哪些卡点维度（信息 / 工具 / 语言 / 支付 / 身份核验）。
2. **`scenario` 在 v1 里没有位置放策展字段。**
   `trigger_timing` / `success_criteria` / `pain_point_ids` / `origin` / `vertical`
   都是 TripPal §6 真实带的，v1 装不下。

## 已做的处置

- 按规格第 1 条，把缺口落成 **`contracts/v2/`**（`task.schema.json` 加 `artifact_card`
  与 `artifact.dimensions`；`scenario.schema.json` 加策展字段），
  **`contracts/v1/` 一个字节都没改**（见 `git diff` 与 `contracts/v2/README.md`）。
- **本期实现仍以 v1 为准**：A 线产出的 16 张卡全部只使用 v1 的两种 `task_type`，
  并用 `contracts/v1/task.schema.json` / `scenario.schema.json` 校验通过（0 失败）。
- 策展字段以**非 schema 附加字段**的形式挂在 `scenario_catalog.jsonl` 的 `origin` / `tripal` 下 ——
  v1 没有 `additionalProperties: false`，加这两个字段不违反 v1，也不改动 v1。
- 垂直已收口为单一主线：TripPal 用 `config/default.yaml`、`fixtures/demand_records_trippal_v1/`、
  `modules/demand-task-factory/out/`、`evals/runs/`；旧的日本线（`config/trippal.yaml`、
  `fixtures/demand_records_v1/`、`fixtures/tool_data_v1/`、`fixtures/task_pack_v1/`）已整体删除。
- 主题约束已落成 A 线的硬校验：`config/default.yaml` 的 `require_china_context: true`
  会在 `build` 时逐场景检查「代表查询在中国语境 + 检索面是中国页面」（`trippal.is_china_question`
  词表来自 `fixtures/trippal_v1/scenario_routing.json`），违例直接 exit 2 且不产出任务包。

## 需要谁来决定

- 是否批准 `contracts/v2/` 转正（把 `tripal.*` 提升为正式字段、需要产物的卡改判 `artifact_card`）。
- 若要把策展字段正式维护，需要确认 B/C 两条线是否要按卡点维度分别统计通过率。

在批准之前，本仓继续按 v1 出卡、按 v1 校验，闭环结论只针对**已通过 v1 的那 16 张卡**。
