# 接口说明 — 旅行 Agent 垂直切片

面向**要接这个服务的另一个模块**。讲的是契约、调用方式与融合边界；
**要拆走某一块代码（而不是整体接入）看 `docs/MODULES.md`**；内部实现（节点链路、判据为什么这么写）看 `docs/ARCHITECTURE.md`。

- 契约版本：`0.1.0`（`routes@2026.10.06`）
- 本文所有示例都在本机**真跑过**（2026-10-06 ~ 10-07），实测输出原样粘在下面
- 机器可读契约：`docs/openapi.json`

---

## 0. 三十秒速览

```
POST /plan   {session_id, message, slot_overrides?}  →  六种结构化响应之一
```

一次调用 = 一次完整链路（意图判定 → 检索 → 生成 → 校验）。**同步**返回，真模型下
**耗时波动很大**，同一天实测两例：`46.0s`（一次通过）与 **`181.7s`**（校验没过、回炉重跑一轮
`generate → validate`）。**调用方超时至少给 180s，建议 300s。**

想要过程进度，用 `POST /plan/start` + 轮询，或 `POST /plan/stream` 拿 NDJSON。

响应是**判别式联合**：先读 `type` 字段（`plan` / `clarify` / `realtime` / `answer` /
`guide` / `degraded`），再按那一类取字段。**不要**假设一定有 `itinerary`。

---

## 1. 端点总表

| 方法 | 路径 | 在 OpenAPI 里 | 用途 |
|---|---|---|---|
| POST | `/plan` | ✅ | **主接口**：一句话进，六类响应之一出 |
| POST | `/plan/start` | ❌ | 起后台任务，立刻返回 `{task_id}`（不等链路跑完） |
| GET | `/plan/progress/{task_id}` | ❌ | 轮询进度，返回 `{done, ms, stages[], response?, error?}` |
| POST | `/plan/stream` | ❌ | 同一条链路的 NDJSON 进度流 |
| GET | `/health` | ✅ | 健康检查 + 版本自检 |
| GET | `/session/{session_id}` | ❌ | 这条会话**现在记住了什么**（槽位 / 最近轮次 / 最近事实主体） |
| GET | `/` | ❌ | 试运行页面（`web/index.html`），不是给融合方用的业务接口 |

> ⚠️ **最容易踩的一点**：`include_in_schema=False` 让后 4 个端点不出现在
> `/openapi.json` 里 —— 但**它们全都是可用的**（试运行页面就在用）。
> 用 codegen 生成 client 的话，这 4 个要手工补。

---

## 2. 请求契约：`PlanRequest`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | string | ✅ | **会话主键**。同一个 id 才有对话记忆（追问承接、槽位沿用）。请用服务端下发的 id 或调用方自己的用户/会话 id，别每次随机 |
| `message` | string | ✅ | 用户这一句。中/英/日/韩都可以（判定见 `app/i18n.py`） |
| `slot_overrides` | object | ❌ | 结构化槽位，**不经过任何抽取**直接生效（见下） |
| `output_language` | string \| null | ❌ | 强制输出语言：`zh` / `en` / `ja` / `ko`。不传按字符集自动判定 |

### 2.1 `slot_overrides` 的键

给「口语怎么说都抽不准」的字段留一条**确定能通**的路 —— 填了就直接是槽位。

| 键 | 类型 | 落到哪个槽位 |
|---|---|---|
| `destination` | string | 目的地（词典里没有的地名也收下） |
| `days` | number | 天数 |
| `budget` | number | **总预算**（CNY），会写 `budget_basis = "total"` |
| `adults` / `children` / `elders` | number | 同行人分项；三项全空 = 没填，不写任何同行人槽位 |

```json
{
  "session_id": "u-42",
  "message": "帮我排个行程",
  "slot_overrides": {"destination": "成都", "days": 4, "budget": 8000, "adults": 2, "elders": 1}
}
```

**优先级：本句 > 表单 > 会话历史。** 本句里明确说了的，表单覆盖不了它 ——
这条别改，改了三处的语义会互相打架。

### 2.2 参数校验失败

FastAPI 标准 422，`detail[].msg` 是中文，可直接透传给终端用户：

