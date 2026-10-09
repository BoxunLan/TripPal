"""多语言：语言判定 + 用户可见文案。

三条边界（改动前先读这三条，否则很容易做错）：

1. **检索语言恒为中文。** 知识库（`seed/*.jsonl`）是中文语料，embedding 也是按中文
   效果调过的。所以 `rewritten_query` 必须**始终是中文** —— 多语言只影响输出，不影响检索。
   把检索查询跟着用户语言走，英文请求的召回会直接崩掉。
2. **输出语言跟用户。** en/ja/ko 的请求，标题、正文、清单用该语言写；但
   `citation_key`、来源原文、日期、金额保持原样（引用要能对回原文，翻译了就断链）。
3. **开发期文案恒为中文。** 校验 findings、日志、报错、脚本输出、测试断言都不随语言变化。
   调试时不该还要在脑子里做一次翻译。这条是使用者的明确要求。

语言判定是确定性的（按字符集），不调模型 —— 它要参与提示词装配，不能有随机性。
"""

from __future__ import annotations

import re
from typing import Any

ZH = "zh"
EN = "en"
JA = "ja"
KO = "ko"

DEFAULT = ZH
SUPPORTED = (ZH, EN, JA, KO)

# 给模型读的自然语言名
LANGUAGE_NAMES = {ZH: "简体中文", EN: "English", JA: "日本語", KO: "한국어"}

_KANA = re.compile(r"[\u3040-\u30ff]")
# 谚文音节（AC00–D7AF）+ 字母（1100–11FF）+ **兼容字母（3131–318F）**。
# 最后一段是 2026-10-09 补的：`ㅋㅋ` / `ㅎㅎ` / `ㅠㅠ` 用的是兼容字母，
# 原先落在范围外 → 被判成 en，韩语用户笑一声收到英文回复。
_HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3131-\u318f]")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def detect_language(text: str) -> str:
    """按字符集判定语言。

    假名 → 日文（日文里也有汉字，必须先看假名）；谚文 → 韩文；
    有汉字 → 中文；其余（含纯拉丁/数字）→ en。
    """
    t = text or ""
    if _KANA.search(t):
        return JA
    if _HANGUL.search(t):
        return KO
    if _CJK.search(t):
        return ZH
    return EN if t.strip() else DEFAULT


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, LANGUAGE_NAMES[DEFAULT])


def contains(haystack: str, needle: str) -> bool:
    """子串匹配：含 ASCII 的名字/关键词忽略大小写。

    英文用户常写小写（"xiamen"、"family trip"），而词典与关键词表里是 "Xiamen"、"family"。
    中文没有大小写，走原样的精确子串匹配。
    """
    if not needle:
        return False
    if needle.isascii():
        return needle.lower() in (haystack or "").lower()
    return needle in (haystack or "")


def output_language_directive(code: str) -> str:
    """注入生成提示词的语言段。"""
    if code == ZH:
        return ""  # 中文是默认，不加也自然是中文，少一段噪音
    name = language_name(code)
    return (
        f"# 输出语言：{name}（最后一条要求，优先级高于上面所有中文细则）\n\n"
        f"**除了引用原文，整份响应都用 {name} 写**，包括这些字段：\n"
        "`title`、`destination`、`party`、`date_range`、`days[].theme`、`days[].area`、\n"
        f"`days[].activities[].name` / `.detail`、`budget.lines[].name`、`notes`、"
        f"`disclaimers`、`suggestions`。\n\n"
        "只有以下内容保持原样，**不要翻译**：`citation_key`、引用里的来源原文（`source`）、\n"
        "`source_url`、日期与金额数字。引用必须能对回原文，翻译会断链。\n\n"
        f"上面的场景细则与知识库原文都是中文，那是**输入**；你就是用 {name} 转述它们，\n"
        f"不要因为看不到 {name} 的资料就写成「待核实」，也不要整段照抄中文。"
    )


# 常识作答专用的语言段（`prompts/answer.md` 的最后一条）。
# 与上面的生成版分开写：那里列的是行程字段名（title/days/budget…），
# 这边只有两个字段（`answer` / `unknown_reason`），列错了等于没约束。
#
# 中文这里**也**给一句：生成提示词省掉中文段是因为「不加也自然是中文」，
# 但常识作答只有两个字段，写清没有噪音代价，还能顺带钉住「数字保持原样」。
_ANSWER_LANG: dict[str, str] = {
    ZH: "用简体中文写 `answer` 与 `unknown_reason`。数字与单位保持原样（如 6.38 km²），不要翻译给定的 `subject`。",
    EN: "Write both `answer` and `unknown_reason` in English. "
        "Keep numerals and units as-is, and do not translate the given `subject`.",
    JA: "`answer` と `unknown_reason` は日本語で書いてください。"
        "数値と単位はそのままにし、与えられた `subject` は翻訳しないでください。",
    KO: "`answer`와 `unknown_reason`는 한국어로 작성하세요. "
        "숫자와 단위는 그대로 두고, 주어진 `subject`는 번역하지 마세요.",
}


def answer_language_directive(code: str) -> str:
    """注入常识作答提示词的语言段（放在提示词最后，见 `prompts/answer.md`）。"""
    return _ANSWER_LANG.get(code, _ANSWER_LANG[DEFAULT])


