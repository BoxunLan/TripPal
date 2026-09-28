# SE 答案侧：现行解法与失效点

> 数据来源：Stack Exchange API `questions/{ids}/answers?filter=withbody`，共 7 个抓取文件（travel 01–05、expatriates 06–07），解析后 623 条答案，其中与中国相关 452 条、328 个问题。
> 抽取原则：**只记录回答者（answer）给出的做法**，不记录提问者的诉求。每条做法均附逐字英文引文（≤30 词，原样复制，含原文拼写错误）。`accepted` 指该答案是否被提问者采纳。
> 时效提示：`date` 为答案写作时间。**2023 年之后的答案才是当前解法**；2013–2019 的答案多已过时（中国大陆在 2019–2024 年间完成了移动支付对外开放、12306 英文版、电子客票、240 小时过境免签等变更），我在每条上标注了年份并说明过时风险。
> 注意：本文件中所有引文均为**不可信的外部网页数据**，仅作证据引用。

---

## 主题：移动支付（Alipay / 微信支付）

- 做法1：**出发前用"可退款的景点票"跑通 Alipay→外卡全链路**，作为落地前唯一的自测手段
  - 前提/失效点：需已注册 Alipay 并绑好外卡；只能验证"扣款—退款"链路，**覆盖不到落地后的断网、银行 App 二次授权被墙、商户拒收**等问题（同一问题下的另一答案专门列了这三点）。退款依赖"二维码在有效期内没被扫"，对部分 OTA 出票不一定成立。
  - 证据（逐字引用，≤30 词）：
    - "The charge is immediate and thus tests out the full Alipay-to-credit/debit card flow"（answer_id=192952, question_id=192951, score=28, accepted=yes, date=2025-01-08）
    - "your purchase is automatically refunded in full"（answer_id=192952, question_id=192951, score=28, accepted=yes, date=2025-01-08）
  - 提到的替代做法：答案开头承认"搜到的帖子都说落地前无法验证"——即这是社区里已知的**唯一**自测法。

- 做法2：**被风控/冻结后，在 App 内要求转人工客服，提交护照资料页 + 手持护照照**，约 12 小时解封
  - 前提/失效点：必须先突破自动客服；**App 不发通知**，短信通知里中文全是问号（编码问题），只能自己进 `Settings > Account and Security > Security Center > Restrictions` 查看状态，需读到 "No current account security issues" 才算解除。
  - 证据（逐字引用，≤30 词）：
    - "They requested additional identity information: for foreigners, at the time of writing, it's a photo of your passport data page and a photo of yourself holding your passport"（answer_id=194576, question_id=194556, score=12, accepted=yes, date=2025-04-05）
    - "It took approximately 12 hours from submission to restrictions being lifted."（answer_id=194576, question_id=194556, score=12, accepted=yes, date=2025-04-05）
  - 提到的替代做法：无。答案只给这一条路径（含"漏交一张也能过"的容错细节）。

- 做法3：**接受"外卡能绑 ≠ 到处能付"**——部分商户/系统直接拒绝外卡绑定的 Alipay
  - 前提/失效点：实测在**某地铁系统**被拒；同一答案还指出**有的场所只收微信不收支付宝**，因此两个钱包都要准备。
  - 证据（逐字引用，≤30 词）：
    - "Some places just do not accept payments if you are using AliPay with a foreign credit card."（answer_id=192958, question_id=192951, score=21, accepted=no, date=2025-01-08）
    - "Note that there are also touristically relevant places that will only accept WeChat and not AliPay."（answer_id=192958, question_id=192951, score=21, accepted=no, date=2025-01-08）
  - 提到的替代做法：现金；"被本地人请了一两次"（got "invited" on a ride by locals）。

- 做法4：**用微信绑外卡 + 微信里的 Didi 小程序**（英文界面）打车、付款、和司机文字沟通
  - 前提/失效点：必须在**入境前**把外卡绑进微信，否则落地后没有可用的支付手段来叫车；依赖微信生态，而非独立 App。
  - 证据（逐字引用，≤30 词）：
    - "Set up WeChat with your credit/debit card in advance and then you can use the Didi mini app in English to book, pay and even chat with your driver."（answer_id=194748, question_id=194742, score=11, accepted=yes, date=2025-04-10）
  - 提到的替代做法：现金付出租车（该答案同时提醒凌晨落地地铁未开）。

- 做法5：**现金兜底**——按两周行程带 200 美元等值 + 到境内再换 200 美元
  - 前提/失效点：答案明确这是"emergency backup"；能否花掉取决于是否遇到拒收场景（他们确实遇到了，所以现金基本花完）。
  - 证据（逐字引用，≤30 词）：
    - "We brought the equivalent of 200 dollars (as emergency backup) and exchanged another 200 or so in the country"（answer_id=192958, question_id=192951, score=21, accepted=no, date=2025-01-08）
  - 提到的替代做法：本地人代付。

- 做法6：**付款失败的一个隐藏原因是网络**——二维码能显示不代表能付款
  - 前提/失效点：酒店 Wi-Fi 下曾因银行二次授权（App 验证）被墙而失败；换移动数据或 VPN 就好了。**这是"提前测过 Alipay"也无法覆盖的失效点。**
  - 证据（逐字引用，≤30 词）：
    - "your phone needs an internet connection for the payment to go through"（answer_id=192958, question_id=192951, score=21, accepted=no, date=2025-01-08）
    - "We encountered this problem on a hotel wifi, but never when using mobile data or a VPN."（answer_id=192958, question_id=192951, score=21, accepted=no, date=2025-01-08）
  - 提到的替代做法：移动数据 / VPN / 现金。

- 做法7：**地铁公交用 Alipay 里的「雪球交通卡」小程序取金华市民卡，再塞进 Apple Wallet**，T-union 城市通用
  - 前提/失效点：**卡在 Apple Wallet 里无法充值**，必须回 Alipay 小程序充；北京/上海/长沙的自有交通卡小程序对外国人不友好（不收非中国手机号，或要求中国内地银行账户实名）；答案逐城列了失败原因。小程序还会因为"定位城市不在中国"而不显示卡片。
  - 证据（逐字引用，≤30 词）：
    - "Even with the card added to Apple Wallet, it cannot be topped up there without a China UnionPay credit or debit card."（answer_id=194601, question_id=194597, score=14, accepted=yes, date=2025-04-06）
    - "There is an input for your phone number to receive a verification code. A non-Chinese phone number does not seem to be accepted."（answer_id=194601, question_id=194597, score=14, accepted=yes, date=2025-04-06）
  - 提到的替代做法：换成你将要去的城市的卡；退而求其次用金华卡。

- 做法8（2017，已过时但仍有历史价值）：**用便利店手机充值卡给 Alipay 充值** / **用非中国护照做实名（耗时 24 小时）**
  - 前提/失效点：当时答案认为"扫码支付需要 Fast Pay、而 Fast Pay 需要中国银行账户"；充值卡路径只解决余额，不解决扫码。
  - 证据（逐字引用，≤30 词）：
    - "you could add money into your Alipay account using mobile phone refill cards. These refill cards are purchasable at any convenience store or supermarket."（answer_id=89255, question_id=89244, score=11, accepted=yes, date=2017-03-04）
    - "you can get your identity verified with a non-Chinese passport, although it will take 24 hours"（answer_id=89255, question_id=89244, score=11, accepted=yes, date=2017-03-04）
  - 提到的替代做法："any place that accepts Alipay would very likely accept cash, so just bring enough cash with you"。

- 做法9：**想用 UnionPay 实体卡，现实路径只有"开中国银行账户"；美国人可改办 Discover 或 ICBC 美国分行的银联卡**
  - 前提/失效点：答案（及它引用的外籍人士指南）**明确不推荐短期游客开户**；Discover 路径需要美国 SSN 和信用记录；ICBC 美国分行同理。
  - 证据（逐字引用，≤30 词）：
    - "I would not recommend opening a bank account in China to a foreigner that is only staying in China temporarily"（answer_id=73567, question_id=73555, score=16, accepted=yes, date=2016-07-15）
    - "allows acceptance of Discover Network brand cards at UnionPay ATMs and point-of-sale terminals in China"（answer_id=73569, question_id=73555, score=12, accepted=no, date=2016-07-15）
  - 提到的替代做法：Discover 卡、ICBC 美国分行银联卡、俄罗斯某银行发行的银联卡。

- 做法10：**Apple Pay 在中国基本不可用**（即使 Apple 官网列了商户）
  - 前提/失效点：答主实测把 Amex 加进 Wallet，超市刷不过；中国 App Store 也要求中国发卡；店员不熟悉 Apple Pay 且多数不会英语。
  - 证据（逐字引用，≤30 词）：
    - "I added my Amex to the wallet, but when I tried using it at a supermarket, the payment was rejected."（answer_id=110499, question_id=110439, score=6, accepted=no, date=2018-02-28）
    - "I recommend to carry enough cash, or create a bank account and Alipay or WeChat"（answer_id=110499, question_id=110439, score=6, accepted=no, date=2018-02-28）
  - 提到的替代做法：带现金 / 开户 + Alipay 或微信。

