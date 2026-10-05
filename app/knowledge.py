"""常识问答：知识库里有没有关于这个主体的东西；没有就用**通用常识**作答。

与 realtime 层的分工
------------------
- realtime 答「现在 / 某天是什么状态」（今天几点升旗、现在开不开门）——
  答案**会过期**，所以有时效闸门，合法产出是「查到没过期的来源」或「明说查不到 + 给入口」。
- 这一层答「这个东西是什么 / 多大 / 哪年」——答案**不会过期**（西湖的面积不随日期变），
  所以没有时效闸门。

真 bug（这一层的存在理由）
------------------------
①「西湖有多大」不含任何时间 / 开放 / 天气 / 交通词，实时闸门不接管，于是掉回
  `clarify_node` 的槽位体检，被回以「目的地？天数？预算？人数？」—— 四个问题全问错方向。
②（2026-09-28 用户复查报出）改成「只查知识库」之后，回答变成了
  「知识库里没有关于「西湖」的条目」—— **依然没有回答「有多大」**。
  用户要的是数字，不是一份「我没有」的说明。同理，「厦门有什么好吃的」这类
  目的地咨询当时还在被追问天数/预算/人数。

所以这一层的结论是：**长尾常识必须由模型答**，没有任何本地资料库能覆盖它。

为什么不调模型的理由在这里不成立
------------------------------
实时链路的红线是「事实断言不该由模型生成」—— 那是因为**时刻会变**，模型必然错
（今天的升旗时间是 06:06 还是 06:07，模型只能猜）。面积、海拔、历史年份不属于这一类：
它们稳定、且是模型训练语料里最扎实的部分。

但这条松动有代价，所以配三道闸：

1. **标注**：`kn.general_knowledge` 明说「未经本知识库核实」，不冒充已核实结论。
2. **复核入口**：`verify` 给一条按主体拼好的检索链接，用户能自己核一遍。
   给一条链接比替用户担保更诚实。
3. **时效性拒答**：提示词（`prompts/answer.md` 规则 4）要求遇到时刻 / 票价 / 开放状态 /
   天气就留空 —— 这是防幻觉的双保险：即使意图闸门漏了，「天安门什么时候升旗」
   也不会在这条链路上被编出一个时刻。

其余口径不变
----------
- **不走向量检索**（用 `store.scan`）：相似度排序为了凑 top_k 一定会返回东西，
  而那些东西会被读成答案。常识问题问的是**主体本身**，不是「跟什么最像」。
- **相关条目要跟着问句变**（`search_knowledge`）：候选池按主体与问句实词取，
  再按**纯词面打分**排序（细节见该函数上方注释）。相关性不用向量 —— 用汉字 bigram
  的命中数，既够准又可复现。
- 命中的知识库条目**不宣称是答案**（文案见 `kn.hits_title`）：问「鼓浪屿有多大」时
  命中的大多是行程攻略，它们谈怎么玩，不是面积。

代价：这条旁路要调一次模型（秒级），比 realtime 的 16–28ms 慢得多。可接受 ——
常识问题本就该等几秒换一个真答案。
"""

from __future__ import annotations

import json
import math
import re
from urllib.parse import quote, urlparse

from pydantic import ValidationError

from .config import Settings
from .i18n import DEFAULT, answer_language_directive, contains, t
from .intent import KnowledgeIntent, knowledge_cfg
from .llm import LLMError
from .prompts import load_prompt
from .schemas import AnswerDraft, AnswerResponse, Chunk, KnowledgeHit, RealtimeChannel
from .slots import resolve_destinations

MAX_HITS = 3

# 方位指代解不出来时填进提示词的说法。提示词是**指令**、恒中文（见 prompts/answer.md 抬头），
# 与用户可见文案无关，所以不走 i18n。
NO_CONTEXT = "（无 —— 这是本次会话里第一句相关的提问，句子里的指代没有可回指的对象。）"


def referent_context(referent: str) -> str:
    """把解出来的方位指代写成人话，供模型消歧（见 `app/intent.py::locative_referent`）。

    真 bug（2026-10-04 用户报）：「上海临港有哪些大学」→「华东师范大学是不是有个新校区在那里」。
    这一句自带新主体（华东师范大学），所以不能整体承接；但「那里」必须解得开，
    否则模型答不出「新校区在哪儿」。
    """
    ref = (referent or "").strip()
    if not ref:
        return ""
    return f"这一句里的「那里 / 这里」指的是本次会话上一轮说的「{ref}」。理解方位指代时按它算。"

