# MiniCPM5-2B 轨（`minicpm5-2b`）—— 轨迹文档

> 目标：在**不改动 skill** 的前提下，用本机小模型把 TripPal 的 332 张任务集跑完，
> 逐条记录过程与结果；等另外两端（大模型侧）的轨迹文档出来后再综合分析 skill 怎么改。
> 本文件即该轨的交接记录：冻结基线、运行配置、结果与原始汇总路径都在这里；轻量分析见
> [`minicpm-track-analysis.md`](minicpm-track-analysis.md)。

## 0. 冻结基线（本次全程不得改动 skill）

| 项 | 值 |
|---|---|
| skill 目录 | `skills/trippal/`（27 个文件） |
| **skill_tree_sha256** | `e6edabf3200ec7c00e69aa21ad8e80a1486e6c14ac15e86160e35ec909126c3c` |
| 任务集 | `skill-loop/modules/demand-task-factory/out-v2/task_pack.jsonl`（**332 张**） |
| **pack_sha256** | `718cc2a6bd17306a…` |
| 数据集构成 | A39 / B130 / C80 / D39 / E20 / F24；train 244 / holdout 88 |

**约束**：本轨只读 skill。若最终提交里 `skills/trippal/**` 有改动，则本轨的结论不成立。

## 1. 运行配置（MiniCPM5-2B / LM Studio 本机）

| 项 | 值 | 备注 |
|---|---|---|
| 模型 | `minicpm5-2b`（MiniCPM5 2.6B，Q4_K_M） | 本机 LM Studio |
| 上下文 / 并发 | **65536 / 4** | 见 §3 的配置发现：16K+4 并发会整批 HTTP 400 |
| 注入模式 | **按域注入**（`--skill-route static/routing.json`） | 只注入命中的域分片，模拟文件型 agent |
| 注入文件数 | 10 / 张（含 SKILL.md、manifest、4 core、3 references、命中分片） | 全量注入是 21 张 |
| 采样与预算 | `-Runs 1 -MaxTokens 4096 -TimeoutSeconds 300` | 与历史基线同口径 |
| 运行脚本 | `integration/pipeline/run_v2_parallel.ps1` | 4 路并行 |

## 2. 跑批清单（MiniCPM5-2B）

| # | 臂 | 条件 | 张数 | 产物目录 | 状态 |
|---|---|---|---|---|---|
| 0 | 试点 | `C-seed-skill`（按域注入） | 12（跨 entry/ticketing/connectivity） | `%TEMP%\v2-route-64k` | ✅ 完成：通过 7/12，断言 45/52，0 错误 |
| 1 | 主跑 | `C-seed-skill`（按域注入） | **332（全部）** | `%TEMP%\v2-local-skill` | ⏳ 进行中 |
| 2 | 对照 | `B-no-skill` | 332（全部） | `%TEMP%\v2-local-base` | ⏳ 待跑 |

> 产物目录在 `%TEMP%`：`runs/<experiment>/<condition>/<task_id>/<n>/{verdict,trace}.json` +
> `artifacts/answer.json`（逐条断言）+ `summary.json`。

## 3. 过程中发现的配置/工具缺陷（都已修，且**与 skill 内容无关**）

1. **`.env` 被按 ANSI 解码，base URL 被静默丢弃** → 12 张卡全部 `HTTP 401`
   （请求打到默认 OpenAI 端点 + 本地 key）。
   根因：PS 5.1 的 `Get-Content` 用 ANSI 读 UTF-8，中文注释行的乱码吞掉换行，
   把注释与 `TRAVEL_LLM_BASE_URL=…` 并成一行 → 正则拒绝 → 键丢失。
   修法：脚本改用 `[System.IO.File]::ReadAllLines(..., UTF8)`，并**在启动前校验**
   "有 key 无 base URL" 直接报错，不再静默跑废一整批。
2. **机读资产被当散文注入**：`static/corpus-map.json`（20.7 KB）与 `routing.json`（1.5 KB）
   是给工具/路由用的，不该进模型上下文（域分片里已列了本域页面）。
   实测注入量从 ~43 KB 降到 ~21 KB。修法：注入 glob 收敛为 `manifest.yaml` +
   `references/**/*.{md,txt}` + `static/**/*.md`。
3. **4 路并行 + 16K 上下文 → 整批 HTTP 400**：每槽约 4K，而带 skill 的提示约 8K token。
   修法（本端）：把本地模型重载为 **64K 上下文 / 4 并发**（显存 4911/8188 MiB）。
   教训：这类失败会被记成 `execution_defect/http_400`（**不会**误算成 `knowledge_gap`），
   但如果不看 `failure_kind`，很容易误读成"模型不行"。
4. **错误信息不带服务端正文**：原先只记 `HTTP 400`，无法区分"上下文超长/请求不合法/模型没加载"。
   修法：`ModelParseError` 现在携带服务端返回正文前 300 字。

## 4. 逐条观察（随跑随记）

### 试点（12 张，跨 3 个域）

| 观察 | 证据 |
|---|---|
| 按域注入生效且可审计 | `manifest.skill_snapshot.routing = {task_field: scenario_id, task_value: TP-S04, fragments: [static/fragments/domain/ticketing.md]}` |
| 注入量随域变化 | 全量 21 文件 / 按域 10 文件（试点时含 JSON 为 23 / 12） |
| 内容失败而非流程失败 | 5 张失败全部是 `knowledge_gap`（断言未命中），0 张执行错误 |

