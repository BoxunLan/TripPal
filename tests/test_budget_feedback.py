"""预算调整反馈：判据、方向、以及「已给预算却抱怨没花完」的分流（2026-10-09）。

用户实测的 bug：他在拿到计划后说「提高开销」，系统不但没提高，反而一路把开销
压下去（2260 → 2190 → 2160），标题从「预算优化」滑成「穷游」。根因有两层：

  ① **词表只有一半**：`BUDGET_SOFT_RE` 只认「便宜 / 省」这类降向说法，
     「开销 / 花费 / 提高」一个都没进表 —— 用户说「提高开销」时三个判据全灭，
     于是走了普通生成、从零重排。
  ② **方向丢失**：唯一那句追问文案是「想控制在多少（以内）」，对提高方向是**反着问**。

修法：词表两侧补齐 + `budget_adjust_direction` 分方向 + `is_budget_underuse`
（抱怨「钱没花完」= 已给预算、要求用足它 → 不再追问，直接带原稿抬高）。
"""

from __future__ import annotations

import pytest
from conftest import post_plan

from app.slots import (
    budget_adjust_direction,
    is_budget_adjust,
    is_budget_underuse,
)

RAISE = [
    "提高开销",
    "开销太少",
    "多花点钱",
    "花多点",
    "提高预算",
    "预算高一点",
    "再贵点也行",
    "预算没用完",
    "花不完我的钱",
    "spend more money",
]
LOWER = [
    "太贵了，能便宜点吗",
    "便宜点",
    "省一点",
    "降低预算",
    "住宿换成便宜一点的",
    "cheaper",
]


@pytest.mark.parametrize("text", RAISE)
def test_raise_side_is_recognised_and_points_up(text):
    """提高方向的词必须**进表**并判成 raise —— 这是本 bug 的第一层根因。"""
    assert is_budget_adjust(text), text
    assert budget_adjust_direction(text) == "raise", text


@pytest.mark.parametrize("text", LOWER)
def test_lower_side_is_recognised_and_points_down(text):
    """降向是原有能力，不能被这次扩表打坏。"""
    assert is_budget_adjust(text), text
    assert budget_adjust_direction(text) == "lower", text


@pytest.mark.parametrize("text", ["预算8000", "厦门3天", "别太贵", "第2天太紧凑了"])
def test_non_adjust_messages_are_not_captured(text):
    """给了数 / 与预算无关的句子不得被当成「调整预算」（否则会多问一句没用的）。"""
    assert not is_budget_adjust(text), text
    assert budget_adjust_direction(text) is None, text


@pytest.mark.parametrize(
    "text",
    ["我有11111元可以花，你这个计划怎么花不完我的钱", "预算花不完", "太省了"],
)
def test_underuse_complaint_is_detected(text):
    assert is_budget_underuse(text), text


def test_plain_budget_number_is_not_an_underuse_complaint():
    assert not is_budget_underuse("预算11111元")


# ---------------------------------------------------------------- 路由层（端到端）
PRIOR = "上海4天，两个人，预算11111元"


def test_raise_request_asks_target_in_the_raise_wording(client):
    """「提高开销」→ 追问目标预算，且文案必须是「提到多少」，不是「控制在多少」。"""
    post_plan(client, PRIOR, session_id="bf1")
    body = post_plan(client, "提高开销", session_id="bf1")
    assert body["type"] == "clarify", body
    assert body["missing_slots"] == ["budget"], body
    assert "提到" in body["question"], body["question"]
    assert "控制" not in body["question"], body["question"]


def test_lower_request_keeps_the_lower_wording(client):
    post_plan(client, PRIOR, session_id="bf2")
    body = post_plan(client, "太贵了，能便宜点吗", session_id="bf2")
    assert body["type"] == "clarify", body
    assert body["missing_slots"] == ["budget"], body
    assert "控制" in body["question"], body["question"]
    assert "提到" not in body["question"], body["question"]


def test_underuse_complaint_replans_instead_of_re_asking(client):
    """已给预算 + 抱怨没花完 → 不再追问一句他已经答过的数，直接在原稿上重出。"""
    post_plan(client, PRIOR, session_id="bf3")
    body = post_plan(client, "我有11111元可以花，你这个计划怎么花不完我的钱", session_id="bf3")
    assert body["type"] == "plan", body
    assert body.get("itinerary"), body
    # 迭代出稿会先说一句「已按你的要求调整：<原话>」
    assert any("已按你的要求调整" in s for s in (body.get("suggestions") or [])), body.get("suggestions")


def test_budget_adjust_without_prior_budget_falls_through_to_normal_clarify(client):
    """会话里还没有预算时，「提高开销」不该假装在调整 —— 走常规缺槽追问（含预算）。"""
    body = post_plan(client, "提高开销", session_id="bf4")
    assert body["type"] == "clarify", body
    assert "budget" in body["missing_slots"], body