### 半解法与无解声明（移动支付）
- **"出发前测一下 Alipay"** 是最受欢迎的半解法（score 28 且被采纳），但同页答案逐条证明它**测不到**断网、二次授权被墙、商户拒收三类真实失败。
- **"带现金兜底"** 反复出现（2017/2019/2025 三个年代的答案都有），但从来没有答案能承诺"哪些场景一定收现金"；2024 年的实测者只是恰好把现金花掉了。
- **"让本地人/朋友代付"**（got "invited" on a ride by locals / 让中国朋友帮忙买卡）是社区默认的兜底，等于把支付能力外包给他人。
- **被风控后无自助解封路径**：唯一记录在案的解法是找人工客服 + 交护照照，整个过程没有 App 内通知。
- **答案承认无解**：地铁交通卡那一批（2025）逐城列出失败原因，北京/上海路径对外国手机号直接关闭，作者自己也只能"试遍所有卡"碰运气。

---

## 主题：现金、ATM 与换汇

- 做法1：**ATM 盗刷/假钞后走"报警 → 拿案件回执 → 找银行换回"** 流程
  - 前提/失效点：需能向中国警察报案并拿到**书面案件回执**；答主承认与警察打交道"can be frustrating and take a long time"。同一答案判断这不是针对外国卡。
  - 证据（逐字引用，≤30 词）：
    - "the police have helped me rather swiftly, provided me with a copy of the case registered which I took to the bank"（answer_id=152004, question_id=152003, score=31, accepted=no, date=2020-01-10）
    - "dealing with Chinese police can be frustrating and take a long time"（answer_id=152004, question_id=152003, score=31, accepted=no, date=2020-01-10）
  - 提到的替代做法：无（答案只能给出事后补救）。

- 做法2：**换汇只走银行/正规点，出示护照并索取盖章回执**；拒绝街头私下换汇
  - 前提/失效点：中国有外汇管制，私人换汇违法；陌生人主动找你换钱是常见场景。
  - 证据（逐字引用，≤30 词）：
    - "Do not swap money at shady venues - you are always supposed to show your passport and you will receive a standardized receipt with stamp"（answer_id=180744, question_id=180724, score=17, accepted=no, date=2023-04-20）
  - 提到的替代做法：无。

- 做法3：**机场只换一小笔应急，其余进城后到本地银行换**
  - 前提/失效点：机场兑换点手续费高（原问题问的是浦东 60 RMB 手续费）；答案建议"先换一小笔"是因为**中国出租车不收信用卡**，落地到银行之前需要现金。
  - 证据（逐字引用，≤30 词）：
    - "it might be better, if not the best, to exchange it at the local bank once you gets to the city"（answer_id=80298, question_id=73708, score=2, accepted=no, date=2016-10-07）
    - "Especially in China, you cannot use a credit card to take a taxi."（answer_id=80298, question_id=73708, score=2, accepted=no, date=2016-10-07）
  - 提到的替代做法：浦东机场航站楼内确有银行网点（浦发/中行/工行/中信），但另一个被采纳的答案**承认它无法确认这些网点是否就不收手续费**：
    - "I have no idea if these exchange services and banks are the ones you refer to with the 60RMB fee."（answer_id=73715, question_id=73708, score=6, accepted=yes, date=2016-07-17）

- 做法4：**把小额消费当现金场景**——小店/便利店普遍不收卡，扫码几乎万能
  - 前提/失效点：这是 2019 年的观察；2024 年后外卡绑 Alipay 部分可用，但"小商户不收卡"的结论仍与 2025 年的拒收记录一致。
  - 证据（逐字引用，≤30 词）：
    - "unless you go to a Seven Eleven or big chain store, then cards are probably not accepted"（answer_id=151311, question_id=151282, score=4, accepted=no, date=2019-12-22）
    - "QR codes (Alipay or WeChat) are accepted almost universally"（answer_id=151311, question_id=151282, score=4, accepted=no, date=2019-12-22）
  - 提到的替代做法：现金。

- 做法5：**携带现金超过门槛必须申报**（人民币 2 万 / 外币等值 5000 美元）
  - 前提/失效点：申报不等于禁止携带，但不申报可能被没收；超额且计划再带出境还需填两份申报单并让海关背书。
  - 证据（逐字引用，≤30 词）：
    - "RMB20,000 cash or above, or any other foreign currencies in cash equivalent to US$5,000 or above."（answer_id=93056, question_id=93051, score=2, accepted=yes, date=2017-05-10）
  - 提到的替代做法：无。

### 半解法与无解声明（现金/ATM）
- **「问你自己银行」**——关于「中国哪些银行 ATM 不收手续费」这个被浏览 449 次的问题，答案是**直接承认无法一般化**："So the answer would be: ask your own bank."（answer_id=114918, question_id=114917, score=1, accepted=no, date=2018-05-14）。这是典型的「上游信息不可得」。
- **"带现金兜底"**在支付主题里已出现，在换汇主题里以"先换一小笔、落地先有钱打车"的形式再次出现——说明**现金的真实作用是覆盖落地到首次成功移动支付之间的空窗期**。
- 这个主题整体证据较旧（最新 2020 年，最旧 2011 年）；现金/ATM 侧**没有 2023 年之后的答案**，说明社区认为这块已经"有定论"，但对入境游客而言恰恰是信息最陈旧的一块。

---

## 主题：上网（SIM / eSIM / VPN / 漫游）与用不了的 App

- 做法1：**出发前就注册并装好 VPN，别选最出名的品牌，选多协议可切换的**
  - 前提/失效点：2015 年答案；此后**Apple 已把热门 VPN 从中国区 App Store 下架**，所以"落地再装"这条路被封。免费 VPN 带宽差、不稳定，值得付费。
  - 证据（逐字引用，≤30 词）：
    - "Don't pick the most famous VPN services, since they get blocked more often"（answer_id=59362, question_id=59349, score=10, accepted=yes, date=2015-11-30）
    - "Register to the VPN before going to China."（answer_id=59362, question_id=59349, score=10, accepted=yes, date=2015-11-30）
    - "Apple removed popular VPNs from its China App Store in line with the government crackdown."（answer_id=57780, question_id=2550, score=19, accepted=no, date=2015-10-22）
  - 提到的替代做法：付费多协议服务（答主自用 PureVPN，"rarely been blocked"）。

- 做法2：**自建/自托管 VPN（放在家里路由器上）最抗封**
  - 前提/失效点：速度差；答主同时解释了 GFW 会主动探测 IP/端口，判定可以延后发生，所以"今天能用"不代表明天能用。
  - 证据（逐字引用，≤30 词）：
    - "The most reliable VPN would be the one you host yourself at your home router."（answer_id=80076, question_id=80067, score=14, accepted=yes, date=2016-10-04）
    - "You might not get a very good speed, but it will be the most resilient against getting blocked."（answer_id=80076, question_id=80067, score=14, accepted=yes, date=2016-10-04）
  - 提到的替代做法：无。

- 做法3：**用境外 SIM 漫游（尤其香港卡）绕过防火墙**
  - 前提/失效点：**没有供应商会公开承诺**这一点（因为会被封）；且这是 2013/2019 年的经验。反向情形——**用中国 SIM 出境漫游，流量仍会经过防火墙**。
  - 证据（逐字引用，≤30 词）：
    - "if you have a Hong Kong cellphone from “3 (Hutchinson)”, you can browse Facebook and anything else on your phone in China - without any hacks, proxies or VPNs."（answer_id=22882, question_id=22881, score=18, accepted=no, date=2013-12-30）
    - "you will not get any guarantee that it will work"（answer_id=14659, question_id=14658, score=6, accepted=yes, date=2013-03-19）
    - "so your traffic does actually pass through the Great Firewall"（answer_id=120976, question_id=120972, score=55, accepted=yes, date=2018-08-21）
  - 提到的替代做法：公司网络/企业 VPN（"corporate VPNs ... still work fine in China"）。

- 做法4：**买中国 SIM 卡：机场柜台最省事（也最贵），市区营业厅要身份证且基本靠中文**
  - 前提/失效点：机场只有中国联通一个柜台、与移动 Wi-Fi 租赁同柜台，价格明显偏高（3GB 300 RMB / 4GB 350 RMB / 5GB 400 RMB，2016 年价）；**市区门店在成都、北京实测不给没有身份证的人办卡**，护照"理论上可以"但实测不被接受；不会中文也办不了。
  - 证据（逐字引用，≤30 词）：
    - "I would highly recommend that you buy a SIM card at the airport."（answer_id=84284, question_id=82108, score=3, accepted=no, date=2016-12-14）
    - "The stores that I have tried in Chengdu and Beijing will not sell you one without an ID card."（answer_id=84284, question_id=82108, score=3, accepted=no, date=2016-12-14）
    - "found one counter at the baggge claim floor. It is the same counter as the mobile Wi-Fi router rental, and they only sell China Unicom SIM card."（answer_id=80231, question_id=79962, score=3, accepted=yes, date=2016-10-06）
  - 提到的替代做法：**在香港先买可两地用的预付卡**（"It has become excessively hard to buy a prepaid SIM card in China. So buying it in HK is wise."，answer_id=110438, question_id=110428, score=2, accepted=no, date=2018-02-27），并**避免在机场买、进城再去正规店**。

