# 本端小模型轨 —— 轨迹文档

> 目标：在**不改动 skill** 的前提下，用本机小模型把 TripPal 的 332 张任务集跑完，
> 逐条记录过程与结果；等另外两端（大模型侧）的轨迹文档出来后再综合分析 skill 怎么改。
> 本文件是过程记录；收尾时的交接说明见 [`handover-local-track.md`](handover-local-track.md)。

## 0. 冻结基线（本次全程不得改动 skill）

| 项 | 值 |
|---|---|
| skill 目录 | `skills/trippal/`（27 个文件） |
| **skill_tree_sha256** | `e6edabf3200ec7c00e69aa21ad8e80a1486e6c14ac15e86160e35ec909126c3c` |
| 任务集 | `skill-loop/modules/demand-task-factory/out-v2/task_pack.jsonl`（**332 张**） |
| **pack_sha256** | `718cc2a6bd17306a…` |
| 数据集构成 | A39 / B130 / C80 / D39 / E20 / F24；train 244 / holdout 88 |

**约束**：本轨只读 skill。若最终提交里 `skills/trippal/**` 有改动，则本轨的结论不成立。

## 1. 运行配置（本端）

| 项 | 值 | 备注 |
|---|---|---|
| 模型 | `minicpm5-2b`（MiniCPM5 2.6B，Q4_K_M） | 本机 LM Studio |
| 上下文 / 并发 | **65536 / 4** | 见 §3 的配置发现：16K+4 并发会整批 HTTP 400 |
| 注入模式 | **按域注入**（`--skill-route static/routing.json`） | 只注入命中的域分片，模拟文件型 agent |
| 注入文件数 | 10 / 张（含 SKILL.md、manifest、4 core、3 references、命中分片） | 全量注入是 21 张 |
| 采样与预算 | `-Runs 1 -MaxTokens 4096 -TimeoutSeconds 300` | 与历史基线同口径 |
| 运行脚本 | `integration/pipeline/run_v2_parallel.ps1` | 4 路并行 |

## 2. 跑批清单（本端）

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

（主跑开始后，这里按族/按模式补充。）

## 5. 待办

- [ ] 主跑（332 张，`C-seed-skill` 按域注入）跑完并汇总
- [ ] 对照跑（332 张，`B-no-skill`）跑完并汇总
- [ ] 按族 / 按模式给出对比（用 `integration/pipeline/pattern_regress.py`，不要只看总分）
- [ ] 收集三端轨迹：本端（本文件）、大模型侧（协作者的文档）、以及大模型侧对 skill 的改动建议
- [ ] 写 `handover-local-track.md` 并提交推送
