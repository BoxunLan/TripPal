# skill-loop — 从 `research/` 的痛点清单到可演化的 SKILL

本目录是 `research/` 的**下游闭环**：把调研产出的痛点与场景切分，落成一套
**可执行、可评测、可回归**的技能演化流水线。

`research/pain_points.md` §6 给了 12 个场景与成功判据，§7 给了 work_log 字段与
「从轨迹反推 SKILL」的 6 条判据——那是**纸面建议**。本目录是它的**实现**：
把建议变成有 schema 契约、有测试、有回归门的代码，跑一遍就能得到「这个 SKILL 该不该合入」的结论。

---

## 1. 闭环长什么样

```
A 需求 → 场景 → 任务包        B 执行 + 评测            C 优化 + 治理
────────────────────────      ────────────────        ────────────────
pain_points.md §6  ──┐
se-questions.tsv     ├──►  import-trippal ──►          propose    ──►
trends-summary.md  ──┘       scenarios.json            ├─ 归因 knowledge_gap
raw/*.txt            ──►     demand records   ──►      ├─ 合并同类失败模式
（权威来源页面）      ──►     tool_data pages  ──►      └─ 产出 change_proposals
                             task_pack.jsonl
                                       │
                                       ▼
                             B 跑两遍：无 SKILL 基线 vs 带 SKILL 候选
                                       │
                                       ▼
                             regress 回归门：holdout 不许退步 + 成本不超预算
                                       │
                                       ▼
                             conclusion = pass | rollback | no_change
```

三个模块各司其职，互不依赖对方内部实现，只通过 `contracts/` 的数据结构耦合：

| 模块 | 角色 | 读 | 写 |
|---|---|---|---|
| `modules/demand-task-factory` | **A 线** 需求→任务包 | `research/` 语料 | `scenarios.json`、`demand records`、`tool_data pages`、`task_pack.jsonl` |
| `modules/execution-evaluation` | **B 线** 执行+评测 | `task_pack.jsonl` + 夹具 | `trace.jsonl`、`verdict`、`lookup.json`、`evaluation_report` |
| `modules/skill-optimizer` | **C 线** 优化+治理 | B 的归因结果 | `change_proposals.jsonl`、候选 SKILL、回归结论 |

---

## 2. 快速开始

```bash
# 0) 环境（Python 3.11+）
python -m venv .venv
.venv/Scripts/pip install -e modules/demand-task-factory \
                        -e modules/execution-evaluation \
                        -e modules/skill-optimizer
.venv/Scripts/pip install pytest jsonschema pyyaml

# 1) A 线：把 research/ 导入成场景 + 需求记录 + 检索夹具 + 任务包
#    （--trippal 默认自动探测父目录即本仓根，通常不用写）
.venv/Scripts/python -m demand_task_factory import-trippal \
    --out       fixtures/trippal_v1 \
    --records   fixtures/demand_records_trippal_v1/records.jsonl \
    --tool-data fixtures/tool_data_trippal_v1/pages.json
# 产出：66 条需求记录 / 8 个场景（源文件 12 个，被 scenario_target 截到 8）

.venv/Scripts/python -m demand_task_factory build \
    --input     fixtures/demand_records_trippal_v1/records.jsonl \
    --config    config/default.yaml \
    --scenarios fixtures/trippal_v1/scenarios.json \
    --out       modules/demand-task-factory/out

# 2) 跑测试（当前 178 条）
PYTHONPATH= .venv/Scripts/python -m pytest \
    modules/demand-task-factory modules/execution-evaluation modules/skill-optimizer

# 3) 真模型闭环（需自备 Key，见下）
.venv/Scripts/python integration/pipeline/run_live.py
```

> Windows 上跑测试建议加 `PYTHONPATH=` 前缀清掉宿主注入的 `sitecustomize`。

### 真模型 Key

`run_live.py` 从 `.env` 读（先找 `skill-loop/.env`，再找其父目录）：

```
TRAVEL_LLM_BASE_URL=...
TRAVEL_LLM_API_KEY=...
TRAVEL_CLASSIFIER_MODEL=...     # 分类用小模型
TRAVEL_GENERATOR_MODEL=...      # 生成用强推理模型
```

模板见 `.env.example`（复制成 `.env` 后填值；`.env` 已被 gitignore）。

没有 Key 时以退出码 2 结束并提示，不会静默跳过。不跑真模型时，B 线可用假模型测通路。

---

## 3. 目录

