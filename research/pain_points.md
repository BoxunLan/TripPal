# TripPal 用户调研：入境中国外国游客的真实痛点

> 调研时间：2026-09-29（CST）｜方法与环境：chrome-devtools MCP 浏览器（`--slim --headless`）+ 本地落盘管线 + 公共 API
> 数据基础：**305 个来源文件 / 6.2 MB 文本**（含 7 批 Stack Exchange 答案、29 条北京 12345 官方问答）、**7 个 Google Trends 词条的 53 周时间线**、**11 个分片证据抽取**（147 条切片级痛点、500+ 条逐字引文，见 `research/findings/`）、**623 条 Stack Exchange 答案**的解法分析（452 条与中国相关）
> 本文件把 147 条切片级条目**归并为 42 条可执行痛点**（9 大主题）+ 依赖链 + 12 个场景切分建议
> 方法与局限详见 [README.md](README.md)

---

## 0. 怎么读

**编号**：`P-<主题字母><序号>`，主题 A–I 见第 4 节。

**严重度**（决定场景优先级）：

| 级别 | 含义 | 例子 |
|---|---|---|
| **S1** | 导致行程失败、资金损失、违法风险，或**落地后无法补救** | 进不了景区、付不出钱、被拒住、超期停留 |
| **S2** | 显著的时间/金钱/体验损失，但通常有兜底路径 | 排队 1–2 小时、被宰、账单翻倍 |
| **S3** | 不便或文化摩擦 | 小费给错、讲价失礼 |

**覆盖度**：`切片数` = 11 个独立抽取切片中报告该痛点的切片数量（跨切片独立出现 = 更强的证据）；`文件数` 为该痛点证据所涉来源文件数。两者口径不同，仅用于相对排序，**不是**真实流行度。

**证据**：每条痛点给 1–3 条逐字引文。所有原始文件头部都有 `# source: <url>`，可回溯到具体页面。

---

## 1. TL;DR：最该先做的 8 件事

| # | 痛点 | 为什么排最前 | 严重度 |
|---|---|---|---|
| 1 | **落地后无法补救的网络准备**（eSIM/VPN/App 必须行前装好，"翻墙工具自己在墙内"） | 跨 4 个切片独立出现；一旦失误整趟行程的"信息能力"归零，且无现场解法 | S1 |
| 2 | **支付：外卡受理率低 + 移动支付成事实强制 + 风控冻结无解** | 行业调研：北上广深重点商圈**外卡受理覆盖率不足 45%**；携程《2026 入境游报告》把支付列为**核心痛点之首** | S1 |
| 3 | **预约制三件套：微信小程序 + 护照实名 + 无现场售票** | 5 个切片独立出现；"能否进景区"在行前即被决定，现场无兜底 | S1 |
| 4 | **中国手机号（+86）鸡生蛋**：机场 WiFi、注册、验证码、地铁多日票都要它 | 官方 12345 热线答复本身反复承认"系统显示我的手机号不正确" | S1 |
| 5 | **住宿涉外门槛**：约 30% 三星及以下酒店/民宿仍拒接外国护照 | 官方口径早已取消"涉外资质"，但前台不会录护照信息 → 当场拒住（有 22:30 被拒的真实案例） | S1 |
| 6 | **Google Maps 双重失效**（既被墙，翻墙后仍因 GCJ-02 坐标偏移 100–700 米） | 导航不可用 → 连锁引发打车、会合、预约迟到 | S1 |
| 7 | **票务：固定放票时点 + 分钟级售罄 + 闸机认不出护照** | 抢不到就没有替代方案；闸机失败只能找人工通道，而检票提前关闭 | S2 |
| 8 | **信息可信度**：官方英文信息被山寨站压过、攻略普遍过时（144 小时规则已废） | 游客"自以为查好了"，到现场才发现规则变了 | S2 |

**一句话结论**：入境中国的痛点不是"玩什么"，而是**进入系统本身**——网络、支付、身份（护照/手机号/微信）、预约与住宿登记这五道门槛在落地前就已经决定了行程的成败。这正是 TripPal SKILL 应该发力的地方。

---

## 2. 需求侧量化证据（Google Trends，浏览器采集）

数据源：`trends.google.com/trends/api`（在 trends.google.com 页面内同源 fetch，原始 JSON 见 `research/raw/trends-*.json`）。geo=US，today 12-m，2026-09-29 抓取。趋势值为相对热度（该词自身峰值=100）。

| 词条 | 全年均值 | 前 13 周(2025/10-12) | 近 13 周(2026/7-9) | 峰值周 | 峰值 |
|---|---|---|---|---|---|
| china travel | 63.7 | 55.7 | 32.2 | Jun 7, 2026 | 100 |
| alipay | 61.9 | 60.1 | 45.8 | May 24, 2026 | 100 |
| china visa | 59.8 | 51.2 | 41.1 | May 31, 2026 | 100 |
| china esim | 51.0 | 40.8 | 31.9 | Jun 28, 2026 | 100 |
| wechat pay | 50.7 | 49.4 | 27.3 | May 24, 2026 | 100 |
| great firewall | 47.0 | 36.6 | 20.7 | Mar 1, 2026 | 100 |
| vpn china | 44.6 | 30.2 | 25.5 | Jun 7, 2026 | 100 |

**判读**：

1. **7 个词条在 2026 年 5–6 月同步见顶**，随后回落——对应北半球夏季赴华旅行季，说明这些词条由**真实出行计划**驱动，而不是资讯热度。
2. **支付与网络词条的量级与"旅行"本体同级**：`alipay`(61.9) 略低于 `china travel`(63.7)，`wechat pay`(50.7)、`china esim`(51.0)、`great firewall`(47.0)、`vpn china`(44.6) 都在同一量级。**"怎么用钱、怎么上网"与"去哪玩"是同等规模的需求**，而不是附属问题。
3. **相关查询直接暴露卡点**（已过滤掉无关泛热搜）：
   - `wechat pay`：how to use wechat pay / **wechat pay for foreigners** / **wechat pay foreign credit card** / how to set up wechat pay / alipay vs wechat pay
   - `alipay`：alipay china / alipay card / alipay international / **alipay fees** / what is alipay payment
   - `china esim`：esim in china / esim for china / airalo esim china / **china mobile esim**；飙升里出现 **"does whatsapp work in china"**
   - `vpn china`：best vpn china / astrill vpn china / **proton vpn china**
   - `china train`：china train tickets / **12306** / china train booking / china train station
   - `china travel`：飙升里出现 **"us travel advisory china detention"**、travel insurance、best travel wallets、travel itinerary template