# --------------------------------------------------------------- 相关条目检索
# 「知识库里的相关条目」必须**跟着问句变**。
#
# 旧实现只有一道布尔闸门（主体出现在正文里 / 条目目的地就是主体），再按 chunk_id 排序 ——
# 排序里**没有问句**。于是同一个城市的任何问题都列出同样那几条：真 bug（2026-10-04 用户报）
# 「同一会话里连问几题，『知识库里的相关条目』一栏每次一模一样」。
# 更糟的是当时还加了「有目的地条目就只留它们」的硬过滤：库里 destination=上海 只有 1 条，
# 于是「上海外卡支付怎么用」也被吸到那条博物馆条目上，真正讲支付的条目全被丢掉。
#
# 现在改成**按问句做确定性词面打分**（仍然不走向量、不调模型）：
#
#   候选池 = 与主体相关（正文提到主体 / 条目目的地就是主体）**或**命中问句的判别性实词；
#   排序   = 命中实词的出现次数 → 条目目的地就是主体 → 主体出现位置 → chunk_id（固定兜底）。
#
# 为什么用词面而不用向量：这一层不允许同一句话两次跑出两种顺序，bigram 命中数完全可复现；
# 而 164 条的稀疏语料里，词面命中已经足够把「问支付」和「问博物馆」分开。
#
# 一条硬约束：库里对主体一无所知时**返回空**（`related_exists`）—— 那一栏是「关于主体的条目」，
# 不是「同样话题的条目」。少了它，「杭州西湖要门票吗」会列出清迈/厦门的免费点位。
#
# 权重里的两个主体项刻意不对称，而且**弱主体不参与打分**（权重 0）：
# - 主体是**具体主题**（非地名，如「离境退税」）时，它出现在正文就是强信号
#   （「离境退税：面向…」开头那句就是正主），压过实词命中。
# - 主体是**地名 / 通用角色**（「上海」「外国人」）时，它在正文里出现**不给分**，只在
#   **同分**条目之间优先（`-subj_hit` 进排序键）。为什么必须为 0：在这类条目里城市名
#   几乎人人提一句，哪怕只给 1 分，也足以把「只是顺带提了一句上海」的条目顶到
#   「通篇讲这个主题」的条目前面 —— 实测「上海外卡支付怎么用」里地铁条目（正文提到上海）
#   就是靠这 1 分反超两条通篇讲支付的条目，抢了榜首。
W_SUBJECT_TOPIC = 5   # 主体（**具体**的非地名主题，如「离境退税」）出现在正文
W_SUBJECT_WEAK = 0    # 主体是地名 / 通用角色 —— 只做同分兜底，**不参与打分**
W_DEST = 1            # 条目目的地就是主体（比正文提及更具体，但弱于实词命中）
W_TERM = 3            # 判别性实词命中（按**出现次数**计，见 `_TERM_CAP`）
W_SRC = 1             # 实词命中来源字段
# 单个实词最多计几次：正文反复讲同一个词的条目确实更贴题，但不能让一个词包打天下。
_TERM_CAP = 3
# 进池最低分：主体/目的地被提及本身就算「沾边」（1 分），但它排在最后 ——
# 只要有一个判别性实词命中，提及类就被压下去（这就是修前那种「一个城市一条命」的解药）。
MIN_SCORE = 1

# 相对分数截断：低于「本问最高分 × 该比例」的条目**不再列出**。
# 为什么需要它（2026-10-04 用户报「相关条目无关而且明显有固定套路」）：`MAX_HITS` 是
# 一个**硬配额**，只要有候选就一定要凑满三条 —— 于是每条答复的尾巴都是那几条
# 到处沾一点的通用条目（门票省钱 / 博物馆免费日）。配额应该是上限，不是目标。
REL_KEEP = 0.5

# 通用角色词：「外国人怎么买高铁票」切出来的主语是「外国人」—— 它不是**主题**，
# 库里的来华条目几乎每条都提外国人，把它当强主体会让「支付」条目压过「高铁」条目。
# 这类主体按弱信号处理（同地名），排序交给问句实词。
_GENERIC_SUBJECT_RE = re.compile(
    r"^(?:(?:外国|境外|国际|海外|外地)(?:人|旅客|游客|客人|朋友|人士)|"
    r"人|大家|我们|你们|你|我|有人|游客|旅客|客人)$"
)

