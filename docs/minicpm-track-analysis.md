# MiniCPM5-2B 轨（`minicpm5-2b`）—— 结果分析（轻量汇总）

> 本文件由 `skill-loop/integration/pipeline/analyze_minicpm_track.mjs` 自动汇总生成，**不含原始轨迹**。
> 运行身份与冻结基线见 [`minicpm-track-trajectory.md`](minicpm-track-trajectory.md)；原始 `verdict/trace` 未入库。

## 1. 总览

| 指标 | 值 |
|---|---|
| 任务数 | 332 |
| 整卡通过 | **255 / 332 = 76.8%** |
| 断言通过 | **1096 / 1213 = 90.4%** |
| train / holdout | 79.9% / 68.2% |
| 完成状态 | 通过 255 · 内容失败 55 · 执行错误 22 |

> 断言通过率的分母是**实际产出断言的卡**：310 / 332 张。
> 执行错误（预算截断）没有断言可算，不进入分母，因此这类卡的失败不会拉低断言率——
> 看按模式表时请注意「通过 6/7 但断言 100%」这种组合通常意味着第 7 张是执行错误。

归因分布：

| failure_kind | 张数 |
|---|---|
| `none` | 255 |
| `knowledge_gap` | 55 |
| `budget_exhausted` | 22 |

## 2. 按族

| 族 | 张数 | 整卡通过 | 断言通过率 | 执行错误 | 最常见的未命中断言 |
|---|---|---|---|---|---|
| A | 39 | 34 (87.2%) | 95.5% | 0 | `contains` ×4 |
| B | 130 | 92 (70.8%) | 92.0% | 14 | `json_path_equals` ×24 |
| C | 80 | 63 (78.8%) | 85.8% | 6 | `regex` ×66 |
| D | 39 | 31 (79.5%) | 94.2% | 0 | `regex` ×8 |
| E | 20 | 18 (90.0%) | 94.7% | 1 | `json_path_equals` ×1 |
| F | 24 | 17 (70.8%) | 91.3% | 1 | `json_path_equals` ×6 |

## 3. 按模式（`task_id` 去掉序号）

| 模式 | 张数 | 整卡通过 | 断言通过率 | 主要失败断言 |
|---|---|---|---|---|
| `V2A-faq` | 39 | 34/39 | 95.5% | `contains` ×4 |
| `V2B-already_holds_visa` | 20 | 12/20 | 90.2% | `json_path_equals` ×5 |
| `V2B-hainan_island_only` | 10 | 5/10 | 83.3% | `json_path_equals` ×5 |
| `V2B-tourism_canada_30day` | 7 | 7/7 | 100.0% | — |
| `V2B-tourism_uk_30day` | 9 | 9/9 | 100.0% | — |
| `V2B-tourism_us_no_unilateral` | 10 | 9/10 | 96.7% | `json_path_equals` ×1 |
| `V2B-transit_harbin_province` | 7 | 6/7 | 100.0% | — |
| `V2B-transit_over_240h` | 9 | 3/9 | 74.1% | `json_path_equals` ×6 |
| `V2B-transit_over_240h_extreme` | 6 | 2/6 | 80.0% | `json_path_equals` ×3 |
| `V2B-transit_placeholder_ticket` | 9 | 7/9 | 91.7% | `json_path_equals` ×1 |
| `V2B-transit_round_trip` | 10 | 9/10 | 100.0% | — |
| `V2B-transit_tibet` | 8 | 8/8 | 100.0% | — |
| `V2B-transit_valid_abc` | 10 | 6/10 | 87.5% | `json_path_equals` ×2 |
| `V2B-transit_via_hk` | 9 | 7/9 | 100.0% | — |
| `V2B-transit_via_macau` | 6 | 2/6 | 77.8% | `json_path_equals` ×1 |
| `V2C-citytransport` | 8 | 5/8 | 71.4% | `regex` ×12 |
| `V2C-connectivity` | 8 | 6/8 | 75.0% | `regex` ×12 |
| `V2C-emergency` | 8 | 6/8 | 85.7% | `regex` ×6 |
| `V2C-food` | 8 | 5/8 | 71.4% | `regex` ×10 |
| `V2C-navigation` | 8 | 6/8 | 85.7% | `regex` ×6 |
| `V2C-payment` | 8 | 5/8 | 71.4% | `regex` ×14 |
| `V2C-predeparture` | 8 | 7/8 | 100.0% | — |
| `V2C-stay` | 8 | 7/8 | 87.5% | `regex` ×6 |
| `V2C-ticketing` | 8 | 8/8 | 100.0% | — |
| `V2C-train` | 8 | 8/8 | 100.0% | — |
| `V2D-abstain` | 20 | 15/20 | 93.8% | `regex` ×5 |
| `V2D2-abstain` | 19 | 16/19 | 94.7% | `regex` ×3 |
| `V2E-tool` | 20 | 18/20 | 94.7% | `json_path_equals` ×1 |
| `V2F-rail_all_good` | 3 | 3/3 | 100.0% | — |
| `V2F-rail_gate_rejects` | 3 | 2/3 | 100.0% | — |
| `V2F-rail_name_mismatch` | 3 | 3/3 | 100.0% | — |
| `V2F-rail_tight_buffer` | 3 | 0/3 | 66.7% | `json_path_equals` ×3 |
| `V2F-ticket_no_cn_number` | 3 | 3/3 | 100.0% | — |
| `V2F-ticket_official_ready` | 3 | 0/3 | 66.7% | `json_path_equals` ×3 |
| `V2F-ticket_third_party` | 3 | 3/3 | 100.0% | — |
| `V2F-ticket_too_early` | 3 | 3/3 | 100.0% | — |