```json
{"detail":[{"type":"value_error","loc":["body","output_language"],
  "msg":"Value error, output_language 只支持 zh/en/ja/ko，收到 'fr'","input":"fr"}]}
```

---

## 3. 响应契约：六种 `type`

| `type` | 什么时候出 | 关键字段 | 有行程吗 |
|---|---|---|---|
| `plan` | 要排行程，槽位齐全 | `itinerary` `route` `citations` `validation` `checklist` `next_questions` | ✅ |
| `clarify` | 要排行程，**缺必填槽位** | `question` `missing_slots[]` | ❌ |
| `realtime` | 会变的事实（升旗时间、今天开不开门） | `info_type` `subject` `found[]` `expired_dropped` `channels[]` `note` | ❌ |
| `answer` | 稳定的常识问询（西湖有多大） | `subject` `topic` `answer` `confidence` `hits[]` `verify[]` | ❌ |
| `guide` | 寒暄 / 道谢 / 元问题（你好、你是谁） | `kind` `reply` `starters[]` | ❌ |
| `degraded` | 链路执行**抛异常**了 | `reason` `partial` `validation` | ❌ |

### 3.1 三条有意的设计约定（融合方要知道，别当成 bug）

1. **`realtime` / `answer` / `guide` 都不是「简化版行程」**，是三条旁路。
   问「天安门几点升旗」不会回一句「想去的城市？玩几天？」—— 那是设计要避免的事。
2. **`realtime` 层不生成任何句子**（时刻会变，模型必然编错，所以只摆来源 + 给官方查询入口 `channels`）。
   `answer` 层才允许用通用常识作答，代价是必须带 `disclaimer`（未经本知识库核实）与 `verify`（复核入口）。
3. **链路异常不抛 500，而是 HTTP 200 + `type = "degraded"`**。
   调用方**必须**判 `type`，只看 HTTP 状态码会把失败当成功。

### 3.2 实测样例（真模型，2026-10-06）

请求 `{"session_id":"demo-fusion","message":"我想去北京玩 3 天，两个人，预算 3000，帮我排个行程"}`：

```
HTTP 200 · 46.0s · type=plan
顶层字段: checklist, citations, itinerary, next_questions, route, suggestions, type, validation
itinerary.title = "北京 3 天双人穷游行（3000 元预算）"   days=3   budget.total=1200.0
citations=6   validation.passed=True
next_questions = ["把第 3 天安排得轻松一点", "住宿换成便宜一点的"]
```

请求 `{"session_id":"demo-stream","message":"天安门几点升旗？"}`（走实时旁路，无需模型，毫秒级）：

```json
{"stage": "clarify",  "label": "体检必填槽位",     "detail": "实时事实（schedule）· 主体 天安门", "ms": 12}
{"stage": "realtime", "label": "判定为实时事实问询", "detail": "实时层命中 2 条", "ms": 16}
{"stage": "output",   "label": "装配最终答复",     "detail": "type=realtime", "ms": 19}
{"done": true, "ms": 21, "response": {"type": "realtime", "info_type": "schedule", "subject": "天安门", "place": "北京", ...}}
```

### 3.3 起步 + 轮询的实测轨迹（回炉那次，共 181.7s）

```
 0.1s  ['clarify']
 3.3s  ['clarify','classify','route']
 3.8s  ['clarify','classify','route','retrieve']
61.5s  ['clarify','classify','route','retrieve','generate','validate']
181.7s ['clarify','classify','route','retrieve','generate','validate',
        'generate','validate','output']        ← 校验不过，回炉重跑了一轮
done=True  ms=181636  type=plan
```

> ⚠️ **`generate` / `validate` 会出现两次**（本期固定「最多回炉一轮」）。
> 做进度条时别按「节点名唯一」去点亮，按**数组长度**推进。

### 3.4 各类型的字段（`*` = 必填）

```
ClarifyResponse    question*              missing_slots
PlanResponse       route* itinerary* validation*   suggestions checklist citations next_questions
RealtimeResponse   info_type* label* subject*      place message_echo question_zh found
                                                   expired_dropped channels unverified note plan_hint
                                                   disclaimer next_questions
AnswerResponse     subject*               topic message_echo question_zh answer confidence
                                          unknown_reason answer_title verify_title hits verify
                                          note plan_hint disclaimer next_questions
GuideResponse      （无必填）             kind message_echo reply starters note disclaimer
DegradedResponse   reason* validation*    partial
```