4. **地区**：稳定出现在前列的是 California / New York / Washington / DC / Kansas 等大州（Wyoming 显示 100 是人口基数小导致的归一化伪影，判读时忽略）。

**采集异常（保留以示可追溯）**：`trends-us-head-3.json` 是 Google 对 widgetdata 接口限流（HTTP 429）下的失败样本；限流需 ≥8 秒请求间隔才稳定。

---

## 3. 全语料关键词覆盖密度（去样板后）

口径：对 `research/raw/clean`（300 个来源文件，剔除在 ≥25% 文件中反复出现的 38 行导航/页脚样板）做大小写不敏感匹配，数字为提及该词的**独立来源文件数**。

| 关键词 | 文件数 | 占比 | 关键词 | 文件数 | 占比 |
|---|---|---|---|---|---|
| English | 211 | 70% | queue（排队） | 77 | 26% |
| hotel | 198 | 66% | Google Maps | 74 | 25% |
| passport | 184 | 61% | registration（登记） | 73 | 24% |
| Alipay | 141 | 47% | VPN | 73 | 24% |
| cash | 121 | 40% | refund（退款） | 69 | 23% |
| taxi | 119 | 40% | QR code | 68 | 23% |
| WeChat Pay | 115 | 38% | ATM | 64 | 21% |
| metro | 103 | 34% | verification（核验） | 63 | 21% |
| Didi | 95 | 32% | foreign card | 56 | 19% |
| Amap | 87 | 29% | WhatsApp | 55 | 18% |
| 12306 | 83 | 28% | mini-program | 53 | 18% |
| visa-free | 79 | 26% | menu | 52 | 17% |
| eSIM | 78 | 26% | translation | 51 | 17% |

**判读**：高覆盖 ≠ 高痛点，但**指南类内容只会写在读者反复卡住的地方**，可视为"需要手把手指导"的代理指标。前六名（English / hotel / passport / Alipay / cash / taxi）与第 1 节的排序高度一致——**语言、住宿、证件、支付、现金、打车**是内容供给侧公认的"必须解释清楚"的六件事。

---

## 4. 痛点清单（9 大主题）

### A. 行前准备与"落地不可补救"

**P-A1｜落地后拿不到翻墙工具：需要它的工具本身在墙内** · S1 · 覆盖 4 切片
- 机制：应用商店、VPN 官网、eSIM 购买页在中国大陆网络下不可达；入境后再装等于不可能。
- 证据：
  - "Once you are inside China, the app stores and the VPN providers' own websites are blocked. You cannot download a VPN... **The tools you need to get over the wall are themselves behind the wall.**" — `mcg-does-whatsapp-work-in-china.txt`
  - "considerably harder to arrange once you have landed and cannot reach the app stores" — `mcg-china-survival-guide-for-tourists.txt`
- 现行解法与失效点：提前装好 VPN 并准备多个备用节点。失效点：2026 年 4 月起封堵加剧、VPN"时通时断"（`se-travel-china-vpn.txt` 中有 15 条相关问句，是该切片第一大痛点）。

**P-A2｜eSIM / SIM 的"三选一都是坑"** · S1 · 覆盖 4 切片
- 机制：① 中国本地实体卡**仍然被防火墙限制**（Gmail/Instagram 打不开），柜台不会主动提醒；② 旅行 eSIM 通常**不给本地号码**，因此无法注册本地服务、收不到验证码；③ 中国大陆版 iPhone 装不了境外 eSIM；④ 门店用外国护照实名开卡常被直接拒绝。
- 证据：
  - "that SIM is firewalled. Your Gmail won't load. Instagram won't open." — `mcg-china-sim-card-for-tourists.txt`
  - "a mainland iPhone can't install an eSIM from a non-Chinese carrier while it is inside mainland China" — `mcg-best-esim-china.txt`
  - "not possible to register with a foreign passport. I tried 10-15 different phone stores ... and they all refused" — `se-travel-china-sim.txt`
- 现行解法与失效点：买 eSIM（有流量、无本地号）+ 保留原号漫游收验证码。失效点：本地服务注册仍缺号码（"纯流量卡收不到码"，`ttgchina-payment-friction.txt`）；公共 WiFi 普遍要短信验证（"Public WiFi in China often requires SMS verification" — `holafly-mobile-internet-china.txt`）；中国版 iPhone 直接无解。

**P-A3｜支付 App 实名/绑卡必须行前做，且成功率不确定** · S1 · 覆盖 3 切片
- 机制：Alipay/WeChat 的实名认证要上传护照、部分场景要人脸核验，微信支付实名审核需 1–3 天；被拒原因多（"三个都是拍照问题"），没有自助解法。
- 证据：
  - "Almost every rejection is one of four things, and three of them are photography." — `mcg-wechat-pay-foreigner-guide.txt`
  - "Real-name verification requires a passport photo upload and takes 1-3 days for approval." — `mcg-china-survival-guide-for-tourists.txt`
  - "despite completing the setup successfully, on arrival in China sometimes payments don't actually go through at all" — `se-travel-alipay.txt`
- 现行解法与失效点：**设置成功 ≠ 能用**——这是最该被产品前置解决的信任问题（见 P-B3）。

**P-A4｜签证/免签资格判读复杂，且旧攻略满天飞** · S2 · 覆盖 3 切片
- 机制：240 小时过境免签是 **A-B-C 规则**（须飞往"第三国/地区"），与**单方面 30 天免签**（50 国名单，美国不在内）是两套完全不同的政策；"144 小时"已于 2024-12-17 废止但仍在网上流传；航司柜台/值机系统会误判（TIMATIC 出错导致拒发登机牌）。
- 证据（官方原文）：
  - "Under the 240-hour visa-free transit policy, the destination country on your confirmed onward ticket must be different from your departure country when entering China... your itinerary is considered a 'round trip' (**A-B-A**) rather than a transit, making it ineligible." — `chinadaily-240h-transit-route-faq.txt`（中国日报官方问答）
  - "Don't trust a 2023 blog post or a YouTube video about '72-hour transit' — that scheme no longer exists." — `mcg-china-visa-free-countries-list-2026.txt`
  - "check-in agents first refuse to check me in but a pointer to TIMATIC usually does the trick" — `se-travel-china-visa.txt`
