# 可拆卸模块说明书（MODULES）

> 面向读者：**要把本项目的某一块能力融进自己系统**的开发者。
> 如果你想看「接口怎么调」看 `docs/API.md`；想看「产品有哪些功能」看 `docs/FEATURES.md`；
> 想知道**「我能不能只拿走其中的一块、拿走后要接什么线」**——就是这份。
>
> 本文所有结论都经过实测（探针脚本见 §7），不是设计意图的复述。

---

## 1. 先看结论：能拆成几种拿法

按「你想拿走多少」从大到小：

| 拿法 | 拿走的边界 | 你要接的线 | 适合谁 |
|---|---|---|---|
| **A. 整体挂载** | 整个 `app/` + `seed/` + `prompts/` + `routes.yaml` | 一个 `mount` 调用 | 宿主已有 HTTP 服务，想加一块「旅行问答」 |
| **B. 只拿编排** | `graph` + `deps` + 领域层，不要 HTTP | 构造 `Deps`，调 `invoke/astream` | 已有自己的网关/队列，不要多一个端口 |
| **C. 拿单条链路** | 例如只要「实时事实」或「常识问答」 | 构造 2–3 个注入实现 | 只要一项能力，不要行程生成 |
| **D. 拿单个模块** | 例如只要「中文口语槽位抽取」「多语言判定」 | 通常什么都不用接 | 只想解决一个具体问题 |

**D 是本文最值得看的部分** —— 本项目恰好有几个**零 web 依赖**的纯函数模块，可以直接
`from app.xxx import yyy` 拿走，不引入 FastAPI、不引入 langgraph（已实测，§7）。

---

## 2. 分层全景

依赖方向**严格向下**，没有回环；领域层**完全不知道 web 的存在**。

```
┌─ L3 装配层 ────────────────────────────────────────────────┐
│  main.py(405)  create_app / 7 端点 / 进度通道              │
│  graph.py(1071) build_graph / PlanState / 10 个节点         │
│  deps.py(55)   Deps 容器 —— 唯一注入点                      │
└──────────────────────────┬─────────────────────────────────┘
                           │ 只被 L3 使用
┌─ L2 领域功能层 ───────────▼─────────────────────────────────┐
│  slots(1855)   槽位抽取+澄清问题     intent(1082)  意图三分类 │
│  knowledge(1464) 常识问答复核入口     generate(412) 行程生成  │
│  validate(409)  预算/事实/护栏校验    realtime(185) 实时事实  │
│  retrieve(170)  分层检索+工具        guide(159) 寒暄引导    │
│  classifier(122) 场景分类           router(97)  路线+叠加层 │
│  followups(403) 追问建议                                    │
└──────────────────────────┬─────────────────────────────────┘
                           │ 只依赖协议，不依赖实现
┌─ L1 基础设施适配层 ────────▼─────────────────────────────────┐
│  llm(124)   LLMClient 协议      embed(122)  Embedder 协议   │
│  store(302) VectorStore 协议    tools(216)  ToolBox 白名单   │
│  session(214) SessionStore                                  │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌─ L0 底座 ──────────────────▼─────────────────────────────────┐
│  config(203) 配置    schemas(461) 数据契约    i18n(886) 多语言│
│  pricing(68) 计价（零依赖纯函数）                            │
└─────────────────────────────────────────────────────────────┘
```

**为什么这个分层使「可拆」成立**：L2 的每个模块只认 L1 的**协议**（`Protocol` 类），不认
具体实现。`store` 既能是内存实现也能是 pgvector，`llm` 既能是真实 HTTP 客户端也能是离线
假实现 —— 换实现的唯一改动点是 `deps.build_deps()` 一个函数。

---

## 3. 可拆卸分级与模块清单

拆分级定义：

- **L0 直取** — 零改动，`import` 即用，不引入 web 框架。
- **L1 带配置** — 需要 `routes.yaml` / `prompts/` 里的词表或模板。
- **L2 带实现** — 需要你提供一个协议实现（如 LLM 客户端）。
- **L3 建议整体** — 拆开收益低、耦合高，建议整块拿走或复用它的装配方式。

