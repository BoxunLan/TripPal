"""外文输入（英 / 日 / 韩 / 法 / 西 / 德）回归。

真 bug（2026-10-08 外文探针实测，用户要求「试试输入外文的交互过程」后报出四类）：

1. **寒暄门只覆盖中 / 英**：日 / 韩 / 法 / 西 / 德的寒暄全部掉进 clarify 四连问
   （而回复话术还是用对方语言写的，等于用母语问「你预算多少」）；
2. **实时门主体抽取套了中文语序**：`What are the opening hours of the Forbidden City?`
   的主体成了 `What are the`（命中词之前只剩疑问 + 助动词）；
3. **常识门主体切出英文残片**：`What is West Lake famous for?` 的卡片标题成了
   `About “West Lake famous”`；
4. **部分英文问法掉澄清器**：`What food is Beijing famous for`、
   `What exhibitions are at the National Museum of China` 都没带问号，既不命中
   话题专属触发词、也不走 `consult_question` 的 `[?？]` 兜底，于是整句掉回四连问。

本组按**类**守，不按单条：非中文问句一律不得套中文语序（主体落在实体上），
非中文寒暄一律走 guide。
"""

from __future__ import annotations

import pytest
from conftest import post_plan

from app.intent import (
    detect_knowledge_intent,
    detect_realtime_intent,
    detect_social_intent,
)


# ------------------------------------------------------------------ 寒暄门
@pytest.mark.parametrize(
    "message, kind",
    [
        ("こんにちは", "greeting"),
        ("안녕하세요", "greeting"),
        ("Bonjour, comment allez-vous ?", "greeting"),
        ("Hola, buenos días", "greeting"),
        ("Hallo, guten Tag", "greeting"),
        ("Merci beaucoup", "thanks"),
        ("Thank you so much!", "thanks"),
    ],
)
def test_foreign_greetings_get_a_guide_not_the_four_questions(client, message, kind):
    """非中文寒暄 → guide；**绝不能**掉进 clarify 的槽位体检。"""
    body = post_plan(client, message, session_id="foreign-social")

    assert body["type"] == "guide", f"{message} 落到 {body.get('type')}：{body}"
    assert body["kind"] == kind
    assert body["reply"], "引导语不能为空"
    assert not body.get("missing_slots"), "寒暄不该带行程槽位追问"


def test_foreign_greeting_is_detected_deterministically(deps):
    """确定性判定：不依赖模型，逐语种都要命中。"""
    s = deps.settings
    for message, kind in (
        ("こんにちは", "greeting"),
        ("안녕하세요", "greeting"),
        ("Bonjour, comment allez-vous ?", "greeting"),
        ("Merci beaucoup", "thanks"),
    ):
        intent = detect_social_intent(message, s, {})
        assert intent is not None, f"{message} 未被判定为寒暄"
        assert intent.kind == kind


def test_long_cjk_message_is_still_not_small_talk(deps):
    """让路护栏仍在：中文长句（含真请求）不得被开头那声招呼吞掉。

    非中文用更宽的上限（`max_chars_latin`），中文仍按 24 字符 —— 这条守着后者。
    """
    s = deps.settings
    assert detect_social_intent("你好啊我最近在计划一场很长很长的旅行你能帮我看看吗", s, {}) is None


# ---------------------------------------------------------------- 实时门主体
def test_english_realtime_subject_falls_back_to_the_place(deps):
    """命中词之前只剩疑问 + 助动词时，主体退到解析出的地名，不再留英文残片。"""
    intent = detect_realtime_intent(
        "What are the opening hours of the Forbidden City?", deps.settings, {}
    )
    assert intent is not None
    assert intent.subject == "北京", f"主体成了英文残片：{intent.subject!r}"


# ---------------------------------------------------------------- 常识门主体
@pytest.mark.parametrize(
    "message, subject",
    [
        ("What is West Lake famous for?", "West Lake"),
        ("What is there to do in Beijing", "Beijing"),
        ("What food is Beijing famous for", "Beijing"),
        ("What exhibitions are at the National Museum of China", "National Museum of China"),
        ("Tell me about Beijing", "Beijing"),
    ],
)
def test_english_subject_is_the_entity_not_a_function_fragment(deps, message, subject):
    """英文问句的主体必须落在实体上，不能是「What is」/「there to do」这类功能词残片。"""
    intent = detect_knowledge_intent(message, deps.settings, {})
    assert intent is not None, f"{message} 没进常识闸门"
    assert intent.subject == subject, f"{message} 的主体是 {intent.subject!r}"


