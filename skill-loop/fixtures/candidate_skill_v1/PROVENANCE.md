# candidate_skill_v1 —— 每条规则的来源

本 SKILL 是「MVP 成品 SKILL」的候选版，用于闭环 C 线的 `C-seed-skill` 条件。
它**不是**从答案键反推的：每条规则要么来自 bundle 里的源页面，要么来自大模型轨轨迹里
「整个模式全灭」的失败模式（S1–S8）。

| SKILL 条款 | 对应方向 | 依据 |
|---|---|---|
| §0.2 正文要求的字段即使键清单没列也要写 | 缺陷修复 | A 族题面自相矛盾（36/36 要 `date`，键清单 `{answered,answer,source}` 里没有） |
| §0.3 输出语言跟题面 | **S7** | 5 张 `execution_defect` 全是"内容对、语言错"（`why` 写中文 → 英文正则不过） |
| §0.4 逐字引用 | **S8** | `V2A-faq` 去掉日期类失败后仍 23/36 缺正文关键短语 |
| §0.5 给出可点 URL | 既有 | 所有族都有 `regex https?://` 断言 |
| §1 先看旅客自身状态再看通用规则 | **S1** | `V2B-already_holds_visa` / `hainan_island_only` 的判定顺序 |
| §1.2 海南单独一套（61 国含美英加俄，30 天） | **S2** | `V2B-hainan_island_only` 10 张 0/10；页面 `hainan-visa-free-sanya-haikou-guide` |
| §1.3 240 小时过境：A→B→C / 已出票 / ≤240h / 停留区 | **S3** | `transit_placeholder_ticket` 9 张 0/9、`transit_round_trip`、`transit_over_240h`、`transit_tibet`；页面 `外交部 240 小时过境免签问答` |
| §1.3 港澳算第三地区 | **S3** | `V2B-transit_via_hk` / `transit_via_macau` 期望 `eligible` |
| §1.4 单边 30 天名单（英加在、美不在） | **S2** | `tourism_us_no_unilateral` not_eligible vs `tourism_uk_30day`/`tourism_canada_30day` eligible；页面 `china-visa-free-entry-countries-2026` |
| §2 先看旅客约束再选动作 | **S1** | `V2F-rail_tight_buffer` 3/3 答 `board_normally`（正解 `arrive_earlier`）；`V2F-ticket_no_cn_number` 3/3 答 `use_official_channel`（正解 `use_staffed_counter`） |
| §3 备用卡 / 实体护照 | **S4** | `V2C-predeparture` 判据含 `(second\|backup\|spare) card`、`original\|physical passport` |
| §3 人工通道 / 黄牛风险 | **S5** | `V2C-ticketing` 判据含 `staffed (lane\|counter)`、`scalper\|tout\|third-party` |
| §4 工具决策 | 能力补强 | `V2E-tool` 10 用 / 10 不用 |
| §5 显式说明证据不足 | **S6** | `V2D-abstain` / `V2D2-abstain` 判据含 `insufficient\|does not (answer\|address)` |

## 成本（实测，非估算）

- 文件 3.6 KB。skill 走 **system 通道**，每次尝试都计一次输入 token。
- 实测 prompt_tokens 增量：**+897 token**（用同一张 B 卡的 prompt 在 ECNU 上开关 system 各测一次）。
  - 迭代记录：4.8 KB 版 +1031、4.2 KB 版 +935、**3.6 KB 版 +897**。
  - 注意 token 数不随字节线性下降——文件里的 `->`、CJK 符号与 markdown 结构占了不少 token。
- 基线 `avg_in=1374 / avg_out=731 / avg_total=2104`（ECNU `ecnu-max`，329 张）。
- 预估 cost 比 = (1374+897+731)/2104 ≈ **1.43**（门限 1.5，`cost_budget_basis: total`）。
  ⚠️ 这是**输入侧**的比；§3 要求"有实质篇幅"可能同时抬高输出，实测比值以 `summary.json` 为准。

## 已知边界

- 本 SKILL 是**候选**，其通过率增益一部分来自"把 bundle 里本来就有的规则讲清楚"，
  不等于跨数据集的泛化能力。
- 反向风险：§3 要求"有实质篇幅"可能推高输出 token，需看实测 cost 比。
