# 旅游 Skill 闭环实现规格

把本文交给实现 Agent。一次只做一条线。做完该线的夹具测试再停，不要顺手做另外两条线，也不要提前做集成。

缩小版闭环的默认规模：1 份 Google Trends 快照、5–8 个场景、每场景 2 类任务、每题 3 次，比较「无 skill」和「有 seed skill」。规模可以按文末的弹性规则缩小，不能自行扩大。

```text
共同：契约 v1 + 夹具
        |
        +--> A 需求与任务     只读 demand_records_v1
        +--> B 执行与评测     只读 task_pack_v1 + seed_skill_v1
        +--> C 优化与治理     只读 run_bundle_v1
        |
        v
集成：三条夹具测试都绿之后才做
```

## 实现 Agent 怎么用本文

1. 先读「不许改」和「弹性规则」。遇到障碍时按弹性规则选退路，并把偏差写入 `integration/deviations.jsonl`。规则里没有的退路，停下来写 `integration/blockers/<id>.md`，不要发明新数据源或新模块。
2. 共同部分没完成时，A/B/C 可以先写测试骨架，但不能把夹具字段改成自己好实现的样子。
3. 每条线结束的唯一标准是该线的 `pytest` 命令退出码为 0。不要用口头说明代替测试。
4. 测试失败时先改实现。禁止删断言、禁止把真实调用记成通过、禁止放宽 schema 来让当前数据合格。

偏差记录每行一个 JSON：

```json
{"id":"dev-001","module":"A","rule":"scenario-floor","chosen":"keep-3-scenarios","evidence":"snapshot has 3 topics"}
```

## 不许改

- 不修改 `contracts/v1/` 里已经提交的 schema。字段不够就新建 `contracts/v2/`，并在 blocker 里说明，本期实现仍以 v1 为准。
- 不采集、不读取、不上传个人 Chrome 历史、Cookie、账号活动、屏幕或键鼠记录。
- 不把 Google Trends 的相对热度写成搜索次数。不把多个来源的数字直接相加。
- 不在任务卡的 `prompt` 里写入 skill 名称、规则原文或标准答案。
- C 在生成候选 skill 之前不能读取 holdout 轨迹。
- 三条线不 import 彼此的 Python 包，不共享数据库。只通过文件交接。
- 没有 LLM Key 时，假模型运行的 `verdict.live` 必须是 `false`，不能计入真实通过率。

## 弹性规则

按顺序使用。命中一条就停，不要同时用更松的下一条。

| 编号 | 触发 | 允许的改法 | 下限 / 上限 |
| --- | --- | --- | --- |
| E1 | 没有 Trends API，也导不出网页快照 | A2 标为跳过；A1 仍须用夹具通过。不安装非官方抓取库 | 真实场景数可以为 0，但要有 blocker |
| E2 | 快照主题少于 5 个 | 有几个用几个，不编造查询词 | 最少 3；不足 3 则只交付 A1。先做完 E3 再数场景 |
| E3 | 某个场景写不出两种任务 | 丢掉该场景，不把一种任务复制成两种 | 每个留下的场景仍是 2 张卡。丢完再回到 E2 数人数 |
| E4 | 发现 train/holdout 近重复 | 两张卡都从任务包删除，不把其中一张改道 | 删除后在偏差里记下 `task_id` |
| E5 | 没有 `LLM_API_KEY` | B、C 使用仓库内的 `FakeModel`，只跑夹具 | 假运行不得写入 `pass_rate` |
| E6 | 单次运行超时或接口 5xx | 原样再试 1 次。超时记 `status=timeout`、`attribution=missing_tool_or_data`；接口 5xx 记 `status=error`、`attribution=execution_defect`。不记 `tool_error` | 每题最多 2 次尝试，然后落盘 |
| E7 | 无 skill 通过率是 0 或 1 | 不改提示词去凑数；写 blocker 检查 verifier | 这里的 `examples` 就是 B 线 `summary.json` 的 `examples`，不是另一份清单。命中时正好 3 条（不足 3 条则全写并记偏差）。未命中时允许 `[]`，也可以写 1 到 3 条 |
| E8 | 知识缺失失败少于 1 条 | C 只交付夹具验收，不生成空的真实候选 | 提案数保持 0，不为了凑数改归因 |
| E9 | 提案想超过 5 条 | 按跨任务重复次数保留前 5 条，其余写入报告不进 skill | 上限 5 |
| E10 | holdout 下降或成本超预算 | 只把 `candidate_skill/SKILL.md` 恢复成 `fixtures/seed_skill_v1/SKILL.md` 的副本 | 不改分数，不删除 `evals/runs/live-candidate/` 里已经落盘的运行 |
| E11 | 集成时上游文件不合法 | 接收方打印结构化错误并退出码 2，不在接收方里修数据 | 不改 v1 |