- 关键细节（官方口径，SKILL 必须内置）：240 小时从**入境次日 00:00** 起算；57 国、65 个口岸、24 个省级区域；**西藏不含在内**；哈尔滨仅限哈尔滨市；港澳台计为"第三地区"；离境后再入境可重新获得 240 小时。

### B. 支付

**P-B1｜外卡受理率低，商户主动不收外卡** · S1 · 覆盖 5 切片
- 行业量化（中文行业媒体）："北上广深重点商圈**外卡受理覆盖率不足 45%**"；中小商户疫后撤掉外卡 POS；刷卡手续费约 **2.5%–3.5%**，商户倾向引导现金或移动支付 — `ttgchina-payment-friction.txt`
- 证据："The single biggest frustration for foreign travelers is a declined credit card." — `mcg-book-china-train-tickets-without-wechat.txt`；"Chinese airline websites often reject foreign credit cards." — `mcg-flying-through-china-layover-transit-guide.txt`
- 现行解法与失效点：把外卡绑到 Alipay/WeChat（Tour Pass / 国际钱包）。失效点：绑卡本身可能失败（P-B3），部分商户仅有**个人收款码**，绑卡支付不可用（新华社报道中的意大利游客案例）。

**P-B2｜移动支付成事实强制，风控冻结后无法自救** · S1 · 覆盖 3 切片
- 机制：现金"接近绝迹"、外卡普遍不收，而账号一旦被风控冻结，解封要求绑定**中国内地银行账户**——外国人拿不出；而要开内地账户本身又缺权威答案（无固定住址能否办卡）。未成年人（<18）无法完成实名，直接没有支付账户。
- 证据：
  - "in cash is close to extinct in post-COVID China, and nobody accepts Visa/MasterCard" — `se-travel-alipay.txt`
  - "linking a Chinese mainland bank account (which as a foreigner I do not have)" — `se-travel-alipay.txt`
  - "an under-18 cannot open a usable Alipay or link a card. All payments go through a parent's phone." — `mcg-first-time-china-trip-guide-for-teens.txt`

**P-B3｜外卡支付"可见但不可用"：限额、附加费、随机失败** · S2 · 覆盖 5 切片
- 行业量化："绑定境外银行卡的**单笔支付限额通常为 3000 至 6500 元**，影响高价值消费" — `ttgchina-payment-friction.txt`
- 证据："A ¥20,000 tour balance will not go through in one payment."（单笔上限）— `mcg-find-authorized-china-travel-agent-guangzhou.txt`；绑卡支付宝另收 **3%** — `mcg-china-high-speed-rail-guide.txt` 等；"foreign cards sometimes fail at the pay step if the bank is not on Alipay's accepted list" — `mcg-how-to-use-meituan-bike-in-china.txt`
- 失效点：失败是**随机**的（同一张卡不同商户结果不同），游客无法行前验证 → 必须准备多张卡 + 现金兜底。

**P-B4｜现金退化：找零难、ATM 费与假钞、兑换点稀缺点差大** · S2 · 覆盖 4 切片
- 证据："you will have to pay (almost) double the fees"（多次取现）— `se-travel-china-cash.txt`；"the exchange counter charged me an eye-whooping 60 RMB commission for one transaction" — `se-travel-china-money.txt`；"商家常备零钱不足，导致现金支付无法找零；市区外币兑换点稀少，主要集中在机场和大型银行" — `ttgchina-payment-friction.txt`；**有的司机以没有零钱为由拒收现金** — `jntimes-cyber-china-details.txt`
- ATM 假钞个案："taxis, fruit shops etc refused the notes" — `se-travel-china-money.txt`（银行与外包公司互相推诿，几乎无法追偿）

**P-B5｜支付完全依赖网络与电量** · S2 · 覆盖 3 切片
- "If your data cuts out — say, underground or due to poor roaming — the QR code won't load." — `mcg-beijing-subway-card-vs-mobile-payment.txt`

**P-B6｜退款/改签规则是隐藏陷阱** · S3 · 覆盖 2 切片
- "once you have changed a ticket, the refund fee is calculated from the new departure time" — `mcg-china-railway-refund-policy-guide.txt`；跨境退款最长 15 个工作日、预付钱包 5% 不退。

**P-B7｜离境退税流程复杂、知晓度低** · S3 · 覆盖 1 切片
- "离境退税流程复杂……申请需在口岸盖章、交单，效率较低" — `ttgchina-payment-friction.txt`
- 对照：重庆侧披露"全市离境退税商店总数达 363 家"（供给在改善，但游客感知仍是"不知道、不会办"）— `xinhua-globe-inbound-2026.txt`

### C. 网络与信息

**P-C1｜防火长城：邮箱/地图/搜索/社交全断，VPN 时通时断** · S1 · 覆盖 4 切片（**最高频单项**）
- "Without VPN I have no Google and other search engines are absolutely useless for finding this info about Xi'an" — `se-travel-china-vpn.txt`
- 影响面：Gmail（验证码/工作）、Google Maps、WhatsApp、Instagram、部分翻译服务。对游客是"信息能力"的整体剥夺，而非个别 App 不可用。

**P-C2｜处处要 +86 号码（鸡生蛋）** · S1 · 覆盖 4 切片
- "The Beijing Capital airport Wi-Fi requires a Chinese phone number for captive-portal registration, which creates a chicken-and-egg problem." — `mcg-paying-in-beijing-digital-wallet-guide.txt`
- 官方侧证据：北京 12345 热线 FAQ 中的真实提问——"the system shows that my phone number is incorrect. What should foreign visitors do"（用新加坡号码订故宫/颐和园门票失败）— `bjfaq-t20250722_4155057.txt`
- 同类场景：地铁多日票、部分 App 注册、客服热线回拨。

**P-C3｜信息可达性与可信度差：官方英文信息被山寨站压过、信息过时、热线无外语** · S2 · 覆盖 5 切片
- "the English-language version of the official website was ranked fifth, after two pages from the unofficial website" — `se-travel-china-hotel.txt`
- 官方热线："Note: currently no foreign language service is available" — `bjfaq-t20250722_4155070.txt`
- 中文媒体口径："多數APP僅面向境內用戶設計，未充分考慮境外使用者需求"；"景區、道路的多語種標識翻譯錯誤或不一致" — `wenweipo-shanghai-inbound.txt`

**P-C4｜公共 Wi-Fi 质量差且登录页中文** · S3 · 覆盖 2 切片
- "So many hotels I stayed at (3~4 stars ratings) provided too slow Wi-Fi" — `se-travel-china-vpn.txt`

### D. 导航与语言

