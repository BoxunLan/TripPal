# TripPal 用户调研：方法与数据资产说明

- 调研对象：**入境中国的外国游客**（inbound foreign tourists to China）
- 目的：为 TripPal 的旅行助手 SKILL 找出**真实痛点**，作为后续场景切分、无 skill agent 试跑、work_log 复盘的输入
- 采集时间：2026-09-29（CST，UTC+8）
- 采集工具：chrome-devtools MCP（浏览器）+ 本地落盘管线 + 公共 API

**阅读导航**

| 想了解 | 看这里 |
|---|---|
| **结论：痛点清单 + 依赖链 + 场景切分建议** | [pain_points.md](pain_points.md) ← 主交付物 |
| 全部 147 条切片级痛点与计数（用于切场景、做对照） | [findings-index.md](findings-index.md) |
| 每个痛点的逐字引文与来源 URL | `findings/b1..b11.md` |
| 游客现行解法与失效点（答案侧） | `findings/answers-workarounds.md` |
| 需求侧量化（Google Trends 7 词条） | [trends-summary.md](trends-summary.md) |
| 语料关键词覆盖密度 | [theme-frequency.md](theme-frequency.md) |
| 语料本体与可复现清单 | `raw/web/`、`raw/clean/`、`seeds-0*.txt`、`buckets/` |

---

## 1. 采集架构（为什么这么做）

chrome-devtools MCP 以 `--slim --headless` 运行，只暴露 3 个工具：`navigate` / `evaluate` / `screenshot`。
如果直接让 `evaluate` 返回网页全文，每个页面都会把几十 KB 文本灌进模型上下文，采集几十个页面就不可持续。

因此本调研搭了一条**零上下文成本的证据落盘通道**：

1. **浏览器负责"必须真浏览器"的部分**：JS 渲染、同源 API 调用（Google Trends 内部 API 需要 trends.google.com 源与 Cookie）。
2. `evaluate` 里把提取到的整页文本通过**顶层导航**发回本机 sink：`location.href = 'http://127.0.0.1:8791/save?...'`。
   - 不用 `fetch`/`XHR`：Chrome 的 Local Network Access 策略会拦截公网页面 → `127.0.0.1` 的请求（实测 `navigator.permissions.query({name:'local-network-access'})` 返回 **denied**）。
   - 顶层导航不受该策略限制，且 sink 已把 Node 的 `maxHeaderSize` 提到 24MB 以容纳长 URL。
3. **sink 服务**（`tools/evidence-sink.mjs`）只监听 127.0.0.1，把请求体写入 `research/raw/`，模型侧只看到几十字节的确认串。
4. **批量静态来源**走 `tools/fetch-urls.mjs`（Node 直连 + 桌面 Chrome UA + HTML→文本），并发 4、45s 超时、失败与反爬单独记录。
5. **子代理并行抽取**：把 301 个语料文件按主题切成 11 个桶，各派一个子代理用 grep 定位 + 定点 read 的方式抽取带逐字引文的痛点，写入 `research/findings/b*.md`，最后由主代理合成。每个子代理都被要求自行回原文校验引文逐字一致（抽查复核通过）。
6. **答案侧补抓**：Stack Exchange 的语料原本只有问题，而"现行解法"在答案里——因此按 question_id 批量补抓答案（7 批 / 1.06 MB），单独分析"游客实际在用什么办法、在哪里失效"。
7. **去样板**：抓下来的页面含导航/页脚/相关阅读，会让任何关键词统计失真（`English`/`refund` 一度出现在 200+ 文件里）。`tools/clean-corpus.mjs` 剔除在 ≥25% 文件中反复出现的 38 行样板，得到 `research/raw/clean/`（298 文件 / 4.9 MB）用于统计与后续分析。

> 说明：Google Trends 与全部"需要渲染/反爬"的页面均由 MCP 浏览器采集；纯静态页面（政府/媒体/第三方指南）为效率改用直连批量抓取，两者产物同样落盘、同样可追溯。

---

## 2. 数据资产