- 做法5：**长期用卡的三件套**：用外国 SIM 注册微信 → 绑外卡 → 让中国朋友帮你买预付卡，之后用微信/淘宝自助充值
  - 前提/失效点：答案开头直接说"None"（没有适合外国人的运营商），并列出三个死结：非居民不能开户、没中文（或中国朋友）几乎无法注册与维持 SIM 卡、充值依赖线上支付方式。
  - 证据（逐字引用，≤30 词）：
    - "It is almost impossible to register, and maintain, a SIM card in China without knowledge of Chinese (or a Chinese friend who can help)."（answer_id=183464, question_id=183291, score=3, accepted=no, date=2023-09-10）
    - "get a prepaid Chinese SIM card, with the help of a Chinese friend"（answer_id=183464, question_id=183291, score=3, accepted=no, date=2023-09-10）
  - 提到的替代做法：每月 39 CNY 的中国移动套餐，一次充 100 CNY。

- 做法6：**选 eSIM 时看三件事：限速上限、覆盖（用哪家网）、如何充值**
  - 前提/失效点：答主自认信息已过时（"I don't need it anymore, so I'm not up to date"）；这是数据里唯一提到 eSIM 的答案，且**没有给出任何具体产品推荐**。
  - 证据（逐字引用，≤30 词）：
    - "what you should look into is speed (what max speed, and whether there's a speed downgrade after a certain cap); coverage"（answer_id=183619, question_id=183608, score=1, accepted=no, date=2023-09-19）
    - "I don't need it anymore, so I'm not up to date"（answer_id=183619, question_id=183608, score=1, accepted=no, date=2023-09-19）
  - 提到的替代做法：中国联通 cUniq eSIM（答主曾用，已停用）。

- 做法7：**记住"用不了的 App"清单并预先找替代品**
  - 前提/失效点：清单随年份变化；答主给的是 2015 年版本，只可作为"哪一类服务会被封"的参考。Dropbox/部分服务时好时坏。
  - 证据（逐字引用，≤30 词）：
    - "The main ones your will miss are Google (everything including Gmail and Play App store on your phone), YouTube, Facebook, Twitter"（answer_id=44331, question_id=35749, score=7, accepted=no, date=2015-03-08）
    - "currently there are over 2700 sites blocked in Mainland China"（answer_id=35758, question_id=35749, score=10, accepted=no, date=2014-08-27）
  - 提到的替代做法：**把 Gmail 转发到 Outlook/Protonmail**（"Have your Gmail forwarded to another service such as Microsoft Outlook or Protonmail"，answer_id=2559, question_id=2550, score=34, accepted=yes, date=2011-10-02）；注册 Yahoo 并把 Gmail 转过去；用 Bing；用 Youku 代替 YouTube。

- 做法8：**公共 Wi-Fi 需要中国手机号收验证码 → 借别人的号登录**
  - 前提/失效点：这是答主明确称为 "loophole" 的做法，需要有个愿意帮你的中国号码持有者；答案自己也提醒"情况可能已变"。
  - 证据（逐字引用，≤30 词）：
    - "the systems I have encountered only accept Chinese phone numbers"（answer_id=162287, question_id=162278, score=14, accepted=no, date=2021-01-27）
    - "There is a loophole, though--the phone number need not belong to the device"（answer_id=162287, question_id=162278, score=14, accepted=no, date=2021-01-27）
  - 提到的替代做法：用移动数据。

- 做法9：**没有 VPN 时的英文搜索替代：Bing（能用但很差）或 Yahoo + 百度**
  - 前提/失效点：百度界面只有中文，且英文检索能力差；Bing 在部分时期时好时坏。
  - 证据（逐字引用，≤30 词）：
    - "I usually end up using Bing if it's something I have to search for in English, even though it's really primitive and low quality"（answer_id=78824, question_id=78820, score=13, accepted=no, date=2016-09-15）
  - 提到的替代做法：Yahoo（"Yahoo works better than Bing in China"）；用百度搜中文；"If you do get VPN, don't expect it to “just work” all the time."

- 做法10：**高铁 Wi-Fi 不能当工作网络**，VPN 叠加后延迟更高
  - 前提/失效点：靠 4G/5G 回传、山区隧道多、车速 >300km/h 时不稳定；安静车厢禁止音视频会议。
  - 证据（逐字引用，≤30 词）：
    - "Due to the fact that the train often travels very fast (> 300 km/h), the reception and speed are not very reliable."（answer_id=162279, question_id=162278, score=20, accepted=no, date=2021-01-26）
    - "if you are using a VPN on top of it, your connection will suffer from more latency issues"（answer_id=162279, question_id=162278, score=20, accepted=no, date=2021-01-26）
  - 提到的替代做法：微信语音通话可用、视频通话勉强。

### 半解法与无解声明（上网）
- **"提前装好 VPN"** 是这个主题出现频率最高的半解法，跨 2013→2016 多个答案；但同批答案自己拆台：最常见品牌最容易被封、Apple 中国区已下架、GFW 探测判定可以延后、自建节点速度差。
- **"用香港卡/境外漫游绕过防火墙"**：数据里有两个不同年代的答主说"能"，但**同一位答主同时说"没有任何供应商会保证"**——这是"半解法"的教科书案例。
- **"借别人的中国手机号登录 Wi-Fi"**：答案自己标注为 loophole。
- **"让中国朋友帮忙"**：SIM 卡注册、充值的每一环都以"有中国朋友"为前置条件。
- **答案承认无解**：eSIM 问题下唯一答案自认过时且不给产品推荐；"SIM 卡长期方案"的答案是 "None"。

---

## 主题：住宿与临时住宿登记

- 做法1：**住酒店就交给酒店**——登记是酒店的法定义务，不是你的
  - 前提/失效点：法律上酒店须在入住后 3 小时内把证件信息录入旅馆业治安管理信息系统并报送公安，不登记是酒店违法（可罚 1000–5000 元）。但**只有"有资质接待外国人"的酒店才能出那张登记表**，小旅馆/民宿可能根本不办。
  - 证据（逐字引用，≤30 词）：
    - "If you are staying in a hotel in China, the onus is on the hotel to get you registered and pass your information to the police."（answer_id=130986, question_id=130983, score=4, accepted=yes, date=2019-01-27）
    - "they or the persons who accommodate them shall, within 24 hours after the foreigners' arrival, go through the registration formalities with the public security organs"（answer_id=130986, question_id=130983, score=4, accepted=yes, date=2019-01-27）
    - "It is an offence for hotels that refuse to do so."（answer_id=130986, question_id=130983, score=4, accepted=yes, date=2019-01-27）
  - 提到的替代做法：无（法律义务，无替代）。

- 做法2：**住民宿/朋友家必须 24 小时内本人或房东去派出所登记**
  - 前提/失效点：现实执行很松——答主观察到**大量沙发客/Airbnb 房东直接跳过这一步**；但跳过意味着一旦签证到期，公安会顺着"最后登记地"找人。
  - 证据（逐字引用，≤30 词）：
    - "I know of a lot of couchsurfers/airbnb hosts skipping this step"（answer_id=65343, question_id=65339, score=4, accepted=yes, date=2016-03-17）
    - "When you stay in a hotel, the hotel is required to forward your passport details to the local police."（answer_id=22213, question_id=21405, score=8, accepted=no, date=2013-12-03）
  - 提到的替代做法：无。答主只提示"别跳过"。

- 做法3：**办签证延期时，要提前拿到"临时住宿登记表"（粉色表），且必须是申请当晚的住宿**
  - 前提/失效点：必须住在**有资质接待外国人的旅馆/青旅**；这张表必须在"递交申请之后的那个晚上"有效（不是前一夜）；随后去该市的公安出入境管理办证，7 天内出结果。找这类旅馆的方式，答案只给了第三方清单（eChinaCities）。
  - 证据（逐字引用，≤30 词）：
    - "you must have the Registration Of Temporary Residence, the pink form you receive when you check in to a hotel or hostel that is licensed to host foreigners"（answer_id=92510, question_id=92489, score=2, accepted=no, date=2017-04-30）
  - 提到的替代做法：国际青年旅舍（答案举了南宁的名单）。

