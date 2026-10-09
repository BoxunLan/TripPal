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
from functools import lru_cache
from urllib.parse import quote, urlparse

from pydantic import ValidationError

from .config import Settings
from .i18n import DEFAULT, answer_language_directive, contains, t
from .intent import KnowledgeIntent, knowledge_cfg, trim_topic_preamble
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
# IDF 下限（见 `search_knowledge` 里那段）：池子只有两三条、且条条都命中同一个词时，
# 纯 BM25 的 idf 会趋近 0，把这一问唯一的判别信号判成噪声。0.3 的含义是——
# 一个词**至少**值它出现 1 次的分数的三成（`W_TERM * 1 * 0.3`），不至于被 MIN_SCORE 抹平。
_IDF_FLOOR = 0.3
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
# 词里有拉丁字母 → 匹配时按**整词**算（见 `_term_count`）。
_LATIN_WORD = re.compile(r"[a-z]")

# **英文虚词不参与检索**。真 bug（2026-10-06 菜品探针实测）：英文问句抽出的实词
# 只剩 `is / in / that / problem` 这类虚词，而中文条目正文里的英文专名 ——
# 「**Visa**」「visafor**china**.cn」「SIM」—— 正好含这些子串，于是「 vegetarian
# 在中国方便吗」的相关条目列出了**签证办理流程**与**外卡支付**（`counts={'is': 1}`）。
# 卡片叫「相关条目」，列签证比列空更糟：用户会以为这两件事有关系。
# 中文侧早就有 `_TERM_STOP_RE` 干这件事，英文侧一直没做 —— 同一个病的两侧。
_EN_TERM_STOP = {
    "am", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "done", "have", "has", "had",
    "can", "could", "shall", "should", "will", "would", "may", "might", "must",
    "a", "an", "the", "this", "that", "these", "those", "it", "its",
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "she", "they", "them",
    "in", "on", "at", "to", "of", "for", "with", "about", "into", "from", "by", "as",
    "and", "or", "but", "if", "then", "than", "so", "because",
    "there", "here", "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "any", "some", "all", "more", "most", "much", "many", "very", "really",
    "not", "no", "yes", "ok", "okay", "just", "also", "too", "still", "now",
    "get", "got", "go", "going", "want", "need", "like", "know", "say", "said",
    "problem", "question", "way", "thing", "things", "day", "days", "time", "times",
}

# **跨语言别名**：外文问句要能命中中文条目。种子是中文写的，而这一层的匹配是字面
# 的 —— 英文 `vegetarian` 在中文正文里一个字都对不上，于是外国人用母语问饮食只能
# 拿到「本库没有相关材料」（真 bug 2026-10-06 菜品探针实测：中/英/日/韩四语问同一
# 件事，只有中文命中）。别名表把外文词**显式映射**成中文实词，种子保持干净。
# 表的 key 是**小写外文原词**（含日文假名 / 韩文），value 是中文实词。
# 用「key 是不是出现在原话里」来触发，不做分词 —— 这些词都是短词，分词只会漏。
_ALIAS_TERM_MAX = 24

# 「沾主体」判定里**不算数**的通用成分：机构名 / 场所名里到处都是它们。
# 真 bug（2026-10-04 实测）：「华东师范大学」的成分 bigram「大学」命中了「厦门**大学**」
# → 一条**厦门地铁**的条目被算成「讲华东师范大学的」。后果不只是多一条噪声：
# `search_knowledge` 里「库里对这个主体一无所知就返回空」那道闸门依赖这个判定，
# 它被误触发之后，整张候选表（住宿省钱 / 韩国签证）都被当成「相关条目」列了出来。
_GENERIC_COMPONENT = {
    "大学", "学院", "学校", "中学", "小学", "幼儿园",
    "公司", "集团", "大厦", "大楼", "中心", "广场", "公园", "机场", "车站",
}

# 英文问句切出来的主体碎片，开头常是**疑问/助动词**（"How long" / "can I stay" / "what time"）。
# 这些词只有句法作用、没有可展示的内容 —— 展示时剥掉，剥空了就退回地名。
# 真 bug（2026-10-06 长会话 B2/C3 实测）：英文「How long from the airport to downtown」
# 的卡片标题成了「北京How long」（非中文主体被无空格拼在地名后面）。
_EN_OPENERS = re.compile(
    r"^(?:(?:how|what|when|where|which|who|why|whose|"
    r"is|are|am|was|were|be|been|being|"
    r"do|does|did|can|could|should|would|will|shall|may|might|must|"
    r"i|you|we|they|he|she|it|there|the|a|an|"
    r"long|much|many|far|old|big|large|tall|high|deep|often|soon)\b[\s,]*)+",
    re.IGNORECASE,
)


def _glue(a: str, b: str) -> str:
    """拼展示标签：汉字与拉丁字母相接处补一个空格（「北京 Forbidden City」而不是「北京Forbidden City」）。

    只对「汉字 ↔ 拉丁/数字」补。原先的判据是「汉字 vs 非汉字」，会把**日文假名**也算进来 ——
    真 bug（2026-10-06 日常功能探针实测）：「北京の地下鉄」被拼成「北京の 地下鉄」
    （の 是假名、不是拉丁字母，不该插空格）。"""
    if not a:
        return b
    if not b:
        return a
    a_latin = a[-1:].isascii() and a[-1:].isalnum()
    b_latin = b[0:1].isascii() and b[0:1].isalnum()
    a_han = bool(_HAN_RUN.search(a[-1:]))
    b_han = bool(_HAN_RUN.search(b[0:1]))
    if (a_han and b_latin) or (a_latin and b_han):
        return f"{a} {b}"
    return f"{a}{b}"


def _looks_proper(s: str) -> bool:
    """是不是像个**专名**（每个词首字母大写）："Forbidden City" / "The Bund" 是，"time does the" 不是。"""
    words = [w for w in re.split(r"[\s,，、]+", s or "") if w]
    return bool(words) and all(w[:1].isupper() for w in words)


# **泛义动词不是话题**：「what should I **order**」「can I **get**」里的动作词只说明
# 用户想做什么，说明不了他在聊什么 —— 拿它当标签等于没写（真 bug 2026-10-06：
# 「I don't eat pork, what should I order?」的卡片标题是「order」，而这一问聊的是
# 不吃猪肉能点什么）。碰到它们就退回原话里**更具体的那个实词**（pork）。
_EN_GENERIC_VERB = {
    "order", "eat", "get", "have", "need", "want", "know", "find", "buy", "use",
    "do", "make", "take", "go", "tell", "say", "see", "pay", "bring", "wear", "ask",
}


def _latin_place_names(settings: Settings) -> set[str]:
    """目的地词典里的**拉丁地名与英文名**（China / Shanghai / Forbidden City…）。

    用来把地名从检索实词里剔除，见 `search_knowledge`。
    """
    table = settings.routes.get("destinations") or {}
    out: set[str] = set()
    for name, meta in table.items():
        for cand in [name, *((dict(meta or {}).get("aliases") or []))]:
            cand_s = str(cand or "").strip()
            if cand_s and re.fullmatch(r"[A-Za-z][A-Za-z\s.'-]*", cand_s):
                out.add(cand_s.lower())
    return out