`Itinerary`：`title*` `destination` `party` `date_range` `scenes[]` `days[]` `budget` `notes[]` `disclaimers[]`
`Citation`：`citation_key*` `chunk_id` `source` `source_url` `effective_date` `fresh_until` `origin`
`ValidationReport`：`passed*` `round` `checks[]` `repair_actions[]` `unresolved[]` `citations_verified[]` `degraded`

---

## 4. 调用示例

### 4.1 直接 HTTP

```bash
curl -s -X POST http://127.0.0.1:8000/plan \
  -H "Content-Type: application/json" \
  -d '{"session_id":"u-42","message":"我想去北京玩 3 天，两个人，预算 3000"}' \
  | python -c "import json,sys; d=json.load(sys.stdin); print(d['type'], d.get('itinerary',{}).get('title'))"
```

### 4.2 要进度：起步 + 短轮询（推荐）

试运行页面走的就是这条 —— 每个轮询请求都是独立小请求，**再笨的代理也缓冲不了它**。

```python
import json, time, urllib.request

BASE = "http://127.0.0.1:8000"
payload = {"session_id": "u-42", "message": "我想去北京玩 3 天，两个人，预算 3000"}

def post(path, body):
    req = urllib.request.Request(BASE + path, method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())

task_id = post("/plan/start", payload)["task_id"]          # 立刻返回，不等链路
while True:
    st = json.loads(urllib.request.urlopen(f"{BASE}/plan/progress/{task_id}", timeout=30).read())
    print("已完成节点:", [s["stage"] for s in st["stages"]])   # 每格都比上次多一个
    if st["done"]:
        break
    time.sleep(0.4)
resp = st["response"]        # 字段与 POST /plan 完全一致
assert resp["type"] != "degraded", st.get("error")
```

> 任务表是**进程内的**，最多保留最近 50 个；`task_id` 只能在**同一个 worker** 里轮询。
> 实测这条通道真模型跑完要 **181.7s**（回炉那次），轮询间隔 0.4s 全程无压力 ——
> 短轮询的开销远低于超时重试的代价。

### 4.3 要流：NDJSON

```bash
curl -N -X POST http://127.0.0.1:8000/plan/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"u-42","message":"天安门几点升旗？"}'
```

行格式：`{"stage","label","detail","ms"}` → … → `{"done":true,"ms",response}`；出错则 `{"error":"..."}`。
响应头带 `X-Accel-Buffering: no` 与 `Cache-Control: no-cache, no-transform`，但**中间如果有会缓冲的代理，
流仍可能被攒到结束**——这也是为什么还留了 4.2 的轮询那套。

### 4.4 内嵌：挂进宿主 FastAPI（实测通过）

```python
from fastapi import FastAPI
from app.main import create_app
from app.deps import build_deps

deps = build_deps()                    # 不传就用 .env 里的真模型
host = FastAPI(title="host-app")
host.mount("/travel", create_app(deps))

# 实测：POST /travel/plan -> 200；GET /travel/health -> 200；GET /travel/ -> 200
```

注意 `mount` 只加**访问前缀**，不改路由定义 —— 访问路径是 `/travel/plan`。
`create_app()` 没有 lifespan/启动钩子，挂载是安全的；但**每条 mount 都带自己的
`app.state.deps`**，多挂一份就要多一份依赖。

### 4.5 内嵌：跳过 HTTP，直接调图（实测通过）

融合方如果就在同进程、只想要结构化结果，这条最省：

```python
from app.deps import build_deps
from app.graph import build_graph

deps = build_deps()
graph = build_graph(deps)
final = graph.invoke({"request_id": "r1", "session_id": "g2",
                      "message": "三个人去上海 5 天，预算一万二", "round": 0})

final["response"]        # 六类响应之一（同 HTTP 契约）
final["slots"]           # 已识别槽位
final["missing_slots"]   # 还缺哪些

# 实测输出：
#   response.type = plan
#   slots = {"destination": "上海", "days": 5, "party_size": 3, "party": "3 位成人",
#            "budget": 12000.0, "budget_basis": "total", ...}
#   missing_slots = []
```