# 抽实词前先从问句里摘掉的字串：疑问词、度量词、纯语法成分。它们不回答「这条在讲什么」，
# 留着只会把 bigram 切成噪声（「有多大」→「有多 / 多大」，然后「多大」去命中别的条目）。
# 按长度倒序替换，先摘长的（「多少公里」先于「多少」）。
_TERM_STOP_RE = re.compile(
    "|".join(
        sorted(
            {
                "有多大", "多高", "多长", "多宽", "多深", "多远", "多少公里", "多少千米",
                "多少米", "多少年历史", "多少", "几个",
                "什么时候", "几点开门", "几点关门", "几点", "何时",
                "在哪里", "在哪儿", "在哪", "哪个", "哪些", "哪里", "哪儿", "哪一年", "哪年", "哪",
                "是什么", "为什么", "怎么样", "怎样", "怎么", "什么样", "如何", "什么",
                "有没有", "有吗", "有哪些", "有什么", "是否有", "是不是", "是否",
                "能不能", "可不可以", "可以", "要不要", "需不需要", "需要", "应该",
                "值得", "介绍", "说说", "了解", "请问", "麻烦", "一下", "以及", "还有",
                "开放时间", "开放吗",
                # 占位名词与它的修饰语：「具体位置 / 详细地址」这几个字不回答「这条在讲什么」，
                # 留着会去跟正文比字面。真 bug（2026-10-04 实测）：「具体位置在哪儿」的实词
                # 「位置」命中了住宿条目的「**位置**优先于房型」—— 一条讲省钱的条目成了
                # 「华东师范大学的位置」的相关条目。与「有多大」同源：问法不是主题。
                "具体位置", "详细位置", "具体地址", "详细地址", "位置", "地址",
                "具体", "详细",
                # 方位指代词：「那里 / 这里」在这句里指的是上一轮的地点，它**不是这一问的
                # 焦点词**。留着会让标题尾巴多出「那里」、并拿它去跟正文比字面。
                "那里", "这里", "那儿", "这儿", "那边", "这边", "此地", "这个地方", "那个地方",
                "个", "的", "了", "吗", "呢", "吧", "啊", "呀", "是", "我", "你", "他", "它",
                "这", "那", "和", "或", "与", "在", "有",
                # 情态残留：「要不要门票」折成「要门票」后剩一个光杆「要」，
                # 留着会把 bigram 切成「要门」这种假词。只摘它，实词不动。
                "要",
            },
            key=len,
            reverse=True,
        )
    )
)
_HAN_RUN = re.compile(r"[\u4e00-\u9fff]+")
# 「V不V」疑问骨架：一个字 + 不 + 一个字（要不要 / 收不收 / 是不是 / 有没有 / 需不需）。
# 只用来**从标题焦点里摘掉骨架**（见 display_topic）。不以「不」开头的真实词不受影响
# （「不锈钢」在句首时前面没有字，匹配不到）。
# 「V不V」→「V」的折叠（收不收费 → 收费，值不值得 → 值得，要不要 → 要，好不好 → 好）。
# 抽实词前必须折：不折的话 bigram 会切出「收不 / 不收 / 贵不 / 不贵」这种跨骨架假词，
# 它们会命中无关条目（见 `_query_terms`）。
_VV_FOLD = re.compile(r"([\u4e00-\u9fff])不\1")
_VV_FRAME = re.compile(r"[\u4e00-\u9fff]不[\u4e00-\u9fff]")
# 是非问的**确认尾巴**：「…对吗 / 对吧 / 是吧 / 对不对 / 行吗」。它与 V不V 骨架同类 ——
# 只有句法作用，没有可检索的内容。**必须先摘**，否则「校区**对吗**」会切出跨界假词
# 「区对」（`_VV_FOLD` 也救不了「对不对」：它把「对不对」折成一个光杆「对」）。
# 真 bug（2026-10-04 实测）：「区对」正好命中韩国签证条目的「领**区对**特定户籍」，
# 于是「华东师范大学的位置」的相关条目里列出了一条韩国签证 —— 与「收不」命中
# 「收不到短信」是同一类，只是骨架换成了确认尾。
_CONFIRM_TAIL = re.compile(r"(?:对不对|对吗|对吧|是吧|行吗|好吗|是吗)$")
_LATIN = re.compile(r"[a-z0-9]{2,}")

# 「沾主体」判定里**不算数**的通用成分：机构名 / 场所名里到处都是它们。
# 真 bug（2026-10-04 实测）：「华东师范大学」的成分 bigram「大学」命中了「厦门**大学**」
# → 一条**厦门地铁**的条目被算成「讲华东师范大学的」。后果不只是多一条噪声：
# `search_knowledge` 里「库里对这个主体一无所知就返回空」那道闸门依赖这个判定，
# 它被误触发之后，整张候选表（住宿省钱 / 韩国签证）都被当成「相关条目」列了出来。
_GENERIC_COMPONENT = {
    "大学", "学院", "学校", "中学", "小学", "幼儿园",
    "公司", "集团", "大厦", "大楼", "中心", "广场", "公园", "机场", "车站",
}


def _bigrams(run: str) -> set[str]:
    """汉字片段切成 bigram：两字片段本身，三字以上取相邻两字。单字片段不取（噪声太大）。"""
    if len(run) < 2:
        return set()
    if len(run) == 2:
        return {run}
    return {run[i : i + 2] for i in range(len(run) - 1)}


def _subject_keys(subject: str) -> set[str]:
    """主体的匹配写法：整个主体 + 它的汉字 bigram。

    只用来**从问句里摘掉主体**（「上海外卡支付怎么用」先摘「上海」，否则 bigram 会切出
    跨界的「海外」这种假词）。bigram 版本是为了摘干净「杭州西湖」这类复合主体。
    """
    subject = (subject or "").strip()
    if not subject:
        return set()
    keys = {subject}
    for run in _HAN_RUN.findall(subject):
        keys |= _bigrams(run)
    return keys