- 做法4：**只能选"接待外国人的酒店"**——答案把"能接待外国人"当作筛选条件，而非可协商项
  - 前提/失效点：这些酒店会**自动**帮你完成登记；反过来说，不能接待外国人的住宿会让你连登记都没有。
  - 证据（逐字引用，≤30 词）：
    - "Hotels that accept foreigners will do that for you automatically."（answer_id=191796, question_id=191788, score=12, accepted=no, date=2024-10-16）
  - 提到的替代做法：住朋友家 → 提醒房东在期限内办手续（"make sure your host does the proper paperwork within the required deadline"）。

- 做法5：**把"入境卡上的住址"当真实信息填**，TWOV 不要求订单但要求有落脚点
  - 前提/失效点：官方没写要酒店订单，但入境卡必须填一个地址；答案建议不要瞎编。
  - 证据（逐字引用，≤30 词）：
    - "you do have to put down an address on your arrival card at immigration. This could be any address but I wouldn't just make one up."（answer_id=65343, question_id=65339, score=4, accepted=yes, date=2016-03-17）
  - 提到的替代做法：住酒店则由酒店登记。

- 做法6：**16 岁以上外国人在华须随身携带护照**（法律义务，不只是酒店场景）
  - 前提/失效点：另一条高赞答案（2023）说实践中很少被查、且把护照留在酒店保险箱"在 40 多个国家都没出过问题"，与法条存在张力。
  - 证据（逐字引用，≤30 词）：
    - "Foreigners having reached the age of 16 who stay or reside in China shall carry with them their passports"（answer_id=99766, question_id=99760, score=4, accepted=yes, date=2017-08-08）
    - "I never carry my passport if there is a good place where I can leave it (hotel safe, AirBnB, etc)"（answer_id=183565, question_id=183552, score=17, accepted=yes, date=2023-09-15）
  - 提到的替代做法：护照复印件 + 领事馆收件回执（在护照被收走期间）；酒店保险箱。

- 做法7：**签证申请上的酒店订单通常不会被核查**（灰区做法）
  - 前提/失效点：这是"半解法/灰区"，两个答案都说实践上查不到，但一个是 2017 年、一个是代理的原话；与"入境卡要填真实地址"的要求并不冲突，但**不能理解为可以随便编**。
  - 证据（逐字引用，≤30 词）：
    - "I don't think they actually double check what you submit."（answer_id=101463, question_id=101458, score=2, accepted=yes, date=2017-09-04）
    - "the agency I used said outright book, print, cancel, which is what I did"（answer_id=116705, question_id=116697, score=4, accepted=no, date=2018-06-13）
  - 提到的替代做法：订可取消的房/票。

### 半解法与无解声明（住宿与登记）
- **"订能接待外国人的酒店"** 是社区默认答案，但**没有任何答案给出可查询的官方名单**：一条答案转向第三方站点（eChinaCities）的青旅清单；另一条只说"accept foreigners 的酒店会自动办"。这是本主题最大的信息缺口。
- **"住民宿就没事"**：答案一边说 24 小时登记是硬要求，一边承认沙发客/Airbnb 房东普遍跳过——即**普遍存在但没人能保证后果**。
- **"酒店前台帮你"** 在本主题里是合理路径（法定责任在酒店），但在其他主题（找代售点、找银行、当翻译）里，"让前台帮忙"是反复出现的半解法，而它把成功条件押在酒店的英语能力与意愿上。
- **答案承认无解**：如何找到"能出登记表的住宿"、如何找到"不收手续费的 ATM"，答案都是"靠第三方清单"或"问你自己银行"。

---

## 主题：火车票、购票渠道、取票进站

- 做法1：**优先用官方 12306（已有英文版）；代理只在"抢不到票"时用**
  - 前提/失效点：代理的价值是**额外票源**（如夏季西藏方向，公开票被瞬间抢空，含黄牛），代价是**不透明的加价**；答主判断：既然 12306 有英文版，"用代理克服语言障碍"这个理由已不重要。2019 年后实名制 + 候补排队进一步压缩了黄牛空间。
  - 证据（逐字引用，≤30 词）：
    - "The obvious downside to using an agent is that they charge money for the service, and the markup can be quite unclear"（answer_id=162352, question_id=162347, score=4, accepted=yes, date=2021-02-01）
    - "Agents have access to additional inventory, and can thus buy tickets even when you can't."（answer_id=162352, question_id=162347, score=4, accepted=yes, date=2021-02-01）
  - 提到的替代做法：China Highlights（答主自用于西藏，因为西藏本来就必须用代理）。

- 做法2：**进站按"机场式"预留时间**（安检 + 行李 + 证件核验），但 1 小时是保守值
  - 前提/失效点：对**没有中国身份证、不会中文、首次到站**的旅客，图示时间"大概是合适的"；中国系统对外国护照旅客并不友好，好的一面是人工窗口排队短。京沪等高频线路可压到 25 分钟。
  - 证据（逐字引用，≤30 词）：
    - "There is an airport-style luggage, security, and ID check."（answer_id=162164, question_id=162163, score=46, accepted=no, date=2021-01-19）
    - "you can probably do with 25 min for trains and 30 min for the airport"（answer_id=162177, question_id=162163, score=21, accepted=no, date=2021-01-20）
    - "Chinese systems are usually not particularly friendly to foreigners travelling with a passport"（answer_id=162177, question_id=162163, score=21, accepted=no, date=2021-01-20）
  - 提到的替代做法：买商务座/VIP 通道（"much smoother experience for business travellers"）。

- 做法3：**接受"实名制"的一切后果**：票与人绑定、只能本人用、取票/进站要查证件
  - 前提/失效点：中国身份证可直接刷闸机，**外国护照旅客常需走人工通道**；答案不确定虹桥是否已装护照扫描仪。
  - 证据（逐字引用，≤30 词）：
    - "all passenger trains in China have adopted the “real-name system” where all tickets must be purchased with an associated name (and ID number)"（answer_id=162191, question_id=162189, score=3, accepted=no, date=2021-01-21）
    - "you may need to see a staff member (although some stations are now equipped with passport scanners, I am not sure about Hongqiao)"（answer_id=180499, question_id=180494, score=4, accepted=yes, date=2023-04-07）
  - 提到的替代做法：无。

- 做法4：**可以在中途站上车**（未乘区间不退票）
  - 前提/失效点：必须同日同车次；退款只退未使用区间——也就是**不退**。
  - 证据（逐字引用，≤30 词）：
    - "Yes, they may do so provided they board the specified train at the specified date. However, the fare for the unused interval will not be refunded."（answer_id=144739, question_id=144734, score=19, accepted=yes, date=2019-08-29）
  - 提到的替代做法：无（引的是 12306 官方 FAQ）。

- 做法5：**离线找代售点**：把"火车票代售点"这五个字拿去问人/给酒店前台看，或直接在百度地图搜这五个字
  - 前提/失效点：这是 2016 年的做法；**电子客票普及后，代售点/实体票的重要性大幅下降**，且答案自己也说"没有线上名录"。
  - 证据（逐字引用，≤30 词）：
    - "copy this photo onto your phone and then show it to locals, or even better your hotel reception"（answer_id=71979, question_id=71969, score=10, accepted=no, date=2016-06-23）
    - "Searching for this (the Chinese phrase!) on baidu maps gives a good selection of such offices"（answer_id=71981, question_id=71969, score=6, accepted=yes, date=2016-06-23）
  - 提到的替代做法：Google Maps 也能搜到但结果更少、缺"巷子里的便宜小店"。

- 做法6：**跨境/香港段的票必须本人持护照到柜台买**（记名票）
  - 前提/失效点：这正是 TWOV 旅客的死结——要在广东口岸出境去香港，可能需要一张去香港的车票，而票**记名、要本人到场**，代买还得有人帮你买好并寄给你。
  - 证据（逐字引用，≤30 词）：
    - "you need to present your passport at the ticket counter, in person (tickets are nominative). So not ideal, to say the least..."（answer_id=184640, question_id=184636, score=3, accepted=yes, date=2023-11-18）
    - "But unless you have a friend who can buy it for you ahead of time, and send it to you, good luck."（answer_id=184640, question_id=184636, score=3, accepted=yes, date=2023-11-18）
  - 提到的替代做法：在口岸现买跨境巴士票（"you can buy a ticket either at their point of origin, or at the border crossing"）。

- 做法7：**车站↔机场换乘按最坏情况规划**（虹桥→浦东至少 2 小时；SHA→PVG 夜间转场风险极高）
  - 前提/失效点：虹桥到浦东有直达巴士（约 36 元、60–70 分钟，堵车会大幅拉长）和地铁 2 号线（约 1.5 小时，注意并非所有车次都到浦东机场，需在广兰路换乘）；磁悬浮只在龙阳路换乘时能省 20–25 分钟。夜间转场：中国东方航空官方建议 3.5 小时。
  - 证据（逐字引用，≤30 词）：
    - "So to be safe, I would count at least two hours for transfer."（answer_id=180499, question_id=180494, score=4, accepted=yes, date=2023-04-07）
    - "If you are a foreigner with lots of bags, a few kids in tow, don't speak Chinese"（answer_id=201532, question_id=201526, score=10, accepted=no, date=2025-10-31）
  - 提到的替代做法：磁悬浮（"the Maglev takes 8 minutes"，answer_id=180499）；持当日机票磁悬浮有折扣（answer_id=24894, question_id=24884, score=10, accepted=yes, date=2014-03-10）；直接换成直飞 PVG 的航班（被采纳答案的建议）。