**P-D1｜Google Maps 双重失效：被墙 + GCJ-02 坐标偏移** · S1 · 覆盖 4 切片
- "a pin that floats anywhere from 100 to 700 meters away from where you are actually standing" — `mcg-does-google-maps-work-in-china.txt`
- 失效点：即使 VPN 通了，偏移依旧存在——**这不是"翻墙就能解决"的问题**，必须换本地地图并做坐标校正。

**P-D2｜本地地图/点评 App 中文界面** · S2 · 覆盖 3 切片
- "Baidu Maps doesn't have an English option, doesn't seem to have a Current Location feature" — `se-travel-china-language.txt`；"The problem: Chinese-only UI." — `mcg-best-chinese-apps-for-tourists.txt`

**P-D3｜地址体系与"最后 500 米"** · S2 · 覆盖 3 切片
- "Your GPS pin drifts onto a road directly above or below the one you are standing on."（重庆垂直城市）— `mcg-chongqing-vertical-city-navigation-guide.txt`
- 拼音地址司机读不懂（`mcg-xian-travel-guide.txt`）；同名站/码头陷阱："The ferry pier you can see from central Xiamen is not the one you are allowed to use." — `mcg-xiamen-gulangyu-ferry-pier-guide.txt`

**P-D4｜语言障碍贯穿全流程** · S1 · 覆盖 5 切片
- "I am American and speak practically no Chinese. Will I have any trouble going through customs in Beijing" — `se-travel-china-language.txt`
- "Do not assume a waiter or taxi driver speaks any English; they almost never do" — `mcg-china-survival-guide-for-tourists.txt`
- 行业口径：携程《中国入境游发展年度报告 2026》把**本地服务语言障碍、外语导览薄弱**列为影响体验的一大痛点（新华社转引，`xinhua-globe-inbound-2026.txt`）。

### E. 票务与预约

**P-E1｜现场无票、全实名在线预约** · S1 · 覆盖 3 切片
- "Jiuzhaigou sells no tickets at the gate. Not a reduced allocation, not a queue — zero." — `mcg-jiuzhaigou-valley-without-tour-group.txt`
- "The on-site manned ticket counters were **abolished — not reduced, removed**." — `mcg-datong-yungang-hanging-temple-booking-guide.txt`
- "no tickets are sold at the gate, entry is real-name with a face scan" — `mcg-chengdu-to-jiuzhaigou-without-tour.txt`

**P-E2｜预约前置三件套：微信小程序 + 护照实名 + 无现场窗口** · S1 · 覆盖 5 切片
- 链条：想预约 → 需要微信小程序 → 微信支付实名要 1–3 天 → 实名要护照照片上传 → 若行前没做，落地当天无法补。
- 数字服务的身份证/手机号前提："The official Chinese rail booking site requires a Chinese phone and a Chinese bank account for registration" — `mychinaguide-independent-travel.txt`
- 系统设计的错配："**passports break the form about half the time**" — `mcg-book-china-attraction-tickets-without-chinese-id.txt`
- "Everything that skips a queue now costs money and lives inside an app that opens in Chinese." — `mcg-shanghai-disney-fastpass-replacement-foreigners.txt`
- 行业口径（最有价值的一条外部确认）："不少热门博物馆、景区实行实名预约制，但**系统对护照识别支持不完善，护照尚无法通过所有自助闸机，只能求助人工通道**" — `jntimes-cyber-china-details.txt`
- 英文侧同类证据："facial recognition lanes are built for Chinese ID cards rather than foreign passports" — `tripcom-book-attractions.txt`
- 中文官方媒体口径："部分热门景区线上预约**仅支持中文界面，且需要使用身份证**……抵达易、入园难" — `xinhua-globe-inbound-2026.txt`

**P-E3｜放票时点固定 + 分钟级售罄** · S2 · 覆盖 4 切片
- "tickets often vanish within **5 to 10 minutes** of release" — `mcg-how-to-buy-forbidden-city-tickets.txt`
- "tickets sell out within minutes"；"book the second tickets go live, 15 days out, 6am Beijing time" — `mcg-china-high-speed-rail-guide.txt`
- "sold out on three in four of those trains four days ahead"（国庆/春节等）— `mcg-chengdu-to-jiuzhaigou-without-tour.txt`
- 官方兜底（多数游客不知道）：线下综合服务窗口可由工作人员代订，"No real-name appointment or phone number information is required in this case." — `bjfaq-t20250722_4155057.txt`

**P-E4｜闸机/取票只认实体护照，姓名必须逐字一致** · S2 · 覆盖 4 切片
- "Screenshots are not accepted, only the physical passport." — `mcg-china-high-speed-rail-guide.txt`
- "If the e-gate flags a mismatch, you're arguing with a station agent in a language you don't speak." — `mcg-china-high-speed-rail-guide.txt`

**P-E5｜黄牛与"保证有位"骗局、二手票作废** · S2 · 覆盖 2 切片
- "ignore touts at train stations offering 'cheap package tours' — they're rarely licensed." — `mcg-zhangjiajie-itinerary-guide-plan-perfect-trip.txt`

**P-E6｜官方渠道 vs 第三方渠道真伪难辨** · S2 · 覆盖 2 切片
- "We have not authorized any third-party ticket-purchasing platform to sell tickets."（官方答复）— `bjfaq-t20250722_4155052.txt`

### F. 交通

**P-F1｜出租车骗局与拒载** · S2 · 覆盖 5 切片
- "Airport and major station taxi drivers sometimes claim the meter is broken and offer a 'flat rate'" — `mcg-is-china-safe-tourists-2025.txt`
- "Negotiated flat-fare offers from drivers standing in the arrivals hall are usually scams" — `mcg-shanghai-pudong-airport-sim-card-after-10pm.txt`

**P-F2｜网约车（Didi）：注册、中文地址、上车点、司机打电话** · S2 · 覆盖 3 切片
- "since the driver can deny the route he doesn't like"（挑单）— `se-travel-china-taxi.txt`

**P-F3｜一城多站/同名站与跨境动线耗时被低估** · S2 · 覆盖 3 切片
- "Arrive 45 minutes before departure on weekdays and 60 minutes on weekends to absorb the double immigration queue." — `mcg-hong-kong-side-trips-guangzhou-shenzhen.txt`