可调参数只在 `config/default.yaml`，并允许被环境变量覆盖。实现时使用这些默认值：

```yaml
geo: CN
language: zh-CN
time_window: 2026-08-01/2026-08-31
scenario_target: 8
scenario_floor: 3
tasks_per_scenario: 2
runs_per_task: 3
max_proposals: 5
temperature: 0
timeout_seconds: 60
max_tokens: 2000
cost_budget_ratio: 1.5
```

`cost_budget_ratio` 是「有 skill 的平均 token / 无 skill 的平均 token」。大于 1.5 视为超预算。

## 环境与技术栈

开发机安装 Python 3.11 和 Git。每个模块自己的 venv，依赖写进该模块的 `requirements.txt`。共用校验库可以复制这三行到每个模块，不要做跨模块安装：

```text
jsonschema>=4.22
pytest>=8.0
pyyaml>=6.0
```

B 和 C 额外使用模型官方 SDK 或 HTTPS 调用，由 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 决定。密钥只放环境变量。

仓库：

```text
tour-skill-system/
  contracts/v1/
  config/default.yaml
  fixtures/
    demand_records_v1/
    task_pack_v1/
    run_bundle_v1/
    seed_skill_v1/SKILL.md
  modules/
    demand-task-factory/
    execution-evaluation/
    skill-optimizer/
  integration/
    deviations.jsonl
    blockers/
    pipeline/
  data/snapshots/trends/
  evals/runs/
```

权限：

| 线 | 需要 | 明确不要 |
| --- | --- | --- |
| 共同 | 本机写仓库 | 生产环境账号 |
| A | 浏览器里打开 Google Trends 并手工导出 | Trends 非官方库、Chrome 历史、Search Console、Google Ads |
| B | `LLM_API_KEY`；无钥匙时走 FakeModel | A 的源码、C 的优化器源码 |
| C | 同一把 `LLM_API_KEY`；无钥匙时走 FakeModel | holdout 目录、A 的数据连接器、B 的运行器 |

## 共同契约

七个文件都是 JSON Schema draft 2020-12。每个业务 JSON 都含 `"schema_version": "v1"`。

校验命令在每个模块里都要有：

```bash
python -m <module> validate --schema <schema.json> --data <file>
```

退出码：`0` 合法，`2` 不合法，stderr 打印 JSON 数组 `[{"path":"...","message":"..."}]`。

### demand-record.schema.json

必填：`schema_version`、`record_id`、`source`、`query_or_topic`、`geo`、`language`、`time_window`、`metric_type`、`metric_value`、`access_scope`、`retrieved_at`。

`source` 枚举：`google_trends`、`google_ads_keyword_planning`、`search_console`、`other`。本期夹具和快照只用 `google_trends`。`metric_type` 枚举：`relative_interest`、`rising_rate`、`avg_monthly_searches`、`impressions`。Trends 快照只用 `relative_interest` 或 `rising_rate`。`access_scope` 枚举：`public_aggregate`、`authorized_first_party`。`metric_value` 是数字。`time_window` 格式 `YYYY-MM-DD/YYYY-MM-DD`。

### scenario.schema.json

必填：`schema_version`、`scenario_id`、`title`、`pool`、`query_cluster`、`evidence_record_ids`、`geo`、`language`、`time_window`。

`pool` 本期只允许 `high_frequency` 或 `rising`。`query_cluster` 至少 1 个字符串。`evidence_record_ids` 至少 1 个，并且必须能在输入需求记录里找到。

### task.schema.json

