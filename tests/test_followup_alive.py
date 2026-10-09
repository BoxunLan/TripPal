"""「接着可以问」不再是死的 + 「按填写的信息重发本句」不再死循环。

两个用户报的形态（2026-10-09）：

1. **建议栏是死的** —— 同一会话连出几版稿，三条建议一字不差；刚点过的那条还会原样
   再出现一次（点完还看到同一条，就像点了没反应）。天数还会跟稿子对不上
   （5 天的稿子写着「把第 3 天…」）。
2. **预算追问死循环** —— 「提高开销」→ 追问卡里填好金额 → 点「按填写的信息重发本句」
   → 又看到同一句追问，点几次都是它。根因是判据只看「**原话里**有没有数」，
   而重发时原话刻意不变、数在表单里。
"""

from __future__ import annotations

import re

from app.followups import next_questions
from app.i18n import t


def _post(client, message: str, sid: str, overrides: dict | None = None) -> dict:
    payload: dict = {"session_id": sid, "message": message}
    if overrides:
        payload["slot_overrides"] = overrides
    resp = client.post("/plan", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


BASE_FORM = {"destination": "上海", "days": 4, "adults": 2, "budget": 8000}


# --------------------------------------------------------------- 建议栏不再「死」
def test_the_bar_visibly_changes_after_you_use_one(client):
    """点掉一条建议之后，建议栏必须肉眼可辨地不同 —— 否则模块看起来就是死的。"""
    body = _post(client, "帮我排个上海4天的行程", "nq-live", BASE_FORM)
    assert body["type"] == "plan", body
    first = body["next_questions"]
    assert len(first) >= 2, first

    used = first[0]
    nxt = _post(client, used, "nq-live", BASE_FORM)
    assert nxt["type"] == "plan", nxt
    second = nxt["next_questions"]
    assert used not in second, ("刚用过的那条不该原样再出现", used, second)
    assert second != first, ("相邻两版的建议栏一字不差 = 模块像死的", first, second)


def test_plan_suggestions_rotate_with_the_turn():
    """同一份稿子在不同轮次给不同的窗口 —— 这是「不再一字不差」的来源。"""
    slots = {"destination": "上海", "days": 5}
    windows = [tuple(next_questions(decision="plan", slots=slots, offset=i))
               for i in range(4)]
    assert len(set(windows)) >= 3, windows
    # 每个窗口都得是满的（备位池够用，不许缩水）
    assert all(len(w) == 3 for w in windows), windows


def test_the_just_used_suggestion_is_dropped():
    """`avoid` 精确摘掉刚做过的那条，且不从别的条目里误伤。"""
    slots = {"destination": "上海", "days": 5}
    full = next_questions(decision="plan", slots=slots)
    assert "住宿换成便宜一点的" in full, full
    trimmed = next_questions(decision="plan", slots=slots, avoid=["住宿换成便宜一点的"])
    assert "住宿换成便宜一点的" not in trimmed, trimmed
    assert len(trimmed) >= 2, trimmed
    # 其余两条原样的仍在
    assert "再多安排一天" in trimmed, trimmed


def test_avoid_never_empties_the_bar():
    """摘完只剩 1 条时宁可留着 —— 「按这个帮我排进行程」是核心出口，不能被掏空。"""
    out = next_questions(decision="answer", slots={"destination": "北京"},
                         avoid=["按这个帮我排进行程"])
    assert len(out) >= 2, out
    assert any("排进行程" in x for x in out), out


def test_spare_pool_is_translated():
    """备位池四语言都要有，否则轮换只对中文生效、小语种建议栏数量骤减。"""
    for lang in ("zh", "en", "ja", "ko"):
        raw = t("nq.plan_extra", lang, place="上海", day=4)
        assert raw != "nq.plan_extra", (lang, "缺这条文案")
        assert len([x for x in raw.split("|") if x.strip()]) >= 3, raw
        # 小语种也要能个性化：{day} 要真的被替换掉
        assert "{day}" not in raw, raw


def test_plan_suggestions_use_the_real_day_count(client):
    """建议里的「第 N 天」必须等于**这一版稿子**的天数（改稿后尤其容易对不上）。"""
    body = _post(client, "帮我排个上海4天的行程", "nq-days", BASE_FORM)
    assert body["type"] == "plan", body
    n = len(body["itinerary"]["days"])
    for item in body["next_questions"]:
        m = re.search(r"第\s*(\d+)\s*天", item)
        if m:
            assert int(m.group(1)) == n, (item, n, body["next_questions"])


# --------------------------------------------------------------- 重发本句不再循环
def test_refill_the_card_does_not_loop(client):
    """「提高开销」→ 追问 → 在追问卡里填好金额点「按填写的信息重发本句」→ 必须出稿。

    复现路径完全照前端：原话**不变**（仍是「提高开销」），数在 `slot_overrides` 里。
    修之前这一步永远返回同一个 clarify（实测连续 3 次），点几次都是它。
    """
    body = _post(client, "帮我排个上海4天的行程", "loop1", BASE_FORM)
    assert body["type"] == "plan", body

    ask = _post(client, "提高开销", "loop1", BASE_FORM)
    assert ask["type"] == "clarify", ask
    assert ask["missing_slots"] == ["budget"], ask

    refilled = _post(client, "提高开销", "loop1", {**BASE_FORM, "budget": 15000})
    assert refilled["type"] != "clarify", (
        "重发本句还是同一个追问 = 死循环", refilled.get("question"))
    assert refilled["type"] == "plan", refilled


def test_a_stale_form_budget_does_not_answer_the_question(client):
    """行程信息卡是**常驻**的：上一次填的预算不能冒充「他已经回答了这一问」。

    少了这条，「提高开销」会被表单里的旧值当成「数已经给了」，于是直接拿旧值改稿 ——
    用户永远等不到那句「想提到多少」。
    """
    body = _post(client, "帮我排个上海4天的行程", "stale1", BASE_FORM)
    assert body["type"] == "plan", body
    ask = _post(client, "提高开销", "stale1", BASE_FORM)   # 表单里还是旧值 8000
    assert ask["type"] == "clarify", ask
    assert ask["missing_slots"] == ["budget"], ask


def test_saying_the_number_revises_in_one_turn(client):
    """原话里自己带上了数 → 一轮就改稿，不需要再问（本句 > 表单）。"""
    body = _post(client, "帮我排个上海4天的行程", "num1", BASE_FORM)
    assert body["type"] == "plan", body
    out = _post(client, "提高开销，预算15000", "num1", BASE_FORM)
    assert out["type"] == "plan", out
