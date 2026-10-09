"""关联问题推荐：每轮答复后给 2–3 条「接着可以问」的建议。

为什么要有
----------
市场对标（携程 TripGenie 的产品复盘明确写：一轮问答结束后提供**关联问题推荐**，
引导多轮对话，"人均对话轮次得到大幅提升"）。这一条的价值不在信息量，而在**降低
下一句的组织成本** —— 用户不用自己想「我还能问什么」，点一下就能续上。

设计取舍
--------
- **模型为主、模板兜底**（2026-10-09 用户反馈「改的自由点儿，别死板」）：plan / answer /
  guide 三条路本来就在调模型，于是让**同一次调用**顺手写 2–4 条建议（零额外延迟），
  建议栏因此跟着这一轮的真实内容走，而不是永远那几句。本模块的 i18n 模板退居**兜底**
  —— 模型没给、格式不合法、或 realtime（这条本来就不调模型）时才用它。
  模型给的建议必须先过 `clean_chips`（去空 / 去重 / 去复读用户原话 / 限长）。
- **只做「下一步」**：不给完整问句清单，2–3 条足够；行程那条刻意演示**迭代句式**
  （「把第 3 天放宽」「住宿换便宜点」），顺带教会用户新上线的改稿能力。
- 寒暄（guide）不做：它已经有 `starters`（建议提问），两套会打架。
"""

from __future__ import annotations

import re
from collections import Counter

from .i18n import DEFAULT as DEFAULT_LANGUAGE
from .i18n import t

# decision → i18n key（无地点上下文版 / 有地点上下文版）。`knowledge` 走 answer 那套文案；
# `social` 刻意不映射（见模块注释）。
_KEY_BY_DECISION = {
    "plan": ("nq.plan", "nq.plan"),
    "answer": ("nq.answer", "nq.answer_place"),
    "realtime": ("nq.realtime", "nq.realtime_place"),
}

# 兴趣标签（`slots.interests`，见 `app/slots.py::_INTEREST_LEXICON`）→ 那条「更贴兴趣」的追问。
# 只收词典里真有的标签；没命中就一条都不换（宁可用通用文案，也别猜用户喜欢什么）。
_INTEREST_KEY = {
    "food": "nq.ctx_food",
    "history": "nq.ctx_history",
    "nature": "nq.ctx_nature",
    "shopping": "nq.ctx_shopping",
    "nightlife": "nq.ctx_nightlife",
    "culture": "nq.ctx_culture",
    "photo": "nq.ctx_photo",
    "family": "nq.ctx_family_place",
}