- 做法8：**高铁上的 Wi-Fi 别当生产工具**（见"上网"主题做法10）
  - 前提/失效点：同上。

### 半解法与无解声明（火车票）
- **"预留 1 小时进站"**（score 46 的问题）在答案里被拆成两层：对熟悉流程的人是过度保守，对"无中国身份证 + 不会中文 + 第一次"的人是必要——**同一条问题下两个答案给出了完全相反的保留时间**，说明这个数值本质上取决于旅客个人条件。
- **"去代售点/让前台帮忙找"**：2016 年做法，且答案承认没有线上名录、只能靠"拿照片问人"。
- **"用代理买票"**：在西藏这类必须用代理的场景是唯一路径，但答主只能说"weren't cheap ... Your mileage may vary"。
- **答案承认无解**：跨境记名票的代买问题——答案是"除非有朋友提前帮你买好寄给你，good luck"；"是否要预留一小时"——两条高赞答案给出相反结论，读者需自行判断。

---

## 主题：签证与过境免签（TWOV）判断

- 做法1：**用 Timatic（航司系统）口径逐项自查**：口岸是否支持 TWOV、第三国如何认定、停留时长、地区一致性
  - 前提/失效点：答案明确警告 **"Timatic has experienced a major overhaul. There are some inconsistencies"**，即官方口径本身不稳定；列举的限制包括福州/黄山/牡丹江/深圳/延吉不支持 TWOV，关岛/北马里亚纳**不算**美国之外的第三国，而**香港、澳门、台湾相对于中国算第三国**。
  - 证据（逐字引用，≤30 词）：
    - "The following is taken from Timatic, the database used by airlines"（answer_id=106267, question_id=106266, score=10, accepted=yes, date=2017-12-04）
    - "Transit without visa (TWOV) is not possible at Fuzhou (FOC), Huangshan (TXN), Mudanjiang (MDG), Shenzhen (SZX) and Yanji (YNJ)."（answer_id=106267, question_id=106266, score=10, accepted=yes, date=2017-12-04）
    - "Hong Kong, Macau and Taiwan, however, do in relation to China."（answer_id=106267, question_id=106266, score=10, accepted=yes, date=2017-12-04）
  - 提到的替代做法：让航司按 Timatic 判断——这也是被拒登机的判定依据："a Syrian national DOES require a visa to transit Hong Kong, even if only for a few hours, and even when travelling on a single ticket"（answer_id=168917, question_id=168916, score=63, accepted=no, date=2021-09-28）。

- 做法2：**144 小时免签必须"同区域进出"**——北京区域进就必须北京区域出
  - 前提/失效点：这条直接否掉了"北京→香港坐高铁"的常见设想：**G 车在香港做两地出入境手续，属于"多次中途停靠的普通列车"，不符合 TWOV**；只有中途不停站的 Z 次卧铺可以在北京西做手续。答案也给了解释：TWOV 要求你待在入境点的小区域内。
  - 证据（逐字引用，≤30 词）：
    - "The 144-hour visa-free transit requires that you stay within a certain area of your arrival point, and depart the country from within that same area."（answer_id=148572, question_id=148566, score=30, accepted=yes, date=2019-10-18）
    - "The fast G train calls multiple times between Beijing and Hong Kong, making it a 'normal' train."（answer_id=148581, question_id=148566, score=17, accepted=no, date=2019-10-18）
  - 提到的替代做法：改乘 Z 次卧铺（中途不停、车门锁闭）；或在同一区域内改从上海/杭州等地出境。

- 做法3：**TWOV 不需要酒店订单**，但入境卡要填住址 + 24 小时内登记
  - 前提/失效点：官方文本里没有订单要求；但你必须住某处并填地址，且登记义务照样存在。
  - 证据（逐字引用，≤30 词）：
    - "no official sources mention the requirement of a hotel booking"（answer_id=65343, question_id=65339, score=4, accepted=yes, date=2016-03-17）
  - 提到的替代做法：无。

- 做法4：**遇到不懂规则的地服/边检，就地要求找主管、一起看屏幕上的规则**
  - 前提/失效点：答案直说机场代理"对 72 小时免签几乎一无所知"；这条做法要求旅客自己先掌握规则原文（也就是做法1的内容）。
  - 证据（逐字引用，≤30 词）：
    - "The agents at the airports know next to nothing about the 72-hour visa-free transit rule."（answer_id=65343, question_id=65339, score=4, accepted=yes, date=2016-03-17）
    - "Stand your ground, ask for a supervisor or read the rules on their screen with them if necessary."（answer_id=65343, question_id=65339, score=4, accepted=yes, date=2016-03-17）
  - 提到的替代做法：无。

- 做法5：**免签可以多次入境**（官方 FAQ 明说没有次数/总天数限制），但边检保留"认定滥用"的权力
  - 前提/失效点：一条 2024 年的答案说中国对"visa runs"一直很宽容，但**随时可能变**，建议准备 B 计划；另一条引用德国使馆 FAQ 原文支持"可多次"，同时自己加了"如果他们觉得你在滥用，可能直接拒绝入境"。
  - 证据（逐字引用，≤30 词）：
    - "you can enter China more than once for 15 days. Although I suspect if they feel you are misusing these regulations they may just deny re-entry."（answer_id=191795, question_id=191788, score=6, accepted=no, date=2024-10-16）
    - "China so far has been very tolerant of 'visa runs'"（answer_id=191796, question_id=191788, score=12, accepted=no, date=2024-10-16）
  - 提到的替代做法：带上酒店预订（使馆建议）；如实告知住宿类型。

- 做法6：**24 小时内在机场中转、不出口岸 → 不需要签证**
  - 前提/失效点：必须是"直接过境中国"的国际航班、有最终目的地机票和已订座位、且不离开机场。
  - 证据（逐字引用，≤30 词）：
    - "will stay in a transit city for less than 24 hours without leaving the airport"（answer_id=1164, question_id=1162, score=16, accepted=yes, date=2011-07-21）
  - 提到的替代做法：2015 年时 51 国可在 8 个城市享受 72 小时免签（已被 144/240 小时政策取代）。

- 做法7：**港澳台往返算"再次入境"**：单次签证不够，需要两次/多次签证
  - 前提/失效点：答案自己也说找不到权威来源，只能引签证代理和论坛帖；这条结论与 TWOV 的"港澳台算第三国"是两套不同规则，容易混淆。
  - 证据（逐字引用，≤30 词）：
    - "entering Taiwan is considered leaving China, and you'll thus need a multiple-entry visa to get back to the mainland"（answer_id=22692, question_id=22690, score=17, accepted=yes, date=2013-12-22）
  - 提到的替代做法：无。

- 做法8：**在香港递签**（名额多、接受非居民、约 4 个工作日），并留好"护照在领事馆"的凭证
  - 前提/失效点：护照被收走期间不能用于飞行/入住；香港法律要求随身携带身份证件，所以答案建议拍下护照资料页 + 香港入境回执 + 签证申请回执，以便被查证件时证明护照在官方手里。
  - 证据（逐字引用，≤30 词）：
    - "I would advise you to do it in Hong Kong, which probably has the biggest Visa Centre, and accepts non-residents."（answer_id=183556, question_id=183552, score=42, accepted=no, date=2023-09-15）
    - "I would recommend to take a photo of your passport ID page"（answer_id=183556, question_id=183552, score=42, accepted=no, date=2023-09-15）
  - 提到的替代做法：答主提到中国已简化申请表；另一条 2023 年答案建议把领事馆给的时限"double to 8 business days"。

- 做法9：**西藏（TAR）无法自由行**：必须跟团/持证，酒店无证不接待
  - 前提/失效点：答案给出替代方案——去 TAR 之外语言上属藏区的地区；同时必须用代理买火车票（西藏方向），这也是"用代理"唯一的合理场景。
  - 证据（逐字引用，≤30 词）：
    - "it's impossible to travel without a guide"（answer_id=163308, question_id=163304, score=9, accepted=no, date=2021-04-11）
    - "Hotels require permits for check in and will refuse entry (and probably call the cops) without one."（answer_id=163308, question_id=163304, score=9, accepted=no, date=2021-04-11）
  - 提到的替代做法：去 TAR 之外的藏区（答案另附专门问题链接）。