def _related_keys(subject: str) -> set[str]:
    """「库里有没有讲它」这个判定用的匹配写法：**整个主体** + 去掉通用成分后的成分。

    与 `_subject_keys` 的分工是刻意的：
    - `_subject_keys`：把主体**从问句里摘干净**，越全越好（多摘一个成分只会少一个假词）；
    - `_related_keys`：判断一条库内条目**是不是在讲这个主体**，越准越好。

    混用会把「华东师范大学」的成分 bigram「大学」当成主体 —— 而「大学」在通用语料里
    根本不构成「关于某所学校」的证据（见 `_GENERIC_COMPONENT` 的说明）。
    """
    subject = (subject or "").strip()
    if not subject:
        return set()
    keys = {subject}
    for run in _HAN_RUN.findall(subject):
        keys |= {bg for bg in _bigrams(run) if bg not in _GENERIC_COMPONENT}
    return keys


def _query_terms(message: str, subject_keys: set[str]) -> set[str]:
    """问句的判别性实词：摘掉主体字面与疑问/语法成分后，取汉字 bigram 与拉丁词。"""
    # **先把「V不V」折成实义部分**（收不收费 → 收费，值不值得 → 值得，要不要 → 要）。
    # 不折的话，bigram 会切出「收不 / 不收 / 贵不 / 不贵」这种**跨骨架的假词** ——
    # 真 bug（2026-10-04 实测，用户报的「相关条目无关」的残株）：「上海博物馆收不收费」
    # 抽出的假词「收不」正好命中 eSIM 条目的「收不**到**短信」，于是一条讲上网的条目
    # 被列进了博物馆问题的相关条目。折完只剩「收费」，那条自然出局。
    # 确认尾（「…对吗」）要**先于**折词摘掉，理由见 `_CONFIRM_TAIL`。
    text = _CONFIRM_TAIL.sub(" ", (message or "").strip())
    text = _VV_FOLD.sub(lambda m: m.group(1), text)
    for key in sorted(subject_keys, key=len, reverse=True):
        text = text.replace(key, " ")
    text = _TERM_STOP_RE.sub(" ", text)
    terms: set[str] = set()
    for run in _HAN_RUN.findall(text):
        terms |= _bigrams(run)
    terms |= set(_LATIN.findall(text.lower()))
    return terms


# 「轻触发词」：这些片段出现只是因为它恰好被 triggers 收了（「要预约」「有没有」），
# 它本身不是这一问的**焦点**，算标题时要摘掉。与之相对，「外卡」「离境退税」这类
# 命中片段就是焦点，必须留着。
_LIGHT_CUE = re.compile(
    r"^(?:是不是|是否|有没有|有吗|有哪些|有什么|是否有|免费|收费|要门票|门票|要预约|需要预约|怎么预约|"
    r"开放时间|几点开门|几点关门|在哪里|在哪儿|是什么|怎么样|多高|多长|多宽|多深|多远|多大)$"
)


def display_topic(subject: str, message: str = "", matched: str = "") -> str:
    """卡片标题 / 复核入口用的短标签：**主体 + 这一问的焦点**。

    真 bug（2026-10-04 用户报「明显有固定套路」）：标题恒为「关于「上海」」、复核恒为
    Bing 搜「上海」—— 同一个城市的每一次提问，卡片长得一模一样，用户看到的就是「套路」。
    焦点词取自原话里去掉主体与疑问成分后最长的那段汉字：

        上海有没有免费的博物馆 → 上海 + 博物馆或博览园 = 上海博物馆或博览园
        上海外卡支付怎么用     → 上海 + 外卡支付       = 上海外卡支付
        那要预约吗（承接过来）  → 自己没焦点，就只有主体
    """
    subject = (subject or "").strip()
    if not message:
        return subject
    # 主体不在这一句里 = 它是**上一轮承接来的**（「那里有什么好吃的」的主体是「上海海事大学」）。
    # 这一句自己只剩属性词（好吃 / 好玩 / 附近 / 学校），机械拼上是「上海海事大学好吃」
    # 这种生硬标题 —— 不如直接用主体当标签，等下一句带了新主体再细化。
    # 判据用「主体是否原样出现在这一句」而不是 `carry_over` 标志：这样「上海外卡支付怎么用」
    # 这类**自带主体**的句子照旧能细化成「上海外卡支付」。
    if subject and subject not in message:
        return subject
    rest = message.replace(subject, " ", 1) if subject else message
    # 先摘「V不V」疑问骨架（要不要 / 需不需要 / 收不收费 / 有没有 / 是不是）。
    # 真 bug（2026-10-04 实测）：「上海博物馆要不要门票」去掉主体后剩「要不要门票」，
    # 骨架若不被摘掉，最长汉字段会取到**残片「要不」**，标题成了「关于「上海博物馆要不」」。
    # 骨架里有「不」，抽焦点前必须先摘，否则残片一定比真焦点长。
    rest = _VV_FRAME.sub(" ", rest)
    if matched and _LIGHT_CUE.match(matched.strip()):
        rest = rest.replace(matched, " ")
    rest = _TERM_STOP_RE.sub(" ", rest)
    runs = [r for r in _HAN_RUN.findall(rest) if len(r) >= 2]
    if not runs:
        return subject
    focus = max(runs, key=len)[:12]
    if not subject or subject in focus:
        return focus or subject
    return f"{subject}{focus}"


