"""意图闸门：这条请求要的是「行程规划」，还是在问一个**事实**。

事实分两种，各有一道闸门，都在 `clarify_node` 的槽位体检**之前**跑：

| 事实 | 例子 | 闸门 | 旁路 |
|---|---|---|---|
| 会变的（实时） | 天安门什么时候升旗 | `detect_realtime_intent` | clarify → realtime → output |
| 不会变的（静态） | 西湖有多大 | `detect_knowledge_intent` | clarify → knowledge → output |

两道闸门都拦不住的（真正的排行程、或两者都不是）才进槽位体检。

真 bug（这一层的存在理由）
------------------------
「天安门什么时候升旗」走进 `clarify_node`，被行程槽位体检规则判为缺
`destination / date_range / budget / party`，于是回了一句：

    还需要确认：想去的城市或国家是哪里？计划玩几天，或者大致哪几天出发？
    这次大概的预算是多少（人民币）？一行几个人，有小孩或长者同行吗？

四个问题**全问错了方向** —— 用户没打算去任何地方玩几天，他问的是一个每天都会变的时刻。
排行程才需要那四个槽位；问事实只需要「问的是什么 + 知识库有没有答案 + 去哪查」。

第二形态（2026-09-28 复查发现）：「西湖有多大」是**静态**事实，不含任何时间词，
所以连实时闸门都不命中 → 照样掉回槽位体检 → 照样回那四问。故补第二道闸门。

判定口径（每道闸门都要同时满足两个条件）
--------------------------------------
1. 命中 `routes.yaml::intents.<闸门>.triggers` 里的正则；
2. **当前这句没抽出任何行程槽位**（天数 / 预算 / 人数）。

第 2 条是「让路」规则，防止把行程请求误吞：「去厦门 5 天，顺便问下鼓浪屿几点开门」
有天数 → 仍按行程走，开放时间由 `opening_hours` 工具与行前清单承接。
常识闸门另有第 3 条让路：句中出现规划动词（「厦门怎么玩」）→ 仍在排行程。

**不调模型**：这一步给 clarify 用，判定必须确定性。同一句话两次跑出两种意图，
用户会看到「有时追问、有时查事实」。分类有随机性（`ecnu-plus`）这件事，
在这一层不允许重演。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .slots import resolve_destinations

INTENT_KEY = "realtime_fact"

# 出现任一个 = 用户在排行程 → 实时闸门让路。
# 刻意不把 `destination` 放进来：「去北京玩，升旗几点」仍然是在问事实。
PLAN_SLOT_KEYS = ("days", "budget", "daily_budget", "party_size", "date_range", "start_date")

# 主语提取：句首的客套话与句尾的语气词都要剥掉，否则 subject 会变成
# 「请问天安门」这种不能当检索词看的东西。
_HEAD_NOISE = re.compile(
    r"^(?:请问|麻烦|想问|想问问|问一下|想了解|想知道|帮我查(?:一下)?|查一下|查查|"
    r"hello|hi|hey|please|can you tell me)[,，、:：\s]*",
    re.IGNORECASE,
)
_TAIL_NOISE = re.compile(r"[\s?？!！。，,、;；:：的了吗呢啊呀吧]+$")
# matched 里的疑问词/时间词（它们对检索没用，留在 query 里会稀释向量；留在主语里会让
# 「厦门下周天气」的主语变成「厦门下周」而不是「厦门」，相关性闸门就再也对不上了）
_QUERY_STOP = re.compile(
    r"什么时候|几点|何时|是否|有没有|正常|开放吗|今天|明天|后天|大后天|今日|明日|现在|"
    r"下周|本周|这周|下个月|周末|未来几天|未来|最近|"
    r"what time|when|is it|today|tomorrow|next week|this week|weekend|"
    r"いつ|何時|今日|明日|언제|오늘|내일",
    re.IGNORECASE,
)
_MAX_SUBJECT = 16

# 度量问句的主语常拖一个尾巴：「天安门高多少米」→ 切在「多少米」前 → 主语「天安门高」。
# 不洗掉它，知识库相关性闸门就再也对不上「天安门」（正文里写的是「天安门广场」，
# 没有「天安门高」这个串）。只剥末尾，不动中间。
_METRIC_TAIL = re.compile(
    r"(?:的)?(?:高度|长度|面积|海拔|人口|规模|宽度|深度|多高|多长|多大|多深|高|长|宽|深)$"
)

# 疑问/动作尾巴：问句的动词落在触发片段之前时会被切进主语里。
# 真 bug（2026-10-04）：「外国人怎么买高铁票」的触发片段是「高铁票」，前半截切出来是
# 「外国人怎么买」—— 这个主语在正文里永远不存在（正文写「外国旅客乘高铁」），相关性闸门
# 于是全灭，命中 0 条。剥掉「怎么买 / 怎么坐 / 怎么办」这类尾巴，主语才是「外国人」。
# 末尾还带**系动词/存在动词**残留的情形（2026-10-04 用户报的「相关条目无关」里挖出来的）：
# 「巴黎铁塔有多高」的触发片段是「多高」，切出来的前半截是「巴黎铁塔有」—— 这个主语在库里
# 永远不存在（正文写「埃菲尔铁塔」/「巴黎铁塔」），于是候选池空的，相关条目一条都列不出。
# 所以把「有 / 是 / 在 / 的」也当作可剥的尾巴。
_QUESTION_TAIL = re.compile(
    r"(?:怎么|怎样|如何|可以|能不能|可不可以|要不要|需不需要|应该|想要)?"
    r"(?:买|购买|订|预订|坐|乘坐|乘|用|使用|办|办理|申请|走|去|来|做|弄|搞|预约|支付|有|是|在)?$"
)

# 只在「主体已经被切成一个代词/光杆」时才算退化 —— 这时上一轮的主体必须接管。
# （「那要预约吗」切出来是「那」；「这个呢」切出来是「这个」。）
_DEGENERATE_SUBJECT = {
    "那", "这", "它", "他", "她", "其", "该", "刚才", "上面", "前面", "这个", "那个", "这些",
    "那些", "呢", "吗", "吧", "还有", "以及", "那里", "这里", "哪儿", "哪里",
}

# 纯功能词骨架：它们**只有句法作用、没有任何可检索的内容**。
# 命中片段落在句首时，主体提取会退化成「用命中片段当主体」（那对「离境退税」这类
# **主题名**是对的），可骨架不是主题 —— 拿它去检索，卡片标题会变成「关于「是不是」」，
# 而榜首是拿这几个字跟库里的散文比字面（纯噪声）。
# 真 bug（2026-10-04 用户报「华东师范大学是不是有个新校区在那里」掉进行程四连问时挖出）：
# 补了「是不是」骨架之后，「是不是要提前预约」这类**以骨架开头**的句子就会踩到这里。
_FUNCTION_SKELETON = {
    "是不是", "是否", "有无", "有没有", "有吗", "是否有", "有哪些", "有什么",
    # 评价 / 属性类 V不V 骨架（`routes.yaml` 的对应触发词一起加的）。命中落在句首时
    # 主体会退化成骨架本身，「关于「能不能」」是纯噪声 —— 一律判退化。
    "要不要", "需不需要", "能不能", "可不可以", "值不值得", "值不值", "贵不贵",
    "好不好", "开不开放", "收不收费",
}

# 命中片段是**纯疑问 / 动作短语**（不是内容主题名）时的排除表。
# 用途见 `detect_knowledge_intent` 的退化分支：「进寺庙要注意什么」前半截只切出一个动词
# 「进」，主体退化；而命中的「寺庙」是**内容名词**，可以直接当主体 —— 但「要预约」「在哪里」
# 这类命中片段本身没有内容，退化时只能退回槽位体检，不能拿它当主体。
_PURE_QUESTION = re.compile(
    r"^(?:在哪(?:里|儿|个城市)?|是哪一年|哪一年|哪年|建于|始建于|建成(?:于)?|成立(?:于|时间)?|"
    r"什么时候建的|是谁|属于哪|是什么|为什么|怎么样|有什么(?:区别|不同)?|有哪些|多少|"
    r"要预约|需要预约|怎么预约|开放时间|几点开门|几点关门|要门票|门票|免费|收费|"
    r"值不值得|值不值|贵不贵|好不好|开不开放|收不收费|"
    r"是不是|是否|有没有|有吗|有无|有什么|能不能|可不可以|要不要|需不需要)+$"
)

# 「这一句是不是中文」—— 英文 / 韩文的主题几乎总是**命中片段本身**（etiquette / customs /
# taboos / 팁），而"命中片段之前那一截"是疑问 + 助动词，断在词中间还会切出 "some Ch" 这种
# 残片（真 bug 2026-10-06 文化探针：「What are some Chinese dining etiquette rules…」的
# 卡片标题成了「some Ch」）。日文有汉字，不走这条。
_HAN = re.compile(r"[\u4e00-\u9fff]")

# **占位名词**：它们不自带任何信息，只是指着上一轮那个东西说「它的位置 / 地址」。
# 真 bug（2026-10-04 用户实测）：「它还有个滴水湖校区对吗」→「具体位置在哪儿」，
# 后半句的主体被切成了「具体位置」这个占位词 —— 库里永远对不上，答案是**空串**。
# 归入「退化主体」：有上一轮承接时用它，没有时退回槽位体检（至少不是空答案）。
# 刻意不收属性名词（门票 / 票价 / 价格）：那些**可以**单独成句被答（「门票多少钱」），
# 收进来会把它们从「有主体」打成「没主体」。
_PLACEHOLDER_NOUN = {
    "位置", "具体位置", "详细位置", "地址", "具体地址", "详细地址", "详情", "具体情况",
    # 方位副词同理：「附近有什么好玩的」「周边呢」都指着上一轮那个地方，自己不是主体。
    # 不判别的话主体会变成「附近」—— 库里对不上，模型也拿不到落点（只能拒答）。
    "附近", "周边", "旁边", "这一带", "那一带", "这一片", "那一片",
}

# 代词打头的碎片：「那个学校」「那它」「这个馆」—— 指代只是在**指着上一轮那个东西**，
# 它不构成一个主体。只认「代词 + 通用名词」（或代词 + 代词）这一形状：
# 光看「以代词开头」会误伤真实专名（「那不勒斯」也以「那」开头）。
_ANAPHORIC_HEAD = re.compile(
    r"^(?:这个|那个|这些|那些|这种|那种|这家|那家|这里|那里|这边|那边|这|那|它|他|她|其|该)"
)
_GENERIC_HEAD_NOUN = {
    "学校", "大学", "学院", "中学", "小学", "地方", "城市", "馆", "景点", "景区",
    "酒店", "宾馆", "饭店", "餐厅", "公园", "广场", "车站", "机场", "校区", "公司",
    "集团", "中心", "大厦", "大楼", "博物馆", "纪念馆", "美术馆", "科技馆", "图书馆",
    "东西", "事物", "事情", "玩法", "价格", "门票", "人", "事",
}


def _is_anaphoric_fragment(subject: str) -> bool:
    """「那个学校 / 那它 / 这个地方」这类**指代碎片** —— 它不是主体，是「上一轮那个东西」。"""
    s = (subject or "").strip()
    m = _ANAPHORIC_HEAD.match(s)
    if not m:
        return False
    rest = s[m.end() :].strip()
    if not rest:
        return True
    return rest in _GENERIC_HEAD_NOUN or _ANAPHORIC_HEAD.match(rest) is not None



def _is_degenerate_subject(subject: str) -> bool:
    """主体是不是「接不上话的碎片」：空、单字、代词、纯功能词骨架、占位名词、指代碎片。"""
    s = (subject or "").strip()
    # 「单字 = 退化」只对**中文 / ASCII**成立：中文单字多是虚词（「那」「用」），
    # 而韩文用空格分词，一个单字**就是一个完整词** —— 真 bug（2026-10-06 文化探针实测）：
    # 韩文「중국에서 팁 문화는 어떻게 되나요?」的主体「팁」（小费）被 `len < 2` 判成退化，
    # 于是整句去承接上一轮的**日文**主体，卡片标题成了「中国の食事」。
    if len(s) < 2 and (s.isascii() or _HAN.match(s)):
        return True
    if s in _DEGENERATE_SUBJECT or s in _FUNCTION_SKELETON or s in _PLACEHOLDER_NOUN:
        return True
    # 事件 / 动作引导残片（「买到」「不小心」）—— 见 `_EVENT_LEAD`：这种主体在库里必然
    # 搜不到，命中片段才是这一问真正的主题。剥掉引导后剩不下东西才算退化，
    # 免得把「遇到**麻烦**」这类真主体也误判掉。
    if _EVENT_LEAD.match(s) and len(_EVENT_LEAD.sub("", s).strip()) <= 1:
        return True
    return _is_anaphoric_fragment(s)


# 「V不V」疑问骨架（要不要 / 需不需要 / 收不收费）的**残片**。
# 真 bug（2026-10-04 用户报「回复异常」时实测出的系统性缺陷）：触发片段落在骨架中间
# （「要不要**门票**」「收不**收费**」）时，前半截会切出「上海博物馆要不」「上海博物馆收不」
# 这种残片。这个主语在库里永远对不上，卡片标题也会变成「关于「上海博物馆要不」」，
# 复核链接变成 Bing 搜「上海博物馆要不」。
# 残片形状固定：末尾是「<字>不<字>?」。只在**末尾**剥，且允许连剥（「需不需」→空）——
# 不以「不」收尾的真实主体（「不锈钢厂」）不受影响。
_VV_RESIDUE = re.compile(r"[一-龥]?不[一-龥]?$")

# 事件 / 动作引导残片：触发词落在句中时，前半截常常只剩「买到 / 不小心 / 遇到」这类
# **动作引导**，它不是主体 —— 真 bug（2026-10-06 生活场景校验，两条端到端 hits=0）：
#   「买到**假货**怎么维权」→ 主体「买到」；「不小心**发烧**了该打什么电话」→ 主体「不小心」。
# 主体在库里永远搜不到字面 → `search_knowledge` 的「库里对这个主体一无所知」闸门直接返回空，
# 用户看到「本库没有相关材料」，而库里其实有假货维权与急救电话两条。
# 判成退化后走既有分支：改用**命中片段**（「假货」「发烧」）当主体。
_EVENT_LEAD = re.compile(
    r"^(?:买到|买了|遇到|碰到|摊上|撞上|发现|出了|出现|不小心|一不小心|突然|忽然|万一|如果|要是|听说|据说|感觉|觉得)"
)
# 尾部的「有什么 / 有哪些 / 是什么」骨架：「吃饭的时候**有什么**禁忌吗」→「吃饭的时候」。
# 只剥完整的三字形态，且剥完至少还剩两个字（`买什么` 这种主体剥了就没了）。
_HAVE_RESIDUE = re.compile(r"(?:有什么|有哪些|有没有|是什么|做什么|吃什么|玩什么|买什么)$")


def _strip_question_tail(subject: str) -> str:
    """剥掉主语末尾的疑问/动作成分；剥成空串就退回原值（主语只可能变小、不会丢）。"""
    out = (subject or "").strip()
    for _ in range(2):
        new = _VV_RESIDUE.sub("", out).strip()
        if not new or new == out:
            break
        out = new
    for _ in range(2):
        new = _HAVE_RESIDUE.sub("", out).strip()
        if len(new) < 2 or new == out:
            break
        out = new
    stripped = _QUESTION_TAIL.sub("", out).strip()
    return stripped or out


# 主语**开头**的疑问骨架：前半截常把骨架和光杆动词一起切进来
# （「能不能用**信用卡**支付」→「能不能用」）。剥掉骨架后若只剩光杆（「用」），
# 退化判定会接手，改用命中片段（「信用卡」）当主体。
_LEADING_FRAME = re.compile(
    r"^(?:(?:what|how|why|where|when|which|who|whose)\s+about\b[^A-Za-z]*|"
    r"(?:what|how|why|where|when|which|who|whose)\s+"
    r"(?:(?:are|is|am|was|were|do|does|did|can|could|should|would|will)\s+)?|"
    r"(?:are|is|am|was|were|do|does|did|can|could|should|would|will)\s+|"
    r"是不是|是否|有没有|有吗|有哪些|有什么|能否|能不能|可不可以|要不要|需不需要|"
    r"值不值得|值不值|贵不贵|好不好|开不开放|收不收费|"
    # **找店 / 找东西的疑问引导**（「哪里能吃到本地人常去的**馆子**」）：命中词在句尾，
    # 前半截于是整段是「哪里能吃到本地人常去的」—— 主体被切成一句疑问句，卡片标题
    # 跟着变成「哪里能吃到本地人常」（真 bug 2026-10-06 菜品探针实测，也是「馆子」这类
    # 词当初不敢收的原因）。剥掉开头的「哪里/哪儿 + 能/可以 + 动词 (+到)」，前半截就
    # 只剩真正的定语（「本地人常去的」），与命中词拼成「本地人常去的馆子」。
    r"哪里(?:能|可以|会)?(?:吃|找|买|坐|去|玩|看|用|点|喝|租|打)?(?:到|上|起)?|"
    r"哪儿(?:能|可以|会)?(?:吃|找|买|坐|去|玩|看|用|点|喝|租|打)?(?:到|上|起)?|"
    r"哪有(?:能|可以)?|哪能(?:吃|找|买|坐|去|玩|看|用|点|喝)?)+",
    re.IGNORECASE,
)


def _strip_leading_frame(subject: str) -> str:
    return _LEADING_FRAME.sub("", (subject or "").strip())


# 句首的**纯疑问骨架**：命中片段本身就是个问句开场词（「为什么」「What is」「Why do」），
# 它没有内容 —— 这一句的主体在它**后面**。
# 真 bug（2026-10-06 文化探针实测）：命中处于句首时一律拿命中片段当主体 →
# 「为什么中国人不喜欢数字4」的卡片标题成了「为什么」，「What is the tipping culture in
# China?」成了「What is」；更糟的是这一轮的 `last_fact_subject` 被记成「为什么」，下一句
# 承接追问（「那去餐厅要给小费吗」）的标题也跟着成了「为什么」。
_INTERROGATIVE_FRAME = {
    "为什么", "为何", "为啥", "是什么", "干什么", "干嘛",
    "what is", "where is", "why is", "why are", "why do", "why does", "why did",
    "how much", "how many", "how long", "how far", "how old", "how big", "how tall",
    "tell me about", "history of", "area of",
    "なぜ", "どうして",
    "왜",
}


# **前导铺垫**：中文问句常在主题前垫一段背景 / 身份 / 疑问副词 —— 它们只有句法作用，
# 既不是这一问的主题，也进不了检索，更不该出现在卡片标题上。
# 真 bug（2026-10-06 日常功能探针实测，四句一个病）：
#   「我第一次来中国，吃饭有什么礼仪要注意吗」→ 主体切出「我第一次来中国，吃饭」；
#   「那去餐厅要给小费吗」                    →「那去餐厅要给」；
#   「我是外国人，在中国用手机支付怎么弄」      →「我是外国人，在中国用手机」；
#   「怎么用支付宝扫码坐地铁」                 →「怎么用」（前半截只剩疑问副词）。
# 四句的主题其实都在后面（吃饭 / 小费 / 手机 / 支付宝）。
# 刻意**不在 `detect_knowledge_intent` 里用它改主体**：主体还担着相关性闸门，动它风险大；
# 这里只服务展示层（`knowledge.display_topic`），与「剥离英文疑问碎片」同一定位。
_TOPIC_PREAMBLE = re.compile(
    r"^(?:"
    # ① 话语标记：承接上文的开头词（「那…」「还有…」「顺便…」）
    # ⚠️ 「那 / 这」后面**紧跟量词**时它不是话语标记、而是指示代词的一部分
    #    （「**这道**菜里有没有猪肉」「**那家**店还开着吗」）—— 剥掉它标题就成了
    #    「道菜里猪肉」「家店」（真 bug 2026-10-06 菜品探针实测）。
    #    一步之差：「那去餐厅要给小费吗」的「那」后面是**动词**，照旧要剥。
    r"(?:(?:那|这)(?![个道家碗份辆条张件套杯盘锅只群双片样类些块口句位本座趟间扇束壶瓶笼碟])"
    r"|那么|这个|然后|所以|还有|以及|另外|其实|就是|就是说|对了|话说|顺便|不过|况且|呃|嗯)"
    # ①′ 指示代词 + 量词（「这道菜」「那家店」）：整段剥掉会让主体丢半截，
    #    上面那条已经用负向前瞻放过了它们，这里不再重复处理。
    # ② 第一人称 / 身份 / 「第一次来」这类自我介绍
    r"|(?:我们|咱们|咱|本人|自己|我)(?:也)?(?:是|叫)?(?:一?个?|一?名?)?"
    r"|(?:外国人|外国游客|外国朋友|外国旅客|游客|旅客|老外|背包客)"
    r"|(?:第一次|初次|首次)(?:来|到|去|在)?(?:中国|这儿|这里|当地|国内)?"
    r"|作为(?:一?名?|一?个?)?(?:外国人|游客|旅客)?"
    # ②′ 礼貌语 / 咨询语（「想问问」「请问」「咨询一下」）—— 同样是铺垫，主题在后面
    r"|(?:想|要|打算|准备|希望)?(?:请问|麻烦|问问|问一下|问下|咨询一下|咨询|了解一下|想了解|想问|了解下)"
    # ③ 疑问副词框架（「怎么用」「如何办」）—— 主题在它后面
    r"|(?:怎么|如何|怎样|咋样|咋)(?:样)?(?:才)?(?:能|可以|可|应该|该)?"
    r"(?:用|弄|搞|操作|办理|办|做|去|坐|乘|乘坐|买|付|支付|扫|叫|打|租|获取|申请)?"
    # ④ 方位铺垫（「在中国…」「在北京…」）。⚠️ 地点表必须**必选**：写成可选会连光杆「在」
    #    一起吃掉（「在线支付」被剥成「线支付」）。
    r"|在(?:中国|这儿|这里|当地|国内|这个国家|北京|上海|广州|深圳|成都|西安)"
    r")[，,。、；;：:\s]*"
)

# 主体开头的**光杆动词**（「用手机」→「手机」，「去餐厅」→「餐厅」）。
# 只在剩余长度 ≥ 2 时剥，免得把「来华」剥成「华」。
_TOPIC_LEAD_VERB = re.compile(r"^(?:去|来|用|坐|买|吃|住|玩|逛)")

# 「动词 + 事件/时间后缀」：见 `trim_topic_preamble` 里的保护 —— 这种整体不该剥光杆动词。
_VERB_EVENT_HEAD = re.compile(
    r"^(?:去|来|用|坐|买|吃|住|玩|逛)[^，,。；;！!？?]{1,8}"
    r"(?:的时候|时|之前|之后|以前|以后|前后|期间|时侯)$"
)

# 主体/焦点末尾的语气与骨架残留（「那去餐厅要给」→「餐厅」，「支付怎么弄」→「支付」）。
_TOPIC_TAIL = re.compile(
    # ⚠️ 这里**刻意不剥**「里 / 中 / 内 / 上」这类方位后缀 —— 试过，代价是先把地名吃掉：
    #    「上海」→「海」、「中国」→「国」，标题塌成「海色菜」「国菜单」（2026-10-06 菜品探针）。
    #    中文里方位字既是后缀、又是地名尾字的情形太多，剥尾部单字得不偿失。
    r"(?:要|给|是|的|了|吗|呢|吧|啊|和|跟|向|怎么|如何|怎样|怎么办|怎么弄|弄|搞|操作|办|办理|做|用)+$"
)
_TOPIC_SEP = " ,，。、；;：:!！?？\t\n"


def trim_topic_preamble(text: str) -> str:
    """剥掉展示标签里的**前导铺垫 + 末尾骨架**；剥空了返回空串（调用方自会退回候选）。

    只用于展示（卡片标题 / 复核入口）。逐段剥、每轮必须有进展，最多 6 轮。
    """
    out = (text or "").strip(_TOPIC_SEP)
    for _ in range(6):
        m = _TOPIC_PREAMBLE.match(out)
        if not m or m.end() == 0:
            break
        nxt = out[m.end() :].lstrip(_TOPIC_SEP)
        if nxt == out:
            break
        out = nxt
    # 光杆动词只在还剩 ≥3 字时剥（剥完至少留 2 字），最多剥 3 次（「去要用手机」→「手机」）
    # ⚠️ 「动词 + 时间/事件后缀」整体不剥：「吃饭的时候」「去看展之前」里动词是整个事件的
    #    一部分，剥掉只剩「饭的时候」「看展之前」—— 真 bug（2026-10-06 生活场景校验）：
    #    「吃饭的时候有什么禁忌吗」的标签成了「饭的时候禁忌」。
    if not _VERB_EVENT_HEAD.match(out):
        for _ in range(3):
            m = _TOPIC_LEAD_VERB.match(out)
            if not m or len(out) < 3:
                break
            out = out[m.end() :]
    for _ in range(4):
        trimmed = _TOPIC_TAIL.sub("", out).strip(_TOPIC_SEP)
        if not trimmed or trimmed == out:
            break
        out = trimmed
    return out.strip(_TOPIC_SEP)


def _strip_metric_tail(subject: str) -> str:
    out = subject or ""
    # 连着剥到不动为止（「天安门高度」→「天安门」），但不允许剥成空串再退回。
    for _ in range(3):
        new = _METRIC_TAIL.sub("", out).strip()
        if not new or new == out:
            break
        out = new
    return out


@dataclass(frozen=True)
class RealtimeIntent:
    """实时事实问询的结构化判定结果。"""

    info_type: str          # schedule / opening_hours / weather / transport
    attr: str               # 要查的属性（中文，进检索式）
    subject: str            # 问的主体：天安门 / 鼓浪屿 / 厦门
    place: str | None       # 主体所在地（能从词典解析出来才有）：北京
    kinds: tuple[str, ...]  # 这一类型只认哪些 metadata.kind（相关性闸门的一半）
    matched: str            # 命中的原话片段（便于排查误判）
    question_zh: str        # 规范化中文检索式（检索恒中文，见 app/i18n.py 边界 1）
    channels: list[dict[str, Any]] = field(default_factory=list)
    # 相关性判据（来自 routes.yaml::intents.realtime_fact.triggers[].relevance）：
    #   "subject"（默认）= 用问句里命中片段**之前**那一截当主体（天安门 / 北京）；
    #   "matched"       = 用**命中的片段本身**当主体（政策类：「过境免签」就是主体，
    #                     它不是地名，切前面那一截只会切出「请问」或一长串原话）。
    relevance: str = "subject"


def realtime_cfg(settings: Settings) -> dict[str, Any]:
    intents = settings.routes.get("intents") or {}
    return dict(intents.get(INTENT_KEY) or {})


def _clean_fragment(text: str) -> str:
    return _QUERY_STOP.sub(" ", text or "").strip()


def _subject(message: str, cut: int, place_surface: str | None) -> str:
    """取问句的主语：命中片段之前的那一截。

    天安门什么时候升旗 → cut 在「什么时候」 → 主语「天安门」
    鼓浪屿开放时间     → 主语「鼓浪屿」
    北京明天天气怎么样 → 剥掉「明天」 → 主语「北京」（等于地点 → 走 kind 闸门）
    提取不出来时退回地名原话（这是最稳的兜底：没有主体但有地点，至少能按地点查）。
    """
    raw = _HEAD_NOISE.sub("", (message or "")[:cut])
    head = _clean_fragment(raw).strip() if raw.strip() else ""
    head = _TAIL_NOISE.sub("", head).strip()
    if 0 < len(head) <= _MAX_SUBJECT:
        return head
    if place_surface:
        return place_surface
    return _TAIL_NOISE.sub("", (message or "").strip())[:_MAX_SUBJECT]


def _channels_for(cfg: dict[str, Any], info_type: str, place: str | None, country: str | None) -> list[dict[str, Any]]:
    """按信息类型取权威入口，并按问题所在地过滤。

    北京问天气不该看到境外城市的入口 —— 给错地方的官方入口，用户点进去查的是
    另一个城市/国家，**比不给链接更坏**。真 bug：「西湖什么时候开门」曾因为地名解析
    不出来就整段跳过过滤，把北京旅游网（天安门升旗指南）给了杭州的问题。

    所以：地点解析不出来时**只保留通用入口**（配置里不写 region 的那类）。
    一条都不剩就走「该类型暂无已核实的权威入口」文案 —— 那是诚实的空缺，不是失败。
    """
    out = []
    for c in list((cfg.get("channels") or {}).get(info_type) or []):
        region = c.get("region")
        # 「对得上」= region 等于解析出的城市，或等于解析出的国家。
        # place / country 都为空时集合里只有 None，带 region 的入口全部落选（这正是要的）。
        if region and region not in {place, country}:
            continue
        out.append(dict(c))
    return out


def detect_realtime_intent(
    message: str,
    settings: Settings,
    slots: dict[str, Any] | None = None,
    hint_subject: str = "",
    hint_place: str = "",
) -> RealtimeIntent | None:
    """判定实时事实问询；不是就返回 None（调用方继续走行程链路）。

    `hint_subject` / `hint_place`：会话承接来的主体与所在地。追问句（「开放时间呢？」）
    自己没有主体，靠它才答得到点上（见 `carry_over_subject`）。
    """
    cfg = realtime_cfg(settings)
    triggers = cfg.get("triggers") or []
    if not triggers or not (message or "").strip():
        return None

    # 闸门 2：这句已经在排行程了 → 让路，别把行程请求吞成事实问询
    slots = slots or {}
    if any(slots.get(k) for k in PLAN_SLOT_KEYS):
        return None

    hit: tuple[dict[str, Any], re.Match[str]] | None = None
    for trigger in triggers:
        for pattern in trigger.get("re") or []:
            m = re.search(pattern, message, re.IGNORECASE)
            if m:
                hit = (trigger, m)
                break
        if hit:
            break
    if hit is None:
        return None

    trigger, match = hit
    info_type = str(trigger.get("type") or "")
    attr = str(trigger.get("attr") or "")
    kinds = tuple(str(k) for k in (trigger.get("kinds") or []))

    destination, country, _names, surface = resolve_destinations(message, settings)
    place = destination
    subject = _subject(message, match.start(), surface)
    relevance = str(trigger.get("relevance") or "subject")
    if relevance == "matched":
        # 政策类的主体就是**命中那条政策名本身**（「过境免签」「单方面免签」），
        # 与它落在句中哪个位置无关。旧代码只在命中处于**句首**时才这么取：
        #   「单方面免签来华可以停留多久」→ 命中在句首 → 主体=政策名 ✓
        #   「那单方面免签的国家有哪些」→ 命中不在句首 → 走通用切分，切出前半截「那」
        #   （实测卡片主体/标题成了「那」），虽然检索式里仍带命中片段、产出对得上，
        #   但展示层很难看。政策类不承接、主体恒为政策名，去掉位置条件即可。
        subject = match.group(0)

    # 承接：短句、无地名、会话里有上一轮主体 → 主体与所在地都接过来。
    # 这条必须放在 relevance 处理**之后**：否则政策类会把承接来的主体再覆盖掉。
    #
    # **政策类（relevance=matched）根本不承接** —— 它的主体是政策名本身，
    # 问的是国家层面的规定，跟上一轮那个城市没有任何关系。
    # 真 bug（2026-10-05，切换过境/免签入口后实测）：
    #   先问「天安门什么时候升旗」→ 再问「240 小时过境免签适用哪些国家」
    #   place 被承接成「北京」，而 policy 条目的 destination 是「中国」，
    #   检索的地点过滤把它们全滤掉 → found 从 2 掉到 0，卡片标题变成「天安门 · policy」。
    #   明明库里有两条现行政策，却只丢给用户一堆官网链接。
    if (
        hint_subject
        and not destination
        and relevance != "matched"
        and len((message or "").strip()) <= FOLLOWUP_MAX_CHARS
    ):
        subject = hint_subject
        place = hint_place or place

    # 检索式：主体 + 命中片段里的实词 + 属性。检索恒中文（与 app/i18n.py 边界 1 一致）。
    parts = [subject]
    fragment = _clean_fragment(match.group(0))
    if fragment and fragment not in subject and fragment not in attr and attr not in fragment:
        parts.append(fragment)
    if attr and attr not in parts[-1]:
        parts.append(attr)
    question_zh = " ".join(p for p in parts if p).strip()

    return RealtimeIntent(
        info_type=info_type,
        attr=attr,
        subject=subject,
        place=place,
        kinds=kinds,
        matched=match.group(0),
        question_zh=question_zh,
        channels=_channels_for(cfg, info_type, place, country),
        relevance=relevance,
    )


# --------------------------------------------------------------------------- 常识闸门
# 与实时闸门并列的第二道意图闸门。两者都在 `clarify_node` 的槽位体检**之前**跑。
KNOWLEDGE_KEY = "knowledge_question"


@dataclass(frozen=True)
class KnowledgeIntent:
    """静态事实问询的结构化判定（「西湖有多大」—— 不随时间变的那种）。"""

    subject: str      # 问的主体：西湖 / 天安门 / 鼓浪屿
    matched: str      # 命中的原话片段（便于排查误判）
    question_zh: str  # 规范化中文检索词（检索恒中文，见 app/i18n.py 边界 1）
    # trigger = 命中度量 / 定义类正则；consult = 目的地咨询兜底；
    # followup = 上一句的主体由会话承接而来（见 carry_over_subject）。
    # 分开记是为了排查误判：用户报「莫名其妙的追问」时，先看它走的是哪条路径。
    source: str = "trigger"


def knowledge_cfg(settings: Settings) -> dict[str, Any]:
    intents = settings.routes.get("intents") or {}
    return dict(intents.get(KNOWLEDGE_KEY) or {})


# --------------------------------------------------------------------------- 会话承接
# （2026-10-04 用户报「没有对话记忆」）追问句自己**不含主体**：
#
#     上海有没有免费的博物馆 → 「那要预约吗？」「开放时间呢？」「多少钱？」
#
# 这种句子按独立一句处理必然判错 —— 上一版就是回一句「想去的城市？玩几天？预算多少？
# 一行几个人？」。成熟对话系统（Rasa 的 slot carry-over / Dialogflow 的 output context）
# 都会把上一轮解析出的主体带过来。这里做同一件事，且**确定性**（不调模型）。
FOLLOWUP_MAX_CHARS = 20

# 追问的形态特征：短、没有地名、带疑问或属性提示。
# 「那要预约吗」= 指示词 + 属性；「开放时间呢」= 属性 + 语气词；「多少钱」= 属性。
_FOLLOWUP_CUE = re.compile(
    r"[?？]|吗|呢|"
    r"开放时间|开放|营业|门票|预约|多少钱|价格|票价|几点|怎么|如何|要不要|需不需要|需要|"
    r"值不值|值得|远不远|大不大|贵不贵|好玩|好吃|能不能|可以|有吗|哪些|多久|多远|"
    # 是非问骨架（真 bug 2026-10-04，见 `_FUNCTION_SKELETON` 上方的说明）。
    # 「是不是还有一个校区」以骨架开头、句尾没有「吗/呢/？」，原先一条线索都不命中，
    # 于是这句**接不上上一轮的学校**，被当成新问题 → 掉回槽位体检查成排行程。
    # 「有没有 / 有无」同理（「有没有别的馆区」）。
    r"是不是|是否|有没有|有无|"
    # **占位名词**：它们不自带信息，只是指着上一轮那个东西说「它的位置 / 地址」。
    # 真 bug（2026-10-04 用户实测）：「它还有个滴水湖校区对吗」→「具体位置在哪儿」，
    # 后半句的主体被切成了「具体位置」这个占位词 —— 库里永远对不上，答案是空串。
    r"具体位置|详细位置|位置|具体地址|详细地址|地址"
)

# 方位指代：**「那里 / 这里」指的是上一轮说过的那个地方**（不是「哪里」——那是问句词）。
# 真 bug（2026-10-04 用户报）：「上海临港有哪些大学」→「华东师范大学是不是有个新校区在那里」。
# 这一句**自带新主体**（华东师范大学），所以整体承接是错的（会把主体覆盖成上一轮的）；
# 但它里面的「那里」必须解得开，否则模型不知道新校区在哪儿、再下一句「那里有什么好吃的」
# 也会失去落点。所以指代只作为**地点上下文**参与，不动主体。
_LOCATIVE_RE = re.compile(r"那里|这里|那儿|这儿|那边|这边|此地|这个地方|那个地方")


def _contains_place(settings: Settings, text: str) -> bool:
    """这段话里有没有出现词典中的地名（「上海临港」含「上海」→ 是）。"""
    table = settings.routes.get("destinations") or {}
    return any(name and name in (text or "") for name in table)


def locative_referent(message: str, settings: Settings, session) -> str:
    """这一句里的方位指代（「那里」）指向哪儿 —— 没有指代 / 无可指代对象时返回空串。

    解引用顺序只认一条：**上一轮的主体如果是地名就用它**（「上海临港」比城市级的
    `last_fact_place` 更精确，也才答得到「临港有什么好吃的」）；上一轮的主体若不是地名
    （「华东师范大学」），才退到上一轮的**所在地**（那正是「华师大的新校区在临港」的落点）。

    这一层不猜：会话里没有可指代的对象、或这一句自带目的地，就返回空串。
    """
    text = (message or "").strip()
    if not text or not _LOCATIVE_RE.search(text):
        return ""
    last_subject = getattr(session, "last_fact_subject", "") or ""
    last_place = getattr(session, "last_fact_place", "") or ""
    if not (last_subject or last_place):
        return ""
    destination, _country, _names, _surface = resolve_destinations(text, settings)
    if destination:
        return ""
    if last_subject and _contains_place(settings, last_subject):
        return last_subject
    return last_place or last_subject


_KANA_SCRIPT = re.compile(r"[\u3040-\u30ff]")
_HANGUL_SCRIPT = re.compile(r"[\u1100-\u11ff\uac00-\ud7af]")
_LATIN_SCRIPT = re.compile(r"[A-Za-z]")
_SCRIPT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("han", _HAN),
    ("kana", _KANA_SCRIPT),
    ("hangul", _HANGUL_SCRIPT),
    ("latin", _LATIN_SCRIPT),
)


def _scripts(text: str) -> set[str]:
    """这句话里出现了哪些**书写系统**（han / kana / hangul / latin）。

    用来挡住**跨语言承接**：上一轮存的主体是原文，换一种语言再追问时，把上一轮那段
    外文原样当主体带过来，既会让标签串味（日文问句挂上韩文标题），也会把生成模型
    带跑 —— 真 bug（2026-10-06 交通探针 T5-F3）：韩文轮存下的主体「지하철」，被下一句
    **日文**追问原样承接，整段答案写成了韩文（提示词里「用日语作答」被无视）。
    """
    found: set[str] = set()
    t = text or ""
    for name, rx in _SCRIPT_PATTERNS:
        if rx.search(t):
            found.add(name)
    return found


# 主体的收尾清洁：把「怎么问」的填充成分从主体里摘掉 —— 它们不是问的内容。
# 真 bug（2026-10-06 交通探针实测）：
#   ·「故宫一般什么时候人最多」→ 主体「故宫一般」→ 卡片标题「故宫一般人最多」；
#   · 日文「観光地は週末混みますか」→ 主体「観光地は週末」（は 是**主题助词**，
#     週末是这一问的**场合**、不是主体）；韩文 은/는 同理。
_ZH_FILLER_TAIL = re.compile(
    r"(?:一般说来|一般来说|通常来说|一般|通常|大概|大致|大约|差不多|平时)$"
)


def _strip_topic_filler(subject: str) -> str:
    """摘掉主体里的疑问填充成分（见 `_ZH_FILLER_TAIL` 的 bug 记录）。摘空则退回原值。"""
    s = (subject or "").strip()
    if not s:
        return s
    if _KANA_SCRIPT.search(s) and "は" in s:
        # 日文：主题助词「は」之前才是主体（観光地は週末 → 観光地）
        head = s.split("は")[0].strip()
        if len(head) >= 2:
            s = head
    elif _HANGUL_SCRIPT.search(s):
        # 韩文：主题助词 은/는 同理（관광지는 주말 → 관광지）
        m = re.search(r"[은는]", s)
        if m and m.start() >= 2:
            s = s[: m.start()].strip()
    trimmed = _ZH_FILLER_TAIL.sub("", s).strip()
    return trimmed or s


def carry_over_subject(
    message: str, settings: Settings, session, slots: dict[str, Any] | None = None
) -> tuple[str | None, str | None]:
    """这一句是不是在**承接上一轮**？是则返回 `(主体, 所在地)`，否则 `(None, None)`。

    四个条件同时满足才算承接（与另外三道闸门同一口径：确定性、不调模型）：

    | 条件 | 为什么 |
    |---|---|
    | 会话里存过事实主体           | 第一句没有可承接的东西 |
    | 消息短（<= `FOLLOWUP_MAX_CHARS`） | 长消息自带上下文，不需要代它决定 |
    | 没有解析出目的地             | 「换成北京的」自带地名，是新的请求 |
    | 带疑问 / 属性提示            | 陈述句不是追问 |
    | 且不含行程槽位 / 规划动词     | 「那去上海玩三天」仍是行程 |
    """
    text = (message or "").strip()
    last_subject = getattr(session, "last_fact_subject", "") or ""
    if not text or not last_subject:
        return None, None
    if len(text) > FOLLOWUP_MAX_CHARS:
        return None, None
    # 跨语言不承接：上一轮主体与这一句**没有共同书写系统**（韩文主体 vs 日文追问）时，
    # 原样带过来会把标签与作答语言一起带偏（见 `_scripts` 的 bug 记录）。
    # 中文主体（汉字）与日文追问（汉字+假名）有交集，照旧承接 —— 汉字在日文里也读得通。
    if not (_scripts(last_subject) & _scripts(text)):
        return None, None
    slots = slots or {}
    if any(slots.get(k) for k in PLAN_SLOT_KEYS):
        return None, None
    destination, _country, _names, _surface = resolve_destinations(text, settings)
    if destination:
        return None, None
    if not _FOLLOWUP_CUE.search(text):
        return None, None
    for pattern in knowledge_cfg(settings).get("plan_verbs") or []:
        if re.search(pattern, text, re.IGNORECASE):
            return None, None
    # 方位指代优先：「那里好玩吗」的「那里」指的是**上一轮那个地方**，不一定是上一轮的
    # 主体本身（主体可能是「在那个地方的一个东西」）。`locative_referent` 自己会处理
    # 「主体就是地名」这层，所以这里只是在有指代时改用它。
    referent = locative_referent(text, settings, session)
    if referent:
        return referent, (getattr(session, "last_fact_place", "") or "")
    return last_subject, (getattr(session, "last_fact_place", "") or "")


def detect_knowledge_intent(
    message: str,
    settings: Settings,
    slots: dict[str, Any] | None = None,
    hint_subject: str = "",
) -> KnowledgeIntent | None:
    """判定「静态事实问询」；不是就返回 None（调用方继续走行程链路）。

    两条命中路径，**共用同一套让路规则**：

    | 路径 | 判据 | 例子 |
    |---|---|---|
    | `trigger` | 命中 `intents.knowledge_question.triggers`（度量 / 位置 / 年份 / 定义 / 咨询词） | 西湖有多大 |
    | `consult` | 没命中任何正则，但句子里有**可解析的目的地**且是疑问 / 咨询语气 | 厦门有什么好吃的 |

    真 bug（这一层的存在理由）
    ------------------------
    ①「西湖有多大」不含任何时间/开放/天气/交通词 → 实时闸门不接管 →
      掉回 `clarify_node` 的槽位体检 → 被回以「目的地？天数？预算？人数？」。
    ②（2026-09-28 用户复查报出）「厦门有什么好吃的」「厦门好玩吗」「厦门大学怎么样」
      「想了解一下杭州」同样掉进那三问。它们跟预算、人数没有任何关系 ——
      句子里根本不存在「要排几天行程」这件事。所以要的不是再补几个正则，
      而是承认**「有明确目的地 + 在提问」本身就说明这不是一句排行程的指令**。

    口径与实时闸门一致：**确定性、不调模型**。分类走 `ecnu-plus` 有随机性，
    同一句话两次跑出两种意图，用户会看到「有时追问、有时答题」。

    `hint_subject`：会话承接来的主体（见 `carry_over_subject`）。追问句自己没有主体，
    靠它才判得对。
    """
    cfg = knowledge_cfg(settings)
    triggers = cfg.get("triggers") or []
    text = (message or "").strip()
    if not text or (not triggers and not cfg.get("consult_fallback")):
        return None

    # 让路 1：这句已经在排行程了（有天数/预算/人数）→ 交给行程链路
    slots = slots or {}
    if any(slots.get(k) for k in PLAN_SLOT_KEYS):
        return None
    # 让路 2：句中出现规划动词。「厦门怎么玩」是排行程，不是问常识 ——
    # 少了这条，「怎么玩」会被当疑问句式收走。
    for pattern in cfg.get("plan_verbs") or []:
        if re.search(pattern, text, re.IGNORECASE):
            return None

    hit: re.Match[str] | None = None
    for pattern in triggers:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            hit = m
            break

    destination, _country, _names, surface = resolve_destinations(text, settings)

    # 承接：短句、无地名、会话里有上一轮主体 → 这一句的主体就是上一轮那个。
    # 顺手把「疑问句里的主题名词」这条路打开（见下面的让路 3）。
    interrogative = bool(re.search(str(cfg.get("consult_question") or "?"), text, re.IGNORECASE))
    hint = (hint_subject or "").strip()
    followup = bool(hint) and not destination and len(text) <= FOLLOWUP_MAX_CHARS

    # 让路 3：**主题名词**（机票 / 酒店 / 住宿 / 签证）本身只说明「这件事跟它有关」，
    # 不说明「要排行程」。真 bug（2026-10-04 用户报）：
    #   「申根签证要什么材料」「外国人在中国住酒店要注意什么」→ 被 plan_verbs 整条
    #   让给了行程链路 → 回「想去的城市？玩几天？预算多少？一行几个人？」四连问。
    # 判据改成**言语行为**：疑问句（在问事）不让路，祈使句（要我办事）才让路。
    if not interrogative:
        for pattern in cfg.get("plan_topics") or []:
            if re.search(pattern, text, re.IGNORECASE):
                return None

    if hit is None:
        # 路径 B：目的地咨询兜底。
        # 不要求命中任何正则，但要求**目的地解析得出**（不然「今天股市怎么样」也会被收走，
        # 而那种问题本知识库既答不了也不该答），且句子是疑问/咨询语气 ——
        # 「去厦门」没有疑问语气，仍在排行程，不能被这条路径吞掉。
        if not cfg.get("consult_fallback"):
            return None
        consult = str(cfg.get("consult_question") or "")
        # 追问句没有地名，兜底条件换成「会话里有主体 + 是疑问句」。
        if followup and re.search(consult, text, re.IGNORECASE):
            return KnowledgeIntent(
                subject=hint, matched=text[:12], question_zh=hint, source="followup"
            )
        if not destination or not surface or not re.search(consult, text, re.IGNORECASE):
            return None
        return KnowledgeIntent(
            subject=destination, matched=surface, question_zh=destination, source="consult"
        )

    if hit.start() == 0:
        # 命中片段出现在**句首**时切不出前半截。「离境退税需要什么条件」若退回整句，
        # 主体就变成一整句话，任何库内条目都对不上（相关性闸门是「主体出现在正文里」），
        # 模型只拿到「本库没有相关材料」，反而更容易按规则 4 拒答。
        # 这里改用命中的咨询词本身当主体（「离境退税」），它能真的对上库内条目。
        subject = hit.group(0)
        had_leading_frame = False
        # 句首命中时前半截是空的。但「命中片段本身是不是一个**有内容的主题名**」要分开：
        #   · 纯功能骨架（「是不是」「有没有」）→ 这一句**没给自己主体**，可以承接上一轮
        #     （「是不是还有一个校区」必须接回上一轮的学校）；
        #   · 真主题名（「离境退税」「景点推荐」）→ 它**就是**这一句自带的主体，
        #     不许被上一轮覆盖。
        # 真 bug（2026-10-06 变卦探针实测）：F10 会话里「支付怎么弄」答完之后接一句
        # 「景点推荐呢」—— 命中「景点推荐」在句首，却因 own_subject 为空被承接成上一轮的
        # 「支付怎么弄」，卡片标题与答案双双跑偏（问景点，答了支付）。
        own_subject = "" if hit.group(0) in _FUNCTION_SKELETON else hit.group(0)
        # 命中的是纯疑问骨架（「为什么」「Why do」）→ 这一句的主体在**它后面**那一截。
        if hit.group(0).strip().lower() in _INTERROGATIVE_FRAME:
            tail = _TAIL_NOISE.sub("", _clean_fragment(text[hit.end():])).strip()
            if tail:
                subject = tail
                own_subject = tail
    else:
        subject = _strip_question_tail(_strip_metric_tail(_subject(text, hit.start(), surface)))
        # 前半截常把疑问骨架和光杆动词一起切进来（「能不能用**信用卡**支付」→「能不能用」）。
        # 剥掉开头的骨架；剥完只剩光杆（「用」）时，下面的退化判定会接手，改用命中片段当主体。
        before_frame = subject
        subject = _strip_leading_frame(subject)
        # 记录「退化是不是剥前导骨架剥出来的」—— 只有这种退化才该改用命中片段当主体。
        had_leading_frame = subject != before_frame
        # 命中片段**之前**切出来的那一截，就是这一句自己带的主体。
        own_subject = subject
        # **非中文问句**（英 / 韩）：主题几乎总在**命中片段本身**上，而"命中片段之前那一截"
        # 是疑问 + 助动词（"What are some Chinese dining"），断在词中间还会切出 "some Ch"。
        if not _HAN.search(text) and hit.group(0) not in _FUNCTION_SKELETON:
            subject = hit.group(0)
            own_subject = subject

    # 这一句**自带**主体吗？自带就不许被上一轮覆盖。
    # 真 bug（2026-10-04 用户报，两副面孔）：
    #   ①「华东师范大学是不是有个新校区在那里」—— 自带新主体（华东师范大学），
    #     只是句尾夹了个方位指代。若整体承接，主体会被上一轮的「上海临港」顶掉，
    #     用户问的学校就丢了（所以 `locative_referent` 只把它当**地点上下文**）。
    #   ②「是不是还有一个校区」—— 骨架在句首，自己没给主体，必须接上一轮的学校。
    # 判据就是这一条：**前半截切得出一个像样的主体**才算自带。
    has_own_subject = bool(own_subject) and not _is_degenerate_subject(own_subject)
    # ⚠️ 前半截是**事件引导残片**（「买到 / 不小心」）时也要算自带主体 ——
    # 真 bug（2026-10-06 生活探针实测，会话第 4 轮）：刚聊完丝绸店，用户问
    # 「买到**假货**怎么维权」，前半截「买到」退化 → `has_own_subject` 假 →
    # 主体被上一轮的「丝绸」盖掉，卡片标题写着「丝绸」，答案却在讲假货维权。
    # 命中片段（「假货」）就是这一问的主题，配得上自带的资格。
    # 判据刻意**只对事件引导生效**：「那要预约吗」这类命中骨架的句子必须照旧去承接。
    if (
        not has_own_subject
        and _EVENT_LEAD.match(own_subject or "")
        and len(_EVENT_LEAD.sub("", own_subject or "").strip()) <= 1
        and hit.group(0) not in _FUNCTION_SKELETON
        and not _PURE_QUESTION.match(hit.group(0))
    ):
        has_own_subject = True
    if followup and not has_own_subject:
        subject = hint
    elif _is_degenerate_subject(subject):
        # 前半截切不出内容（空、单字、代词、纯骨架、占位名词）。分两种：
        # ① **剥前导骨架**剥出来的退化（「能不能用信用卡支付」→「用」）：命中片段是这一问的
        #    主题名（「信用卡」），用它当主体，别把主题丢掉；
        # ② 其余退化（代词「那」、纯骨架、占位名词「具体位置」）且**没有可承接的上下文**：
        #    退回槽位体检。
        #    真 bug（2026-10-04）：这一支原先照答，卡片标题成了「关于「那」」/「关于「是不是」」；
        #    「具体位置在哪儿」更糟 —— 主体「具体位置」在库里对不上，答案是**空串**。
        #    退回槽位体检是差一点，但至少它问的是「你想问哪里」而不是假装答了一个代词。
        if had_leading_frame and not _is_degenerate_subject(hit.group(0)):
            subject = hit.group(0)
        elif hit.group(0) not in _FUNCTION_SKELETON and not _PURE_QUESTION.match(hit.group(0)):
            # ③ 命中片段是**内容主题名**（「寺庙」「小费」「礼仪」）→ 它就是这一问的主体。
            # 真 bug（2026-10-06 文化探针实测）：「进寺庙要注意什么」前半截只切出一个动词
            # 「进」→ 主体退化 → 整句退回槽位体检，被回四连问。而命中的「寺庙」完全够当主体。
            subject = hit.group(0)
        else:
            return None
    subject = _strip_topic_filler(subject)
    return KnowledgeIntent(subject=subject, matched=hit.group(0), question_zh=subject)


# --------------------------------------------------------------------------- 寒暄闸门
# 第三道前置闸门：**寒暄 / 闲话 / 元问题**（「你好」「在吗」「谢谢」「你是谁」）。
#
# 它比前两道更弱（要求短、无目的地、无行程动词），所以排在最后判：能落进实时 / 常识
# 的句子优先走那两条，剩下的短寒暄才归它。
#
# 真 bug（2026-10-04 用户报）：「你好」掉进 `clarify_node` 的槽位体检 →
# 被回「想去的城市或国家是哪里？计划玩几天？预算是多少？一行几个人？」四连问。
# 用户要的是一句引导，不是一份需求表单。这与「天安门什么时候升旗」是同一错误的第三形态：
# 把「不是排行程的请求」当成了排行程。成熟做法（Rasa 的 chitchat intent / Dialogflow 的
# help + fallback intent）都是给这类输入一个**独立意图 + 独立话术**，而不是让任务流去接。
#
# 判定确定性、不调模型 —— 与另两道闸门同一口径。答复才由模型生成（见 `app/guide.py`）。
SOCIAL_KEY = "social"


@dataclass(frozen=True)
class SocialIntent:
    """寒暄类输入的结构化判定。"""

    kind: str      # greeting / thanks / farewell / meta
    matched: str   # 命中的原话片段（便于排查误判）


def social_cfg(settings: Settings) -> dict[str, Any]:
    intents = settings.routes.get("intents") or {}
    return dict(intents.get(SOCIAL_KEY) or {})


def detect_social_intent(
    message: str, settings: Settings, slots: dict[str, Any] | None = None
) -> SocialIntent | None:
    """判定寒暄 / 闲话 / 元问题；不是就返回 None（调用方继续走行程链路）。

    四条**同时**满足才成立，缺一不可：

    | 条件 | 为什么 |
    |---|---|
    | ① 命中 `intents.social.triggers` | 是寒暄，不是随口一句 |
    | ② 消息很短（<= `max_chars`）     | 长消息更可能是真请求 |
    | ③ 没解析出任何目的地             | 「你好，上海怎么玩」不能被这声招呼吞掉 |
    | ④ 没有行程动词 / 行程槽位        | 「你好，我去上海 3 天」仍是行程 |

    后两条是关键：缺了它们，一句礼貌开场会把后面真正的问题一起带走。
    """
    cfg = social_cfg(settings)
    triggers = cfg.get("triggers") or []
    text = (message or "").strip()
    if not triggers or not text:
        return None

    # ③ 有目的地 → 不是寒暄
    destination, _country, _names, _surface = resolve_destinations(text, settings)
    if destination:
        return None

    # ④ 让路：已在排行程（有槽位）、或句中出现规划动词
    slots = slots or {}
    if any(slots.get(k) for k in PLAN_SLOT_KEYS):
        return None
    for pattern in knowledge_cfg(settings).get("plan_verbs") or []:
        if re.search(pattern, text, re.IGNORECASE):
            return None

    # ② 短句
    if len(text) > int(cfg.get("max_chars") or 24):
        return None

    # ① 命中
    for trigger in triggers:
        for pattern in trigger.get("re") or []:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return SocialIntent(
                    kind=str(trigger.get("kind") or "greeting"), matched=m.group(0)
                )
    return None