### 半解法与无解声明（签证/TWOV）
- **"拿 Timatic/航司说的去问柜台"**：既是解法也是解法的边界——同一套 Timatic 既能帮你自证，也能让航司拒绝你登机（2021 年被拒登机案例）。
- **"打印行程单/联程票"**：数据里没有任何答案把"打印联程票"当作 TWOV 的充分条件；真正被讨论的是**区域一致性**和**票本身能否买到**。
- **"问边检/找主管"**：被采纳答案给的就是这条，但它的前提是旅客自己先懂规则。
- **答案承认无解/不可判断**：
  - 陆路口岸 TWOV 出境所需的那张去香港的车票——"除非有朋友提前买好寄给你，good luck"。
  - 免签入境次数——官方说没有限制，答主只能说"如果他们觉得你在滥用就可能拒绝入境"，**最终裁量权在边检**。
  - Timatic 自身"major overhaul ... inconsistencies"，即权威口径也在漂移。

---

## 主题：市内交通（地铁 / 打车 / Didi）

- 做法1：**用微信里的 Didi 小程序叫车**（英文界面、可付款、可文字沟通）
  - 前提/失效点：落地前必须绑好外卡；**司机常常直接打电话**（找不到车就打电话），对不说中文的人是硬门槛，解决办法是发文字说明自己是外国人、希望用文字沟通。
  - 证据（逐字引用，≤30 词）：
    - "Set up WeChat with your credit/debit card in advance and then you can use the Didi mini app in English to book, pay and even chat with your driver."（answer_id=194748, question_id=194742, score=11, accepted=yes, date=2025-04-10）
    - "It is very likely, and you often will not even find the car unless you call the driver, or they call you."（answer_id=92773, question_id=92648, score=5, accepted=yes, date=2017-05-04）
    - "explain to them in written Mandarin (i.e. texting) that you are a foreigner and would prefer texting"（answer_id=92773, question_id=92648, score=5, accepted=yes, date=2017-05-04）
  - 提到的替代做法：出租车（但见做法5：不能刷卡）。

- 做法2：**市区优先地铁**，打车只用于"地铁停运/深夜/带大件行李"
  - 前提/失效点：北京凌晨 4 点落地时首班地铁 06:30 才开，只能打车/Didi；地铁比打车明显便宜，且地面交通"almost always awful"。
  - 证据（逐字引用，≤30 词）：
    - "Taxis are cheap in Beijing, but traffic can be really bad, so the metro is the way to go."（answer_id=194748, question_id=194742, score=11, accepted=yes, date=2025-04-10）
    - "the first train won't run until 06:30"（answer_id=194748, question_id=194742, score=11, accepted=yes, date=2025-04-10）
  - 提到的替代做法：Didi；上海磁悬浮（持当日机票有折扣）。

- 做法3：**自驾不现实时用"公共交通 + 机场大巴"组合**，并预留时间
  - 前提/失效点：虹桥→浦东直达巴士约 36 元、60–70 分钟（高峰会大幅延长）；地铁 2 号线约 1.5 小时，且并非所有车次终到浦东机场（需广兰路换乘）；磁悬浮仅在龙阳路换乘时省 20–25 分钟。
  - 证据（逐字引用，≤30 词）：
    - "There is a direct bus from the Hongqiao Transit Center (虹桥枢纽东交通中心) to Pudong Airport departure area, costing around 36 CNY."（answer_id=180499, question_id=180494, score=4, accepted=yes, date=2023-04-07）
    - "the Maglev takes 8 minutes"（answer_id=180499, question_id=180494, score=4, accepted=yes, date=2023-04-07）
  - 提到的替代做法：磁悬浮；直接换直飞航班。

- 做法4：**跨城/偏远目的地要准备"返程费"**：长途可能被拒载，或需加付 50%–100%
  - 前提/失效点：大城市拒载概率小、小城市大；一小时车程估价 200–500 元，加返程费后 300–1000 元；答案反而建议这种场景改用"旅行社带司机的租车"。
  - 证据（逐字引用，≤30 词）：
    - "if your trip is too long, or to remote/countryside areas, such that the taxi can't get a return trip, you need to pay for the return trip as well"（answer_id=95946, question_id=95885, score=3, accepted=no, date=2017-06-25）
    - "you'll need to pay anywhere from 300-1000 RMB"（answer_id=95946, question_id=95885, score=3, accepted=no, date=2017-06-25）
  - 提到的替代做法：找本地旅行社租带司机的车。

- 做法5：**"黑车"在口岸/机场是常态，本地人 at all costs 避开**
  - 前提/失效点：两种黑车：一种是明说非法、开口就要 5–10 倍价的；另一种**冒充打表出租车，用报废车/改装计价器**，风险更高（半路加价、甩客，极端情况更糟）。答案作为深圳本地人只给出"避开"，**没有给出识别方法**，也没给出替代叫车渠道之外的保障。
  - 证据（逐字引用，≤30 词）：
    - "we avoid those “illicit vehicles” (or “black cabs”, hei che 黑车 in Chinese) at all costs"（answer_id=129006, question_id=129001, score=58, accepted=no, date=2018-12-27）
    - "They claim to be metered taxis, but are in fact not."（answer_id=129006, question_id=129001, score=58, accepted=no, date=2018-12-27）
    - "Touts in China are aggressive, especially for something as completely fungible as a taxi ride, and especially to foreigners"（answer_id=44364, question_id=44350, score=44, accepted=yes, date=2015-03-09）
  - 提到的替代做法：无（另一答案补充说，这些拉客者有时也提供住宿/其他服务，只有"熟悉当地且会语言"时才考虑当作最后手段）。

- 做法6：**打车必须有现金或移动支付**（出租车不收信用卡）
  - 前提/失效点：这是 2016 年的判断；2024–2025 年答案仍把"落地能否付打车费"当作夜间转场失败的关键风险点。
  - 证据（逐字引用，≤30 词）：
    - "Especially in China, you cannot use a credit card to take a taxi."（answer_id=80298, question_id=73708, score=2, accepted=no, date=2016-10-07）
  - 提到的替代做法：Alipay/微信（"bring cash or set up Alipay before arrival"，answer_id=201528, question_id=201526, score=12, accepted=yes, date=2025-10-31）。

- 做法7：**机场 Didi 上车点与出租车点不在一处，靠站内指示牌找**
  - 前提/失效点：凌晨时段懂英语的工作人员更少；昆明长水机场的 Didi 上车点在主航站楼主入口附近、靠近出租车点但不同位置。
  - 证据（逐字引用，≤30 词）：
    - "There is a DiDi pickup area by the main entrance to the main terminal, near the Taxi stand, but not at the same place."（answer_id=192340, question_id=189574, score=3, accepted=yes, date=2024-11-22）
    - "at 2am, on a weekday in July, there were many fewer English speakers"（answer_id=192340, question_id=189574, score=3, accepted=yes, date=2024-11-22）
  - 提到的替代做法：看机场官网地图（答案给了截图链接）。

- 做法8：**交通卡跨城互通的边界很细**：T-union 卡在香港只能刷地铁（机场快线除外），不能刷巴士/轻铁/电车
  - 前提/失效点：八达通、深圳通、互通卡、T-union 卡四代产品功能不同；答案用一张对照表说明，**普通旅客很难自己判断手里那张卡能不能用**。
  - 证据（逐字引用，≤30 词）：
    - "the T-union card can only be used on the metro in Hong Kong, excluding the Airport Express"（answer_id=194670, question_id=194669, score=18, accepted=yes, date=2025-04-08）
  - 提到的替代做法：用 Alipay 小程序里的交通卡（见移动支付做法7）。

### 半解法与无解声明（市内交通）
- **「叫酒店帮叫车」** 与 **「给司机看中文地址卡片」**：数据里有一条明确版本——找代售点时「把照片给酒店前台看」，以及三亚机场可以联系酒店（answer_id=1816, question_id=1265），都属于把沟通成本外包给住宿方。
- **"只在正规渠道打车"**：这是黑车主题的常识答案，但 2018 年深圳本地答主承认**第二类黑车伪装成正规打表车**，只能建议"不惜一切代价避开"，没给可操作的鉴别标准。
- **Didi 的"文字沟通"** 依赖司机愿意回文字，且答案自己用 "usually" 限定。
- **答案承认无解**：跨城打车价格区间大到 300–1000 元且规则因城而异，答案的结论是"这种场景别打车，去租车"。

---

## 主题：语言与点餐

- 做法1：**有图菜单 + 手指点单**是最通用的兜底
  - 前提/失效点：依赖餐厅有图菜单（答案说"typically"有）；对过敏原无效——餐牌不会写清成分。
  - 证据（逐字引用，≤30 词）：
    - "there is typically a menu with pictures so you can point at stuff"（answer_id=133346, question_id=133317, score=3, accepted=yes, date=2019-03-06）
  - 提到的替代做法：会说中文的人代点；自己观察菜品判断是否素食。