def _en_focus_word(message: str, exclude: set[str] | None = None) -> str:
    """原话里**最后一个**非停用、不在 `exclude` 里的拉丁实词。

    英文碎片当不了标签时的兜底焦点（真 bug 2026-10-06）：
    · `I don't eat pork` 的主体 "don't eat" → 排除掉残片里的词后剩 `pork`；
    · `…get sick in China` 的地点 "China" 已排除 → 剩 `sick`。

    取**最后一个**：英语里越靠后的名词越常是这一问的对象（`in China` 的国名已被排除，
    不会盖在焦点上）。太短的词（<3 字母，如 `is` / `at`）本就没有标题价值。
    """
    banned = {str(x).lower() for x in (exclude or set())}
    out = ""
    for word in re.findall(r"[A-Za-z][A-Za-z'-]*", message or ""):
        low = word.lower()
        # 泛义动词同样跳过（见 `_EN_GENERIC_VERB`）：「…what should I **order**」里它
        # 排在 pork 后面，取"最后一个实词"就会抓到它，标签还是「order」。
        if low in _EN_TERM_STOP or low in banned or low in _EN_GENERIC_VERB or len(low) < 3:
            continue
        out = word
    return out


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


@lru_cache(maxsize=512)
def _latin_term_re(term: str) -> re.Pattern:
    return re.compile(r"(?<![0-9a-z_-])" + re.escape(term) + r"(?![0-9a-z_-])")


def _term_count(text: str, term: str) -> int:
    """实词在文本里的出现次数：**拉丁词按整词，中文按子串**。

    中文没有空格，`text.count` 就是对的；拉丁词按子串会在英文专名里误命中
    （`Visa` 里的 `is`、`visaforchina.cn` 里的 `china`）—— 那是「相关条目」出现
    签证 / 支付条目的直接成因，见 `_EN_TERM_STOP` 处的注释。
    """
    if not text or not term:
        return 0
    if _LATIN_WORD.search(term):
        return len(_latin_term_re(term).findall(text.lower()))
    return text.count(term)


def _alias_idf_groups(settings: Settings) -> list[set[str]]:
    """`term_aliases` 里**同一个 key 的中文词**构成一组（`english → [英文, 英语]`）。

    组内的词是同一概念的中文写法，检索时它们的判别力必须一致 —— 见 `search_knowledge`
    里「同一外文词译成的多个中文写法」那段注释。表在 `routes.yaml::retrieval.term_aliases`。
    """
    # 路径与 `_alias_terms` 一致（表在 `routes.yaml::retrieval.term_aliases`，
    # 不在 `settings.routes` 的顶层）。
    table = (settings.routes.get("retrieval") or {}).get("term_aliases") or {}
    groups: list[set[str]] = []
    for values in table.values():
        if isinstance(values, (list, tuple)):
            words = {str(v).strip() for v in values if str(v).strip()}
            if len(words) > 1:
                groups.append(words)
    return groups


def _alias_terms(settings: Settings, message: str) -> set[str]:
    """把原话里的外文词（`vegetarian` / `채식` / `ベジタリアン`）换成中文实词。

    种子是中文的，检索是字面匹配 —— 不加这一步，外国人用母语问，相关条目永远是空的。
    表在 `routes.yaml::term_aliases`，key 小写匹配。
    """
    text = (message or "").lower()
    if not text:
        return set()
    table = (settings.routes.get("retrieval") or {}).get("term_aliases") or {}
    out: set[str] = set()
    for key, aliases in table.items():
        key_l = str(key or "").lower()
        if not key_l or key_l not in text:
            continue
        for alias in list(aliases or [])[:_ALIAS_TERM_MAX]:
            alias_s = str(alias or "").strip()
            if alias_s:
                out.add(alias_s)
    return out


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
    # **填充副词不算内容实词**：「一般 / 通常 / 大概 / 左右 / 基本」在条目正文里遍地都是
    # （「手续费一般 1%-3%」「一般需要」），一旦让它参与 `W_TERM × IDF`，一条银行卡、
    # SIM 卡条目就会被顶进「故宫什么时候人最多」的相关条目里 ——
    # 真 bug（2026-10-06 交通探针 + 真浏览器复核同时看见：`故宫一般什么时候人最多？`
    # 列出 3 条，去掉句里的「一般」只剩 1 条）。展示层早就有 `_DISPLAY_ADVERB`，
    # 检索侧一直没摘，两边同一个病。
    text = _DISPLAY_ADVERB.sub(" ", text)
    terms: set[str] = set()
    for run in _HAN_RUN.findall(text):
        terms |= _bigrams(run)
    terms |= {w for w in _LATIN.findall(text.lower()) if w not in _EN_TERM_STOP}
    return terms


# 「轻触发词」：这些片段出现只是因为它恰好被 triggers 收了（「要预约」「有没有」），
# 它本身不是这一问的**焦点**，算标题时要摘掉。与之相对，「外卡」「离境退税」这类
# 命中片段就是焦点，必须留着。
# 主体**前面**那半截是不是疑问引导（「哪里能吃…」）。是的话焦点只能取主体**之后**
# —— 见 `display_topic` 里 `rest` 那段注释。只认这几个引导词：别的引导词（「那」「我想」）
# 前面的半截常常本身就是铺垫，切掉会丢信息。
# ⚠️ 事件 / 动作引导（「不小心」「买到」「遇到」）也要算「前缀是铺垫」：
#   「不小心**发烧**了该打什么电话」→ 焦点取到「不小心」，标题成了「发烧不小心」
#   （真 bug 2026-10-06 生活场景校验）。这些词和「哪里 / 怎么」同理：它们是**事件的开场**，
#   不是这一问的对象；命中它们在前面时，焦点只看主体**之后**那半句。
_FOCUS_PREFIX_LEAD = re.compile(
    r"(?:哪里|哪儿|哪有|哪能|怎么|如何|怎样|为什么|为啥|"
    r"不小心|一不小心|突然|忽然|万一|如果|要是|遇到|碰到|买到|买了|发现|听说|据说)"
)

# 焦点的**退化开头**：被动标记（被 / 让 / 叫 / 给）、量词残片（家 / 里）、否定方式
# （才不 / 不会 / 以免 / 免得）之后接一个光杆动词 —— 整段都是「怎么避免 / 去哪做」的问法，
# 不是这一问的对象（见 `display_topic` 里那处判据）。
# 英文主体剥掉开场词后**仍是疑问骨架**（`How long` / `can I stay`）—— 那不是焦点，
# 只能退回地名；`local dishes` / `vegetarian` 这类实义短语不在此列（见 `display_topic`）。
_EN_SKELETON_HEAD = re.compile(
    # ⚠️ 缩写否定（`don't` / `can't`）必须单独列出：`do\b` 匹配不到 `don't` ——
    # 真 bug（2026-10-06 菜品探针 E3）：`I don't eat pork…` 的标题成了「China don't eat」。
    r"^(?:what|how|why|where|when|which|who|whose|whom|can|could|do|does|did|"
    r"is|are|am|was|were|will|would|should|shall|if|any|some|the|a|an|it|there|"
    r"i|we|you|they|to|of|in|on|for|with|about|there|"
    r"don'?t|doesn'?t|didn'?t|can'?t|won'?t|isn'?t|aren'?t|wouldn'?t|couldn'?t)\b",
    re.IGNORECASE,
)

_FOCUS_VERBAL_HEAD = re.compile(
    # 情态前缀（「该打」「要吃」）：真 bug（2026-10-06 生活场景校验）——
    #   「不小心发烧了该打什么电话」的焦点取到「该打」，标题成了「发烧该打」，
    #   而这一问的对象就是「发烧」本身。
    r"^(?:被|让|叫|给|才不|不会|以免|免得|不至于|家|里|该|应该|得|要|能|会|可以)?"
    r"(?:吃|点|喝|做|用|买|卖|坐|走|看|玩|去|找|选|订|挑|弄|搞|办|烫|踩|排队|预约|"
    r"打|拨|叫|问|说|写|填|带|换|退)"
)