def subject_place(settings: Settings, subject: str) -> str:
    """主体里的**地点名**（「上海博物馆」→ 上海，「离境退税」→ 空串）。

    会话承接要存它：下一句「开放时间呢」命中实时旁路时，所在城市决定了哪些权威入口
    算对得上（北京问天气不该看到境外城市的入口）。
    """
    scope = _place_scope(settings, subject)
    return scope[0] if scope else ""


def query_terms(subject: str, message: str) -> list[str]:
    """这一问的判别性实词（排序用），供**会话承接**存下来给下一句用。

    公开出来是因为它是「对话式查询扩展」的原料：追问句「那要预约吗」自己只有一个词，
    落不到上一轮的条目上；把它和上一轮实词并起来才对齐（见 `search_knowledge`）。
    """
    return sorted(_query_terms(message or "", _subject_keys(subject or "")))


def _is_place(settings: Settings, subject: str) -> bool:
    """主体是不是地名 —— 已有的目的地词典说了算（「上海」是，「离境退税」不是）。

    直接查词典**包含关系**，而不是走 `resolve_destinations` —— 后者会把 `origin_only`
    的「中国」过滤掉（那是给「哪些国家对中国免签」这种国籍语境准备的），于是
    「中国」在这里被当成一个**具体主题**，权重拉满，任何提到中国的条目都并列第一。
    地名就是地名，不管它出现在句子的哪个角色上。
    """
    table = settings.routes.get("destinations") or {}
    for name in sorted(table, key=len, reverse=True):
        if contains(subject or "", name):
            return True
    return False


def _is_generic_subject(subject: str) -> bool:
    """主体是不是「外国人 / 游客」这类通用角色（不是主题，见 `_GENERIC_SUBJECT_RE`）。"""
    return bool(_GENERIC_SUBJECT_RE.match((subject or "").strip()))


def _dest_parts(settings: Settings, destination: str) -> tuple[str, str]:
    """把 `destination` 串拆成 `(国家, 城市/区域)`。

    条目里的写法是空格分隔的规范名（「泰国 清迈」「中国 厦门」），也可能只有一级
    （「上海」「中国」）。拆不出来（词典里没有的新词）就返回两个空串。
    """
    table = settings.routes.get("destinations") or {}
    country = city = ""
    for token in str(destination or "").split():
        meta = dict(table.get(token) or {})
        kind = meta.get("type")
        if kind == "country":
            country = token
        elif kind in {"city", "region"}:
            city = token
            country = country or str(meta.get("country") or "")
    return country, city


def _place_scope(settings: Settings, subject: str) -> tuple[str, str] | None:
    """主体若是**具体地点**，返回 `(地点名, 所在国)`；否则 `None`（不做地点过滤）。

    主体不一定等于地名：「上海博物馆」的主语不是词典里的词，但它**含有**「上海」，
    作用域就该按上海算 —— 否则「上海博物馆要预约吗」会因为没有作用域，
    又把「北京免费博物馆」列进来。所以这里按**包含关系**找，而且按名字长度倒序，
    免得「泰国」先于「清迈」命中。
    """
    table = settings.routes.get("destinations") or {}
    for name in sorted(table, key=len, reverse=True):
        meta = dict(table.get(name) or {})
        if meta.get("origin_only") or not contains(subject or "", name):
            continue
        kind = meta.get("type")
        if kind in {"city", "region"}:
            return name, str(meta.get("country") or "")
        # 只提到国家（「来华签证怎么办」）→ 不作用域：全国各城的条目本来就都该看到。
        return None
    return None


def _place_compatible(
    settings: Settings, scope: tuple[str, str], entry_destination: str
) -> bool:
    """条目地点与问句主体是否相容（**过滤**，不是分层）。

    | 条目绑定 | 与主体相容的条件 |
    |---|---|
    | 没写目的地（通用条目） | 永远相容 |
    | 具体城市         | 同城 —— 同国不同城**不算**（问上海不该列北京） |
    | 只到国家         | 同国 |

    真 bug（2026-10-04 用户报）：问「上海博物馆要预约吗」，卡片里列了
    `gen-in-museum-002`（**北京**免费博物馆）与 `fam-cn-002`（**北京**铁道博物馆）。
    三条里两条跟上海无关 —— 这就是「相关条目与问题无关」最直白的样子。
    """
    if not str(entry_destination or "").strip():
        return True
    subject_city, subject_country = scope
    entry_country, entry_city = _dest_parts(settings, entry_destination)
    if entry_city:
        return entry_city == subject_city
    if entry_country:
        return bool(subject_country) and entry_country == subject_country
    return True