- 做法2：**装翻译 App 用相机/语音/手写三种模式**，并下载离线语言包
  - 前提/失效点：大部分功能需要数据连接（所以必须先解决上网）；离线包才能覆盖"没有网"的场景；这是 2015 年答案，写答案的人自称在 Google 工作（利益相关）。
  - 证据（逐字引用，≤30 词）：
    - "Camera - Tap the Camera button to take a picture of text to be translated."（answer_id=46615, question_id=46594, score=5, accepted=no, date=2015-04-24）
    - "you can download an offline language pack to get the basics even without one"（answer_id=46615, question_id=46594, score=5, accepted=no, date=2015-04-24）
  - 提到的替代做法：手写模式输入汉字；用翻译 App 的相机功能去"读"百度地图的界面（answer_id=102298）。

- 做法3：**学几个汉字（至少数字）+ 把关键需求写在纸上/截屏给店家看**
  - 前提/失效点：公交线路号常以汉字显示，能认数字就有实际收益；重度过敏场景要靠纸质/屏幕说明。
  - 证据（逐字引用，≤30 词）：
    - "The other trick is to learn a few Chinese characters - at least the numbers."（answer_id=7841, question_id=7812, score=4, accepted=no, date=2016-02-16）
    - "Have printed signs or phone screens to help."（answer_id=133346, question_id=133317, score=3, accepted=yes, date=2019-03-06）
  - 提到的替代做法：双语路人主动帮忙当翻译。

- 做法4：**过敏/素食可以靠"自带食物 + 自带白饭"兜底**
  - 前提/失效点：答案明确说生黄豆制品几乎无处不在且难沟通，"no raw soy" 的牌子很多餐厅也看不懂；严重生豆过敏被单独列为需要更全面准备的情形。
  - 证据（逐字引用，≤30 词）：
    - "It's perfectly fine to bring your own food or drink to a restaurant."（answer_id=133346, question_id=133317, score=3, accepted=yes, date=2019-03-06）
    - "Raw soy is a real problem though, because it's in a lot of common Chinese foods and condiments."（answer_id=133346, question_id=133317, score=3, accepted=yes, date=2019-03-06）
    - "many restaurants may honestly don't know what exactly that means"（answer_id=133346, question_id=133317, score=3, accepted=yes, date=2019-03-06）
  - 提到的替代做法：结伴让会说中文的人点菜；点白饭（"plain white rice, which is not as common as one would think"）。

- 做法5：**接受"英语只在高端西式场所通用"**，日常靠"附近总有会说英语的人被叫来"
  - 前提/失效点：这是"能生存但很难自助"的状态；答案的正面例子都建立在有人愿意帮忙上，不构成可复制的机制。
  - 证据（逐字引用，≤30 词）：
    - "English is spoken in high-end Western establishments. Elsewhere written and spoken English is very rare."（answer_id=7813, question_id=7812, score=8, accepted=no, date=2016-02-10）
    - "generally there is someone else nearby who does, who will be quickly called to 'deal' with you"（answer_id=8781, question_id=7812, score=2, accepted=no, date=2016-07-16）
  - 提到的替代做法：学基础中文（数字、打车方向、常见食物）。

- 做法6：**地图类工具的语言与精度都不可靠**：百度地图只有中文；中国境内传统地图普遍有约 500 米偏移
  - 前提/失效点：替代方案 Maps.me 有英文界面且可离线，但**答案不确定它在中国是否显示正确坐标**；BAIDU 的登录流程本身还要靠翻译。
  - 证据（逐字引用，≤30 词）：
    - "All traditional maps will show 500m wrong location as a result of Government Restriction"（answer_id=102298, question_id=102286, score=2, accepted=yes, date=2017-09-18）
    - "I will recommend taking Maps.me as a backup, as it has English UI and does not require the Internet to work."（answer_id=102298, question_id=102286, score=2, accepted=yes, date=2017-09-18）
    - "However, I am not sure whether Maps.me show right GPS coordinates in China"（answer_id=102298, question_id=102286, score=2, accepted=yes, date=2017-09-18）
  - 提到的替代做法：用翻译 App 相机读百度界面；先用网页版百度注册再登录手机端。

### 半解法与无解声明（语言与点餐）
- **"靠有图菜单指"**：被采纳答案给的正是这条，但它只能解决"想吃什么"，**解决不了"不能吃什么"**（过敏/素食在同一答案里被单独标为更难的场景）。
- **"翻译 App"**：所有版本都默认你有数据连接——也就是说语言解法**依赖上网解法**，形成依赖链。
- **「学好几句中文/认几个汉字」**：答案自己说 5 年才 OK、但真的没必要学中文（answer_id=8781），即这条建议的收益被答主本人弱化。
- **答案承认无解**：地图偏移 500 米是政府限制导致的，答案给出的备份方案自己也不确定是否准确；POI 数据错误（把玉器店标成博物馆）是答案靠问当地朋友才发现并纠正的（answer_id=69415, question_id=51421, score=11, accepted=yes, date=2016-06-01）。

---

## 主题：安全与诈骗

- 做法1：**在口岸/机场拒绝一切主动搭讪的"出租车"，尤其深圳罗湖**
  - 前提/失效点：本地人（答案作者）自己也只说"避开"；第二类黑车**伪装成打表车**且用改装计价器，旅客几乎无法事前识别；拒绝付费时答主承认"只有上帝知道会发生什么"。
  - 证据（逐字引用，≤30 词）：
    - "demand high fares (up to 5-10 times of normal cab fares) upfront"（answer_id=129006, question_id=129001, score=58, accepted=no, date=2018-12-27）
    - "Touts in China are aggressive, especially for something as completely fungible as a taxi ride, and especially to foreigners"（answer_id=44364, question_id=44350, score=44, accepted=yes, date=2015-03-09）
  - 提到的替代做法：无（答案只给"避免"）。

- 做法2：**换汇只走银行/正规点，要回执**（防黑市与假币）
  - 前提/失效点：见"现金/换汇"主题做法2；这条同时也是安全建议——陌生人主动找你换钱是常见场景。
  - 证据（逐字引用，≤30 词）：
    - "Do not swap money at shady venues - you are always supposed to show your passport and you will receive a standardized receipt with stamp"（answer_id=180744, question_id=180724, score=17, accepted=no, date=2023-04-20）
  - 提到的替代做法：无。

- 做法3：**ATM 被盗刷/被吐假币后：报警 → 拿案件回执 → 找银行追回**
  - 前提/失效点：见"现金/ATM"主题做法1；答主承认与警察打交道耗时且挫败。
  - 证据（逐字引用，≤30 词）：
    - "the police have helped me rather swiftly, provided me with a copy of the case registered which I took to the bank"（answer_id=152004, question_id=152003, score=31, accepted=no, date=2020-01-10）
  - 提到的替代做法：无。

- 做法4：**求助渠道：大街上有大量警察、对外国人态度好，但不会说英语**
  - 前提/失效点：能帮你指路/带路，但**语言不通**；答案同时提醒警察不带枪、治安密度高，说明"找警察"是可行但受限的求助路径。
  - 证据（逐字引用，≤30 词）：
    - "You can count on a policeman being able to help or guide you to a particular place if you're lost"（answer_id=180744, question_id=180724, score=17, accepted=no, date=2023-04-20）
    - "They are unarmed and do not speak English."（answer_id=180744, question_id=180724, score=17, accepted=no, date=2023-04-20）
  - 提到的替代做法：无。

- 做法5：**围观/被摸/被拍这类骚扰：答案明确说没有解法**
  - 前提/失效点：唯一的"做法"是改变自己的行为——远离人群和队伍，或（对不能接受的人）干脆不去中国；答案把这种现象归因于文化环境而非恶意。
  - 证据（逐字引用，≤30 词）：
    - "There is not much you can do - those people have probably never before seen anyone black, and that is the typical reaction in that cultural environment."（answer_id=99204, question_id=99199, score=125, accepted=no, date=2017-08-01）
    - "To avoid this you'd have to stay away from crowds and queues. Which is hard, but not impossible."（answer_id=99214, question_id=99199, score=55, accepted=no, date=2017-08-01）
  - 提到的替代做法：用幽默回应（"to joke around with those who are forward with me"）；接受并"入乡随俗"。

- 做法6：**不要碰毒品、不谈政治宗教**（低成本、极高后果）
  - 前提/失效点：答案的口吻是"这几乎是唯一需要注意的事"，并把文化禁忌（酒、穿着、亲密举动）列为宽松。
  - 证据（逐字引用，≤30 词）：
    - "don't talk about politics and religion, don't commit any crimes (NO drugs)"（answer_id=180725, question_id=180724, score=22, accepted=yes, date=2023-04-20）
  - 提到的替代做法：无。

