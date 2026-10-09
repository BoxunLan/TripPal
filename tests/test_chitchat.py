"""纯笑声 / 字母梗（「哈哈哈」「呵呵」「lol」「hhhh」）不该掉进四连问（2026-10-09）。

用户报：发一句「哈哈哈」也跳出了追问。追根：前门三道闸门（realtime / knowledge / social）
都不接管，于是落进 `clarify_node` 的槽位体检 → 回「想去哪？几天？预算？几个人？」
—— 这是「把不是排行程的请求当成排行程」的**第九形态**（前八种见 `docs/FEATURES.md`
与 `MEMORY-app.md`）。用户的动作是笑，不该收到一张需求表单。

修法：给 social 闸门加一类 `chitchat`（`routes.yaml` 的 `intents.social.triggers`），
与 greeting / thanks 等同属**封闭式套话** —— 回复与用户笑的那声无关，i18n 定稿即答案，
**零模型调用**（`app/guide.py::SCRIPTED_KINDS`）。

这组用例钉住三条边界：
1. 该直答的 —— 笑声 / 字母梗 → guide(kind=chitchat)，**一次模型调用都不许有**；
2. 别吞真请求 —— 笑声后面跟着正事（「哈哈，上海怎么玩」）不算寒暄；
3. 数字梗（233 / 666）**刻意不收** —— 裸数字会被槽位抽取器读成预算，
   那就该按「用户给了数字」处理，而不是当成笑声。
"""

from __future__ import annotations

import pytest
from conftest import FROZEN_TODAY, post_plan
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import build_deps
from app.embed import HashingEmbedder
from app.fakes import FakeLLM
from app.i18n import EN, KO, SUPPORTED, ZH, t
from app.intent import detect_social_intent
from app.main import create_app
from app.session import SessionStore
from app.store import InMemoryVectorStore


class WatchingLLM(FakeLLM):
    """记下每一次模型调用 —— 用来断言「有没有调模型」。"""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.calls: list[dict] = []

    def complete_json(self, *, role, prompt, schema, context):
        self.calls.append({"role": role, "task": (context or {}).get("task")})
        return super().complete_json(role=role, prompt=prompt, schema=schema, context=context)


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


# 会命中 chitchat 的笑声 / 字母梗。第二列是**期望的输出语言** —— 回复跟输出语言
# （`detect_language`：有汉字 → zh，谚文 → ko，其余拉丁 → en），所以中/英/韩各取样一支。
LAUGHS = [
    ("哈哈哈", ZH), ("哈哈", ZH), ("呵呵", ZH), ("嘿嘿", ZH), ("嘻嘻", ZH), ("哈哈哈啊", ZH),
    ("哈哈嘿", ZH), ("笑死我了", ZH), ("笑不活了", ZH), ("太逗了", ZH), ("绝了", ZH),
    ("hhhh", EN), ("wwww", EN), ("lol", EN), ("hahaha", EN),
    ("ㅋㅋ", KO), ("ㅎㅎ", KO),
]


# ---------------------------------------------------------- 该直答的：零模型调用
@pytest.mark.parametrize("message,lang", LAUGHS)
def test_laughter_gets_a_guide_and_never_touches_the_model(watched, message, lang):
    """笑声必须走 guide(chitchat) 且**零模型调用** —— 「哈哈哈掉进四连问」的回归门。"""
    client, llm = watched
    body = post_plan(client, message, session_id="chitchat-%s" % abs(hash(message)))

    assert body["type"] == "guide", body
    assert body["kind"] == "chitchat", body
    assert llm.calls == [], f"「{message}」不该有任何模型调用，实际：{llm.calls}"
    assert body["reply"] == t("gd.reply.chitchat", lang), body["reply"]


def test_chitchat_reply_is_deterministic():
    """直答就是定稿本身：两次「哈哈哈」逐字相同（确定性，无模型抖动）。"""
    s = get_settings()
    a = detect_social_intent("哈哈哈", s, {})
    b = detect_social_intent("哈哈哈", s, {})
    assert a is not None and b is not None
    assert (a.kind, a.matched) == (b.kind, b.matched) == ("chitchat", "哈哈哈")


# ---------------------------------------------------------- 判据要窄：别吞真请求 / 别吞数字
@pytest.mark.parametrize("message", [
    "哈哈，帮我排个上海的行程",      # 笑声 + 真请求（有目的地）
    "哈哈，上海怎么玩",             # 笑声 + 真请求（有地名）
    "哈哈，我想去成都玩 3 天",       # 笑声 + 行程动词 / 槽位
])
def test_laughter_does_not_swallow_a_real_request(message, deps):
    """笑声只是开场 —— 后面跟着正事时不算寒暄，必须继续走行程链路。"""
    assert detect_social_intent(message, deps.settings, {}) is None, message


def test_laughter_plus_a_real_request_still_plans(client):
    body = post_plan(client, "哈哈，我想去成都玩 3 天", session_id="chitchat-guard")
    assert body["type"] != "guide", body


@pytest.mark.parametrize("message", ["233", "666", "2333"])
def test_numeric_memes_are_not_chitchat(message, deps):
    """数字梗刻意不收：裸数字会被抽取器读成预算（`233` → budget=233）。

    这不是漏判 —— 用户写下「233」时系统按「他给了个数字」处理，优先级高于当成笑声；
    否则会把一个真数字吞掉。真正的笑声（哈 / 嘿 / 嘻…）才是这一类的判据。
    """
    assert detect_social_intent(message, deps.settings, {}) is None, message


def test_a_single_ha_is_not_laughter(deps):
    """单个「哈」可能是语气词，不收 —— 判据是「笑声字符 ≥2」。"""
    assert detect_social_intent("哈", deps.settings, {}) is None


# ---------------------------------------------------------- 定稿齐全（直答的前提）
def test_chitchat_has_a_fallback_in_every_supported_language():
    """直答的前提是**定稿齐全**：缺一种语言，直答就会退化成空回复。"""
    for lang in SUPPORTED:
        text = t("gd.reply.chitchat", lang)
        assert text and text.strip(), f"{lang} 缺 gd.reply.chitchat 定稿文案"


def test_korean_compatibility_jamo_counts_as_korean():
    """`ㅋㅋ` / `ㅎㅎ` 用的是**兼容字母**（U+3131–318F），原先不在 `_HANGUL` 里 → 被判 en，
    韩语用户笑一声收到英文回复。补进范围后归 ko（顺带修好 `ㅠㅠ` 等真实韩语输入）。"""
    from app.i18n import detect_language
    assert detect_language("ㅋㅋ") == KO
    assert detect_language("ㅎㅎ") == KO
    assert detect_language("안녕하세요") == KO   # 谚文音节本来就在范围里，未被改坏
