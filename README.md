# TripPal

TripPal combines a travel-readiness intake website with a Codex Skill that turns a submitted profile into a prioritized China travel preparation plan.

## 当前进展与分工（2026-10-04 更新，协作者请看这一节）

### 第一要务：MVP 已落地 → 下一步是**丰富 skill 本体**

MVP 已经跑通：skill 从"8KB 单体文档"改造成 **路由器 + 声明式清单 + 12 域分片 + 质量闸** 的产品结构
（见下方 [Skill architecture](#skill-architecture-seed-v0-2026-10-04)），并且已经用小模型跑完 332 张拿到第一份本机反馈。

**下一步（团队当前重点）**：就着这个框架**丰富优化 skill 本体**，而不是再动架构 ——
补 12 个域分片的内容、接入外部权威源、把质量闸接进流程、补金标准验收样例。

### 三轨反馈与当前进度

三条轨**跑同一份任务集**（332 张，见下），各自留证据，之后再合看。

| 轨道 | 谁 | 模型与配置 | 进度 |
|---|---|---|---|
| **MiniCPM5-2B 轨** | BoxunLan（本机 LM Studio） | `minicpm5-2b`，64K 上下文 / 4 并发，**按域注入** | ✅ **332/332 跑完**：整卡通过 **76.8%**、断言 **90.4%**、holdout 68.2%、22 张 `budget_exhausted`。轨迹文档与轻量分析已入库 |
| **大模型轨 A** | MysteriousmanA | `deepseek-flash` | 有**结果记录**（数据集文档 R11–R13）：修正后 332 张 321/332（96.69%）、断言 98.34%。**尚无轨迹文档，原始 trace 未入库** |
| **大模型轨 B** | zying333 | ECNU `ecnu-max`（329 张） | 有**实测口径与规则依据**（`fixtures/candidate_skill_v1/PROVENANCE.md`：prompt 增量 +897 token、基线 2104 token/次；提交信息里 5 处 harness 缺陷的实测后果）。**尚无轨迹文档** |

- MiniCPM5-2B 轨证据：[轨迹文档](docs/minicpm-track-trajectory.md) ·
  [轻量分析](docs/minicpm-track-analysis.md) ·
  [原始汇总](docs/minicpm-track-results/summary.json)（原始 `verdict/trace` 因体积未入库）
- ⚠️ **三条轨的数字不可直接相减**：模型、上下文、输出预算、注入方式都不同
  （MiniCPM5-2B 轨 = 4096 输出预算 + 按域注入；大模型轨 96.69% 是它自己的口径）。
  合看前请先对齐口径，并各自补一条**同包同配置的 `B-no-skill` 基线臂**（MiniCPM5-2B 轨本次未跑）。
- 想让三端可合并分析，最好各自提供：**逐失败卡清单**（task_id + 未命中断言 + 归因）、
  **实测口径**（模型/上下文/预算/并发/注入范围）、以及**同配置的基线臂**数字。

### 大模型轨怎么接（改环境变量即可，不用改代码）

```powershell
cd skill-loop
$env:LLM_BASE_URL = "https://<你的 API 端点>/v1"
$env:LLM_API_KEY  = "<API Key>"
$env:LLM_MODEL    = "<模型名>"

# 全量 332 张（4 路并行；大模型可把 Jobs 调大）
& .\integration\pipeline\run_v2_parallel.ps1 -Pack modules/demand-task-factory/out-v2/task_pack.jsonl `
    -Out "$env:TEMP\v2-big" -ExperimentId v2-big -Jobs 4 -Runs 1 -MaxTokens 12288 -TimeoutSeconds 180

# 只回归上次未通过的任务（FailedFrom 指向上次的 condition 目录）
& .\integration\pipeline\run_v2_parallel.ps1 -Pack modules/demand-task-factory/out-v2/task_pack.jsonl `
    -FailedFrom "$env:TEMP\v2-previous\runs\<experiment>\<condition>" `
    -Out "$env:TEMP\v2-failed-only" -ExperimentId v2-failed-only `
    -Jobs 4 -Runs 1 -MaxTokens 12288 -TimeoutSeconds 180

# 或走完整闭环（A 造题 → B 跑 → C 提案/回归门 → 收口）
& .\.venv\Scripts\python.exe integration\pipeline\run_live.py --runs 1
```

> **预算建议**：对会把推理 token 计入 `max_tokens` 的模型，使用
> `-MaxTokens 12288 -TimeoutSeconds 180`。2026-10-02 的 4096-token 大模型基线中有 106/329 张卡
> 在生成正文前耗尽推理预算，其中 C 族为 80/80。本地非推理模型可按实际情况降低。
> 预算不够会被如实记为 `execution_defect`，不会静默通过。
> `-Jobs` 数量 = 端点并发槽位；超过槽位不会更快。
>
> 也可以把端点写进 `skill-loop/.env`（键名：`TRAVEL_LLM_BASE_URL` / `TRAVEL_LLM_API_KEY` /
> `TRAVEL_GENERATOR_MODEL`，或 `TRAVEL_CLASSIFIER_MODEL`）。完整闭环还可设
> `TRAVEL_LLM_MAX_TOKENS=12288` / `TRAVEL_LLM_TIMEOUT_SECONDS=180`。该文件已被 `.gitignore` 忽略、不会进库。
>
> 结果写在 `-Out` 目录下：`runs/<experiment>/<condition>/<task_id>/<run_n>/{verdict,trace}.json`
> 与 `artifacts/answer.json`（内含**逐条断言**结果），再加 `summary.json` / `report.md`。
> **调用失败**（端点不可达、超时、预算截断）会落 `artifacts/error.txt` 并记成 `execution_defect` ——
> 这类失败**不算** `knowledge_gap`，不会被 SKILL 学习线当成"知识缺口"。

### 现在就能用的任务集（v2）

- **位置**：`skill-loop/modules/demand-task-factory/out-v2/task_pack.jsonl`
- **规模**：**332 张**，六族覆盖 12 个真实出行场景
  （A 有据事实答 39 · B 规则应用 130 · C 产物清单 80 · D 拒答 39 · E 工具决策 20 · F 到达后动作 24），
  train 244 / holdout 88，`pack_sha256 = 718cc2a6…`（跨进程可复现）。
- **质量门**：生成期强制 **G1–G9** 全绿 —— 其中 **G8「逐字/条款有据」**（断言必须能在所引用的页面里找到）、
  **G9「题目可解」**（用标准答案反证判据可满足，曾抓出 84 张不可能过的坏卡）。
- **小模型全量基线**（历史数据，来自修正前的 `7b8753d1…` 任务包；新包需重跑）：

  | 族 | A | B | C | D | E | F | 合计 |
  |---|---|---|---|---|---|---|---|
  | 整卡通过 | 8% | 62% | 19% | 15% | 65% | 63% | **40.4%** |
  | 断言通过率 | 56% | 84% | 69% | 69% | 65% | 88% | **75.1%** |

- **必须看的文档**：
  [`docs/dataset-v2-card.md`](skill-loop/docs/dataset-v2-card.md)（数据集卡片：组成/门禁/复现/**已知局限**）、
  [`docs/dataset-v2.md`](skill-loop/docs/dataset-v2.md)（设计 + 12 轮调试记录）。

### 跑之前请先读"已知局限"（会直接影响你怎么读结果）

1. **单卡判定有噪声**：同一张卡重复 3 次，状态翻转率 **11%**、断言率极差均值 **0.105**（`temperature=0` 也一样）。
   → **比聚合指标、不比单卡**；跨条件比较要配对 + 看大 Δ。
2. **`pass@3` 会因方差虚高**（实测单次 25% → pass@3 41.7%）→ 优先看连续指标 `mean_assertion_pass`。
3. **并发会引入额外漂移**（LM Studio 多槽批处理）→ 要可复现数字用 `-Jobs 1`；
   但注意"第 1 次运行"与后续不同（KV/前缀缓存冷启动），别用小样本下结论。
4. **带 skill 跑时上下文要够**：4 路并行 + 16K 上下文意味着每槽只有约 4K，而 skill 文本约 8K token
   → **整批 HTTP 400**（记为 `execution_defect/http_400`，不是模型不行）。MiniCPM5-2B 轨的做法是
   把本地模型重载为 **64K 上下文 / 4 并发**；若机器吃不消，就 `-Jobs 1` 独占上下文。
   另：`.env` 必须按 UTF-8 读，否则 base URL 可能被静默丢弃、请求打到默认端点而整批 401
   （脚本已修，并在"有 key 无 base URL"时启动即报错）。
5. 数据集**能证伪改动**：闭环产出的候选 SKILL 在拒答族 +45 个点、却把 `already_holds_visa` 从 13/19 打到 **0/19**，
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

### Skill architecture (seed v0, 2026-10-04)

The skill is now a **router + static fragments** product, modeled on the `nature-*` skill layout:

- [`SKILL.md`](skills/trippal/SKILL.md) is a router only (5-step protocol, "never work from memory");
- [`manifest.yaml`](skills/trippal/manifest.yaml) declares what loads when (version, `always_load`, the
  `domain` axis with 12 readiness domains, on-demand references, quality tool);
- [`static/core/`](skills/trippal/static/core) is always loaded (principles, tool policy, workflow,
  output/quality); [`static/fragments/domain/`](skills/trippal/static/fragments/domain) holds one
  fragment per domain; [`static/corpus-map.json`](skills/trippal/static/corpus-map.json) maps domains
  to bundled authoritative pages with effective dates; [`static/routing.json`](skills/trippal/static/routing.json)
  is the machine-readable routing table (`scenario_id` → which fragments to inject) used by
  `run_v2_parallel.ps1 -SkillRoute` so a run only loads the matched domains, like a file-reading agent;
- [`references/source-policy.md`](skills/trippal/references/source-policy.md) is the credibility ladder
  (T1 statutory → T4 community), dating discipline, conflict rules and per-domain search recipe;
- [`scripts/verify_assessment.py`](skills/trippal/scripts/verify_assessment.py) is the declared quality
  gate: webpage payload contract + "no policy claim without a source and a date";
- [`tests/test_skill_architecture.py`](skills/trippal/tests/test_skill_architecture.py) guards the shape
  (router budget, manifest paths, no orphan fragments, routing table vs axis, no drift from the generator).

The 12 domain fragments are **generated drafts** (SEED banners) from `research/pain_points.md` + the
dataset v2 cards (`python skill-loop/integration/pipeline/build_skill_seed.py`). Full status, layout and
the feedback we need are in [`skills/trippal/README.md`](skills/trippal/README.md).

**下一步 = 丰富 skill 本体**（不是再动架构）：按 MiniCPM5-2B 轨的分析（
[`docs/minicpm-track-analysis.md`](docs/minicpm-track-analysis.md)）优先做三件事 ——
① 把 12 个域分片的"要核对的检查项"补成实测有效的内容（当前 10 个域还是兜底句）、
② 按分析里"`regex` 失败 79 次 vs `contains` 只 4 次"的结论，重点补**覆盖/格式类**要求（含 C 族清单篇幅）、
③ 把 `references/source-policy.md` 里 `declared` 的外部权威源逐步接成 `wired`，
并补 10–20 个金标准验收样例（产品的验收不该只靠 332 张卡）。

**What the three tracks should report** (exact commands are in the skill README): which domain fragments
are wrong or thin, which required items a domain misses, where the router misclassifies a profile,
which source-policy call was wrong, and where the quality gate is too loud or too quiet — per domain
and per pattern, not as a single score.