必填：`schema_version`、`task_id`、`scenario_id`、`split`、`task_type`、`demand_evidence`、`prompt`、`initial_state`、`allowed_tools`、`expected_end_state`、`verifier`、`prohibited_leakage`。

`split` 为 `train` 或 `holdout`。`task_type` 本期只允许 `fact_lookup` 或 `conditional_decision`。`demand_evidence` 含 `source`、`query_cluster`、`geo`、`time_window`。`verifier` 含 `kind` 和 `assertions`。`kind` 本期只允许 `deterministic`。`assertions` 是对象数组，每项含 `op` 和 `value`。`op` 只允许 `contains`、`regex`、`json_path_equals`。`prohibited_leakage` 是字符串数组，至少包含 `skill` 和 `answer_derivation`。

### run-manifest.schema.json

必填：`schema_version`、`experiment_id`、`condition`、`model`、`temperature`、`timeout_seconds`、`max_tokens`、`tool_snapshot`、`skill_snapshot`、`task_pack_sha256`。

`condition` 只允许 `B-no-skill` 或 `C-seed-skill`。`B-no-skill` 的 `skill_snapshot` 必须是 `{"loaded": false}`。`C-seed-skill` 必须是 `{"loaded": true, "path": "fixtures/seed_skill_v1/SKILL.md", "sha256": "<64 hex>"}`。除 `condition` 和 `skill_snapshot` 外，配对的两份 manifest 其他字段必须相同。

### trace-event.schema.json

`log.md` 的机器可读副本是 `trace.jsonl`，每行一个事件。必填：`schema_version`、`step`、`timestamp`、`action`、`observation`、`decision`、`skipped_or_abandoned`、`cost`。`cost` 含 `input_tokens`、`output_tokens`、`tool_calls`。没有跳过时 `skipped_or_abandoned` 为空字符串，不能省略字段。

### verdict.schema.json

必填：`schema_version`、`task_id`、`run_n`、`live`、`status`、`overall_pass`、`attribution`、`contaminated`、`scores`。

`status` 枚举：`pass`、`fail`、`timeout`、`error`。`attribution` 枚举：`none`、`knowledge_gap`、`missing_tool_or_data`、`execution_defect`、`task_defect`。通过时 `attribution` 为 `none`。`contaminated` 为 true 时，该次运行不进入通过率。`scores` 含 `outcome`、`process`、`evidence`、`efficiency`，每个是 0 到 1 的数。`live` 表示是否调用了真实模型。

### skill-change.schema.json

必填：`schema_version`、`change_id`、`operation`、`target_section`、`evidence_tasks`、`failure_pattern`、`proposed_change`、`expected_effect`、`regression_risk`、`acceptance_test`。

`operation` 枚举：`add`、`revise`、`merge`、`delete`。`evidence_tasks` 至少 1 个，且只能来自 `split=train`。`acceptance_test` 是一句可以变成断言的话，不能是某张任务卡的答案原文。

### 夹具内容

`fixtures/demand_records_v1/records.jsonl`：8 到 12 行，全部 `source=google_trends`，`access_scope=public_aggregate`。至少 3 个不同的 `query_or_topic`。

`fixtures/task_pack_v1/task_pack.jsonl`：至少 4 行。两种 `task_type` 都要有。train 和 holdout 都要有。其中一张卡的 `prompt` 故意不含答案。

`fixtures/run_bundle_v1/` 至少 4 次运行，目录名即类别：

```text
success/            attribution=none, overall_pass=true
knowledge_gap/      attribution=knowledge_gap, overall_pass=false
missing_tool/       attribution=missing_tool_or_data, overall_pass=false
task_defect/        attribution=task_defect, overall_pass=false
```

每次运行都有 `task.json`、`manifest.json`、`log.md`、`trace.jsonl`、`verdict.json`、`artifacts/`，与 B 线单次目录一致。`manifest.json` 通过 run-manifest schema。C 的 `propose` 不读 `manifest.json`，也不看里面的 `experiment_id`。另有一份 `holdout/`，C 的默认输入路径不要包含它。

`fixtures/seed_skill_v1/SKILL.md` 不超过 40 行，只写一条可迁移检查：回答政策或材料问题时必须带来源和生效日期，没有来源就写待核实。

共同部分的完成命令：