# **话题 → 整套模板**（2026-10-06 菜品探针实测）：建议栏在饮食话题下连问四轮一字不差。
# 按**这一轮的主体**认话题（主体是「素食者」「清真」「川菜」→ 饮食），命中就整套换成
# 同话题的三条。判据是**子串命中**（主体就是几个字，分词反而漏），按表里顺序取第一个
# 命中的话题 —— 顺序即优先级，「清真」既像饮食又像宗教，落在 food 上更贴。
_TOPIC_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "food",
        (
            # ⚠️ 不写裸「馆」：「博物馆周一闭馆吗」的「馆」会把整句抢到饮食话题
            #    （真 bug 2026-10-06：问闭馆时间，建议栏给的是「有哪些当地菜」）。
            "素食", "斋", "清真", "菜", "辣", "清淡", "餐", "餐馆", "面馆", "小吃", "夜市", "早餐", "早点",
            "点菜", "菜单", "口味", "火锅", "烤鸭", "饺子", "豆腐", "米线", "小笼包", "面条",
            "过敏", "忌口",
            "自来水", "饮用水", "冰块", "打包", "筷子",
            "food", "dish", "dishes", "cuisine", "menu", "spicy", "vegetarian", "vegan",
            "halal", "tap water", "breakfast", "street food", "allerg", "pork", "peanut",
            "料理", "辛", "ベジタリアン", "ハラール", "アレルギ", "水道水",
            "음식", "채식", "할랄", "매운", "수돗물",
        ),
    ),
    (
        "transport",
        (
            # ⚠️ 不收 `didi`：它归 `apps`（问「用什么打车软件」想听的是装哪个应用，
            #    不是地铁换乘）。真 bug（2026-10-06）：en 问 didi，建议栏给的是地铁三连。
            "地铁", "公交", "巴士", "打车", "出租", "网约车", "高铁", "动车", "火车", "单车",
            "机场", "班次", "高峰", "人流", "拥堵", "换乘", "末班车",
            "metro", "subway", "taxi", "train", "rush hour", "busiest",
            "地下鉄", "電車", "タクシー", "ラッシュ", "混雑",
            "지하철", "버스", "택시", "러시아워", "혼잡",
        ),
    ),
    # ⚠️ `shop` 必须排在 `pay` **之前**：「退税」两个话题都收，而问退税的人想听的是
    #    退税流程 / 伴手礼，不是「还要不要带现金」。
    (
        "shop",
        (
            "退税", "免税", "免税店", "即买即退", "砍价", "还价", "讲价", "批发市场", "小商品",
            "假货", "仿品", "山寨", "发票", "小票", "维权", "12315", "伴手礼", "特产", "土特产",
            "老字号", "茶叶", "丝绸", "购物", "买东西",
            "tax refund", "duty free", "duty-free", "bargain", "haggle", "counterfeit",
            "fake", "receipt", "invoice", "souvenir", "shopping", "silk",
            "免税", "還付", "税金還付", "値切", "偽物", "レシート", "お土産", "買い物",
            "환급", "면세", "흥정", "가짜", "영수증", "기념품", "쇼핑",
        ),
    ),
    (
        "pay",
        (
            "支付", "银行卡", "信用卡", "现金", "支付宝", "微信", "外卡", "退税", "汇率", "取现",
            "pay", "payment", "card", "cash", "alipay", "wechat", "refund",
            "決済", "支払", "キャッシュレス",
            "결제", "지불", "카드", "현금",
        ),
    ),
    # ⚠️ `apps` 必须排在 `pay` **之后**：「微信」两个话题都收，而问「微信支付」的人
    #    想听的是绑卡与手续费，不是「境外应用用不了怎么办」。
    (
        "apps",
        (
            "应用", "软件", "App", "谷歌", "Google", "地图软件", "导航", "高德", "百度地图",
            "打车软件", "滴滴", "扫码", "二维码", "小程序", "境外应用", "用不了", "使不了",
            "app", "apps", "google", "whatsapp", "instagram", "blocked", "vpn", "didi",
            "amap", "navigation", "qr", "scan", "wechat", "mini-program",
            "アプリ", "地図", "ナビ", "グーグル",
            "앱", "어플", "지도", "구글", "디디",
        ),
    ),
    # ⚠️ `safety` 必须排在 `visa` **之前**：「护照丢了」两个话题都收，而问丢护照的人
    #    想听的是报案 → 使馆 → 补办这条链，不是签证材料清单。
    (
        "safety",
        (
            "丢", "丢失", "遗失", "被盗", "被偷", "扒手", "小偷", "骗", "骗局", "套路", "宰客",
            "黑车", "报警", "报案", "警察", "急救", "救护车", "治安", "危险", "使馆", "领事",
            "补办", "旅行证", "紧急电话", "110", "119", "120",
            # ⚠️ 必须含 `lose` / `lost` /「なくした」/「잃어버」：只写 `passport` 会被
            #    `visa` 抢走（真 bug 2026-10-06：丢护照的人拿到的是签证材料三连）。
            "safe", "safety", "scam", "pickpocket", "stolen", "theft", "police", "ambulance",
            # ⚠️ 不写裸 `lose` / `lost`：判据是**子串命中**，`closed` 里有 `lose`
            #    （真 bug 2026-10-06：「closed on monday」被判成安全话题）。
            "embassy", "consulate", "emergency", "dangerous",
            "lose my", "lost my", "lose it", "lost it", "losing",
            "詐欺", "盗", "紛失", "警察", "救急", "大使館", "危険", "なくした", "パスポート",
            "사기", "도난", "분실", "경찰", "위험", "대사관", "잃어버", "여권",
        ),
    ),
    (
        "visa",
        (
            "签证", "免签", "入境", "海关", "护照", "过境",
            "visa", "transit", "immigration", "passport",
            "ビザ", "入国",
            "비자", "입국",
        ),
    ),
    (
        "net",
        (
            "上网", "流量", "手机卡", "SIM", "Wi-Fi", "wifi", "eSIM", "网络", "信号", "漫游", "热点",
            "sim card", "esim", "wifi", "internet", "roaming",
            "ネット", "インターネット",
            "유심", "와이파이", "인터넷",
        ),
    ),
    (
        "stay",
        (
            "酒店", "住宿", "民宿", "入住", "青旅", "宾馆", "房源",
            "hotel", "hostel", "accommodation", "airbnb", "check-in",
            "ホテル", "宿泊",
            "호텔", "숙소",
        ),
    ),
    (
        "health",
        (
            "医院", "看病", "挂号", "门诊", "急诊", "急救", "医生", "诊所", "药店", "药房",
            "买药", "处方", "抗生素", "常备药", "医疗保险", "保险", "不舒服", "发烧", "腹泻",
            "拉肚子", "体检",
            "hospital", "clinic", "doctor", "pharmacy", "medicine", "prescription",
            "emergency", "ambulance", "insurance", "fever", "diarrhea", "sick",
            "病院", "医院", "薬局", "薬", "医者", "救急", "保険", "病気", "発熱", "下痢",
            "병원", "약국", "약", "의사", "응급", "보험", "아프", "열", "설사",
        ),
    ),
    # ⚠️ `holiday` 必须排在 `sight` **之前**：「故宫周一闭馆吗」问的是节假日规则，
    #    落到 sight 只会给「还有哪些景点」。`holiday` 必须排在 `transport` **之后**：
    #    「地铁高峰」的「高峰」归交通，所以这里只用「人多 / 人挤」这类整词。
    (
        "holiday",
        (
            "黄金周", "国庆", "十一", "春节", "过年", "元旦", "五一", "清明", "端午", "中秋",
            "法定", "节假日", "放假", "调休", "闭馆", "周一", "人多", "人挤", "人山人海",
            "错峰", "避开人流", "放票", "抢票", "售罄",
            "golden week", "national day", "chinese new year", "spring festival",
            "lunar new year", "public holiday", "holiday", "crowded", "closed on monday",
            "monday closure", "off-peak", "sold out",
            "ゴールデンウィーク", "国慶節", "春節", "連休", "休館", "混雑", "完売",
            "국경절", "설날", "연휴", "휴관", "혼잡", "매진",
        ),
    ),
    (
        "sight",
        (
            "景点", "门票", "博物馆", "预约", "开放", "景区", "展览", "公园", "寺庙",
            "ticket", "museum", "attraction", "opening", "booking", "exhibit",
            "観光", "チケット", "博物館",
            "명소", "티켓", "박물관",
        ),
    ),
    (
        "weather",
        (
            "天气", "气温", "冷不冷", "热不热", "下雨", "雨季", "梅雨", "台风", "雾霾", "空气",
            "污染", "AQI", "穿什么", "带什么衣服", "几月", "什么时候去", "最佳季节",
            "淡季", "旺季", "季节",
            "weather", "how cold", "how hot", "rain", "rainy", "typhoon", "air quality",
            "pollution", "aqi", "what to wear", "best time", "best month",
            "天気", "気温", "台風", "空気", "大気汚染", "服装", "ベストシーズン",
            "날씨", "미세먼지", "대기오염", "옷", "태풍",
        ),
    ),
    (
        "language",
        (
            "英文", "英语", "说中文", "讲中文", "普通话", "语言不通", "沟通", "翻译",
            "路牌", "标识", "问路", "指路", "怎么说",
            "english", "speak english", "language", "translate", "translator", "translation",
            "signage", "phrase", "mandarin",
            "英語", "中国語", "翻訳", "言葉", "案内",
            "영어", "중국어", "번역", "언어",
        ),
    ),
    # 礼仪与生活惯例放**最后**：它的关键词（「禁忌」「习俗」）也常出现在文化 / 宗教类问句里，
    # 让更具体的主题先接。
    (
        "manner",
        (
            "小费", "服务费", "礼仪", "习俗", "禁忌", "敬酒", "劝酒", "公筷", "抽烟", "吸烟",
            "洗手间", "厕所", "卫生间", "插座", "电压", "转换插头", "变压器", "排队", "拍照",
            "tipping", "tip", "etiquette", "taboo", "toilet", "restroom", "adapter",
            "voltage", "power outlet", "plug",
            "チップ", "マナー", "エチケット", "タブー", "乾杯", "喫煙", "トイレ", "お手洗い",
            "コンセント", "プラグ", "電圧",
            "팁", "매너", "에티켓", "금기", "건배", "흡연", "화장실", "콘센트", "전압",
        ),
    ),
)

