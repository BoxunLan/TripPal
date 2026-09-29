# task_pack_trippal_v1 说明

4 张任务卡，2 个场景，覆盖两种 `task_type` 与两种 `split`。主题是**外国人来中国旅行**。

| task_id | split | task_type | 用 FakeModel 跑时的结果 |
|---|---|---|---|
| `fx-cn-visa-fact-001` | train | fact_lookup | fail —— 桩固定返回 `{"decision":"unknown","sources":[]}`，不含 `contains` 的目标串 |
| `fx-cn-visa-cond-002` | train | conditional_decision | pass —— `$.decision` 等于 `unknown` |
| `fx-cn-pay-fact-003` | holdout | fact_lookup | fail —— 同 `fx-cn-visa-fact-001` |
| `fx-cn-pay-cond-004` | holdout | conditional_decision | fail —— 期望 `confirmed`，桩给出 `unknown` |

按「分母 = 纳入统计的运行数」的口径，FakeModel 下 `train_pass_at_3 = 0.5`、`holdout_pass_at_3 = 0.0`。

**「故意不含答案」的那张卡是 `fx-cn-visa-cond-002`。** 它的 `prompt` 明确说「只检查字段结构，
不要求给出任何具体的政策结论」，`expected_end_state` 也只描述字段结构 —— 即题面里没有任何
可被抄走的政策答案，答案要从权威来源取（本夹具里没有，所以桩只能给 `unknown`）。

`fx-cn-pay-cond-004` 的 `prompt` 同理只定义 `decision` 的取值约定，不给结论。

两条 `fact_lookup` 的来源断言取自 `fixtures/tool_data_trippal_v1/pages.json` 的 `source_url`
（`govt.chinadaily.com.cn`），不是手写答案；代表查询也必须在那个夹具里查得到页面。