```
skill-loop/
├── contracts/
│   ├── v1/                     7 份 JSON Schema（需求记录/场景/任务/轨迹/判定/变更/清单）
│   └── v2/                     本期未启用的字段缺口（见 integration/blockers/b1-*.md）
├── config/default.yaml         唯一配置：来华垂直（geo=GLOBAL / language=en）
├── fixtures/                   夹具：需求记录、检索页面、任务包、场景目录、运行包、种子 SKILL
├── modules/
│   ├── demand-task-factory/    A 线（含 trippal.py 导入器 + build.py 出卡）
│   ├── execution-evaluation/   B 线（runner 执行 + summarize 评测）
│   └── skill-optimizer/        C 线（propose 归因 + regress 回归门）
└── integration/
    ├── pipeline/               run_once.py（六步编排）、run_live.py（真模型）、compare_cost_basis.py
    ├── blockers/               受阻记录（每条含现象/判据/处置清单）
    └── deviations.jsonl        对规格的每一处偏差（dev-001..011），含理由与证据
```

---

## 4. 设计上几个刻意的选择

**① 契约先行，模块互不 import。** 三个模块只认 `contracts/` 里的 JSON Schema，
  换实现不用改别人。`contracts/v1/` 一旦提交就不再改——字段不够就开 `v2/` 并写 blocker。

**② 归因决定提案，不靠人挑。** C 线只对 `attribution=knowledge_gap` 的失败生成提案，
  这是「SKILL 缺知识」与「工具不可达 / 判据太弱」的分界线。归因错了，整个闭环就学错东西。

**③ 回归门是硬闸，且方向不对称。** 候选 SKILL 必须同时过两关：
  holdout 集**不许退步**（宁可没有提升，不能变差）+ 成本比不超预算（默认 1.5×）。
  实测抓到过「看起来合理、实测有害」的技能——回归门是这套东西里最有价值的一环。

**④ 成本口径可配置且不静默改行为。** `cost_budget_basis: total|output|delta`，
  默认 `total`。三种口径都写进报告，只用配置指定的那个判定，不靠调参凑过门。

**⑤ 主题有硬校验。** `config` 的 `require_china_context: true` 会在出卡前检查
  每个场景是否真的落在中国语境（代表查询过双命中 + 检索面含 china 页面），
  违例直接 `exit 2` 不产出任务包。这是防止「说着来华、混进无关目的地」的兜底。

**⑥ 只查代表查询、不逐条查整簇。** 场景的 `query_cluster` 里除了代表查询还有
  辅助关键词（用于配对检索页面），拿短关键词做语境校验会误伤合法场景。
  详见 `integration/deviations.jsonl` 的 dev-011。

---

## 5. 当前状态

| 项 | 值 |
|---|---|
| 测试 | **178 全绿**（A 66 / B 55 / C 57），一条命令跑完 |
| A 线产出 | 16 张任务卡 / 8 个场景（`scenario_target: 8`） |
| 真模型基线 pass@3 | **0.958**（train 0.952 / holdout 1.0） |
| C 线产出 | 1 条提案 `ch-001`「来源与生效日期检查」 |
| 回归门 | **`conclusion=pass`**（holdout 未退步，成本比 1.366 ≤ 1.5） |

上表为真模型实测值；`178 全绿` 与 `16 卡 / 8 场景` 已在本目录（合并进 TripPal 后的新布局）
复跑确认，`import-trippal` 无需 `--trippal` 即可自动定位本仓根。

**未决**：`contracts/v2/`（`artifact_card`、场景的 severity/dependency 字段）
本期未启用，仍以 v1 为准，见 `integration/blockers/b1-contracts-v2-trippal.md`。

---

## 6. 诚实性边界（读结论前请看）

- **语料偏倚继承自 `research/`**：Stack Exchange 以英语技术型用户为主，
  缺少 Reddit/TripAdvisor 类即时吐槽语料（见 `research/README.md` §3）。结论只支持
  「英语技术型入境游客的需求结构」，不是全体入境游客。
- **场景切分是人工策展的**：`fixtures/trippal_v1/scenario_routing.json` 里场景↔页面的映射
  由人写，关键词取自 `pain_points.md` §6 的场景名与 §4 的痛点标题。**没有编造任何查询词或数字**，
  但策展边界本身是主观的。
- **不跑真模型时，B 线用假模型**，只能验证通路，不能得出结论。
- **真库组合未实测**（本项目不依赖数据库）；成本数字来自单次运行，不是统计均值。