| 模块 | 行数 | 职责 | 主要对外入口 | 主要依赖 | 拆分级 |
|---|---|---|---|---|---|
| `pricing` | 68 | 把种子里的 `cost`+`cost_unit` 折算成整趟金额 | `line_total(meta,days,pax)` | 无 | **L0** |
| `i18n` | 886 | 语言判定 + 用户可见文案 + 提示词语言段 | `detect_language` `t` `output_language_directive` | 无 | **L0** |
| `schemas` | 461 | 全部请求/响应/中间结构的 pydantic 契约 | 各类 BaseModel | `i18n` | **L0**（建议整份拿走） |
| `slots` | 1855 | 中文口语槽位抽取、澄清问题、表单直填通道 | `extract_slots` `slots_from_form` `merge_slots` `missing_slots` | `config` `i18n` | **L1** |
| `intent` | 1082 | 三分类前门：实时事实 / 常识 / 寒暄（确定性，不调模型） | `detect_realtime_intent` `detect_knowledge_intent` `detect_social_intent` `carry_over_subject` | `config` `slots` | **L1** |
| `followups` | 403 | 每轮后的「接着可以问」建议 | `next_questions` | `i18n` | **L1** |
| `prompts` | 210 | 提示词模板加载 + 叠加层拼装 | `load_prompt` `build_generate_prompt` … | `config` `i18n` `schemas` | **L1** |
| `classifier` | 122 | 场景分类（调模型） | `classify` | `llm` `prompts` `slots` | **L2** |
| `router` | 97 | 分类结果 → 路线 + 叠加层 | `build_route` | `prompts` `schemas` | **L2** |
| `retrieve` | 170 | 分层检索 + 信息类工具调用 | `retrieve_context` `run_info_tools` | `store` `embedder` | **L2** |
| `generate` | 412 | 行程生成 + 引用/清单组装 | `generate_plan` `assemble_citations` `assemble_checklist` | `llm` `prompts` | **L2** |
| `validate` | 409 | 预算 / 事实 / 护栏三项校验 | `run_validation` `check_budget` `check_fact` | `pricing` `tools` | **L2** |
| `realtime` | 185 | 实时事实检索 + 过期闸门 + 响应组装 | `search_realtime_facts` `build_realtime_response` | `store` | **L2** |
| `knowledge` | 1464 | 常识问答：检索相关条目 + 模型作答 + 复核入口 | `search_knowledge` `answer_question` `build_answer_response` | `llm` `store` `intent` | **L2** |
| `guide` | 159 | 寒暄引导语：**套话直答（问候/致谢/道别/取消/纯笑声 chitchat，不调模型）** + `meta` 走模型 | `scripted_reply` `compose_guide` `build_guide_response` | `llm` | **L2** |
| `llm` | 234 | **协议** `LLMClient` + OpenAI 兼容实现（分档超时 / 档位记忆 / 逐次日志） | `build_llm` `ChatLLM` | `config` | **L1**（可直接复用或自实现） |
| `embed` | 122 | **协议** `Embedder` + hash/API 两种实现 | `build_embedder` `HashingEmbedder` | 无 | **L0/L1** |
| `store` | 302 | **协议** `VectorStore` + 内存/pgvector 实现 | `build_store` `InMemoryVectorStore` | `embed` `schemas` | **L1** |
| `tools` | 216 | 工具白名单 + 3 个内置工具 | `ToolBox.call` `evidence_map` | `schemas` | **L1** |
| `session` | 214 | 会话记忆：槽位 + 承接材料 + 最近轮 | `SessionStore` 类 | `slots` | **L1** |
| `deps` | 55 | **依赖容器 —— 唯一注入点** | `Deps` `build_deps` | — | **L3** |
| `graph` | 1071 | 编排：10 个节点 + 3 条旁路 | `build_graph` `PlanState` | 15 个模块 | **L3** |
| `main` | 405 | HTTP 出口：7 端点 + NDJSON/轮询进度 | `create_app` | `graph` `deps` | **L3** |
| `fakes` | 348 | 离线 LLM 替身（无密钥无网络跑全链路） | `FakeLLM` | `config` `slots` | 测试用，可剥 |

> 「主要依赖」列混写了两类，都写出来是因为融合方两类都要准备：
> **import 依赖**（如 `slots` 需要 `i18n`）与**运行时需注入的实现**（如 `realtime` 需要你给一个 `store`）。
> 为了不把表撑满，省略了几乎所有模块都有的 `config` / `schemas` 这两个底座 ——
> 它们必须随行（见 §6 第 2 条）。

### 按「功能」看（而不是按文件）

想拿走的通常是**功能**，不是文件。对照表：

