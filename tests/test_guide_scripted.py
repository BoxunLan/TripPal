"""寒暄旁路里「不该调模型」的那几类（2026-10-09）。

用户报「我输入你好，就花了很久时间响应」。查下来：`guide` 走的是**大模型**
（`role=generator` = ecnu-max）+ `json_schema` 那一档，实测同一条提示词中位约 1s，
但出现过 2.7 / 3.0 / 6.9 / **11.1s** 的长尾（证据见 `_server.log` 里
`profile=generator:guide ... 耗时=11.10s`）。而模型写出来的引导语与 i18n 定稿
**几乎逐字相同** —— 也就是说，为一句「你好」白等了最多 11 秒。

于是把寒暄七类里的**封闭式套话**（greeting / thanks / farewell / cancel / chitchat）改成
定稿直答（`app/guide.py::scripted_reply`），只留 `meta` 走模型。

这组用例钉住两条边界：
1. 该直答的 —— **一次模型调用都不许有**（这条断言就是「你好」不再慢的回归门）；
2. 该润色的 —— `meta` 仍然要调模型（别把润色一起优化掉了）。
"""

from __future__ import annotations

import pytest
from conftest import FROZEN_TODAY, post_plan
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import build_deps
from app.embed import HashingEmbedder
from app.fakes import FakeLLM
from app.guide import SCRIPTED_KINDS, scripted_reply
from app.i18n import EN, JA, KO, SUPPORTED, ZH, t
from app.intent import SocialIntent
from app.main import create_app
from app.session import SessionStore
from app.store import InMemoryVectorStore


class WatchingLLM(FakeLLM):
    """记下每一次模型调用（role + task）—— 用来断言「有没有调模型」。"""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.calls: list[dict] = []

    def complete_json(self, *, role, prompt, schema, context):
        self.calls.append({"role": role, "task": (context or {}).get("task")})
        return super().complete_json(role=role, prompt=prompt, schema=schema, context=context)

    def guide_calls(self) -> list[dict]:
        return [c for c in self.calls if c["task"] == "guide"]


@pytest.fixture
def watched():
    """带「模型调用记录」的整链路客户端。"""
    s = get_settings()
    emb = HashingEmbedder(s.embedding.dim)
    llm = WatchingLLM(s)
    deps = build_deps(
        s,
        embedder=emb,
        llm=llm,
        store=InMemoryVectorStore.from_seed_dir(s.seed_dir, emb),
        sessions=SessionStore(),
        today=lambda: FROZEN_TODAY,
    )
    with TestClient(create_app(deps)) as c:
        yield c, llm


# ---------------------------------------------------------- 该直答的：零模型调用
@pytest.mark.parametrize(
    "message,kind",
    [("你好", "greeting"), ("谢谢", "thanks"), ("再见", "farewell")],
)
def test_scripted_social_kinds_never_touch_the_model(watched, message, kind):
    """套话类必须**零模型调用** —— 这是「输入你好要等很久」的回归门。"""
    client, llm = watched
    body = post_plan(client, message, session_id="scripted-%s" % kind)

    assert body["type"] == "guide", body
    assert body["kind"] == kind, body
    assert llm.calls == [], f"「{message}」不该有任何模型调用，实际：{llm.calls}"
    assert body["reply"] == t(f"gd.reply.{kind}", ZH), body["reply"]


def test_cancel_is_scripted_too(watched):
    """「算了不去了」= cancel：先有一趟行程，再说放弃 —— 也走定稿，不调模型。"""
    client, llm = watched
    post_plan(client, "我想去成都玩", session_id="scripted-cancel")
    body = post_plan(client, "算了不去了", session_id="scripted-cancel")

    assert body["type"] == "guide", body
    assert body["kind"] == "cancel", body
    assert llm.guide_calls() == [], "放弃套话不该调模型"
    assert body["reply"] == t("gd.reply.cancel", ZH)


