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

CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
          # 「仨」是中文口语里最常见的三人说法（「我们仨」），单字不是复合词，
          # 漏了它 party 槽位落空、clarify 会追问一个用户刚说过的数。
          "仨": 3,
          # 「俩」同理，是最常见的两人说法（「我们俩」「俩大人」）。它比「仨」更要命：
          # 「俩大人一个小孩」里漏掉「俩」之后，组合语法只认到「一个小孩」→ 人数记成 1
          # （用户说 3 人、系统记 1 人，且因为已有值就不再追问，错值静默落地）。
          "俩": 2}
# 必须容忍复合数词（十五 / 二十 / 二十三）：只写单字类会让「十五个人」只吃到「十」。
# 「仨 / 俩」一并进来：「仨人」「我们俩」是「三个人」「两个人」的口语写法。
CN_NUM_RE = r"[一两二三四五六七八九十仨俩]+"
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
    # 裸「我自己」也很常见（「想自己去」「我自己去」），前面不一定带「就/只有」。
    # 排除「我们自己 / 我俩」—— 那些不是独行。
    r"|我\s*自己(?!\s*[们俩])"
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
        # 单字（三 / 两）或连写（二三、三五）
        if all(ch in CN_NUM for ch in token):
            # 连写是**约数区间**，不是加法：「两三天」「三五天」「四五天」在中文口语里表示
            # 2–3、3–5、4–5 天。旧实现用 sum() 把「三五天」读成 8 天、「两三天」读成 5 天
            # —— 都是极常见的说法，而且错值会静默进天数/人数，比"抽不到"更危险。
            # 取**上限**：规划上宁可多备一天，也别把用户说的范围截短。
            return max(CN_NUM[ch] for ch in token) or None
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


# ---------------------------------------------------------------- 用户来源国（国籍）
# 产品主题是「外国人来华」——「你从哪来」决定了解释的**参照系**，不是要去的地方。
# 与 `destination_country`（办哪国签证）正交：前者描述**用户**，后者描述**行程**；
# 「我是德国人，第一次来中国怎么点菜」里，德国是来源国、中国才是目的地。
#
# 有了它，模型才做得了**中外类比/对比**（「你在德国习惯的……在中国是……」）；
# 没有它，只能给一套放之四海的通用说明 —— 对外国游客等于没说。
#
# 抽取策略：**只认「我是/来自/from」这类明确的身份措辞**，裸国名不认。
# 「北京离上海多远」里的地名、以及目的地国名，绝不能被读成来源国。
_NATIONALITY_FORMS: dict[str, tuple[str, ...]] = {
    "美国": ("美国", "美利坚", "美籍", "america", "american", "americans", "usa", "us", "united"),
    "英国": ("英国", "英籍", "britain", "british", "uk", "england", "english"),
    "加拿大": ("加拿大", "canada", "canadian"),
    "澳大利亚": ("澳大利亚", "澳洲", "australia", "australian", "aussie"),
    "新西兰": ("新西兰", "newzealand", "zealander", "kiwi"),
    "德国": ("德国", "德籍", "germany", "german", "germans"),
    "法国": ("法国", "法籍", "france", "french"),
    "意大利": ("意大利", "italy", "italian"),
    "西班牙": ("西班牙", "spain", "spanish"),
    "葡萄牙": ("葡萄牙", "portugal", "portuguese"),
    "荷兰": ("荷兰", "netherlands", "holland", "dutch"),
    "比利时": ("比利时", "belgium", "belgian"),
    "瑞士": ("瑞士", "switzerland", "swiss"),
    "奥地利": ("奥地利", "austria", "austrian"),
    "瑞典": ("瑞典", "sweden", "swedish"),
    "挪威": ("挪威", "norway", "norwegian"),
    "丹麦": ("丹麦", "denmark", "danish"),
    "芬兰": ("芬兰", "finland", "finnish"),
    "爱尔兰": ("爱尔兰", "ireland", "irish"),
    "波兰": ("波兰", "poland", "polish"),
    "希腊": ("希腊", "greece", "greek"),
    "俄罗斯": ("俄罗斯", "俄国", "russia", "russian"),
    "日本": ("日本", "日籍", "japan", "japanese"),
    "韩国": ("韩国", "韩籍", "korea", "korean", "koreans"),
    "新加坡": ("新加坡", "singapore", "singaporean"),
    "马来西亚": ("马来西亚", "malaysia", "malaysian"),
    "泰国": ("泰国", "thailand", "thai"),
    "越南": ("越南", "vietnam", "vietnamese"),
    "印度尼西亚": ("印度尼西亚", "印尼", "indonesia", "indonesian"),
    "菲律宾": ("菲律宾", "philippines", "filipino"),
    "印度": ("印度", "india", "indian"),
    "巴西": ("巴西", "brazil", "brazilian"),
    "墨西哥": ("墨西哥", "mexico", "mexican"),
    "阿根廷": ("阿根廷", "argentina", "argentinian"),
    "南非": ("南非", "southafrica", "south africa"),
    "埃及": ("埃及", "egypt", "egyptian"),
    "土耳其": ("土耳其", "turkey", "turkish"),
    "以色列": ("以色列", "israel", "israeli"),
    "阿联酋": ("阿联酋", "uae", "emirati"),
    "沙特阿拉伯": ("沙特阿拉伯", "沙特", "saudi"),
}

# 身份措辞。中文侧用**惰性**量词：连写的「我是美国人想去北京」也要切出「美国」，
# 贪婪会把「美国人想去北京」整段吃进去，一个都对不上。
_NATIONALITY_CUE_RE = re.compile(
    r"(?:我是|我来自|来自|我从|身为)\s*([\u4e00-\u9fa5]{2,8}?)"
    r"|(?:i(?:'m| am)|we(?:'re| are))\s+(?:from\s+)?(?:the\s+)?([A-Za-z]{2,20})"
    r"|\bfrom\s+(?:the\s+)?([A-Za-z]{2,20})"
    r"|\bas an?\s+([A-Za-z]{2,20})",
    re.IGNORECASE,
)
_NATIONALITY_STRIP = ("from", "a", "an", "the", "am", "is")


def _match_nationality(token: str) -> str | None:
    t = token.strip().lower()
    for pre in _NATIONALITY_STRIP:
        if t.startswith(pre + " "):
            t = t[len(pre) + 1 :]
    if not t:
        return None
    for zh, forms in _NATIONALITY_FORMS.items():
        for f in forms:
            ff = f.lower()
            if t == ff or t in {ff + "人", ff + "国人", ff + "籍"}:
                return zh
    return None


def _extract_nationality(message: str) -> str | None:
    """用户来源国。抽不到就返回 None（**不猜** —— 猜错会让类比全部跑偏）。"""
    if not message:
        return None
    for m in _NATIONALITY_CUE_RE.finditer(message):
        token = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").strip()
        if not token:
            continue
        hit = _match_nationality(token)
        if hit:
            return hit
    return None


