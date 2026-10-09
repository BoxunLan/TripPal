"""会话记忆（追问承接 + 元问题）。

真 bug（2026-10-04 用户报「一是没有对话记忆」）：一个会话里问完「上海有没有免费的博物馆」，
接着问「那要预约吗？」，系统回的是

    还需要确认：想去的城市或国家是哪里？计划玩几天？…… 一行几个人？

四个问题。因为每一句都被当成独立的一句：`clarify_node` 只拿当前 message 判意图，
上一轮解析出的主体就地丢了。`app/session.py` 里那个 `history` 字段**只写不读**，
连日志都没有 —— 用户想回看自己刚问过什么，只能翻浏览器。

本组用例按**类**守三件事：

1. 追问句（短、无地名、带疑问）必须承接上一轮主体，并且**不许吞掉真请求**；
2. 承接要靠得住 —— 承接来的实词要和上一轮并起来，否则第二问就落到别的条目上；
3. 「刚才我说了什么」这类元问题由历史**确定性**回答，不调模型。
"""

from __future__ import annotations

from conftest import post_plan

from app.intent import carry_over_subject, locative_referent
from app.session import SessionStore

FOUR_QUESTIONS = ("想去的城市", "计划玩几天", "预算是多少", "一行几个人")


def _looks_like_the_four_questions(text: str) -> bool:
    return sum(1 for q in FOUR_QUESTIONS if q in (text or "")) >= 2


# ---------------------------------------------------------------- 承接
def test_followup_question_inherits_the_previous_subject(client):
    """「那要预约吗？」自己没有主体 → 接上一轮的「上海」，**不能再回四连问**。"""
    session = "mem-follow"
    first = post_plan(client, "上海有没有免费的博物馆或博览园", session_id=session)
    second = post_plan(client, "那要预约吗？", session_id=session)

    assert first["type"] == "answer", first
    assert second["type"] == "answer", (
        f"追问句掉回槽位体检了：{second.get('question') or second}"
    )
    assert second["subject"] == "上海", second
    assert not _looks_like_the_four_questions(second.get("note", "")), second["note"]


def test_followup_keeps_listing_the_same_subject_matter(client):
    """承接不只是「有个主体」而已：条目也要还落在上一轮那一批上。

    靠的是把上一轮的实词一起带进检索（对话式查询扩展）。少了它，「那要预约吗」自己
    那几个词（「预约」）会落到别的条目上，答的是另一件事。
    """
    session = "mem-carry-terms"
    post_plan(client, "上海有没有免费的博物馆或博览园", session_id=session)
    followup = post_plan(client, "那要预约吗？", session_id=session)

    assert followup["type"] == "answer", followup
    assert followup["hits"], "承接来的追问一条都没命中 —— 上一轮的实词没带过来"
    assert followup["hits"][0]["chunk_id"] == "gen-in-museum-001", [
        h["chunk_id"] for h in followup["hits"]
    ]


def test_followup_does_not_swallow_a_new_request(client):
    """「你好」之后说「那去上海玩三天」—— 这是**新的行程请求**，不是追问。

    `carry_over_subject` 的四条守卫里，「没有解析出目的地」与「不含行程槽位」就是为它设的：
    少了守卫，一句礼貌开场会把后面真正的问题一起带进事实旁路，用户的行程请求就丢了。
    """
    session = "mem-not-swallow"
    post_plan(client, "你好", session_id=session)
    after = post_plan(client, "那去上海玩三天", session_id=session)

    assert after["type"] == "clarify", after
    assert "budget" in after["missing_slots"] and "party" in after["missing_slots"], after


def test_no_history_means_no_carry(client):
    """全新会话里问「那要预约吗」—— 没有可承接的东西，老老实实追问。"""
    body = post_plan(client, "那要预约吗？", session_id="mem-fresh")

    assert body["type"] == "clarify", body