def test_greeting_reply_is_the_i18n_text_verbatim(client):
    """直答就是定稿本身：两次「你好」逐字相同（确定性，无模型抖动）。"""
    a = post_plan(client, "你好", session_id="determinism-a")["reply"]
    b = post_plan(client, "你好", session_id="determinism-b")["reply"]
    assert a == b == t("gd.reply.greeting", ZH)


@pytest.mark.parametrize("message,lang", [("Bonjour", EN), ("こんにちは", JA), ("안녕하세요", KO)])
def test_foreign_greetings_use_their_own_language_fallback(watched, message, lang):
    """非中文问候同样直答，且用**该语言**的定稿（zh/en/ja/ko 四套齐全，不调模型）。

    注：法/西/德等非中日韩文本会被 `detect_language` 判成 `en` —— 这与改前一致
    （改前模型也是按 en 指令作答），不是这次的回归。
    """
    client, llm = watched
    body = post_plan(client, message, session_id="scripted-%s" % lang)

    assert body["type"] == "guide", body
    assert llm.calls == [], f"「{message}」不该有模型调用，实际：{llm.calls}"
    assert body["reply"] == t("gd.reply.greeting", lang), body["reply"]


# ---------------------------------------------------------- 该润色的：仍走模型
def test_meta_still_goes_through_the_model(watched):
    """`meta`（你是谁 / 能做什么）是七类里唯一的真实问句，仍然调模型润色。"""
    client, llm = watched
    body = post_plan(client, "你能做什么", session_id="scripted-meta")

    assert body["type"] == "guide", body
    assert body["kind"] == "meta", body
    assert llm.guide_calls(), "meta 应当交给模型润色，不该被一起改成直答"
    assert body["reply"], "模型给的引导语不能为空"


# ---------------------------------------------------------- 规则本身（纯单元）
def test_scripted_reply_returns_verbatim_for_scripted_kinds():
    for kind in SCRIPTED_KINDS:
        got = scripted_reply(SocialIntent(kind=kind, matched=""), ZH)
        assert got == t(f"gd.reply.{kind}", ZH), kind


def test_scripted_reply_defers_the_other_kinds_to_the_model():
    """判据要窄：只认这四类，别把 meta / recall 也吞进直答。"""
    for kind in ("meta", "recall", "greeting_ish_typo"):
        assert scripted_reply(SocialIntent(kind=kind, matched=""), ZH) is None, kind


def test_every_scripted_kind_has_a_fallback_in_every_supported_language():
    """直答的前提是**定稿齐全**：缺一种语言，直答就会退化成空回复。"""
    for lang in SUPPORTED:
        for kind in SCRIPTED_KINDS:
            text = scripted_reply(SocialIntent(kind=kind, matched=""), lang)
            assert text and text.strip(), f"{lang}/{kind} 缺定稿文案"


def test_scripted_kinds_are_exactly_the_social_formulas():
    """把集合本身钉住：增删都要有人看一眼（新增 kind 先问「回复会随用户说的话变吗」）。"""
    assert set(SCRIPTED_KINDS) == {"greeting", "thanks", "farewell", "cancel", "chitchat"}


# ---------------------------------------------------------- 提示词主题护栏
def test_guide_prompt_pins_the_inbound_china_theme():
    """`meta` 的示例提问必须是「外国游客来中国」，不是中国游客出境。

    真 bug（2026-10-09 探针实测，真模型 ecnu-max）：`guide.md` 规则 7 只说「覆盖不同方向」，
    模型照着写成了「去日本玩需要办什么签证？/ 出国玩怎么绑卡支付？」—— 与「外国人来华」
    这个唯一垂直定位冲突。修法是在 `prompts/guide.md` 里把主题写成硬约束。
    这里钉住那条约束**还在**（改提示词时删掉它，问题只会静默复发）。
    """
    from app.config import get_settings
    from app.prompts import load_prompt

    text = load_prompt(get_settings().prompt_paths.get("guide", "prompts/guide.md"))
    assert "外国游客来中国" in text, "guide.md 缺「主题恒为外国游客来中国」的硬约束"
    # 反面词也要点名，否则模型不知道「不能写什么」
    assert "出境" in text or "去日本" in text, "没把「不要写出境问题」说清楚"