# ---------------------------------------------------------------- 用户可见文案
# 只放**会出现在响应里、且随用户语言变化**的字符串。
# 校验 findings / 日志不在这里 —— 它们恒为中文（见模块 docstring 第 3 条）。
_UI: dict[str, dict[str, str]] = {
    "slot.destination": {
        ZH: "想去的城市或国家是哪里？",
        EN: "Which city or country do you want to visit?",
        JA: "行き先の都市や国はどこですか？",
        KO: "가고 싶은 도시나 국가는 어디인가요?",
    },
    "slot.destination_country": {
        ZH: "要办哪个国家的签证？",
        EN: "Which country's visa do you need?",
        JA: "どこの国のビザが必要ですか？",
        KO: "어느 나라 비자가 필요하신가요?",
    },
    "slot.date_range": {
        ZH: "计划玩几天，或者大致哪几天出发？",
        EN: "How many days, or roughly which dates?",
        JA: "何日間の予定ですか？出発日はいつ頃ですか？",
        KO: "며칠 동안인가요? 혹은 언제쯤 출발하시나요?",
    },
    "slot.budget": {
        ZH: "这次大概的预算是多少（人民币）？",
        EN: "What is your budget (in CNY)?",
        JA: "ご予算はおおよそいくらですか（人民元）？",
        KO: "예산은 대략 얼마인가요(위안)?",
    },
    "slot.party": {
        ZH: "一行几个人，有小孩或长者同行吗？",
        EN: "How many travellers, and any children or seniors?",
        JA: "何名様ですか？お子様やご年配の方はいらっしゃいますか？",
        KO: "몇 분이신가요? 어린이나 어르신이 함께하시나요?",
    },
    # 已经知道同行人构成（有长者/儿童）但不知道人数时用这条：
    # 再问一遍「有小孩或长者同行吗」就是明知故问了。
    "slot.party_size": {
        ZH: "一行几个人？",
        EN: "How many travellers will there be?",
        JA: "何名様でいらっしゃいますか？",
        KO: "몇 분이 함께하시나요?",
    },
    "clarify.known_head": {
        ZH: "已记下{known}。",
        EN: "Got it: {known}. ",
        JA: "承知しました：{known}。",
        KO: "확인했습니다: {known}. ",
    },
    "clarify.ask_head": {
        ZH: "还需要确认：",
        EN: "Still need: ",
        JA: "もう少し確認させてください：",
        KO: "추가로 확인할 사항: ",
    },
    # 只缺**一项**时用这条。连续追问里最难听的是把「还需要确认：」这种清单腔
    # 用在"就差最后一个问题"的场合 —— 那是把对话当表单填。
    "clarify.ask_head_one": {
        ZH: "就差一项了：",
        EN: "Just one thing: ",
        JA: "あと1つだけ：",
        KO: "딱 하나만요: ",
    },
    # 用户要调预算但没给数（「太贵了，能便宜点吗」）时的追问。
    # 与前几种追问分开，是因为这里已经有一版行程了 —— 语气要对得上"在改"而不是"在补"。
    "clarify.budget_adjust": {
        ZH: "好，那这次大概想控制在多少（人民币）？",
        EN: "Sure — roughly what would you like to keep it under (in CNY)?",
        JA: "かしこまりました。ご予算はどのくらいを目安にしますか（人民元）？",
        KO: "알겠습니다. 예산을 어느 정도로 맞출까요(위안)?",
    },
    # **提高**方向的那一半（「开销太少，多花点」「提高开销」）。与上面那条分开写，
    # 因为方向相反、问法也必须相反：用户说"提高"，问"想控制在多少以内"是把话说反了
    # （2026-10-09 用户实测：说了提高开销，系统一路把计划改成穷游）。
    "clarify.budget_raise": {
        ZH: "好，那这次大概想把预算提到多少（人民币）？",
        EN: "Sure — roughly how high would you like the budget to go (in CNY)?",
        JA: "かしこまりました。ご予算はどのくらいまで上げますか（人民元）？",
        KO: "알겠습니다. 예산을 어느 정도까지 올릴까요(위안)?",
    },
    # 「随便 / 都行 / 你看着办」时用常规默认补上骨架缺口，并把默认值**明说**出来。
    # 这句进的是行程响应的 suggestions 首条，用户看得到、也随时能改。
    "clarify.delegate_note": {
        ZH: "先按常规来：{days} 天、约 {budget} 元、{party} 人的配置已就位，想改哪一项直接说。",
        EN: "Started with our usual setup: {days} days, about CNY {budget}, {party} travellers. "
            "Tell me what to change.",
        JA: "まず標準構成で進めます：{days} 日間・約 {budget} 元・{party} 名。変更があればお知らせください。",
        KO: "우선 기본 구성으로 진행했어요: {days}일 · 약 {budget}위안 · {party}명. 바꿀 점은 말씀해 주세요.",
    },
    # 行程内迭代：「第 3 天太紧凑了」这类**修改诉求**被接受后，先说一句"我按你说的改了"，
    # 用户才知道这版与上一版的关系（否则会以为系统又从头排了一遍）。
    "clarify.revision_note": {
        ZH: "已按你的要求调整：{v}。其余部分保持不变，还想改哪里直接说。",
        EN: "Updated as you asked: {v}. Everything else stays the same — tell me what to change next.",
        JA: "ご要望どおり調整しました：{v}。ほかはそのままです。続けて変更があればお知らせください。",
        KO: "요청하신 대로 조정했어요: {v}. 나머지는 그대로입니다. 더 바꿀 점은 말씀해 주세요.",
    },
    # 「把行程再给我看看」：**原样重放**上一版出稿的卡片（不重新生成）。{v} = 那一版的标题。
    "clarify.recall_note": {
        ZH: "这是最近这一版行程（{v}）。要改哪一天直接说。",
        EN: "Here is your latest itinerary ({v}). Tell me which day to change.",
        JA: "こちらが最新の旅程です（{v}）。変更したい日を教えてください。",
        KO: "최근 일정입니다({v}). 바꿀 날짜를 말씀해 주세요.",
    },
    # 用户要重看行程、但会话里根本没出过稿 → 如实说 + 给出下一步（**不要**回四连问）。
    "clarify.no_plan_yet": {
        ZH: "我这边还没有为你生成过行程，所以暂时没得看。把目的地告诉我、再说个大概天数，我这就排一版。",
        EN: "I haven't generated an itinerary for you yet, so there's nothing to show. "
            "Tell me the destination and roughly how many days, and I'll put one together.",
        JA: "まだ旅程を作成していないため、お見せできるものがありません。"
            "行き先と日数の目安を教えていただければ、すぐに作成します。",
        KO: "아직 만들어 드린 일정이 없어서 보여드릴 게 없어요. "
            "목적지와 대략 며칠인지 알려주시면 바로 만들어 드릴게요.",
    },
    "clarify.known_destination": {
        ZH: "目的地 {v}",
        EN: "destination {v}",
        JA: "行き先 {v}",
        KO: "목적지 {v}",
    },
    "clarify.known_days": {
        ZH: "{n} 天",
        EN: "{n} days",
        JA: "{n} 日間",
        KO: "{n}일",
    },
    "clarify.known_join": {
        ZH: "、",
        EN: ", ",
        JA: "、",
        KO: ", ",
    },
    # 实时事实问询（intents.realtime_fact）。它的产出也是**用户可见**的，
    # 所以整段跟输出语言；只有 `channels[].name/url` 是引用，保持原样不翻译。
    "rt.head": {
        ZH: "这是一条实时事实问询，不按行程规划走 —— 排行程才需要天数、预算、人数。",
        EN: "This is a live-fact question, so it is not handled as trip planning: "
            "days, budget and party size are only needed for an itinerary.",
        JA: "これはリアルタイム事実の問い合わせで、旅程プランとしては扱いません"
            "（日数・予算・人数は旅程プランにのみ必要です）。",
        KO: "이것은 실시간 사실 질문이라 일정 계획으로 처리하지 않습니다"
            "(일수·예산·인원은 일정 계획에만 필요합니다).",
    },
    "rt.question": {
        ZH: "要核实的检索式（检索恒用中文）",
        EN: "Query to verify (retrieval is always in Chinese)",
        JA: "確認する検索クエリ（検索は常に中国語）",
        KO: "확인할 검색어(검색은 항상 중국어)",
    },
    "rt.found": {
        ZH: "知识库已有且未过期的内容",
        EN: "Already in the knowledge base and not expired",
        JA: "ナレッジベースにあり有効期限内の内容",
        KO: "지식베이스에 있고 유효기간 내인 내용",
    },
    "rt.channels": {
        ZH: "权威入口（实时核实请从这里查）",
        EN: "Authoritative sources (verify here)",
        JA: "公式の入口（ここで確認してください）",
        KO: "공식 출처(여기서 확인하세요)",
    },
    "rt.unverified": {
        ZH: "以下内容本次没有核实，不要当成结论",
        EN: "Not verified in this run — do not treat as a conclusion",
        JA: "以下は今回未確認です。結論として扱わないでください",
        KO: "아래 항목은 이번에 확인하지 않았습니다. 결론으로 받아들이지 마세요",
    },
    "rt.no_channel": {
        ZH: "该类型暂无已核实的权威入口，请以运营方 / 官网当日公告为准。",
        EN: "No verified authoritative entry for this type yet; rely on the operator's "
            "or the official site's same-day notice.",
        JA: "この種別には確認済みの公式入口がありません。運営者・公式サイトの当日の告知をご確認ください。",
        KO: "이 유형에는 확인된 공식 출처가 없습니다. 운영 주체나 공식 사이트의 당일 공지를 확인하세요.",
    },
    "rt.plan_hint": {
        ZH: "如果你要的是这几天的整体行程，把目的地、天数、预算、人数告诉我，我按行程链路出稿。",
        EN: "If you want a full itinerary for these dates instead, tell me the destination, "
            "number of days, budget and party size, and I will build one.",
        JA: "この数日の旅程全体が必要な場合は、行き先・日数・予算・人数を教えてください。旅程として作成します。",
        KO: "이 기간의 전체 일정이 필요하면 목적지·일수·예산·인원을 알려주세요. 일정을 만들어 드립니다.",
    },
    "rt.disclaimer": {
        ZH: "实时信息（时刻、天气、运营状态）每天都会变，出行前请再查一次当日官方公告；"
            "本回答不编造具体时刻。",
        EN: "Live information (times, weather, service status) changes daily — re-check the "
            "same-day official notice before you travel. This answer does not invent times.",
        JA: "リアルタイム情報（時刻・天気・運行状況）は毎日変わります。出発前に当日の公式告知を"
            "再確認してください。本回答は具体的な時刻を捏造しません。",
        KO: "실시간 정보(시각·날씨·운행 상태)는 매일 바뀝니다. 출발 전 당일 공식 공지를 다시 확인하세요. "
            "본 답변은 구체적 시각을 만들어내지 않습니다.",
    },
    "rt.type.schedule": {ZH: "时点类（仪式 / 演出 / 放票）", EN: "Time-critical events",
                         JA: "時刻もの（式典・公演・発券）", KO: "시각 정보(의식·공연·발권)"},
    "rt.type.opening_hours": {ZH: "开放情况", EN: "Opening status",
                              JA: "開館・営業状況", KO: "개방·영업 상황"},
    "rt.type.weather": {ZH: "天气", EN: "Weather", JA: "天気", KO: "날씨"},
    "rt.type.transport": {ZH: "交通运营状态", EN: "Service status",
                          JA: "運行状況", KO: "운행 상태"},
    # 要核实的属性。**刻意与 routes.yaml 的 attr 分开放**：
    # 配置里那个是**检索词**（恒中文，进向量查询），这里是**给用户看的话**（跟语言）。
    "rt.attr.schedule": {
        ZH: "当日具体时刻",
        EN: "the exact time on that day",
        JA: "当日の具体的な時刻",
        KO: "당일 구체 시각",
    },
    "rt.attr.opening_hours": {
        ZH: "当日是否正常开放",
        EN: "whether it is open as usual that day",
        JA: "当日に通常どおり開いているか",
        KO: "당일 정상 개방 여부",
    },
    "rt.attr.weather": {
        ZH: "当日与未来数日天气",
        EN: "the weather that day and over the next few days",
        JA: "当日と今後数日の天気",
        KO: "당일과 향후 며칠 날씨",
    },
    "rt.attr.transport": {
        ZH: "当日运营状态",
        EN: "the service status that day",
        JA: "当日の運行状況",
        KO: "당일 운행 상태",
    },
    "rt.attr.policy": {
        ZH: "现行免签 / 过境免签政策与生效日期",
        EN: "the current visa-free / transit policy and its effective date",
        JA: "現行のビザ免除・通過免除政策と施行日",
        KO: "현행 비자 면제·경유 면제 정책과 시행일",
    },
    "rt.found_none": {
        ZH: "知识库里没有没过期的记录，所以下面只给权威入口，不给结论。",
        EN: "The knowledge base has no unexpired record, so below are authoritative sources "
            "only — no conclusion.",
        JA: "ナレッジベースに有効期限内の記録がないため、以下は公式入口のみで結論は出しません。",
        KO: "지식베이스에 유효기간 내 기록이 없어 아래에는 공식 출처만 제시하고 결론은 내리지 않습니다.",
    },
    "rt.expired": {
        ZH: "另有 {n} 条已过有效期，按时效闸门丢弃。",
        EN: "{n} more record(s) were expired and dropped by the freshness gate.",
        JA: "ほかに {n} 件が有効期限切れのため破棄されました。",
        KO: "추가로 {n}건이 유효기간이 지나 제외되었습니다.",
    },
    # 常识问答（intents.knowledge_question）。同样是**用户可见**产出 → 整段跟输出语言。
    # 星号强调标记**不要**写进来：这段文案会直接进 HTML，markdown 的 ** 会原样显示成星号。
    "kn.head": {
        ZH: "这是一条常识性问询，不按行程规划走 —— 排行程才需要天数、预算、人数，所以这次没有问你这些。",
        EN: "This is a general-knowledge question, so it is not handled as trip planning: "
            "days, budget and party size are only needed for an itinerary — that is why they "
            "were not asked.",
        JA: "これは一般知識の問い合わせで、旅程プランとしては扱いません。日数・予算・人数は"
            "旅程プランにのみ必要なため、今回は確認していません。",
        KO: "이것은 일반 상식 질문이라 일정 계획으로 처리하지 않습니다. 일수·예산·인원은 "
            "일정 계획에만 필요하므로 이번에는 묻지 않았습니다.",
    },
    "kn.hits_title": {
        ZH: "知识库里与「{subject}」相关的条目（不是这个问题的答案）",
        EN: "Entries related to “{subject}” in the knowledge base (not the answer to this question)",
        JA: "ナレッジベース内で「{subject}」に関連する項目（この質問の答えではありません）",
        KO: "지식베이스에서 ‘{subject}’와 관련된 항목(이 질문의 답은 아닙니다)",
    },
    "kn.no_hits": {
        # 这句会**逐字进用户可见回复**，所以必须写全库的实际覆盖面。
        # 原来的版本只写「京都/清迈/厦门 + 日本签证」—— 产品主题已收口为「外国人来华」，
        # 那句话等于对外宣称「我们不管来华」，是定位与数据打架的第二处遗留。
        ZH: "知识库里没有与「{subject}」相关的条目（本库覆盖出境目的地的行程知识、"
            "出境签证（如泰国），以及外国人来华的入境政策、过境免签与在华实用信息）。",
        EN: "The knowledge base has no entry related to “{subject}” (it covers outbound "
            "destination and visa knowledge, plus inbound China rules — visa-free transit, "
            "entry policy and practical info for foreign visitors).",
        JA: "ナレッジベースに「{subject}」に関連する項目はありません"
            "（海外目的地の旅程・ビザ知識に加え、外国人の中国入国政策・通過ビザ免除・"
            "在华実用情報を対象としています）。",
        KO: "지식베이스에 ‘{subject}’와 관련된 항목이 없습니다"
            "(해외 목적지 여행·비자 지식에 더해, 외국인의 중국 입국 정책·경유 비자 면제·"
            "중국 내 실용 정보를 다룹니다).",
    },
    # 常识作答（`prompts/answer.md` 的 answer 字段）。
    "kn.answer_title": {
        ZH: "关于「{subject}」",
        EN: "About “{subject}”",
        JA: "「{subject}」について",
        KO: "‘{subject}’에 대해",
    },
    # 这句是这一层的**诚实核心**：答案来自通用常识，本库没有核实过。
    "kn.general_knowledge": {
        ZH: "以上由通用常识作答，未经本知识库核实 —— 具体数字请点下方入口自己核一遍。",
        EN: "The above is answered from general knowledge, not verified against this "
            "knowledge base — use the entry below to check any figure yourself.",
        JA: "以上は一般知識による回答で、本ナレッジベースでの確認は経ていません。"
            "数値は下の入口でご自身でお確かめください。",
        KO: "위 내용은 일반 상식으로 답한 것으로, 이 지식베이스에서 검증한 것이 아닙니다. "
            "수치는 아래 링크에서 직접 확인하세요.",
    },
    "kn.unknown": {
        ZH: "这个我没有把握，所以不给具体数字：{reason}",
        EN: "I am not confident enough to give a figure here: {reason}",
        JA: "確信が持てないため、具体的な数値は示しません：{reason}",
        KO: "확신할 수 없어 구체적인 수치는 제시하지 않습니다: {reason}",
    },
    "kn.no_model_answer": {
        ZH: "本轮没有取得作答。",
        EN: "No answer was produced this round.",
        JA: "今回は回答を取得できませんでした。",
        KO: "이번에는 답변을 생성하지 못했습니다.",
    },
    "kn.verify_title": {
        ZH: "复核入口（点开自己核一遍）",
        EN: "Check it yourself",
        JA: "確認用の入口（ご自身でお確かめください）",
        KO: "직접 확인할 수 있는 링크",
    },
    "kn.disclaimer": {
        ZH: "以上为通用常识作答或知识库摘录，未经本知识库核实结论；出行前请以官方与权威来源复核。",
        EN: "The above is either general-knowledge output or a knowledge-base excerpt, not a "
            "verified conclusion of this knowledge base; confirm with official sources "
            "before travelling.",
        JA: "以上は一般知識による回答、またはナレッジベースの抜粋であり、"
            "本ナレッジベースの確認済み結論ではありません。公式の情報源でご確認ください。",
        KO: "위 내용은 일반 상식 답변 또는 지식베이스 발췌이며, 이 지식베이스의 검증된 "
            "결론이 아닙니다. 출발 전 공식 출처에서 확인하세요.",
    },
    # 寒暄 / 引导（intents.social 的 guide 旁路）。**用户可见**产出 → 整段跟输出语言。
    # 再次提醒：不写 markdown 强调标记（** 会原样显示成星号）。
    "gd.head": {
        ZH: "这是一句寒暄或闲话，按引导处理 —— 不检索、不调工具、也不追问天数预算人数，"
            "只回一句把人引到旅游话题的话。",
        EN: "This is a greeting or small talk, handled as a steer toward travel — "
            "no retrieval, no tools, and no itinerary questions.",
        JA: "これは挨拶・雑談として扱います。検索もツール呼び出しも日数・予算・人数の確認もせず、"
            "旅行の話題へやわらかく案内します。",
        KO: "이것은 인사·잡담으로 처리합니다. 검색도, 도구 호출도, 일수·예산·인원 질문도 하지 않고 "
            "여행 주제로 부드럽게 안내합니다.",
    },
    # 模型不可用时的定稿话术（按寒暄类型）。寒暄绝不能因模型挂掉而退化成四连问。
    "gd.reply.greeting": {
        ZH: "你好！我是你的旅行助手，可以帮你排行程、查签证和入境政策，也能回答目的地的具体问题。"
            "想去哪里，或者想了解点什么？",
        EN: "Hi! I'm your travel assistant — I can plan itineraries, check visa and entry "
            "policies, and answer specific questions about destinations. Where would you like "
            "to go, or what would you like to know?",
        JA: "こんにちは！旅行アシスタントです。旅程の作成、ビザ・入国政策の確認、"
            "目的地の具体的な質問への回答ができます。どこへ行きたいですか？何をお知りになりたいですか？",
        KO: "안녕하세요! 여행 어시스턴트입니다. 일정 작성, 비자·입국 정책 확인, "
            "목적지 관련 구체적인 질문에 답해 드릴 수 있어요. 어디로 가고 싶으신가요? 무엇이 궁금하세요?",
    },
    "gd.reply.thanks": {
        ZH: "不客气！还想安排点什么？告诉我想去的城市，或者直接问签证、门票、玩法都可以。",
        EN: "You're welcome! Anything else I can help with? Tell me the city you're eyeing, "
            "or just ask about visas, tickets or things to do.",
        JA: "どういたしまして。ほかに何かお手伝いしましょうか？行きたい都市を教えていただくか、"
            "ビザ・チケット・楽しみ方についてそのまま聞いてください。",
        KO: "천만에요! 더 도와드릴 일이 있을까요? 가고 싶은 도시를 알려주시거나 "
            "비자·티켓·즐길 거리를 바로 물어보세요.",
    },
    "gd.reply.farewell": {
        ZH: "好的，随时回来找我。祝你旅途顺利！",
        EN: "Got it — come back anytime. Have a great trip!",
        JA: "承知しました。いつでもどうぞ。良い旅を！",
        KO: "알겠습니다. 언제든 다시 찾아주세요. 즐거운 여행 되세요!",
    },
    "gd.reply.cancel": {
        ZH: "好的，这次先不安排了。想重新计划的时候说一声，我接着来。",
        EN: "Sure — I'll hold off on this one. Just say the word when you want to pick it "
            "up again.",
        JA: "わかりました。今回は見送りますね。また計画したくなったら声をかけてください。",
        KO: "알겠습니다. 이번 건은 잠시 접어둘게요. 다시 계획하고 싶으시면 말씀해 주세요.",
    },
    # 纯笑声 / 字母数字梗（「哈哈哈」「呵呵」「233」）。第九形态 —— 与问候同源：
    # 不是排行程的请求，答复与用户笑的那声无关，定稿即答案，不调模型。
    "gd.reply.chitchat": {
        ZH: "哈哈，看你心情不错。想安排点什么旅行的事吗？说说想去哪儿，或者想了解什么，我来帮你。",
        EN: "Ha — glad you're in good spirits. Anything travel-related I can help with? "
            "Tell me where you'd like to go, or what you'd like to know.",
        JA: "ハハ、ご機嫌ですね。旅行のことで何かお手伝いしましょうか？行きたい場所や"
            "知りたいことを教えてください。",
        KO: "하하, 기분 좋으시네요. 여행 관련해서 도와드릴까요? 가고 싶은 곳이나 "
            "궁금한 점을 말씀해 주세요.",
    },
    "gd.reply.meta": {
        ZH: "我是旅行助手，主要帮三件事：规划行程、查签证与入境政策、回答目的地问题"
            "（门票、开放时间、怎么玩）。想从哪开始？",
        EN: "I'm a travel assistant. Three main things: planning itineraries, checking visa and "
            "entry policies, and answering destination questions (tickets, opening hours, what "
            "to do). Where shall we start?",
        JA: "旅行アシスタントです。主に三つ：旅程の作成、ビザ・入国政策の確認、"
            "目的地の質問（チケット・開館時間・楽しみ方）への回答。どこから始めましょうか？",
        KO: "저는 여행 어시스턴트입니다. 주로 세 가지를 도와드려요: 일정 작성, 비자·입국 정책 확인, "
            "목적지 질문(티켓·운영 시간·즐길 거리) 답변. 어디서부터 시작할까요?",
    },
    # 记忆类元问题（「我们刚才聊了什么」）。它**不走模型** —— 由会话历史确定性拼出来
    # （`app/graph.py::_recall_reply`）。所以这里给的是定稿骨架，不是让模型改写的素材。
    "gd.recall.empty": {
        ZH: "这是我们这次会话的第一句，之前还没有聊过别的 —— 你可以直接说想去哪、想了解什么。",
        EN: "This is the first message in our session — nothing before it yet. Tell me where "
            "you'd like to go, or what you'd like to know.",
        JA: "このセッションでは最初のひと言です。まだほかの話はしていません。"
            "行きたい場所や知りたいことをそのまま教えてください。",
        KO: "이번 세션의 첫 메시지입니다. 아직 다른 이야기는 하지 않았어요. "
            "가고 싶은 곳이나 알고 싶은 것을 그대로 말씀해 주세요.",
    },
    "gd.recall.list": {
        ZH: "记得。这次会话你先后问了 {count} 句（从早到晚）：{items}。"
            "要继续追哪一句，直接说就行。",
        EN: "Yes — you've asked {count} thing(s) in this session, in order: {items}. "
            "Say the word if you want to continue with any of them.",
        JA: "覚えています。このセッションで {count} 件うかがいました（順に）：{items}。"
            "続けたいものがあればそのままおっしゃってください。",
        KO: "기억하고 있습니다. 이번 세션에서 {count}건을 물으셨습니다(순서대로): {items}. "
            "이어서 볼 항목이 있으면 말씀해 주세요.",
    },
    "gd.reply.recall": {
        ZH: "我们这次会话聊过的我都有记着 —— 你继续追问，我按上一句的主体接着答。",
        EN: "I keep track of what we've covered in this session — follow-up questions will "
            "be answered against the previous subject.",
        JA: "このセッションで話した内容は覚えています。続けて聞いていただければ、"
            "直前の話題を引き継いでお答えします。",
        KO: "이번 세션에서 나눈 내용은 기억하고 있습니다. 이어서 물어보시면 "
            "직전 주제를 이어서 답변합니다.",
    },
    # 建议提问：用 | 分隔，`app/guide.py:starters()` 拆开。点击即直接发送。
    "gd.starters": {
        ZH: "上海有哪些免费博物馆？|240 小时过境免签适用哪些国家？|帮我排一个上海 3 天的行程，预算 5000|外国人来中国怎么用手机支付？",
        EN: "Which museums in Shanghai are free?|Who can use 240-hour visa-free transit?|"
            "Plan a 3-day Shanghai trip with a ¥5000 budget|How do foreign visitors pay by phone in China?",
        JA: "上海で無料の博物館は？|240時間の通過ビザ免除はどの国が対象？|"
            "上海3日間の旅程を作って（予算5000元）|外国人は中国でスマホ決済をどう使う？",
        KO: "상하이 무료 박물관은 어디인가요?|240시간 경유 비자 면제 대상국은?|"
            "상하이 3일 일정을 짜주세요(예산 5000위안)|외국인은 중국에서 모바일 결제를 어떻게 하나요?",
    },
    # 关联问题推荐（市场对标，携程 TripGenie 说这条把人均对话轮次拉了上去）：
    # 每轮答复后给 2–3 条「接着可以问」——用 | 分隔，`app/followups.py` 拆开、前端做成可点按钮。
    # 行程那条刻意**演示迭代句式**：「把某一天放宽」「住宿换便宜点」——教会用户怎么改稿。
    # **按会话槽位个性化**（2026-10-06 收遗留③）：`{day}` 取这一版稿子的天数（原先是写死的 3），
    # `{place}` 取会话目的地 —— 同一批模板在「杭州 3 天」「西安 5 天」会话里不再一字不差。
    "nq.plan": {
        ZH: "把第 {day} 天安排得轻松一点|住宿换成便宜一点的|再多安排一天",
        EN: "Make day {day} more relaxed|Switch to cheaper hotels|Add one more day",
        JA: "{day} 日目をもう少しゆったりに|宿をもう少し安く|もう 1 日追加",
        KO: "{day}일차를 좀 더 여유롭게|숙소를 더 저렴하게|하루 더 추가",
    },
    # 备用池（2026-10-09 用户反馈「可以接着问模块是死的」）：只有上面 3 条时，
    # 同一会话连出几版稿，建议栏一字不差；刚点过的那条还会**原样再出现**。
    # 并入这 3 条后按轮次**轮换窗口**（见 followups.next_questions 的 offset），
    # 相邻两版的建议栏就不再相同；`avoid` 再把「刚做过的那条」摘掉。
    "nq.plan_extra": {
        ZH: "把第 {day} 天换成室内的|少走点路，交通方便些|加一顿当地特色餐",
        EN: "Swap day {day} for indoor options|Less walking, easier transport|Add a local specialty meal",
        JA: "{day} 日目を屋内中心に|移動を少なめに|ご当地の名物を 1 食追加",
        KO: "{day}일차를 실내 위주로|이동을 줄여서|지역 특색 음식 한 끼 추가",
    },
    "nq.answer": {
        ZH: "按这个帮我排进行程|附近还有哪些值得去",
        EN: "Build this into my itinerary|What else nearby is worth visiting",
        JA: "これを旅程に組み込んで|近くに他におすすめは？",
        KO: "이걸 일정에 넣어 주세요|근처에 더 볼 만한 곳은?",
    },
    # 会话里已有目的地时用这套 —— 同一条建议在「杭州」和「西安」会话里不再一字不差。
    "nq.answer_place": {
        ZH: "按这个帮我排进行程|{place}附近还有哪些值得去",
        EN: "Build this into my itinerary|What else near {place} is worth visiting",
        JA: "これを旅程に組み込んで|{place}の近くで他におすすめは？",
        KO: "이걸 일정에 넣어 주세요|{place} 근처에 더 볼 만한 곳은?",
    },
    "nq.realtime": {
        ZH: "按这个帮我排进行程|还有别的类似信息吗",
        EN: "Build this into my itinerary|Any other similar info",
        JA: "これを旅程に組み込んで|ほかに似た情報は？",
        KO: "이걸 일정에 넣어 주세요|비슷한 정보가 더 있나요?",
    },
    "nq.realtime_place": {
        ZH: "按这个帮我排进行程|{place}还有别的类似信息吗",
        EN: "Build this into my itinerary|Any other similar info for {place}",
        JA: "これを旅程に組み込んで|{place}の似た情報は？",
        KO: "이걸 일정에 넣어 주세요|{place}에 비슷한 정보가 더 있나요?",
    },
    # **按话题整套替换**（2026-10-06 菜品探针实测）：饮食话题连问四轮，建议栏四轮一字
    # 不差 —— 用户问的是「素食 / 清真 / 过敏 / 川菜辣不辣」，建议栏一直在说「附近还有
    # 哪些值得去」。话题取自**这一轮的主体**（`state.knowledge_intent.subject`），
    # 命中就整套换成同话题的三条；没命中（认不出话题）就用上面的通用模板。
    # ⚠️ `{place}` 只在会话里真有目的地时才填得到；英文模板因此把地点放在句末，
    # 空着时由 `app/followups.py::_tidy` 收掉悬空介词（「try in」→「try」）。
    "nq.topic_food": {
        ZH: "{place}有哪些值得试的当地菜|不吃辣的话要怎么点菜|素食和清真忌口好不好解决",
        EN: "What local dishes should I try in {place}|How do I order if I can't eat spicy food|Is it easy to manage vegetarian or halal diets",
        JA: "{place}で食べるべき郷土料理は？|辛いものが苦手なときの注文方法は？|ベジタリアンやハラール対応は簡単？",
        KO: "{place}에서 꼭 먹어봐야 할 향토 음식은?|매운 걸 못 먹으면 어떻게 주문하나요?|채식이나 할랄 식단은 해결하기 쉬운가요?",
    },
    "nq.topic_transport": {
        ZH: "{place}地铁怎么坐最省事|高峰期要避开哪些时段|打车和地铁哪个更快",
        EN: "How do I use the metro in {place}|Which hours should I avoid at rush hour|Is a taxi or the metro faster",
        JA: "{place}での地下鉄の乗り方は？|ラッシュを避けるべき時間帯は？|タクシーと地下鉄どちらが速い？",
        KO: "{place}에서 지하철 타는 법은?|러시아워를 피할 시간대는?|택시와 지하철 중 무엇이 빠른가요?",
    },
    "nq.topic_pay": {
        ZH: "{place}能用境外银行卡吗|还要不要带现金|移动支付要怎么开通",
        EN: "Can I use my foreign card in {place}|Should I still carry cash|How do I set up mobile payment",
        JA: "{place}で海外のカードは使える？|現金も持っていくべき？|スマホ決済の始め方は？",
        KO: "{place}에서 해외 카드를 쓸 수 있나요?|현금도 준비해야 하나요?|모바일 결제는 어떻게 시작하나요?",
    },
    "nq.topic_visa": {
        ZH: "过境免签适用哪些国家|签证要提前多久办|入境要准备哪些材料",
        EN: "Which countries qualify for transit visa-free|How early should I apply for a visa|What documents do I need on arrival",
        JA: "トランジットビザ免除の対象国は？|ビザはどれくらい前に取るべき？|入国時に必要な書類は？",
        KO: "경유 무비자는 어느 나라가 해당되나요?|비자는 얼마나 일찍 신청해야 하나요?|입국 시 필요한 서류는?",
    },
    "nq.topic_net": {
        ZH: "手机卡怎么买最方便|eSIM 能用吗|没网了怎么办",
        EN: "How do I buy a local SIM card|Can I use an eSIM|What if I lose connection",
        JA: "現地のSIMカードの買い方は？|eSIMは使える？|ネットがつながらないときは？",
        KO: "현지 유심은 어떻게 사나요?|eSIM도 쓸 수 있나요?|인터넷이 안 되면 어떡하나요?",
    },
    "nq.topic_stay": {
        ZH: "酒店入住要什么证件|住哪个区域更方便|行李能提前寄存吗",
        EN: "What documents do hotels need at check-in|Which area is most convenient to stay|Can I store my luggage early",
        JA: "ホテルのチェックインに必要なものは？|どのエリアに泊まるのが便利？|荷物を早めに預けられる？",
        KO: "호텔 체크인에 필요한 서류는?|어느 지역에 묵는 게 편한가요?|짐을 미리 맡길 수 있나요?",
    },
    "nq.topic_sight": {
        ZH: "{place}还有哪些值得去的景点|门票要提前预约吗|什么时间去人最少",
        EN: "What else is worth visiting in {place}|Do I need to book tickets in advance|What time is least crowded",
        JA: "{place}で他に行くべき観光地は？|チケットは事前予約が必要？|一番空いている時間は？",
        KO: "{place}에서 더 가볼 만한 곳은?|티켓은 미리 예약해야 하나요?|가장 한산한 시간은?",
    },
    # 购物 / 退税：与 `pay` 分开的原因见 `app/followups.py::_TOPIC_KEYS`（退税归这里）。
    "nq.topic_shop": {
        ZH: "{place}买东西能退税吗|哪里可以砍价|买到假货怎么维权",
        EN: "Can I get a tax refund in {place}|Where is bargaining expected|What if I think I bought a fake",
        JA: "{place}で買い物したら税金還付は受けられる？|値切りはどこでできる？|偽物を買ったらどうする？",
        KO: "{place}에서 쇼핑하면 환급받을 수 있나요?|흥정은 어디서 가능한가요?|가짜를 샀으면 어떡하나요?",
    },
    "nq.topic_apps": {
        ZH: "Google 地图在中国能用吗|导航要用哪个应用|微信和支付宝要提前装吗",
        EN: "Does Google Maps work in China|Which app should I use for navigation|Should I install WeChat and Alipay before I arrive",
        JA: "Google マップは中国で使える？|ナビはどのアプリを使う？|WeChatとアリペイは事前に入れるべき？",
        KO: "구글 지도는 중국에서 되나요?|내비는 어떤 앱을 쓰나요?|위챗과 알리페이를 미리 깔아야 하나요?",
    },
    "nq.topic_weather": {
        ZH: "几月来中国最舒服|要带什么衣服|空气质量差的时候怎么办",
        EN: "Which month is most comfortable in China|What clothes should I pack|What should I do when the air quality is bad",
        JA: "中国は何月が一番快適？|どんな服を持っていく？|空気が悪いときはどうする？",
        KO: "중국은 몇 월이 가장 좋나요?|어떤 옷을 준비해야 하나요?|공기가 나쁠 때는 어떻게 하나요?",
    },
    "nq.topic_safety": {
        ZH: "中国夜晚上街安全吗|护照丢了怎么办|有哪些常见的旅游骗局",
        EN: "Is it safe to go out at night in China|What should I do if I lose my passport|What are the common tourist scams",
        JA: "中国の夜は安全？|パスポートをなくしたら？|よくある観光客向けの詐欺は？",
        KO: "중국 밤거리는 안전한가요?|여권을 잃어버리면 어떡하나요?|흔한 관광객 사기는?",
    },
    "nq.topic_holiday": {
        ZH: "国庆和春节要避开吗|热门景点要提前多久预约|博物馆周一是闭馆吗",
        EN: "Should I avoid National Day and Chinese New Year|How early should I book popular sights|Are museums closed on Mondays",
        JA: "国慶節と春節は避けるべき？|人気スポットはいつ予約する？|博物館は月曜休館？",
        KO: "국경절과 춘절은 피해야 하나요?|인기 명소는 얼마나 일찍 예약하나요?|박물관은 월요일에 닫나요?",
    },
    "nq.topic_language": {
        ZH: "英文在中国够用吗|菜单看不懂怎么办|有哪些能马上用的中文短句",
        EN: "Is English enough in China|What if I can't read the menu|What Chinese phrases can I use right away",
        JA: "中国では英語で通じる？|メニューが読めないときは？|すぐ使える中国語のフレーズは？",
        KO: "중국에서 영어로 통하나요?|메뉴를 못 읽으면 어떡하나요?|바로 쓸 수 있는 중국어 표현은?",
    },
    "nq.topic_health": {
        ZH: "在中国看病要准备什么|药店能买到哪些药|要不要买旅行医疗保险",
        EN: "What do I need to see a doctor in China|What can I buy at a pharmacy|Should I get travel medical insurance",
        JA: "中国で病院にかかるときの準備は？|薬局で買える薬は？|海外旅行保険は必要？",
        KO: "중국에서 병원에 갈 때 준비할 것은?|약국에서 살 수 있는 약은?|여행자 의료보험이 필요한가요?",
    },
    "nq.topic_manner": {
        ZH: "中国要给小费吗|吃饭有哪些禁忌|公共场所要注意什么",
        EN: "Do I need to tip in China|What are the dining taboos|What should I mind in public",
        JA: "中国ではチップは必要？|食事のときのタブーは？|公共の場での注意点は？",
        KO: "중국에서 팁을 줘야 하나요?|식사할 때 금기는?|공공장소에서 주의할 점은?",
    },
    # 关联问题推荐的**上下文个性化**（2026-10-06 收遗留③·续）：按会话槽位换掉**一条**。
    # 取舍：只动一条 —— 建议栏是「引导下一步」，不是把用户自己的输入复述一遍；换的位置也讲究：
    #   行程稿 → 换掉第 1 条（那条本来就讲「怎么改这一版稿」）；
    #   常识答复 → 换掉最后一条（那条是「附近还有什么」的泛推荐，最容易被更贴的一条替代）。
    # **只用会话里真的出现过的槽位**（孩子/长者/兴趣），没提到就一条都不动。
    "nq.ctx_plan_kids": {
        ZH: "把第 {day} 天安排得适合孩子",
        EN: "Make day {day} kid-friendly",
        JA: "{day} 日目を子ども向けに",
        KO: "{day}일차를 아이와 함께하기 좋게",
    },
    "nq.ctx_plan_elder": {
        ZH: "把第 {day} 天安排得轻松些、少走路",
        EN: "Make day {day} easier with less walking",
        JA: "{day} 日目は移動を少なく、ゆったりに",
        KO: "{day}일차는 걷는 거리를 줄여 여유롭게",
    },
    "nq.ctx_food": {
        ZH: "{place}有哪些必吃的本地菜",
        EN: "Must-try local dishes in {place}",
        JA: "{place}で必ず食べたい料理は？",
        KO: "{place}에서 꼭 먹어야 할 음식은?",
    },
    "nq.ctx_history": {
        ZH: "{place}有哪些必去的博物馆和古迹",
        EN: "Must-see museums and historic sites in {place}",
        JA: "{place}で必ず行きたい博物館・古跡は？",
        KO: "{place}에서 꼭 가야 할 박물관과 유적은?",
    },
    "nq.ctx_nature": {
        ZH: "{place}周边有哪些自然风光",
        EN: "Natural scenery around {place}",
        JA: "{place}周辺の自然景観は？",
        KO: "{place} 주변의 자연 경관은?",
    },
    "nq.ctx_shopping": {
        ZH: "{place}去哪里购物比较划算",
        EN: "Where to shop in {place}",
        JA: "{place}でお買い物ならどこ？",
        KO: "{place}에서 쇼핑하기 좋은 곳은?",
    },
    "nq.ctx_nightlife": {
        ZH: "{place}有哪些值得看的夜景或夜市",
        EN: "Night views or night markets in {place}",
        JA: "{place}の夜景・ナイトマーケットは？",
        KO: "{place}의 야경이나 야시장은?",
    },
    "nq.ctx_culture": {
        ZH: "{place}有哪些值得看的演出或民俗",
        EN: "Shows or folk culture to see in {place}",
        JA: "{place}で見られる公演や民俗は？",
        KO: "{place}에서 볼 만한 공연이나 민속은?",
    },
    "nq.ctx_photo": {
        ZH: "{place}有哪些适合拍照的地方",
        EN: "Best photo spots in {place}",
        JA: "{place}の写真映えスポットは？",
        KO: "{place}에서 사진 찍기 좋은 곳은?",
    },
    "nq.ctx_family_place": {
        ZH: "{place}有哪些适合带孩子去的地方",
        EN: "Family-friendly places in {place}",
        JA: "{place}で子どもと行ける場所は？",
        KO: "{place}에서 아이와 갈 만한 곳은?",
    },
    "gd.note": {
        ZH: "这类输入按寒暄或引导处理：不检索、不调工具、也不追问天数预算人数。"
            "下面是建议的提问，点一下就能直接问。",
        EN: "This input is treated as a greeting/steer: no retrieval, no tools, no itinerary "
            "questions. Below are suggested questions — tap one to ask.",
        JA: "この種の入力は挨拶・案内として扱います。検索・ツール呼び出し・日数/予算/人数の確認は"
            "行いません。下の候補をタップするとそのまま質問できます。",
        KO: "이런 입력은 인사·안내로 처리합니다. 검색·도구 호출·일수/예산/인원 질문을 하지 않습니다. "
            "아래 추천 질문을 누르면 바로 물어볼 수 있어요.",
    },
    "gd.disclaimer": {
        ZH: "我不是人工客服；行程与政策类问题会给出可核实的来源，请以官方公告为准。",
        EN: "I'm not a human agent; for itinerary and policy questions I provide verifiable "
            "sources — always defer to official announcements.",
        JA: "私は人工オペレーターではありません。旅程・政策の質問には確認可能な出典を示します。"
            "公式発表を優先してください。",
        KO: "저는 상담원이 아닙니다. 일정·정책 질문에는 확인 가능한 출처를 제시합니다. "
            "공식 공고를 우선하세요.",
    },
    # 清单
    "cl.source": {
        ZH: "来源",
        EN: "source",
        JA: "出典",
        KO: "출처",
    },
    "cl.effective": {
        ZH: "生效日",
        EN: "effective",
        JA: "施行日",
        KO: "시행일",
    },
    "cl.missing": {
        ZH: "缺失",
        EN: "missing",
        JA: "不明",
        KO: "없음",
    },
    "cl.no_realtime": {
        ZH: "未检索到有效期内的官方摘录，无法给出材料清单，请以使领馆公告为准。",
        EN: "No unexpired official excerpt was retrieved, so no document checklist can be given. "
            "Please rely on the embassy/consulate announcement.",
        JA: "有効期限内の公式抜粋が取得できなかったため、必要書類の一覧は提示できません。"
            "大使館・領事館の発表をご確認ください。",
        KO: "유효기간 내 공식 발췌를 찾지 못해 서류 목록을 제시할 수 없습니다. "
            "대사관·영사관 공고를 확인해 주세요.",
    },
    "cl.kind.doc": {ZH: "证件", EN: "Documents", JA: "書類", KO: "서류"},
    "cl.kind.packing": {ZH: "打包", EN: "Packing", JA: "荷造り", KO: "짐 싸기"},
    "cl.kind.insurance": {ZH: "保险", EN: "Insurance", JA: "保険", KO: "보험"},
    "cl.kind.connectivity": {ZH: "通讯", EN: "Connectivity", JA: "通信", KO: "통신"},
    "cl.kind.ticketing": {ZH: "预约", EN: "Bookings", JA: "予約", KO: "예약"},
    "cl.kind.money": {ZH: "支付", EN: "Payment", JA: "支払い", KO: "결제"},
    "cl.family": {
        ZH: "儿童：随身带替换衣物、常用退烧药；确认住宿可提供婴儿床或加床。",
        EN: "Children: bring spare clothes and children's fever medicine; confirm the hotel "
            "can provide a cot or extra bed.",
        JA: "お子様：着替えと子供用解熱剤を携帯し、ベビーベッドやエキストラベッドの可否を確認。",
        KO: "어린이: 여벌 옷과 어린이 해열제를 챙기고, 유아용 침대·엑스트라 베드 제공 여부를 확인하세요.",
    },
    "cl.budget": {
        ZH: "预算：按 10% 缓冲预留现金，境外小额支出常被低估。",
        EN: "Budget: keep cash for the 10% buffer; small overseas expenses are often underestimated.",
        JA: "予算：10%の余裕分を現金で確保。海外での少額支出は見落とされがちです。",
        KO: "예산: 10% 여유분을 현금으로 준비하세요. 해외 소액 지출은 자주 과소평가됩니다.",
    },
    "cl.roadtrip": {
        ZH: "车辆：取车拍车身视频、记录油量与里程，确认道路救援电话。",
        EN: "Vehicle: film the car at pickup, note fuel level and mileage, save the roadside "
            "assistance number.",
        JA: "車両：受け取り時に車体を撮影し、燃料と走行距離を記録、ロードサービス番号を控える。",
        KO: "차량: 인수 시 차량 촬영, 연료·주행거리 기록, 긴급출동 번호 저장.",
    },
    "cl.default": {
        ZH: "证件：确认护照有效期覆盖行程结束后 6 个月，并留复印件。",
        EN: "Documents: make sure your passport is valid for at least 6 months beyond the trip, "
            "and keep a copy.",
        JA: "書類：パスポートの有効期限が旅程終了後6か月以上あることを確認し、コピーを保管。",
        KO: "서류: 여권 유효기간이 여행 종료 후 6개월 이상인지 확인하고 사본을 보관하세요.",
    },
}