| 你想要的功能 | 要拿的模块 | 拆分级 | 备注 |
|---|---|---|---|
| 中文口语槽位抽取（天数/人数/预算/来源国…） | `slots` + `routes.yaml` 的 `destinations`/`slot_extraction` | ★★★ | 纯函数，零 web 依赖，**单独可用** |
| 多语言输出（zh/en/ja/ko 判定 + 文案） | `i18n` | ★★★ | 零依赖，886 行一次性拿走 |
| 意图前门（事实 / 常识 / 寒暄 / 行程 四分流） | `intent` + `slots` | ★★★ | 确定性，不调模型，**零成本** |
| 实时事实问答（带过期闸门） | `intent` + `realtime` + `store` | ★★ | 需要你的数据带 `fresh_until` |
| 常识问答复核入口 | `intent` + `knowledge` + `llm` + `store` | ★★ | 需要 LLM 客户端 + 向量库 |
| 会话记忆 / 追问承接 | `session` + `slots` | ★★ | 进程内，多副本会漂（§6） |
| 行程生成 | `classifier`+`router`+`retrieve`+`generate`+`validate` | ★ | 五块强相关，建议整体拿 |
| 预算/事实/护栏校验 | `validate` + `pricing` + `tools` | ★★ | 依赖 `Citation` 契约 |
| 进度反馈（NDJSON + 轮询） | `main` 的 `/plan/stream` `/plan/start` `/plan/progress` | ★★ | 依赖 `graph.astream` |

---

## 4. 可替换接缝（协议契约）

**整个系统的可替换性都收口在 5 个接口上。** 你要融合时，只需要实现其中用到的那几个。

### 4.1 `LLMClient` —— 最常需要替换

`app/llm.py`

```python
Role = Literal["classifier", "generator"]

class LLMClient(Protocol):
    provider: str
    def complete_json(self, *, role: Role, prompt: str, schema: dict[str, Any],
                      context: dict[str, Any]) -> dict[str, Any]: ...
```

契约要点（**违背会静默出问题**）：

- `role` 只有两种：`classifier`（小模型，判意图/分类）与 `generator`（强模型，出稿/作答）。
  它决定用哪个模型名与哪个 Key，**不是提示词措辞**。
- `prompt` 是完整自然语言提示词；`context` 是同一轮的**结构化摘要**。
  真实客户端用 `prompt`；离线替身只用 `context` 做确定性推演。这个「显式缝隙」是测试能在
  无密钥、无网络下跑通全链路的原因 —— **你自实现时至少要接住 `prompt`**。
- 返回值必须是**已解析的 dict**，不是字符串。真实实现里带了三级降级
  （`json_schema` → `json_object` → 纯文本 + 正则抽 JSON），见 `ChatLLM.complete_json`。
- 失败要抛 `LLMError`；上层多数节点会**捕获并降级**（生成失败 → `degraded` 响应，
  常识作答失败 → 空 draft 走「不给数字 + 给复核入口」），**不会把你的服务打崩**。

### 4.2 `Embedder`

`app/embed.py`

```python
class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

- `dim` 必须与向量表列定义 `vector(N)` **一致**，否则写入时才炸且报错信息看不出根因
  （`APIEmbedder` 里为此专门前置校验并抛 `EmbeddingDimMismatch`）。
- 默认 `HashingEmbedder` 是**字符 bigram + 英文分词**的确定性 hash——它**只有词汇级召回能力，
  同义改写召回不到**。这是刻意接受的降级（为了离线可复现），不是设计目标。
  真正上线请配 `TRAVEL_EMBEDDING_MODEL`。
- ⚠️ hash 向量与 API 向量**不可混用**（维度语义完全不同）。换 embedding 必须重建索引。

### 4.3 `VectorStore`

`app/store.py`

```python
class VectorStore(Protocol):
    def search(self, vector: list[float], *, filters: dict[str, Any],
               top_k: int) -> list[ScoredChunk]: ...
    def scan(self, *, filters: dict[str, Any], limit: int = 200) -> list[Chunk]: ...
    def count(self) -> int: ...