## 4. 失败断言画像

| 算子 | 失败次数 | 占全部断言 |
|---|---|---|
| `regex` | 79 | 6.5% |
| `json_path_equals` | 34 | 2.8% |
| `contains` | 4 | 0.3% |

**最常见的未命中 `contains`（逐字引用类）**

| 期望片段 | 未命中张数 |
|---|---|
| `or renewal of visas or residence permits for foreigners in` | 1 |
| `online application website for this certificate is: https://gaj.` | 1 |
| `the e-tickets of 9 parks (Summer Palace, Temple of Heaven` | 1 |
| `below), 1% will` | 1 |

**最常见的未命中 `regex`（条款/格式类）**

| 期望正则 | 未命中张数 |
|---|---|
| `(?s).{400,}` | 11 |
| `(?i)insufficient|does not (answer|address|cover|mention|explain)|not (…` | 8 |
| `(?i)240|10 days|midnight|00:00|clock` | 2 |
| `(?i)didi|ride-?hailing|taxi app` | 2 |
| `(?i)chinese (address|name)|address in chinese` | 2 |
| `(?i)metro|subway|QR|transit card` | 2 |
| `(?i)unlicensed|black taxi|meter|refuse` | 2 |
| `(?i)pick-?up|meeting point|landmark` | 2 |
| `(?i)e-?sim|roaming|local sim` | 2 |
| `(?i)before (you )?(arrive|depart|fly)|install[^.\n]{0,30}before|pre-?i…` | 2 |

## 4b. 要点（由上面的数字自动挑出，供三端合看时快速定位）

1. **失败断言以 `regex`（覆盖/格式类）为主**：`regex` 79 次，而 `contains`（逐字引用类）只有 4 次 —— 在这一配置下，小模型的瓶颈已不是"引用来源"，而是"把要求的条目/格式写全"。
2. **11 张只差篇幅**：未命中的正则是 `(?s).{400,}`（C 族清单长度下限），属于输出啰嗦度/预算问题，不是知识缺口。
3. **决策类错误 24 张、另有 14 张没解析出决策**：明细见 §5 的"← 错"行（`not_eligible` 与 `eligible` 互错、以及 `use_visa` 被答成 `not_eligible`）。
4. **22 张 `budget_exhausted` 会低估上面的比例**：它们没产出正文，因此既不算通过、也没有断言进入分母。

## 5. 决策类混淆（规则应用族）

| 期望决策 | 模型实际给出 | 张数 |
|---|---|---|
| `not_eligible` | `not_eligible` | 44 |
| `eligible` | `eligible` | 36 |
| `use_visa` | `use_visa` | 12 |
| `not_eligible` | `eligible` ← 错 | 10 |
| `eligible` | `not_eligible` ← 错 | 8 |
| `eligible` | `(未解析出)` ← 错 | 7 |
| `use_visa` | `not_eligible` ← 错 | 5 |
| `not_eligible` | `(未解析出)` ← 错 | 4 |
| `use_visa` | `(未解析出)` ← 错 | 3 |
| `not_eligible` | `use_visa` ← 错 | 1 |

## 5b. 动作类混淆（到达后动作族）

| 期望动作 | 模型实际给出 | 张数 |
|---|---|---|
| `use_official_channel` | `use_official_channel` | 6 |
| `board_normally` | `board_normally` | 3 |
| `fix_name_before_travel` | `fix_name_before_travel` | 3 |
| `arrive_earlier` | `board_normally` ← 错 | 3 |
| `book_now` | `use_official_channel` ← 错 | 3 |
| `wait_for_release_window` | `wait_for_release_window` | 3 |
| `use_staffed_lane` | `use_staffed_lane` | 2 |
| `use_staffed_lane` | `(未解析出)` ← 错 | 1 |

## 6. 执行错误清单（与模型内容判断无关）

共 **22** 张 `budget_exhausted`（推理 token 吃满、未产出正文）：

```
V2B-already_holds_visa-10, V2B-already_holds_visa-16, V2B-already_holds_visa-19, V2B-transit_harbin_province-01, V2B-transit_over_240h_extreme-04, V2B-transit_placeholder_ticket-06, V2B-transit_round_trip-02, V2B-transit_valid_abc-08, V2B-transit_valid_abc-09, V2B-transit_via_hk-04, V2B-transit_via_hk-05, V2B-transit_via_macau-02, V2B-transit_via_macau-03, V2B-transit_via_macau-06, V2C-citytransport-08, V2C-emergency-07, V2C-food-01, V2C-navigation-07, V2C-payment-04, V2C-predeparture-08, V2E-tool-08, V2F-rail_gate_rejects-01
```

## 7. 工具决策

| 期望 use_tool | 模型实际 | 张数 |
|---|---|---|
| `false` | `false` | 10 |
| `true` | `true` | 8 |
| `true` | `(未解析)` ← 错 | 1 |
| `true` | `false` ← 错 | 1 |

## 8. 读这份分析时的限制

- **没有对照臂**：本端只跑了 `C-seed-skill`（按域注入），没跑同包同配置的 `B-no-skill`。
  因此上面的数字**不能**拆成「skill 的贡献」与「题目变简单的贡献」。
- **只在这一配置下成立**：MiniCPM5 2.6B / 64K 上下文 / 4 并发 / 按域注入 / 4096 输出预算。
- **原始轨迹未入库**（体积原因）。需要逐卡证据时按 `minicpm-track-trajectory.md` 的复现命令重跑，
  或用该文件里记录的产物路径读取当次运行的 `verdict.json` / `trace.jsonl` / `artifacts/answer.json`。