_LIGHT_CUE = re.compile(
    r"^(?:是不是|是否|有没有|有吗|有哪些|有什么|是否有|免费|收费|要门票|门票|要预约|需要预约|怎么预约|"
    r"开放时间|几点开门|几点关门|在哪里|在哪儿|是什么|怎么样|多高|多长|多宽|多深|多远|多大)$"
)

# **焦点噪声**：这些词是「怎么问」而不是「问什么」。留在焦点里会产出「寺庙注意」
# 这种半截标签（真 bug 2026-10-06 文化探针实测），摘掉后焦点为空就退回主体（「寺庙」）。
_FOCUS_NOISE = {"注意", "当心", "小心", "留意", "提醒"}

# 只服务展示的**副词噪声**：它们没有信息量，却常被最长片段选中 ——
# 真 bug（2026-10-06 日常功能探针实测）：「末班车一般到几点」的标签成了「末班车一般到」
# （焦点取到「一般到」）。从焦点那半句里摘掉，摘完没有别的片段就退回主体。
_DISPLAY_ADVERB = re.compile(r"(?:一般|通常|大概|大致|大约|差不多|左右|基本)")

# 焦点**收尾**的评价词：「打车方便」「现在去值得吗」里的「方便 / 值得」在问句里是系表结构，
# 不是这一问的对象，留在标题上就成了半截判断句（同上 T1-A3）。只摘词尾，不摘词头 ——
# 「方便的时间」这种「评价词作定语」不受影响。
_FOCUS_JUDGMENT = re.compile(
    r"(?:方不方便|不方便|方便吗|方便|不方便吗|划算吗|划算|合算|合适吗|合适|值得吗|值得|"
    r"推荐吗|推荐|更好|更好一点|好一点|安全吗|安全|可靠吗|可靠|"
    # 「好找 / 难找 / 好订」同理：「清真餐厅好找吗」问的是**清真餐厅**，不是「好找」
    # （真 bug 2026-10-06 菜品探针实测：标题成了「清真餐厅好找」）。
    r"好找|难找|好订|好约|好买|好停|好吃|好玩)$"
)

# 焦点**开头**的程度 / 范围词：「川菜是不是**都很**辣」里的「都很」是量化的口气，
# 不是这一问的对象；把它留在焦点上，标题就成了半截判断句「川菜都很辣」（真 bug
# 2026-10-06 菜品探针 C2-B1）。摘完焦点短到不像话（只剩「辣」一个字）就退回主体。
# ⚠️ 与 `_FOCUS_JUDGMENT` 分工：那个只摘**词尾**的评价词，这个只摘**词头**的口气词，
#    两边都不摘词中间的成分（「别太贵」的「太」还在 —— 那是实质诉求）。
# ⚠️ 别收「不」前缀的否定（「不辣」「不太好」）：否定是用户的真实表达，摘掉就语义反转。
# ⚠️ 只收**≥2 字**的形式：单字「特 / 超 / 很 / 太」会把真词的首字吞掉（「**特**色菜」→
#    「色菜」，2026-10-06 菜品探针实测），得不偿失。
# ⚠️ 也不做**否定**形式（不太 / 不怎么）：那会把用户的主张反过来。
_FOCUS_LEAD = re.compile(
    r"^(?:都很|都挺|还算|也是|那是|真是|确实|倒是|听说|据说|感觉|好像|"
    r"特别|非常|比较|稍微|好像是|据说很)"
)


# 日文假名：判定「这句要不要按日文路子取焦点」（见 `display_topic`）用。
_KANA = re.compile(r"[\u3040-\u30ff]")

_EN_STOPWORD = frozenset(
    """What When Where Which Who Whose How Why Is Are Am Do Does Did Can Could Should
    Would Will The A An And Or But In On At To For Of With From About Any Some It Its I My We You
    Doesnt Dont Please Tell Give Show Have Has Does""".split()
)

_EN_PROPER_RUN = re.compile(r"\b[A-Z][a-zA-Z]{1,}(?:\s+[A-Z][a-zA-Z]{1,}){0,2}\b")


def _longest_proper_noun(message: str) -> str:
    """原话里最长的英文专名（"Forbidden City" / "Beijing"）。

    英文句子到此说明**抽取没能给出专名**（给的是 "crowded" / "least busy" 这类泛词）——
    真 bug（2026-10-06 交通探针 T4）：四句英文的卡片标题分别是 subway / crowded /
    taxi / least busy，看不出问的是哪儿。此时宁可用原话里的专名当标签。
    句首疑问词（How / What）会被专名正则连带进来，先按词剥掉再判断。
    """
    best = ""
    for m in _EN_PROPER_RUN.finditer(message or ""):
        words = m.group(0).split()
        while words and words[0] in _EN_STOPWORD:
            words = words[1:]
        cand = " ".join(words)
        if len(cand) >= 3 and len(cand) > len(best):
            best = cand
    return best


