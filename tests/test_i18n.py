"""多语言切片：语言判定、四语言的输入抽取、输出语言边界、检索恒中文。

三条边界（改这里之前先读 app/i18n.py 的 docstring）：
1. 检索查询恒中文；2. 输出跟用户语言；3. 开发期文案（findings/日志/断言）恒中文。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import build_deps
from app.embed import HashingEmbedder
from app.fakes import FakeLLM
from app.generate import assemble_checklist
from app.i18n import DEFAULT, EN, JA, KO, ZH, contains, detect_language, t
from app.main import create_app
from app.session import SessionStore
from app.slots import build_clarify_question, extract_slots
from app.store import InMemoryVectorStore

from conftest import FROZEN_TODAY, post_plan  # noqa: E402  (tests 不是包，conftest 按顶层模块导入)

# ---------------------------------------------------------------- 语言判定
@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("带 6 岁孩子去厦门 5 天", ZH),
        ("厦门で5日間、家族で予算8000", JA),  # 汉字 + 假名 → 必须先判假名，否则会被当成中文
        ("샤먼 5일, 가족", KO),
        ("Xiamen 5 days family", EN),
        ("", ZH),  # 空串退回默认
        ("12345", EN),  # 纯数字走拉丁分支
    ],
)
def test_detect_language(text, want):
    assert detect_language(text) == want


def test_contains_is_case_insensitive_for_ascii_only():
    assert contains("Xiamen 5 days", "xiamen")
    assert contains("xiamen", "Xiamen")
    # 中日韩没有大小写，走原样子串
    assert contains("厦门5天", "厦门")
    assert not contains("厦门5天", "厦门府")
    assert not contains("anything", "")


def test_t_falls_back_to_chinese():
    assert t("slot.destination", ZH) == "想去的城市或国家是哪里？"
    assert t("slot.destination", "fr") == t("slot.destination", ZH)  # 未支持语言退回中文
    assert t("no.such.key", EN) == "no.such.key"  # 未知 key 原样返回，不抛异常


# ---------------------------------------------------------------- 槽位抽取（四语言）
def test_slots_english():
    s = extract_slots("Xiamen 5 days, 2 adults, budget 8000", get_settings())
    assert s["destination"] == "厦门"  # 规范名恒中文，检索要用它
    assert s["destination_surface"] == "Xiamen"  # 原话写法只用于回显
    assert s["days"] == 5
    assert s["budget"] == 8000.0
    assert s["party_size"] == 2


def test_slots_english_per_day_budget_is_per_person():
    """「300 per day」按人均每日理解，据此折算总预算（口径与中文「每天 300」一致）。"""
    s = extract_slots("Chiang Mai 4 days, 2 people, 300 per day", get_settings())
    assert s["daily_budget"] == 300.0
    assert s["budget"] == 300.0 * 4 * 2


def test_slots_japanese():
    s = extract_slots("厦门で5日間、家族で予算8000", get_settings())
    assert s["destination"] == "厦门"
    assert s["days"] == 5
    assert s["budget"] == 8000.0
    assert s.get("has_children") is True


def test_slots_korean():
    s = extract_slots("샤먼 5일, 가족, 예산 8000", get_settings())
    assert s["destination"] == "厦门"  # 谚文别名要能命中，否则韩语请求一路追问
    assert s["destination_surface"] == "샤먼"
    assert s["days"] == 5
    assert s["budget"] == 8000.0


def test_slots_korean_couple_is_two_not_three():
    """夫妻不能被兜底分支算成「2 大 1 小」—— 未识别的措辞会掉进那个分支。"""
    s = extract_slots("파리 3일, 부부, 예산 5000", get_settings())
    assert s["party_size"] == 2
    assert not s.get("has_children")


def test_slots_chinese_unchanged():
    """中文路径必须逐字不变（回归基线）。"""
    s = extract_slots("带 6 岁孩子去厦门 5 天，预算 8000", get_settings())
    assert s["destination"] == "厦门"
    assert s["days"] == 5
    assert s["budget"] == 8000.0
    assert s["has_children"] is True
    assert s["child_age"] == 6


def test_numeric_party_is_not_mistaken_for_family():
    """「3 个人」是 3 位成人，不该被当成带娃 —— 兜底分支的假阳性（中英都会踩）。"""
    zh = extract_slots("去厦门玩 3 天，3 个人，预算 6000", get_settings())
    assert zh["party_size"] == 3
    assert not zh.get("has_children")

    en = extract_slots("Xiamen 3 days, 3 people, budget 6000", get_settings())
    assert en["party_size"] == 3
    assert not en.get("has_children")


def test_slots_chinese_couple_is_not_missing_party():
    """「情侣 / 夫妻 / 夫妇 / 蜜月」必须落地 party 槽位。

    漏了不会报错，只会让 clarify 拦下信息已经齐全的请求（目的地+天数+预算+双人都在），
    用户看到的是「还需要确认：一行几个人」—— 这是最容易漏测的一类静默失败。
    """
    for text in (
        "去上海 5 天，情侣，预算 8000",
        "去上海 5 天，夫妻，预算 8000",
        "去上海 5 天，夫妇，预算 8000",
        "去上海 5 天，蜜月，预算 8000",
    ):
        s = extract_slots(text, get_settings())
        assert s["party_size"] == 2, text
        assert s.get("party"), f"{text} 没落 party 槽位（会被 clarify 拦下）"
        assert not s.get("has_children"), text


def test_slots_age_splits_child_adult_elder():
    """抽到的岁数必须落进区间，不能一律当儿童。

    「70 岁老人」曾被判成 `child_age=70` + `has_children=True`，连带把请求推到
    family 场景、加载儿童节奏叠加层（午睡窗口），还在 party 里写出「含 70 岁儿童」。
    """
    child = extract_slots("带 6 岁孩子去厦门 5 天", get_settings())
    assert child.get("has_children") and child.get("child_age") == 6
    assert not child.get("has_elder")

    # 17 岁及以下仍是未成年人；18 岁起按成年人处理
    assert extract_slots("带 16 岁的孩子去厦门 5 天", get_settings()).get("child_age") == 16
    adult = extract_slots("带 20 岁的儿子去厦门 5 天", get_settings())
    assert not adult.get("has_children") and not adult.get("has_elder")

    elder = extract_slots("厦门 5 天，同行有 70 岁老人", get_settings())
    assert elder.get("has_elder")
    assert not elder.get("has_children"), "70 岁不能同时是儿童"
    assert not elder.get("child_age")

    # 只给岁数、不带「老人」二字，也要落到长者
    assert extract_slots("同行有 65 岁的人，去厦门 5 天", get_settings()).get("has_elder")
    # 英文写法同样受区间约束
    en = extract_slots("Xiamen 5 days with my 70-year-old mother", get_settings())
    assert en.get("has_elder") and not en.get("has_children")


def test_slots_solo_phrases_are_one_person():
    """「独自 / 单人」是 1 人，不能掉进「未识别 → 2 大 1 小」的兜底。"""
    for text in ("去上海 5 天，独自，预算 8000", "去上海 5 天，单人，预算 8000"):
        s = extract_slots(text, get_settings())
        assert s["party_size"] == 1, text
        assert not s.get("has_children"), text


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("去上海 5 天，预算 2万", 20000.0),
        ("去上海 5 天，预算 2 万", 20000.0),
        ("去上海 5 天，预算 2万元", 20000.0),
        ("去上海 5 天，预算 2万以内", 20000.0),
        ("去上海 5 天，预算 8千", 8000.0),
        ("Xiamen 5 days, budget 2k", 2000.0),
        ("厦门で5日間、予算20万", 200000.0),
        ("파리 3일, 예산 200만", 2000000.0),
        # 没有量级后缀时不能被放大
        ("去上海 5 天，预算 20000", 20000.0),
        ("去上海 5 天，预算 8000 元", 8000.0),
        # 反例：量级后缀必须紧邻数字，`budget 8000 Xiamen` 里的 K 不是千倍
        ("Kunming 5 days, budget 8000", 8000.0),
        ("去上海 5 天，预算 8000，万豪酒店 3 晚", 8000.0),
    ],
)
def test_budget_magnitude_suffix_is_scaled(text, want):
    """「预算 2 万」不是 2 元 —— 中文口语几乎都这么写，读错会让预算校验整体失真。"""
    assert extract_slots(text, get_settings())["budget"] == want


# ---------------------------------------------------------------- 追问语言
def test_clarify_question_follows_user_language():
    miss = ["date_range", "budget", "party"]
    assert build_clarify_question(miss, {}, language=ZH).startswith("还需要确认")
    assert "Still need:" in build_clarify_question(miss, {}, language=EN)
    assert "もう少し確認" in build_clarify_question(miss, {}, language=JA)
    assert "추가로 확인" in build_clarify_question(miss, {}, language=KO)


def test_clarify_echoes_surface_form_not_normalized_name():
    """英文用户写 Shanghai，追问里就该是 Shanghai，不能蹦出中文「上海」。"""
    slots = {"destination": "上海", "destination_surface": "Shanghai"}
    q = build_clarify_question(["date_range"], slots, language=EN)
    assert "Shanghai" in q and "上海" not in q
    # 没有 surface 时退回规范名（老行为）
    q2 = build_clarify_question(["date_range"], {"destination": "上海"}, language=EN)
    assert "上海" in q2


# ---------------------------------------------------------------- 端到端：输出语言
class _SpyEmbedder(HashingEmbedder):
    """记下送进 embed 的文本 —— 检索 query 从这里过，用来钉死「检索恒中文」。"""

    def __init__(self, dim: int = 1024) -> None:
        super().__init__(dim)
        self.texts: list[str] = []

    def embed(self, texts):
        self.texts.extend(texts)
        return super().embed(texts)


@pytest.fixture
def spy_client():
    settings = get_settings()
    embedder = _SpyEmbedder(settings.embedding.dim)
    deps = build_deps(
        settings,
        embedder=embedder,
        llm=FakeLLM(settings),
        store=InMemoryVectorStore.from_seed_dir(settings.seed_dir, embedder),
        sessions=SessionStore(),
        today=lambda: FROZEN_TODAY,
    )
    with TestClient(create_app(deps)) as c:
        yield c, embedder


_PLAN_EN = "Xiamen 5 days, family with a kid, budget 8000"
_PLAN_ZH = "带 6 岁孩子去厦门 5 天，预算 8000"


def test_english_request_routes_to_english_output(spy_client):
    client, _ = spy_client
    body = post_plan(client, _PLAN_EN, session_id="i18n-en")
    assert body["type"] == "plan"
    assert body["route"]["output_language"] == "en"


def test_chinese_request_stays_chinese(spy_client):
    client, _ = spy_client
    body = post_plan(client, _PLAN_ZH, session_id="i18n-zh")
    assert body["type"] == "plan"
    assert body["route"]["output_language"] == ZH == DEFAULT


@pytest.mark.parametrize(
    ("sid", "message", "want"),
    [
        ("i18n-ja", "厦门で5日間、家族で予算8000", JA),
        ("i18n-ko", "샤먼 5일, 가족, 예산 8000", KO),
    ],
)
def test_japanese_korean_requests_route_and_language(spy_client, sid, message, want):
    client, _ = spy_client
    body = post_plan(client, message, session_id=sid)
    assert body["type"] == "plan", "日/韩请求不该退化成追问"
    assert body["route"]["output_language"] == want


@pytest.mark.parametrize("message", [_PLAN_EN, "厦门で5日間、家族で予算8000", "샤먼 5일, 가족, 예산 8000"])
def test_retrieval_query_is_always_chinese(spy_client, message):
    """知识库是中文语料 —— 英文/日文/韩文请求的检索 query 也必须落在中文上。"""
    client, embedder = spy_client
    embedder.texts.clear()
    post_plan(client, message, session_id=f"q-{detect_language(message)}")
    queries = [x for x in embedder.texts if x]
    assert queries, "应当发生检索"
    assert any(any("\u4e00" <= ch <= "\u9fff" for ch in q) for q in queries), queries


def test_checklist_labels_follow_language_but_source_text_does_not(spy_client):
    """清单标签跟用户语言；引用里的来源原文保持原样（翻译会断链）。"""
    client, _ = spy_client
    en = post_plan(client, _PLAN_EN, session_id="cl-en-2")
    zh = post_plan(client, _PLAN_ZH, session_id="cl-zh-2")
    assert en["checklist"] and zh["checklist"]
    assert any("source" in item for item in en["checklist"])
    assert any("来源" in item for item in zh["checklist"])


def test_visa_checklist_labels_follow_language():
    """签证清单的「来源/生效日」标签也跟语言（这两行是确定性拼的，不走模型）。"""
    from types import SimpleNamespace

    route = _route_with_language("en", ["visa"])
    items = assemble_checklist(
        settings=get_settings(),
        route=route,
        context=SimpleNamespace(chunks=[]),
        chunk_index={},
        tool_results={
            "visa_policy": SimpleNamespace(
                ok=True,
                payload={
                    "materials": [
                        {
                            "name": "护照",
                            "requirement": "有效期 6 个月以上",
                            "citation_key": "v-1",
                            "source_url": "https://example.invalid/a",
                            "effective_date": "2026-01-01",
                        }
                    ]
                },
            )
        },
        slots={},
    )
    joined = " ".join(items)
    assert "source" in joined and "effective" in joined
    assert "来源" not in joined
    # 中文路径不受影响
    items_zh = assemble_checklist(
        settings=get_settings(),
        route=_route_with_language(ZH, ["visa"]),
        context=SimpleNamespace(chunks=[]),
        chunk_index={},
        tool_results={
            "visa_policy": SimpleNamespace(
                ok=True,
                payload={"materials": [{"name": "护照", "requirement": "有效期 6 个月以上"}]},
            )
        },
        slots={},
    )
    assert "来源" in items_zh[0] and "生效日" in items_zh[0]


def _route_with_language(language: str, scenes: list[str]):
    from app.router import build_route
    from app.schemas import SceneClassificationResult, SceneLabel

    settings = get_settings()
    cls = SceneClassificationResult(
        request_id="r",
        labels=[SceneLabel(scene_id=s, confidence=0.9) for s in scenes],
        primary_scene=scenes[0],
        slots={},
        missing_slots=[],
        clarify_question=None,
        rewritten_query="泰国 签证 材料",
        classifier_version=settings.classifier_version,
    )
    return build_route(settings=settings, classification=cls, language=language)


# ---------------------------------------------------------------- 词典与规则
def test_every_destination_has_aliases():
    table = get_settings().routes.get("destinations") or {}
    assert table
    for name, meta in table.items():
        assert meta.get("aliases"), f"{name} 缺 aliases"


def test_korean_and_japanese_aliases_present_for_core_destinations():
    table = get_settings().routes["destinations"]
    assert "샤먼" in table["厦门"]["aliases"]
    assert "アモイ" in table["厦门"]["aliases"]  # 日文片假名别名，缺了日语请求会一路追问
    assert "서울" in table["首尔"]["aliases"]
    assert "蘇州" in table["苏州"]["aliases"]  # 繁体/日文字形与简体不同，必须显式列


def test_slot_patterns_cover_four_languages():
    pats = get_settings().slot_patterns
    joined = "\n".join(pats["date_range"] + pats["budget"] + pats["party"])
    for token in ("days?", "予算", "예산", "泊", "박", "夫婦", "부부", "情侣", "独自", "万"):
        assert token in joined, f"槽位规则缺 {token}"
    # 「만 / 千」是 slots._BUDGET_UNITS 的换算后缀（韩文「万」与中文「千」），
    # 不需要进 YAML 正则，但必须真在换算表里 —— 否则 200만 会被读成 200。
    from app.slots import _BUDGET_UNITS

    assert {"万", "萬", "千", "만"} <= set(_BUDGET_UNITS)


def test_scene_keywords_cover_four_languages():
    settings = get_settings()
    for sid in ("family", "budget", "roadtrip", "visa"):
        kws = settings.scene_cfg(sid).get("keywords") or []
        assert any(any("\u3040" <= ch <= "\u30ff" for ch in k) for k in kws), f"{sid} 缺日文关键词"
        assert any(any("\uac00" <= ch <= "\ud7af" for ch in k) for k in kws), f"{sid} 缺韩文关键词"


def test_output_language_defaults_for_legacy_route_config():
    """老配置不写 output_language 时必须是中文 —— 不得因多语言改造把默认值改掉。"""
    from app.schemas import RouteConfig

    assert RouteConfig.model_fields["output_language"].default == ZH


# ---------------------------------------------------------------- guardrail 文案
def test_guardrail_text_follows_language_without_duplicating_chinese():
    """guardrail 正文会进用户可见的 disclaimers → 非中文要有译文。
    中文仍以 routes.yaml 为唯一真源（i18n 里只放译文，避免两处中文分叉）。"""
    from app.i18n import guardrail_text

    settings = get_settings()
    zh = guardrail_text(settings, "safety_disclaimer", ZH)
    assert zh == settings.guardrail_text("safety_disclaimer")
    for lang in (EN, JA, KO):
        got = guardrail_text(settings, "safety_disclaimer", lang)
        assert got and got != zh, f"{lang} 缺 safety_disclaimer 译文"
    # 未知 id 退回配置值，不抛异常
    assert guardrail_text(settings, "no_such_guardrail", EN) == settings.guardrail_text("no_such_guardrail")


def test_real_itinerary_disclaimers_are_localized(client):
    """端到端：英文请求的 disclaimers 里不该混进中文 guardrail 正文。"""
    body = post_plan(client, "Xiamen 5 days, 2 adults, budget 8000", session_id="gr-en")
    assert body["type"] == "plan"
    disclaimer_text = "\n".join(body["itinerary"]["disclaimers"])
    assert "行程为参考建议" not in disclaimer_text
    assert "This itinerary is a suggestion" in disclaimer_text


def test_complete_chinese_couple_request_is_not_interrupted(client):
    """端到端回归：信息齐全的中文请求必须一路走到 plan，不能被 clarify 拦下。

    「情侣」曾经同时漏在 party 正则与词表里，结果是目的地/天数/预算/人数全给了，
    仍被追问「一行几个人」。
    """
    body = post_plan(client, "去上海 5 天，情侣，预算 2万，想去外滩", session_id="couple-e2e")
    assert body["type"] == "plan", body
    assert body["itinerary"]["budget"]["user_budget"] == 20000.0


# ---------------------------------------------------------------- 强制输出语言
def test_forced_output_language_overrides_detection(spy_client):
    """英文输入 + output_language=zh → 输出走中文，方便开发时直接读。"""
    client, _ = spy_client
    resp = client.post(
        "/plan",
        json={"session_id": "force-zh", "message": _PLAN_EN, "output_language": "zh"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "plan"
    assert body["route"]["output_language"] == ZH
    # 中文模式下不加语言段，且清单标签回到中文
    assert any("来源" in item for item in body["checklist"])


def test_forced_output_language_applies_to_clarify(spy_client):
    """强制语言也要作用在追问上（追问是用户可见文案）。"""
    client, _ = spy_client
    resp = client.post(
        "/plan",
        json={"session_id": "force-clarify", "message": "Xiamen", "output_language": "zh"},
    )
    body = resp.json()
    assert body["type"] == "clarify"
    assert "还需要确认" in body["question"] and "Still need:" not in body["question"]


def test_forced_output_language_does_not_change_retrieval_language(spy_client):
    """强制中文只是输出语言；反过来强制 en 时，检索查询也不能变成英文。"""
    client, embedder = spy_client
    embedder.texts.clear()
    client.post(
        "/plan",
        json={"session_id": "force-en", "message": _PLAN_ZH, "output_language": "en"},
    )
    queries = [x for x in embedder.texts if x]
    assert queries
    assert any(any("\u4e00" <= ch <= "\u9fff" for ch in q) for q in queries)


def test_unsupported_output_language_is_rejected(spy_client):
    client, _ = spy_client
    resp = client.post(
        "/plan",
        json={"session_id": "force-bad", "message": _PLAN_ZH, "output_language": "fr"},
    )
    assert resp.status_code == 422


def test_language_directive_is_the_last_section():
    """语言段必须在场景叠加层**之后**：叠加层全是中文，夹在中间会把模型带回中文。"""
    from app.i18n import output_language_directive
    from app.prompts import build_generate_prompt
    from app.schemas import RetrievedContext

    settings = get_settings()
    route = _route_with_language(EN, ["family"])
    prompt = build_generate_prompt(
        settings=settings,
        scenes=route.scenes,
        primary_scene=route.scenes[0],
        rewritten_query="厦门 5 天亲子游",
        slots={"destination": "厦门", "days": 5},
        context=RetrievedContext(query="q", retriever_version="retr-test"),
        tool_results=[],
        guardrail_ids=route.guardrail_ids,
        output_language=EN,
    )
    directive = output_language_directive(EN)
    assert directive in prompt
    assert prompt.rstrip().endswith(directive), "语言段后面不该还有中文叠加层"
    # safety.md 的 {{guardrail_disclaimer}} 会被模型原样抄进 disclaimers → 必须是译文
    assert "行程为参考建议" not in prompt
    assert "This itinerary is a suggestion" in prompt
    # 中文路径不加语言段
    prompt_zh = build_generate_prompt(
        settings=settings,
        scenes=route.scenes,
        primary_scene=route.scenes[0],
        rewritten_query="厦门 5 天亲子游",
        slots={"destination": "厦门", "days": 5},
        context=RetrievedContext(query="q", retriever_version="retr-test"),
        tool_results=[],
        guardrail_ids=route.guardrail_ids,
        output_language=ZH,
    )
    assert "# 输出语言" not in prompt_zh


# ---------------------------------------------------------------- 槽位回归（六处静默缺陷）
def test_chinese_numeral_party_size():
    """「我们三个人」是最口语的说法，必须抽到 3 —— 旧正则只认阿拉伯数字。"""
    s = extract_slots("我们三个人去厦门 5 天，预算 8000", get_settings())
    assert s["party_size"] == 3
    assert s["party"] == "3 位成人"
    assert not s.get("has_children"), "纯人数不该被当成亲子"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("两个人去厦门 5 天", 2),
        ("四个人去厦门 5 天", 4),
        ("我们十五个人去厦门 5 天", 15),
        ("二十人去厦门 5 天", 20),
        ("三口之家去厦门 5 天", 3),
        ("五口人去厦门 5 天", 5),
        ("厦门 5 天，一个人去", 1),
    ],
)
def test_party_size_from_chinese_numerals(text, want):
    assert extract_slots(text, get_settings())["party_size"] == want


def test_unknown_party_is_left_missing_not_defaulted_to_two():
    """没识别出人数时不能编一个 2：那会让预算按错误人数折算，也不再追问人数。"""
    for text in ("和朋友一起去厦门 5 天，预算 8000", "和家人一起去厦门 5 天，预算 8000"):
        s = extract_slots(text, get_settings())
        assert "party_size" not in s, text
        assert "party" not in s, text


def test_daily_budget_falls_back_to_two_without_writing_party_size():
    """折算可以用兜底人数，但槽位必须保持「未知」—— 否则 clarify 永远不追问人数。"""
    s = extract_slots("去厦门 5 天，每天 300", get_settings())
    assert s["budget"] == 300 * 5 * 2
    assert "party_size" not in s


def test_elderly_age_does_not_hit_family_scene():
    """单字「岁」会子串命中 family，「70 岁老人」于是加载儿童节奏叠加层（午睡窗口）。"""
    from app.slots import scene_hints

    settings = get_settings()
    assert "family" not in scene_hints("同行有 70 岁老人，去厦门 5 天，预算 1 万", settings)
    # 真正带娃的句子仍必须命中
    assert "family" in scene_hints("带 6 岁孩子去厦门 5 天，预算 8000", settings)
    assert "family" in scene_hints("xiamen trip with kids 5 days, budget 8000", settings)


def test_mentioning_budget_alone_is_not_a_budget_trip():
    """「预算 8000」是任何请求都会写的字段，不等于穷游意图（旧关键词裸写了 budget）。"""
    from app.slots import scene_hints

    settings = get_settings()
    assert "budget" not in scene_hints("Kunming 5 days, budget 8000", settings)
    assert "budget" not in scene_hints("厦门 5 天，预算 8000，想去四川省", settings)
    # 真表达穷游意图的仍要命中
    assert "budget" in scene_hints("厦门 5 天，想省钱，预算 8000", settings)
    assert "budget" in scene_hints("Xiamen on a budget, 5 days", settings)


def test_china_is_origin_not_destination():
    """「哪些国家对中国免签」里的中国是签发护照的国家，不是要去的地方。"""
    from app.slots import resolve_destinations

    settings = get_settings()
    dest, _, names, _ = resolve_destinations("哪些国家对中国免签", settings)
    assert dest is None and "中国" not in names
    # 中国仍可作为国内城市的所属国
    assert extract_slots("去厦门 3 天，预算 6000", settings)["destination_country"] == "中国"
    # 「从中国出发去泰国」的目的地是泰国
    assert resolve_destinations("从中国出发去泰国 5 天，预算 8000", settings)[0] == "泰国"


def test_origin_only_flag_is_only_used_on_countries():
    """origin_only 是「出发地」标记，只对国家级条目有意义。"""
    table = get_settings().routes["destinations"]
    flagged = [n for n, m in table.items() if m.get("origin_only")]
    assert flagged, "中国应当带 origin_only 标记"
    for name in flagged:
        assert table[name].get("type") == "country", name


def test_clarify_does_not_reask_about_companions_when_elder_is_known():
    """已经说了有长者同行，追问就不能再问「有小孩或长者同行吗」—— 那是明知故问。"""
    slots = {"destination": "厦门", "days": 5, "has_elder": True}
    q = build_clarify_question(["party"], slots, language=ZH)
    assert "一行几个人" in q
    assert "长者同行吗" not in q
    # 完全不知道构成时仍问完整问题
    q2 = build_clarify_question(["party"], {"destination": "厦门"}, language=ZH)
    assert "有小孩或长者同行吗" in q2
    # 英文同理
    q3 = build_clarify_question(["party"], {"has_children": True}, language=EN)
    assert "How many travellers will there be?" in q3
    assert "children or seniors" not in q3


# ---------------------------------------------------------------- 中文口语槽位（P0：漏抽 → 反问用户刚说过的）
@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("厦门三天，预算 2000", 3),
        ("厦门七日游，预算 8000", 7),
        ("厦门三天两晚，预算 2000", 3),        # 「N 天」优先，晚数不覆盖
        ("厦门两晚，预算 2000", 3),            # 两晚 = 3 天
        ("去厦门玩一周，预算 8000", 7),        # 周=7 天，不是 1 天
        ("去三亚两个星期，预算 30000", 14),
        ("去厦门二十五天，预算 20000", 25),     # 复合数词
        ("厦门五日間、家族で、予算8000", 5),    # 日文的「日間」也要认中文数词
    ],
)
def test_chinese_numeral_day_counts(text, want):
    """「三天 / 七日游 / 一周」是中文里最常见的写法，只认阿拉伯数字会把天数整条漏掉。"""
    assert extract_slots(text, get_settings())["days"] == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("预算两万去厦门 5 天", 20000.0),
        ("预算五千去西安三天", 5000.0),
        ("预算 三百，厦门 5 天", 300.0),
        ("预算三百以内，厦门 5 天", 300.0),
        ("预算下限 8000，厦门 3 天", 8000.0),
        ("预算约 8000，厦门 3 天", 8000.0),
        ("每天 三百，厦门 5 天", 300.0 * 5 * 2),  # 人均每日预算的中文写法
    ],
)
def test_chinese_numeral_budget(text, want):
    """「预算两万」必须能进算术 —— 捕获组是中文数词时直接 float() 会抛错被静默跳过。"""
    assert extract_slots(text, get_settings())["budget"] == want


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("三百万", 3000000.0),   # 复合量级：只抓「三」再乘一次 100 会读成 300
        ("一万五", 15000.0),     # 省略式：末尾裸数词取最近单位的 1/10
        ("两千五百", 2500.0),
        ("三百五", 350.0),
        ("一百零五", 105.0),     # 「零」之后不能按省略式读
        ("百五", 150.0),
        ("万豪", None),          # 含不认识的字符 → 宁可不填也不猜
        ("百", None),            # 裸量级字符不成金额
    ],
)
def test_compound_chinese_amount(raw, want):
    from app.slots import _cn_amount

    assert _cn_amount(raw) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("预算三百万去厦门 5 天", 3000000.0),
        ("预算一万五去厦门 5 天", 15000.0),
    ],
)
def test_compound_amount_flows_into_slot(text, want):
    """复合量级必须一路走到槽位：读错一位就会把「超没超预算」判定反过来。"""
    assert extract_slots(text, get_settings())["budget"] == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("厦门 5 天，2 大 1 小，预算 8000", 3),
        ("3 大 1 小去北京 5 天，预算 10000", 4),
        ("两大两小去三亚 6 天，预算 20000", 4),
    ],
)
def test_adult_child_combo_party_size(text, want):
    """「N 大 M 小」没有「人」字，旧规则落不进人数分支 → 默认 2 大 1 小，「3 大 1 小」被读成 3 人。"""
    s = extract_slots(text, get_settings())
    assert s["party_size"] == want
    assert s["has_children"]


@pytest.mark.parametrize("text", ["全家去厦门 5 天，预算 8000", "厦门五日家庭游，预算 8000", "亲子游去厦门 3 天"])
def test_family_phrases_are_recognised(text):
    """「全家 / 家庭游 / 亲子游」是家庭出行，不能被当成「未识别」而反问人数。"""
    s = extract_slots(text, get_settings())
    assert s["party_size"] == 3
    assert s["has_children"]


def test_full_date_does_not_swallow_day_count():
    """整日期不能提前返回：「2026年10月25日去厦门 3 天」里显式写的 3 天必须留得住。"""
    s = extract_slots("2026年10月25日去厦门 3 天，预算 6000", get_settings())
    assert s["days"] == 3
    assert s["start_date"] == "2026-10-25"


def test_month_day_is_not_a_duration():
    """「12月25日」是日期不是时长；「N 日」规则必须排除它，否则会被读成 25 天行程。"""
    s = extract_slots("12月25日去厦门，预算 6000", get_settings())
    assert "days" not in s


def test_weekend_is_not_a_duration():
    """「周末」没有时长含义，不能被「N 周」规则吃掉。"""
    s = extract_slots("这周末去厦门，预算 2000", get_settings())
    assert "days" not in s


@pytest.mark.parametrize(
    ("text", "dest"),
    [
        ("重庆 4 天，预算 3000，两个人", "重庆"),
        ("上海 3 天，预算 8000", "上海"),
        ("深圳 5 天，预算 12000", "深圳"),
        ("西安 4 天，预算 6000", "西安"),
        ("广州 3 天，预算 5000", "广州"),
        ("成都 4 天，预算 6000", "成都"),
    ],
)
def test_mainland_cities_are_resolvable(text, dest):
    """大陆主力城市必须在词表里：用户写出城市名却被反问「想去的城市是哪里」是最伤的体验。"""
    s = extract_slots(text, get_settings())
    assert s["destination"] == dest
    assert s["destination_country"] == "中国"


@pytest.mark.parametrize(
    ("text", "dest"),
    [
        ("兵马俑怎么去", "西安"),
        ("外滩附近住哪里", "上海"),
        ("布达拉宫要预约吗", "拉萨"),
        ("莫高窟什么时候开放", "敦煌"),
    ],
)
def test_china_landmarks_map_to_their_city(text, dest):
    """地标别名必须挂到城市上（同北京「天安门」的做法），否则一个地点都抽不出来。"""
    from app.slots import resolve_destinations

    assert resolve_destinations(text, get_settings())[0] == dest


def test_every_china_city_carries_a_country():
    """新增城市必须带 country=中国，否则 visa/检索预过滤会拿到空国家。"""
    table = get_settings().routes["destinations"]
    for name, meta in table.items():
        if meta.get("type") in {"city", "region"} and name in {
            "上海", "重庆", "西安", "广州", "深圳", "南京", "苏州", "武汉", "长沙", "天津",
            "昆明", "桂林", "三亚", "哈尔滨", "拉萨", "乌鲁木齐", "张家界", "敦煌", "黄山",
            "洛阳", "大同", "贵阳", "呼和浩特", "九寨沟",
        }:
            assert meta.get("country") == "中国", name