**P-F4｜自驾不可行 & 末班车一次性机会** · S2 · 覆盖 2 切片
- "Your International Driving Permit is **worthless here**" — `mcg-west-sichuan-self-drive-guide.txt`（只有现场换临时许可一条路，部分路段禁止外国人自驾）
- "Miss the last shuttle out and you are walking 25 kilometers in the dark." — `mcg-chengdu-to-jiuzhaigou-without-tour.txt`

### G. 住宿

**P-G1｜涉外资质门槛：前台直接说"no foreigner"** · S1 · 覆盖 4 切片
- **真实案例**："The front desk clerk took one look at her Austrian passport, shook his head, and said 'no foreigner.'"（22:30、已预付、13 小时飞行后；最后换到 ¥1480/晚的酒店，原酒店三周后才退款）— `mcg-china-hotels-foreigners-check-in-guide.txt`
- **机制（关键，决定 SKILL 怎么写）**："So 'no foreigner' is almost never personal. It is a legal system problem. The hotel is not allowed to check you in because they literally cannot complete the registration... front-desk staff do not know how to enter passport data into the police system and would rather not risk the fine." — `mcg-china-solo-backpacker-15-day-guide.txt`
- **量化**："About 30% of three-star and budget Chinese hotels still refuse foreign guests, technically illegal since 2019" — `mcg-is-china-a-good-place-to-visit.txt`
- 现行解法：Trip.com 的 **"Foreigner-friendly" 筛选**（"it is a real, enforced category"）；选国际连锁；到店前先电话确认（"A licensed hotel will answer quickly and confidently"）。
- 外部印证（英文媒体）："hotels have turned them away **despite having confirmed bookings**"，且预订页不标注是否接待外国人 — `tempo-hotels-turn-away-foreigners.txt`

**P-G2｜非酒店住宿 24 小时内须自行到派出所登记** · S1 · 覆盖 3 切片
- "Skipping registration can bring a warning and a fine of up to ¥2,000" — `mcg-can-americans-enter-china-without-visa.txt`
- "I went around in the smaller hotels and asked if they could fill out a form." — `se-travel-china-hotel.txt`
- 官方侧："I am a foreigner and did not know about this until recently." — `bjfaq-t20250722_4155054.txt`

**P-G3｜长租被房东拒租 / 宣传与实物落差** · S3 · 覆盖 2 切片
- "Many individual Chinese landlords refuse to rent to foreigners purely because they do not want to visit the police station" — `mcg-yunnan-long-term-stay-guide-renting-travel-tips.txt`

### H. 餐饮与文化

**P-H1｜点单墙：中文菜单 + 扫码点单 + 服务员不会英语** · S2 · 覆盖 3 切片
- "In Shanghai, Beijing, Shenzhen, and most tier-1 cities, around 90% of restaurants now run on QR-code ordering with a mini-program inside WeChat or Alipay. The menu is on your phone, in Chinese, and the server is not coming over to help. **If you cannot read it, you cannot eat.**" — `mcg-how-to-read-chinese-restaurant-menu.txt`

**P-H2｜计价单位与隐性费用（账单被"炸掉"）** · S2 · 覆盖 3 切片
- "A menu shows ¥58 for a steamed fish, you order it, the fish comes out at 1.2 kg, and you pay ¥140." — `mcg-how-to-read-chinese-restaurant-menu.txt`
- 茶位费/服务费/假收款码；"Any restaurant in the Qianmen, Wangfujing, or Forbidden City exit zones that has touts outside the door is running at roughly 2.5x local prices." — `mcg-beijing-dumpling-restaurants-local-only.txt`

**P-H3｜饮食限制说不清（辣度/过敏/素食/清真）** · S2 · 覆盖 3 切片
- "'Micro spicy' (微辣) in Sichuan and Hunan is not 'micro' by Western standards." — `mcg-how-to-read-chinese-restaurant-menu.txt`
- "Pointing at a dish is not enough if allergies matter." — `mcg-chengdu-neighborhood-citywalk-food.txt`
- 行业口径（上海一线导游调研）：清真餐厅供给不足、餐具适配（筷子/刀叉）是"最低使用门槛"级别的痛点 — `thepaper-inbound-service-chain.txt`

**P-H4｜小费/AA/买单/餐桌禁忌/讲价尺度错位** · S3 · 覆盖 3 切片
- "Tipping is not expected in China. Do not tip. It genuinely confuses people." — `mcg-how-to-order-food-in-china-without-chinese.txt`
- "Never stick chopsticks upright in a bowl of rice. This mimics incense sticks at a funeral altar." — `mcg-chinese-etiquette-guide.txt`

**P-H5｜购物辨伪与逼单诈骗** · S2 · 覆盖 3 切片
- "This is the single biggest risk for foreign travelers in Yunnan tea shopping." — `mcg-yunnan-tea-guide-kunming-lijiang.txt`
- "The bill then arrives at ¥2,000–10,000 per person."（茶室/艺术学生/假和尚式诈骗）— `mcg-shanghai-travel-guide.txt`

### I. 健康、安全与合规

**P-I1｜突发医疗：先付费、保险不直付、诊室翻译失灵** · S1 · 覆盖 2 切片
- "most hospitals overseas do not accept US health insurance, and public hospitals in China ask foreigners to pay before treatment" — `mcg-can-americans-enter-china-without-visa.txt`
- "For non-emergency care, they will refuse to provide treatment without payment." — `mcg-what-to-do-if-you-get-sick-in-china-hospital-guide.txt`

**P-I2｜超期停留/非法居留处罚重** · S1 · 覆盖 2 切片
- "Overstaying without one is illegal stay, with fines of up to ¥10,000 or detention"；每日罚款、上限 ¥10,000、**1–5 年禁止入境** — `mcg-can-americans-travel-china-visa-free.txt`、`mcg-can-americans-enter-china-without-visa.txt`
- 补救路径：在到期前去当地公安出入境管理部门申请停留许可。

**P-I3｜签证流程本身（美国护照）** · S2 · 覆盖 2 切片
- 机票+酒店订单悖论："it said that I had to have my flight reservations and hotel bookings, and only then apply"；只能"先订可退、出签后退"，游客自认"像造假" — `se-travel-china-visa.txt`
- 照片规格："I tried CVS, Walgreens, Walmart. Nobody can do it."（48×33mm）— `se-travel-china-visa.txt`；"Using a US passport photo is the number one rejection reason I see." — `mcg-china-visa-photo-requirements.txt`
- 拒签成本："If your application is refused, you don't get the $140 back."（不退费、不给理由、不可申诉、须本人到场）— `mcg-china-visa-for-us-citizens-step-by-step.txt`