def display_topic(subject: str, message: str = "", matched: str = "", scope: str = "") -> str:
    """卡片标题 / 复核入口用的短标签：**主体 + 这一问的焦点**。

    真 bug（2026-10-04 用户报「明显有固定套路」）：标题恒为「关于「上海」」、复核恒为
    Bing 搜「上海」—— 同一个城市的每一次提问，卡片长得一模一样，用户看到的就是「套路」。
    焦点词取自原话里去掉主体与疑问成分后最长的那段汉字：

        上海有没有免费的博物馆 → 上海 + 博物馆或博览园 = 上海博物馆或博览园
        上海外卡支付怎么用     → 上海 + 外卡支付       = 上海外卡支付
        那要预约吗（承接过来）  → 自己没焦点，就只有主体
    """
    subject = (subject or "").strip()
    # 中文主体里**不该有空格**：那是 `_strip_leading_frame` 摘疑问骨架时替换出来的
    # 痕迹（「那几点出发能避开堵车」→「那 出发能避开」），原样排进标题就是一坨噪点
    # （真 bug 2026-10-06 交通探针 T1-A4）。
    if _HAN_RUN.search(subject):
        subject = re.sub(r"\s+", "", subject)
    # 主体**不跨逗号**：前半截切出来的主体常把第二个分句一起带进来 ——
    # 真 bug（2026-10-06 交通探针 T1-A3）：「从首都机场到市区，打车方便还是坐地铁方便」
    # 主体成了「从首都机场到市区，打车方便还是」，整句原话被搬进卡片标题。
    # 第一个分句（「从首都机场到市区」）才是这一问在聊的东西。
    if "，" in subject or "," in subject:
        head = re.split(r"[，,]", subject)[0].strip()
        # ⚠️ 前半截必须**自己撑得住标题**才切：整句都是铺垫时（「我第一次来中国，吃饭」）
        # head 会被 `trim_topic_preamble` 剥空，切了等于把主体扔掉 ——
        # 真回归（2026-10-06）：上面无脑切让「我第一次来中国，吃饭…」的标签塌成「有什么礼仪」。
        if head and trim_topic_preamble(head):
            subject = head
    # 会话借来的地点（`scope`）在主体自己不带地点时**前置到主体上**：「景点推荐呢」在
    # 聊上海的会话里 → 「上海景点推荐」。否则标题恒为「关于「景点推荐」」，用户看不出
    # 这一问落在哪儿（真 bug 2026-10-06）。主体自带地点时（「上海有什么好玩的」）不动。
    scope = (scope or "").strip()
    # 主体若是**非中文碎片**（英文问句切出来的 "How long" / "can I stay"），管线自己的主语
    # 抽取对它并不可靠 —— 只有在剥掉疑问/助动词后**像个专名**（"Forbidden City"）时才展示，
    # 否则一律退回地名。（真 bug 2026-10-06 长会话 B2/C3：卡片标题成了「北京How long」。）
    if not _HAN_RUN.search(subject or ""):
        stripped = _EN_OPENERS.sub("", subject).strip(" ,，、")
        if stripped and 0 < len(stripped.split()) <= 3 and _looks_proper(stripped):
            # **介词 + 大写专名 = 地点补位，不是焦点**。真 bug（2026-10-06）：
            # 「What should I do if I get sick in China?」的标题落成「China」——
            # 问的是「生病了怎么办」，标签却只有一个国家名，看不出这一问在聊什么。
            # 判据刻意窄：只认 `in / at / to / near / around / from + 专名` 这种补位
            # 结构；「the Forbidden City」前面是冠词，不在这儿动手（那是焦点本身）。
            if re.search(
                r"\b(?:in|at|to|near|around|from)\s+" + re.escape(stripped) + r"\b",
                message or "",
                re.IGNORECASE,
            ):
                focus = _en_focus_word(
                    message, {w.lower() for w in re.findall(r"[A-Za-z']+", stripped)}
                )
                if focus:
                    return f"{stripped} {focus}"
            return _glue(scope, stripped) if scope else stripped
        # **焦点短语优先于地名**。真 bug（2026-10-06 菜品探针实测）：英文问句的标题
        # 几乎一律落成地点单词 —— 「What local dishes should I try in Chengdu?」→
        # 「Chengdu」，「I'm vegetarian…in China?」→「China」。原因是英文分支只在
        # 主体**像个专名**时才用它，否则一律退回 ` _longest_proper_noun`，而 `local dishes`
        # / `vegetarian` / `tap water` 都不是专名 —— 焦点就这么被地名顶掉了。
        # 判据：不是**疑问骨架开头**（那种才是真退化，仍要走地名兜底），就当焦点用，
        # 地名改當**補位**（「Chengdu local dishes」），與中文「上海特色菜」同構。
        if stripped and len(stripped.split()) <= 4 and not _EN_SKELETON_HEAD.match(stripped):
            words = [w.lower() for w in re.findall(r"[A-Za-z0-9'-]+", stripped)]
            # 含**停用词**的碎片是问句残片，不是焦点：「What time **does the** Forbidden City
            # open」剥掉开场词后剩「time does the」，拼上去就是「北京 time does the」
            # （回归用例 `test_english_question_fragment_is_not_glued_onto_the_place`）。
            has_stopword = any(w in _EN_TERM_STOP for w in words)
            # **单词 + 后面紧跟系动词** = 属性而不是对象：「How **crowded** is the Forbidden
            # City」问的是故宫，不是「拥挤」（回归用例
            # `test_english_traffic_label_uses_the_proper_noun_not_the_generic_word`）。
            # 只对**单词**判：短语「local dishes should I try」里的 should 是这个判据的假阳性。
            is_attribute = len(words) == 1 and bool(
                re.search(
                    re.escape(stripped) + r"\s+(?:is|are|was|were|does|do|did|can|could|will|would)\b",
                    message or "",
                    re.IGNORECASE,
                )
            )
            if len(words) == 1 and stripped.lower() in _EN_GENERIC_VERB:
                # 见 `_EN_GENERIC_VERB`：动作词当不了话题，退回更具体的实词。
                focus = _en_focus_word(
                    message, {w.lower() for w in re.findall(r"[A-Za-z']+", stripped)}
                )
                if focus:
                    place = scope or _longest_proper_noun(message)
                    if place and place.lower() not in focus.lower():
                        return f"{place} {focus}"
                    return focus
            if not has_stopword and not is_attribute:
                place = scope or _longest_proper_noun(message)
                if place and place.lower() not in stripped.lower():
                    # ⚠️ 不能用 `_glue`：它只在「汉字 ↔ 拉丁」之间补空格，两个英文词会被粘成
                    # `Chengdulocal dishes`（拉丁 ↔ 拉丁 不在它的补空格条件里）。
                    return f"{place} {stripped}"
                return stripped
            if has_stopword:
                # 碎片里**夹着虚词**（「get **sick**」的 get、「does the」的 does）→ 先剥掉
                # 虚词再看剩下的是什么：真 bug（2026-10-06）「What should I do if I get
                # sick in China?」的主体是 `get sick`，含虚词就被整条判成残片，最后退回
                # `_longest_proper_noun` → 标题只剩「China」，看不出这一问聊的是生病。
                # 剥完还有实词就用它；剥空了（「don't eat」这类）退回原话里最后一个
                # 非停用实词（pork）—— 那条兜底在下面，这里不重复处理。
                core_words = [
                    w
                    for w in re.findall(r"[A-Za-z][A-Za-z'-]*", stripped)
                    if w.lower() not in _EN_TERM_STOP and len(w) > 2
                ]
                focus = " ".join(core_words)
                if focus:
                    place = scope or _longest_proper_noun(message)
                    if place and place.lower() not in focus.lower():
                        return f"{place} {focus}"
                    return focus
        # 连会话地点都没有、且掉下来的又不是专名 → 退回**原话里最长那个专名**
        # （见 `_longest_proper_noun`）。有 scope 时不走这条，`scope` 本来就是地点。
        if not scope:
            proper = _longest_proper_noun(message)
            if proper:
                return proper
        # **疑问 / 否定残片不能当标题**。真 bug（2026-10-06）：`I don't eat pork` 抽出的
        # 主体是 "don't eat"，一路落到最后就成了标题「don't eat」—— 用户看不出这一问
        # 聊的是猪肉。残片时退回原话里**最后一个非停用实词**（pork），那才是这一问的对象。
        if stripped and _EN_SKELETON_HEAD.match(stripped):
            focus = _en_focus_word(
                message, {w.lower() for w in re.findall(r"[A-Za-z']+", stripped)}
            )
            if focus:
                return _glue(scope, focus) if scope else focus
        return scope or stripped or subject
    # 中文主体先**剥前导铺垫**（自我介绍 / 身份 / 疑问副词 / 方位铺垫）—— 真 bug（2026-10-06
    # 日常功能探针实测）：「我第一次来中国，吃饭有什么礼仪要注意吗」的标签成了
    # 「我第一次来中国，吃饭礼仪」，「那去餐厅要给小费吗」成了「那去餐厅要给小费」。
    # 剥空了（主体整个是「怎么用」这种框架）就改用命中片段（「支付宝」）当主体。
    trimmed = trim_topic_preamble(subject)
    if trimmed != subject:
        subject = trimmed or trim_topic_preamble(matched) or matched.strip() or subject
    if scope and scope not in subject:
        subject = _glue(scope, subject) if subject else scope
    if not message:
        return subject
    # 主体不在这一句里 = 它是**上一轮承接来的**（「那里有什么好吃的」的主体是「上海海事大学」）。
    # 这一句自己只剩属性词（好吃 / 好玩 / 附近 / 学校），机械拼上是「上海海事大学好吃」
    # 这种生硬标题 —— 不如直接用主体当标签，等下一句带了新主体再细化。
    # 判据用「主体是否原样出现在这一句」而不是 `carry_over` 标志：这样「上海外卡支付怎么用」
    # 这类**自带主体**的句子照旧能细化成「上海外卡支付」。
    if subject and subject not in message:
        return subject
    # 焦点是**主体之后**那一截。原来是把主体替换成空格后取整句最长汉字段 ——
    # 于是「哪里能吃到本地人常去的**馆子**」里，主体的**前面**那半截（「哪里能吃」）
    # 也是等长汉字段，会反过来赢过真焦点，标题成了「本地人常能吃到」
    # （真 bug 2026-10-06 菜品探针实测，收「馆子」之后才暴露）。
    # 只在主体前面确实是**疑问引导**时才切掉前半截：其余句子（焦点本就取自整句剩余）
    # 行为保持原样，避免动到既有标题。
    idx = message.find(subject) if subject else -1
    prefix = message[:idx] if idx > 0 else ""
    if prefix and _FOCUS_PREFIX_LEAD.search(prefix):
        rest = message[idx + len(subject):]
    else:
        rest = message.replace(subject, " ", 1) if subject else message
    # 前导铺垫也可能留在这半句里（它本来就排在主体之前）：「…吃饭…礼仪…」去掉主体「吃饭」后
    # 剩下「我第一次来中国，…礼仪…」，不剥的话焦点会取到「第一次来中国」。
    rest = trim_topic_preamble(rest)
    # 剩下的「要/给X」也是情态 + 介词短语、不是焦点（「那去餐厅要给小费吗」去掉主体后剩
    # 「要给小费」）。只对**焦点那半句**剥，不动主体 —— 「给中国朋友送礼」的「给」要留着。
    # ⚠️ `(?!不)` 必须有：不分青红皂白地吃开头那个「要」，会把 V不V 骨架「要不要门票」切成
    # 「不要门票」，后续 `_VV_FRAME` 只摘得掉「要不」，标题反而多出「不要」。
    rest = re.sub(r"^(?:要|给)+(?!不)(?=[\u4e00-\u9fff]{2,})", "", rest)
    rest = _DISPLAY_ADVERB.sub(" ", rest)
    # 先摘「V不V」疑问骨架（要不要 / 需不需要 / 收不收费 / 有没有 / 是不是）。
    # 真 bug（2026-10-04 实测）：「上海博物馆要不要门票」去掉主体后剩「要不要门票」，
    # 骨架若不被摘掉，最长汉字段会取到**残片「要不」**，标题成了「关于「上海博物馆要不」」。
    # 骨架里有「不」，抽焦点前必须先摘，否则残片一定比真焦点长。
    rest = _VV_FRAME.sub(" ", rest)
    if matched and _LIGHT_CUE.match(matched.strip()):
        rest = rest.replace(matched, " ")
    # **比较句**（「打车方便还是坐地铁方便」）只在问「哪一种更好」，焦点若把整句都装进来，
    # 标题就成了半截 predicate（真 bug 2026-10-06 交通探针 T1-A3：
    # 「从首都机场到市区打车方便还」）。取舍是在triggers 上引爆 — 只留第一个分句。
    rest = re.split(r"还是|或者|還是|或是|要么", rest)[0]
    rest = _TERM_STOP_RE.sub(" ", rest)
    runs = [
        r for r in _HAN_RUN.findall(rest) if len(r) >= 2 and r not in _FOCUS_NOISE
    ]
    if not runs:
        return subject
    # 日文句子的汉字段会被假名**断在词中间**（「週末混｜みます」），取「最长的汉字段」必然
    # 截出半个词 —— 真 bug（2026-10-06 交通探针 T5-F3）：标题成了「観光地週末混」。
    # 日文语序里主题紧跟助词，triggers 收的「混み / ラッシュ」才是这一问的对象，所以
    # 句里有假名时优先拿**命中片段**当焦点。
    focus = ""
    if _KANA.search(message or "") and len((matched or "").strip()) >= 2:
        if not _LIGHT_CUE.match(matched.strip()):
            focus = matched.strip()
    if not focus:
        focus = max(runs, key=len)[:12]
    # 焦点收尾是「方不方便 / 划算 / 值得 / 推荐」这类**评价词**时不算内容（同上 T1-A3：
    # 「打车方便」的「方便」不是这一问的对象，只是问句的一部分），摘掉后为空就退回主体。
    focus = _FOCUS_JUDGMENT.sub("", focus)
    # 前面的语气 / 程度词同理（「川菜是不是**都很**辣」→「都很辣」），只在焦点本身够看时摘。
    if len(focus) >= 3:
        stripped = _FOCUS_LEAD.sub("", focus)
        # ⚠️ 剥完必须仍是**原话里连续的一段**，否则等于凭空造词（「特色菜」→「色菜」）。
        if stripped and stripped in (message or ""):
            focus = stripped
    # ⚠️ 摘完若只剩一个汉字（「素食者 + 方便」摘成「饭」），这个焦点已经**不足以当标签** ——
    # 真 bug（2026-10-06 菜品探针 C3-C1）：「我是素食者，在中国吃饭方便吗？」的标题成了
    # 「素食者饭」。`runs` 那时还挑得出「饭方便」，是收尾评价词摘完后才塌成一个字的，
    # 所以长度检查必须放在所有摘除**之后**。
    if len(focus) < 2:
        return subject
    # 焦点是**动补 / 被动残片**时不算内容：那是「怎么吃才不会被烫到」里的「被烫到」、
    # 「去哪家吃比较正宗」里的「家吃比较正宗」（真 bug 2026-10-06 菜品探针实测：
    # 标题成了「小笼包被烫到」「北京烤鸭家吃比较正宗」）。两者问的都是**那一道菜本身**，
    # 后半句只是问法，所以整段丢弃、退回主体。
    # 判据只看**开头**：被动标记 / 量词残片 / 光杆动词，后面跟什么都一样不成立。
    if _FOCUS_VERBAL_HEAD.match(focus):
        return subject
    if not subject or subject in focus:
        return focus or subject
    # 主体与焦点在**原话里紧邻**时中间的定语标记要留着：「本地人常去的**馆子**」被切成
    # 主体「本地人常」+ 焦点「馆子」之后，直接拼是「本地人常馆子」—— 少了「去的」，
    # 看着像机器切词。只在两者**确实紧邻**（中间至多一个「去的」）时才补，
    # 「上海｜有什么值得吃的｜特色菜」这种隔了半句的自然不补。
    link = ""
    if subject and focus:
        m = re.search(
            re.escape(subject) + r"(去的|的|之)" + re.escape(focus), message or ""
        )
        if m:
            link = m.group(1)
    return _glue(subject, link + focus)


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
    text = subject or ""
    for name in sorted(table, key=len, reverse=True):
        meta = dict(table.get(name) or {})
        if meta.get("origin_only"):
            continue
        # 命中方式两种：主体**含有**该地名（「上海博物馆」含「上海」），
        # 或主体含/等于该地名的**别名**（「西湖」= 杭州、「外滩」= 上海）。
        # 别名这一条必须有：真 bug（2026-10-06 实测）—— 「西湖」不是独立条目、只是杭州的
        # 别名，不加它就会判成"主体没带地点"，于是借用会话地点、标题成了「北京西湖」。
        aliases = [a for a in (meta.get("aliases") or []) if a]
        if not (contains(text, name) or any(contains(text, a) for a in aliases)):
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
    scope_destination: str = "",
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
    # **主体也要过一遍跨语言别名**。真 bug（2026-10-06 菜品探针实测）：`素食` 命中了
    # 条目正文，却仍返回空 —— 因为「库里对这个主体一无所知就返回空」那道闸门看的是
    # `related_keys`，而外文主体（`vegetarian` / `채식`）在中文条目里永远搜不到字面，
    # 于是**每一句外文问句都被判成「库里没有」**。判据没错，缺的是主体的语言对齐。
    alias_keys = _alias_terms(settings, subject)
    if alias_keys:
        subject_keys |= alias_keys
        related_keys |= alias_keys
    terms = _query_terms(message or intent.question_zh, subject_keys) | {
        str(x) for x in (extra_terms or ()) if str(x).strip()
    }
    # 外文 → 中文实词（见 `_alias_terms`）：种子是中文的，不加这步外国人用母语问
    # 就永远「本库没有相关材料」。别名词只在检索侧生效，不进标题、也不改答案语言。
    terms |= _alias_terms(settings, message or intent.question_zh)
    # **主体把问句的实词全吃掉时，用主体自己的成分回填**。真 bug（2026-10-06）：
    # 「在中国生病了怎么办」的主体是「中国生病」，而 `_query_terms` 的第一步就是
    # 把主体从问句里摘掉再抽实词 —— 摘完只剩「了怎么办」，terms 为空，于是**整库没有
    # 一条够格进池**，「相关条目」一栏空着，可库里明明有急救与看病两条。
    # 回填时**先剥掉地名成分**（「中国生病」→「生病」）：地名不该当判别词，它在
    # 全国条目里到处都是，留着会把无关条目拉进池。
    if not terms and subject:
        residue = subject
        for name in sorted(settings.routes.get("destinations") or {}, key=len, reverse=True):
            if len(name) >= 2:
                residue = residue.replace(name, " ")
        for key in _subject_keys(residue.strip()):
            if len(key) >= 2 and not _is_place(settings, key):
                terms.add(key)
    # **地名不当判别实词**。真 bug（2026-10-06）：「What should I do if I get sick in
    # China?」抽出的实词里有 `China`，而中文条目里的 China 常常是**专名的一部分**
    # （银联「Nihao China」App）—— 于是问生病，相关条目里混进一条讲支付的条目。
    # 地名另有通道（`scope` / `dest_hit` / `borrowed_hit`），不该再靠词频得分。
    if terms:
        places = _latin_place_names(settings)
        terms = {t for t in terms if t.lower() not in places}
    own_scope = _place_scope(settings, subject)
    scope = own_scope
    borrowed_place = ""
    if scope is None and (scope_destination or "").strip():
        # 主体自己没带地点 → 借用**会话目的地**当作用域。真 bug（2026-10-06 变卦探针实测）：
        # 会话在聊「上海 5 天」，用户问「景点推荐呢」，若不借地点则 scope=None、全国条目
        # 都会进池，且模型会答「没指明城市」。优先仍看主体自带的地点（上面那行），
        # 所以「会话在上海、问西湖有多大」不会被套错作用域（西湖能解出杭州）。
        scope = _place_scope(settings, scope_destination)
        if scope is not None:
            borrowed_place = str(scope_destination).strip()
    if borrowed_place and borrowed_place not in related_keys:
        # 会话在聊上海、问「景点推荐呢」—— 用户问的其实是"上海的推荐"，所以「讲上海的条目」
        # 也算相关。不加这条，`related_exists` 为假 → hits 直接空（真 bug 2026-10-06：
        # 明明刚说了上海，答复却说「本库没有相关材料」）。
        related_keys |= _related_keys(borrowed_place)
    # 主体里**一个汉字都没有**（外文问句切出来的 "don't eat" / "China"）：它在中文条目里
    # 永远搜不到字面，「库里有没有讲它」这道闸门得换个判据，见上面 `related` 的兜底。
    foreign_subject = bool(subject) and not _HAN_RUN.search(subject)
    # 拉丁**地名**主体（China / Beijing…）不能靠「正文里出现了这个串」证明条目在讲它：
    # 真 bug（2026-10-06）—— 「…get sick in China?」的相关条目里混进一条支付条目，
    # 只因那条讲银联「Nihao **China**」App。地名到处都是，还会出现在别的专名里。
    latin_place_subject = foreign_subject and subject.lower() in _latin_place_names(settings)
    subject_place = _is_place(settings, subject)
    strong_subject = bool(subject) and not subject_place and not _is_generic_subject(subject)
    subject_weight = W_SUBJECT_TOPIC if strong_subject else W_SUBJECT_WEAK

    # 第一遍：定候选池，并统计「这个词在本问的池子里出现在多少条目里」（IDF 的分母）。
    # IDF 必须在**本问的池子**上算 —— 那才是这一问的判别力；在整库上算会把
    # 「来华问答」这个小池子里其实很常见的词算成稀有词。
    related_exists = False
    pool: list[tuple[Chunk, str, int, int, dict[str, int], int]] = []
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
        # 「沾主体」的判定在下面（要先算出实词命中数，外文主体要靠它兜底）。
        dest_hit = 1 if (subject and chunk.destination == subject) else 0
        subj_hit = 0 if latin_place_subject else (1 if (subject and subject in text) else 0)
        # 借来的**会话地点**命中：会话在聊上海、问「景点推荐呢」—— 这种条目没有本句实词
        # 可依（问句只剩「景点推荐」四个字），全靠借来的地点才有资格进池。
        # 判据要求条目**地点就是那个城市**，不收"正文顺带提一句"的通用条目 ——
        # 否则在华地铁 / SIM 卡这类全国通用条目也会挤进来（实测确实会）。
        borrowed_hit = 0
        if borrowed_place:
            _, entry_city = _dest_parts(settings, chunk.destination)
            borrowed_hit = 1 if entry_city == borrowed_place else 0
        # 实词按**出现次数**计（封顶 `_TERM_CAP`）：一个条目反复讲「支付」比只是顺带提到
        # 一次更贴题 —— 「上海外卡支付怎么用」里地铁条目只是提了一句「支付宝」，不该抢先。
        counts = {t: min(c, _TERM_CAP) for t in terms if (c := _term_count(text, t))}
        src_hits = [t for t in terms if _term_count(source, t)]
        # 「沾主体」= 正文提到主体（含它的成分，如「杭州西湖」认「西湖」）或条目就属于它。
        # 判据刻意窄：只是同样谈「门票 / 预约」不算 —— 那属于「话题像」，不是「关于它」。
        # 用 `related_keys` 而不是 `subject_keys`：「大学」这种通用成分不构成"在讲这所学校"
        # （见 `_related_keys` / `_GENERIC_COMPONENT`）。
        # **地名不当「在讲它」的证据**。真 bug（2026-10-06）：天气条目里举例写了
        # 「多数城市（北京、上海、…、杭州）」，于是问「杭州西湖要门票吗」时这条把
        # `related_exists` 翻成真，闸门放行后一条**通用门票省钱规则**被列进
        # 「知识库里的相关条目」—— 那正是这条判据要挡的「拿别的话题凑数」。
        # 地点另有通道（`scope` / `dest_hit` / `borrowed_hit` / `destination`），
        # 不该拿正文里出现一次城市名当证据：举例、罗列、交通枢纽都会提到城市。
        text_keys = {k for k in related_keys if not _is_place(settings, k)}
        if not text_keys and related_keys:
            # 主体本身就是个地名（「上海」）→ 退回最长成分，保住原有行为。
            text_keys = {max(related_keys, key=len)}
        related = bool(related_keys) and (
            any(key in text for key in text_keys) or chunk.destination in related_keys
        )
        if not related and not related_keys and counts:
            # **主体本身提供不了任何相关性判据**（「几月来」「穿什么」这类疑问骨架，
            # `_related_keys` 榨不出成分），此时只能靠问句实词 —— 条目真命中了实词
            # 就是相关，不该判成「库里没有」。真 bug（2026-10-06）：
            # 「几月来中国最舒服」命中了天气条目，界面却写「本库没有相关材料」。
            # 只在 `related_keys` 为空时兜底：「杭州西湖要门票吗」这类仍有主体成分，
            # 那道闸门（不让异城条目凑数）照旧生效。
            related = True
        if not related and foreign_subject and counts:
            # **外文主体**在中文库里永远搜不到字面，上面那条判据对它必然为假 ——
            # 真 bug（2026-10-06）：`I don't eat pork` 的主体是英文残片 "don't eat"，
            # 库里明明有讲清真与忌口的条目，却整句被判成「本库没有相关材料」。
            # 外文主体的「相关」只能由**实词命中**证明：问句里的 pork 经别名换成
            # 「猪肉」后真的出现在正文里，就是相关。中文主体不进这条（`foreign_subject`
            # 为假），所以「杭州西湖要门票吗却列出清迈」那类误召回不会被放进来。
            related = True
        related_exists = related_exists or related
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
            or borrowed_hit
        ):
            continue
        for t in counts:
            doc_freq[t] = doc_freq.get(t, 0) + 1
        pool.append((chunk, text, dest_hit, subj_hit, counts, borrowed_hit))

    if not related_exists:
        # 库里对这个主体一无所知 → 返回空。那一栏叫「知识库里的相关条目」，
        # 拿别的话题（甚至别的城市）凑数比空着更坏：实测「杭州西湖要门票吗」会列出
        # 清迈 / 厦门的免费点位 —— 用户在问西湖，给他是误导。
        # 空着时文案（`kn.no_hits`）会明说「本库没有关于它的材料」，那是诚实的信息。
        return []

    # BM25 的 idf 分量（永远为正）。一个词在池子里到处都是 → 接近 0 → 它说了不算。
    # 「免费 / 门票 / 预约」就是这类词：靠它们凑出来的次序，正是用户看到的「固定套路」。
    n_docs = max(len(pool), 1)
    # ⚠️ IDF 要有**下限**。真 bug（2026-10-06 生活场景补种子时实测）：池子很小时
    # （「上海的地铁怎么坐」→ 只有 2-3 条进池）**每条都命中同一个词**，BM25 的 idf
    # 于是趋近 0 —— 那个词明明是这一问唯一的判别信号，却被算成"毫无判别力"，
    # 分数掉到 `MIN_SCORE` 之下，用户看到「本库没有相关材料」。
    # 这次的直接触发是新种子里有一条顺带提到「地铁」，把 df 从 2 推到 3：
    # idf 0.18 → 0.13，分数 1.09 → 0.78，正好压线下。
    # **这种"加一条种子就让另一条问答突然失效"的临界抖动不该依赖条目措辞**，
    # 所以给 idf 一个地板：池子小的时候别把真信号判成噪声。
    idf = {
        t: max(math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5)), _IDF_FLOOR)
        for t, df in doc_freq.items()
    }
    # **同一个外文词译成的多个中文写法，判别力必须一致**。真 bug（2026-10-06）：
    # 「Do people speak English in China」把 `english` 译成「英文 / 英语」两个 term，
    # 而种子里顺带提「可出具英文诊断证明」的就医条目恰好写了「英语」，
    # 整条讲语言沟通的条目通篇只写「英文」—— 于是**稀有的那个写法拿到 18 倍权重**，
    # 问语言沟通却列出就医与防骗条目。哪个写法更稀有，纯粹是种子措辞的偶然，
    # 答案不该被措辞决定（与「地铁」那条 IDF 抖动同源）。
    # 做法：把同一别名 key 的中文词并成一组，组内 idf 统一取**组内最小值**
    # （= 最保守的那个），这样同一概念不会因为写法不同而互相压过。
    for group in _alias_idf_groups(settings):
        present = [t for t in group if t in idf]
        if len(present) > 1:
            floor_idf = min(idf[t] for t in present)
            for t in present:
                idf[t] = floor_idf

    ranked: list[tuple[float, int, int, str, Chunk]] = []
    for chunk, text, dest_hit, subj_hit, counts, borrowed_hit in pool:
        # 借来的会话地点与「条目就是主体」同权：它是本句唯一的地点信号（见 `borrowed_hit`）。
        score = subject_weight * subj_hit + W_DEST * (dest_hit + borrowed_hit)
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
        # 池子里有条目**真的提到了问句的实词**，却全被 `MIN_SCORE` 卡掉 —— 这时返回空，
        # 界面上就是「本库没有相关材料」，而库里其实写了（真 bug 2026-10-06：
        # 「在中国生病了怎么办」「I don't eat pork」两条都命中正文，hits 却是 0）。
        # 成因是**池子太小**时 IDF 失去判别力：两三条都命中同一个词 → idf≈0 →
        # 那个词明明是这一问唯一的信号，却被算成没有信号。那是打分模型的边界，
        # 不是"不相关"，把边界当成"没有"是更坏的错。
        # 降级只收**真的命中实词**的条目，按命中次数排序：宁可给两条贴题的，
        # 也不要靠主体字面拉进来的凑数条目。
        fallback = sorted(
            (row for row in pool if row[4]),
            key=lambda row: (-sum(row[4].values()), 0 if row[2] else 1, row[0].chunk_id),
        )
        return [row[0] for row in fallback[:limit]]
    # 固定排序：同一句话两次跑必得同一顺序（这一层不允许有随机性）。
    ranked.sort(key=lambda row: (-row[0], row[1], row[2], row[3], row[4]))
    # 相对截断：`limit` 是**上限**，不是目标。连最高分三分之一都不到的条目，是那种
    # 到处沾一点的通用条目 —— 列出来只会让每张卡片长得一样（用户报的「固定套路」）。
    floor = max(MIN_SCORE, ranked[0][0] * REL_KEEP)
    return [chunk for *_, chunk in [row for row in ranked if row[0] >= floor][:limit]]