def search_knowledge(
    *,
    settings: Settings,
    store,
    intent: KnowledgeIntent,
    message: str = "",
    extra_terms: object = (),
    limit: int = MAX_HITS,
) -> list[Chunk]:
    """在 scene / general 层找与**这一问**相关的条目。**不走向量、不调模型。**

    三段式，每一步都对应一类真实缺陷：

    | 步骤 | 做什么 | 少了它会怎样 |
    |---|---|---|
    | **候选池** | 沾主体（正文提到 / 条目就是它）**或**命中问句实词 | 只按主体找 → 同一个城市任何问题都列同样几条 |
    | **地点作用域** | 主体是具体地点时滤掉异城异国条目 | 问上海列出北京 / 厦门（用户 2026-10-04 报的「无关」） |
    | **打分排序** | 实词按出现次数 × **IDF** 加权 | `免费 / 门票 / 预约` 这类到处都是的词把通用条目顶上来（「固定套路」） |

    `message` 是用户的**原话**：排序要用的就是原话里的实词。
    `extra_terms` 是**承接来的一轮实词**（对话式查询扩展，见 `app/intent.py::carry_over_subject`）——
    追问句「那要预约吗」自己只有一个词，并上一轮才落在同一批条目上。
    """
    subject = (intent.subject or "").strip()
    scopes = list(knowledge_cfg(settings).get("scopes") or ["scene:*", "general:*"])
    subject_keys = _subject_keys(subject)
    related_keys = _related_keys(subject)
    terms = _query_terms(message or intent.question_zh, subject_keys) | {
        str(x) for x in (extra_terms or ()) if str(x).strip()
    }
    scope = _place_scope(settings, subject)
    subject_place = _is_place(settings, subject)
    strong_subject = bool(subject) and not subject_place and not _is_generic_subject(subject)
    subject_weight = W_SUBJECT_TOPIC if strong_subject else W_SUBJECT_WEAK

    # 第一遍：定候选池，并统计「这个词在本问的池子里出现在多少条目里」（IDF 的分母）。
    # IDF 必须在**本问的池子**上算 —— 那才是这一问的判别力；在整库上算会把
    # 「来华问答」这个小池子里其实很常见的词算成稀有词。
    related_exists = False
    pool: list[tuple[Chunk, str, int, int, dict[str, int]]] = []
    doc_freq: dict[str, int] = {}
    for chunk in store.scan(filters={"scopes": scopes}, limit=500):
        # 地点作用域：主体是具体地点时，异城条目直接出局（同国不同城也算出局）。
        if scope is not None and not _place_compatible(settings, scope, chunk.destination):
            continue
        text = chunk.text or ""
        source = chunk.source or ""
        # 「沾主体」= 正文提到主体（含它的成分，如「杭州西湖」认「西湖」）或条目就属于它。
        # 判据刻意窄：只是同样谈「门票 / 预约」不算 —— 那属于「话题像」，不是「关于它」。
        # 用 `related_keys` 而不是 `subject_keys`：「大学」这种通用成分不构成"在讲这所学校"
        # （见 `_related_keys` / `_GENERIC_COMPONENT`）。
        related = bool(related_keys) and (
            any(key in text for key in related_keys) or chunk.destination in related_keys
        )
        related_exists = related_exists or related
        dest_hit = 1 if (subject and chunk.destination == subject) else 0
        subj_hit = 1 if (subject and subject in text) else 0
        # 实词按**出现次数**计（封顶 `_TERM_CAP`）：一个条目反复讲「支付」比只是顺带提到
        # 一次更贴题 —— 「上海外卡支付怎么用」里地铁条目只是提了一句「支付宝」，不该抢先。
        counts = {t: min(text.count(t), _TERM_CAP) for t in terms if t in text}
        src_hits = [t for t in terms if t in source]
        # 进榜资格（三条满足其一）：
        #   ① 与问句**有共同词汇** —— 这是最硬的信号；
        #   ② 主体是**具体主题**（非地名，如「离境退税」）且正文真的在讲它 —— 那种主题
        #      提到它本身就是相关，「条件」只是问法；
        #   ③ 条目**就是讲这个主体的**（目的地等于主体），且问句自身没有可用实词。
        # 刻意**不**让「目的地等于主体」单独构成资格：库里 `destination=上海` 只有一条
        # 博物馆条目，那样它就够格回答**每一个**上海问题（「上海的地铁怎么坐」也列表它）——
        # 这正是用户报的「无关 + 固定套路」的成因。
        if not (
            counts
            or src_hits
            or (strong_subject and subj_hit)
            or (dest_hit and not terms)
        ):
            continue
        for t in counts:
            doc_freq[t] = doc_freq.get(t, 0) + 1
        pool.append((chunk, text, dest_hit, subj_hit, counts))

    if not related_exists:
        # 库里对这个主体一无所知 → 返回空。那一栏叫「知识库里的相关条目」，
        # 拿别的话题（甚至别的城市）凑数比空着更坏：实测「杭州西湖要门票吗」会列出
        # 清迈 / 厦门的免费点位 —— 用户在问西湖，给他是误导。
        # 空着时文案（`kn.no_hits`）会明说「本库没有关于它的材料」，那是诚实的信息。
        return []

    # BM25 的 idf 分量（永远为正）。一个词在池子里到处都是 → 接近 0 → 它说了不算。
    # 「免费 / 门票 / 预约」就是这类词：靠它们凑出来的次序，正是用户看到的「固定套路」。
    n_docs = max(len(pool), 1)
    idf = {t: math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5)) for t, df in doc_freq.items()}

    ranked: list[tuple[float, int, int, str, Chunk]] = []
    for chunk, text, dest_hit, subj_hit, counts in pool:
        score = subject_weight * subj_hit + W_DEST * dest_hit
        score += W_TERM * sum(c * idf.get(t, 1.0) for t, c in counts.items())
        score += W_SRC * sum(idf.get(t, 1.0) for t in terms if t in (chunk.source or ""))
        if score < MIN_SCORE:
            continue
        # 主体出现位置只在「一个实词都没命中」的条目之间当判据：那种条目是靠主体进池的，
        # 主体出现得越靠前，越可能是整条在讲它（「离境退税：面向…」vs 别处顺带提一句）。
        pos = text.find(subject) if (subject and not counts and subj_hit) else 0
        # 同分次序：① 条目就是主体（dest_hit）优先；② 正文提到主体（subj_hit）优先；
        # ③ 主体出现位置靠前；④ chunk_id 固定兜底。这些**只在 score 相同时**生效 ——
        # 所以地名主体不会靠「顺带提了一句」翻盘（见权重注释）。
        ranked.append(
            (score, 0 if dest_hit else 1, 0 if subj_hit else 1, pos, chunk.chunk_id, chunk)
        )

    if not ranked:
        return []
    # 固定排序：同一句话两次跑必得同一顺序（这一层不允许有随机性）。
    ranked.sort(key=lambda row: (-row[0], row[1], row[2], row[3], row[4]))
    # 相对截断：`limit` 是**上限**，不是目标。连最高分三分之一都不到的条目，是那种
    # 到处沾一点的通用条目 —— 列出来只会让每张卡片长得一样（用户报的「固定套路」）。
    floor = max(MIN_SCORE, ranked[0][0] * REL_KEEP)
    return [chunk for *_, chunk in [row for row in ranked if row[0] >= floor][:limit]]