MAX_QUESTIONS = 3
_SEP = "|"


def _render(key: str, language: str, **kw) -> str:
    """取一条 i18n 文案；**缺这条就返回空串**（不是返回 key 本身）。"""
    raw = t(key, language, **kw)
    return "" if raw == key else raw


def _render_items(raw: str, place: str) -> list[str]:
    """把一条 `|` 分隔的 i18n 文案切成若干条建议（去掉空项、收掉悬空介词）。"""
    return [_tidy(s, place) for s in (x.strip() for x in (raw or "").split(_SEP)) if s]


def _norm(s: str) -> str:
    """比较建议 / 用户原话是否「同一条」用：折叠空白 + 忽略大小写。"""
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


# 模型给的一条建议能有多长：长于此判为「把整段回答塞进来了」，丢掉。
_CHIP_MAX_CHARS = 60


def _is_repeat(a: str, b: str) -> bool:
    """a 是不是「几乎就是」b（用户刚说的那句）。

    用**字符多重集重叠率**判，而不是精确相等 —— 实测用户打「住宿换便宜一点的」、
    建议栏回「住宿换成便宜一点的」，只差一个字，精确比较抓不住（真 bug 2026-10-09
    探针实测：那条被原样又推了一次，看起来就像「点了没反应」）。
    只对**足够长**的原话做，短问候不参与（否则「你好」会把「你好，帮我排行程」误伤）。
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) < 5:
        return False
    common = sum((Counter(short) & Counter(long_)).values())
    return common / len(short) >= 0.8


def clean_chips(
    items: list[str] | None, *, avoid: list[str] | None = None, limit: int = MAX_QUESTIONS
) -> list[str]:
    """把**模型给的**建议洗净：去空 / 去重复 / 去复读用户原话 / 限长限量。

    模型自由发挥就必须过这一道：它会写空串、写重复、把用户刚说的那句原样还回来、
    偶尔塞一整段话进来。洗完为空 = 调用方退回模板（见 `next_questions`）。
    """
    avoid = [a for a in (avoid or []) if a]
    seen = {_norm(a) for a in avoid}
    out: list[str] = []
    for raw in items or []:
        raw_s = str(raw or "")
        if "\n" in raw_s or "\r" in raw_s:
            continue  # 一整段话被塞进来了（建议栏是一行 chip，不是段落）
        s = re.sub(r"\s+", " ", raw_s.strip())
        if not s or len(s) > _CHIP_MAX_CHARS:
            continue
        if _norm(s) in seen:
            continue
        if any(_is_repeat(s, a) for a in avoid):
            continue
        out.append(s)
        seen.add(_norm(s))  # 输入内部也要去重：模型很爱把同一条写两遍
        if len(out) >= limit:
            break
    return out


def _context(slots: dict | None) -> tuple[str, int]:
    """从会话槽位取个性化参数：`place`（目的地）与 `day`（这一版稿子的天数）。

    真 bug / 收遗留（2026-10-06）：模板原先写死「把第 3 天…」「附近还有哪些值得去」，
    同一会话连问几轮，建议栏一字不差 —— 用户看不出系统记住了什么。
    `day` 取槽位里的天数（迭代改稿后天数会跟着稿子走，见 `graph.output_node`）；
    天数缺失或 < 2 时退回 3（「把第 1 天安排得轻松一点」是句废话）。
    """
    slots = slots or {}
    place = str(slots.get("destination") or "").strip()
    day = 3
    days = slots.get("days")
    if isinstance(days, (int, float)) and int(days) >= 2:
        day = int(days)
    return place, day


def _ctx_plan(slots: dict, language: str, day: int) -> str:
    """行程稿的**同行人**个性化：带了孩子 / 长者，第一条就换成照顾节奏的那句。

    为什么是「带孩子/带长者」而不是「人数」：人数不影响「怎么改这一版稿」，
    同行人构成才影响 —— 这也是行程会用到的 `has_children` / `has_elder` 两个槽位。
    孩子优先于长者：两者同时在（三代同游）时，儿童节奏是更硬的约束。
    """
    if slots.get("has_children"):
        kids = _render("nq.ctx_plan_kids", language, day=day)
        if kids:
            return kids
    if slots.get("has_elder"):
        elder = _render("nq.ctx_plan_elder", language, day=day)
        if elder:
            return elder
    return ""


def _topic_key(subject: str) -> str:
    """这一轮主体属于哪个话题；认不出来就返回空串（用通用模板）。"""
    text = (subject or "").lower()
    if not text:
        return ""
    for topic, words in _TOPIC_KEYS:
        if any(w.lower() in text for w in words):
            return f"nq.topic_{topic}"
    return ""


def _tidy(item: str, place: str = "") -> str:
    """收掉 `{place}` 为空时留下的悬空介词 / 助词。

    中文无所谓（「有哪些值得试的当地菜」本来就通顺）；英文的地名在**句末**，空着多一个
    dangling preposition（「…should I try in」）；日韩的地名在**句首**，空着多一个裸助词
    （「**で**食べるべき郷土料理は？」「**에서** 꼭 먹어봐야 할…」）。
    只在句末 / 分隔符前 / 句首收，不动句中成分。
    """
    out = re.sub(
        r"\s+(?:in|near|at|for|of|on|to)\s*(?=$|[|?！!。.])", "", item, flags=re.IGNORECASE
    )
    if not place:
        out = re.sub(r"^\s*(?:에서|에|의|で|の|in|near|at)\s*", "", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()


def _ctx_answer(slots: dict, language: str, place: str) -> str:
    """常识答复的**兴趣**个性化：把「附近还有什么」换成一条更贴兴趣的追问。

    只在**有目的地**时启用 —— 兴趣句不带地名时，拼出来的标签会缺主语
    （「有哪些必吃的本地菜」读者不知道问的是哪），不如保留通用文案。
    兴趣按 `_INTEREST_LEXICON` 的顺序取**第一个**命中的（同句多个兴趣时取最靠前的那个 =
    词典里更"主"的那个），避免建议栏堆满同一会话的兴趣清单。
    """
    if not place:
        return ""
    for label in slots.get("interests") or []:
        key = _INTEREST_KEY.get(str(label))
        if key:
            rendered = _render(key, language, place=place)
            if rendered:
                return rendered
    return ""


def next_questions(
    *,
    decision: str,
    language: str = DEFAULT_LANGUAGE,
    slots: dict | None = None,
    subject: str = "",
    avoid: list[str] | None = None,
    offset: int = 0,
) -> list[str]:
    """返回这一轮之后「接着可以问」的建议（0–3 条）。

    `slots` 用会话槽位做**个性化**：
    - 有目的地 → 换用带 `{place}` 的那套文案；
    - 行程稿 → 天数取这一版稿子的天数（见 `_context`），带小孩/带长者时换掉第一条；
    - 常识答复 → 会话兴趣命中时，把最后一条泛推荐换成更贴兴趣的追问。

    `avoid` / `offset` 解决的是「建议栏像死的」（2026-10-09 用户反馈）：
    - `avoid`：**刚做过的那条**（通常是用户刚点的那一句）不再原样出现 —— 点完还看到
      同一条，用户会以为点了没反应。只在摘掉后仍剩 ≥2 条时生效，不掏空建议栏。
    - `offset`：行程那套并入备用池（`nq.plan_extra`）后**按轮次轮换窗口**。只有 3 条
      模板时，同一会话连出几版稿建议栏一字不差；轮换后相邻两版肉眼可辨地不同。
    """
    keys = _KEY_BY_DECISION.get(decision or "")
    if not keys:
        return []
    plain_key, place_key = keys
    slots = slots or {}
    place, day = _context(slots)
    key = place_key if (place and place_key != plain_key) else plain_key
    # 话题整套替换：认得出话题就换掉**整套**（饮食话题下四轮重复同一模板，就是缺这一步）。
    topic = _topic_key(subject)
    key = topic or key
    raw = t(key, language, place=place, day=day)
    if raw == key:  # i18n 缺这条 → 宁可不给，也不要露出 key
        return []
    items = _render_items(raw, place)

    # 行程稿：并入备用池并按轮次轮换。话题套（topic）不轮换 —— 它是按这一轮主体选出来的，
    # 轮换会把最贴题的那条转走。
    if decision == "plan" and not topic:
        extra_raw = t("nq.plan_extra", language, place=place, day=day)
        if extra_raw and extra_raw != "nq.plan_extra":
            pool = items + _render_items(extra_raw, place)
            if offset and len(pool) > MAX_QUESTIONS:
                k = offset % (len(pool) - MAX_QUESTIONS + 1)
                pool = pool[k:] + pool[:k]
            items = pool

    seen = {_norm(a) for a in (avoid or []) if a}
    if seen:
        kept = [x for x in items if _norm(x) not in seen]
        if len(kept) >= 2:
            items = kept
    items = items[:MAX_QUESTIONS]

    # 上下文个性化：只换**一条**，位置见上面 i18n 的注释。
    if decision == "plan":
        ctx = _ctx_plan(slots, language, day)
        if ctx and items:
            items = [ctx] + items[1:]
    elif decision == "answer" and not topic:
        # 话题已整套替换时不再叠兴趣替换：两套都在改同一条，叠上去只会互相抵消。
        ctx = _ctx_answer(slots, language, place)
        if ctx:
            items = (items[:-1] + [ctx]) if len(items) >= 2 else ([ctx] + items)

    return items[:MAX_QUESTIONS]