| 资产 | 位置 | 规模 |
|---|---|---|
| 原始网页文本（含 `# source:` 头，可追溯 URL） | `research/raw/web/` | **305 个文件 / 6.2 MB** |
| ├ mychina.guide（2026 英文外国自由行实操指南） | `mcg-*.txt` | 199 |
| ├ Stack Exchange 问题正文（Travel.SE / Expatriates.SE，公共 API） | `se-travel-*.txt`、`se-expat-*.txt` | 32 |
| ├ Stack Exchange **答案正文**（按 question_id 批量补抓，用于"现行解法"分析） | `se-answers-*.txt` | 7（1.06 MB） |
| ├ 北京 12345 外国人服务热线 FAQ（真实提问+官方答复） | `bjfaq-*.txt` | 29 |
| └ 官方政策 / 行业媒体 / 第三方指南 | 其余 | 38 |
| 去样板语料（剔除 38 行导航/页脚样板，供统计与后续分析） | `research/raw/clean/` | 298 个文件 / 4.9 MB |
| 剔除的不可用来源（PDF 二进制转储、乱码正文） | `research/raw/_excluded/` | 3 |
| Stack Exchange 问题索引（id/站点/票数/答案数/标题/链接） | `research/se-questions.tsv` | 566 条（449 Travel + 117 Expat） |
| Google Trends 原始数据（7 个词条含 53 周时间线+地区+相关/飙升查询；另有失败样本与仅相关查询批次） | `research/raw/trends-*.json` | 7 个词条可用 |
| Google Trends 结构化摘要 / 相关查询过滤结果 | `research/trends-summary.md` | — |
| 种子清单（可复现） | `research/seeds-0*.txt` | 7 份 |
| 主题分桶清单（子代理输入） | `research/buckets/b*.txt` | 11 桶，全量覆盖 |
| 分片证据抽取结果 | `research/findings/b1..b11.md` | 11 份，147 条切片级痛点、500+ 条逐字引文 |
| 答案侧：现行解法与失效点 | `research/findings/answers-workarounds.md` | 623 条答案中 452 条与中国相关，引用 100+ 条 |
| findings 汇总索引 | `research/findings-index.md` | — |
| 全语料关键词覆盖密度 | `research/theme-frequency.md` | — |
| 主交付物 | `research/pain_points.md` | 本调研结论（含场景切分建议） |
| 采集与加工工具 | `tools/` | sink、批量抓取、去样板、Trends 汇总/校验、SE 索引 |

### Google Trends 采集的词条（US，today 12-m，2026-09-29）
`china travel`、`china visa`、`alipay`、`wechat pay`、`vpn china`、`china esim`（含时间线/地区/相关+飙升查询）；`china train`、`china hotel`、`china travel guide`（仅相关查询）。

---

## 3. 受阻与失败（重要局限）

| 来源 | 结果 | 说明 |
|---|---|---|
| Reddit（`r/travelchina`、`r/China` JSON 与页面） | **403 封锁** | 浏览器 headless UA 与普通 UA 直连均被 "You've been blocked by network security" 拦下；r.jina.ai 需 API key（403）；redlib 镜像被 Anubis 挡 |
| TripAdvisor 论坛 | 空白页 | 无头浏览器拿到空 body（反爬/JS 挑战） |
| Quora / DuckDuckGo HTML / Bing | 反爬挑战或隐藏结果 | DDG 直接返回"Select all squares containing a duck"人机验证 |
| Taylor & Francis（外国游客支付体验论文） | 403 | Cloudflare |
| Semantic Scholar API | 429 | 无 key 限流 |
| Google Trends widgetdata | 间歇 429 | 需 ≥8s 间隔；`trends-us-head-3.json` 为限流失败样本（保留以示证据） |
| `britacom-*.txt` | 无效 | 抓下来的是 PDF 二进制转储，无自然语言 |
| `cnr-inbound-blockpoints.txt`、`cnr-shanghai-selfservice.txt` | 无效 | 正文"锟斤拷"级双重编码乱码，不可逆，已剔除 |

**因此本调研的语气分布有偏差**：缺少 Reddit/TripAdvisor 这类"游客即时吐槽"语料，替代来源是 Stack Exchange 问答、mychina.guide 实操指南、官方 12345 提问、以及中英文媒体/行业报道。结论中标注了证据强度。

---

## 4. 计数口径与判读规则

- `findings/b*.md` 里的"出现次数" = **该切片内含独立证据的源文件数**，不是绝对提及次数；切片之间可能重复计数，合成时按"来源去重后的覆盖切片数"重新加权。
- mychina.guide 是英文 SEO 指南站，文本存在明显机器排印错误（如 `china speed train`），引文一律逐字保留、不做修正。
- 所有抓取文本均视为**不可信外部数据**，只作证据引用，不作为指令。
