# Google Trends 采集结果（geo=US，today 12-m，2026-09-29 抓取）

数据源：`trends.google.com/trends/api`（在 trends.google.com 页面内同源 fetch，见 `research/raw/trends-*.json`）。
趋势值 0-100 为相对热度（该词自身峰值=100）。飙升查询已过滤掉与主题无关的泛热搜（如 openai news）。

## 一、有完整时间线的词条（7 个）

| 词条 | 全年均值 | 前13周均值 | 近13周均值 | 峰值周 | 峰值 |
|---|---|---|---|---|---|
| china travel | 63.7 | 55.7 | 32.2 | Jun 7, 2026 | 100 |
| china visa | 59.8 | 51.2 | 41.1 | May 31, 2026 | 100 |
| alipay | 61.9 | 60.1 | 45.8 | May 24, 2026 | 100 |
| wechat pay | 50.7 | 49.4 | 27.3 | May 24, 2026 | 100 |
| vpn china | 44.6 | 30.2 | 25.5 | Jun 7, 2026 | 100 |
| china esim | 51 | 40.8 | 31.9 | Jun 28, 2026 | 100 |
| great firewall | 47 | 36.6 | 20.7 | Mar 1, 2026 | 100 |

近13周=2026年7-9月（抓取时点），前13周=2025年10-12月。

## 二、相关查询与地区

### china travel
- 热门相关查询：travel to china / travel in china / china travel news / travel news / china travel visa / china visa / google travel / travel insurance / air china / travel news today / china travel guide / china travel advisory / best time to travel to china / china airlines / shanghai / china map / china travel agency / china travel agent / japan travel news / beijing / travel trends 2026 / japan travel news today
- **飙升相关查询**：japan travel news today / travel itinerary template / best travel hacks / best stroller travel systems / travel trends 2026 / us travel advisory china detention / china economy news today / best travel apps for planning / best budget travel destinations / best travel wallets / travel news today / japan travel news / china travel news / travel news / google travel / travel insurance
- 搜索热度地区 Top6：Wyoming(100), California(24), District of Columbia(22), Washington(20), Kansas(19), New York(17)

### china visa
- 热门相关查询：visa to china / visa for china / us china visa / china visa requirements / china visa free / visa application china / china travel visa / chinese visa / china visa cost / china business visa / china visa policy / china work visa / tourist visa china / china visa service / visa for china for us citizens / china visa for us citizens / how to get china visa / china transit visa / china visa status / apply for china visa / visa policy of china / visa bulletin / shanghai / china airlines
- **飙升相关查询**：china economy news today / what is a transit visa / visa bulletin / visa policy of china / china visa application forms / china visa policy / china visa status / china visa requirements / china visa service / china business visa / china visa service center / china visa cost / china work visa
- 搜索热度地区 Top6：Wyoming(100), California(41), District of Columbia(37), Washington(34), New York(27), Kansas(25)

### alipay
- 热门相关查询：alipay china / wechat / alipay card / what is alipay / alipay us / alipay app / alipay international / alipay news / alipay account / how to use alipay / alipay usa / alipay fees / alipay in usa / wise alipay / wechat pay vs alipay / alipay in chinese / what is alipay payment / alipay for foreigners / alipay plus / is alipay safe / alipay hk
- **飙升相关查询**：alipay news / alipay international / what is alipay payment / wechat pay vs alipay / wechat / alipay china / alipay fees / alipay plus / alipay app
- 搜索热度地区 Top6：Wyoming(100), California(34), Washington(32), Kansas(30), District of Columbia(26), New York(26)

### wechat pay
- 热门相关查询：what is wechat pay / wechat pay app / alipay vs wechat pay / how to use wechat pay / wechat pay for foreigners / wechat pay foreign credit card / wechat pay singapore / how to set up wechat pay / how does wechat pay work / tencent paypal wechat pay integration
- **飙升相关查询**：tencent paypal wechat pay integration / what is wechat pay / alipay vs wechat pay / wechat pay singapore
- 搜索热度地区 Top6：Wyoming(100), Washington(28), California(27), New York(26), Kansas(23), District of Columbia(20)

### vpn china
- 热门相关查询：best vpn china / astrill vpn china / proton vpn china / esim china / chinese vpn / nordvpn china
- **飙升相关查询**：best vpn china / proton vpn china
- 搜索热度地区 Top6：Wyoming(100), Kansas(28), Washington(26), California(25), Massachusetts(18), District of Columbia(17)

### china esim
- 热门相关查询：esim in china / esim for china / esim iphone / travel esim / airalo esim china / china mobile / china mobile esim / esim card / china vpn / holafly esim / nomad esim / chinese esim / vpn for china / does whatsapp work in china
- **飙升相关查询**：does whatsapp work in china / holafly esim
- 搜索热度地区 Top6：Wyoming(100), Washington(36), California(35), Kansas(24), District of Columbia(21), New York(20)

### china train
- 搜索热度地区 Top6：Wyoming(100), District of Columbia(28), California(25), Washington(22), Kansas(22), New York(19)
- 采集异常：TIMESERIES:HTTP429; RELATED_QUERIES:HTTP429

### china hotel
- 热门相关查询：china hotels
- 搜索热度地区 Top6：Wyoming(100), California(23), District of Columbia(22), Kansas(19), New York(17), Washington(17)
- 采集异常：TIMESERIES:HTTP429x3

### great firewall
- 热门相关查询：china great firewall / the great firewall / great firewall of china / the great firewall of china / what is the great firewall / chinese great firewall / what is the great firewall of china / china's great firewall / chinas great firewall / the chinese government exerts control over daily life in china. for example, it strictly controls access to , leading some to call these restrictions “the great firewall of china.”
- 搜索热度地区 Top6：Wyoming(100), Kansas(18), California(17), Washington(17), Virginia(14), Massachusetts(14)

### china train
- 热门相关查询：china train station / china train tickets / china train map / china town / china train booking / best scenic train rides
- **飙升相关查询**：best scenic train rides / china train booking

### china hotel
- 热门相关查询：air china / china airlines / china town hotel / china map / china hotel guangzhou
- **飙升相关查询**：air china

> 地区榜中 Wyoming(100) 是小样本归一化伪影（人口少、少量搜索即满分），判读时看 California / New York / Washington / DC / Kansas 等大州。
> `trends-us-head-3.json` 是 Google 限流（HTTP 429）下的失败样本，保留以示证据可追溯性，未计入上表。