# ------------------------------------------------------------------ 模型作答
#
# ------------------------------------------------- 话题类型：文化 / 饮食（决定类比力度）
#
# 为什么这个判定要放在代码里，而不是交给模型自判：类比是**可选补充**，模型在把握不足时
# 会倾向于省掉它（少写一句最省事）—— 而「中国的小费怎么给」「川菜是不是都很辣」恰恰是
# 最该类比的问句。给一个显式信号（写进提示词的第 5 条规则 `{{analogy_rule}}`），它才不会闭嘴。
#
# 判据是**词面**的，与这一层其余部分一致：不调模型、同一句话两次跑必得同一结论。
# 刻意判宽：命中的代价只是「多问一句要不要类比」，判漏的代价才是「该类比的话题一个字没提」。
_CULTURE_FOOD_ZH = (
    # 饮食
    "吃", "菜", "美食", "餐厅", "饭店", "馆子", "小吃", "点菜", "菜单", "筷子", "餐具",
    "口味", "忌口", "素食", "清真", "小费", "早餐", "午餐", "晚餐", "夜宵",
    "喝茶", "茶叶", "白酒", "敬酒", "酒桌", "火锅", "烧烤",
    # 文化 / 礼仪 / 习俗
    "文化", "礼仪", "礼节", "习俗", "风俗", "习惯", "传统",
    "节日", "春节", "中秋", "端午", "红包", "送礼", "礼物", "忌讳", "禁忌",
    "寺庙", "宗教", "信仰", "排队", "让座",
)
_CULTURE_FOOD_EN = (
    "food", "foods", "dish", "dishes", "cuisine", "restaurant", "restaurants",
    "eat", "eating", "meal", "meals", "menu", "chopsticks", "spicy", "flavor", "flavour",
    "vegetarian", "vegan", "halal", "pork", "beef", "seafood",
    "tip", "tips", "tipping", "gratuity",
    "culture", "cultural", "etiquette", "custom", "customs", "tradition", "traditional",
    "festival", "festivals", "religion", "religious", "temple", "temples", "gift", "gifts",
    "taboo", "queue", "queuing", "queueing", "manners", "habit", "habits",
)
# 日文 / 韩文：这一层是多语言的（答案跟输出语言），日韩问句同样要能触发类比。
_CULTURE_FOOD_CJK = (
    "食べ", "料理", "文化", "習慣", "マナー", "チップ", "箸", "礼儀", "祭り",
    "음식", "문화", "예절", "팁", "젓가락", "명절",
)


