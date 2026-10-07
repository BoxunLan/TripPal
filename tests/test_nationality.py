"""用户来源国（`nationality`）：抽取、表单直填、以及进生成提示词做中外类比。

与 `destination_country` 分开测，因为把两者搞混会让检索拿到**出境**知识
（历史包袱：见 slots.py 的 `_INBOUND_CUE` 注释）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.graph import _visitor_nationality
from app.intent import KnowledgeIntent
from app.knowledge import build_answer_prompt, culture_food_topic
from app.main import create_app
from app.prompts import build_generate_prompt
from app.schemas import RetrievedContext
from app.slots import extract_slots, slots_from_form

# (原话, 期望来源国)。**认不出来的必须认不出来** —— 猜错会让整篇类比跑偏，
# 所以负面用例和正面用例一样重要。
POSITIVE = [
    ("我是德国人，第一次来中国，怎么点菜？", "德国"),
    ("I am American and I want to visit Beijing", "美国"),
    ("I'm from France, is it safe at night?", "法国"),
    ("I am from the United States", "美国"),
    ("我来自韩国，想在北京玩三天", "韩国"),
    ("we are Canadian, travelling with kids", "加拿大"),
    ("as a Spanish traveller, how do I pay?", "西班牙"),
    ("coming from Italy, do I need a visa?", "意大利"),
    ("我是日本人，想去看长城", "日本"),
]

NEGATIVE = [
    "我是上海人，想去成都吃火锅",          # 本国城市名 ≠ 国籍
    "北京离上海有多远",                    # 没有任何身份措辞
    "帮我排一个上海 3 天行程，预算 5000",
    "from Beijing to Shanghai, how long?",  # from 后面接的是城市，不是国家
    "我想去泰国玩 5 天",                    # 目的地国 ≠ 来源国
]


def test_nationality_extracted_from_identity_phrasing(settings):
    for msg, expect in POSITIVE:
        got = extract_slots(msg, settings).get("nationality")
        assert got == expect, f"{msg!r} → {got!r}，期望 {expect!r}"


def test_no_false_positive_when_user_never_says_where_they_are_from(settings):
    for msg in NEGATIVE:
        got = extract_slots(msg, settings).get("nationality")
        assert got is None, f"{msg!r} 不该抽出来源国，却得到 {got!r}"


def test_nationality_is_not_destination_country(settings):
    """「我是美国人，想去北京玩」：来源国是美国，**目的地国是中国**。

    这正是历史漏点的形状 —— 一旦 destination_country 落成美国，检索就会去捞
    美国出境签证知识，答一个没人问过的问题。
    """
    slots = extract_slots("我是美国人，想去北京玩 5 天，需要签证吗？", settings)
    assert slots.get("nationality") == "美国"
    assert slots.get("destination") == "北京"
    assert slots.get("destination_country") == "中国"


def test_form_field_is_normalised_through_the_same_table(settings):
    out = slots_from_form({"nationality": "United States"}, settings)
    assert out["nationality"] == "美国"
    # 原话留着回显：表单里写的「United States」不该被换成中文再问回去
    assert out["nationality_surface"] == "United States"


def test_unknown_country_written_in_form_is_kept_not_dropped(settings):
    """表单是用户**亲手填**的：表里没有的国名照收，丢掉的代价是「类比全没了」。"""
    out = slots_from_form({"nationality": "哈萨克斯坦"}, settings)
    assert out["nationality"] == "哈萨克斯坦"


def _prompt_with(settings, **slots):
    return build_generate_prompt(
        settings=settings,
        scenes=["family"],
        primary_scene="family",
        rewritten_query="带着孩子来北京玩三天",
        slots=slots,
        context=RetrievedContext(
            query="q", filters={}, chunks=[], layer_quota={}, dropped_stale=0, retriever_version="t"
        ),
        tool_results=[],
        guardrail_ids=[],
    )


def test_nationality_reaches_the_generate_prompt(settings):
    prompt = _prompt_with(settings, nationality="德国", destination="北京")
    assert '"nationality": "德国"' in prompt, "来源国必须随 slots_json 进提示词"


def test_prompt_carries_the_analogy_rule_with_its_guardrail(settings):
    """类比规则必须**自带刹车**：允许类比、禁止编造对方国家的具体规定。"""
    prompt = _prompt_with(settings, nationality="德国")
    assert "中外类比" in prompt
    assert "绝不编造对方国家" in prompt
    assert "缺失时**不做类比" in prompt


# ---------------------------------------------------------------- 常识旁路（文化 / 饮食）
# 文化、美食类问题走的**不是**行程生成，而是常识旁路（`app/knowledge.py` + `prompts/answer.md`）。
# 那条路上「按来源国做类比」原来完全没接：既没有来源国输入、也没有类比规则 ——
# 而它恰恰是最该类比的对话（「这里要给小费吗」「川菜是不是都很辣」）。
# 素材来源也放宽了：**类比属常识，不必来自知识库**（库里没有对应条目时照样比）。

KI_SUBJECT = "小费"


def _answer_prompt(settings, message, nationality="", subject=KI_SUBJECT):
    return build_answer_prompt(
        settings=settings,
        intent=KnowledgeIntent(subject=subject, matched=subject, question_zh=subject),
        hits=[],
        message=message,
        nationality=nationality,
    )


def test_answer_prompt_carries_the_visitor_nationality(settings):
    prompt = _answer_prompt(settings, "那去餐厅要给小费吗", nationality="德国")
    assert "德国" in prompt
    assert "中外类比" in prompt
    assert "素材可以来自你的常识" in prompt, (
        "素材来源必须写明可来自常识 —— 否则模型会因为「库里没有对应条目」而放弃类比，"
        "这正是本条需求的由来"
    )


def test_answer_prompt_forbids_analogy_when_nationality_is_unknown(settings):
    """未知来源国时，整条类比规则必须**被换掉**，不能只是末尾加一句禁止。

    真模型实测（2026-10-07）：规则里留着类比教程、只加一句禁令时，模型照样写
    「与你在**欧美国家**通常按 15%–20% 另付小费的习惯不同」—— 规则存在即许可。
    """
    prompt = _answer_prompt(settings, "那去餐厅要给小费吗", nationality="")
    assert "本问不做中外类比" in prompt
    assert "素材可以来自你的常识" not in prompt, "未知来源国时不能把类比教程留在提示词里"


def test_culture_food_topic_makes_the_analogy_mandatory(settings):
    hot = _answer_prompt(settings, "川菜是不是都很辣？", nationality="德国", subject="川菜")
    mild = _answer_prompt(settings, "西湖有多大？", nationality="德国", subject="西湖")
    assert "文化 / 饮食 / 生活习俗类" in hot
    assert "一般话题" in mild


CULTURE_FOOD = [
    "那去餐厅要给小费吗",
    "川菜是不是都很辣？",
    "What local dishes should I try in Chengdu?",
    "寺庙参观要注意什么",
    "Do people queue in China?",
    "春节有什么习俗",
    "안녕하세요 음식 추천해줘",
]
NOT_CULTURE_FOOD = [
    "西湖有多大？",
    "中国的地铁怎么坐",
    "How much does the Forbidden City ticket cost?",
    "北京离上海有多远",
    "长城有多长",
]


def test_culture_food_topic_is_detected_and_still_narrow():
    """判宽一点可以（多问一句要不要类比），但明显不相干的问句不能命中。"""
    for msg in CULTURE_FOOD:
        assert culture_food_topic(msg), f"{msg!r} 应判为文化/饮食类（要主动类比）"
    for msg in NOT_CULTURE_FOOD:
        assert not culture_food_topic(msg), f"{msg!r} 不该判为文化/饮食类（不强制类比）"


def test_visitor_nationality_prefers_the_current_sentence_then_the_session(settings):
    """来源国优先级与本句槽位一致：本句 > 会话历史；都没有 = 空串 = **禁止类比**。

    空串必须真的落到空串上 —— 它一路传到提示词里就是「不知道」，而不知道时不类比
    （猜错来源国 = 整篇类比跑偏）。
    """
    assert _visitor_nationality({"message": "我是德国人"}, {"nationality": "法国"}, settings) == "德国"
    assert _visitor_nationality({"message": "那里吃饭要给小费吗"}, {"nationality": "法国"}, settings) == "法国"
    assert _visitor_nationality({"message": "x", "slots": {"nationality": "意大利"}}, {}, settings) == "意大利"
    assert _visitor_nationality({"message": "西湖有多大"}, {}, settings) == ""


class _SpyLLM:
    """套在假 LLM 外面，记下「常识作答」那一次调用收到的上下文。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.answer_context: dict | None = None

    def complete_json(self, *, role, prompt, schema, context):
        if context.get("task") == "answer":
            self.answer_context = context
        return self.inner.complete_json(role=role, prompt=prompt, schema=schema, context=context)


def test_knowledge_bypass_carries_and_remembers_the_nationality(deps):
    """说了国籍之后：本句带着它进模型，**后续追问不必重新自我介绍**。

    常识旁路不经过 `clarify_node` 的槽位合并 —— 不在这一层补写，用户就要在每一次
    文化类追问里重报国籍，类比也跟着断掉（真链路回归见 graph.py 的 knowledge_node）。
    """
    spy = _SpyLLM(deps.llm)
    deps.llm = spy
    with TestClient(create_app(deps)) as client:
        first = client.post(
            "/plan",
            json={"session_id": "nat-chain", "message": "我是德国人，在中国餐厅吃饭要给小费吗？"},
        ).json()
        assert first["type"] == "answer"
        assert spy.answer_context is not None
        assert spy.answer_context["nationality"] == "德国"

        # 第二句不提国籍 —— 完全靠会话记住的那一个
        client.post("/plan", json={"session_id": "nat-chain", "message": "那川菜是不是都很辣呢"})
        assert spy.answer_context["nationality"] == "德国"