```bash
pytest modules/demand-task-factory/tests/test_contracts.py
pytest modules/execution-evaluation/tests/test_contracts.py
pytest modules/skill-optimizer/tests/test_contracts.py
```

每份测试至少包含：合法样例通过、缺一个必填字段失败、枚举写错失败。

## A 线

目录 `modules/demand-task-factory/`。包名 `demand_task_factory`。

命令：

```bash
python -m demand_task_factory build \
  --input fixtures/demand_records_v1/records.jsonl \
  --config config/default.yaml \
  --out modules/demand-task-factory/out
```

输出：

```text
out/scenario_catalog.jsonl
out/task_pack.jsonl
out/provenance.json
out/leakage_report.json
```

`provenance.json` 必填：`schema_version`、`input_sha256`、`config_sha256`、`record_count`、`scenario_count`。

实现顺序：

1. 写 `tests/test_build_fixture.py`，先失败再实现。断言：输出通过 scenario 和 task schema；每个 `evidence_record_ids` 都存在于输入；`prompt` 不包含 `SKILL.md`、`seed skill`、`标准答案`；同一 `query_cluster` 集合不会同时出现在 train 和 holdout；缺 `metric_value` 的输入行被跳过并出现在 stderr 的错误数组里，进程仍可用剩余合法行完成，除非合法行少于 `scenario_floor`。
2. 聚类规则写死，不调用 LLM。规范化：去首尾空白、英文小写、全角转半角。两条查询在去掉「怎么办 / 需要什么 / 材料」这些功能词后，字符 bigram Jaccard 相似度大于或等于 0.6，则归入同一簇。相似度函数和阈值放在代码里，并在测试里用两对例子锁定。
3. 场景池：`metric_type=rising_rate` 进 `rising`，其余进 `high_frequency`。超过 `scenario_target` 时，`rising` 和 `high_frequency` 交替保留，直到达到目标。
4. 每个场景两张卡。`fact_lookup` 的 verifier 至少有一条 `contains`，值来自该场景证据记录的 `query_or_topic`，不是手写答案。`conditional_decision` 的 `initial_state` 带 `applicant_region` 和 `trip_days`，verifier 用 `json_path_equals` 检查输出 JSON 里有 `decision` 字段。这两张卡的期望终态只描述字段结构，不写具体政策结论。
5. 场景数按这个顺序判定，不能颠倒：

   1. 先执行 E3：写不出两张卡的场景直接丢掉。
   2. 再数剩下的场景。少于 `scenario_floor`（3）时，不写真实 `task_pack.jsonl`，只保留夹具上的 A1，并记一条偏差，规则编号 `E2`。
   3. 大于或等于 3 且少于 5 时，全部保留，也记一条 `E2` 偏差。
   4. 大于 `scenario_target` 时，按前面的交替规则截到目标值。
   5. 最后才 split：按 `scenario_id` 排序，最后 1 个场景的两张卡进 holdout，其余进 train。场景数正好是 3 时，结果就是 train 2 个场景、holdout 1 个场景。这是合法划分，不是跌破下限。
6. 快照导入是另一个命令，失败不影响第 1 步：

```bash
python -m demand_task_factory import-snapshot \
  --raw data/snapshots/trends/raw.csv \
  --geo CN --language zh-CN \
  --time-window 2026-08-01/2026-08-31 \
  --retrieved-at <ISO8601> \
  --out data/snapshots/trends/records.jsonl
```

`raw.csv` 只认表头 `query,metric_type,metric_value`。缺文件时退出码 2，并写 blocker `integration/blockers/missing-trends-snapshot.md`。不要自己生成看起来像真数据的行。

A 线完成命令：

```bash
pytest modules/demand-task-factory/tests
```

## B 线

目录 `modules/execution-evaluation/`。包名 `execution_evaluation`。

命令：

```bash
python -m execution_evaluation run \
  --task-pack fixtures/task_pack_v1/task_pack.jsonl \
  --condition B-no-skill \
  --runs 3 \
  --experiment-id fixture-b1 \
  --out evals/runs
```

`--condition C-seed-skill` 时额外要求 `--skill fixtures/seed_skill_v1/SKILL.md`。其他条件直接退出码 2。

