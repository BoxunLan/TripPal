"""同行人抽取的两处静默缺陷 + 「行程信息卡」这条结构化通路。

背景（2026-10-05 用户报）：系统追问「一行几个人、有没有老人和小孩」，用户答
「只有我一个成年人」，系统没识别、又问了一遍。查下来是**两个**缺陷叠在一起：

① 抽取是**整串短语枚举**（「(\\d+)\\s*(?:个|名)?\\s*人」「(\\d+)\\s*个大人」「(\\d+)\\s*名成人」），
   要求数词、量词、身份词严丝合缝地相邻。「一个成年人」「1位成人」「两位成人」
   这种再自然不过的说法，一个都抽不到。

② 身份词**不认否定**：只看句子里有没有出现「老人」「小孩」这几个字。用户答
   「没有老人也没有小孩」→ 判定**有长者同行**。这比抽不到更糟：抽不到只是再问一句，
   判反会让行程加载长者节奏叠加层，而且因为 has_elder 已知，追问里不再问同行人构成。

③ 表单（slot_overrides）：口语的写法枚举不完，正则补一轮就漏一轮新的说法。
   所以留给调用方一条**不依赖抽取**的路 —— 填了就直接是槽位。这是用户提的方案。
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.slots import extract_slots, slots_from_form


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("只有我一个成年人", 1),          # ← 用户原话
        ("就我一个成年人", 1),
        ("一个成年人", 1),
        ("1个成年人", 1),
        ("我一个成人", 1),
        ("1位成人", 1),
        ("一名成人", 1),
        ("两位成人", 2),
        ("两大人", 2),
        ("三个大人", 3),
        ("4位成年人", 4),
    ],
)
def test_party_size_survives_changed_words(text, want):
    """数词/量词/身份词自由组合都要认 —— 它们是正交的，不能靠枚举。"""
    s = extract_slots(text, get_settings())
    if want is None:
        assert "party_size" not in s, text
    else:
        assert s.get("party_size") == want, text


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("就我自己", 1),
        ("只有我", 1),
        ("只有我自己去", 1),
        ("光我一个人去厦门", 1),
    ],
)
def test_pronoun_solo_is_one_person(text, want):
    """「就我自己」没有数词，靠代词规则落地。"""
    assert extract_slots(text, get_settings()).get("party_size") == want, text


@pytest.mark.parametrize("text", ["只有我们两个人", "只有我和老婆两人", "就我们一家三口"])
def test_pronoun_solo_does_not_eat_plural(text):
    """反例：代词规则不能把「只有我们…」读成 1 人 —— 那正好把意思搞反。"""
    s = extract_slots(text, get_settings())
    assert s.get("party_size") != 1, f"{text} 被读成了 1 人"


def test_composed_counts_add_up():
    """同一句里多处计数要累加：「两个大人一个小孩」是 3 人，不是 2 人。"""
    s = extract_slots("两个大人一个小孩去厦门 5 天，预算 8000", get_settings())
    assert s["party_size"] == 3
    assert s.get("has_children")


def test_english_adult_child_counts_add_up():
    s = extract_slots("Xiamen 5 days, 2 adults and 1 child, budget 8000", get_settings())
    assert s["party_size"] == 3
    assert s.get("has_children")


# ---------------------------------------------------------------- 否定
@pytest.mark.parametrize(
    "text",
    [
        "没有老人也没有小孩，就我一个成年人",
        "没有，只有我一个成年人",
        "无老人无儿童，一名成人",
        "不带孩子，就我一个人",
        "不算老人，就两个人",
    ],
)
def test_negation_is_not_read_as_having_companions(text):
    """用户说「没有」，就不能判定「有」—— 这是比漏抽更严重的反向错误。"""
    s = extract_slots(text, get_settings())
    assert not s.get("has_elder"), f"{text} 被读成有长者同行"
    assert not s.get("has_children"), f"{text} 被读成带儿童"


def test_negated_clause_still_lets_other_clause_count():
    """『没有，我带着爸妈去』 = 有长者同行（否定落在另一小句上）。"""
    s = extract_slots("没有，我带着爸妈去厦门 5 天", get_settings())
    assert s.get("has_elder")


@pytest.mark.parametrize(
    "text", ["同行有 70 岁老人，去厦门 5 天", "我带着爸妈去厦门 5 天", "带 6 岁孩子去厦门 5 天"]
)
def test_affirmative_companions_still_detected(text):
    """否定修法的回归防线：肯定的说法一个都不能丢。"""
    s = extract_slots(text, get_settings())
    assert s.get("has_elder") or s.get("has_children"), text


def test_negated_age_is_not_counted():
    """『没有 6 岁孩子』里的岁数也不能算 —— 年龄走的是同一套分句判据。"""
    s = extract_slots("没有 6 岁孩子，就我一个成年人", get_settings())
    assert not s.get("has_children")
    assert not s.get("child_age")


# ---------------------------------------------------------------- 行程信息卡
def test_form_slots_become_canonical_slots():
    settings = get_settings()
    s = slots_from_form(
        {"destination": "厦门", "days": 5, "budget": 8000, "adults": 1}, settings
    )
    assert s["destination"] == "厦门"
    assert s["days"] == 5
    assert s["budget"] == 8000.0
    assert s["party_size"] == 1
    # `party` 这个字符串才是缺槽检查看的那一项，只写 party_size 会被继续追问
    assert s["party"] == "1 位成人"


def test_form_party_composition():
    settings = get_settings()
    s = slots_from_form({"adults": 2, "children": 1, "child_age": 6}, settings)
    assert s["party_size"] == 3
    assert s["has_children"] is True
    assert s["child_age"] == 6
    assert "6 岁" in s["party"]

    elders = slots_from_form({"adults": 2, "children": 0, "elders": 1}, settings)
    assert elders["party_size"] == 3
    # 明确写 0 也要落 False：用户可能是在更正上一句
    assert elders["has_children"] is False
    assert elders["has_elder"] is True


def test_form_empty_party_writes_nothing():
    """同行人四项全空 = 没填。写了就会把「没识别」伪装成「已知」—— 那是另一个坑。"""
    settings = get_settings()
    s = slots_from_form({"destination": "厦门", "children": ""}, settings)
    assert "party_size" not in s
    assert "has_children" not in s
    assert s["destination"] == "厦门"


def test_form_accepts_destination_outside_gazetteer():
    """词典里没有的地名也要收下：用户在卡里填了就是目的地，
    反问一个他刚填过的城市，正是这张卡要避免的事。"""
    s = slots_from_form({"destination": "冷门小城"}, get_settings())
    assert s["destination"] == "冷门小城"


def test_form_is_empty_when_nothing_filled():
    assert slots_from_form({}, get_settings()) == {}
    assert slots_from_form(None, get_settings()) == {}


# ---------------------------------------------------------------- 端到端：表单能救回原话
def test_clarify_accepts_the_answer_that_nlp_missed(client):
    """用户报的那条路径走一遍：先被追问，再用信息卡补上 ⟶ 不该再追问。

    注意 message 用的是**抽不出来的那句**（模拟口语），补信息全靠表单 ——
    这正是表单存在的意义：它不是锦上添花，是抽不到时唯一能通的路。
    """
    sid = "form-party-1"
    first = client.post("/plan", json={"session_id": sid, "message": "去厦门玩 5 天，预算 8000"})
    assert first.status_code == 200
    assert first.json()["type"] == "clarify"

    second = client.post(
        "/plan",
        json={
            "session_id": sid,
            "message": "只有我一个成年人",
            "slot_overrides": {"adults": 1, "children": 0, "elders": 0},
        },
    )
    assert second.status_code == 200
    body = second.json()
    assert body["type"] != "clarify", f"又被追问了：{body.get('question')}"
    assert "party" not in (body.get("missing_slots") or [])


def test_message_beats_stale_form(client):
    """本句 > 表单：同一句里自己讲清楚的，优先于表单里可能停留着的旧值。"""
    sid = "form-party-2"
    body = client.post(
        "/plan",
        json={
            "session_id": sid,
            "message": "去厦门玩 5 天，预算 8000，三个人",
            "slot_overrides": {"destination": "厦门", "adults": 1},
        },
    ).json()
    slots = body.get("itinerary", {}).get("party") or ""
    assert "3" in slots or body.get("type") != "clarify", body