**P-I4｜入境与海关细节** · S3 · 覆盖 2 切片
- 健康申报 App、指纹自助通道对外国护照报错（"パスポートを読み込ませると「外国人専用です！」とメッセージが出てできませんでした" — `chiebukuro-pudong-fingerprint.txt`）
- 烟酒限额极严："Tobacco: 19 cigarettes. Not 200 like most of the world." — `mcg-hong-kong-entry-requirements-2026.txt`

**P-I5｜针对游客的本地化诈骗与安全摩擦** · S3 · 覆盖 4 切片
- 黑车、ATM"热心帮忙"、假 App/假押金、验证码钓鱼（`mcg-shanghai-pudong-airport-sim-card-after-10pm.txt` 等）
- 独行游客面临的围观注视与官方 Level 2 法律风险提示 — `mcg-solo-female-travel-china-safety.txt`

---

## 4.5 现行解法与失效点（答案侧）

以上痛点的"现行解法"来自两种语料：**指南站给读者的方案**（第 4 节各条的"现行解法与失效点"）与**Stack Exchange 回答者给提问者的方案**。后者更接近"真实用户在实践中试出来的办法"，单独成节。原始证据见 `research/findings/answers-workarounds.md`（623 条答案中 452 条与中国相关，引用 100+ 条）。

### 各主题最常用的做法与失效点

| 主题 | 最常用做法 | 失效点 |
|---|---|---|
| 移动支付 | 出发前用**可退的景点票**跑通 Alipay→外卡链路；被风控时在 App 内转人工客服，交护照资料页 + 手持照 | 自测测不到断网、银行 App 二次授权被墙、商户拒收；解封约 12 小时且 **App 不发通知**；"Some places just do not accept payments if you are using AliPay with a foreign credit card." |
| 现金/ATM/换汇 | 在银行换汇并**索取盖章回执**；机场只换一小笔（出租车不收卡） | ATM 手续费无法一般化（"ask your own bank"）；**本主题最新答案停留在 2020 年**，是全部主题里信息最陈旧的一块 |
| 上网 | 出发前注册并装好 VPN，**避开最热门品牌**（中国区 App Store 已下架）；用香港卡/境外漫游 | "you will not get any guarantee that it will work"；反向（中国卡出境漫游）确认仍经过防火墙 |
| 住宿与登记 | 酒店登记是**酒店的法定义务**（3 小时内报送公安）；民宿/朋友家 24 小时内由本人或房东去派出所 | 只有"有资质接待外国人"的酒店能出登记表，而**官方没有可查询名单** |
| 火车票 | 优先 12306 英文版；代理只用于抢不到票（加价不透明）；进站按机场式安检 + 实名核验预留时间 | 同一问题下两条高赞答案对"要不要留 1 小时"结论**相反** |
| 签证/TWOV | 用 Timatic 自查口岸、第三国认定与时长 | Timatic 自身有 "inconsistencies"；边检有裁量权；陆路出境票"除非朋友提前买好寄给你，good luck"；144h 时代必须同区域进出（北京→香港 G 车不合规） |
| 市内交通 | 微信绑外卡后用 **Didi 小程序（有英文界面）** 叫车付款；市区优先地铁 | 司机常直接打电话（语言不通无法接）；有假冒打表车的黑车且**无识别方法** |
| 语言与点餐 | 有图菜单指单；翻译 App 相机 + 离线包 + 写中文卡片 | 过敏原无法沟通（"no raw soy" 牌子很多餐厅也看不懂）；地图普遍偏移约 500 米 |
| 安全与诈骗 | 拒绝口岸/机场拉客黑车（5–10 倍价、改装计价器）；ATM 盗刷走"报警→案件回执→银行追回" | 骚扰/围观类最高赞答案是 "There is not much you can do" |

### 反复出现但都不彻底的「半解法」

| # | 半解法 | 为什么"不彻底" |
|---|---|---|
| 1 | **带现金兜底** | 从 2016 到 2025 反复出现，但**没有任何答案能说清哪些场景一定收现金**；实测者只是"恰好把 200 美元花掉了"。现金的真实作用是"覆盖落地到首次成功移动支付之间的空窗"。 |
| 2 | **提前装好 VPN** | 同批答案自己拆台：最热门品牌最容易被封；Apple 中国区已下架；GFW 探测可延后；自建节点速度差；叠加 VPN 后延迟更高。 |
| 3 | **用香港卡/境外漫游绕过防火墙** | 两位答主都说"能"，但同一位同时说"没有任何供应商会保证"；反向（中国 SIM 出境漫游）确认仍会经过防火墙。 |
| 4 | **让酒店前台/工作人员帮忙** | 把成功条件押在"酒店愿意帮 + 会英语"上；在找代售点、填登记表、找 ATM 三个场景都是同一招，且都没有兜底。 |
| 5 | **借别人的中国手机号** | 答案自己标注为 loophole / 借用他人身份——"能过但现在不合法/不合规"。 |
| 6 | **出发前自测（用可退票试支付）** | 被采纳且高分，但同页答案逐条证明它测不到三类真实失败：断网、银行二次授权被墙、商户拒收。 |
| 7 | **"先订可取消的酒店/机票"（为签证）** | 满足签证材料硬要求，属合规灰区，答案也只是转述代理原话。 |
| 8 | **改乘"更贵的正规服务"**（商务座/VIP 通道/带司机包车/改直飞） | 用钱换确定性——对预算敏感的用户等于没有解法。 |

**这一栏对 SKILL 的直接含义**：游客已有的解法**不是"不知道"，而是"知道一个半可靠的招，并且没有兜底"**。因此 SKILL 的价值不是科普"要装 VPN / 要带现金"，而是给出**判定条件（什么情况下这招会失效）+ 分级兜底（第一方案失败后的第二、第三步）**。

### 答案自己承认"无解 / 不可判断"的地方

这些是**最容易被 SKILL 做出差异化的位置**，因为社区里根本没有可信答案：

1. **支付**：外卡绑 Alipay 后仍有商户拒收，2025 年答案只能给出"那就用现金"；地铁交通卡对外国手机号直接关闭。
2. **风控解封**：没有自助路径，只能人工客服 + 交护照照，且 App 无通知。
3. **SIM 卡长期方案**：唯一答案开头就是 "None"，并列三个死结。
4. **公共 Wi-Fi**：需要中国手机号，答案自称 loophole。
5. **地图**：500 米偏移是政府限制，备份方案的作者自己也不确定是否准确；POI 错误只能靠问当地人（Google 把玉器店标成"博物馆"）。
6. **过敏**：写着 "no raw soy" 的牌子很多餐厅也看不懂。
7. **TWOV 陆路出境票**："除非有朋友提前帮你买好寄给你，good luck"。
8. **免签入境次数**：官方说无限制，但边检保留"认定滥用"的裁量权。
9. **找能接待外国人的住宿**：**没有官方可查询名单**，只能靠第三方清单。
10. **火车进站预留时间**：同一问题下两条高赞答案给出相反结论，读者只能自行判断。