单次目录：

```text
evals/runs/<experiment_id>/<condition>/<task_id>/<run_n>/
  task.json
  manifest.json
  log.md
  trace.jsonl
  verdict.json
  artifacts/
```

`log.md` 用表格，列就是 `step`、`timestamp`、`action`、`observation`、`decision`、`skipped_or_abandoned`、`cost`、`artifact`。内容与 `trace.jsonl` 的对应步骤一致。

实现顺序：

1. `FakeModel` 不读网络。输入里包含任务 `prompt` 时，返回固定 JSON：`{"decision":"unknown","sources":[]}`。用它先把落盘和 verifier 测通。
2. 真实模型适配器只在 `LLM_API_KEY` 非空时启用。请求体记录 `model`、`temperature`、`max_tokens`。响应解析失败时 `status=error`，`attribution=execution_defect`，不重试解析，运行仍落盘。
3. 工具只有一个：`lookup_local(query)`，读取 `fixtures/tool_data_v1/pages.json`，返回 `{"text","source_url","effective_date"}` 或空对象。空对象时轨迹的 `observation` 写 `empty`，由模型继续；不要假装查到了内容。
4. verifier 对模型最终输出执行任务卡里的 `assertions`。全部命中则 `overall_pass=true`。超时记 `status=timeout`、`overall_pass=false`、`attribution=missing_tool_or_data`。这里以 verdict schema 的枚举为准。夹具目录名 `missing_tool/` 只是文件夹标签，不是第二个枚举值；不要为了和目录名对齐去新增 `timeout` 或 `missing_tool` 这类 attribution。接口 5xx 记 `status=error`、`attribution=execution_defect`，不要记成 schema 里不存在的 `tool_error`。与 E6 相同。
5. 污染检查：同一 `experiment_id` 下两个 condition 的 manifest，如果模型、温度、超时、工具快照或任务包哈希不同，两份 `verdict.contaminated=true`。
6. 汇总命令：

```bash
python -m execution_evaluation summarize \
  --experiment-id fixture-b1 \
  --out evals/runs/fixture-b1/baseline_report.md
```

这一条命令同时写两个文件，缺一不可。C 的 `regress` 只读 JSON，不解析 markdown。

- `--out` 指向的 markdown：给人看的报告。
- 同目录下的 `summary.json`：始终保留，不要在复制后删除。实验只含 `B-no-skill` 时，再复制一份为同目录的 `baseline_summary.json`，两份内容相同。实验只含 `C-seed-skill` 时，再复制一份为 `candidate_summary.json`，两份内容相同。集成和 C 只读带 `baseline_` 或 `candidate_` 前缀的那份；`summary.json` 是同内容的留底。两个条件都在同一个 `experiment_id` 下时，两份前缀文件都写，内容按条件分开，不能混进同一个 `pass_at_3`。此时 `summary.json` 不能同时代表两个条件，改为只写 `{"schema_version":"v1","note":"see prefixed files"}`。这个 note 不按完整 summary 必填字段校验，缺 `experiment_id`、`condition`、`pass_at_3` 是允许的。完整字段只校验两份前缀文件。

`summary.json` 必填：

```json
{
  "schema_version": "v1",
  "experiment_id": "fixture-b1",
  "condition": "B-no-skill",
  "task_count": 4,
  "pass_at_3": 0.5,
  "train_pass_at_3": 0.5,
  "holdout_pass_at_3": 0.0,
  "avg_tokens": 1200,
  "attribution_counts": {"knowledge_gap": 1},
  "examples": [
    {
      "task_id": "visa-jp-001",
      "run_n": 1,
      "status": "fail",
      "attribution": "knowledge_gap",
      "verdict_path": "evals/runs/fixture-b1/B-no-skill/visa-jp-001/1/verdict.json"
    }
  ]
}
```

`pass_at_3`、`train_pass_at_3`、`holdout_pass_at_3` 的分母都不含 `contaminated=true` 和 `live=false` 的运行。某一档分母为 0 时，该字段写 `null`，markdown 里写 `n/a`，退出码仍为 0。C 只读 `holdout_pass_at_3` 和 `avg_tokens` 做回归，不从全部任务的 `pass_at_3` 反推 holdout。

