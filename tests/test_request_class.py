"""请求分类（front-door 闸门）回归：把「寒暄」「有无类事实问答」当**类**守。

用户实测报出两种「把不是排行程的请求当成排行程」：

1. 「你好」→ 回「想去的城市？玩几天？预算？几个人？」四连问（寒暄被当行程）；
2. 「上海有没有免费的博物馆或博览园」→ 掉回槽位体检（有无类事实被当行程），
   而「北京有哪些博物馆」却能答 —— 差别只在 consult 词表收没收「有哪些」。

本组用例按**类**守，不按单条：
- social：短寒暄 / 闲话 / 元问题 → guide 旁路，永不回四连问；
- fact：有无 / 列举 / 免费 / 门票 / 开放时间 → 落到 knowledge / realtime；
- 让路：一旦有目的地或行程槽位，寒暄类不得吞掉真请求。
"""

from __future__ import annotations

import pytest
from conftest import post_plan

from app.intent import detect_social_intent


# ---------------------------------------------------------------- social 类
@pytest.mark.parametrize(
    "message, kind",
    [
        ("你好", "greeting"),
        ("在吗", "greeting"),
        ("谢谢", "thanks"),
        ("你叫什么名字", "meta"),
    ],
)
def test_social_inputs_get_a_guide_not_the_four_questions(client, message, kind):
    """寒暄 / 致谢 / 元问题 → guide；不得出现任何行程槽位追问。"""
    body = post_plan(client, message, session_id="cls-social")

    assert body["type"] == "guide", f"{message} 落到 {body.get('type')}：{body}"
    assert body["kind"] == kind
    assert body["reply"], "引导语不能为空"
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in body["reply"], f"{message} 的回复里出现了行程追问：{word}"
    assert body["starters"], "引导卡应给出建议提问"


def test_greeting_plus_a_real_request_is_not_swallowed(client):
    """「你好，我想去上海玩 3 天」是真行程 —— 不能被这声招呼当寒暄吞掉。"""
    body = post_plan(client, "你好，我想去上海玩 3 天", session_id="cls-social-guard")
    assert body["type"] != "guide", body


def test_gratitude_plus_a_fact_question_still_answers(client):
    """「谢谢，西湖有多大」里的事实问题优先 —— 致谢不该把它变成寒暄。"""
    body = post_plan(client, "谢谢，西湖有多大", session_id="cls-social-fact")
    assert body["type"] == "answer", body


# ---------------------------------------------------------------- social 闸门单元
def test_social_gate_is_deterministic_and_guarded(deps):
    s = deps.settings
    assert detect_social_intent("你好", s, {}) is not None
    assert detect_social_intent("你好", s, {}).kind == "greeting"
    assert detect_social_intent("谢谢", s, {}).kind == "thanks"
    # 让路 1：有行程槽位
    assert detect_social_intent("你好，我想去上海玩 3 天", s, {"days": 3}) is None
    # 让路 2：有可解析目的地
    assert detect_social_intent("你好，上海怎么玩", s, {}) is None
    # 长消息不算寒暄
    assert detect_social_intent("你好啊我最近在计划一场很长很长的旅行你能帮我看看吗", s, {}) is None


# ---------------------------------------------------------------- fact 类：有无 / 列举
@pytest.mark.parametrize(
    "message",
    [
        "上海有没有免费的博物馆或博览园",
        "北京有哪些博物馆",
        "杭州西湖要门票吗",
    ],
)
def test_destination_fact_questions_are_answered_not_clarified(client, message):
    """有无 / 列举 / 免费类问题 → answer；不得回「目的地？天数？预算？人数？」。"""
    body = post_plan(client, message, session_id="cls-fact")

    assert body["type"] == "answer", f"{message} 落到 {body.get('type')}：{body}"
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in body["note"], f"{message} 里出现了行程追问：{word}"


def test_free_museum_question_hits_a_seed_with_official_url(client):
    """「上海有没有免费博物馆」要能命中带**官网**的库内条目（用户要的就是「有哪些 + 网址」）。"""
    body = post_plan(client, "上海有没有免费的博物馆或博览园", session_id="cls-museum")

    assert body["type"] == "answer"
    ids = {h["chunk_id"] for h in body["hits"]}
    assert "gen-in-museum-001" in ids, f"没命中免费博物馆条目：{ids}"
    urls = [h.get("source_url") or "" for h in body["hits"]]
    assert any("shanghaimuseum.net" in u or "artmuseumonline.org" in u for u in urls), urls


def test_museum_seed_is_present_with_an_official_site(store):
    chunks = store.scan(filters={}, limit=500)
    row = [c for c in chunks if c.chunk_id == "gen-in-museum-001"]
    assert row, "gen-in-museum-001 缺失"
    assert row[0].source_url and row[0].source_url.startswith("http")


# ---------------------------------------------------------------- realtime 类：开放时间
def test_opening_time_question_is_a_realtime_fact(client):
    """「故宫开放时间」是会变的事实 → realtime；改前掉回槽位体检（漏判）。"""
    body = post_plan(client, "故宫开放时间", session_id="cls-opentime")

    assert body["type"] == "realtime", body
    assert body["info_type"] == "schedule"
    assert body["subject"] == "故宫"
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in body["note"], word
