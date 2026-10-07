"""「知识库里的相关条目」回归：它必须**跟着问句变**，不能是「这个城市的名片」。

真 bug（2026-10-04 用户报）：同一会话里连问几题，页面「知识库里的相关条目（不是这个问题的
答案）」一栏每次列出来的东西一模一样 —— 问「上海有没有免费的博物馆」「上海博物馆要预约吗」
「上海外卡支付怎么用」，三条都只列出同一条 `gen-in-museum-001`。

根因两处，都在 `app/knowledge.py:search_knowledge`：

1. 排序里**没有问句** —— 候选池只有一道布尔闸门（主体出现在正文 / 条目目的地等于主体），
   然后按 `chunk_id` 排；
2. 还有一道「有目的地条目就只留它们」的硬过滤 —— 库里 `destination=上海` 只有 1 条，
   于是任何带「上海」的问题都被吸到那条博物馆条目上，真正讲支付的条目全被丢掉。

本组用例按**类**守：不同问句 → 不同条目；贴题条目排前面；同一问句两次 → 同一顺序。
"""

from __future__ import annotations

import pytest
from conftest import post_plan

from app.knowledge import (
    _bigrams,
    _is_place,
    _query_terms,
    _related_keys,
    _subject_keys,
    display_topic,
)


def hit_ids(body) -> tuple[str, ...]:
    return tuple(h["chunk_id"] for h in body["hits"])


# ---------------------------------------------------------------- 类：跟着问句变
def test_consecutive_questions_do_not_list_the_same_entries(client):
    """同一会话连问三题（同主体、不同诉求）→ 相关条目**必须不一样**。

    这是用户报的那一类：修之前三条全是同一条 `gen-in-museum-001`。
    """
    session = "hits-vary"
    museum = post_plan(client, "上海有没有免费的博物馆或博览园", session_id=session)
    booking = post_plan(client, "上海博物馆要预约吗", session_id=session)
    payment = post_plan(client, "上海外卡支付怎么用", session_id=session)

    for body in (museum, booking, payment):
        assert body["type"] == "answer", body

    listed = {hit_ids(museum), hit_ids(booking), hit_ids(payment)}
    assert len(listed) > 1, f"三题列出了完全相同的条目：{listed}"
    assert hit_ids(museum) != hit_ids(payment), "问博物馆和问支付列出的条目不该一样"


def test_the_topic_of_the_question_leads_the_list(client):
    """问支付 → 打头的必须是讲支付的条目；问博物馆 → 打头的是博物馆条目。"""
    session = "hits-lead"
    payment = post_plan(client, "上海外卡支付怎么用", session_id=session)
    museum = post_plan(client, "上海有没有免费的博物馆或博览园", session_id=session)

    assert payment["hits"], "支付问题不该一条都命中不了"
    assert payment["hits"][0]["chunk_id"].startswith("gen-in-pay"), (
        f"支付问题打头的不是支付条目：{hit_ids(payment)}"
    )
    assert museum["hits"][0]["chunk_id"] == "gen-in-museum-001", hit_ids(museum)


def test_subject_relatedness_is_not_a_hard_filter(client):
    """目的地条目**不得**独占榜首：全国性条目（来华支付/联网/交通）也要能进榜。

    修前：`destination=上海` 只有 1 条 → 任何「上海 X」都只剩那条（硬过滤把真正讲 X 的挤掉）。
    """
    body = post_plan(client, "上海外卡支付怎么用", session_id="hits-nofilter")

    assert body["hits"], body
    destinations = {h.get("destination") for h in body["hits"]}
    assert any(d != "上海" for d in destinations), (
        f"榜单被目的地条目垄断了：{[(h['chunk_id'], h.get('destination')) for h in body['hits']]}"
    )


def test_topic_subject_beats_an_incidental_mention(client):
    """主体是非地名主题（「离境退税」）时，正文开头就讲它的那条要排最前。

    `gen-in-pay-003` 也在正文里顺带提到「离境退税」，但它不是讲退税的 ——
    修之前两者同分、按 chunk_id 排，pay-003 会插到前面。
    """
    body = post_plan(client, "离境退税需要什么条件", session_id="hits-tax")

    assert body["hits"], body
    assert body["hits"][0]["chunk_id"] == "gen-in-tax-001", hit_ids(body)


def test_transport_question_finds_the_transport_entry(client):
    """「地铁」类问句既要有条目，也要真的落到常识链路（修前掉回槽位体检）。"""
    body = post_plan(client, "上海的地铁怎么坐", session_id="hits-metro")

    assert body["type"] == "answer", body
    assert any("metro" in h["chunk_id"] for h in body["hits"]), hit_ids(body)