def _match_nationality_loose(text: str) -> str | None:
    """表单/直填通道用：用户**明确写了**国家，允许子串匹配（「United States」→ 美国）。

    与 `_extract_nationality` 的严格口径分开是有意的：从自由句子里猜要保守
    （错一个国名，整篇类比都跑偏），而表单是用户亲手填的、意图明确。
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    for zh, forms in _NATIONALITY_FORMS.items():
        for f in forms:
            if f.lower() in t:
                return zh
    return None


# ---------------------------------------------------------------- 各槽位抽取
# ---------------------------------------------------------------- 月日（不带年）
# 「10月20日」「10月20号」「10月20日に出発」这类**不带年**的出发日：既有的整日期规则硬要求
# 4 位年，整条落空。而 date_range 是**全部规划场景的必填槽** → 系统会反复追问出发日，
# 「你写了日期、我没听见」的第二种形态（真 bug 2026-10-08 用户实测：日语会话卡在追问环节）。
# 年份靠推断：今年的这一天还没到就是今年，已经过了取下一年（用户在做**将来**的行程）。
_MONTH_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号號]")
# 斜杠简写：「10/20 出发」「10/20」——日语也常用。守卫必须严：紧跟时长/人数/金额/「的」
# 一律不算日期，否则「1/2 的预算」「3/4 天」会被读成 1 月 2 日 / 3 月 4 日（错值比缺值危险）。
_SLASH_MONTH_DAY_RE = re.compile(
    r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])"
    r"(?!\s*(?:天|日|人|名|个|位|公里|千米|小时|時間|分钟|岁|元|块|万|萬|千|预算|予算|的))"
)
# 相对月：「来月の20日」「下个月20号」「本月20号」—— 日期词里没有月份数字。
_REL_MONTH_DAY_RE = re.compile(
    r"(今月|本月|这个月|這個月|当月|當月|来月|來月|下个月|下個月|下月)"
    r"\s*(?:の)?\s*(\d{1,2})\s*[日号號]"
)
_REL_MONTH_NEXT = ("来月", "來月", "下个月", "下個月", "下月")


def _resolve_year_for(mo: int, d: int) -> int | None:
    """把「M 月 D 日」补成具体年份：还没过就是今年，已经过了就是下一年。非法月日 → None。"""
    from datetime import date

    today = date.today()
    try:
        cand = date(today.year, mo, d)
    except ValueError:
        return None
    if cand < today:
        try:
            date(today.year + 1, mo, d)
        except ValueError:
            return None
        return today.year + 1
    return today.year


def _month_day(text: str) -> tuple[int | None, int, int, str] | None:
    """抽「月日」，返回 `(年, 月, 日, 命中原文)`；月份数字缺失时用相对月词推。"""
    from datetime import date

    m = _REL_MONTH_DAY_RE.search(text or "")
    if m:
        word, d = m.group(1), int(m.group(2))
        today = date.today()
        y, mo = today.year, today.month
        if word in _REL_MONTH_NEXT:
            mo += 1
            if mo > 12:
                mo, y = 1, y + 1
        elif d < today.day:          # 「本月 20 号」而今天已过 20 号 → 指下个月
            mo += 1
            if mo > 12:
                mo, y = 1, y + 1
        try:
            date(y, mo, d)
        except ValueError:
            return None
        return y, mo, d, m.group(0)
    m = _MONTH_DAY_RE.search(text or "")
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        return _resolve_year_for(mo, d), mo, d, m.group(0)
    m = _SLASH_MONTH_DAY_RE.search(text or "")
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        return _resolve_year_for(mo, d), mo, d, m.group(0)
    return None


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
    # 不带年的月日（10月20日 / 10月20号 / 10月20日に出発 / 来月の20日）。放在整日期之后、
    # 时长规则之前：既保证「带年」的写法优先，也让识别到的那段**先从时长扫描串里摘掉**
    # —— 否则日语的「10月20日」会在下面的裸「N日」模式里被读成 **20 天**（错值比缺值更危险）。
    scan = message
    if start_date is None:
        md = _month_day(message)
        if md:
            y, mo, d, raw = md
            date_text = raw
            if y is not None:
                start_date = f"{y:04d}-{mo:02d}-{d:02d}"
            scan = message.replace(raw, " ")
    for pat in patterns[1:]:
        m = re.search(pat, scan)
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
    # 口语里的时长不总带可解析的数词：「半个月」「十来天」里「半」「来」都不是数字，
    # 正则类永远抽不到 → 掉回 clarify 追问一个用户已经回答过的天数。放在规则循环之后，
    # 只在没有任何显式天数时才认（显式的「3 天」优先级更高）。
    if days is None:
        m_half = re.search(r"半\s*个?\s*月", message)
        m_fuzzy = re.search(r"([一两二三四五六七八九十]+|\d+)\s*(?:来|多)\s*(?:天|日)", message)
        if m_half:
            days, date_text = 15, "半个月（约 15 天）"
        elif m_fuzzy:
            n = _num(m_fuzzy.group(1))
            if n:
                days, date_text = n, f"{n} 天（约）"
    return days, date_text, start_date


# 「N 天够吗」这类**关于够不够**的问句，里面的时长是**被讨论的对象**，不是这趟行程的长度。
# 真 bug（2026-10-06 长会话 B7）：「长城一天够吗」（会话已定 5 天）把 days 从 5 改成 **1**，
# 之后 C1/C2/C3 全在「1 天稿」上操作 —— 一句问句污染了整条会话的下半场。
# 判据要**紧邻**（「三天，钱够吗」不能中），否则「我去北京玩三天，钱够吗」的 3 天会被误删。
_DUR_TOKEN = r"(?:\d+|[一两二三四五六七八九十两])"
_ADEQUACY_Q_RE = re.compile(
    _DUR_TOKEN + r"\s*(?:天|日)\s*(?:够|不够|够不够|够用|够么|玩得完|逛得完|看得完)"
    r"|(?:够|不够|够不够|足够)\s*(?:玩|逛|去|看|安排|待)?\s*" + _DUR_TOKEN + r"\s*(?:天|日)"
)


# 增量天数（「加一天」「多玩两天」「少住一晚」）—— 这是在**已有行程上**做加减，不是把天数
# 改成 N。旧行为会把「加两天」里的「两天」抽成绝对天数 days=2，把刚出的 5 天行程改成 2 天
# —— 与「第二天 → 2 天」同类：错值静默进槽位，比抽不到更危险。
_DAY_DELTA_INC_RE = re.compile(
    r"(?:再|又)?(?:加|增加|延长|多(?:玩|待|待上|住|留|逛)?)\s*"
    r"(\d+|[一两二三四五六七八九十两])\s*(?:天|日|晚)"
)
_DAY_DELTA_DEC_RE = re.compile(
    r"(?:少(?:玩|待|住|留)?|减|缩短|去掉|减去)\s*"
    r"(\d+|[一两二三四五六七八九十两])\s*(?:天|日|晚)"
)


def _extract_day_delta(message: str) -> tuple[int, str]:
    """返回 (增量天数, 摘掉增量说法后的句子)。

    增量说法必须从「绝对天数」的作用域里拿掉，否则「加两天」的「两天」会被
    `_extract_date_range` 抽成 days=2，覆盖掉会话里已有的天数。
    """
    text = message or ""
    m = _DAY_DELTA_DEC_RE.search(text)
    sign = -1
    if not m:
        m = _DAY_DELTA_INC_RE.search(text)
        sign = 1
    if not m:
        return 0, text
    n = _num(m.group(1))
    if not n:
        return 0, text
    return sign * n, text[: m.start()] + " " + text[m.end():]


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
    # 阿拉伯小数的量级写法（「1.5万」「2.5千」）：下面的逐字符状态机只认整数，遇到「.」
    # 会当场 return None（实测「预算1.5万」整条预算抽不到）。这类口语写法很常见，捷径短接。
    m_dec = re.fullmatch(r"(\d+(?:\.\d+)?)([万千百仟萬億亿])", s)
    if m_dec:
        unit = {"万": 1e4, "萬": 1e4, "千": 1e3, "仟": 1e3,
                "百": 1e2, "億": 1e8, "亿": 1e8}[m_dec.group(2)]
        return float(m_dec.group(1)) * unit
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
        # IGNORECASE 是必须的：英文单位与关键词的大小写不统一 —— 「10000 RMB」原来因为
        # 正则只写了小写 `rmb` 而抽不到（真 bug 2026-10-06 长会话 A4：用户报了预算，
        # 系统一路「还差预算」）。中文 / 日 / 韩不受影响。
        m = re.search(pat, message, re.IGNORECASE)
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


# 裸金额：「我想十一去西安，三天，三千块，我自己」—— 口语里的预算常常**不带「预算」二字**，
# 而 routes.yaml 的 budget 规则每条都要求前置词（预算 / 每天 / 人均 / … 或「N 元以内」），
# 于是整条预算漏掉、clarify 反过来追问用户刚说过的那个数字。
# 收窄靠三道闸：① 必须有人民币量词（块/元/人民币/块钱）；
#              ② 紧邻不得出现非预算场景词（门票 / 票价 / 人均 / 每天…），否则「门票 55 元」会被当总预算；
#              ③ 金额下限 100 —— 太小的数字不可能是整趟预算。
# 三道闸以外的金额照旧交给显式规则（「预算 X」等），这里只在显式规则没抽到时兜底。
_BARE_AMOUNT_RE = re.compile(
    r"(?<![\d一两二三四五六七八九十百千萬万])"
    # 量级**必须进捕获组**：「3万元」若只抓到「3」，_cn_amount 拿到的是 3 → 低于下限被丢，
    # 整条预算照旧落空（用户报了预算却被反复追问）。真 bug 2026-10-08。
    r"(\d+(?:\.\d+)?[百千萬万佰仟]?|[一两二三四五六七八九十百千萬万佰仟]+)"
    # 要么带人民币量词（三千块 / 5000 元），要么带约数标记（一千多 / 五千来块）。
    # **不能**允许裸数词：否则任何数字都会变成预算。
    r"\s*(?:(?:多|来)\s*(?:块钱|块|元)?|(?:块钱|块|元整|元|人民币))"
    # 后面紧跟计数单位说明那是人数/里程/时长，不是钱（「十多天」「三百多公里」「一千多人」）。
    r"(?!\s*(?:人|名|个|位|公里|千米|天|小时|分钟|秒|岁))"
)
_AMOUNT_SCENE_DENY_RE = re.compile(
    r"门票|票价|单价|售价|押金|手续费|人均|每天|每日|一日|1日|一晚|房费|小费|退款"
)
BARE_AMOUNT_MIN = 100.0


def _extract_bare_amount(message: str) -> float | None:
    """兜底抽「不带前置词的金额」；命中非预算场景词或金额过小则放弃。"""
    text = message or ""
    for m in _BARE_AMOUNT_RE.finditer(text):
        lo, hi = max(0, m.start() - 6), min(len(text), m.end() + 6)
        if _AMOUNT_SCENE_DENY_RE.search(text[lo:hi]):
            continue
        token = m.group(1)
        val = _cn_amount(token)
        if val is None:
            n = _num(token)
            val = float(n) if n is not None else None
        if val and val >= BARE_AMOUNT_MIN:
            return val
    return None


# 兜底之再兜底：**整句就是一个数**（「6000 吧」「1万」「6千」）。
# 场景是"追问-应答"——系统刚问「这次大概想控制在多少」，用户直接回一个数。
# 这种回答既没有量词也没有「预算」二字，上面两条规则都落空 → 槽位保留旧值，
# 表现成"你问了、我答了、你没听见"。限定**整句只有数字+语气词**，
# 所以不会误伤「5天」「两个人」这类带单位的槽位答复。
_PURE_AMOUNT_RE = re.compile(
    r"^[\s，,。.!！?？~～]*(?:大概|大约|差不多|就|控制在|预算(?:是|大概)?)?\s*"
    r"(?P<num>\d+(?:\.\d+)?|[一两二三四五六七八九十百千萬万佰亿零〇]+)"
    r"\s*(?P<unit>[萬万仟千佰百kK])?"
    r"[\s，,。.!！?？~～]*(?:吧|了|呢|的|就行|就好|就可以|左右|上下|以内|以下|出头)?"
    r"[，,。.!！?？~～\s]*$"
)


def _extract_pure_amount(message: str) -> float | None:
    """整句只是一个数 → 当预算。下限与裸金额一致，挡住「3」「5」这种无信息量输入。"""
    m = _PURE_AMOUNT_RE.match((message or "").strip())
    if not m:
        return None
    token = m.group("num") + (m.group("unit") or "")
    val = _amount(token, token, "")
    if val is None:
        n = _num(m.group("num"))
        val = float(n) * _budget_scale(token, "") if n is not None else None
    return val if val and val >= BARE_AMOUNT_MIN else None


# 「预算加到一万五」「提到两万」「改成八千」—— **设定目标**的预算，不是增量。
# 口语里说「加到」时给的是**目标值**（改完是一万五），所以直接抽那个数当预算。
# 缺口实测（2026-10-06 长会话 C4）：这句既不带「预算 X」的语序、也没有人民币量词，
# 显式规则与裸金额兜底两层都落空 → 槽位仍停在一万，而模型标题却写了 15000，自相矛盾。
_CHANGE_TARGET_BUDGET_RE = re.compile(
    r"(?:加到|提到|增加到|涨到|调到|提到|改成|改为|调成|拉到)\s*"
    r"(" + r"\d+(?:\.\d+)?|[一两二三四五六七八九十百千萬万佰亿零〇]+)\s*([萬万仟千佰百kK]?)"
)


def _extract_change_target_amount(message: str) -> float | None:
    """「加到一万五」这类**目标值**预算；不是这类说法就返回 None。"""
    m = _CHANGE_TARGET_BUDGET_RE.search(message or "")
    if not m:
        return None
    token = m.group(1) + (m.group(2) or "")
    val = _amount(token, token, "")
    if val is None:
        n = _num(m.group(1))
        val = float(n) if n is not None else None
    return val if val and val >= BARE_AMOUNT_MIN else None


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
    r"(?:\d+|[一两二三四五六七八九十仨俩]+"
    r"|one|two|three|four|five|six|seven|eight|nine|ten)"
)
# 量词缺失是常态（「三人」「五个人」都合法），所以整段可选
_PARTY_CLASSIFIER = r"(?:个|名|位|口)?"
# 身份词一律**长优先**：短分支写前面会把「成年人」先读成「人」
_ADULT_WORD = r"(?:成年人|成人|大人|人)"
_CHILD_WORD = r"(?:小孩子|小朋友|小孩|孩子|儿童|婴幼儿|幼儿|婴儿|宝宝|娃娃|娃)"
_ELDER_WORD = r"(?:老年人|老人家|老人|长者|父母|爸妈|爷爷|奶奶|外公|外婆|岳父|岳母)"

_PARTY_COUNT_RES: list[tuple[str, re.Pattern[str]]] = [
    # 量词**本身就是人数单位**：「2名」「3位」「2名様」—— 句中一个「人」字都没有。
    # 日语里「2名」是最标准的人数说法（中文也常说「2名」），旧表要求量词后面必须再跟一个
    # 身份词（_ADULT_WORD），于是整条落空。真 bug（2026-10-08 用户实测）：会话中途改说日语
    # 答「2名です」，追问原样重复、连问两轮纹丝不动 —— 卡在追问环节。
    # 负向断言只排「量词后紧跟身份词」的情形（那三种由下面三条负责），避免重复计数；
    # `(?<!第)` 排掉「第2位」这类名次。
    ("adult", re.compile(
        rf"(?<!第)(?<!第\s)({_PARTY_NUM})\s*(?:名|位)"
        rf"(?!\s*(?:{_ADULT_WORD}|{_CHILD_WORD}|{_ELDER_WORD}))"
    )),
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
_ELDER_KEYWORD_RE = re.compile(r"老人|长者|父母|爸妈|爷爷|奶奶|外公|外婆|二老|两老")
# 中文的「儿童」关键词：与长者词对称。
# 旧代码只查长者词（`_ELDER_KEYWORD_RE`），中文裸「孩子/小孩/娃」一旦不带数词就**完全不打标记**——
# 「带我爸妈和孩子」于是只有 has_elder、没有 has_children，行程不加载儿童节奏叠加层。
_CHILD_KEYWORD_RE = re.compile(r"小孩子|小朋友|小孩|孩子|儿童|婴幼儿|幼儿|婴儿|宝宝|娃娃|娃")
# 「爸妈 / 父母」= 两位长者（成对）且隐含「我」这个基点：用于组合语法的基点补偿。
_PARENTS_RE = re.compile(r"爸妈|父母|双亲|二老|两老")
# 「带 / 领 / 拖家带口」的施动者默认是说话人自己 —— 「带两个小孩」= 我 + 两个小孩。
# 只在数词没数到成人、且句中没有别的基点线索时，用它补回那个成人。
_BRING_RE = re.compile(r"带(?:着|著|上|了)?|领(?:着|著)?|携带")
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

# 配偶 / 伴侣同行：「我和我老婆」「带我女朋友」是最常见的双人写法。
# 旧代码里它们既不进人数语法（没有「人」也没有数词）、也不进情侣短语表，
# 于是掉进下面的「未识别 → 2 大 1 小」兜底分支 —— 「带女朋友旅行」被判成"带娃"，
# 加载儿童节奏叠加层、按 3 人算预算。**判反比抽不到更糟**，所以要在兜底之前拦住。
_SPOUSE_RE = re.compile(
    r"老婆|老公|太太|媳妇儿?|妻子|丈夫|女朋友|男朋友|对象|爱人|伴侣|未婚妻|未婚夫|老伴|内人"
)
# 「我们仨 / 咱们仨」：中文最口语的"三人"说法，句子里既没有数词也没有「人」字，
# 组合语法与短语表都够不着 → 会掉进「未识别 → 2 大 1 小」兜底（人数错、还多个儿童）。
_TRIO_RE = re.compile(r"仨")

# 「我和朋友 / 我跟同学 / 和同事一起」——最口语的双人写法，句子里既没有数词也没有「人」字，
# 组合语法够不着、会掉进「未识别 → 2 大 1 小」兜底（凭空白送一个儿童）。
# 只认「我 + 结伴词 + 单数身份词」这一形状；后面紧跟「一家 / 数量词」时让位 ——
# 「我和朋友一家三口」是 3 人，不能被这条读成 2 人。
_FRIEND_PAIR_RE = re.compile(
    r"我\s*(?:和|跟|同|与)\s*(?:一个|1\s*个|个)?\s*"
    r"(?:朋友|同学|同事|闺蜜|兄弟|姐妹|发小|室友|哥们儿?)"
    r"(?!\s*(?:一家|[一两二三四五六七八九十\d]\s*[口个位人名]))"
)

# ---------------------------------------------------------------- 节奏 / 偏好（市场对标）
# 成熟产品（Layla / Mindtrip / TripGenie）都把「节奏」与「兴趣」当作行程个性化的两个主输入：
# 「我不想太赶」「我喜欢美食和历史」这类说法**不给任何数值槽位**，却是行程形态的决定因素
# （每天排几个点 / 要不要午休 / 优先什么主题）。抽取层原先完全认不出它们 →
# 被当「没给信息」→ 掉回缺槽追问，用户明明说了却被重问一遍。
# 真 bug（2026-10-06 市场对标探针 M3/M8 实测）。
_PACE_RELAXED_RE = re.compile(
    r"不(?:想)?太?赶|别太赶|不要太赶|轻松|放松|悠闲|休闲|慢(?:一点|一些|一些|悠悠|游)|"
    r"别太累|不要太累|不累|松弛|不折腾|慢慢来|宽(?:松|裕)|"
    r"relax(?:ed|ing)?|slow[\s-]?paced|not\s+too\s+(?:packed|rushed|hectic)|easy[\s-]?going|"
    r"のんびり|ゆっくり|여유|느긋",
    re.IGNORECASE,
)
_PACE_PACKED_RE = re.compile(
    r"紧凑|充实|尽量多|尽可能多|多(?:玩|逛|看)(?:一?点|一些)?|抓紧|满满|"
    r"不浪费时间|多打卡|high[\s-]?paced|packed|see\s+as\s+much|busy\s+schedule|"
    r"ぎっしり|たくさん|빡빡|알차",
    re.IGNORECASE,
)

# 兴趣标签词典：命中即并入 `interests`（同一句可命中多个：「美食和历史」）。
# 刻意**不用泛字**：「山 / 海」这类单字会和地名撞（上海、唐山、中山），一律写成双字词。
_INTEREST_LEXICON: list[tuple[str, re.Pattern[str]]] = [
    ("food", re.compile(r"美食|吃货|小吃|餐厅|料理|本地菜|特色菜|米其林|food|cuisine|グルメ|맛집", re.IGNORECASE)),
    ("history", re.compile(r"历史|古迹|博物馆|文物|文化遗产|古建筑|history|museum|historic|遺跡|歴史|박물관|역사", re.IGNORECASE)),
    ("nature", re.compile(r"自然|风景|风光|海边|海滩|看海|海岛|湖泊|森林|公园|户外|徒步|爬山|山景|nature|scenery|hiking|landscape|ハイキング|자연", re.IGNORECASE)),
    ("shopping", re.compile(r"购物|逛街|买买买|商场|免税店|shopping|mall|ショッピング|쇼핑", re.IGNORECASE)),
    ("nightlife", re.compile(r"夜生活|夜景|夜市|酒吧|night\s?life|night\s?view|夜景|야경|나이트", re.IGNORECASE)),
    ("culture", re.compile(r"文化|民俗|非遗|演出|表演|艺术|话剧|culture|art\s|performance|공연", re.IGNORECASE)),
    ("family", re.compile(r"亲子|适合孩子|适合小孩|孩子喜欢|family[\s-]?friendly|kid[\s-]?friendly", re.IGNORECASE)),
    ("photo", re.compile(r"拍照|摄影|打卡|出片|photography|写真|사진", re.IGNORECASE)),
]

# 硬约束 / 忌口（Layla 明确把 dealbreaker 当主输入）：饮食与行动能力直接决定每天去哪、
# 吃什么，不写进槽位就被整套忽略（真 bug 2026-10-06 P15/P16：素食者 / 腿脚不便的约束
# 完全没进提示词，行程照常排步行与常规餐饮）。
_CONSTRAINT_LEXICON: list[tuple[str, re.Pattern[str]]] = [
    ("vegetarian", re.compile(r"素食|吃素|不吃肉|纯素|斋食|vegetarian|vegan|ベジタリアン|채식", re.IGNORECASE)),
    ("halal", re.compile(r"清真|halal|ハラール|할랄", re.IGNORECASE)),
    ("no_spicy", re.compile(r"不吃辣|忌辣|怕辣|不要辣|少辣|not\s+spicy|mild\s+food|辛いの?が?苦手|맵지\s*않", re.IGNORECASE)),
    ("food_allergy", re.compile(r"过敏|忌口|allerg|アレルギー|알레르기", re.IGNORECASE)),
    ("limited_mobility", re.compile(
        r"腿脚|行动不便|不便行动|少走(?:点|些|路)|不想走太多|走不动|不能久走|走不了|"
        r"无障碍|轮椅|体力不好|老人腿|walking\s+(?:is\s+)?difficult|limited\s+mobility|wheelchair|"
        r"足が(?:悪|不自由)|歩くのが?大変|거동|휠체어", re.IGNORECASE)),
]

# 多目的地：「北京玩三天再去上海玩两天」——旧逻辑只取第一个城市（北京 3 天），第二段整段丢失。
# 市场对标：Layla / Mindtrip 都把多城路由当作核心能力（含交通衔接与天数分配）。
_MULTI_CITY_JOIN_RE = re.compile(r"再去|然后再?去|然后去|再到|接着去|之后去|随后去|再去|->|→|⇒")
# 「把 A 换成 B」：多城行程的换站（「把上海换成杭州」）。不更新 multi_city 会留下旧城市。
_CITY_SWAP_RE = re.compile(r"把\s*(.{1,8}?)\s*(?:换|改)(?:成|为)\s*(.{1,8})")


def _extract_pace(message: str) -> str:
    """节奏诉求：`relaxed` / `packed` / 空串。

    `relaxed` 优先：「别太赶，但想多看点」这种矛盾句里，压降节奏是更安全的默认
    （排太满的伤害远大于排太空）。
    """
    text = message or ""
    if _PACE_RELAXED_RE.search(text):
        return "relaxed"
    if _PACE_PACKED_RE.search(text):
        return "packed"
    return ""


def _extract_interests(message: str) -> list[str]:
    """兴趣标签列表，按词典顺序去重。命中不到就是空列表（下游据此保持通用行程）。"""
    text = message or ""
    out: list[str] = []
    for label, rx in _INTEREST_LEXICON:
        if label not in out and rx.search(text):
            out.append(label)
    return out


def _extract_constraints(message: str) -> list[str]:
    """饮食忌口 / 行动能力的硬约束。**认得否定**：被否定的分句整句跳过
    （「没有忌口」「腿脚没问题」不该被当成约束）。"""
    out: list[str] = []
    for clause in _CLAUSE_SPLIT_RE.split(message or ""):
        if not clause.strip() or _NEG_CUE_RE.search(clause):
            continue
        for label, rx in _CONSTRAINT_LEXICON:
            if label not in out and rx.search(clause):
                out.append(label)
    return out


def _extract_multi_city(message: str, names: list[str]) -> list[str]:
    """多目的地城市序列（按在句中出现的位置排序）；单城或没有衔接词 → 空列表。

    词典命中的顺序是**名字长度序**，不是位置序，所以必须按 `find` 的下标重排。
    """
    text = message or ""
    if not _MULTI_CITY_JOIN_RE.search(text):
        return []
    positioned = sorted((text.find(n), n) for n in dict.fromkeys(names) if text.find(n) >= 0)
    ordered = [n for _, n in positioned]
    return ordered if len(ordered) >= 2 else []


# 每个城市名**之后**最近的「N 天」——用于多城天数分配（北京 3 天 → 上海 2 天 = 全 5 天）。
_DAY_INLINE_RE = re.compile(r"(\d+|[一两二三四五六七八九十两]+)\s*(?:天|日)")


def _multi_city_days(message: str, cities: list[str]) -> list[int]:
    """按城市出现顺序取各自名后最近的天数；任何一站取不到就返回空（宁可不猜）。"""
    text = message or ""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for name in cities:
        idx = text.find(name, cursor)
        if idx < 0:
            return []
        nxt = len(text)
        cursor = idx + len(name)
        spans.append((cursor, nxt))
    out: list[int] = []
    for i, (start, _) in enumerate(spans):
        end = text.find(cities[i + 1], start) if i + 1 < len(cities) else len(text)
        if end < 0:
            end = len(text)
        m = _DAY_INLINE_RE.search(text, start, end)
        if not m:
            return []
        n = _num(m.group(1))
        if not n or n <= 0 or n > 60:
            return []
        out.append(n)
    return out


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
        # 长者与儿童**两个独立判断**：一句里同时出现「我爸妈和孩子」时两者的标记都要打。
        # 原来是 `elif`，长者词命中就把儿童那条吞掉了。
        if _CHILD_KEYWORD_RE.search(clause) or JA_KO_CHILD_RE.search(clause):
            out["has_children"] = True


def _extract_party(message: str, patterns: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    raw: str | None = None
    # 「2 大 1 小 / 两大一小」= 成人数 + 儿童数，同行人数是两者之和。
    # 这类写法一个「人」字都没有，落不进人数分支 → 会被当「未识别」→ 默认 2 大 1 小，
    # 「3 大 1 小」于是被读成 3 人（少算一个成人），预算人均折算随之偏高。
    m_ac = re.search(
        rf"(?<![\\d一两二三四五六七八九十俩])(\d+|{CN_NUM_RE})\s*(?:个|位)?\s*大\s*(?:人)?"
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
            # 基点补偿：组合语法数的是**带数词的类**（「两个孩子」），句中的「我 / 我老婆 /
            # 我父母」这类**基点**一个字都没数进去。「我老婆和两个孩子」真实是 4 人，
            # 旧逻辑只数到「两个孩子」→ 2 人（少算一半，而且因为已经有值就不再追问）。
            # 只在成人计数为 0 时补 —— 否则「我和我老婆两个人」的「两个人」=2 会被补成 4。
            if counts["adult"] == 0:
                if _SPOUSE_RE.search(message or ""):
                    total += 2          # 我 + 配偶
                elif _PARENTS_RE.search(message or ""):
                    total += 3          # 我 + 父母（两位）
                elif re.search(r"(?<![他她它])我(?!们)", message or ""):
                    total += 1          # 只有我
                elif counts["child"] and _BRING_RE.search(message or ""):
                    # 「带两个小孩去北京」：数词只数了小孩，**带**字的施动者（我）没数。
                    # 真 bug（2026-10-06 市场对标探针 M8）：抽成 2 人（应 3），
                    # 出稿标题还被模型脑补成「2 大 2 小」。
                    total += 1
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

    # ②c 配偶 / 伴侣同行 → 两人。位置必须在 ③ 之前：③ 最后那条
    #     「未识别 → 默认 2 大 1 小」会把「带我女朋友」吃成"带娃"。
    if not out.get("party_size"):
        m_sp = _SPOUSE_RE.search(message or "")
        if m_sp:
            # 「老婆孩子」是「我老婆和孩子」的口语缩略 —— 隐含还带一个孩子。
            out["party_size"] = 3 if _CHILD_KEYWORD_RE.search(message or "") else 2
            raw = m_sp.group(0)

    # ②c2 「我和朋友/同学/同事」→ 两人（同样要在 ③ 兜底之前拦住）。
    if not out.get("party_size"):
        m_fr = _FRIEND_PAIR_RE.search(message or "")
        if m_fr:
            out["party_size"] = 2
            raw = m_fr.group(0).strip()

    # ②d 「仨」= 三人。同样要在 ③ 的兜底之前拦住。
    if not out.get("party_size") and _TRIO_RE.search(message or ""):
        out["party_size"] = 3
        raw = "仨"

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

    # 用户来源国（与目的地国正交）。抽到就落槽位，随 {{slots_json}} 进生成提示词，
    # 供模型做中外类比；抽不到就不写，免得把「没识别」伪装成「已知」。
    nationality = _extract_nationality(message)
    if nationality:
        slots["nationality"] = nationality

    # 多目的地（「北京玩三天再去上海玩两天」）：`destination` 仍是首站（排程、检索以它为主），
    # 其余站点进 `multi_city` —— 不记的话第二段整段蒸发（真 bug 2026-10-06 M6）。
    multi = _extract_multi_city(message, names)
    if multi:
        slots["multi_city"] = multi
        slots["destination_secondary"] = multi[1]

    # 节奏 / 偏好：不给数值、却是行程形态的决定因素（每天几点、要不要午休、主题排序）。
    # 放进槽位即随 `{{slots_json}}` 进生成提示词，无需另设通道。
    pace = _extract_pace(message)
    if pace:
        slots["pace"] = pace
    interests = _extract_interests(message)
    if interests:
        slots["interests"] = interests
    # 硬约束（素食 / 清真 / 忌口 / 行动不便）：直接改变每天的餐饮与点位选择。
    constraints = _extract_constraints(message)
    if constraints:
        slots["constraints"] = constraints
    # 「把 A 换成 B」：多城换站的标记，由 `merge_slots` 落到 `multi_city` 上。
    # **两边都必须能解析成目的地**才认 —— 否则「把预算换成便宜点的」也会被当成换站。
    m_swap = _CITY_SWAP_RE.search(message or "")
    if m_swap:
        old_hits = gazetteer_hits(m_swap.group(1).strip(), settings)
        new_hits = gazetteer_hits(m_swap.group(2).strip(), settings)
        if old_hits and new_hits:
            slots["destination_swap"] = [old_hits[0][0], new_hits[0][0]]

    # 先摘掉「加一天 / 多玩两天 / 少住一晚」这类**增量**说法，再抽绝对天数：
    # 否则「加两天」里的「两天」会被当成「改成 2 天」，把会话里已有的 5 天行程改成 2 天。
    day_delta, days_scope = _extract_day_delta(message)
    days, date_range, start_date = _extract_date_range(days_scope, pats.get("date_range", []))
    # 「长城一天够吗」—— 问的是"够不够"，不是"就玩一天"。若不挡，这句会把已有行程的
    # days 改成 1，并让后续所有迭代都在 1 天稿上操作（真 bug 2026-10-06 长会话 B7 连锁污染）。
    if days is not None and _ADEQUACY_Q_RE.search(message or ""):
        days, date_range = None, None
    if days is not None:
        slots["days"] = days
    if date_range:
        slots["date_range"] = date_range
    if start_date:
        slots["start_date"] = start_date
    if day_delta:
        slots["days_delta"] = day_delta
    # 多城天数分配：「北京玩 3 天再去上海玩 2 天」= 全 5 天。不这么算，`days` 会只剩
    # 首站的 3 天，行程按 3 天排却要覆盖两座城（真 bug 2026-10-06 M6：上海整段丢失）。
    if multi:
        legs = _multi_city_days(message, multi)
        if legs:
            slots["multi_city_days"] = legs
            total_days = sum(legs)
            if total_days:
                slots["days"] = total_days
                slots["date_range"] = f"{total_days} 天"

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
    if total is None:
        # 「加到一万五」这类**目标值**说法（先于裸金额：它语义更明确）。
        total = _extract_change_target_amount(message)
    if total is None:
        # 显式规则（预算 X / 每天 X / 人均 X / N 元以内）都没抽到时的兜底：裸金额。
        total = _extract_bare_amount(message)
    if total is None:
        # 再兜一层：整句就是一个数（回答「想控制在多少」时最常见的形态）。
        total = _extract_pure_amount(message)
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

    # 用户来源国（与 destination 正交：一个描述用户、一个描述行程）。
    # 表单里用户是**明确写的国家**，所以用宽松匹配（"United States" → 美国）；
    # 表里没有的写法也照收，交给生成提示词去做类比 —— 丢掉的代价是「类比全没了」。
    nat = str(raw.get("nationality") or raw.get("home_country") or "").strip()
    if nat:
        out["nationality"] = _match_nationality_loose(nat) or nat
        out["nationality_surface"] = nat

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
    delta = (new_slots or {}).get("days_delta")
    for k, v in (new_slots or {}).items():
        # days_delta 是**相对量**，不是槽位本身，单独处理（见下）。
        if k == "days_delta":
            continue
        if v in (None, "", []):
            continue
        merged[k] = v
    # 「加一天 / 少住一晚」：在既有天数上做加减，而不是覆盖。
    # 只有**已有基数**时才应用 —— 新会话里单说「加两天」没有基数，保持缺槽让 clarify 问一句，
    # 比替用户猜一个起点更好。
    if delta and merged.get("days"):
        merged["days"] = max(1, min(60, int(merged["days"]) + int(delta)))
        merged["date_range"] = f"{merged['days']} 天"
    # 「把 A 城换成 B 城」（多城换站，真 bug 2026-10-06 P20）：B 是**替换后的站点**，
    # 不是新的首站 —— 既要把 multi_city 里的 A 换成 B，也不能让它把 destination 顶掉
    # （否则「北京+上海 → 把上海换成杭州」会变成首站杭州、还留着旧上海）。
    swap = (new_slots or {}).get("destination_swap")
    if isinstance(swap, (list, tuple)) and len(swap) == 2 and merged.get("multi_city"):
        old, new = str(swap[0]).strip(), str(swap[1]).strip()
        cities = [new if c == old else c for c in merged["multi_city"]]
        if cities != merged["multi_city"]:
            merged["multi_city"] = cities
            if len(cities) > 1:
                merged["destination_secondary"] = cities[1]
            merged["destination_aliases"] = list(dict.fromkeys(cities))
            if (session_slots or {}).get("destination"):
                merged["destination"] = session_slots["destination"]
                merged["destination_surface"] = (
                    session_slots.get("destination_surface") or session_slots["destination"]
                )
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
    # 只缺一项时换说法：连续追问里最难听的就是把「还需要确认：」这种清单腔
    # 用在"就差一个问题"的场合 —— 那是把对话当表单填。
    ask_key = "clarify.ask_head_one" if len(ordered) == 1 else "clarify.ask_head"
    return head + t(ask_key, language) + " ".join(parts)


# ---------------------------------------------------------------- 放手表达与空转应答
# 交互流畅度（2026-10-06 用户要求）：识别更口语、更随机的输入，少用固定模版。
#
# 「随便 / 都行 / 你看着办 / 听你的 / 你推荐」= 用户把决定权交给系统。这时候**再抛一遍
# 四连问**是交互上最刺眼的错：用户已经明确说"你定"，系统却把同样四个问题原样问回去
# （实测：七种放手表达全部掉回同一个模版追问，一问不问地重来）。
# 正确处理是用**常规默认**把骨架缺口补上、并把默认值**明说**出来，让用户先看到一版
# 具体的东西，再决定改哪一项 —— 从"填 4 个空"变成"改 1 个值"。
DELEGATE_RE = re.compile(
    r"随便|都行|都可以|都成|怎样都行|怎么都行|你(?:来)?(?:看着办|定|决定|安排|推荐|拿主意)|"
    r"听你的|听你安排|你说了算|你说得算|无所谓|随意|没意见|看你(?:的|安排)?|都听你的|"
    r"帮我(?:定|决定|安排|推荐)|替我(?:定|决定|安排)|按你(?:说的|推荐的)?(?:来|办|安排)|"
    r"看着(?:来|办|安排)|都随你|随你(?:定|安排)?|你(?:来)?安排|"
    r"简单(?:点|一点|就好|就行|点的)|越简单越好|省事(?:点|就行|一点)|"
    # 「预算不多 / 别太贵 / 不贵就行」：预算这一项没给数、但给了态度 —— 同样按放手处理，
    # 免得掉回四连问去追问一个他已经回答过的问题。
    r"别太贵|不贵就行|不要太贵|"
    r"预算(?:不多|有限|无所谓|看着来|不限|不是问题|都可以|随意)|不限预算|不差钱|不用管预算|"
    r"价格(?:无所谓|随便)|"
    # 「那按这个来排 / 就这样 / 就这么办」：用户是对**已经说过的信息**点头、让系统继续，
    # 语义与放手等价（剩下的你定）。不认它 → 会把已经交代过的目的地/天数再问一遍
    # （真 bug 2026-10-06 M3：「那按这个来排」掉回缺槽追问）。
    r"(?:那|就)?(?:按|照|依)(?:这个|这样|这样子|上面|刚才(?:说|讲)的|此)(?:来|办|排|安排|做|即可|就行)?|"
    r"就(?:这么|这样)(?:办|来|定|排|安排|做)(?:吧|了)?|"
    r"up\s*to\s*you|you\s*decide|you\s*choose|your\s*call|whatever|any(?:thing)?\s*(?:is)?\s*fine",
    re.IGNORECASE,
)
# 纯应答（「好的 / 嗯 / 可以 / 对」）：**不含任何槽位信息**，也不是放手（没说让系统定）。
# 它只是确认。此时同样不该把四连问再刷一遍 —— 收窄成**一个问题**，把节奏拉回一问一答。
# 用 `fullmatch` 是因为它必须**整句**都是应答词：「可以的，三个人」里有信息，不算空转。
ACK_ONLY_RE = re.compile(
    r"(?:好的?|好嘞|好呀|好哒|行|可以|没问题|明白|知道了|懂了|嗯+|哦+|对|是的?|是啊|没错|收到|"
    r"ok|okay|sure|yes|yep|fine)"
    r"(?:\s*[呀啊吧了呢哈~～]*)?[!！。.~～]*",
    re.IGNORECASE,
)
# 模糊的预算调整诉求（「太贵了，能便宜点吗」「开销太少，多花点」）：用户要改**花多少**，
# 但**没给数**。单独出来，是因为它的正确应对与前两类都不同：不是放手（他有具体偏好），
# 也不该直接重出稿（那会让模型自己编一个他从没说过的数）—— 该追问一句目标预算。
#
# ⚠️ 词表必须**两侧都覆盖**（贵 / 省），单向漏词会把整条诉求静默吞掉。
# 真 bug（2026-10-09 用户实测）：用户说「提高开销」，三个判据全灭 → 走了普通生成、
# 从零重排，开销反而一路降（2260 → 2190 → 2160），标题从「预算优化」滑成「穷游」。
# 用户说「提高」系统给「穷游」—— 不是没听懂，是词表里只有「便宜 / 省」那一半。
# 而且用户说的是**「开销」不是「预算」**，所以「开销 / 花费 / 花销」也必须进表。
BUDGET_SOFT_RE = re.compile(
    # —— 降低方向 ——
    r"便宜(?:一?点|一些|些|点)|贵(?:了|一?点|一些|些)|省(?:一?点|一些|些)|"
    r"降(?:低|一?点|一些)|压(?:缩|低)(?:预算|价|开销|花费)|少花(?:点|一点|一些)?|"
    # —— 提高方向（旧表完全没有这一半）——
    r"提高(?:预算|开销|花费|花销)|增加(?:预算|开销|花费)|涨(?:预算|价)|加(?:点|些)?预算|"
    r"(?:预算|开销|花费|花销)(?:太?少|不够|低了|偏低)|(?:预算|开销)再?高(?:点|一点|一些)|"
    r"多花(?:点|一点|一些)|花(?:多|贵)(?:点|一点|一些)|贵(?:点|一点|一些)|"
    r"预算(?:高|低|多|少)(?:一?点|一些|些)?|"
    # —— 抱怨「钱没花完」（用户已给预算，诉求是用足它）——
    r"花不完|用不完|没花完|没用完|花不出去|不够花|"
    # —— 英文 ——
    r"cheaper|less\s+expensive|too\s+expensive|too\s+pricey|more\s+expensive|"
    r"too\s+cheap|not\s+expensive\s+enough|spend\s+more|increase\s+the\s+budget|"
    r"raise\s+the\s+budget|use\s+up\s+the\s+budget|"
    # —— 日 / 韩 ——
    r"もう少し安く|高すぎ|予算を下げ|予算を上げ|もっと使|安すぎ|"
    r"더\s*저렴|너무\s*비싸|예산을\s*올|더\s*쓰",
    re.IGNORECASE,
)

# 放手时的常规骨架：5 天 / 5000 元 / 2 位成人 —— 最保守也最不容易踩坑的一组。
# 刻意**只在放手表达时**使用，绝不拿它去兜用户没说过的普通句子（那等于替人编预算）。
DELEGATION_DEFAULTS = {"days": 5, "budget": 5000.0, "party_size": 2}


def is_delegating(message: str) -> bool:
    """用户是不是在说「你来定」。"""
    return bool(DELEGATE_RE.search(message or ""))


def is_ack_only(message: str) -> bool:
    """整句是不是纯应答词（不含任何实质信息）。"""
    return bool(ACK_ONLY_RE.fullmatch((message or "").strip()))


def is_budget_adjust(message: str) -> bool:
    """用户是在要求**调整**预算（而不是给出预算、也不是放手让你定）。"""
    return bool(BUDGET_SOFT_RE.search(message or ""))


# 调整的**方向**。同一条诉求，问法必须相反 ——「太贵了」该问"想控制在多少以内"，
# 「开销太少」该问"想提到多少"。旧实现只有一套降向文案，对提高方向是**反着问**：
# 用户说"提高开销"，系统回"想控制在多少"，语义正好拧过来（2026-10-09 用户实测）。
_BUDGET_RAISE_RE = re.compile(
    r"提高|增加|上调|调高|涨|加(?:点|些)?预算|多花|花(?:多|贵)(?:点|一点|一些)?|"
    r"(?:预算|开销|花费|花销)(?:太?少|不够|低了|偏低)|贵(?:点|一点|一些)|"
    r"再?高(?:点|一点|一些)|花不完|用不完|没花完|没用完|不够花|"
    r"too\s+cheap|spend\s+more|increase|raise|more\s+expensive|higher|use\s+up|"
    r"予算を上げ|もっと|安すぎ|예산을\s*올|더\s*쓰",
    re.IGNORECASE,
)
_BUDGET_LOWER_RE = re.compile(
    r"便宜|省(?:一?点|一些|些)?|降低|降(?:一?点|一些)|压缩|压(?:低|缩)|下调|调低|"
    r"少花|贵(?:了)|太贵|减少|"
    r"cheaper|less\s+expensive|too\s+expensive|too\s+pricey|reduce|lower|"
    r"安く|高すぎ|予算を下げ|저렴|비싸|절약",
    re.IGNORECASE,
)
# 抱怨「钱没花完 / 太省了」：用户**已经给了预算**，诉求是"把它用足"，不是"换个预算"。
# 这类不该再追问一句目标数（他给了），该直接带原稿把开销抬上去。
BUDGET_UNDERUSE_RE = re.compile(
    r"花不完|用不完|没花完|没用完|花不出去|不够花|太省|省太多|"
    r"spend\s+it\s+all|use\s+it\s+all|not\s+using\s+(?:\w+\s+)?enough",
    re.IGNORECASE,
)


def budget_adjust_direction(message: str) -> str | None:
    """预算调整的**方向**：`'raise'` / `'lower'` / `None`（没方向，或根本不是调整）。

    两侧同时命中（「预算高一点低一点都行」这类含糊句）时返回 None —— 不猜，
    由调用方落回默认文案。
    """
    text = message or ""
    if not is_budget_adjust(text):
        return None
    up = bool(_BUDGET_RAISE_RE.search(text))
    down = bool(_BUDGET_LOWER_RE.search(text))
    if up and not down:
        return "raise"
    if down and not up:
        return "lower"
    return None


def is_budget_underuse(message: str) -> bool:
    """用户是不是在抱怨「预算没花完 / 花得太省」（= 要求把开销抬上去）。"""
    return bool(BUDGET_UNDERUSE_RE.search(message or ""))


# ---------------------------------------------------------------- 行程内迭代（市场对标）
# 成熟产品（Layla / Mindtrip / TripGenie）的核心闭环是：出稿之后用户**改**它 ——
# 「第 3 天太紧凑了」「把第 2 天换成博物馆」「住宿换便宜点」「第 2 天下雨有备选吗」。
# 这些句子不给任何数值槽位，旧逻辑一律当「你没说信息」→ 把缺槽追问原样重刷一遍
# （真 bug：2026-10-06 市场对标探针 M2/M4/M5/M10 全中）。识别出来交给生成阶段按
# 「上一版行程 + 本次修改诉求」重排，才是迭代，而不是从零重来。
_DAY_REF_RE = re.compile(
    r"第\s*(?:\d+|[一二三四五六七八九十两]+)\s*(?:天|日|站|晚|个?白天)"
    r"|\bday\s*\d+|\b\d+(?:st|nd|rd|th)\s+day"
    # 日 / 韩的「第 N 天」：市场对标测试里日文用户说「3日目はきつい」——
    # 只认中文锚点会把外语迭代当成「没给信息」，又掉回缺槽追问。
    r"|(?:\d+|[一二三四五六七八九十]+)\s*日目"
    r"|(?:\d+|[一二三四五六七八九十]+)\s*일차",
    re.IGNORECASE,
)
_REVISE_VERB_RE = re.compile(
    r"换(?:成|掉|一个|一下|个)?|改(?:成|掉|一下|为|一改)|调整|重排|重新排|重来|"
    r"删(?:掉|去)|去掉|取消(?:这个|该项)?|加(?:上|入|一个|点)|减(?:少|掉|一点|一些)?|"
    r"多(?:待|留|玩|安排|给)|少(?:待|留|玩|安排)|延长|缩短|往后?挪|往?前挪|"
    r"change|swap|replace|adjust|add|remove|switch(?:ing)?(?:\s+to)?|more\s+time|less\s+time",
    re.IGNORECASE,
)
_ITIN_ASPECT_RE = re.compile(
    r"行程|安排|路线|行程表|计划|景点|玩法|住宿|酒店|民宿|美食|餐厅|吃饭|餐饮|"
    r"交通|出行|购物|活动|门票|节奏|天数|时间|预算|花费|开销|花销|总价|"
    # 英文的行程要素词也必须进表 —— 否则「Change the hotel to something cheaper」的
    # 判据②（修改动词 + 要素词）拿不到要素词，会掉进「预算调整」被追问一句目标预算
    # （真 bug 2026-10-06 长会话 C2；连我们自己的关联问题模板「Switch to cheaper hotels」
    # 也中招）。
    r"hotels?|accommodation|hostel|lodging|restaurants?|itinerary|schedule|transport",
    re.IGNORECASE,
)
# 「第 2 天下雨有备选吗」：带行程锚点的天气问 —— 要的是**给那天安排备选**，
# 不是查实时天气（旧行为误判成 weather realtime，主体还被切成「第 2 天」，见 M4）。
_RAIN_BACKUP_RE = re.compile(r"下雨|雨天|weather|rain|台风|下雪", re.IGNORECASE)
# 「预算怎么分配到住宿和吃饭」：问的是**这一趟**的预算构成，不是通用常识。
_BUDGET_SPLIT_RE = re.compile(r"分配|怎么分|如何分|拆分|构成|明细|花在哪|用在哪|breakdown")


def is_plan_revision(message: str) -> bool:
    """这一句是不是在**修改当前行程**（而不是给新槽位、也不是问事实）。

    三条判据（任一成立即为真）：
    ① **出现「第 N 天」这类行程锚点** —— 锚点本身就说明它在指向一份行程；
    ② **修改动词 + 行程要素词**（「住宿换成便宜点」「景点多安排一个」）；
    ③ **预算 / 天气 + 分配 / 备选** 这类「关于这一趟」的追问。

    调用方（`graph.clarify_node`）只在会话里**确实已有行程骨架**（有目的地与天数）
    时才据此改道 —— 没有行程可改时退回常规缺槽追问。
    """
    text = message or ""
    if _DAY_REF_RE.search(text):
        return True
    if _REVISE_VERB_RE.search(text) and _ITIN_ASPECT_RE.search(text):
        return True
    if _RAIN_BACKUP_RE.search(text) and _ITIN_ASPECT_RE.search(text):
        return True
    if "预算" in text and _BUDGET_SPLIT_RE.search(text):
        return True
    return False


# ---------------------------------------------------------------- 行程细节问句（问，不是改）
# 与 `is_plan_revision` 的区别：那一类要**改**行程（有修改动词），这一类只是**问**
# 当前行程里的内容 ——「那第三天上午安排什么」「第一天几点开始比较合适」
# 「What is planned for day 3 morning」。
# 真 bug（2026-10-06 长会话 B1/B5）：`_DAY_REF_RE` 命中「第三天」就把整句判成**改稿**，
# 于是这类"问"被当成"重排整份行程"，既不答问题、又白跑一次生成。
# 判据：**出现了指向某天的锚点 + 整个句子是疑问句**，且**没有修改动词**。
_ITIN_Q_ASK_RE = re.compile(
    r"[?？]|吗|呢|什么|啥|几点|多久|多长时间|怎么|怎样|多少|哪|谁|"
    r"\b(?:what|when|how|where|which|who|why)\b",
    re.IGNORECASE,
)
_ITIN_Q_DAY_NUM_RE = re.compile(
    r"第\s*(\d+|[一二三四五六七八九十两]+)\s*(?:天|日)"
    r"|\bday\s*(\d+)\b",
    re.IGNORECASE,
)


def is_itinerary_question(message: str) -> bool:
    """这一句是不是在**问当前行程里的某个细节**（不是给槽位、不是改稿、不是问常识）。"""
    text = message or ""
    if not _ITIN_Q_DAY_NUM_RE.search(text):
        return False
    if not _ITIN_Q_ASK_RE.search(text):
        return False
    # 带修改动词的优先当**迭代**（「把第 2 天换成博物馆」「第 4 天去掉」）。
    if _REVISE_VERB_RE.search(text):
        return False
    # 「第 2 天下雨有备选吗」也是**迭代**（给那天补室内备选），不是问那个字面上的问题 ——
    # 它虽然以「吗」结尾，要的是改稿动作。判据与 `is_plan_revision` 的雨天条款保持一致。
    if _RAIN_BACKUP_RE.search(text):
        return False
    return True


_ITIN_ANSWER_LEAD = {
    "zh": "第 {n} 天（{theme}）的安排：",
    "en": "Day {n} ({theme}) plan:",
    "ja": "{n} 日目（{theme}）の予定：",
    "ko": "{n}일차({theme}) 일정:",
}
_ITIN_ANSWER_TIME_LEAD = {
    "zh": "第 {n} 天从 {first} 开始、最后一项到 {last}。",
    "en": "Day {n} starts around {first} and the last item runs to {last}.",
    "ja": "{n} 日目は {first} 開始、最後の予定は {last} までです。",
    "ko": "{n}일차는 {first}에 시작해 마지막 일정이 {last}까지입니다.",
}
_ITIN_ANSWER_EMPTY = {
    "zh": "第 {n} 天暂无具体安排。",
    "en": "Day {n} has no specific activities yet.",
    "ja": "{n} 日目はまだ具体的な予定がありません。",
    "ko": "{n}일차에는 아직 구체적인 일정이 없습니다.",
}


def answer_from_itinerary(message: str, itinerary: dict[str, Any] | None, language: str = "zh") -> str | None:
    """从**已出稿的行程**里取第 N 天，直接答给用户（确定性、不调模型）。

    只在 `is_itinerary_question` 为真、且该稿真有那一天时返回字符串，否则返回 None
    （调用方据此退回正常链路，不制造"空答案"）。
    """
    if not itinerary:
        return None
    days = itinerary.get("days") or []
    m = _ITIN_Q_DAY_NUM_RE.search(message or "")
    if not m or not days:
        return None
    raw = m.group(1) or m.group(2) or ""
    n = int(raw) if raw.isdigit() else _num(raw)
    if not n or n < 1 or n > len(days):
        return None
    # 优先按 `day` 字段找（行程数组理论上 `day` 从 1 连续递增），找不到再退回下标 ——
    # 别假设数组一定严格 1:1 对齐，模型偶尔会漏一个 day 对象。
    def _day_no(item: Any) -> int | None:
        try:
            return int(item.get("day"))
        except (AttributeError, TypeError, ValueError):
            return None

    picked = next(
        (d for d in days if isinstance(d, dict) and _day_no(d) == n),
        days[n - 1],
    )
    day = picked if isinstance(picked, dict) else {}
    lang = language if language in _ITIN_ANSWER_LEAD else "zh"
    theme = str(day.get("theme") or day.get("area") or "").strip()
    acts = [a for a in (day.get("activities") or []) if isinstance(a, dict)]
    if not acts:
        return _ITIN_ANSWER_EMPTY[lang].format(n=n)
    times = [str(a.get("time") or "").strip() for a in acts if a.get("time")]
    head = ""
    if re.search(r"几点|什么时间|时间安排|what time|what's the schedule", message or ""):
        if times:
            head = _ITIN_ANSWER_TIME_LEAD[lang].format(n=n, first=times[0], last=times[-1]) + "\n"
    lines = [_ITIN_ANSWER_LEAD[lang].format(n=n, theme=theme).strip()]
    for a in acts[:8]:
        t = str(a.get("time") or "").strip()
        name = str(a.get("name") or "").strip()
        lines.append(f"- {t} {name}".strip() if t else f"- {name}")
    return head + "\n".join(lines)


# 撤销 / 放弃**当前行程**的表达（句首）。与社交闸门的 `cancel`（整句只有放弃词、走寒暄
# 引导）互补：这一条针对「算了，先问下签证」「不去了，改成去成都」这类
# **放弃旧行程 + 提出新诉求**的句子 —— 它们不该进寒暄，但也不该把旧行程带过来。
# 真 bug（2026-10-06 变卦探针实测）：会话已记「西安四天」，用户说「算了，先问下签证」，
# 旧行为把 4 天当残留槽位注入签证场景，出了一份「西安 4 日行程（含签证清单）」——
# 用户明明撤了那个行程，却收到一份按它排的稿。
_ABANDON_WORD = (
    r"(?:算了|不去了|不去玩了|不玩了|不搞了|别去了|取消(?:行程|计划|这次)?|作罢|改主意|重新来|重新开始|换个|换一个)"
)
_TRIP_RESET_RE = re.compile(r"^(?:那|这|就)?\s*" + _ABANDON_WORD)

# **整句就是放弃**（「算了不去了」「不去了」「取消」）：这类由寒暄闸门的 cancel 接走、
# 回一句引导语，**不清行程槽位** —— 用户常在这之后「还是去吧」，槽位留着才恢复得回来。
# 真 bug（2026-10-06 实测）：「我想去成都玩」→「算了不去了」→「还是去吧」，目的地丢了。
_FULL_ABANDON_RE = re.compile(
    rf"^(?:那|这|就)?\s*(?:{_ABANDON_WORD}[\s，,、。]*)+(?:吧|了|啊|呢)?[。！？.!?，,\s]*$"
)

# 「撤销行程」时要一起清掉的槽位：目的地 / 天数 / 预算 / 同行人 全是**旧行程**的属性。
# 其余键保留（含 `_` 前缀的调试字段）。
_TRIP_SLOT_KEYS = frozenset({
    "destination", "destination_surface", "destination_aliases", "destination_country",
    "days", "date_range", "start_date",
    "budget", "budget_basis",
    "party", "party_size", "party_raw", "has_children", "has_elder",
})


def is_trip_reset(message: str) -> bool:
    """用户是不是在**放弃旧行程、另提诉求**（「算了，先问下签证」「改去成都」）。

    两个边界（都在 `graph.clarify_node` 里配合使用）：
    - 只在**句首**认（后半句还可以有新诉求）；
    - **整句只是放弃**（「算了不去了」）不算 —— 那种走寒暄闸门的 cancel，且**不清槽位**
      （用户常接着说「还是去吧」，留着才恢复得回来，见 `_FULL_ABANDON_RE`）；
    - 与 `is_delegating` 有交集时让放手优先 —— 「算了，就按你说的办」是放手，不是撤销。
    """
    text = (message or "").strip()
    if not _TRIP_RESET_RE.match(text):
        return False
    return not _FULL_ABANDON_RE.match(text)


def clear_trip_slots(slots: dict[str, Any]) -> dict[str, Any]:
    """清掉**行程**相关的槽位，保留其余（撤销旧行程时用，见 `is_trip_reset`）。"""
    return {k: v for k, v in (slots or {}).items() if k not in _TRIP_SLOT_KEYS}


# 「把之前那份行程再给我看看」—— 用户要**重看**已有行程，既不是改、也不是重排。
# 真 bug（2026-10-06 长会话 E1 实测）：「北京的行程再给我看看」被当成缺槽请求，回了一句
# 四连问（目的地已记下，却还问天数 / 预算 / 人数）—— 用户想看刚出过的那份稿，被反问。
# 关键：句子里必须**同时**有「行程类名词」和「看 / 发我」的动作。光有「看看」不算
# （「帮我看看签证」不是），光有「行程」也不算（「把第 3 天的行程换成博物馆」是改稿）。
_ITIN_NOUN = r"(?:行程表|行程|方案|计划|攻略|安排)"
_LOOK_VERB = r"(?:看看|看一下|看一眼|看一遍|瞧一眼|瞅一眼|看下)"
_ITIN_RECALL_RE = re.compile(
    r"(?:"
    rf"(?:再|又|重新|再次)?\s*(?:给|发|拿)?\s*我?\s*{_LOOK_VERB}\s*[^。！？，,]{{0,10}}?{_ITIN_NOUN}|"
    rf"{_ITIN_NOUN}[^。！？，,]{{0,12}}?(?:再|又|重新|再次)?\s*(?:给|发|拿)?\s*我?\s*{_LOOK_VERB}|"
    rf"{_ITIN_NOUN}[^。！？，,]{{0,12}}?(?:发|给)\s*我(?:一下|一份|看看|看下)?|"
    r"(?:show|give|send|re-?show)\s+(?:me\s+)?(?:the|my|that|our)?\s*(?:itinerary|schedule)|"
    r"(?:show|give|send)\s+(?:me\s+)?(?:the|my|that|our)\s+(?:plan|trip)|"
    r"(?:what|where)(?:'s| is| was)\s+(?:the|my|our)\s+(?:itinerary|plan|schedule)|"
    r"remind\s+me\s+(?:of\s+)?(?:the|my|our)\s+(?:itinerary|plan)|"
    r"(?:my|the)\s+itinerary\s+again"
    r")",
    re.IGNORECASE,
)


def is_itinerary_recall(message: str) -> bool:
    """这一句是不是「把之前的行程再给我看看」（见 `_ITIN_RECALL_RE` 的说明）。"""
    return bool(_ITIN_RECALL_RE.search(message or ""))


def first_missing(missing: list[str]) -> list[str]:
    """按 SLOT_ORDER 取**第一个**缺槽 —— 纯应答时只问这一个，别一次砸四个。"""
    ordered = [s for s in SLOT_ORDER if s in (missing or [])]
    return ordered[:1]


def apply_delegation_defaults(slots: dict[str, Any]) -> dict[str, Any]:
    """放手表达时，把排程骨架里**还没定的**几项补成常规默认。

    只补 days / party / budget 三项：它们是排行程的必要骨架，也是用户最不想被逐一追问的
    三项。目的地不补 —— 那是唯一"不能替你猜"的槽位（猜错整份行程都跑偏），所以放手时
    若目的地未知，仍会只问这一个问题。
    """
    out = dict(slots or {})
    if not out.get("days"):
        out["days"] = DELEGATION_DEFAULTS["days"]
        out["date_range"] = f"{DELEGATION_DEFAULTS['days']} 天"
    if not out.get("party"):
        out["party_size"] = DELEGATION_DEFAULTS["party_size"]
        out["party"] = f"{DELEGATION_DEFAULTS['party_size']} 位成人"
    if out.get("budget") is None:
        out["budget"] = DELEGATION_DEFAULTS["budget"]
        out["budget_basis"] = "total"
    return out