### 一处重要的供给侧发现

答案侧的**知识滞后区**恰好是痛点最集中的地方：**eSIM 与中国本地 SIM 的产品级建议、涉外酒店的可查询名单、240 小时过境免签（2024 年后政策）在 Stack Exchange 这一批数据里几乎没有被答案覆盖**。这意味着：

- 现有社区答案在这些主题上**要么缺失、要么过时**（例如仍有大量 72/144 小时旧规则的答案）；
- 这正是 TripPal SKILL 相对"通用旅行社区知识"的**差异化信息点**：把**当前有效的政策事实 + 可执行路径**做成结构化、带有效期的知识，而不是让 agent 去复制过时的社区共识。

---

## 5. 依赖链：为什么"行前"决定一切

这批语料里最有产品价值的结构性发现，是**多条痛点并不独立，而是串成一条"越晚做越做不成"的链条**：

```
签证/免签资格判定 (P-A4, P-I3)
        │  决定"能不能来、能待多久"
        ▼
行前网络准备：eSIM / VPN / 离线地图装机 (P-A1, P-A2)
        │  落地后应用商店与 VPN 官网不可达 → 无法补救
        ▼
支付开通：Alipay / WeChat 实名 + 绑外卡 (P-A3, P-B1~B3)
        │  实名审核 1–3 天；未成年人无解
        ▼
微信生态可用（小程序 + 实名）(P-E2)
        │  景点预约、点餐、乘车码、共享单车都挂在这里
        ▼
预约与购票：景点 / 火车 / 演出 (P-E1~E4)
        │  放票时点固定、分钟级售罄；闸机只认实体护照
        ▼
现场执行：导航 / 打车 / 点餐 / 住宿登记 (P-D1~D4, P-F1~F4, P-G1~G2, P-H1~H3)
```

**"落地后无法补救"清单（S1 且无现场替代）**：
1. 没装 VPN / eSIM → 信息与通讯能力归零（P-A1、P-A2）
2. 没做支付实名/绑卡 → 只能靠现金，而现金正在退化（P-A3、P-B2、P-B4）
3. 没有可收验证码的号码 → 注册、WiFi、预约全线卡死（P-C2）
4. 没提前预约热门景区 → 现场无票（P-E1）
5. 订到无涉外接待能力的酒店 → 深夜被拒（P-G1）
6. 240 小时算错/去错口岸/去了西藏 → 入境被拒或超期（P-A4、P-I2）

---

## 6. 场景切分建议（供下一阶段"无 skill agent 试跑"用）

建议按**用户决策时点**而不是按功能模块切场景，这样每个场景都有明确的"输入 → 输出 → 成功判据"，便于观察 agent 在无 SKILL 时的失败模式。

| ID | 场景 | 触发时点 | 用户目标 | 关键输入 | 成功判据 | 对应痛点 |
|---|---|---|---|---|---|---|
| S01 | 免签/签证资格判定与行程合规 | 订票前 | 确认"我这条路线要不要签证、能待几天、能从哪进" | 国籍、出发地、联程航段、停留城市与天数 | 给出合规判定 + 需要准备的材料清单 + 不合格时的替代方案 | P-A4, P-I3 |
| S02 | 行前 72 小时"落地可生存"准备 | 出发前 3 天 | 落地即可用网、可用钱、可导航 | 手机型号、运营商、银行卡、行程城市 | 一份可勾选的装机/开通清单（含"落地后无法补救"项） | P-A1~A3, P-C1 |
| S03 | 移动支付开通与失败兜底 | 出发前至落地 | 把钱变成可用的扫码支付；失败时知道下一步 | 卡种、护照、是否有本地号 | 开通路径 + 风控/失败的分级兜底（第二张卡 / 现金 / 线下窗口） | P-B1~B6 |
| S04 | 景区/博物馆预约与抢票 | 放票前 | 约到热门景点 | 护照信息、微信状态、放票时点、证件要求 | 拿到预约 + 实体护照/闸机注意事项 + 未抢到的替代 | P-E1~E6 |
| S05 | 城际交通（火车/高铁/跨境） | 出行前 1–15 天 | 买到票并顺利进站换乘 | 出发到达站、证件、姓名拼写、口岸 | 正确车站 + 取票/进站方式 + 时间缓冲 | P-E3, P-E4, P-F3 |
| S06 | 市内交通与打车 | 现场 | 从 A 到 B 且不被宰 | 中文地址、附近地标、支付方式 | 叫车方案 + 中文地址卡 + 黑车识别与拒付话术 | P-D3, P-F1, P-F2 |
| S07 | 住宿筛选与入住登记 | 订房前 / 入住时 | 订到能接待外国人的住处并完成登记 | 城市、预算、住宿类型（酒店/民宿/朋友家） | 涉外友好筛选 + 登记义务提示 + 被拒时的处理路径 | P-G1~G3 |
| S08 | 上网与信息获取 | 出发前 / 落地 | 有可用网络 + 可用的信息源 | 手机、停留时长、是否需要本地号码 | 连接方案（eSIM/漫游/本地卡）+ 哪些服务不可用 + 离线替代 | P-A2, P-C1~C4 |
| S09 | 导航与地址沟通 | 现场 | 不迷路、能和司机对上 | 目的地中文名、坐标、地标 | 本地地图用法 + 校正后的定位 + 中文地址/截图 | P-D1~D3 |
| S10 | 点餐与饮食限制 | 现场 | 点对菜、不踩过敏/禁忌坑 | 饮食限制、预算、就餐场景 | 中文点单卡（含辣度/过敏/素食/清真）+ 计价单位提示 | P-H1~H3 |
| S11 | 应急处理 | 突发 | 生病/被拒/丢证件/超期时知道找谁 | 事件类型、所在城市、证件状态 | 分级处置路径（谁、在哪、要带什么、中文话术） | P-I1, P-I2, P-I4, P-I5 |
| S12 | 全流程语言支持 | 贯穿 | 不因语言失去选择权 | 场景、对方对象（司机/服务员/柜台） | 可直接出示的双语文字卡 + 翻译降级方案 | P-D4, P-C3, P-H1 |