# ------------------------------------------------------------------ 模型作答
def _render_hits_block(hits: list[Chunk]) -> str:
    if not hits:
        return "（知识库里没有与主体相关的条目。）"
    lines: list[str] = []
    for c in hits:
        lines.append(f"- source={c.source or '未知'}")
        lines.append(f"  {c.text}")
    return "\n".join(lines)


def build_answer_prompt(
    *,
    settings: Settings,
    intent: KnowledgeIntent,
    hits: list[Chunk],
    message: str = "",
    context: str = "",
    language: str = DEFAULT,
) -> str:
    """装配提示词。**先填 schema 与语言段，再填用户内容** ——

    反过来做的话，用户消息里恰好出现 `{{subject}}` 这种片段就会被二次替换，
    把模板结构打乱。这个顺序让用户内容永远是最后落进去的。

    `context` 是**指代解析结果**（「这一句里的『那里』指上一轮说的『上海临港』」）——
    它和 `message` 一样是用户侧内容，所以也排在最后替换。
    """
    tmpl = load_prompt(settings.prompt_paths.get("answer", "prompts/answer.md"))
    schema = json.dumps(AnswerDraft.model_json_schema(), ensure_ascii=False, indent=2)
    return (
        tmpl.replace("{{output_schema}}", schema)
        .replace("{{language_directive}}", answer_language_directive(language))
        .replace("{{hits_block}}", _render_hits_block(hits))
        .replace("{{subject}}", intent.subject or "")
        .replace("{{context}}", context or NO_CONTEXT)
        .replace("{{message}}", message or "")
    )