**同一个 `deps` 连续调用 = 同一份会话记忆**（实测：第 2 轮带 `session_id="g2"` 问
「那要预约吗？」，服务端 `turn` 变 2 并接上了上一轮的上海）。跨进程/多实例时这条不成立。

### 4.6 读会话快照

```bash
curl -s http://127.0.0.1:8000/session/demo-fusion
```

```json
{"session_id":"demo-fusion","turn":1,
 "slots":{"destination":"北京","days":3,"party_size":2,"party":"2 位成人","budget":3000.0,...},
 "last_fact_subject":null,"recent":[...]}
```

---

## 5. 运行与配置

### 5.1 启动

```bash
# 需要真实模型（读根目录 .env）
./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --host 127.0.0.1

# 无密钥、无 docker 的离线跑法（检索走内存、LLM 走假实现）
TRAVEL_VECTOR_BACKEND=memory TRAVEL_LLM_PROVIDER=fake ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

改 `app/**` 或 `routes.yaml` **必须重启**；改 `web/index.html` 不用（每次读盘）。

### 5.2 环境变量（全部经 `app/config.py` 唯一入口读取）

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRAVEL_LLM_PROVIDER` | 有 key → `openai`，否则 `fake` | `fake` = 离线假模型，只验链路 |
| `TRAVEL_LLM_API_KEY` | — | 共享 key（分类/生成都用它） |
| `TRAVEL_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容端点 |
| `TRAVEL_CLASSIFIER_MODEL` / `TRAVEL_GENERATOR_MODEL` | — | 两个角色可分别指定 |
| `TRAVEL_LLM_TIMEOUT_CLASSIFIER_S` | `15` | 分类（小模型）单次超时；正常 2–4s，给短才不至于端点卡死时白等 |
| `TRAVEL_LLM_TIMEOUT_GENERATOR_S` | `180` | 行程生成单次超时；正常 45–105s（长 JSON），必须留足余量 |
| `TRAVEL_LLM_TIMEOUT_ANSWER_S` | `90` | 常识作答单次超时 |
| `TRAVEL_LLM_TIMEOUT_GUIDE_S` | `6` | **仅 `meta`**（你是谁/能做什么）单次超时；问候/致谢/道别/取消是套话，已改定稿直答，**根本不调模型** |
| `TRAVEL_EMBEDDING_MODEL` | 空 = 本地 hash | 空字符串是**刻意**的离线开关 |
| `TRAVEL_EMBEDDING_DIM` | `1024` | 必须与建表 `vector(N)` 一致 |
| `TRAVEL_VECTOR_BACKEND` | `pg` | `memory`（无需 docker） / `pg`（pgvector） |
| `DATABASE_URL` | `postgresql://travel:travel@localhost:5433/travel` | 仅 pg 用 |
| `TRAVEL_ROUTES` | `routes.yaml` | 路由表位置 |
| `TRAVEL_SEED_DIR` | `seed/` | 知识库位置 |

> **超时按档位，不共用一个数**（2026-10-09）。旧的 `TRAVEL_LLM_TIMEOUT_S` 已废止：
> 分类正常只要 2–4s、生成正常要 45–105s，一个 60s 同时造成「小模型卡死白等 60s」和
> 「正常生成被误判超时再重跑」。档位 = 角色 + 任务，见 `app/llm.py` 顶部。
> `response_format` 仍是 `json_schema → json_object → 纯文本` 三档**降级**，但客户端会
> 记住最近成功的那一档、下次直接从它开始；每次尝试都打日志（失败与偏慢的进 WARNING）。

### 5.3 自检

```bash
curl -s http://127.0.0.1:8000/health
```

```json
{"status":"ok","llm_provider":"openai","vector_backend":"memory","chunks":170,
 "embedding":"ecnu-embedding-small","embedding_dim":1024,"embedding_batch":16,
 "routes_version":"routes@2026.10.06","retriever_version":"retr-0.1.0","classifier_version":"cls-0.1.0"}
```

`embedding_dim` 与建表维度不一致是最容易配错的一环，所以摆在 health 里一眼可见。

---

## 6. 融合边界（先看这节，再决定怎么接）

| # | 事实 | 对融合的影响 |
|---|---|---|
| 1 | **无鉴权**，`CORSMiddleware` 是 `allow_origins=["*"]`，服务只监听 `127.0.0.1` | 只适合本机/内网。对外必须由宿主加网关、鉴权与限流 |
| 2 | **会话记忆是进程内的**（`SessionStore` + 审计 JSONL 落 `.workbuddy/audit/`），**重启即丢** | 多副本/`--workers>1` 时同一用户会「记忆漂移」。要稳定就固定单实例，或把会话外置（需改造） |
| 3 | `/plan/start` 的任务表是**进程内 dict**（保留最近 50 个） | `task_id` 必须回**同一个 worker** 轮询。多实例部署下这条会随机 404 |
| 4 | `/plan` 是 `def`（同步），FastAPI 放线程池跑 | 单次耗时见下条，并发上限≈线程池大小（anyio 默认 40）。高并发要自己加信号量或改异步 |
| 5 | 真模型**单链路耗时波动很大**：实测 **11.3s**（一次通过）～ **181.7s**（回炉一轮）。2026-10-09 起模型调用超时按档位给（分类 15s / 生成 180s），单请求内最多 2–3 次尝试，最坏 ≈ 2×180s + 回炉 | 调用方超时至少 180s、建议 300s，别无脑套 30s |
| 6 | **重试没有幂等键** | 重试 = 重新跑一遍链路、重新计费。要么按 `session_id` 去重，要么把 `/plan/start` 的 `task_id` 存下来复用 |
| 7 | 检索查询**恒为中文**（知识库是中文语料），`output_language` 只管输出正文 | 别指望传英文 `message` 会去检索英文语料 |
| 8 | `settings` 走 `lru_cache`，`routes.yaml` 进程内只读一次 | 改配置必须重启；热更新要另做 |
| 9 | 链路异常 → **HTTP 200 + `type="degraded"`** | 只判状态码会把失败当成功，必须判 `type` |
| 10 | 实时事实有**有效期闸门**（`fresh_until`），过期条目不返回，只在 `expired_dropped` 里计数 | 「知识库没有」与「有但过期」是两件事，UI 要分开讲 |

---

## 7. 知识库与数据（融合方若要加料）

- 数据在 `seed/*.jsonl`，每行一个 chunk：

```json
{"chunk_id":"gen-in-food-001","layer":"general","destination":"中国",
 "text":"...","source":"...","source_url":"https://...",
 "effective_date":"2026-09-01","fresh_until":"2026-12-31",
 "metadata":{"kind":"scene:food","scene":"general"}}
```

- `layer` 三档：`general` / `scene` / `realtime`。**只有 `realtime` 层有 `fresh_until` 时效语义**
- 加料后**必须重启**（内存后端在启动时装载），并在 `/health` 的 `chunks` 上看到数字变化
- 当前规模：**170 条**

---

## 8. 重新生成契约（代码改了就跑一次）

```bash
curl -s http://127.0.0.1:8000/openapi.json > docs/openapi.json
```

`docs/openapi.json` 是**生成物**，别手改。注意它只含 `/plan` 与 `/health`
（其余端点 `include_in_schema=False`，见 §1）。

---

## 9. 融合前需要对齐的几件事

请对方确认下面几条，我好把本文补成最终版：

1. **融合形态**：跨进程 HTTP 调用 / 同进程内嵌（§4.4 或 §4.5）/ 两者都要？
2. **会话 id 由谁生成**，是否需要跨请求、跨天保持？
3. **部署形态**：单实例（当前假设）还是多副本？多副本的话 §6 的 2、3 两条要改造。
4. **鉴权与限流**放在哪一层？
5. **输出语言**：跟随用户输入，还是由宿主统一指定？
6. 是否需要**批量/异步回调**（当前只有同步、起步+轮询、NDJSON 三种）？

---

## 附：变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-06 | 首版。实测 `POST /plan`（真模型 46s 出稿）、`/plan/stream`、`/session/{id}`、整站挂载、直接调图五条路径 |