def test_english_topic_inside_the_trigger_is_not_replaced_by_a_place(deps):
    """⚠️ 反向护栏（2026-10-08 回归）：触发词**本身带话题**时不许被地点顶掉。

    `Do people speak English in China` 的命中片段是 `Do people speak English`，
    主题词 `English` 就在里面 —— 若按「以功能词开头就取后面的尾截」改主体，
    会变成 `China`，问语言却列出防骗条目（两条语言用例曾因此变红）。
    """
    intent = detect_knowledge_intent("Do people speak English in China", deps.settings, {})
    assert intent is not None
    assert "english" in intent.subject.lower(), f"主体丢了主题词：{intent.subject!r}"


# ---------------------------------------------------------------- 路由（端到端）
@pytest.mark.parametrize(
    "message",
    [
        "What food is Beijing famous for",
        "What exhibitions are at the National Museum of China",
        "What is West Lake famous for?",
        "Tell me about Beijing",
    ],
)
def test_english_entity_questions_never_fall_back_to_clarify(client, message):
    """英文问事句 → 常识链路（answer），不得掉回澄清器。"""
    body = post_plan(client, message, session_id="foreign-qa")
    assert body["type"] == "answer", f"{message} 落到 {body.get('type')}：{body}"


# ---------------------------------------------------------------- 让路护栏
def test_foreign_greeting_does_not_swallow_a_real_request(client):
    """带目的地的英文寒暄开头仍走行程链路，不被招呼吞掉。"""
    body = post_plan(client, "Hello, I want to visit Beijing for 3 days", session_id="foreign-mix")
    assert body["type"] != "guide", "带真请求的寒暄被当成纯寒暄吞掉了"


# ---------------------------------------------------------------- 实时门：日 / 韩开放时间
@pytest.mark.parametrize(
    "message",
    [
        "故宮の入場時間は何時ですか",
        "故宮は何時に開きますか",
        "자금성 개방 시간은 몇 시예요",
        "자금성은 몇 시에 문을 여나요",
    ],
)
def test_ja_ko_opening_hours_go_to_the_realtime_gate(deps, message):
    """日 / 韩的「开放时间」问句必须进实时闸门 —— 与中 / 英同义。

    真 bug（2026-10-09 多语言对话探针实测）：「故宮の入場時間は何時ですか」
    「자금성 개방 시간은 몇 시예요」两道闸门全灭 → 掉回 clarify 四连问，而**同一句英文**
    「What are the opening hours of the Forbidden City?」能进本闸门。
    修法：给实时门 schedule 补日 / 韩的「开放 + 时间」构造。
    """
    intent = detect_realtime_intent(message, deps.settings, {})
    assert intent is not None, f"{message} 没进实时闸门"
    assert intent.info_type == "schedule", intent.info_type


@pytest.mark.parametrize(
    "message",
    [
        "ラッシュ時はいつ",
        "혼잡한 시간은?",
        "何時に行けば空いていますか",
        "지하철 몇 시에 붐벼요",
    ],
)
def test_ja_ko_crowd_questions_are_not_realtime(deps, message):
    """反向护栏：裸「何時 / 몇 시」问的是**人流 / 什么时段**，归常识门，不许被实时门抢走。"""
    assert detect_realtime_intent(message, deps.settings, {}) is None, message


@pytest.mark.parametrize(
    "message",
    ["故宮の入場時間は何時ですか", "자금성 개방 시간은 몇 시예요"],
)
def test_ja_ko_opening_hours_never_fall_back_to_clarify(client, message):
    """端到端：不得落到澄清器（四连问）。"""
    body = post_plan(
        client, message, session_id="foreign-open-" + str(abs(hash(message)) % 9999)
    )
    assert body["type"] == "realtime", f"{message} 落到了 {body.get('type')}：{body}"


def test_japanese_realtime_subject_drops_the_trailing_particle(deps):
    """日文助词不是主体的一部分：「故宮の入場時間」→ 主体「故宮」，不是「故宮の」。

    真 bug（2026-10-09 多语言对话探针实测）：卡片标题曾写成「关于「故宮の」」。
    """
    for message, want in (
        ("故宮の入場時間は何時ですか", "故宮"),
        ("故宮は何時に開きますか", "故宮"),
    ):
        intent = detect_realtime_intent(message, deps.settings, {})
        assert intent is not None, message
        assert intent.subject == want, f"{message} 的主体是 {intent.subject!r}"
