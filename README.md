# TripPal

TripPal combines a travel-readiness intake website with a Codex Skill that turns a submitted profile into a prioritized China travel preparation plan.

## 当前进展与分工（2026-10 更新，协作者请看这一节）

### 第一要务：先跑出 **MVP 成品 SKILL**

架构确认与后续迭代都排在 MVP 之后。现在的目标只有一个：**用真实模型的轨迹反馈，快速迭代出一个能用的 TripPal SKILL**。

### 双轨反馈（各自跑、之后再合看）

| 轨道 | 谁 | 怎么跑 | 拿到什么 |
|---|---|---|---|
| **小模型轨** | 本端（BoxunLan） | 本地 LM Studio + MiniCPM5 2.6B（16K 上下文），跑评测任务集 | 每张任务的 `verdict` + `trace` + 逐条断言结果 |
| **大模型轨** | 协作者（zying333） | 调 API 用大模型跑**同一批**任务集 | 同上，用于对照 |

两条轨**跑同一份任务集**（329 张，见下），跑完**分别分析轨迹**，再合起来确认 SKILL 的产品架构。

### 大模型轨怎么接（改环境变量即可，不用改代码）

```powershell
cd skill-loop
$env:LLM_BASE_URL = "https://<你的 API 端点>/v1"
$env:LLM_API_KEY  = "<API Key>"
$env:LLM_MODEL    = "<模型名>"

# 全量 329 张（4 路并行；大模型可把 Jobs 调大）
& .\integration\pipeline\run_v2_parallel.ps1 -Pack modules/demand-task-factory/out-v2/task_pack.jsonl `
    -Out "$env:TEMP\v2-big -ExperimentId v2-big" -Jobs 4 -Runs 1

# 或走完整闭环（A 造题 → B 跑 → C 提案/回归门 → 收口）
& .\.venv\Scripts\python.exe integration\pipeline\run_live.py --runs 1
```

> 也可以把端点写进 `skill-loop/.env`（`TRAVEL_LLM_BASE_URL` / `TRAVEL_LLM_API_KEY` / `TRAVEL_GENERATOR_MODEL` / `TRAVEL_EXECUTOR_MODEL`），该文件不入库。

### 现在就能用的任务集（v2）

- **位置**：`skill-loop/modules/demand-task-factory/out-v2/task_pack.jsonl`
- **规模**：**329 张**，六族覆盖 12 个真实出行场景
  （A 有据事实答 36 · B 规则应用 130 · C 产物清单 80 · D 拒答 39 · E 工具决策 20 · F 到达后动作 24），
  train 244 / holdout 85，`pack_sha256 = 7b8753d1…`（跨进程可复现）。
- **质量门**：生成期强制 **G1–G9** 全绿 —— 其中 **G8「逐字/条款有据」**（断言必须能在所引用的页面里找到）、
  **G9「题目可解」**（用标准答案反证判据可满足，曾抓出 84 张不可能过的坏卡）。**193 个测试**通过。
- **小模型全量基线**（供大模型轨对照）：

  | 族 | A | B | C | D | E | F | 合计 |
  |---|---|---|---|---|---|---|---|
  | 整卡通过 | 8% | 62% | 19% | 15% | 65% | 63% | **40.4%** |
  | 断言通过率 | 56% | 84% | 69% | 69% | 65% | 88% | **75.1%** |

- **必须看的文档**：
  [`docs/dataset-v2-card.md`](skill-loop/docs/dataset-v2-card.md)（数据集卡片：组成/门禁/复现/**已知局限**）、
  [`docs/dataset-v2.md`](skill-loop/docs/dataset-v2.md)（设计 + 11 轮调试记录）。

### 跑之前请先读"已知局限"（会直接影响你怎么读结果）

1. **单卡判定有噪声**：同一张卡重复 3 次，状态翻转率 **11%**、断言率极差均值 **0.105**（`temperature=0` 也一样）。
   → **比聚合指标、不比单卡**；跨条件比较要配对 + 看大 Δ。
2. **`pass@3` 会因方差虚高**（实测单次 25% → pass@3 41.7%）→ 优先看连续指标 `mean_assertion_pass`。
3. **并发会引入额外漂移**（LM Studio 多槽批处理）→ 要可复现数字用 `-Jobs 1`；
   但注意"第 1 次运行"与后续不同（KV/前缀缓存冷启动），别用小样本下结论。
4. 数据集**能证伪改动**：闭环产出的候选 SKILL 在拒答族 +45 个点、却把 `already_holds_visa` 从 13/19 打到 **0/19**，
   而当时全局 holdout 反而上升 —— 因此回归判定请用
   [`integration/pipeline/pattern_regress.py`](skill-loop/integration/pipeline/pattern_regress.py)（按模式回归门，退出码 1）。

## Website

The static website is in [`website`](website). It collects route, connectivity, payment, accommodation, and booking details in a four-step form, supports Chinese and English, and exposes WebMCP tools for the TripPal Skill to read a submitted profile and return its assessment.

The form saves drafts and submitted profiles in the current browser's local storage; it does not send them to a server. To run it locally, serve the static directory over localhost:

```powershell
python -m http.server 8000 --directory website
```

Then open <http://localhost:8000>. The `website` directory can also be used as the static hosting root.

## Skill

The Skill is in [`skills/trippal`](skills/trippal). See its [web profile contract](skills/trippal/references/web-profile-contract.md) for the data schema shared with the website.
