# Dataset Card —— TripPal 任务集 v2（`trippal-inbound-v2`）

> 交付物：`modules/demand-task-factory/out-v2/`（`task_pack.jsonl` + `provenance.json` + `dataset_report.json`）
> 夹具：`fixtures/tool_data_trippal_v2/pages.json`（任务卡引用的来源页正文）
> 设计/演进记录：`docs/dataset-v2.md`

## 1. 这是什么

面向**入境中国的外国游客**的评测任务集：329 张任务卡，覆盖 12 个真实出行场景（签证入境、景区票务、
城际交通、市内出行、住宿登记、支付、联网、餐饮、安全应急、超期与处罚等）。

用途有两个，且都必须成立：

1. **评测**：给 `B-no-skill`（不给 SKILL）与 `C-seed-skill`（给候选 SKILL）跑同一批卡，比较分数。
2. **暴露需求**：失败必须能归因到"缺什么知识/工具"，从而驱动 SKILL 的下一版内容。

## 2. 组成

| 族 | 张数 | 任务类型 | 考什么 | 判据的实质部分 |
|---|---|---|---|---|
| **A 有据事实答** | 36 | `fact_lookup` | 在官方长答复里定位并**逐字引用**事实 + 写出页面日期 | 页面正文的精确子串（关键短语）+ 日期 + 来源 URL |
| **B 规则应用** | 130 | `conditional_decision` | 读规则页后判 `eligible / not_eligible / use_visa` | 决策值 + 必须写出**管辖条款**（正则）+ 来源 |
| **C 产物卡** | 80 | `artifact_card` | 产出可用清单（10 个模板 × 8 个画像） | 每条**必需项**的正则（缺一项就不算过） |
| **D 拒答** | 39 | `fact_lookup` | 来源没回答时**如实说不知道** | `answered=false` + 说明证据不足 + `not_contains` 禁套用别的规则 |
| **E 工具决策** | 20 | `fact_lookup` | 判断"要不要去查来源、查什么" | `use_tool` 布尔 + 查询词正则 |
| **F 到达后动作** | 24 | `conditional_decision` | 读票务/铁路规则后判"下一步做什么" | `action` 值 + 必须点名管辖条款 + 来源 |

- 类型分布：`fact_lookup` 75 / `conditional_decision` 174 / `artifact_card` 80
- 切分：**train 244 / holdout 85**（分组切分：同一规则/同一页/同一模板不会跨切分）
- 证据页：32 页（29 页带生效日期），全部来自真实语料（`research/raw/clean/`）

## 3. 判据与质量门（生成期强制，不过门就不出包）

| 门 | 防什么 |
|---|---|
| G1 schema | 字段/枚举越界（v2 契约） |
| G2 唯一性 | 重复卡（id 或 prompt+verifier 级） |
| G3 非退化 | v1 的病：**1 个 prompt × N 张同样的卡**。现口径：族内 prompt 多样性 ≥0.4 且单 prompt ≤2 张 |
| G4 实质断言 | "复述问题 + 贴领域"这类空判据 |
| G5 证据可溯 | 引用了不存在的页 / 页太短 / 缺生效日期 |
| G6 泄漏 | 把答案抄进 prompt |
| G7 分组切分 | 同一规则/页/模板跨 train/holdout 造成泄漏 |
| **G8 逐字有据** | 实质断言在它所引用的页面里**找不到**（曾抓出 17 条：片段被压平而页面没压平） |
| **G9 题目可解** | 判据自相矛盾、任何模型都不可能过（曾抓出 84 张：断言含引号→JSON 转义后永远匹配不上；`not_contains` 禁了被引 URL 里的关键词） |

G8/G9 是这套数据集"值得信"的核心：**有据**由 G8 保证，**可解**由 G9 保证。测试里都固化了
（`modules/demand-task-factory/tests/test_dataset_v2.py`，共 192 个测试）。

## 4. 可复现