def culture_food_topic(message: str, subject: str = "") -> bool:
    """这一问是不是「文化 / 饮食 / 生活习俗」类 —— 是的话类比要更主动（见 answer.md 规则 5）。

    中文按子串（「川菜」「点菜」都含「菜」），拉丁词按**整词**（避免 `eat` 命中 `great`，
    与 `_term_count` 同一套判据），日韩按子串。
    """
    text = f"{message or ''} {subject or ''}"
    if not text.strip():
        return False
    if any(w in text for w in _CULTURE_FOOD_ZH) or any(w in text for w in _CULTURE_FOOD_CJK):
        return True
    low = text.lower()
    return any(_latin_term_re(w).search(low) for w in _CULTURE_FOOD_EN)


def _render_nationality_block(nationality: str) -> str:
    """提示词里的「游客来源国」一行。

    空串的含义是「**不知道**」，不是「没有这个国家」—— 所以文案要明确禁止类比，
    否则模型会自己补一个来源国（真 bug 教训：猜错来源国，整篇类比全跑偏）。
    值可能来自表单的自由输入，先压成一个短词再写进提示词。
    """
    name = re.sub(r"\s+", " ", str(nationality or "")).strip()[:24]
    if not name:
        return "（未知 —— 这一问**不要**做中外类比，也不要写「如果你是 X 国人…」这类假想句式。）"
    return f"{name} —— 用户是来自{name}的游客，可按它做中外类比（见硬性规则 5）。"