def test_odd_subject_question_is_not_left_empty(client):
    """「外国人怎么买高铁票」修前命中 0 条：主语被切成「外国人怎么买」，正文里永远没有。

    剥掉疑问/动作尾巴后主语是「外国人」，用问句实词（高铁/12306）也能命中来华交通条目。
    """
    body = post_plan(client, "外国人怎么买高铁票", session_id="hits-train")

    assert body["type"] == "answer", body
    ids = hit_ids(body)
    assert any(i.startswith("gen-in-") or i == "gen-cn-001" for i in ids), ids


# ---------------------------------------------------------------- 稳定性
def test_same_question_twice_gives_the_same_order(client):
    """同一句话两次跑必得同一顺序 —— 这一层（确定性闸门）不允许有随机性。"""
    first = post_plan(client, "上海外卡支付怎么用", session_id="hits-stable-a")
    second = post_plan(client, "上海外卡支付怎么用", session_id="hits-stable-b")

    assert hit_ids(first) == hit_ids(second), (hit_ids(first), hit_ids(second))


def test_library_silence_stays_silent(client):
    """库里真没有就列空 —— 不许为了凑数把不相干条目推上去。"""
    body = post_plan(client, "西湖有多大", session_id="hits-none")

    assert body["type"] == "answer", body
    assert body["hits"] == [], hit_ids(body)


def test_a_known_subject_is_not_padded_with_other_cities(client):
    """主体在库里一无所知时返回空，**不拿别的话题或别的城市凑数**。

    「杭州西湖要门票吗」修后一度列出清迈/厦门的免费点位 —— 都在讲门票，但没一条在讲西湖。
    那一栏是「知识库里的相关条目」，不是「同样话题的条目」；空着由文案明说本库没有。
    """
    body = post_plan(client, "杭州西湖要门票吗", session_id="hits-padding")

    assert body["type"] == "answer", body
    assert body["hits"] == [], f"拿别的城市凑数了：{hit_ids(body)}"


# ---------------------------------------------------------------- 打分口径（单元）
def test_query_terms_drops_the_subject_and_question_words():
    """实词里不能留主体字面（否则「上海」把同城条目全拉平）与疑问词。"""
    terms = _query_terms("上海外卡支付怎么用", _subject_keys("上海"))

    assert "上海" not in terms
    assert "海外" not in terms, "跨界 bigram 会把「上海外卡」切成「海外」这种假词"
    assert "支付" in terms
    assert "怎么" not in terms


def test_query_terms_are_empty_for_a_pure_metric_question():
    """「西湖有多大」去掉主体与度量词后不留实词 —— 它不该因为「多大」二字命中别的条目。"""
    assert _query_terms("西湖有多大", _subject_keys("西湖")) == set()


def test_subject_is_place_depends_on_the_destination_dictionary(deps):
    assert _is_place(deps.settings, "上海") is True
    assert _is_place(deps.settings, "离境退税") is False
    assert _is_place(deps.settings, "") is False


@pytest.mark.parametrize(
    "run, want",
    [
        ("上海", {"上海"}),
        ("外卡支付", {"外卡", "卡支", "支付"}),
        ("地", set()),
        ("", set()),
    ],
)
def test_bigrams_shape(run, want):
    assert _bigrams(run) == want


def test_subject_keys_include_the_compound_pieces():
    """「杭州西湖」要能同时摘掉整个主体和它的成分（否则剩余实词里会留「西湖」）。"""
    keys = _subject_keys("杭州西湖")

    assert "杭州西湖" in keys and "杭州" in keys and "西湖" in keys


# ---------------------------------------------------------------- 类：实词不能有跨骨架假词
@pytest.mark.parametrize(
    ("message", "forbidden", "wanted"),
    [
        ("上海博物馆收不收费", {"收不", "不收"}, "收费"),
        ("上海博物馆贵不贵", {"贵不", "不贵"}, None),
        ("上海博物馆要不要门票", {"要门"}, "门票"),
    ],
)
def test_v_not_v_frames_do_not_become_query_terms(message, forbidden, wanted):
    """「V不V」骨架不能抽成**跨骨架的假词** —— 它们会命中无关条目。

    真 bug（2026-10-04 实测，用户报「相关条目无关」的最后一块）：`_query_terms` 直接对原话
    取汉字 bigram，「收不收费」切出「收不 / 不收」。其中「收不」正好命中 eSIM 条目里的
    「**收不**到短信」，于是一条讲在华上网的条目被列进了博物馆问题的相关条目。

    折词（`_VV_FOLD`）后只剩实义部分（收费 / 贵 / 门票），假词与那条误召回一起消失。
    """
    terms = _query_terms(message, _subject_keys("上海博物馆"))

    assert not (terms & forbidden), (message, terms)
    if wanted is not None:
        assert wanted in terms, (message, terms)