`avg_tokens` 等于纳入分母的每次运行的 `input_tokens + output_tokens` 的算术平均，与 markdown 里的「平均 input_tokens+output_tokens」是同一个数。不算工具调用次数，也不单算输入或输出。

`examples` 每条只放指针，不复制轨迹正文。未命中 E7 时，`examples` 可以是空数组 `[]`，也可以写 1 到 3 条，实现不要假定默认一定有 1 条。命中 E7（`pass_at_3` 为 0 或 1）时正好写 3 条：为 0 取失败运行，为 1 取成功运行；真实运行不足 3 条就把现有的全写上，并记偏差，不编造样例。markdown 报告用 `## examples` 列出同样的 `verdict_path`；空数组时这一节写「无」。

测试至少覆盖：无 skill 的 manifest 里 `loaded=false`；超时也产生四个文件，且 `attribution` 等于 `missing_tool_or_data`；污染运行不进 `pass_at_3`；缺任务包时退出码 2；summarize 后同目录同时存在 markdown、`summary.json` 和 `baseline_summary.json`。

B 线完成命令：

```bash
pytest modules/execution-evaluation/tests
```

## C 线

目录 `modules/skill-optimizer/`。包名 `skill_optimizer`。默认输入不要指向 holdout。

命令：

```bash
python -m skill_optimizer propose \
  --run-bundle fixtures/run_bundle_v1 \
  --out modules/skill-optimizer/out
```

输出：

```text
out/attribution_report.json
out/change_proposals.jsonl
out/candidate_skill/SKILL.md
```

`propose` 不写 `evaluation_report.md` 或 `evaluation_report.json`。这两份文件只由 `regress` 创建和覆盖。两条命令都把产物放在 `modules/skill-optimizer/out/`。`regress` 跑完后，无论走的是正常回归还是 `--no-candidate`，这个目录里都是 propose 的三份加上 regress 的两份，共五份：`attribution_report.json`、`change_proposals.jsonl`、`candidate_skill/SKILL.md`、`evaluation_report.md`、`evaluation_report.json`。不要把后两份算进 propose 的完成条件。

实现顺序：

1. 读取每次运行的 `verdict.json`。`attribution!=knowledge_gap` 的运行计入报告的排除数，不生成提案。测试要断言 `missing_tool`、`task_defect`、`success` 的 `change_id` 不存在。
2. 把相同 `failure_pattern` 的 train 任务并成一条提案。`failure_pattern` 取轨迹里第一条 `observation` 包含 `no source` 或判分为知识缺失的 `decision` 摘要，截断到 80 字。不要把 `task.prompt` 抄进 `proposed_change`。
3. `candidate_skill/SKILL.md` 由提案渲染。每条提案一个二级标题，正文只含检查步骤。提案数为 0 时仍写出这个文件，内容为 `fixtures/seed_skill_v1/SKILL.md` 的副本。`attribution_report.json` 增加字段 `proposal_state`，值为 `proposed` 或 `no_change`。不要把这个状态写进 `evaluation_report.*`。
4. 输入路径若包含名为 `holdout` 的目录，立即退出码 2，不写 `out/`。
5. 回归命令在没有两份汇总时不运行：

```bash
python -m skill_optimizer regress \
  --baseline evals/runs/<id>/baseline_summary.json \
  --candidate evals/runs/<id>/candidate_summary.json \
  --out modules/skill-optimizer/out/evaluation_report.md
```

没有候选实验时改为 `regress --baseline <baseline_summary.json> --no-candidate --out modules/skill-optimizer/out/evaluation_report.md`。`--no-candidate` 无条件写 `conclusion: no_change`，不读取 `attribution_report.json` 的 `proposal_state`，也不从上游文件推断。实现处加注释说明这一点。这条路径不读取候选分数，也不改 skill 文件。此时 `out/` 里没有 `candidate_summary.json` 是预期结果，不要为了补文件去跑第 5 步。`candidate_summary.json` 只出现在 `evals/runs/live-candidate/`，不复制进 `out/`。

