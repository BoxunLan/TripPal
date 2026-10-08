"""外文（尤其日语）答追问时的槽位抽取回归。

背景（2026-10-08 用户实测）：会话中途改说日语后**卡在追问环节** —— 系统问
「何名様ですか？」，用户答「2名です」，追问原样重复、连问两轮纹丝不动。查下来是
四条独立缺陷叠在一起，全部属于「语序/量词按中文假设写死」：

① 人数：`_PARTY_COUNT_RES` 要求量词（个/名/位）后面**必须再跟一个身份词**
   （成年人|成人|大人|人）。日语最标准的人数说法就是「2名」，句中一个「人」字都没有
   → 整条落空。中文「2名」同样漏。
② 出发日：整日期规则硬要求 4 位年，「10月20日」「10月20号」「10/20」整条落空。
   而 `date_range` 是**全部规划场景的必填槽** → 反复追问出发日，第二条死循环。
③ 日语的「20日」= 某月 20 号，不是 20 天。旧规则把「来月の20日」读成 **20 天行程**
   —— 错值比缺值更危险。
④ 日 / 韩是**谓语后置**语言，「予算」落在数字之后（「3万元の予算」）；预算规则全部
   假设提示词在前 → 整条落空，用户报了预算却被一路追问。

判据一律「同一句话的中/日两版都必须抽到」，并对每条修复配一条**反向护栏**
（第2位、门票3万元、3月の20日），防止为了接住而误伤。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.config import get_settings
from app.slots import extract_slots


def _slots(text: str) -> dict:
    return extract_slots(text, get_settings())


# ------------------------------------------------------------------ ① 量词自带人数
@pytest.mark.parametrize(
    "text",
    ["2名です", "2名で", "2名様", "3名で伺います", "2名", "2位", "3位です"],
)
def test_standalone_counter_counts_people(text):
    """「数词 + 名/位」本身就是人数，不需要再跟一个「人」字。"""
    slots = _slots(text)
    want = 2 if text.startswith("2") else 3
    assert slots.get("party_size") == want
    assert slots.get("party") == f"{want} 位成人"


def test_standalone_counter_does_not_double_count():
    """「2位成人」由带身份词那条规则负责，不能被独立量词规则再数一遍（否则记成 4 人）。"""
    assert _slots("2位成人").get("party_size") == 2
    assert _slots("2名の成人").get("party_size") == 2


def test_rank_word_is_not_a_party_size():
    """反向护栏：「第2位」是名次，不是 2 个人。"""
    assert _slots("第2位").get("party_size") is None


# ------------------------------------------------------------------ ② 出发日（不带年）
@pytest.mark.parametrize(
    "text",
    ["10月20日", "10月20号出发", "10月20日に出発します", "10/20", "10/20出发"],
)
def test_month_day_without_year_fills_date_range(text):
    """不带年的月日必须落进 date_range —— 它是必填槽，落空会把用户卡在追问里。"""
    slots = _slots(text)
    assert slots.get("date_range"), text
    assert str(slots.get("start_date") or "").endswith("-10-20"), slots.get("start_date")


def test_slash_shorthand_needs_date_context():
    """反向护栏：斜杠在别的语境里不是日期（「1/2 的预算」「3/4 天」）。

    注意 `date_range` 是**时长标签**，显式写了天数时会被「N 天」覆盖（既有设计），
    所以这里断言的是**没有推出出发日**（start_date），不是 date_range。
    """
    assert _slots("1/2的预算").get("start_date") is None
    assert _slots("3/4天").get("start_date") is None
    assert _slots("3/4天").get("days") == 4


# ------------------------------------------------------------------ ③ 日语「N日」歧义
def test_relative_month_day_is_a_date_not_a_duration():
    """「来月の20日」是日期；旧规则把它读成 20 天（错值）。"""
    slots = _slots("来月の20日に出発します")
    assert slots.get("date_range") == "来月の20日"
    assert slots.get("days") is None, "「20日」是某月 20 号，不是 20 天"
    today = date.today()
    y, mo = today.year, today.month + 1
    if mo > 12:
        mo, y = 1, y + 1
    assert slots.get("start_date") == f"{y:04d}-{mo:02d}-20"


def test_month_day_kanji_is_not_a_duration():
    """反向护栏：「3月の20日」里的 20 日不是时长。"""
    assert _slots("3月の20日").get("days") is None


def test_japanese_duration_still_works():
    """「N日間」「N泊」这类**真正的时长**不能被误伤。"""
    assert _slots("3日間です").get("days") == 3
    assert _slots("2泊3日です").get("days") == 3


# ------------------------------------------------------------------ ④ 预算语序与裸写
@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("予算は1万元です", 10000.0),
        ("3万元の予算です", 30000.0),      # 谓语后置（日语语序）
        ("だいたい1万元", 10000.0),        # 无提示词
        ("1万元ぐらい", 10000.0),
        ("予算は1万5千円", 15000.0),
        ("10万円", 100000.0),
    ],
)
def test_budget_survives_word_order_and_bare_amount(text, want):
    assert _slots(text).get("budget") == want


def test_bare_amount_does_not_eat_scene_prices():
    """反向护栏：「门票 3 万元」是单价，不是整趟预算。"""
    assert _slots("门票3万元").get("budget") is None