def answer_question(
    *,
    settings: Settings,
    llm,
    intent: KnowledgeIntent,
    hits: list[Chunk],
    message: str = "",
    context: str = "",
    language: str = DEFAULT,
) -> AnswerDraft:
    """用通用常识作答。**失败不抛异常**，返回空 draft。

    空 draft 会走「不给数字 + 给复核入口」这条诚实路径。这比抛出去降级成 `degraded`
    更合适：`degraded` 的语义是「行程出稿失败」，跟一个常识问题没有关系。

    空 draft 的 `unknown_reason` 也**不填中文报错** —— 那句话会进用户可见文案
    （`kn.unknown`），必须跟输出语言，所以交给 i18n 的 `kn.no_model_answer`。
    """
    try:
        raw = llm.complete_json(
            role="generator",
            prompt=build_answer_prompt(
                settings=settings,
                intent=intent,
                hits=hits,
                message=message,
                context=context,
                language=language,
            ),
            schema=AnswerDraft.model_json_schema(),
            context={
                "task": "answer",
                "subject": intent.subject,
                "question": message,
                "referent": context,
                "language": language,
                "hits": [c.text for c in hits],
            },
        )
    except LLMError:
        return AnswerDraft()
    if not isinstance(raw, dict):
        return AnswerDraft()
    try:
        return AnswerDraft.model_validate(raw)
    except ValidationError:
        return AnswerDraft()


OFFICIAL_LINKS_MAX = 2


def verify_links(
    settings: Settings,
    intent: KnowledgeIntent,
    hits: list[Chunk] | None = None,
    topic: str = "",
) -> list[RealtimeChannel]:
    """按主体拼复核入口。主体为空就**不给** —— 编一条搜索链接等于给假入口。

    顺序有讲究：**先给命中条目自带的官方页面**（那是这一问的权威落点），再给一条通用检索。
    真 bug（2026-10-04 用户报「固定套路」）：这里原先永远只有一条「Bing 搜索「上海」」，
    问博物馆、问支付、问地铁，复核栏长得一模一样 —— 而库里明明存着
    `https://www.shanghaimuseum.net/` 这样的官网地址。
    `name` 一律用**主机名**：名字必须如实描述点开落在哪儿，不能用条目的 source 描述
    （它可能列了三个馆，而 URL 只指向其中一个）。
    """
    label = (topic or intent.subject or "").strip()
    out: list[RealtimeChannel] = []
    seen: set[str] = set()
    for chunk in hits or []:
        url = str(chunk.source_url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            RealtimeChannel(
                name=urlparse(url).netloc or url,
                url=url,
                what=f"命中条目引用的来源：{chunk.source}" if chunk.source else "命中条目引用的来源",
            )
        )
        if len(out) >= OFFICIAL_LINKS_MAX:
            break

    cfg = dict(knowledge_cfg(settings).get("verify") or {})
    tmpl = str(cfg.get("url_template") or "")
    if not tmpl or not label:
        return out
    out.append(
        RealtimeChannel(
            name=str(cfg.get("name") or "{query}").replace("{query}", label),
            url=tmpl.replace("{query}", quote(label)),
            what=str(cfg.get("what") or ""),
        )
    )
    return out


def build_answer_response(
    *,
    settings: Settings,
    intent: KnowledgeIntent,
    hits: list[Chunk],
    draft: AnswerDraft | None = None,
    message: str = "",
    language: str = DEFAULT,
) -> AnswerResponse:
    """组装答复。整段用户可见文案跟输出语言；`hits[].source/url` 与 `name/url` 是引用，保持原样。"""
    draft = draft or AnswerDraft()
    # 标题 / 复核入口用「主体 + 焦点」而不是光秃秃的主体 —— 否则同一个城市的每一次提问，
    # 卡片标题与复核链接都长得一样（用户报的「固定套路」，见 display_topic）。
    topic = display_topic(intent.subject, message, intent.matched) or intent.subject
    note = t("kn.head", language)
    if draft.answer:
        # 给了答案就必须标注它的来源性质：通用常识，不是本知识库核实过的结论。
        # 少了这句，用户会把模型答的数字当成库里的权威数据 —— 这正是要防的。
        note += " " + t("kn.general_knowledge", language)
    else:
        # 模型没给答案（按提示词拒答时效性问题、把握不足、或调用失败）→ 如实说，不凑一句。
        reason = draft.unknown_reason or t("kn.no_model_answer", language)
        note += " " + t("kn.unknown", language, reason=reason)
    if not hits:
        # 「本库没有相关材料」也是信息：用户才知道这不是「库里查到的」结论。
        note += " " + t("kn.no_hits", language, subject=topic)

    return AnswerResponse(
        subject=intent.subject,
        topic=topic,
        message_echo=message,
        question_zh=intent.question_zh,
        answer=draft.answer,
        confidence=draft.confidence,
        unknown_reason=draft.unknown_reason,
        answer_title=t("kn.answer_title", language, subject=topic),
        verify_title=t("kn.verify_title", language),
        hits=[
            KnowledgeHit(
                chunk_id=c.chunk_id,
                text=c.text,
                source=c.source,
                source_url=c.source_url,
                destination=c.destination,
            )
            for c in hits
        ],
        verify=verify_links(settings, intent, hits=hits, topic=topic),
        note=note,
        plan_hint=t("rt.plan_hint", language),
        disclaimer=t("kn.disclaimer", language),
    )