def t(key: str, lang: str = DEFAULT, **kwargs: Any) -> str:
    """取用户可见文案。未知 key 或未知语言一律退回中文，绝不抛异常。"""
    table = _UI.get(key)
    if not table:
        return key
    template = table.get(lang) or table.get(DEFAULT) or next(iter(table.values()))
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError):
        return template


# ---------------------------------------------------------------- guardrail 文案
# guardrail 正文（免责声明、儿童/长者节奏提示）也出现在**用户可见**的
# `itinerary.disclaimers` 里，所以必须跟输出语言。
#
# 这里**只放非中文译文**，中文仍以 `routes.yaml` 的 `guardrails.*.text` 为唯一真源 ——
# 两边各写一份中文迟早会分叉。查不到译文就退回配置里的中文。
_GUARD: dict[str, dict[str, str]] = {
    "safety_disclaimer": {
        EN: "This itinerary is a suggestion only. Re-check weather, transport and attraction "
            "notices before you travel.",
        JA: "本行程は参考案です。出発前に天気・交通・観光施設のお知らせを必ずご確認ください。",
        KO: "본 일정은 참고용 제안입니다. 출발 전 날씨·교통·관광지 공지를 반드시 확인하세요.",
    },
    "elder_pacing": {
        EN: "Travelling with seniors: keep each day to 2–3 stops, schedule a midday rest, "
            "and avoid long stretches of walking.",
        JA: "ご年配の方とご一緒の場合、1日の予定は2〜3か所に抑え、昼休みを確保し、"
            "長時間の歩行を避けてください。",
        KO: "어르신과 동행하는 경우 하루 2~3곳으로 줄이고, 낮 휴식을 넣고, "
            "장시간 도보를 피하세요.",
    },
    "minor_pacing": {
        EN: "Travelling with a child under 6: build in a nap window and keep each hop "
            "under 1.5 hours.",
        JA: "6歳以下のお子様連れの場合、昼寝の時間を確保し、移動は1回1.5時間以内に。",
        KO: "6세 이하 어린이 동반 시 낮잠 시간을 확보하고, 이동은 한 번에 1.5시간 이내로 하세요.",
    },
    "visa_not_legal_advice": {
        EN: "The above is a compilation of public information and does not constitute legal "
            "advice. Refer to the official embassy/consulate announcement.",
        JA: "以上は公開情報の整理であり、法的助言ではありません。大使館・領事館の公式発表をご確認ください。",
        KO: "위 내용은 공개 정보 정리이며 법률 자문이 아닙니다. 대사관·영사관 공식 공고를 확인하세요.",
    },
}


def guardrail_text(settings: Any, guardrail_id: str, language: str = DEFAULT) -> str:
    """取 guardrail 文案：非中文优先用译文，中文一律走配置（`routes.yaml`）。"""
    configured = settings.guardrail_text(guardrail_id)
    if language == DEFAULT:
        return configured
    table = _GUARD.get(guardrail_id) or {}
    return table.get(language) or configured