def test_realtime_followup_inherits_subject_and_place(client):
    """「开放时间呢？」走实时旁路，主体与所在地同样要承接过来。

    承接必须在 relevance 处理**之后**做（政策类会把主体覆盖成命中的政策名），
    也要在 question_zh 拼装**之前**做（否则检索式里带的是「开放时间呢」）。

    注意承接的是**「上海博物馆」而不是「上海」**：上一句的主语切出来本来就是「上海博物馆」
    （`要预约` 之前的全部），承接要原样带过来 —— 降级成城市就是把用户的话读粗了。
    """
    session = "mem-realtime"
    post_plan(client, "上海博物馆要预约吗", session_id=session)
    body = post_plan(client, "开放时间呢？", session_id=session)

    assert body["type"] == "realtime", body
    assert body["subject"] == "上海博物馆", body
    # `place` 的口径与实时闸门一致：**城市**（北京问天气不该看到别的城市的入口）。
    assert body["place"] == "上海", body


# ---------------------------------------------------------------- 元问题
def test_recall_is_answered_from_history_not_by_the_model(client):
    """「我刚才说了什么」必须由**会话历史**回答，且不许退化成四连问。

    这句问话是用户在验证系统记不记得住 —— 交给模型只会：看不到历史时编，
    看得到历史时也可能答歪。唯一正确的来源就是我们自己记的那几轮。
    """
    session = "mem-recall"
    post_plan(client, "你好", session_id=session)
    post_plan(client, "上海外卡支付怎么用", session_id=session)
    body = post_plan(client, "我们刚才聊了什么", session_id=session)

    assert body["type"] == "guide", body
    assert body["kind"] == "recall", body
    assert "你好" in body["reply"], body["reply"]
    assert "上海外卡支付怎么用" in body["reply"], body["reply"]


def test_recall_on_a_fresh_session_says_it_is_the_first_message(client):
    """第一句就问「刚才聊了什么」→ 如实说没有前面的对话，而不是编一段。"""
    body = post_plan(client, "刚才我说了什么", session_id="mem-recall-empty")

    assert body["type"] == "guide", body
    assert body["kind"] == "recall", body
    assert "第一句" in body["reply"], body["reply"]


# ---------------------------------------------------------------- 可查看
def test_session_endpoint_exposes_what_was_remembered(client):
    """`GET /session/{sid}` 要能看出「记住了什么」—— 面板与审计都读它。"""
    session = "mem-snapshot"
    post_plan(client, "上海有没有免费的博物馆或博览园", session_id=session)
    post_plan(client, "那要预约吗？", session_id=session)

    snap = client.get(f"/session/{session}").json()

    assert snap["turn"] == 2, snap
    assert len(snap["recent"]) == 2, snap
    assert snap["recent"][0]["message"] == "上海有没有免费的博物馆或博览园"
    assert snap["last_fact_subject"] == "上海", snap


# ---------------------------------------------------------------- 守卫（单元）
def _session_with(subject: str, place: str = "中国") -> SessionStore:
    store = SessionStore()
    store.remember_fact("s", subject=subject, place=place, terms=["博物", "物馆"])
    return store


def test_carry_over_only_for_short_destination_free_questions(deps):
    """四个条件：会话里有主体、消息短、没地名、带疑问。任一不满足就不承接。"""
    store = _session_with("上海")
    session = store.get("s")

    # 承接：短、无地名、带疑问
    assert carry_over_subject("那要预约吗？", deps.settings, session, {}) == ("上海", "中国")
    # 不承接：自带地名（这是新请求）
    assert carry_over_subject("换成北京的", deps.settings, session, {}) == (None, None)
    # 不承接：带行程槽位
    assert carry_over_subject("那玩三天呢", deps.settings, session, {"days": 3}) == (None, None)
    # 不承接：没有疑问/属性提示（陈述句）
    assert carry_over_subject("上海真不错", deps.settings, session, {}) == (None, None)
    # 不承接：太长的消息自带上下文
    assert carry_over_subject("那" + "预约" * 12 + "吗", deps.settings, session, {}) == (None, None)


def test_carry_over_needs_a_remembered_subject(deps):
    """第一句没有可承接的东西 —— 不许凭空造一个主体出来。"""
    empty = SessionStore().get("s")

    assert carry_over_subject("那要预约吗？", deps.settings, empty, {}) == (None, None)


