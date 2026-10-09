"""建议栏改成**模型为主、模板兜底**（2026-10-09 用户反馈「改的自由点儿，别死板」）。

「接着可以问」(`next_questions`) 与「试着这样问」(`starters`) 原先各自是一份**固定文案**，
连问几轮一字不差。现在改为：**让本来就在调的那一次模型调用顺手写**（plan / answer / guide），
本模块的 i18n 模板退居兜底。三件事各自要有测试：

1. `clean_chips`：模型自由发挥必须过这道闸 —— 去空 / 去重 / 去复读用户原话 / 限长限量。
2. schema 容错：模型把数组写成字符串 / null 时**不许把整篇判废**（建议栏不该有否决权）。
3. 端到端：模型写了建议就用模型的（且洗净）；没写 / 全是复读就退回模板，**绝不空栏**。
4. 「试着这样问」的**兜底**也轮换：两套 i18n 池子按会话轮次滑窗，连点两次「你好」不再一字不差。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.fakes import FakeLLM
from app.followups import clean_chips, next_questions
from app.generate import _coerce_flattened
from app.i18n import DEFAULT as ZH
from app.i18n import t
from app.guide import starters as i18n_starters
from app.main import create_app
from app.schemas import AnswerDraft, GeneratorOutput, GuideDraft

FORM = {"destination": "上海", "days": 4, "adults": 2, "budget": 8000}


def _post(client, message: str, sid: str, overrides: dict | None = None) -> dict:
    payload: dict = {"session_id": sid, "message": message}
    if overrides:
        payload["slot_overrides"] = overrides
    resp = client.post("/plan", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _patched_plan(monkeypatch, followups):
    orig = FakeLLM._plan

    def patched(self, ctx):
        out = orig(self, ctx)
        out["followups"] = list(followups)
        return out

    monkeypatch.setattr(FakeLLM, "_plan", patched)


def _patched_answer(monkeypatch, followups):
    orig = FakeLLM._answer

    def patched(self, ctx):
        out = orig(self, ctx)
        out["followups"] = list(followups)
        return out

    monkeypatch.setattr(FakeLLM, "_answer", patched)


def _patched_guide(monkeypatch, starters):
    orig = FakeLLM._guide

    def patched(self, ctx):
        out = orig(self, ctx)
        out["starters"] = list(starters)
        return out

    monkeypatch.setattr(FakeLLM, "_guide", patched)


# ------------------------------------------------------------------ 1. clean_chips
def test_clean_chips_drops_empty_overlong_and_newlines():
    out = clean_chips(["", "   ", "正常一条", "x" * 80, "带\n换行"])
    assert out == ["正常一条"], out


def test_clean_chips_dedupes():
    assert clean_chips(["同一条", "同一条", " 同一条 ", "另一条"]) == ["同一条", "另一条"]


def test_clean_chips_drops_the_echo_even_when_one_char_differs():
    """用户打「住宿换便宜一点的」、建议回「住宿换成便宜一点的」—— 差一个字也得认出来。

    这是命门：抓不住就等于「点完还看到同一条」，用户以为点了没反应。
    """
    out = clean_chips(
        ["住宿换成便宜一点的", "换成亲子向的行程"],
        avoid=["住宿换便宜一点的"],
    )
    assert out == ["换成亲子向的行程"], out


def test_clean_chips_keeps_unrelated_short_suggestions():
    """`avoid` 是短问候时不许误伤：不能因为原话短就把别的建议当复读砍掉。"""
    out = clean_chips(["按这个帮我排进行程", "附近还有什么"], avoid=["你好"])
    assert len(out) == 2, out


def test_clean_chips_respects_limit():
    assert clean_chips(list("abcdef")) == ["a", "b", "c"]


# ------------------------------------------------------------------ 2. schema 容错
def test_drafts_coerce_a_bare_string_instead_of_exploding():
    assert GeneratorOutput.model_validate(
        {"itinerary": {"title": "t"}, "followups": "一条问题"}
    ).followups == ["一条问题"]
    assert AnswerDraft(followups="回答后的追问").followups == ["回答后的追问"]
    assert GuideDraft(starters="示例提问").starters == ["示例提问"]


def test_drafts_tolerate_null_and_junk():
    assert AnswerDraft(followups=None).followups == []
    assert GuideDraft(starters=123).starters == []
    assert GeneratorOutput.model_validate(
        {"itinerary": {"title": "t"}, "followups": []}
    ).followups == []


def test_flattened_generation_keeps_followups():
    """模型把 Itinerary 字段摊在顶层时，`suggestions` 与 `followups` 都要原样带过去。"""
    out = _coerce_flattened({"title": "上海 4 天", "suggestions": ["s1"], "followups": ["f1", "f2"]})
    assert out is not None
    assert out.suggestions == ["s1"]
    assert out.followups == ["f1", "f2"]


# ------------------------------------------------------------------ 3. 端到端
def test_plan_uses_the_model_written_followups(monkeypatch, deps):
    """模型写了就用模型的 —— 这是「自由」的主路。"""
    chips = ["上海的夜市一般几点收摊", "帮我把住宿换成青旅", "再加一天去周边古镇"]
    _patched_plan(monkeypatch, chips)
    with TestClient(create_app(deps)) as client:
        body = _post(client, "帮我排个上海4天的行程", "chips-plan", FORM)
    assert body["type"] == "plan", body
    assert body["next_questions"] == chips, body["next_questions"]


def test_plan_falls_back_to_templates_when_model_is_silent(monkeypatch, deps):
    """模型没给 → 退回模板，建议栏不许空。"""
    _patched_plan(monkeypatch, [])
    with TestClient(create_app(deps)) as client:
        body = _post(client, "帮我排个上海4天的行程", "chips-fallback", FORM)
    assert body["type"] == "plan", body
    assert body["next_questions"], body
    # 轮换窗口随轮次变，所以不钉死具体那一条，只断言「确实来自 plan 的模板池」。
    pool: set[str] = set()
    for i in range(8):
        pool.update(
            next_questions(decision="plan", slots={"destination": "上海", "days": 4}, offset=i)
        )
    assert set(body["next_questions"]) <= pool, (body["next_questions"], pool)


def test_plan_falls_back_when_every_model_chip_is_an_echo(monkeypatch, deps):
    """模型只会复读用户那句 → 洗完为空 → 退回模板（而不是把一个复读推给用户）。"""
    _patched_plan(monkeypatch, ["帮我排个上海4天的行程"])
    with TestClient(create_app(deps)) as client:
        body = _post(client, "帮我排个上海4天的行程", "chips-echo", FORM)
    assert body["type"] == "plan", body
    assert body["next_questions"], body
    assert "帮我排个上海4天的行程" not in body["next_questions"], body


def test_answer_uses_the_model_written_followups(monkeypatch, deps):
    _patched_answer(monkeypatch, ["那我可以提前多久绑定", "绑定要手续费吗"])
    with TestClient(create_app(deps)) as client:
        body = _post(client, "外国人能用外卡支付吗", "chips-answer")
    assert body["type"] == "answer", body
    assert body["next_questions"] == ["那我可以提前多久绑定", "绑定要手续费吗"], body


def test_guide_meta_uses_the_model_written_starters(monkeypatch, deps):
    """`meta`（你是谁 / 能做什么）是唯一走模型的引导类 —— 示例提问跟着它走。"""
    starters = ["外国人怎么用支付宝", "上海 4 天怎么安排", "高铁票怎么买"]
    _patched_guide(monkeypatch, starters)
    with TestClient(create_app(deps)) as client:
        body = _post(client, "你能做什么", "chips-guide-meta")
    assert body["type"] == "guide", body
    assert body["kind"] == "meta", body
    assert body["starters"] == starters, body


def test_guide_scripted_kinds_keep_the_i18n_starters_as_fallback(client):
    """问候 / 套话这条**不走模型**，示例提问必须仍拿得到 i18n 定稿（兜底没坏）。"""
    body = _post(client, "你好", "chips-guide-hi")
    assert body["type"] == "guide", body
    assert body["starters"] == i18n_starters(ZH), body
    assert body["starters"], body


def test_i18n_starters_are_still_translated_for_every_language():
    """兜底也必须是四语言的 —— 模型挂了的小语种路径不能退化成空栏。"""
    for lang in ("zh", "en", "ja", "ko"):
        raw = t("gd.starters", lang)
        assert raw != "gd.starters", (lang, "缺这条文案")
        assert len([x for x in raw.split("|") if x.strip()]) >= 3, (lang, raw)


# ---------------------------------------------------- 4. 「试着这样问」兜底也要轮换
def _starter_pool(lang: str) -> list[str]:
    pool: list[str] = []
    for key in ("gd.starters", "gd.starters_extra"):
        pool += [x.strip() for x in t(key, lang).split("|") if x.strip()]
    return pool


def test_starter_pool_merges_primary_and_extra():
    """两套池子合起来明显多于 4 条 —— 只有 4 条时「轮换」等于没换。"""
    for lang in ("zh", "en", "ja", "ko"):
        pool = _starter_pool(lang)
        assert len(pool) >= 6, (lang, pool)
        # 池内不重复（重复条目会让轮换窗口看着没变）
        assert len(set(pool)) == len(pool), (lang, pool)


def test_starters_rotate_across_turns():
    """兜底按轮次轮换窗口：连点两次「你好」示例提问不再一字不差。"""
    first = i18n_starters(ZH, 0)
    second = i18n_starters(ZH, 1)
    assert len(first) == 4 and len(second) == 4
    assert first != second, (first, second)
    # 池子有限 → 轮换是循环的：转满一圈回到第一窗
    period = len(_starter_pool(ZH)) - 4 + 1
    assert i18n_starters(ZH, period) == first


def test_starters_rotation_covers_the_whole_pool():
    """轮换窗口滑过整个池子 —— 备用池那几条必须真能被看到，不能只是摆设。"""
    pool = _starter_pool(ZH)
    seen: set[str] = set()
    for off in range(len(pool)):
        seen.update(i18n_starters(ZH, off))
    assert seen == set(pool)


def test_guide_greeting_starters_change_across_turns(client):
    """端到端：同会话连点两次「你好」，示例提问随轮次变化（套话类不走模型，靠轮换）。"""
    first = _post(client, "你好", "chips-rotate")
    second = _post(client, "你好", "chips-rotate")
    assert first["type"] == "guide" and second["type"] == "guide"
    assert first["starters"] and second["starters"]
    assert first["starters"] != second["starters"], (first["starters"], second["starters"])