`baseline_summary.json` 和 `candidate_summary.json` 都由 B 的 summarize 写出，路径与上面的 `--baseline`、`--candidate` 一致。C 不自己从 `verdict.json` 重算通过率。回归通过的条件是：候选的 `holdout_pass_at_3` 大于或等于基线，且两边的 `avg_tokens` 都按输入加输出计算后，`candidate.avg_tokens / baseline.avg_tokens <= cost_budget_ratio`。`holdout_pass_at_3` 为 `null` 时退出码 2，不把空分母当成通过。任一不满足，只由 `regress` 写出 `evaluation_report.md` 和同目录的 `evaluation_report.json`，`conclusion` 为 `rollback`，并把 `candidate_skill/SKILL.md` 恢复成 seed 的副本。缺 summary 文件时退出码 2，不估算分数。回滚不删除任何 `evals/runs/` 目录。

`evaluation_report.json` 必填 `conclusion`，只允许 `pass`、`rollback`、`no_change`。回归是否通过以这个字段为准。

C 线完成命令：

```bash
pytest modules/skill-optimizer/tests
```

## 集成

仅当下面三条都退出码 0 之后开始：

```bash
pytest modules/demand-task-factory/tests
pytest modules/execution-evaluation/tests
pytest modules/skill-optimizer/tests
```

集成脚本放在 `integration/pipeline/run_once.py`，顺序固定：

1. 校验 A 的 `task_pack.jsonl`。失败则退出码 2，B 不启动。
2. B 对真实任务包跑 `B-no-skill`，`runs=3`，`experiment_id=live-baseline`。没有 API Key 则写 blocker 并退出码 2，不把 FakeModel 的结果当作 live。跑完后立刻调用一次 summarize，确认产出 `evals/runs/live-baseline/baseline_summary.json`。
3. 从 `live-baseline` 里复制 `attribution=knowledge_gap` 且任务 `split=train` 的运行到 `integration/tmp/train-knowledge-gap/`。这是复制，不移动、不改写原运行。临时目录保持和 `fixtures/run_bundle_v1/` 相同的单次运行结构，每次运行下都有 `task.json`、`manifest.json`、`log.md`、`trace.jsonl`、`verdict.json`、`artifacts/`。复制后的 `manifest.json` 仍是原来的 `experiment_id=live-baseline`，不要改写。`propose` 不读它。路径中不能出现名为 `holdout` 的目录。
4. C 对这个临时目录执行 `propose`。`proposal_state=no_change` 时不跑第 5 步，改为调用 `regress --baseline evals/runs/live-baseline/baseline_summary.json --no-candidate`，由 `regress` 写出 `conclusion: no_change`。集成退出码 0。
5. `proposal_state=proposed` 时，B 再用候选 skill 跑一遍。manifest 字段与无 skill 相同，仅 `skill_snapshot.path` 指向候选文件，`experiment_id=live-candidate`。跑完后第二次调用 summarize，确认产出 `evals/runs/live-candidate/candidate_summary.json`。两次 summarize 不能省，因为两个实验 ID 各自只含一个 condition。
6. C 用这两份 summary 做回归。`conclusion` 为 `pass`、`rollback` 或 `no_change` 时，进程退出码都是 0。只有脚本崩溃或缺少 summary 才用非 0。CI 不能用退出码判断回归有没有过门，必须读 `evaluation_report.json` 的 `conclusion`。`rollback` 只恢复 `candidate_skill/SKILL.md`，`evals/runs/live-baseline/` 和 `evals/runs/live-candidate/` 都保留。

收口时人工能核对这 5 条即可，它们都应能从文件里查到，而不是口头声明：

1. `provenance.json` 的输入哈希对得上快照文件，且记录里没有浏览器历史字段。
2. 每条 scenario 的 `evidence_record_ids` 都能在需求记录里找到。
3. `B-no-skill` 的 manifest 全是 `loaded: false`。
4. `change_proposals.jsonl` 里每条 `evidence_tasks` 在对应 verdict 里都是 `knowledge_gap`。`proposal_state=no_change` 时这个文件允许是空文件，空文件算通过。
5. `evaluation_report.json` 的 `conclusion` 与 markdown 一致；下降时为 `rollback`，且 `evals/runs/live-candidate/` 仍在。

集成失败不修改三条线里已经通过的测试，只追加 `integration/blockers/` 或 `integration/deviations.jsonl`。