# ---------------------------------------------------------------- 方位指代（「那里」）
def test_a_new_subject_with_a_locative_still_gets_answered(client):
    """真 bug（2026-10-04 用户报，与闸门那条同批）：

        上海临港有哪些大学  →  华东师范大学是不是有个新校区在那里

    第二句**自带新主体**（华东师范大学），所以整体承接是错的（会把主体覆盖成上一轮的）。
    但它也**不该掉进行程四连问** —— 那是上一节修掉的那类错误。两句都要成立：
    主体取「华东师范大学」，同时里面的「那里」解得开（见下一条）。
    """
    session = "mem-locative"
    post_plan(client, "上海临港有哪些大学", session_id=session)
    body = post_plan(client, "华东师范大学是不是有个新校区在那里", session_id=session)

    assert body["type"] == "answer", body.get("question") or body
    assert body["subject"] == "华东师范大学", body
    assert not _looks_like_the_four_questions(body.get("note", "")), body["note"]


def test_the_next_followup_still_lands_on_the_place(client):
    """紧接着的「那里有什么好吃的」必须还落在**临港**上。

    这一句才是真承接（自带方位指代）。若上一轮只把主体记成「华东师范大学」、不记地点，
    「那里」就没了落点，答的会是另一个地方。
    """
    session = "mem-locative-next"
    post_plan(client, "上海临港有哪些大学", session_id=session)
    post_plan(client, "华东师范大学是不是有个新校区在那里", session_id=session)
    body = post_plan(client, "那里有什么好吃的", session_id=session)

    assert body["type"] == "answer", body
    assert body["subject"] == "上海临港", body


def test_locative_pronoun_resolves_to_the_previous_place(deps):
    """解引用口径：上一轮主体**本身就是地名**就用它（比城市级所在地更精确）；
    不是地名才退到上一轮的**所在地**（「华师大的新校区在临港」，落点是临港）。"""
    place_like = SessionStore()
    place_like.remember_fact("s", subject="上海临港", place="上海")
    assert locative_referent("那里有什么好吃的", deps.settings, place_like.get("s")) == "上海临港"

    entity = SessionStore()
    entity.remember_fact("s", subject="华东师范大学", place="上海临港")
    assert locative_referent("那里交通方便吗", deps.settings, entity.get("s")) == "上海临港"


def test_locative_pronoun_needs_a_referent_and_yields_to_a_new_place(deps):
    """不许瞎猜：没有可回指的对象、或这一句自带目的地（「上海那里有什么好吃的」）时返回空。"""
    empty = SessionStore().get("s")
    assert locative_referent("那里有什么好吃的", deps.settings, empty) == ""

    session = SessionStore()
    session.remember_fact("s", subject="厦门", place="中国")
    assert locative_referent("上海那里有什么好吃的", deps.settings, session.get("s")) == ""
    # 「哪里」是问句词，不是方位指代 —— 不能拿它去回指上一轮
    assert locative_referent("哪里好玩", deps.settings, session.get("s")) == ""


def test_remember_fact_ignores_empty_values(deps):
    """空值不许覆盖已记住的主体：「换成北京的」那一轮不该把「上海」抹掉。"""
    store = _session_with("上海")
    store.remember_fact("s", subject="", place="")

    assert store.get("s").last_fact_subject == "上海"
    assert store.get("s").last_fact_place == "中国"


# ---------------------------------------------------------------- 出稿推进话题
def test_a_plan_advances_the_topic_for_the_next_followup(client):
    """行程出稿后，省略式追问要接到**这一版行程**上，而不是更早那轮的事实主体。

    真 bug（2026-10-09 多语言对话探针实测）：「西湖有多大」→「北京玩 3 天」出稿 →
    「那要预约吗」被答成了**西湖** —— 出稿没推进 `last_fact_subject`，承接停在了两轮之前。
    """
    session = "mem-plan-topic"
    first = post_plan(client, "上海博物馆要预约吗", session_id=session)
    plan = post_plan(
        client, "想去北京玩 3 天，两个人，预算 3000，帮我排个行程", session_id=session
    )
    after = post_plan(client, "那要预约吗？", session_id=session)

    assert first["type"] == "answer" and first["subject"] == "上海博物馆", first
    assert plan["type"] == "plan", plan
    assert after["type"] == "answer", f"追问掉回槽位体检了：{after}"
    assert after["subject"] == "北京", f"追问停在了更早那轮的主体上：{after['subject']!r}"

    snap = client.get(f"/session/{session}").json()
    assert snap["last_fact_subject"] == "北京", snap