# ---------------------------------------------------------------- 类：卡片标题不退化
def test_display_topic_keeps_the_subject_when_it_was_carried_over():
    """承接来的追问自己**没有主体**，只剩属性词 —— 拼上去只会得到生硬标题。

    真 bug（2026-10-04 实测把用户对话连起来跑时看到）：追问「那里有什么好吃的／那个学校怎么样」
    的主体是上一轮的「上海海事大学」，机械拼接产出「上海海事大学好吃」「上海海事大学学校」。
    这类标题既不像话也不帮用户分辨提问，直接用主体当标签才对。
    """
    assert display_topic("上海海事大学", "那里有什么好吃的", "") == "上海海事大学"
    assert display_topic("上海海事大学", "那个学校怎么样", "") == "上海海事大学"
    assert display_topic("上海海事大学", "附近有什么好玩的", "") == "上海海事大学"
    assert display_topic("上海博物馆", "那要预约吗？", "") == "上海博物馆"


def test_display_topic_refines_when_the_subject_is_in_the_message():
    """主体**在这一句里**时照旧细化：标题要能区分同一城市的多次提问（原 bug 的另一面）。"""
    assert display_topic("上海", "上海外卡支付怎么用", "") == "上海外卡支付"
    # 「V不V」骨架不能被当成焦点词（残片「要不」曾比真焦点长，必被选中）。
    topic = display_topic("上海博物馆", "上海博物馆要不要门票", "要门票")
    assert topic == "上海博物馆门票", topic


# ------------------------------------------- 类：假词 / 通用词不许把无关条目拉进相关条目
# 真 bug（2026-10-04 实测，把用户审计里的对话逐轮回放才看到）：
#   「华东师范大学在哪儿」→（answer ✅，无相关条目）
#   「它还有个滴水湖校区对吗」→（answer ✅）
#   「具体位置在哪儿」→ 相关条目列出了 `bud-gen-002`（住宿省钱）与 `vis-kr-001`（韩国签证）。
# 两个来源各一处，都是「拿问法的字面去跟正文比字面」：
#   ① 确认尾「校区**对吗**」切出跨界假词「区对」→ 命中「领**区对**特定户籍」；
#   ② 占位名词语「**位置**」→ 命中「**位置**优先于房型」。
def test_confirmation_tails_do_not_become_cross_frame_terms():
    """「…对吗 / 对吧」这类确认尾与 V不V 同类，不许被切成跨界假词。"""
    terms = _query_terms("它还有个滴水湖校区对吗", _subject_keys("华东师范大学"))

    assert "区对" not in terms, terms
    assert "校区" in terms, terms


def test_placeholder_nouns_are_not_query_terms():
    """「具体位置在哪儿」不带给库内条目用的实词 —— 问法不是主题。"""
    terms = _query_terms("具体位置在哪儿", _subject_keys("华东师范大学"))

    assert not ({"位置", "具体", "体位"} & terms), terms


def test_generic_institution_component_is_not_subject_relatedness():
    """「华东师范大学」的成分 bigram「大学」不构成「在讲这所学校」。

    少了这条，「厦门地铁…厦门大学一带」会被算成讲华东师范大学的条目，
    于是「本库对它一无所知 → 返回空」的闸门失效，整张候选表被当成相关条目列出来。
    """
    assert "华东师范大学" in _related_keys("华东师范大学")
    assert "大学" not in _related_keys("华东师范大学")
    # 复合主体的**专名部分**照旧算数（「杭州西湖」认「西湖」）。
    assert "西湖" in _related_keys("杭州西湖")


def test_an_unknown_subject_lists_no_other_topics(client):
    """端到端：库里没有它的材料时，「相关条目」必须是**空的**，不许拿别的话题凑数。"""
    body = post_plan(client, "华东师范大学在哪儿", session_id="hits-unknown")

    assert body["type"] == "answer", body
    assert hit_ids(body) == (), f"未知主体却列出了别的条目：{hit_ids(body)}"