**建议的首批试跑顺序**（痛点最集中、失败模式最容易观察）：
`S02 → S03 → S04 → S01 → S07 → S06`
理由：S02/S03/S04 命中率最高且"错一步全盘卡"；S01 决定可行性；S07/S06 是现场高频失败点。

---

## 7. 给下一阶段（无 SKILL agent 试跑 + work_log.md）的建议

**work_log.md 建议记录字段**（便于后续从轨迹反推 SKILL 内容）：

| 字段 | 为什么需要 |
|---|---|
| 场景 ID + 用户画像（国籍/语言/同行人/预算） | 同一场景不同画像的失败模式不同（如美国护照 vs 50 国免签） |
| 每一步的动作与**信息依据**（查了哪、引了什么） | 区分"不知道"和"知道但找不到" |
| 卡点类型：**信息缺失 / 工具不可达 / 语言 / 支付 / 身份核验** | 直接对应 SKILL 该补的是知识还是流程还是话术 |
| 失败与返工次数、耗时 | 作为 SKILL 收益的基线 |
| agent 自造的**替代路径**（绕过手段） | 这些是最可能沉淀成 SKILL 步骤的候选 |
| 事实性错误（幻觉的规则/价格/时点） | 反推 SKILL 必须内置的权威事实清单 |

**从轨迹反推 SKILL 内容的判据**：
1. 若卡点是**事实类**（240 小时怎么算、放票几天前）→ SKILL 需要**权威事实卡 + 政策有效期标注**。
2. 若卡点是**流程类**（微信实名 → 预约 → 闸机）→ SKILL 需要**可勾选的步骤编排 + 前置依赖提示**。
3. 若卡点是**不可达类**（应用商店、VPN 官网被墙）→ SKILL 必须**前置到行前阶段**，落地后再讲无用。
4. 若卡点是**沟通类**（点餐/司机/柜台）→ SKILL 需要**可直接出示的中文卡片**（而非语音或长篇解释）。
5. 若 agent 反复"假装知道"→ SKILL 需要**明确的"我不知道，应该去 X 查"降级协议**。
6. 若 agent 给出的只是"半解法"却不给失效条件（见 4.5 节）→ SKILL 要为每个方案标注**适用条件 + 兜底层级**（第一/第二/第三方案）。

**要防止 SKILL 退化成"复述常识"**：4.5 节列出的半解法（带现金、装 VPN、找酒店前台帮忙）已是公开常识；TripPal 的增量必须落在**判定条件 + 分级兜底 + 政策时效性**上，否则无法与通用旅行社区知识拉开差距。

---

## 8. 证据强度与局限

**强证据**（多切片独立出现 + 有官方或行业数据交叉验证）：
- 支付摩擦（P-B1~B4）：Stack Exchange、指南站、北京 12345 官方问答、TTG China 行业调研、新华社转引携程报告五方一致；且有量化数字（外卡受理率 <45%、手续费 2.5–3.5%）。
- 网络/防火墙（P-A1、P-C1）：4 个切片独立；Google Trends 里 `vpn china`/`great firewall`/`china esim` 与 `china travel` 同量级。
- 预约制与护照识别（P-E1、P-E2）：指南站 + 官方问答 + 中文行业媒体（"护照尚无法通过所有自助闸机"）三方一致。
- 住宿涉外门槛（P-G1）：有具体案例、机制解释与量化估计。

**弱证据 / 需谨慎**：
- 单一来源口径的数字（如"约 30% 三星及以下酒店拒接"、"90% 餐厅扫码点单"、"VPN 2026 年 4 月起不可靠"）都来自 `mychina.guide` 的单一指南站，**未经第二来源验证**，报告中已按来源标注，落地前建议复核。
- 出租车痛点总量偏小（SE 切片里多为机场接驳而非市内打车）。
- 厕所/卫生类证据稀少（仅 8 个文件提及）。

**结构性局限（会影响结论的偏向）**：
0. **两个来源在采集后被剔除**（不计入上述结论，也不计入语料统计）：`britacom-living-in-china-guide.txt` 实为 PDF 二进制转储、无可引用自然语言；`cnr-inbound-blockpoints.txt` 与 `cnr-shanghai-selfservice.txt` 正文是不可逆乱码（"锟斤拷"级双重编码），无法提取可信引文。三者已移入 `research/raw/_excluded/`。
1. **缺少游客即时吐槽类语料**：Reddit（r/travelchina 等）被网络层封锁（浏览器 headless 与普通 UA 均 403）、TripAdvisor 论坛返回空页、Quora 与 DuckDuckGo/Bing 触发人机验证、r.jina.ai 需 key。因此"情绪强度"可能被低估。
2. **Stack Exchange 侧以问题为主**（本次已补抓 7 批答案用于第 4.5 节），提问者以英语、技术型用户为主，**非英语国家游客（韩、日、东南亚、欧洲小语种）声音明显不足**——语料里只有少量日文（Yahoo 知恵袋）与中文行业视角。且答案侧存在**知识滞后**：eSIM/本地 SIM 产品建议、涉外酒店可查名单、2024 年后的 240 小时政策几乎没有被覆盖（详见 4.5 节末尾）。
3. **内容供给方偏见**：199/300 的来源文件来自 `mychina.guide`（一个为自家 App 导流的英文指南站），其选题=他们判断的读者困难点，存在商业动机；`trip.com` 指南同理。
4. **检索式语料不是全量语料**：Stack Exchange 切片来自关键词检索，切片内计数不能外推为真实频次。
5. Google Trends 为美国区、英文词条，未覆盖其他客源国（本次未能采到，因 widgetdata 接口限流）。

**建议的下一步补证**（若需要更硬的结论）：
- 用可用的出口绕过 Reddit 封锁（换网络出口或使用带 key 的第三方读取服务）补 r/travelchina 语料——这是当前最大的语料缺口；
- 补采非英语客源国的一手声音（韩国 Naver、日本 5ch/知恵袋、德语/法语论坛），验证"英语技术用户视角"是否覆盖了真实分布；
- 对单一来源的量化断言（30% 酒店拒接、90% 扫码点单、外卡受理率 45%）做二次核实；
- 把第 4.5 节的"无解清单"当作 SKILL 的**事实核查清单**：每一条都要有权威来源与有效期，否则 agent 会重复社区里的过时答案。