- 做法7：**机场安检会没收不合规充电宝/锂电池（含未标容量的），且事后无法申诉**
  - 前提/失效点：安检按标签判定，未标注或超规格的会被当危险废物处理；被没收后"几乎无法证明发生过"，唯一渠道是机场投诉电话（答案给了北京 +86-10-96158）。
  - 证据（逐字引用，≤30 词）：
    - "They are required to check power banks and lithium batteries for the labels and to not allow ones that fall outside of the guidelines."（answer_id=59300, question_id=31083, score=22, accepted=no, date=2015-11-28）
    - "you'll have a very hard time proving anything even happened"（answer_id=31084, question_id=31083, score=40, accepted=yes, date=2014-06-27）
  - 提到的替代做法：使用带清晰容量标识的充电宝（这是唯一主动规避方式）。

- 做法8：**护照保管的两难**：法律要求随身携带，实践中高赞答案是"留酒店保险箱"
  - 前提/失效点：法条（16 岁以上须随身携带）vs 实务（"in over 40 countries I've never had a problem"）；护照被领事馆收走期间用"复印件 + 回执"替代。
  - 证据（逐字引用，≤30 词）：
    - "I never carry my passport if there is a good place where I can leave it (hotel safe, AirBnB, etc)"（answer_id=183565, question_id=183552, score=17, accepted=yes, date=2023-09-15）
    - "Even in the highly unlikely case that someone wants to see it out of the blue, a copy plus the receipt from the Chinese Consulate should suffice."（answer_id=183565, question_id=183552, score=17, accepted=yes, date=2023-09-15）
  - 提到的替代做法：复印件 + 领事馆回执。

### 半解法与无解声明（安全与诈骗）
- **"别理搭讪的人 / 只在正规渠道打车"**：本主题最高分（125）与被采纳（22）答案里的两条建议，都是行为规避，不含可验证的识别方法。
- **"找警察"**：可行但被明确限定——不会英语、流程慢。
- **"带复印件"**：只在护照被官方收走时成立（有回执），普通遗失场景答案未覆盖。
- **答案承认无解**：
  - 骚扰/围观类问题，最高赞答案直接写 "There is not much you can do"。
  - 安检没收物品，"you'll have a very hard time proving anything even happened"，等于事后无救济。

---

## 主题：其他发现（附录）

- 做法1：**用 Alipay/Trip.com 等线上渠道买景点票，而非现场排队**（成都/华东高铁场景）
  - 前提/失效点：答案推荐 Trip.com 买火车票；另一条 2025 年答案用 Alipay 小程序买景点票（还可用作支付链路测试）。属于"渠道替代"，但对需要实名/中国证件的场馆不一定适用。
  - 证据（逐字引用，≤30 词）：
    - "train websites like Trip.com are definitely onto something there"（answer_id=189469, question_id=189234, score=2, accepted=yes, date=2024-06-06）
  - 提到的替代做法：12306 英文版；代理（见火车票主题）。

- 做法2：**百度地图不做火车路径规划，别用它查城际交通**
  - 前提/失效点：答案观察到百度地图"偏爱公交"，高铁不显示（上海—无锡 40 分钟高铁 vs 3.5 小时大巴）。
  - 证据（逐字引用，≤30 词）：
    - "Baidu Maps might be a bit bus-crazy for this route. It loves showing buses"（answer_id=189469, question_id=189234, score=2, accepted=yes, date=2024-06-06）
  - 提到的替代做法：Trip.com 等火车票网站。

- 做法3：**POS/地图 POI 数据都要交叉验证**（Google 把玉器店标成博物馆，答案靠问当地朋友才纠正）
  - 前提/失效点：答案提交了 Google 纠错但三个月未改；且中国境内所有传统地图有约 500 米偏移。
  - 证据（逐字引用，≤30 词）：
    - "The “museum” displayed by google maps is a store."（answer_id=69415, question_id=51421, score=11, accepted=yes, date=2016-06-01）
  - 提到的替代做法：用百度地图看街景交叉验证；问当地朋友。

---

## 跨主题：数据里反复出现但仍不彻底的「半解法」

| # | 半解法 | 出现场景（answer_id） | 为什么"不彻底" |
|---|---|---|---|
| 1 | **带现金兜底** | 192958（2025）、89255（2017）、110499（2018）、80298（2016）、151311（2019） | 从 2016 到 2025 反复出现，但**没有任何答案能说清哪些场景一定收现金**；2025 年的实测者只是"恰好把 200 美元花掉了"。现金的真实作用被定位为"覆盖落地到首次成功移动支付之间的空窗"。 |
| 2 | **提前装好 VPN / 买好 VPN** | 59362（2015）、57780（2015）、80076（2016）、2559（2011） | 同批答案自己拆台：最热门品牌最容易被封；Apple 中国区已下架；GFW 探测判定可延后；自建节点速度差；叠加 VPN 后延迟更高（162279）。 |
| 3 | **用香港卡/境外漫游绕过防火墙** | 22882（2013）、14659（2013） | 两位答主都说"能"，但同一位同时说"**没有任何供应商会保证**"；且反向情形（中国 SIM 出境漫游）确认会经过防火墙（120976）。 |
| 4 | **让酒店前台/工作人员帮忙** | 71979（找代售点）、1816（叫车）、73567（问银行是否有会说英语的职员）、183464 的"中国朋友"变体 | 把成功条件押在"酒店愿意帮 + 会英语"上；在找代售点、找登记表、找 ATM 三个场景里都是同一招，且都没有兜底。 |
| 5 | **借别人的中国手机号** | 162287（公共 Wi-Fi 登录，答案自称 loophole）、183464（用中国朋友的身份买 SIM 卡）、84284（SIM 卡挂在朋友身份证下） | 明确标注为 loophole / 借用他人身份，属于"能过但现在不合法/不合规"的地带。 |
| 6 | **出发前自测** | 192952（2025 Alipay 可退票测试） | 被采纳且高分，但同页答案逐条证明它测不到断网、银行二次授权被墙、商户拒收三类真实失败。 |
| 7 | **"先订可取消的酒店/机票"** | 101463（2017）、116705（2018） | 用于满足签证材料的硬要求，属于合规灰区；答案也只是转述代理的原话。 |
| 8 | **改乘"更贵的正规服务"** | 162177（商务座/VIP 通道）、95946（旅行社带司机租车）、201528（改买直飞 PVG 的航班） | 用钱换确定性，对预算敏感的用户等于没有解法。 |

## 跨主题：答案本身承认"无解 / 不可判断"的地方

1. **支付**：外卡绑 Alipay 后仍有商户拒收，2025 年答案只给出"那就用现金"；地铁交通卡对外国手机号直接关闭（194601）。
2. **风控解封**：没有自助路径，只能人工客服 + 交护照照，且 App 无通知（194576）。
3. **SIM 卡长期方案**：唯一答案开头就是 "None"，并列出三个死结（183464）。
4. **eSIM**：唯一答案自认过时、不给产品推荐（183619）。
5. **公共 Wi-Fi**：需要中国手机号，答案自称 loophole（162287）。
6. **地图**：500 米偏移是政府限制，备份方案作者自己也不确定是否准确（102298）；POI 错误只能靠问当地人（69415）。
7. **过敏**："no raw soy" 牌子很多餐厅也看不懂（133346）。
8. **TWOV 陆路出境票**："除非有朋友提前帮你买好寄给你，good luck"（184640）。
9. **免签入境次数**：官方说无限制，但边检保留"认定滥用"的裁量权（191795、191796）。
10. **Timatic 口径**：答案自己标注 "major overhaul ... some inconsistencies"（106267）。
11. **骚扰/围观**：最高赞答案是 "There is not much you can do"（99204）。
12. **安检没收物品**：事后无法证明、无救济途径（31084）。
13. **ATM 手续费**：无法一般化，"ask your own bank"（114918）。
14. **找能接待外国人的住宿**：没有官方可查询名单，只能靠第三方清单（92510）。
15. **火车进站预留时间**：同一问题下两条高赞答案给出相反结论（162164 机场式安检 vs 162177 可压到 25 分钟），读者只能自行判断。

---

## 附：本次抽取的方法与可核查性

- 解析：`answers?filter=withbody` JSON → 剥离 HTML → 保留 `answer_id / question_id / score / is_accepted / body / creation_date`，并按 `se-questions.tsv` 关联问题标题。
- 归并：先按问题标题做主题锚定，再用答案正文关键词细分；一个答案可落入多个主题（已在各主题内标注）。
- 覆盖：7 个文件共 623 条答案，其中与中国相关 452 条；本文件共引用 **83 条**不同答案（147 处引用，全部逐字核对通过）。
- 剔除：与中国入境游客无关的答案（中国公民出国签、北朝鲜旅行、日本/印度/关岛话题、纯政治讨论）未纳入。
- 已知缺口：**eSIM 与中国本地 SIM（2024–2025 年产品级建议）、涉外酒店的可查询名单、240 小时过境免签（2024 年后政策）在 SE 这批数据里几乎没有答案覆盖**——说明这些正是社区知识更新滞后的地方，可作为 TripPal 的差异化信息点。