def _render_analogy_rule(nationality: str, message: str, subject: str) -> str:
    """提示词里第 5 条规则（中外类比）的**整条正文**。

    ⚠️ 来源国未知时**整条换成禁令**，而不是「保留一段类比教程、末尾加一句禁止」——
    提示词里只要存在那段教程，模型就会去用。实测（2026-10-07 真模型）：来源国未知时，
    它照样写「与你在**欧美国家**通常按 15%–20% 另付小费的习惯不同」，把猜出来的国家
    当成了事实。规则的存在本身就是许可，所以未知时不能让它存在。
    """
    name = re.sub(r"\s+", " ", str(nationality or "")).strip()[:24]
    if not name:
        return (
            "5. **本问不做中外类比**：来源国未知（用户没说过来自哪个国家）。"
            "所以不要写「与你在 X 相比」「在你们国家」这类对比句 —— 那等于按猜出来的国家"
            "作答，比不说更糟。只客观说明中国这边的情况即可。"
        )
    if culture_food_topic(message, subject):
        strength = "这一问属于**文化 / 饮食 / 生活习俗类**，所以**必须**给出 1–2 处中外对比。"
    else:
        strength = "这一问属一般话题：有自然可比的点就给 1 处，没有不必硬凑。"
    return (
        f"5. **来源国已知（{name}），主动做中外类比**。外国旅客看不懂中国的「做法」，"
        f"但看得懂「和自己国家比差在哪」。{strength}\n"
        "   - 写法：「与你在 X 通常的 A 相比，中国多是 B」—— 点出**相同 / 不同**这一层关系即可。\n"
        "     常有的可比点：小费给不给、几点吃晚饭、排队要不要预约、能不能刷本地卡、\n"
        "     自来水直不直饮、室内能不能抽烟、进寺观要不要脱鞋、送礼收不收。\n"
        "   - **类比的素材可以来自你的常识，不必来自知识库**。类比讲的是「同 / 不同」这层关系，\n"
        "     不是精确事实 —— 所以知识库里没有对应条目时**照样做类比**。这与规则 6 不冲突：\n"
        "     规则 6 管的是「拿同主题材料冒充答案」，不是禁止你自己做类比。\n"
        "   - 但**绝不编造对方国家的具体数字、法规、价格或政策**。涉及对方国家的细节只写到\n"
        "     「框架」这一层；把握不足就写「和你国内的习惯可能不同，按这里的来」。\n"
        "   - **由类比带出的数字仍然是数字**，仍受规则 3 约束：把握不住就不要写。"
    )


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
    nationality: str = "",
) -> str:
    """装配提示词。**先填 schema 与语言段，再填用户内容** ——

    反过来做的话，用户消息里恰好出现 `{{subject}}` 这种片段就会被二次替换，
    把模板结构打乱。这个顺序让用户内容永远是最后落进去的。

    `context` 是**指代解析结果**（「这一句里的『那里』指上一轮说的『上海临港』」）——
    它和 `message` 一样是用户侧内容，所以也排在最后替换。

    `nationality` 是**游客来源国**（会话槽位里的 `nationality`，与 `destination_country`
    正交）。它决定提示词有没有资格做中外类比 —— 空串 = 不知道 = 明确禁止类比。
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
        # 这两个的值里可能带用户输入（来源国是表单自由文本），所以与用户内容一起放在最后。
        .replace("{{nationality_block}}", _render_nationality_block(nationality))
        .replace("{{analogy_rule}}", _render_analogy_rule(nationality, message, intent.subject or ""))
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
    nationality: str = "",
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
                nationality=nationality,
            ),
            schema=AnswerDraft.model_json_schema(),
            context={
                "task": "answer",
                "subject": intent.subject,
                "question": message,
                "referent": context,
                "language": language,
                "nationality": nationality,
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
    scope: str = "",
) -> AnswerResponse:
    """组装答复。整段用户可见文案跟输出语言；`hits[].source/url` 与 `name/url` 是引用，保持原样。

    `scope` 是**会话借来的地点**（主体自己不带地点时），既进标题也让复核入口可搜 ——
    见 `display_topic`。
    """
    draft = draft or AnswerDraft()
    # 标题 / 复核入口用「主体 + 焦点」而不是光秃秃的主体 —— 否则同一个城市的每一次提问，
    # 卡片标题与复核链接都长得一样（用户报的「固定套路」，见 display_topic）。
    topic = display_topic(intent.subject, message, intent.matched, scope=scope) or intent.subject or scope
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
        # 「接着可以问」以**模型同一次作答里写的那几条**为主（不额外调模型）；
        # 为空时 `app/graph.py` 退回定稿模板。见 `prompts/answer.md` 规则 9。
        next_questions=list(draft.followups or []),
    )