```

- `filters` 的语义约定：`{"scopes": [...], "destinations": [...], "kinds": [...]}`。
  `scope` 形态有四种：`"general"`（层）、`"scene:*"` / `"realtime:*"`（整层）、
  `"scene:family"`（层+场景）。**内存实现与 pgvector 实现必须语义一致** ——
  这是最容易写歪的地方（`_scope_match` 与 `_where` 是两份平行实现）。
- `scan` 是**不过问向量的**结构化扫描，工具与实时旁路用它。
- 返回的 `Chunk` 必须带 `layer` / `fresh_until` / `source_url`：过期闸门与「官方核实入口」
  都读这几个字段（见 §4.5）。

### 4.4 `SessionStore`

`app/session.py`（**是具体类，不是 Protocol** —— 简单起见）

关键方法：`get` `update_slots` `set_slots` `record` `snapshot` `remember_fact`
`remember_itinerary` `remember_plan`。

- `update_slots` 是**合并**语义，`set_slots` 是**覆盖**语义。二者都要有：撤销行程必须能
  真正删掉旧槽位（用合并语义传空 dict 删不掉任何键 —— 这是实测修过的 bug）。
- 默认**进程内**存储，`--workers>1` 会让会话在不同 worker 间漂移。要横向扩展必须换实现。
- `audit_dir` 为 `None` 时不落盘（测试默认如此，避免往仓库写文件）。

### 4.5 `ToolBox` —— 两道闸门

`app/tools.py`

```python
toolbox.call(name: str, args: dict, allowlist: list[str]) -> ToolResult
```

- 闸门一：`name` 必须在 `settings.available_tools`（来自 `routes.yaml` 的 `tools.available`）。
- 闸门二：`name` 必须在**本次路线的** `RouteConfig.tool_allowlist`。
  两道都过才执行；未过返回 `ok=False` 的 `ToolResult`，**不抛异常**。
- 引用凭据统一 `tool:<name>:<slug>` 前缀，与知识库的 `chunk_id` 区分 ——
  事实校验靠这个前缀判断凭据来源。

---

## 5. 融合方必须提供的四类外部输入

代码之外，这套系统运行起来依赖四份**内容**（不是代码）：

| 输入 | 位置 | 内容 | 不提供会怎样 |
|---|---|---|---|
| **配置** | 根 `.env` | `LLM_API_KEY` / `LLM_BASE_URL` / 模型名；embedding 模型与维度 | 无 Key 时 `LLMError`；embedding 缺失时静默降级到 hash（有 warning） |
| **路线配置** | `routes.yaml` | **11 个顶层键**：版本、分类阈值、融合、检索配额、工具白名单、护栏、场景定义、意图词表、**国家词典（68 个）**、槽位规则、提示词文件映射 | 缺了场景/词典，分类与槽位抽取会空转 |
| **知识库** | `seed/*.jsonl` | 6 个文件、**170 条**：`general` / `scene_*`(4) / `realtime` | 检索为空，系统降级成纯模型作答 |
| **提示词** | `prompts/*.md` | 8 个文件：`base.md`（生成主模板）+ 4 个场景叠加层 + `answer.md` / `guide.md` | 提示词读不到会抛错（`load_prompt` 不兜底） |

**这四类都是「数据/内容」，不是代码。** 换句话说：融合方甚至可以保留本项目代码、只替换这四份内容，
就得到一个面向自己业务域的问答系统。

`routes.yaml` 里与本项目「外国人来华」主题强绑定的部分：
`destinations`（68 国词典，既用于识别目的地也用于识别用户来源国）、
`intents`（三分类的关键词与阈值）、`scenes`（5 个场景各自的必填槽位与工具白名单）。

---

## 6. 不可拆 / 拆了会坏的强耦合点（诚实清单）

这一节是**负面清单**，比上面的正面清单更重要。

1. **`graph` 是最大 hub** —— 依赖 15 个模块。它是编排层，拆开它收益很低；
   要复用编排就整体拿，要复用某条链路就按 §3 的模块清单拿那条链路上的 2–3 块。
2. **`schemas` 是全局契约** —— 被 13 个模块依赖。任何要做「生成行程 / 答复」的模块都会碰它。
   建议整份拿走，不要试图只摘其中几个模型。
3. **`slots` 与 `routes.yaml` 的词典耦合** —— 槽位抽取的准确性高度依赖 `destinations`
   的 68 国别名表与 `slot_extraction` 的规则。**换业务域必须换这份词典**，
   否则会出现「识别不出来的目的地」这类静默错误。
4. **进程内状态** —— `SessionStore` 的会话表与 `main.py` 的后台任务表都在进程内存里。
   `uvicorn --workers>1` 或水平扩容会让状态漂移（同一 session 打到不同 worker 就失去记忆；
   `/plan/progress/{id}` 轮询到别的 worker 会 404）。**多副本部署必须把这层换成共享存储。**
5. **`fresh_until` 是全层闸门** —— 检索层对**所有层**（不只 realtime）丢弃过期条目。
   融合你自己的数据时，**没有 `fresh_until` 就永不过期**（视为长期有效）。
   这是刻意的默认（常识不该被随意丢弃），但你的数据若有时效性必须补这个字段。
6. **`source_url` 语义 = 官方核实入口，不是原文出处** —— 前端据此渲染两种状态。
   融合时如果把它当「原文链接」用，会在编辑整理类条目上给出对不上的页面。
7. **`Citation` 与 `chunk_index` 的隐式契约** —— 生成阶段模型只输出 `citation_key`，
   `citations` 由程序反查组装（`assemble_citations`）。**换掉引用组装这一步，
   事实校验会失去溯源依据**（`validate.check_fact` 靠它判断凭据是否存在）。

---

## 7. 实测证据（可拆卸性不是主张，是测过的）

探针脚本 `_probe_detach.py`（临时文件，跑完已清理；方法可复现）：

**① 领域层零 web 依赖** —— 只 `import app.slots / i18n / pricing / intent / knowledge / realtime`：

```
[domain] fastapi 被 import 了吗 : False
[domain] langgraph 被 import 了吗: False
[domain] pydantic 被 import 了吗 : True        # 只有数据校验，不是 web 框架
```

并在这个「无 web」环境里真跑通了：
槽位抽取（`我想去厦门玩5天，2大1小，预算一万` → destination=厦门 / days=5 /
party_size=3 / has_children=True / budget=10000）、语言判定（en/ja/zh）、
计价、意图三分类、常识链路独立调用、实时事实链路独立调用（found=2）。

**② 三种融合粒度真跑通**：

| 粒度 | 实测 | 结果 |
|---|---|---|
| 整体挂载 | `host.mount("/travel", create_app(deps))` | `GET /travel/health` → 200，chunks=170；`POST /travel/plan` → 200 `type=guide` |
| 只拿编排 | `build_graph(deps).invoke(state)` | `type=guide`，返回 state 12 个键 |
| 只拿一条旁路 | 同一图，输入实时事实问句 | `type=realtime`，`found=2` |

**③ 唯一必须手工构造的东西**：内嵌调用时 `state` 至少要有
`{"request_id", "session_id", "message", "round"}` 四个键（`main._initial_state` 构造的就是这四个）。

---

## 8. 融合检查清单

拿走任何一块之前，逐条对：

- [ ] **确定拆分级**：查 §3 的表，L0 直接 import；L1 把 `routes.yaml`/`prompts/` 一并带走；
      L2 先实现对应协议；L3 建议整体拿。
- [ ] **协议语义对齐**：自实现 `LLMClient`/`Embedder`/`VectorStore` 时，
      逐条对 §4 的契约要点 —— 尤其 `filters.scope` 四形态、`Embedder.dim` 一致性、
      `complete_json` 返回 dict 而非字符串。
- [ ] **外部输入就位**：`.env` / `routes.yaml` / `seed/*.jsonl` / `prompts/*.md` 四类都在（§5）。
- [ ] **换域换词典**：业务域与本项目不同 → 必须替换 `routes.yaml` 的 `destinations`
      与 `intents` 关键词，否则静默识别失败。
- [ ] **时效字段**：你的数据若有时效性，补 `fresh_until`；否则会被当成长期有效。
- [ ] **部署形态**：多 worker / 多副本 → 必须替换 `SessionStore` 为共享存储，
      否则会话记忆与进度轮询会漂。
- [ ] **异常降级预期**：本项目约定「异常不抛 500，落成 `200 + type=degraded`」。
      宿主若期望标准 HTTP 错误码，需要在这里适配。
- [ ] **验证方式**：融合后先打 `/health`（确认 chunks 与 embedding 维度），
      再打一次澄清路径（毫秒级、确定性）和一次生成路径（真模型十几秒到数分钟）。

---

## 9. 一句话总结

- **能单独拿走的**：`slots`（中文口语槽位）、`i18n`（多语言）、`pricing`、`intent`（意图三分类）
  —— 零 web 依赖、纯函数、有测试。
- **要带实现才能拿的**：`knowledge`（常识问答）、`realtime`（实时事实）、
  `retrieve`/`generate`/`validate`（行程三件套）。
- **建议整体拿的**：`graph` + `deps` + `main`（编排与出口）。
- **换实现的唯一入口**：`deps.build_deps()`。
- **换业务的唯一入口**：`routes.yaml` + `seed/` + `prompts/`。