### 主跑最终结果（`C-seed-skill` 按域注入，332/332 完成，退出码 0）

| 指标 | 值 |
|---|---|
| 完成 | **332 / 332**（`-Jobs 4`，无中断） |
| 整卡通过 | **255 / 332 = 76.8%** |
| **断言通过率** | **1096 / 1213 = 90.4%**（`mean_assertion_pass = 0.9118`） |
| train / holdout | 0.799 / **0.682** |
| avg_tokens / 次 | **10220**（input 8596 + output 1624） |
| 归因 | `knowledge_gap` 55 / 通过 255 / `execution_defect` 22 |
| failure_kind | `budget_exhausted` 22（其余为 `knowledge_gap`） |

**按族**

| 族 | 张数 | 整卡通过 | 断言通过率 | knowledge_gap | 预算截断 |
|---|---|---|---|---|---|
| A 有据事实答 | 39 | 34 (87%) | 95.5% | 5 | 0 |
| B 规则应用 | 130 | 92 (71%) | 92.0% | 24 | **14** |
| C 产物卡 | 80 | 63 (79%) | 85.8% | 11 | 6 |
| D 拒答 | 39 | 31 (79%) | 94.2% | 8 | 0 |
| E 工具决策 | 20 | 18 (90%) | 94.7% | 1 | 1 |
| F 到达后动作 | 24 | 17 (71%) | 91.3% | 6 | 1 |
| **合计** | **332** | **255 (76.8%)** | **90.35%** | 55 | 22 |

原始汇总已存档：[`minicpm-track-results/summary.json`](minicpm-track-results/summary.json)、
[`minicpm-track-results/report.md`](minicpm-track-results/report.md)。

### 观察（只描述本轨看到的事实，不做因果结论）

1. **22 张执行错误全部是 `budget_exhausted`**，集中在 B 族 14 张（`already_holds_visa` 模式为主）
   与 C 族 6 张：推理模型把 4096 token 全用在 `reasoning` 上、没产出正文。
   它们被正确记成 `execution_defect`，**不是** `knowledge_gap`，因此不会污染 SKILL 学习线。
   要消除它们只需提高输出预算后复跑这批卡（本次未做，属本目标范围之外）。
2. **断言通过率（90.4%）明显高于整卡通过率（76.8%）**：多数失败是"差一条断言"，
   而不是整张卡跑偏 —— 这也是为什么建议下游看 `mean_assertion_pass`，而不只看 pass 率。
3. **holdout(0.682) 低于 train(0.799)**：分组切分下未见泄漏迹象，与数据集设计一致。
4. **本轨不能用来自证"skill 有效"**：本次没有跑同包同配置的 `B-no-skill` 对照臂，
   而数据集本身在 R11–R13 被修过（A 族 36→39、JSON 契约补 `date` 等）。
   因此"A 族 87%"只能说明"在当前包 + 当前 skill 下小模型的表现"，
   **不能**拆成"skill 的贡献"与"题目变简单的贡献"。要做这个拆分需要补一条对照臂。
5. 本轨结论只在**这一配置**下成立：MiniCPM5 2.6B / 64K 上下文 / 4 并发 / 按域注入 / 4096 输出预算。

## 5. 本轨到此停止（按指令收口）

本轨只负责"本端小模型把 332 张跑完并留下轨迹"。以下工作**按指令不做**，留给后续或另外两端：

- 同包同配置的 `B-no-skill` 对照臂（要做效果拆分就必须补）
- 22 张预算截断卡的高预算复跑
- 按模式对比与 skill 改动建议（等另外两端的大模型侧轨迹文档出来后再综合分析）
- 独立交接文档：本文件即为交接记录（冻结点、配置、结果、原始汇总路径都在这里）

**复现命令**（`%TEMP%` 被清理后可用它重跑）：

```powershell
# 1) 本地模型需重载为 64K / 4 并发（16K + 4 并发会因每槽仅 4K 而整批 HTTP 400）
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" unload --all
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" load minicpm5-2b --context-length 65536 --parallel 4 -y

# 2) 主跑（332 张，按域注入）
cd skill-loop
& .\integration\pipeline\run_v2_parallel.ps1 -Pack modules/demand-task-factory/out-v2/task_pack.jsonl `
    -Out "$env:TEMP\v2-minicpm-skill" -Jobs 4 -Runs 1 -MaxTokens 4096 -TimeoutSeconds 300 `
    -ExperimentId localskill -Condition C-seed-skill -Skill "..\skills\trippal" `
    -SkillRoute "..\skills\trippal\static\routing.json"
```

> 说明：2026-10-04 那次运行的实际产物目录名是 `%TEMP%\v2-local-skill`（早期命名，含 "local"）；
> 上面的复现命令用新名 `v2-minicpm-skill`，两者内容一致。

**读结果的入口**：`summary.json`（总指标）、`report.md`（含 examples）、
`runs/localskill/C-seed-skill/<task_id>/1/{verdict,trace}.json`、`artifacts/answer.json`（逐条断言）。
