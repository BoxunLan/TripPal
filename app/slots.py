"""确定性槽位抽取。

刻意的设计选择：**数字类槽位（天数、预算、人数）不用 LLM 抽**，用 routes.yaml 里的
正则规则抽。原因是这些值直接进预算校验的算术，LLM 抽错一位数会让校验结论反向。
LLM 只负责补空缺（例如从「厦门玩 5 天」补出季节偏好）。

词典（destinations）与正则都在 routes.yaml，图节点里不出现任何地名或场景文案。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .config import Settings
from .i18n import DEFAULT as DEFAULT_LANGUAGE
from .i18n import contains, t

SLOT_ORDER = ["destination", "destination_country", "date_range", "budget", "party"]

CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# 必须容忍复合数词（十五 / 二十 / 二十三）：只写单字类会让「十五个人」只吃到「十」。
CN_NUM_RE = r"[一两二三四五六七八九十]+"
EN_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
# 英文里的「人数/同行人」措辞，和中文的「人/大人/成人」等价
EN_PARTY_RE = re.compile(r"people|persons?|adults?|pax|travell?ers?", re.IGNORECASE)
EN_CHILD_RE = re.compile(r"\b(?:kid|kids|child|children|toddler|baby|infant)s?\b", re.IGNORECASE)
EN_ELDER_RE = re.compile(r"\b(?:elderly|seniors?|grandparents?|grandpa|grandma)\b", re.IGNORECASE)

# 「两人/一人/一家」三类的多语言措辞。日韩不共用拉丁词形，必须显式列出，
# 否则会掉进「未识别 → 按 2 大 1 小处理」的兜底分支：夫妻会被算成三口之家。
COUPLE_PHRASES = {
    "两人", "二人", "双人", "俩", "两个", "情侣", "恋人", "夫妻", "夫妇", "两口子", "蜜月",
    "couple", "two people", "two adults", "honeymoon",
    "夫婦", "カップル", "恋人", "新婚", "부부", "커플", "신혼",
}
SOLO_PHRASES = {
    "独行", "自己一个", "一个人", "单个", "单人", "独自", "独自一人", "我自己", "就我自己",
    "solo", "alone", "by myself",
    "一人", "ひとり", "혼자", "혼자서",
}
# 代词式独行（「就我自己」「只有我」「光我一个」）。
# **不能**往上面的集合里加字符串「只有我」：那是子串匹配，会命中意思完全相反的
# 「只有我们两个人」「只有我和老婆」。后面那个负向断言就是用来挡这类句子的 ——
# 「只有我一个人」之所以不放进来，是因为「一个人」那条已经覆盖了它。
_SOLO_PRONOUN_RE = re.compile(
    r"(?:只有|就|光|剩下|仅)\s*我(?!\s*[们俩两和跟带]|\s*[一二两三四五1-9])"
)
FAMILY_PHRASES = {"一家人", "一家", "family", "家族", "가족"}
# 日韩的「孩子/长者」：`_extract_party` 的兜底分支会把未识别措辞当亲子，
# 但长者必须单独认出，否则「父母同行」这类请求会漏掉节奏压降。
JA_KO_CHILD_RE = re.compile(r"子供|子ども|こども|キッズ|아이|어린이|자녀")
JA_KO_ELDER_RE = re.compile(r"高齢|シニア|ご年配|お年寄り|어르신|노인|부모님")

# 年龄区间：抽到具体岁数时要落进哪一类。没有上限时「70 岁老人」会被判成儿童。
CHILD_AGE_MAX = 17  # 未成年人
ELDER_AGE_MIN = 60  # 长者（安全叠加层会给节奏压降）


def _num(token: str) -> int | None:
    token = (token or "").strip().lower()
    if token.isdigit():
        return int(token)
    if token in EN_NUM:
        return EN_NUM[token]
    return _cn_num(token)


def _cn_num(token: str) -> int | None:
    """中文数词 → 整数，支持「十五 / 二十 / 二十三」这类复合写法。

    「我们三个人」曾经完全抽不到人数（旧正则只认阿拉伯数字），于是 party 槽位落空、
    clarify 多追问一轮 —— 明明句子里写得清清楚楚。中文口语里数词比阿拉伯数字更常见。
    """
    if not token:
        return None
    if "十" not in token:
        # 单字（三 / 两）或连写（二三，按逐字相加处理）
        if all(ch in CN_NUM for ch in token):
            return sum(CN_NUM[ch] for ch in token) or None
        return CN_NUM.get(token)
    left, _, right = token.partition("十")
    if left and left not in CN_NUM:
        return None
    if right and right not in CN_NUM:
        return None
    tens = CN_NUM[left] if left else 1
    ones = CN_NUM[right] if right else 0
    return tens * 10 + ones


def _is_nights_pattern(pat: str) -> bool:
    """「N 晚」要换算成 N+1 天。中/英/日/韩都要认，否则英文的 nights 或日文的「泊」会少算一天。"""
    return "晚" in pat or "泊" in pat or "박" in pat or "night" in pat.lower()


def _is_week_pattern(pat: str) -> bool:
    """「一周 / 两星期 / 一个礼拜」要换算成 N×7 天。

    中文口语里「玩一周」比「玩 7 天」更常见；只补数字正则不够 ——
    周不是天数单位，必须乘 7，否则「一周」会被读成 1 天行程。
    """
    return any(k in pat for k in ("周", "週", "星期", "礼拜"))


def _is_per_day_pattern(pat: str) -> bool:
    """「每天/每日/人均」或 per day / daily / per person / 1日あたり / 1일 → 人均每日预算。"""
    p = pat.lower()
    return any(k in pat for k in ("每天", "每日", "人均", "1日", "一日", "1일", "하루")) or any(
        k in p for k in ("per day", "daily", "per person", "a day", "/day", "p/d")
    )


# ---------------------------------------------------------------- 目的地词典
# 入境语境（产品主题＝外国人来华）。**目的地是中国，国籍/出发地不是目的地。**
# 少了这条，「我是美国人，想去北京玩 5 天，需要签证吗」会被解析成
# destination_country=美国 → 检索到的是**美国出境签证**知识，
# 也就是「对外宣称来华、手上全是出境数据」那个漏点在检索层的具体表现。
# 判据只看「来/去/在 + 中国」这类位移或停留措辞，**不认裸「中国」** ——
# 「哪些国家对中国免签」里的中国是签发护照的国家，不能算目的地。
_INBOUND_CUE = re.compile(
    r"来华|来中国|到中国|去中国|在中国|中国入境|入境中国|入境旅游|入境游"
    r"|visit\s+china|into\s+china|enter(?:ing)?\s+china",
    re.IGNORECASE,
)


def gazetteer_hits(message: str, settings: Settings) -> list[tuple[str, dict[str, Any], str]]:
    """命中 destinations 词典的条目，按名字长度降序（长名优先，避免短名吃掉同前缀的长名）。

    返回 `(规范名, 元数据, 原话里的写法)`。第三个值是**用户实际打出来的那个串**
    （可能是别名 "Xiamen"、韩语 "샤먼"），追问回显要用它 —— 否则英文用户会看到
    中文的「厦门」，等于把输出语言的边界漏了个洞。检索依旧用规范名（中文）。
    """
    table: dict[str, Any] = settings.routes.get("destinations", {}) or {}
    hits: list[tuple[str, dict[str, Any], str]] = []
    for name, meta in table.items():
        meta = dict(meta or {})
        names = [name] + list(meta.get("aliases", []) or [])
        matched = [n for n in names if contains(message, n)]
        if matched:
            # 同一条目多个写法都命中时取最长的（最具体）
            hits.append((name, meta, max(matched, key=len)))
    hits.sort(key=lambda x: -len(x[0]))
    return hits


def resolve_destinations(
    message: str, settings: Settings
) -> tuple[str | None, str | None, list[str], str | None]:
    """返回 (destination, destination_country, 全部命中的规范名, 目的地原话写法)。

    `origin_only` 的条目（目前只有「中国」）表示**出发地/国籍语境**，不是要去的地方：
    「哪些国家对中国免签」里的中国是签发护照的国家，不排除它 destination 会变成中国。
    厦门/成都等城市的 country 字段不受影响 —— 那是从城市条目上取的。

    两条收紧规则，都是「宣称来华、数据全是出境」这个漏点漏到检索层之后补的：
    ① 城市的所属国**压过**句中单独提到的国家 —— 「我是美国人，想去北京玩 5 天」里
       美国是国籍，目的地是北京（中国）。不这么判，destination_country 会落成美国，
       于是去检索美国出境签证知识。
    ② `destination_aliases` 只留与目的地同国的名字 —— 别名会当检索 token 用，
       留着「美国」会把美国条目一起召回，来华问题又被出境内容答了。
    ③ 出现「来/去/在 + 中国」这类位移措辞时，即使没点城市也把目的地定成中国。
    """
    hits = [h for h in gazetteer_hits(message, settings) if not h[1].get("origin_only")]
    names = [n for n, _, _ in hits]

    def _same_country(cc: str | None) -> list[str]:
        same = [n for n, m, _ in hits if cc and m.get("country") == cc]
        return same or names

    if _INBOUND_CUE.search(message or ""):
        # 入境语境：把「中国」扶正成目的地，把国籍/出发地（美国 / 韩国…）从目的地里剔掉。
        cn = [h for h in hits if h[1].get("country") == "中国"]
        if cn:
            city = next(((n, s) for n, m, s in cn if m.get("type") in {"city", "region"}), None)
            cn_names = [n for n, _, _ in cn]
            if city is not None:
                return city[0], "中国", cn_names, city[1]
            surface = next((s for _, m, s in cn if m.get("country")), None) or "中国"
            return "中国", "中国", cn_names, surface
        # 只说了「来华 / 来中国」没点具体城市：目的地按中国算，
        # 让检索至少能召回入境政策与在华实用知识，而不是空手。
        return "中国", "中国", ["中国"], "中国"

    city_hit = next((h for h in hits if h[1].get("type") in {"city", "region"}), None)
    if city_hit is not None:
        name, meta, surface = city_hit
        cc = meta.get("country")
        return name, cc, _same_country(cc), surface
    country = next((m.get("country") for _, m, _ in hits if m.get("type") == "country"), None)
    if country is None:
        country = next((m.get("country") for _, m, _ in hits if m.get("country")), None)
    surface = next((s for _, m, s in hits if m.get("country")), None)
    return (country, country, names, surface)


# ---------------------------------------------------------------- 各槽位抽取
def _extract_date_range(message: str, patterns: list[str]) -> tuple[int | None, str | None, str | None]:
    """返回 (天数, date_range 文本, 出发日期)。"""
    days: int | None = None
    date_text: str | None = None
    start_date: str | None = None
    # 整日期（2026年10月25日）只解析成 start_date，**不能提前返回**：
    # 「2026年10月25日去厦门 3 天」里显式写的天数也要抽到，否则行程天数整条丢失，
    # 后面按 date_range 缺槽反问 —— 而用户明明写了「3 天」。
    if patterns:
        m = re.search(patterns[0], message)
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                start_date = f"{y:04d}-{mo:02d}-{d:02d}"
                date_text = m.group(0)
            except (IndexError, ValueError):
                pass
    for pat in patterns[1:]:
        m = re.search(pat, message)
        if not m:
            continue
        n = _num(m.group(1))
        if n is None:
            continue
        if _is_week_pattern(pat):
            days, date_text = n * 7, f"{n} 周（约 {n * 7} 天）"
        elif _is_nights_pattern(pat):
            days, date_text = n + 1, f"{n} 晚（约 {n + 1} 天）"
        else:
            days, date_text = n, f"{n} 天"
        break
    return days, date_text, start_date


# 预算的量级后缀：中文 万/萬/千/百/佰，韩文 만（200만 = 200 万）。日文用汉字「万」，同中文。
# 「百」必须一起进表：「预算三百」「人均五百」是很常见的口语写法，漏了它会读成 3 元 / 5 元。
_BUDGET_UNITS: dict[str, float] = {
    "万": 10000.0,
    "萬": 10000.0,
    "千": 1000.0,
    "百": 100.0,
    "佰": 100.0,
    "만": 10000.0,
}

# 中文量级字符。出现在金额表达式里就说明要整体解析（三百万 / 一万五 / 两千五百），
# 不能拆成「数词 × 单个后缀」——那会把「三百万」读成 300。
_CN_MAG_RE = re.compile(r"[百仟千萬万佰億亿]")
_CN_AMOUNT_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_AMOUNT_SMALL = {"十": 10, "百": 100, "千": 1000, "仟": 1000}
_CN_AMOUNT_BIG = {"万": 10000, "萬": 10000, "亿": 100000000, "億": 100000000}


def _cn_amount(raw: str) -> float | None:
    """中文金额/数量 → 数值，支持复合量级与「一万五」这类省略式。

    `_cn_num` 只认「十」体系（十五 / 二十三），够不到「三百万 / 一万五 / 两千五百」。
    预算是要进算术的槽位（超没超预算由它判定），把「三百万」读成 300 比读不到更危险 ——
    缺值会触发追问，错值会静默给出相反结论。所以这里单独实现：

      三百万 → 3000000   一万五 → 15000（末尾裸数词取最近单位的 1/10）
      两千五百 → 2500     三百五 → 350     一百零五 → 105（「零」之后不按省略式读）

    出现任何不认识的字符（如「万豪酒店」的「豪」）一律返回 None —— 宁可不填也不猜。
    """
    s = re.sub(r"[\s,，]|元|块|¥|￥|\$", "", raw or "")
    if not s:
        return None
    # 裸量级字符不成金额（「百」「千」）；「十」例外（「十五」的十本身就是 1 个十）。
    if len(s) == 1 and s in ("百", "仟", "千"):
        return None
    total = 0.0        # 万/亿 级以上的累计
    section = 0.0      # 当前「万以下」小节的累计
    number = 0.0       # 待并入的数词
    last_unit = 0.0    # 最近一次用到的单位（省略式要用）
    pending = False    # 是否有未并入的数词
    zero_seen = False  # 「零」之后不能再按省略式读（一百零五 ≠ 150）
    run = False        # 是否正在读一串阿拉伯数字
    for ch in s:
        if ch.isdigit():
            number = number * 10 + int(ch) if run else float(ch)
            run = True
            pending = True
            continue
        run = False
        if ch in _CN_AMOUNT_DIGITS:
            number = float(_CN_AMOUNT_DIGITS[ch])
            pending = True
            if ch in ("零", "〇"):
                zero_seen = True
            continue
        if ch in _CN_AMOUNT_SMALL:
            u = float(_CN_AMOUNT_SMALL[ch])
            if not pending:
                number = 1.0  # 「十五」的「十」前面没有数词
            section += number * u
            number = 0.0
            pending = False
            last_unit = u
            zero_seen = False
            continue
        if ch in _CN_AMOUNT_BIG:
            u = float(_CN_AMOUNT_BIG[ch])
            section = (section + number) * u
            total += section
            section = 0.0
            number = 0.0
            pending = False
            last_unit = u
            zero_seen = False
            continue
        return None
    if pending and number and not zero_seen and last_unit >= 100:
        section += number * last_unit / 10.0
        number = 0.0
    return (total + section + number) or None


def _amount(token: str, matched: str, tail: str) -> float | None:
    """把一条预算规则捕获到的文本折成数值。

    两条路径：表达式自带量级（三百万 / 一万五 / 2万）→ 交给 _cn_amount 整体解析；
    否则（8000 / 2k / 12）→ 数词 × _budget_scale 的量级后缀。
    """
    tok = re.sub(r"(?:元|块)$", "", (token or "").strip()).strip()
    if _CN_MAG_RE.search(tok):
        return _cn_amount(tok)
    n = _num(tok)
    if n is None:
        return None
    return float(n) * _budget_scale(matched, tail)


def _budget_scale(matched: str, tail: str) -> float:
    """把「2万 / 8千 / 200만 / 2k」的量级后缀折成倍数。

    中文口语里「预算 2 万」远比「预算 20000」常见，只吃数字会把 2 万读成 2 元，
    之后 budget 校验与超预算判定会整体失真（本项目不关心币种，关心的是与预算的比例）。
    后缀可能被正则吃到（`预算 2万` → 落在 matched 末尾），也可能留在匹配之外
    （`预算 2 万` → 落在紧随其后的字符里），两处都要看。

    判定必须是**紧邻**：只允许中间夹空白。若放宽成「后面两个字符里出现过 万」，
    `预算 8000，万豪酒店` 这种句子会被判成 8000 万。
    """
    head = (matched or "").rstrip()
    rest = (tail or "").lstrip()
    unit = ""
    if head and head[-1] in _BUDGET_UNITS:
        unit = head[-1]
    elif rest and rest[0] in _BUDGET_UNITS:
        unit = rest[0]
    if unit:
        return _BUDGET_UNITS[unit]
    # 英文口语的 k 也要求紧贴数字（`2k` 可以，`8000 Xiamen` 里的 X 不行）
    if re.search(r"\d[kK](?![A-Za-z])", f"{matched or ''}{tail or ''}"):
        return 1000.0
    return 1.0


def _extract_budget(message: str, patterns: list[str]) -> tuple[float | None, float | None]:
    """返回 (总预算, 人均每日预算)。"""
    total: float | None = None
    daily: float | None = None
    for pat in patterns:
        m = re.search(pat, message)
        if not m:
            continue
        # 中文数词与复合量级都要能进算术：「预算两万」「预算三百万」「预算一万五」
        # 直接 float() 会抛错被静默跳过，于是中文口语写的预算被整条丢掉。
        val = _amount(m.group(1), m.group(0), message[m.end() : m.end() + 2])
        if val is None:
            continue
        if _is_per_day_pattern(pat):
            if daily is None:
                daily = val
        elif total is None:
            total = val
    return total, daily


# ---------------------------------------------------------------- 同行人：组合式语法
# 旧做法是 routes.yaml 里把每种说法当**整串短语**枚举（「(\d+)\s*(?:个|名)?\s*人」、
# 「(\d+)\s*个大人」、「(\d+)\s*名成人」），要求「数词 + 量词 + 身份词」严丝合缝地相邻。
# 只要中间换一个量词（位）或插一个修饰词（成年），这条规则就整条落空 ——
# 「只有我一个成年人」因此完全抽不到人数，clarify 把用户刚答过的东西又问一遍
# （2026-10-05 用户实测：「它问我几个人、有没有老人小孩，我答只有我一个成年人，它没识别」）。
#
# 这三个维度是**正交**的：数词 × 量词 × 身份词。它们的组合数随措辞自由增长，
# 靠加短语永远枚举不完 —— 只能改成语法覆盖。这是同一类漏点换个说法再漏一次的形态。
_PARTY_NUM = (
    r"(?:\d+|[一两二三四五六七八九十]+"
    r"|one|two|three|four|five|six|seven|eight|nine|ten)"
)
# 量词缺失是常态（「三人」「五个人」都合法），所以整段可选
_PARTY_CLASSIFIER = r"(?:个|名|位|口)?"
# 身份词一律**长优先**：短分支写前面会把「成年人」先读成「人」
_ADULT_WORD = r"(?:成年人|成人|大人|人)"
_CHILD_WORD = r"(?:小孩子|小朋友|小孩|孩子|儿童|婴幼儿|幼儿|婴儿|宝宝|娃娃|娃)"
_ELDER_WORD = r"(?:老年人|老人家|老人|长者|父母|爸妈|爷爷|奶奶|外公|外婆|岳父|岳母)"

_PARTY_COUNT_RES: list[tuple[str, re.Pattern[str]]] = [
    ("adult", re.compile(rf"({_PARTY_NUM})\s*{_PARTY_CLASSIFIER}\s*{_ADULT_WORD}")),
    ("child", re.compile(rf"({_PARTY_NUM})\s*{_PARTY_CLASSIFIER}\s*{_CHILD_WORD}")),
    ("elder", re.compile(rf"({_PARTY_NUM})\s*{_PARTY_CLASSIFIER}\s*{_ELDER_WORD}")),
    # 英文同理：「2 adults and 1 child」里 children 那条旧表根本不看，只读到 2 成人
    ("adult", re.compile(rf"\b({_PARTY_NUM})\s*(?:adults?|people|persons?|pax|travell?ers?)\b", re.IGNORECASE)),
    ("child", re.compile(rf"\b({_PARTY_NUM})\s*(?:kids?|children|child|toddlers?|babies|baby|infants?)\b", re.IGNORECASE)),
    ("elder", re.compile(rf"\b({_PARTY_NUM})\s*(?:seniors?|elders?|elderly|grandparents?)\b", re.IGNORECASE)),
]

# 年龄正则一次拼好：组合式方案要**按分句**取岁数，抽成模块级常量才能让
# 「被否定那一句里的岁数不算数」落成代码（写在函数里的要搬出来）。
_AGE_RE = re.compile(
    r"(\d{1,2})\s*岁|(\d{1,2})\s*[- ]?\s*(?:year|yr)s?[- ]?old|\b(\d{1,2})\s*(?:yo|y/o)\b"
    r"|(\d{1,2})\s*歳|(\d{1,2})\s*(?:살|세)",
    re.IGNORECASE,
)
_ELDER_KEYWORD_RE = re.compile(r"老人|长者|父母|爸妈|爷爷|奶奶|外公|外婆")
# 否定必须认：系统问「有没有老人和小孩」，用户答「没有老人也没有小孩，就我一个成年人」，
# 旧代码只看这几个字有没有出现 → 判定「有长者同行」。这比抽不到更糟：抽不到只是再问一句，
# 判反会让行程加载长者节奏叠加层，而且因为 has_elder 已知，追问里也不再问同行人构成。
# 否定词本身有歧义（「有没有」里就有「没有」），所以按**分句**判：
# 被否定的那一句里的身份词不算数，别的小句里出现的照旧成立。
_NEG_CUE_RE = re.compile(
    r"(?:没有|沒有|没带|沒帶|不带|不帶|不需要|不包括|不包含|不算|没有算"
    r"|无(?=[带帶老长長父爷爺奶儿兒小孩宝寶婴嬰幼娃岳外童])"
    r"|without|no\s)",
    re.IGNORECASE,
)
_CLAUSE_SPLIT_RE = re.compile(r"[，,。.!！?？;；、\n]")


def _composed_party_counts(message: str) -> tuple[dict[str, int], str]:
    """组合式扫描：把「数词 × 量词 × 身份词」拆成三类计数。

    返回 `(计数, 命中的那段原文)`。同一句里出现多处就累加 ——
    「两个大人一个小孩」是 2 成人 + 1 儿童，不是 2 个人。
    """
    counts = {"adult": 0, "child": 0, "elder": 0}
    frags: list[str] = []
    for kind, rx in _PARTY_COUNT_RES:
        for m in rx.finditer(message or ""):
            n = _num(m.group(1))
            # 上限是防误伤：日期、金额、年份里的数字不能当人数
            if not n or n <= 0 or n > 60:
                continue
            counts[kind] += n
            frags.append(m.group(0).strip())
    return counts, max(frags, key=len) if frags else ""


def _companion_flags(message: str, out: dict[str, Any]) -> None:
    """打 `has_children` / `has_elder` / `child_age`，**认得否定**。

    按分句处理：被否定的那一句整句跳过（「没有老人也没有小孩」里的身份词不算数），
    别的小句里出现的照旧成立（「没有，我带着爸妈去」仍算有长者同行）。
    """
    for clause in _CLAUSE_SPLIT_RE.split(message or ""):
        if not clause.strip():
            continue
        if _NEG_CUE_RE.search(clause):
            continue
        age = _AGE_RE.search(clause)
        if age:
            years = int(next(g for g in age.groups() if g))
            # 年龄必须有上下限语义：「70 岁老人」一度被判成「70 岁儿童」，
            # 进而把整条请求推到 family 场景、加载儿童节奏叠加层（午睡窗口）。
            if years <= CHILD_AGE_MAX:
                out["has_children"] = True
                out["child_age"] = years
            elif years >= ELDER_AGE_MIN:
                out["has_elder"] = True
            # 18–59 岁是成年人，两个标记都不打
        if (
            _ELDER_KEYWORD_RE.search(clause)
            or EN_ELDER_RE.search(clause)
            or JA_KO_ELDER_RE.search(clause)
        ):
            out["has_elder"] = True
        elif JA_KO_CHILD_RE.search(clause):
            out["has_children"] = True


def _extract_party(message: str, patterns: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    raw: str | None = None
    # 「2 大 1 小 / 两大一小」= 成人数 + 儿童数，同行人数是两者之和。
    # 这类写法一个「人」字都没有，落不进人数分支 → 会被当「未识别」→ 默认 2 大 1 小，
    # 「3 大 1 小」于是被读成 3 人（少算一个成人），预算人均折算随之偏高。
    m_ac = re.search(
        rf"(?<![\\d一两二三四五六七八九十])(\d+|{CN_NUM_RE})\s*(?:个|位)?\s*大\s*(?:人)?"
        rf"[，,、\s和与及]*(\d+|{CN_NUM_RE})\s*(?:个|位)?\s*小",
        message,
    )
    if m_ac:
        adults, kids = _num(m_ac.group(1)), _num(m_ac.group(2))
        if adults and kids:
            out["party_size"] = adults + kids
            out["has_children"] = True
            raw = m_ac.group(0)
            out["party_raw"] = raw
    # ② 组合式语法（数词 × 量词 × 身份词）。
    #    覆盖「一个成年人」「1 位成人」「两位成人」「两个大人一个小孩」这类旧表压根没列过的组合。
    counts, frag = _composed_party_counts(message)
    if frag and not out.get("party_size"):
        total = counts["adult"] + counts["child"] + counts["elder"]
        if total:
            out["party_size"] = total
            if counts["child"]:
                out["has_children"] = True
            if counts["elder"]:
                out["has_elder"] = True
            raw = frag

    # ②b 代词式独行（「就我自己」「只有我」）—— 上面两条都收不到这类没有数词的说法。
    #    短语表之前：短语表里没有这些说法，但它那条「未识别 → 2 大 1 小」兜底会把它们吃掉。
    if not out.get("party_size") and _SOLO_PRONOUN_RE.search(message or ""):
        out["party_size"] = 1
        raw = _SOLO_PRONOUN_RE.search(message).group(0)

    # ③ 旧短语表（情侣 / 一人 / 一家 / 独自 …）。只在上面没拿到人数时才跑 ——
    #    它最后有条「未识别 → 默认 2 大 1 小」的兜底分支，一旦跑起来会把已知人数覆盖掉。
    if not out.get("party_size"):
        for pat in patterns:
            m = re.search(pat, message)
            if not m:
                continue
            frag = m.group(1)
            matched = m.group(0)
            if raw is not None and not out.get("has_children"):
                continue
            # 数字 token：阿拉伯数字 / 中文数词 / 英文数词（two、three…）
            num = re.match(rf"\d+|{CN_NUM_RE}|[A-Za-z]+", frag)
            # 判「有没有人数单位」必须看**整段匹配**，不能只看捕获组：
            # 英文规则把单位留在组外（`(\d+)\s*adults?`），只看组内容会把「2 adults」判成没单位，
            # 然后一路掉进「未识别 → 2 大 1 小」的兜底分支（人数对、但被当成带娃）。中文「3个人」同样中招。
            is_party_phrase = any(k in matched for k in ("人", "명")) or bool(
                EN_PARTY_RE.search(matched)
            )
            if num and is_party_phrase and frag not in FAMILY_PHRASES:
                n = _num(num.group(0))
                if n:
                    out["party_size"] = n
                    raw = frag
                    continue
            low = frag.strip().lower()
            if frag in COUPLE_PHRASES or low in COUPLE_PHRASES:
                out["party_size"] = 2
                raw = frag
                continue
            if frag in SOLO_PHRASES or low in SOLO_PHRASES:
                out["party_size"] = 1
                raw = frag
                continue
            # 一家 / 带孩子 / 带娃 / 亲子 / with kids / family / 家族 / 가족
            # 注意「一家人」不能走上面的数词分支（「一」会被当成 1 个人）—— 它是 2 大 1 小。
            out.setdefault("party_size", 3)  # 默认 2 大 1 小
            out["has_children"] = True
            raw = frag
    if raw:
        out["party_raw"] = raw
    _companion_flags(message, out)
    return out


def extract_slots(message: str, settings: Settings) -> dict[str, Any]:
    pats: dict[str, list[str]] = settings.slot_patterns
    slots: dict[str, Any] = {}

    dest, country, names, dest_surface = resolve_destinations(message, settings)
    if dest:
        slots["destination"] = dest
    if country:
        slots["destination_country"] = country
    if names:
        slots["destination_aliases"] = names
    # 原话写法只用于**回显**（追问里说「destination Xiamen」而不是「厦门」）；
    # 检索与预算口径一律用规范名，见 app/i18n.py 的边界第 1 条。
    if dest_surface:
        slots["destination_surface"] = dest_surface

    days, date_range, start_date = _extract_date_range(message, pats.get("date_range", []))
    if days is not None:
        slots["days"] = days
    if date_range:
        slots["date_range"] = date_range
    if start_date:
        slots["start_date"] = start_date

    party_meta = _extract_party(message, pats.get("party", []))
    slots.update(party_meta)
    # 只有真抽到人数才落 party_size。无条件写默认 2 会掩盖「没识别出来」这件事：
    # 下游（人均预算折算、clarify 的缺槽检查）会以为人数已知 —— 「和家人一起去」
    # 于是被按 2 人算预算、也不再追问人数。宁可不填，让它缺槽。
    party_size = slots.get("party_size")
    if party_meta:
        if party_meta.get("has_children"):
            age = party_meta.get("child_age")
            slots["party"] = f"亲子家庭（含 {age} 岁儿童）" if age else "亲子家庭（含儿童）"
        elif party_size:
            slots["party"] = f"{party_size} 位成人"

    total, daily = _extract_budget(message, pats.get("budget", []))
    if daily is not None:
        slots["daily_budget"] = daily
        slots["budget_basis"] = "daily_per_person"
        # 折算用的兜底人数（2）**不写回槽位**：槽位保持「未知」，clarify 仍会追问。
        headcount = party_size or 2
        if days:
            # 「每天 300」在穷游语境下按**人均每日**理解，据此折算总预算
            slots["budget"] = float(daily * days * headcount)
    if total is not None:
        slots["budget"] = float(total)
        slots.setdefault("budget_basis", "total")

    return slots


def _as_count(val: Any, cap: int = 60) -> int | None:
    """表单里的计数字段。空白 / 非法值 → None（等于「没填」），0 是有意义的（明确没有）。

    `cap` 是防误伤：人数、天数这种量级写错到 8000 一定是串了别的值。
    **预算不能走这个函数** —— 它天生比 60 大（`_as_amount` 不设上限）。
    """
    if val is None or (isinstance(val, str) and not val.strip()):
        return None
    try:
        n = int(float(str(val).strip()))
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= cap else None


def _as_amount(val: Any) -> float | None:
    """表单里的金额。没有上限 —— 预算本来就比人数大几个量级。"""
    if val is None or (isinstance(val, str) and not val.strip()):
        return None
    try:
        n = float(str(val).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def slots_from_form(raw: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """把「行程信息卡」里填的东西折成标准槽位（不经过任何 NLP 抽取）。

    存在的理由（2026-10-05 用户提出）：**口语的写法是枚举不完的**。同一个意思可以说成
    「只有我一个人 / 就我一个成年人 / 我自己 / 1 位成人 / 单人出行」，正则补一轮就漏一轮
    新的说法（看 `_PARTY_COUNT_RES` 上面的注释）。表单是那条**确定能通**的路：
    它不依赖抽取，填了就直接是槽位 —— 用户不必猜系统听得懂哪一句。

    三层优先级（在 `graph.clarify_node` 里落地）：**本句 > 表单 > 会话历史**。
    本句优先是因为它最新、最明确；表单次之；会话里的最旧，只用来补前两者没说的部分。

    同行人四项全留空 = 没填，就不会写出任何 party 槽位（免得「没识别」被伪装成「已知」）。
    """
    out: dict[str, Any] = {}
    if not raw:
        return out

    dest = str(raw.get("destination") or "").strip()
    if dest:
        name, cc, names, surface = resolve_destinations(dest, settings)
        # 词典里没有的地名也要收下（用户在表单里填了就是目的地），
        # 否则会被反问一个他刚填过的城市 —— 那正是这个表单要避免的事。
        out["destination"] = name or dest
        if cc:
            out["destination_country"] = cc
        if names:
            out["destination_aliases"] = names
        out["destination_surface"] = surface or dest

    days = _as_count(raw.get("days"))
    if days:
        out["days"] = days
        out["date_range"] = f"{days} 天"
    budget = _as_amount(raw.get("budget"))
    if budget:
        out["budget"] = budget
        out["budget_basis"] = "total"

    adults = _as_count(raw.get("adults"))
    kids = _as_count(raw.get("children"))
    elders = _as_count(raw.get("elders"))
    if adults is None and kids is None and elders is None:
        return out
    total = (adults or 0) + (kids or 0) + (elders or 0)
    # 明确写 0 也要落 False：用户可能是在**更正**上一句（先说带娃，表单里改成 0）
    out["has_children"] = bool(kids)
    out["has_elder"] = bool(elders)
    # `party` 这个字符串才是缺槽检查看的那一项 —— 只写 party_size 会被当成没填，
    # 于是照旧反问「一行几个人」。`extract_slots` 用同一套措辞，两条路的口径要一致。
    age = _as_count(raw.get("child_age"))
    if total:
        out["party_size"] = total
        if kids:
            out["party"] = f"亲子家庭（含 {age} 岁儿童）" if age else "亲子家庭（含儿童）"
        else:
            out["party"] = f"{total} 位成人"
    if kids and age:
        out["child_age"] = age
    return out


def merge_slots(session_slots: dict[str, Any], new_slots: dict[str, Any]) -> dict[str, Any]:
    merged = dict(session_slots or {})
    for k, v in (new_slots or {}).items():
        if v in (None, "", []):
            continue
        merged[k] = v
    return merged


def missing_slots(slots: dict[str, Any], required: Iterable[str]) -> list[str]:
    out = []
    for name in required:
        val = slots.get(name)
        if val is None or val == "" or val == []:
            out.append(name)
    return out


def scene_hints(message: str, settings: Settings) -> list[str]:
    """关键词命中的场景集合，供 clarify 节点选择槽位策略。"""
    hits: list[str] = []
    for sid in settings.scene_ids:
        keywords = settings.scene_cfg(sid).get("keywords", []) or []
        score = sum(1 for kw in keywords if contains(message, kw))
        if score:
            hits.append(sid)
    return hits


def required_slots_for(hints: list[str], settings: Settings) -> list[str]:
    if not hints:
        return list(settings.required_slots(settings.fallback_scene))
    req: list[str] = []
    for sid in hints:
        for r in settings.required_slots(sid):
            if r not in req:
                req.append(r)
    # visa 场景用 destination_country 顶掉 destination，不重复追问
    if "destination_country" in req and "destination" in req:
        req.remove("destination")
    return req or list(settings.required_slots(settings.fallback_scene))


def build_clarify_question(
    missing: list[str], slots: dict[str, Any], language: str = DEFAULT_LANGUAGE
) -> str:
    """追问是给**终端用户**看的 → 文案跟用户语言（zh 是默认，文案与改动前一致）。"""
    ordered = [s for s in SLOT_ORDER if s in missing]
    parts = []
    for s in ordered:
        key = f"slot.{s}"
        # 已经知道有长者/儿童同行时，别再问「有小孩或长者同行吗」—— 那是明知故问。
        if s == "party" and (slots.get("has_elder") or slots.get("has_children")):
            key = "slot.party_size"
        text = t(key, language)
        parts.append(text if text != key else f"{s}?")
    known = []
    # 回显用户原话里的地名（"Xiamen" / "샤먼"），而不是规范化的中文名
    dest_echo = slots.get("destination_surface") or slots.get("destination")
    if dest_echo:
        known.append(t("clarify.known_destination", language, v=dest_echo))
    if slots.get("days"):
        known.append(t("clarify.known_days", language, n=slots["days"]))
    head = t("clarify.known_head", language, known=t("clarify.known_join", language).join(known)) if known else ""
    return head + t("clarify.ask_head", language) + " ".join(parts)