```powershell
cd skill-loop
$env:PYTHONPATH=''; $env:PYTHONIOENCODING='utf-8'

# 生成（幂等：同一代码两次构建 pack_sha256 相同）
& .\.venv\Scripts\python.exe -m demand_task_factory build-dataset-v2 --out modules/demand-task-factory/out-v2

# 跑评测（4 路并行）
& .\integration\pipeline\run_v2_parallel.ps1 -Pack modules/demand-task-factory/out-v2/task_pack.jsonl `
    -Out "$env:TEMP\v2-run" -Jobs 4 -Runs 1 -MaxTokens 4096 -TimeoutSeconds 180 -ExperimentId v2

# 走完整闭环（A→B→C→回归门→收口）
$env:TSD_A_OUT = (Resolve-Path modules\demand-task-factory\out-v2).Path
& .\.venv\Scripts\python.exe integration\pipeline\run_live.py --runs 1
```

- `provenance.json` 的 `pack_sha256` / `pages_sha256` 是**产物文件本身的 SHA-256**，可被下游直接核对。
- **可复现性是跨进程保证的**：生成器不依赖 `set`/`dict` 的哈希迭代顺序
  （`PYTHONHASHSEED` 变化不影响结果，测试 `test_generation_is_deterministic_across_processes` 守着这条）。
- `manifest.json` 记的是**真实生效**的 `max_tokens` / `timeout_seconds`（env 覆盖后的值），
  另存 `config_*` 为配置默认值；其中的 `task_pack_sha256` 是**本次消费的那个文件**的摘要
  （4 路并行时是切片），数据集包的摘要在 `provenance.json`。
- `TSD_A_OUT` 可把任意数据集接进同一套编排（v1 路径不受影响）。

**配套工具**（`integration/pipeline/`）

| 工具 | 用途 |
|---|---|
| `run_v2_parallel.ps1` | 多路并行跑任务包（`-Jobs` / `-Runs` / `-Skill` / `-Condition`） |
| `split_pack.mjs` | 轮转切分任务包（保证每片都含各族） |
| `pattern_regress.py` | **按模式**回归门：抓"全局不退步、某类能力崩掉"（退出码 1 可直接进 CI） |
| `check-determinism.mjs` | 同卡多次重复的判定一致性检查 |
| `noise-floor.mjs` | 量化"单卡判定噪声下界"（状态翻转率、断言率极差） |
| `paired-compare.mjs` / `inspect-b.mjs` | 配对比较 / 读作答原文（R8、R9 结论的复现脚本） |

## 5. 已知局限（用之前必须知道）

1. **只有一个小模型**（MiniCPM5 2.6B / Q4_K_M，16K 上下文）。已证明"题目可解 + 小模型做不到"，
   但**没有一个更强的模型来证明分数会随能力上升** —— 这是目前最大的证据缺口。
2. **场景分布偏斜是有意的**：TP-S01（签证入境）占 140/329 —— 入境资格是唯一有大量成文条款可判的场景。
   其余场景由 A（事实）、C（产物）、F（动作）覆盖（如 TP-S04 28 张、TP-S05 20 张）。
   强行拉平会造出"无条款可依"的决策题，反而伤害评测效度。
3. **A 族很难**：小模型逐字引用命中 22%、写出日期 31%。这是真实能力缺口（对旅行安全类 SKILL 有意义），
   但意味着 A 族的单次通过率接近地板，看 A 族要看 `mean_assertion_pass` 而不是 pass 率。
4. **单卡判定有噪声，且我踩过一次"小样本结论"的坑**：18 张 × 3 次（`-Jobs 1`、`LLM_SEED=0`、`temperature=0`）
   实测 **状态翻转 2/18 = 11%**，同一卡 3 次之间**断言通过率极差均值 0.105、最大 0.444**；
   更值得注意的是 **6/18 是"只有第 1 次不同、第 2/3 次相同"**（冷启动 / KV 前缀缓存特征）。
   我曾在 R3 用 3 张卡得出"单槽+seed 下 3/3 完全一致"——**那个结论是错的**，样本太小。
   → 用法约束：**比聚合指标、不比单卡**；跨条件比较用配对 + 大 Δ（R8/R9 的 -68%、+45% 远超这个噪声）；
   看连续指标 `mean_assertion_pass` 而不是 pass@3。
5. **`pass@3` 会因方差虚高**（实测单次 25% → pass@3 41.7%）。报告里的连续指标
   `mean_assertion_pass`（平均断言通过率）比它稳，应优先看。
6. **对比方的 SKILL 线**：C 线的提案内容目前是固定模板（5 条提案文字完全相同），
   吃不下数据集提供的多样化失败 —— 这是学习者侧的限制，不是数据集的。

## 6. 基线（小模型 MiniCPM5 2.6B，`B-no-skill`，全量 329 张，4 路并行）

| 指标 | 值 |
|---|---|
| `pass@3`（整卡通过率） | **0.404**（133/329） |
| **`mean_assertion_pass`** | **0.751**（938/1283 条断言） |
| train / holdout | 0.430 / **0.329**（holdout 更低 → 分组切分没有泄漏） |
| avg_tokens / 次 | 2428（input 1386 + output 1042） |
| 归因 | `knowledge_gap` 177 / `none`(通过) 133 / `execution_defect` 19 |

**按族**（这组数字就是"评测有意义"的证据：不是一律 0 也不是一律 1）

| 族 | 张数 | 整卡通过 | 断言通过率 | 主要归因 |
|---|---|---|---|---|
| A 有据事实答 | 36 | **3 (8%)** | 56.4% | knowledge_gap 32 |
| B 规则应用 | 130 | 81 (62%) | 84.4% | none 81 / knowledge_gap 49 |
| C 产物卡 | 80 | 15 (19%) | 68.7% | execution_defect 16 / knowledge_gap 49 |
| D 拒答 | 39 | 6 (15%) | 69.3% | knowledge_gap 33 |
| E 工具决策 | 20 | 13 (65%) | 65.0% | none 13 / knowledge_gap 7 |
| F 到达后动作 | 24 | 15 (63%) | **87.5%** | none 15 / knowledge_gap 7 |

- 难度梯度真实存在：A 8% ← C 19% ← D 15% ← B 62% ≈ F 63% ≈ E 65%。
- `execution_defect` 19 张（格式/语言/预算类失败）**没有**被算成 `knowledge_gap` ——
  C 线只从 `knowledge_gap` 学习，这个区分保证它不会学到垃圾。

**其他对照**

| 运行 | 张数 | pass@3 | mean_assertion_pass | 备注 |
|---|---|---|---|---|
| 24 张分层抽样 | 24 | 0.292 | 0.660 | 布尔与连续指标的差距最直观 |
| A 族专项（修复后） | 36 | 0.111 | — | 逐字引用 22%、日期 31%、URL 36/36 |
| 旧 16 张退化卡 | 16 | 0.875 | — | 说明旧集的"高分"没有信息量 |

## 6b. 这套评测能**证伪**改动（关键性质）

拿闭环产出的候选 SKILL（"没有来源就不给结论"）与基线做配对比较（同任务、同预算）：

| 模式 | 张数 | 基线通过 | 候选通过 | 基线断言率 | 候选断言率 |
|---|---|---|---|---|---|
| `V2A-faq`（引用官方答复） | 36 | 3/36 | **5/36** | 56% | **72%** |
| `V2B-already_holds_visa`（旅客已有签证） | 18 | **12/18** | **0/18** | 89% | 67% |
| **合计** | 56 | 15/56 | 5/56 | 65.5% | 71.1% |

- 评测**抓到了**一个"看起来更严谨"的改动其实是净回归，并**定位到具体模式**。
- 两个指标**会分歧**：断言通过率上升而整卡通过崩塌 —— 只看向量指标会把它读成进步，两个都要看。
- 机制可读：该模式的正解 `use_visa` 依赖**旅客自身状态**，而候选规则把模型推向**只看检索到的页面**。

## 7. 引用

- 设计依据与需求调研：`research/pain_points.md`、`research/findings-index.md`、`research/README.md`
- 契约：`contracts/v2/task.schema.json`（v1 冻结，v2 为扩展点）
- 演进记录（R1–R6，含每次被证伪的假设与修掉的缺陷）：`docs/dataset-v2.md` §7
