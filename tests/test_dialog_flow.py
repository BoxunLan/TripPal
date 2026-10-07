"""交互流畅度：口语/随机输入的识别 + 少用固定模版追问（2026-10-06）。

分两层测：
  · **抽取层**：约数区间、裸金额、口语同行人、城市昵称 —— 抽不到就会把用户刚说过的
    东西再问一遍（「用户卡在已经被回答过的问题上」是这类产品最伤的体验）。
  · **交互层**：放手表达（随便/都行）与纯应答（好的/嗯）**不得**再甩一次四连问；
    放手时用常规默认补齐骨架，并把默认值**说给用户听**。
"""

from __future__ import annotations

from conftest import post_plan

from app.config import get_settings
from app.i18n import DEFAULT as ZH
from app.slots import (
    build_clarify_question,
    extract_slots,
    is_ack_only,
    is_budget_adjust,
    is_delegating,
    merge_slots,
)


def _slots(text):
    return extract_slots(text, get_settings())


# ---------------------------------------------------------------- 抽取层
def test_fuzzy_range_is_a_range_not_a_sum():
    """「三五天」是 3–5 天，不是 3+5=8 天。旧实现用加法，把约数读成 8 / 5 / 9 天。"""
    assert _slots("玩个三五天")["days"] == 5
    assert _slots("两三天")["days"] == 3
    assert _slots("玩个四五天")["days"] == 5
    assert _slots("一周左右")["days"] == 7


def test_bare_amount_without_the_word_budget():
    """口语里的预算常不带「预算」二字（「三天，三千块，我自己」）—— 旧规则整条漏掉。"""
    s = _slots("我想十一去西安，三天，三千块，我自己")
    assert s["budget"] == 3000.0
    assert s["days"] == 3
    assert s["party_size"] == 1
    assert _slots("就一千多吧")["budget"] == 1000.0
    assert _slots("两千来块")["budget"] == 2000.0


def test_ticket_price_is_not_a_budget():
    """「门票 55 元」不是整趟预算。兜底抽取必须认场景词，否则预算校验整体失真。"""
    assert "budget" not in _slots("门票 55 元")
    assert "budget" not in _slots("大熊猫基地门票 55 元一个人")


def test_spouse_and_three_people_are_recognised():
    """「我和我老婆」「我们仨」—— 旧代码掉进「未识别 → 2 大 1 小」兜底，凭空多一个儿童。"""
    s = _slots("带我女朋友")
    assert s["party_size"] == 2 and not s.get("has_children")
    s2 = _slots("我和我老婆")
    assert s2["party_size"] == 2 and not s2.get("has_children")
    assert _slots("我们仨")["party_size"] == 3
    assert _slots("我们俩")["party_size"] == 2
    # 裸「我自己」（不带「就」）也要算独行
    assert _slots("我自己去")["party_size"] == 1


def test_city_nicknames():
    """「魔都 / 帝都 / 羊城」这类口语叫法不该被反问「想去的城市是哪里」。"""
    assert _slots("想去魔都")["destination"] == "上海"
    assert _slots("去帝都")["destination"] == "北京"
    assert _slots("想去羊城")["destination"] == "广州"


# ---------------------------------------------------------------- 文案
def test_single_missing_uses_softer_ask_head():
    """只缺一项时不再用「还需要确认：」这种清单腔；多项仍是原契约文案。"""
    assert build_clarify_question(["destination"], {}, language=ZH).startswith("就差一项了")
    assert build_clarify_question(["date_range", "budget", "party"], {}, language=ZH).startswith(
        "还需要确认"
    )


# ---------------------------------------------------------------- 交互层
def test_delegating_words_are_detected():
    for w in ["随便", "都行", "你看着办", "听你的", "无所谓", "简单点就行", "你推荐吧", "预算不多"]:
        assert is_delegating(w), w
    assert not is_delegating("去成都玩三天")


def test_ack_only_needs_the_whole_sentence():
    for w in ["好的", "嗯", "可以", "对", "ok"]:
        assert is_ack_only(w), w
    # 带信息的句子不算空转 —— 它应当照常进正常抽取
    assert not is_ack_only("可以的，三个人")


def test_delegating_asks_only_destination_not_four_questions(client):
    """用户说「随便」= 让你定。不能再甩一次四连问，只该问唯一不能替他猜的目的地。"""
    body = post_plan(client, "随便", session_id="dlg1")
    assert body["type"] == "clarify"
    assert body["missing_slots"] == ["destination"], body
    assert "就差一项了" in body["question"]


def test_ack_only_narrows_to_single_question(client):
    """「好的」不含信息，也不该把四连问再刷一遍 —— 收窄成一个问题。"""
    body = post_plan(client, "好的", session_id="dlg2")
    assert body["type"] == "clarify"
    assert len(body["missing_slots"]) == 1


def test_delegating_with_destination_goes_straight_to_plan(client):
    """已知道目的地之后说「随便」→ 用常规默认直接出方案，并把默认值说给用户听。"""
    first = post_plan(client, "成都", session_id="dlg3")
    assert first["type"] == "clarify"
    body = post_plan(client, "你看着办", session_id="dlg3")
    assert body["type"] == "plan", body
    assert body["suggestions"] and "常规" in body["suggestions"][0]
    # 骨架默认：5 天 / 2 人
    assert len(body["itinerary"]["days"]) == 5


def test_delegating_does_not_override_what_the_user_said(client):
    """「成都玩三天，预算 8000」之后说「都行」→ 已说过的三项不能被默认值覆盖。"""
    post_plan(client, "成都玩三天，预算 8000，两个人", session_id="dlg5")
    body = post_plan(client, "都行", session_id="dlg5")
    # 这句本身信息已齐，第一句就应出方案；这里只断言"不会被改小/改乱"
    assert body["type"] in ("plan", "clarify")
    if body["type"] == "plan":
        assert len(body["itinerary"]["days"]) == 3


def test_form_still_wins_when_user_is_delegating(client):
    """放手表达 + 表单填了值 → 表单直达，不追问。"""
    resp = client.post(
        "/plan",
        json={
            "session_id": "dlg6",
            "message": "随便",
            "slot_overrides": {"destination": "成都", "days": "5", "budget": "10000", "adults": "2"},
        },
    )
    body = resp.json()
    assert body["type"] == "plan", body
    assert "destination" not in (body.get("missing_slots") or [])


# ---------------------------------------------------------------- 多轮修订（2026-10-06 二轮）
# 这一组全部来自「自己用一会儿」跑出来的现场：用户拿到一份稿之后再说一句话，
# 系统把**修订意图**读成了**绝对值**——错值静默进槽位，比抽不到更危险。
def test_ordinal_day_is_not_a_duration():
    """「第二天」是指向某一天，不是「改成 2 天」。

    真 bug：刚出一份 5 天稿，用户问「第二天具体去哪儿」，date_range 的「天」正则
    把「二天」吃掉 → days=2 → 行程被重排成「成都 2 日行程」。
    """
    for m in ["第二天具体去哪儿？", "第2天", "第 2 天", "第三天呢", "第4晚"]:
        assert "days" not in _slots(m), m
    # 正常时长表述不受影响
    assert _slots("三天")["days"] == 3
    assert _slots("5日")["days"] == 5


def test_increment_days_is_relative_not_absolute():
    """「加一天」是在已有行程上 +1，不是把 5 天改成 1 天。"""
    assert _slots("加一天").get("days_delta") == 1
    assert "days" not in _slots("加一天")
    assert _slots("多玩两天").get("days_delta") == 2
    assert _slots("少住一晚").get("days_delta") == -1


def test_increment_days_applied_on_top_of_session():
    prior = {"days": 5, "date_range": "5 天", "budget": 8000.0}
    assert merge_slots(prior, _slots("加一天"))["days"] == 6
    assert merge_slots(prior, _slots("多玩两天"))["days"] == 7
    assert merge_slots(prior, _slots("少住一晚"))["days"] == 4
    # 绝对改写照旧覆盖
    assert merge_slots(prior, _slots("改成3天"))["days"] == 3
    # 没有基数时不猜起点：新会话里单说「加两天」保持缺槽，让 clarify 问一句
    assert "days" not in merge_slots({}, _slots("加两天"))


def test_vague_budget_words_are_detected():
    for w in ["太贵了，能便宜点吗", "再贵点也行", "便宜点", "省一点", "降低预算", "cheaper"]:
        assert is_budget_adjust(w), w
    # 「别太贵」走的是放手（预算没给态度）那条路，不是"调整已有预算"
    for w in ["预算8000", "厦门3天", "别太贵"]:
        assert not is_budget_adjust(w), w


def test_vague_budget_request_asks_target_instead_of_inventing(client):
    """「太贵了，能便宜点吗」→ 追问目标预算。

    旧行为：槽位齐全 → 直接重出稿，模型自己编一个用户没说过的数
    （实测把 8000 的行程重排成 1128 元）—— 那不是调整，是替用户拍板。
    """
    post_plan(client, "成都5天，预算8000，两个人", session_id="dlg7")
    body = post_plan(client, "太贵了，能便宜点吗", session_id="dlg7")
    assert body["type"] == "clarify", body
    assert body["missing_slots"] == ["budget"]


def test_bare_number_answer_is_taken_as_budget():
    """系统追问「想控制在多少」后，用户回一个裸数字（「6000 吧」）要接住。

    旧行为：既没有量词也没有「预算」二字 → 前两条预算规则都落空 → 槽位保留旧值，
    表现成"你问了、我答了、你没听见"。限定整句只有数字+语气词，所以不会误伤
    带单位的槽位答复。
    """
    for m, v in [("6000 吧", 6000.0), ("1万", 10000.0), ("6千", 6000.0),
                 ("5000就行", 5000.0), ("大概6000", 6000.0), ("六千", 6000.0)]:
        assert _slots(m).get("budget") == v, m
    for m in ["5天", "两个人", "门票 55 元", "3", "第二天"]:
        assert "budget" not in _slots(m), m


def test_ordinal_question_does_not_shrink_session_days(client):
    """图级：已经 5 天了，问「第二天」，会话里的天数不能被改成 2。"""
    post_plan(client, "成都5天，预算8000，两个人", session_id="dlg8")
    post_plan(client, "第二天具体去哪儿？", session_id="dlg8")
    slots = client.get("/session/dlg8").json()["slots"]
    assert slots.get("days") == 5, slots


# ---------------------------------------------------------------- 广覆盖场景（2026-10-06 三轮）
# 这一组来自「把用户说法按维度铺开跑一遍」的场景探针：真服务跑出来才发现，
# 抽取层的漏点都长成了"用户明明说了、系统还要再问一遍"的样子。
def test_colloquial_durations():
    """「半个月」「十来天」里没有可解析的数词（半 / 来都不是数字），正则类永远抽不到。"""
    assert _slots("半个月")["days"] == 15
    assert _slots("十来天")["days"] == 10
    assert _slots("二十来天")["days"] == 20
    # 显式天数优先
    assert _slots("半个月，其实 3 天就行")["days"] == 3


def test_liang_means_two():
    """「俩」= 2。漏掉它时「俩大人一个小孩」只数到「一个小孩」→ 人数记成 1。"""
    assert _slots("我们俩")["party_size"] == 2
    assert _slots("俩大人一个小孩")["party_size"] == 3
    assert _slots("俩大人一个小孩").get("has_children")


def test_friend_pair_is_two():
    """「我和朋友」既没数词也没「人」字，旧逻辑完全抽不到 → 掉进「2 大 1 小」兜底。"""
    assert _slots("我和朋友")["party_size"] == 2
    assert _slots("我跟同学一起去")["party_size"] == 2
    assert _slots("我和朋友")["party_size"] == 2


def test_family_baseline_is_added_to_counted_members():
    """组合语法只数「带数词的类」，「我 / 我老婆 / 父母」这类基点要补上。"""
    assert _slots("我老婆和两个孩子")["party_size"] == 4
    assert _slots("我老婆和孩子")["party_size"] == 3
    assert _slots("老婆孩子")["party_size"] == 3
    assert _slots("我和两个孩子")["party_size"] == 3
    assert _slots("我父母和两个孩子")["party_size"] == 5
    # 成人已带数词时**不补**基点，否则「我和我老婆两个人」会变 4
    assert _slots("我和我老婆两个人")["party_size"] == 2


def test_bare_child_keyword_marks_children():
    """中文裸「孩子」不带数词也要打 has_children（旧代码只查长者词，儿童那条被 elif 吞了）。"""
    s = _slots("带我爸妈和孩子")
    assert s.get("has_elder") and s.get("has_children")


def test_decimal_wan_budget():
    """「1.5万」逐字符状态机遇到「.」会直接返回 None。"""
    assert _slots("预算1.5万")["budget"] == 15000.0
    assert _slots("1.5万")["budget"] == 15000.0


def test_hongkong_taipei_are_destinations():
    """香港 / 台北是中国的城市，此前词典缺失 → 「去香港玩三天」被反问「想去的城市是哪里」。"""
    assert _slots("去香港玩三天")["destination"] == "香港"
    assert _slots("去台北")["destination"] == "台北"


def test_more_delegating_words():
    for w in ["按你说的办", "看着来", "不限预算", "预算不是问题"]:
        assert is_delegating(w), w


def test_foreign_visa_question_goes_to_answer_not_plan(client):
    """外语签证问询不得掉进行程链路。

    真 bug（场景探针实测）：`plan_verbs` 的英文规则里含 `visa`，把「Do I need a visa?」
    直接放行到通用行程 → 出了一份「China Entry & Visa Checklist / 1 天」假行程；
    日文 / 韩文则掉回四连问。这是「把问事实当排行程」的第九形态。
    """
    for i, m in enumerate(["Do I need a visa?", "ビザは必要ですか", "비자가 필요한가요"]):
        body = post_plan(client, m, session_id="visa-f%d" % i)
        assert body["type"] == "answer", (m, body.get("type"))


def test_chinese_visa_question_still_on_visa_chain(client):
    """反向守卫：中文「签证」仍走 visa 场景（不能让知识闸门把它吸走）。"""
    body = post_plan(client, "办日本签证要准备什么", session_id="visa-cn")
    assert body["type"] != "answer", body.get("type")


def test_english_trip_request_still_plans(client):
    """反向守卫：明确要排行程的英文请求不能被知识闸门吞掉（即使句中有 trip/plan）。"""
    body = post_plan(client, "I want to plan a trip to Beijing for 5 days", session_id="visa-en2")
    assert body["type"] == "clarify", body.get("type")
    assert "destination" not in (body.get("missing_slots") or [])


def test_multi_party_size_survives_the_graph(client):
    """图级：多类同行人的合计数要真的进会话槽位。"""
    post_plan(client, "我老婆和两个孩子，去成都玩5天，预算一万", session_id="party-multi")
    slots = client.get("/session/party-multi").json()["slots"]
    assert slots.get("party_size") == 4, slots


def test_japanese_korean_duration_and_party(client):
    """日 / 韩的时长与人数也要抽得到（否则外语用户会被追问已经答过的项）。"""
    ja = post_plan(client, "北京に3日間行きたい、予算は5万円です", session_id="ja1")
    assert ja["type"] == "clarify"
    assert "date_range" not in (ja.get("missing_slots") or []), ja


def test_policy_subject_is_the_policy_name_anywhere_in_the_sentence(client):
    """政策类主体恒为政策名，与它在句中哪个位置无关。

    旧代码只在命中处于**句首**时取命中片段，于是「那单方面免签的国家有哪些」的
    主体被切成前半截「那」（虽然检索仍带命中片段、产出对得上，但展示层很难看）。
    """
    body = post_plan(client, "那单方面免签的国家有哪些", session_id="pol1")
    assert body["type"] == "realtime", body
    assert body["subject"] == "单方面免签", body.get("subject")


# ================================================================ 会话中途变卦（2026-10-06）


def test_fact_question_does_not_rewrite_plan_destination(client):
    """变卦场景：聊着 A 城的行程，中途插一句 B 城景点的事实问 —— 行程目的地**不能**被改。

    真 bug：会话在排「北京 5 天」，用户插一句「外滩几点关门」→ 旧行为把 destination
    改成上海（同城时则改 destination_surface，追问文案变成「已记下目的地 故宫」）。
    一句问句改掉在谈的行程，用户回到行程时发现目的地已经不是原来那个。
    """
    post_plan(client, "我想去北京玩五天，两个人，预算八千", session_id="fk-dest")
    post_plan(client, "外滩几点关门", session_id="fk-dest")
    post_plan(client, "上海博物馆要预约吗", session_id="fk-dest")
    slots = client.get("/session/fk-dest").json()["slots"]
    assert slots.get("destination") == "北京", slots
    assert slots.get("destination_surface") == "北京", slots


def test_inbound_visa_question_is_answered_not_planned(client):
    """变卦场景：排到一半问「外国人需要签证吗」—— 要的是政策答案，不是一份行程。

    真 bug：会话里已有 destination_country（中国）时，裸「签证」让路 → 槽位体检不缺槽
    → 直接出了一份「西安 4 日行程（含来华签证办理要点）」。
    """
    post_plan(client, "我要去西安玩四天", session_id="fk-visa")
    body = post_plan(client, "外国人需要签证吗", session_id="fk-visa")
    assert body["type"] == "answer", body
    # 行程槽位不能被这次问句动过
    slots = client.get("/session/fk-visa").json()["slots"]
    assert slots.get("destination") == "西安", slots


def test_planning_resumes_after_visa_interruption(client):
    """插队问完政策后，「继续吧」要能接回原来的行程（槽位与轮次都没丢）。"""
    post_plan(client, "我要去西安玩四天", session_id="fk-resume")
    post_plan(client, "外国人需要签证吗", session_id="fk-resume")
    body = post_plan(client, "继续吧", session_id="fk-resume")
    assert body["type"] == "clarify", body
    assert set(body.get("missing_slots") or []) == {"budget", "party"}, body


def test_payment_question_reversed_word_order_is_answered(client):
    """「支付怎么弄」的疑问词在主题词**之后**，旧触发词只认反序 → 掉回槽位体检。"""
    body = post_plan(client, "支付怎么弄", session_id="fk-pay")
    assert body["type"] == "answer", body


def test_abandoning_the_trip_is_acknowledged_not_re_asked(client):
    """「算了不去了」原本把同一个四连问原样又刷一遍 —— 要一句收束，不是再问一遍。"""
    post_plan(client, "我想去成都玩", session_id="fk-quit")
    body = post_plan(client, "算了不去了", session_id="fk-quit")
    assert body["type"] == "guide", body
    assert body.get("kind") == "cancel", body
    # 反悔后还能接着规划（槽位没被清掉）
    again = post_plan(client, "还是去吧", session_id="fk-quit")
    assert again["type"] == "clarify", again


def test_cancel_trigger_is_anchored_and_does_not_swallow_a_following_question(client):
    """反向守卫：「算了，先问下签证」不是放弃（后面还有真问题），不能被 cancel 吞掉。"""
    body = post_plan(client, "算了，先问下签证", session_id="fk-quit2")
    assert body.get("type") != "guide" or body.get("kind") != "cancel", body


def test_outbound_visa_questions_are_not_realtime_intents_after_the_inbound_trigger(deps):
    """反向守卫：新增来华签证触发词后，出境问题仍不被实时闸门接管。"""
    from app.intent import detect_realtime_intent

    for m in ["哪些国家对中国免签", "办泰国签证要准备什么", "泰国签证需要什么材料"]:
        assert detect_realtime_intent(m, deps.settings, {}) is None, m


def test_short_question_with_own_topic_is_not_carried_onto_the_previous_subject(client):
    """变卦场景：事实答完之后接一句**自带主题**的短问句，不能被上一轮主体顶掉。

    真 bug：F10 会话「支付怎么弄」答完 → 「景点推荐呢」走在承接兜底里，
    上一轮的主体「支付怎么弄」被整个接了过来 —— 问景点，答的是支付。
    命中片段落在句首时，**有内容的主题名**（景点推荐）就是这一句自带的主体，
    只有**纯功能骨架**（是不是 / 有没有）才算「没给自己主体」、才允许承接。
    """
    post_plan(client, "我想去上海玩五天", session_id="fk-topic")
    post_plan(client, "支付怎么弄", session_id="fk-topic")
    body = post_plan(client, "景点推荐呢", session_id="fk-topic")
    assert body["type"] == "answer", body
    assert body.get("topic") != "支付怎么弄", body
    assert "景点" in (body.get("topic") or ""), body


def test_skeleton_at_sentence_start_still_carries_over(client):
    """反向守卫：句首是**纯功能骨架**时仍要接上一轮主体（「是不是还有一个馆区」）。"""
    post_plan(client, "上海博物馆要预约吗", session_id="fk-skel")
    body = post_plan(client, "是不是还有一个馆区", session_id="fk-skel")
    assert body["type"] == "answer", body
    assert body.get("subject") == "上海博物馆" or body.get("topic") == "上海博物馆", body


# ------------------------------------------------- 会话地点借用（2026-10-06 遗留清理）
def test_session_destination_is_borrowed_as_knowledge_scope(client):
    """会话在聊上海、问「景点推荐呢」—— 这一问自带不了地点，标题与检索都该按上海来。

    真 bug：检索与提示词都拿不到会话地点，答复「问题没有指明具体地点或城市」、hits=0，
    而用户明明刚说了上海。
    """
    post_plan(client, "我想去上海玩五天", session_id="bsc1")
    body = post_plan(client, "景点推荐呢", session_id="bsc1")
    assert body["type"] == "answer", body
    assert (body.get("topic") or "").startswith("上海"), body


def test_alias_place_wins_over_the_session_destination(client):
    """「西湖」是杭州的**别名** —— 会话在北京时问「西湖有多大」，不能被套上北京。

    真 bug：别名解不出地点，于是借用了会话地点，标题成了「北京西湖」。
    """
    from app.knowledge import subject_place

    assert subject_place(get_settings(), "西湖") == "杭州"
    post_plan(client, "我想去北京玩五天", session_id="alias1")
    body = post_plan(client, "西湖有多大", session_id="alias1")
    assert "北京" not in (body.get("topic") or ""), body


def test_trip_reset_clears_the_previous_itinerary(client):
    """撤销当前行程：说过「西安四天」后又说「算了，先问下签证」，不能把 4 天带过去。

    真 bug：旧行为把 4 天当残留槽位注入签证场景，出了一份「西安 4 日行程（含签证清单）」。
    """
    post_plan(client, "我要去西安玩四天", session_id="rst1")
    body = post_plan(client, "算了，先问下签证", session_id="rst1")
    if body.get("type") == "plan":
        days = len((body.get("itinerary") or {}).get("days") or [])
        assert days != 4, body
    snap = client.get("/session/rst1").json().get("slots", {})
    assert snap.get("destination") != "西安", snap


def test_trip_reset_yields_to_delegation(client):
    """反向守卫：「算了，就按你说的办」是**放手**不是撤销 —— 骨架槽位不能被清掉。"""
    post_plan(client, "我要去西安玩四天", session_id="rst2")
    body = post_plan(client, "算了，就按你说的办", session_id="rst2")
    assert body["type"] == "plan", body
    assert (body.get("itinerary") or {}).get("days"), body


def test_is_trip_reset_only_recognises_abandonment_at_the_start():
    """`is_trip_reset` 的边界：只认句首的放弃词，普通问句与**整句放弃**都不算。"""
    from app.slots import clear_trip_slots, is_trip_reset

    assert is_trip_reset("算了，先问下签证")
    assert is_trip_reset("不去了，改去成都")
    assert is_trip_reset("换个地方，去三亚吧")
    assert not is_trip_reset("办日本签证要准备什么")
    assert not is_trip_reset("我想去西安玩四天")
    # 整句就是放弃 → 走寒暄闸门、**不清槽**（用户常接着说「还是去吧」）。
    assert not is_trip_reset("算了不去了")
    assert not is_trip_reset("取消")
    cleared = clear_trip_slots({"destination": "西安", "days": 4, "budget": 8000.0, "note": "keep"})
    assert cleared == {"note": "keep"}, cleared


def test_pure_abandon_keeps_the_slots_for_a_later_comeback(client):
    """整句放弃不清槽：「成都」→「算了不去了」→「还是去吧」，目的地要还在。

    真 bug（2026-10-06 变卦探针实测）：清了之后「还是去吧」时目的地已经丢了。
    """
    post_plan(client, "我想去成都玩", session_id="ab1")
    post_plan(client, "算了不去了", session_id="ab1")
    assert client.get("/session/ab1").json()["slots"].get("destination") == "成都"
    body = post_plan(client, "还是去吧", session_id="ab1")
    assert body["type"] == "clarify", body
    assert "destination" not in (body.get("missing_slots") or []), body


def test_transport_question_is_answered_not_planned(client):
    """「从浦东机场怎么到市区」问的是**怎么走** —— 不该出一份「5 天参考行程」。"""
    post_plan(client, "我想去上海玩五天", session_id="tp1")
    body = post_plan(client, "从浦东机场怎么到市区", session_id="tp1")
    assert body["type"] == "answer", body


def test_stay_duration_question_is_answered_not_planned(client):
    """「免签能待多久」要的是**期限**这一句答案，不是一份签证合规行程稿。"""
    post_plan(client, "我要去上海玩", session_id="sd1")
    body = post_plan(client, "免签能待多久", session_id="sd1")
    assert body["type"] in ("answer", "realtime"), body


# ---------------------------------------------------------------- 市场对标（2026-10-06 第三轮）
# 对标携程 TripGenie / Layla / Mindtrip 反复强调、而我们此前缺的交互模式：
# 节奏与偏好、行程内迭代、雨天备选、多目的地、关联问题推荐。
def test_pace_and_interests_are_extracted():
    """节奏（不想太赶）与偏好（美食/历史）不给数值槽位，却是行程形态的决定因素。

    旧逻辑完全认不出 → 被当「没给信息」掉回缺槽追问（M3/M8）。
    """
    s = _slots("我想去成都玩五天，我不想太赶，喜欢美食和历史")
    assert s["pace"] == "relaxed"
    assert set(s["interests"]) == {"food", "history"}
    assert _slots("想去上海玩五天，节奏紧凑点，多点购物")["pace"] == "packed"
    assert "nature" in _slots("杭州玩三天，想悠闲一点，看看自然风景")["interests"]


def test_bringing_two_kids_counts_the_adult():
    """「带两个小孩」= 我 + 两个小孩 = 3 人。

    旧逻辑只数到小孩（2 人），出稿标题还被模型脑补成「2 大 2 小」（M8）。
    """
    s = _slots("带两个小孩去北京玩四天，别太累")
    assert s["party_size"] == 3, s
    assert s["has_children"] is True


def test_multi_city_keeps_the_second_leg_and_sums_days():
    """「北京玩三天再去上海玩两天」= 北京 + 上海，全 5 天。

    旧逻辑只取首站、天数只剩 3，第二座城市整段蒸发（M6）。
    """
    s = _slots("我想北京玩三天再去上海玩两天")
    assert s["multi_city"] == ["北京", "上海"]
    assert s["destination_secondary"] == "上海"
    assert s["days"] == 5


def test_is_plan_revision_detects_iteration_but_not_facts():
    """迭代判定的正反两面：改稿句要认出来，事实问句不能误判。"""
    from app.slots import is_plan_revision

    for m in ["第3天太紧凑了，放松一点", "把第2天换成博物馆", "第2天下雨有备选吗",
              "住宿换成便宜点的", "预算怎么分配到住宿和吃饭"]:
        assert is_plan_revision(m), m
    for m in ["外滩几点关门", "办日本签证要准备什么", "我想去上海玩五天", "今天天气怎么样"]:
        assert not is_plan_revision(m), m


def test_in_plan_revision_revises_instead_of_re_asking(client):
    """出稿后说「第 3 天太紧凑了」要在原稿上改，**不是**把缺槽追问重刷一遍（M1/M10）。"""
    post_plan(client, "我想去北京玩五天，两个人，预算一万", session_id="rev1")
    body = post_plan(client, "第3天太紧凑了，放松一点", session_id="rev1")
    assert body["type"] == "plan", body
    assert body.get("suggestions"), body
    assert "调整" in body["suggestions"][0], body


def test_revision_works_even_when_skeleton_was_missing(client):
    """会话只说了目的地 + 天数就改稿：「把第2天换成博物馆」应直接出稿，而不是追问预算/人数（M2）。"""
    post_plan(client, "我想去上海玩四天", session_id="rev2")
    body = post_plan(client, "把第2天换成博物馆", session_id="rev2")
    assert body["type"] == "plan", body


def test_rain_backup_is_not_misread_as_live_weather(client):
    """「第2天下雨有备选吗」带行程锚点 → 给那天安排备选，不是查实时天气（M4）。"""
    post_plan(client, "我想去杭州玩三天", session_id="rev3")
    body = post_plan(client, "第2天下雨有备选吗", session_id="rev3")
    assert body["type"] == "plan", body


def test_proceed_phrase_counts_as_delegation():
    """「那按这个来排」是对已说信息点头、让系统继续 —— 与放手等价（M3）。"""
    assert is_delegating("那按这个来排")
    assert is_delegating("就按这个来排")
    assert not is_delegating("我想去上海玩五天")


def test_next_questions_are_offered_after_each_response(client):
    """关联问题推荐（TripGenie 说这条拉高了人均对话轮次）：答完给几条可点的下一步。"""
    plan = post_plan(client, "我想去西安玩四天，预算八千，两个人", session_id="nq1")
    assert plan["type"] == "plan", plan
    assert len(plan.get("next_questions") or []) >= 2, plan
    rt = post_plan(client, "外滩几点关门", session_id="nq2")
    assert rt["type"] == "realtime", rt
    assert len(rt.get("next_questions") or []) >= 1, rt


def test_foreign_day_reference_is_not_read_as_duration():
    """「3日目 / 3일차」是「第 3 天」，不是「3 天」。

    真 bug（2026-10-06 P12）：日文用户说「3日目はきつい」被读成「改成 3 天」，
    把 5 天行程直接砍成 3 天。
    """
    assert "days" not in _slots("3日目はきついので、ゆったりにしてください")
    assert "days" not in _slots("3일차는 힘들어요")
    # 反向：真正的时长写法照旧要能抽到
    assert _slots("北京に5日間行きたい")["days"] == 5
    assert _slots("베이징에서 5일")["days"] == 5


def test_hard_constraints_are_extracted():
    """饮食忌口与行动能力是**硬约束**（Layla 把 dealbreaker 当主输入）。

    旧逻辑完全不认（P15/P16）：素食者 / 腿脚不便根本没进提示词。
    """
    assert _slots("我想去成都玩四天，我是素食者")["constraints"] == ["vegetarian"]
    assert _slots("我想去西安玩五天，我腿脚不太好，不想走太多路")["constraints"] == ["limited_mobility"]
    assert _slots("有清真餐吗")["constraints"] == ["halal"]
    assert "constraints" not in _slots("没有忌口，什么都吃")


def test_multi_city_swap_replaces_the_station_not_the_first_city():
    """「把上海换成杭州」= 北京 + 杭州，首站仍是北京（真 bug 2026-10-06 P20）。"""
    from app.slots import merge_slots

    prior = {
        "destination": "北京", "destination_surface": "北京",
        "destination_aliases": ["北京", "上海"],
        "multi_city": ["北京", "上海"], "destination_secondary": "上海", "days": 5,
    }
    merged = merge_slots(prior, _slots("把上海换成杭州"))
    assert merged["multi_city"] == ["北京", "杭州"], merged
    assert merged["destination"] == "北京", merged
    assert merged["destination_secondary"] == "杭州", merged


def test_flattened_itinerary_output_is_coerced_not_crashed():
    """模型把 Itinerary 字段**摊在顶层**时要能包回去。

    真 bug（2026-10-06 P11 英文实测）：提示词给的是 Itinerary 的 schema、校验用的却是
    包一层的 GeneratorOutput，模型照着提示词写就整句 degraded。
    """
    from app.generate import _coerce_flattened

    flat = {"title": "北京 5 天", "destination": "北京", "days": [{"day": 1, "title": "抵达"}]}
    out = _coerce_flattened(flat)
    assert out is not None and out.itinerary.title == "北京 5 天"
    assert _coerce_flattened({"itinerary": {"title": "x"}}) is None  # 正常形态不接管
    assert _coerce_flattened({"foo": 1}) is None                    # 认不出就不认
    assert _coerce_flattened("not a dict") is None


def test_error_channel_is_registered_in_plan_state():
    """`error` 必须在 PlanState 里注册。

    没注册 → LangGraph 丢弃 `generate_node` 的错误 → `output_node` 取 `state["draft"]`
    抛 KeyError，整条链路以「链路执行失败：KeyError: 'draft'」收场。
    """
    from app.graph import PlanState

    assert "error" in PlanState.__annotations__


def test_generation_failure_degrades_instead_of_crashing():
    """生成失败要落成 degraded，不能崩成 KeyError。"""
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.deps import build_deps
    from app.embed import HashingEmbedder
    from app.fakes import FakeLLM
    from app.llm import LLMError
    from app.main import create_app
    from app.session import SessionStore
    from app.store import InMemoryVectorStore

    class BoomLLM(FakeLLM):
        def complete_json(self, *, role, prompt, schema, context):
            if role == "generator" and context.get("task") not in ("answer", "guide"):
                raise LLMError("boom")
            return super().complete_json(role=role, prompt=prompt, schema=schema, context=context)

    s = get_settings()
    emb = HashingEmbedder(s.embedding.dim)
    deps = build_deps(
        s, embedder=emb, llm=BoomLLM(s),
        store=InMemoryVectorStore.from_seed_dir(s.seed_dir, emb), sessions=SessionStore(),
    )
    with TestClient(create_app(deps)) as c:
        body = post_plan(c, "我想去西安玩四天，预算八千，两个人", session_id="boom1")
    assert body["type"] == "degraded", body
    assert "KeyError" not in (body.get("reason") or ""), body


# ---------------------------------------------------------------- 长会话（2026-10-06 第七轮）
# 一条会话从模糊地点一路问到具体时间细节。把「问」与「改」分开是第一要务：
#   问 → 从已出稿的行程里直接答；改 → 在原稿上迭代。混了就会「问一句被重排整份稿」。
def test_adequacy_question_does_not_override_days():
    """「长城一天够吗」里的「一天」是被讨论的对象，不是这趟行程的长度。

    旧行为把 days 从 5 改成 1，之后所有迭代都在 1 天稿上操作（长会话 B7 连锁污染）。
    但「我去北京玩三天，钱够吗」的 3 天是**真天数**，不能删。
    """
    assert "days" not in _slots("长城一天够吗")
    assert "days" not in _slots("三天够不够玩")
    assert "days" not in _slots("够玩三天吗")
    assert _slots("我去北京玩三天，钱够吗")["days"] == 3


def test_change_target_budget_is_recognised():
    """「预算加到一万五」给的是**目标值**（改完是一万五），既没有「预算 X」语序、也没有量词。"""
    assert _slots("预算加到一万五")["budget"] == 15000.0
    assert _slots("提到两万")["budget"] == 20000.0
    assert _slots("改成八千")["budget"] == 8000.0


def test_english_budget_units_are_case_insensitive():
    """英文预算单位大小写混写（RMB / Yuan）+ 夹词（around）都要认。

    旧正则只写小写 `rmb` 且要求数字紧跟关键词 →「budget around 10000 RMB」整条漏掉，
    系统一路「还差预算」（长会话 A4）。
    """
    assert _slots("budget around 10000 RMB")["budget"] == 10000.0
    assert _slots("10000 RMB")["budget"] == 10000.0
    assert _slots("budget 8000")["budget"] == 8000.0


def test_cheaper_hotel_is_a_revision_not_a_budget_adjust():
    """「Change the hotel to something cheaper」是改住宿，不是「调整预算」。

    英文要素词 hotel 不在行程要素表里 → 判据②拿不到要素词 → 掉进预算调整被追问目标预算
    （长会话 C2；连我们自己的关联问题模板「Switch to cheaper hotels」也中招）。
    """
    from app.slots import is_plan_revision

    for m in ["Change the hotel to something cheaper", "Switch to cheaper hotels"]:
        assert is_plan_revision(m), m


def test_itinerary_question_detection():
    """「问第 N 天」与「改第 N 天」要分开。"""
    from app.slots import is_itinerary_question

    for m in ["那第三天上午安排什么", "第一天几点开始比较合适",
              "第 2 天做什么", "What is planned for day 3 morning"]:
        assert is_itinerary_question(m), m
    # 反过来：改稿 / 雨天备选 / 含修改动词的都不是「问」
    for m in ["第 2 天太满了，放松点", "把第 2 天换成博物馆",
              "第 2 天下雨有备选吗", "Make day 3 more relaxed"]:
        assert not is_itinerary_question(m), m


def test_answer_from_itinerary_formats_the_day():
    """从已出稿的行程里取第 N 天，直接拼成答复（确定性，不调模型）。"""
    from app.slots import answer_from_itinerary

    itin = {"days": [
        {"day": 1, "theme": "抵达", "activities": [
            {"time": "09:00", "name": "故宫"}, {"time": "14:00", "name": "天坛"}]},
        {"day": 2, "theme": "长城", "activities": [{"time": "08:00", "name": "八达岭"}]},
    ]}
    ans = answer_from_itinerary("第 1 天几点开始比较合适", itin, "zh")
    assert ans and "09:00" in ans and "14:00" in ans and "故宫" in ans, ans
    day2 = answer_from_itinerary("那第 2 天安排什么", itin, "zh")
    assert day2 and "八达岭" in day2, day2
    # 稿子没有那一天 → 返回 None（调用方退回正常链路，不制造空答案）
    assert answer_from_itinerary("第 9 天呢", itin, "zh") is None
    assert answer_from_itinerary("第 2 天安排什么", None, "zh") is None


def test_itinerary_question_is_answered_from_the_plan(client):
    """出稿后问「第 2 天安排什么」→ 出 **answer**（从稿子里答），不是重排一份行程。"""
    post_plan(client, "我想去北京玩五天，两个人，预算一万", session_id="iq1")
    body = post_plan(client, "那第 2 天上午安排什么", session_id="iq1")
    assert body["type"] == "answer", body
    assert (body.get("answer") or "").strip(), body


def test_itinerary_answer_channel_is_registered():
    """`itinerary_answer` 必须在 PlanState 里注册 —— 否则写进去的确定性答复会被
    LangGraph 静默丢弃，`knowledge_node` 短路后拿不到答案。"""
    from app.graph import PlanState

    assert "itinerary_answer" in PlanState.__annotations__


def test_remove_day_is_reconciled_in_code():
    """「删掉第 N 天」如果模型没缩数组，代码替它缩并重排 —— 中英一视同仁。

    模型默认只改那天的内容 / 只改标题，`days` 原样留着：用户说「去掉第 4 天」而行程仍是
    5 天（长会话 C1；中文偶尔跟、英文基本不跟）。天数变化是确定性可算的。
    """
    from app.generate import _reconcile_revision_days
    from app.schemas import Itinerary

    def mk(n):
        return Itinerary(title="t", destination="北京",
                         days=[{"day": i, "theme": f"d{i}", "activities": []} for i in range(1, n + 1)])

    prev = {"days": [{"day": i} for i in range(1, 6)]}
    it = mk(5)
    _reconcile_revision_days(it, revision_note="Remove day 4", previous_itinerary=prev)
    assert [d.day for d in it.days] == [1, 2, 3, 4], it.days
    it = mk(5)
    _reconcile_revision_days(it, revision_note="去掉第 4 天", previous_itinerary=prev)
    assert [d.day for d in it.days] == [1, 2, 3, 4], it.days
    # 已经缩过 / 不是删天 → 绝不能动手
    it = mk(4)
    _reconcile_revision_days(it, revision_note="Remove day 4", previous_itinerary=prev)
    assert [d.day for d in it.days] == [1, 2, 3, 4]
    it = mk(5)
    _reconcile_revision_days(it, revision_note="第 3 天放松点", previous_itinerary=prev)
    assert [d.day for d in it.days] == [1, 2, 3, 4, 5]


def test_itinerary_recall_detection():
    """「把之前的行程再给我看看」= 重看，不是改稿、也不是缺槽（长会话 E1）。"""
    from app.slots import is_itinerary_recall

    for m in ["北京的行程再给我看看", "把行程再给我看看", "行程再看一下",
              "把之前的方案发我一下", "行程给我一份",
              "show me my itinerary", "Can you show me the plan",
              "What is my itinerary", "remind me of the itinerary"]:
        assert is_itinerary_recall(m), m
    # 不能误吃：只问事实 / 只补槽 / 改稿 / 问第 N 天
    for m in ["帮我看看签证", "再看看", "把第 3 天的行程换成博物馆",
              "把第 4 天去掉", "帮我加一天", "西安四天",
              "What is planned for day 3 morning", "Remove day 4"]:
        assert not is_itinerary_recall(m), m


def test_recall_replays_the_previous_plan_without_regenerating(client):
    """「北京的行程再给我看看」→ 原样重放上一版（type=plan，标题与天数一致）。

    既不是四连问，也不重新排一份 —— 用户要的是"刚才那份"。
    """
    first = post_plan(client, "我想去北京玩五天，两个人，预算一万", session_id="rc1")
    assert first["type"] == "plan", first
    again = post_plan(client, "北京的行程再给我看看", session_id="rc1")
    assert again["type"] == "plan", again
    assert again["itinerary"]["title"] == first["itinerary"]["title"], again
    assert len(again["itinerary"]["days"]) == len(first["itinerary"]["days"]), again
    assert again["suggestions"], again  # 首条是「这是最近这一版」的说明


def test_recall_without_a_plan_answers_honestly(client):
    """没出过稿就要求看行程 → 如实说（answer），**不是**四连问。"""
    assert post_plan(client, "我想去北京玩", session_id="rc2")["type"] == "clarify"
    body = post_plan(client, "北京的行程再给我看看", session_id="rc2")
    assert body["type"] == "answer", body
    assert (body.get("answer") or "").strip(), body


def test_recall_of_a_different_city_does_not_replay(client):
    """点名另一个城市的行程 → 不重放北京那份（避免张冠李戴）。"""
    post_plan(client, "我想去北京玩五天，两个人，预算一万", session_id="rc3")
    body = post_plan(client, "上海的行程再给我看看", session_id="rc3")
    assert body["type"] == "answer", body


def test_session_slots_days_follow_the_itinerary(client):
    """迭代改了天数（「去掉第 4 天」）后，会话槽位的 days 要跟着稿子走（长会话 E2）。

    不跟的话，下一句「再加一天」会按旧的 5 天算。
    """
    post_plan(client, "我想去北京玩五天，两个人，预算一万", session_id="ds1")
    body = post_plan(client, "去掉第 4 天", session_id="ds1")
    assert body["type"] == "plan", body
    n = len(body["itinerary"]["days"])
    snap = client.get("/session/ds1").json()
    assert snap["slots"]["days"] == n, snap


def test_english_question_fragment_is_not_glued_onto_the_place():
    """英文疑问碎片不能和地名无空格拼成「北京How long」（长会话 B2/C3）。"""
    from app.knowledge import display_topic

    assert display_topic("How long", "How long from the airport to downtown",
                         scope="北京") == "北京"
    assert display_topic("What time does the", "What time does the Forbidden City open",
                         scope="北京") == "北京"
    # 真正的专名仍保留，但用空格隔开
    assert display_topic("Forbidden City", "When does the Forbidden City open",
                         scope="北京") == "北京 Forbidden City"
    # 中文路径完全不变
    assert display_topic("上海", "上海有没有免费的博物馆", "有没有") == "上海博物馆"


# ---------------------------------------------------------------- 文化话题
CULTURE_QUESTIONS = [
    ("我第一次来中国，吃饭有什么礼仪要注意吗", ""),
    # 首字是「那」= 承接上一轮的追问，必须带上会话承接来的主体才会命中。
    ("那有什么禁忌吗", "中国饭桌礼仪"),
    ("为什么中国人不喜欢数字4", ""),
    ("中国的小费文化是怎样的", ""),
    ("进寺庙要注意什么", ""),
    ("春节期间去北京有什么特别的习俗", ""),
    ("What are some Chinese dining etiquette rules I should know?", ""),
    ("Should I tip in restaurants?", ""),
    ("Any taboos about giving gifts in China?", ""),
    ("What are the customs during Chinese New Year?", ""),
    ("中国の食事マナーを教えてください", ""),
    ("중국에서 팁 문화는 어떻게 되나요?", ""),
    ("旧正月（春節）の習慣を教えてください", ""),
]


def test_culture_questions_are_routed_to_the_fact_gate():
    """文化类问题（中/英/日/韩）必须进事实闸门，不能掉进槽位体检回四连问。

    真 bug（2026-10-06 文化探针实测）：文化词**一个都不在触发词里** →
    中文「中国的小费文化是怎样的」「进寺庙要注意什么」回四连问；英文 8 题 5 题回四连问；
    日 / 韩 4/4 全回四连问。
    """
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    for msg, hint in CULTURE_QUESTIONS:
        ki = detect_knowledge_intent(
            msg, settings, extract_slots(msg, settings), hint_subject=hint
        )
        assert ki is not None, msg


def test_culture_fact_question_never_becomes_a_plan(client):
    """会话已有完整行程时，插一句文化问句**不得重排一份新稿**。

    真 bug（2026-10-06 文化探针实测）：「What are the customs during Chinese New Year?」没被
    任何闸门接住，而槽位已齐 → 直接又出了一份 3 天行程（问习俗，给行程）。
    """
    first = post_plan(client, "我想去北京玩五天，两个人，预算一万", session_id="cf1")
    assert first["type"] == "plan", first
    body = post_plan(client, "春节期间有什么特别的习俗", session_id="cf1")
    assert body["type"] == "answer", body


def test_culture_trip_request_still_plans(client):
    """「体验茶文化」是**排行程**诉求，不能被文化触发词抢走。"""
    body = post_plan(client, "我想去杭州体验茶文化，玩三天，两个人，预算六千", session_id="cp1")
    assert body["type"] == "plan", body


def test_subject_is_not_the_interrogative_word():
    """句首命中疑问骨架时，主体取**骨架之后**那一截，而不是骨架本身。

    真 bug：命中处于句首时一律拿命中片段当主体 →「为什么中国人不喜欢数字4」的主体/标题
    成了「为什么」；「What is the tipping culture in China?」成了「What is」。
    """
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    zh = detect_knowledge_intent("为什么中国人不喜欢数字4", settings, {})
    assert zh is not None and zh.subject != "为什么", zh
    en = detect_knowledge_intent("What is the tipping culture in China?", settings, {})
    assert en is not None and en.subject.strip().lower() not in {"what is", "what"}, en


def test_english_topic_word_is_the_subject_not_a_truncated_fragment():
    """英文的主题在**命中片段本身**上，切前半截会断在词中间（「some Ch」）。"""
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    ki = detect_knowledge_intent(
        "What are some Chinese dining etiquette rules I should know?", settings, {}
    )
    assert ki is not None and ki.subject == "etiquette", ki


# ---------------------------------------------------------------- 日常功能话题
# 来华旅客落地后最先要解决的一批：地铁怎么坐 / 手机怎么支付 / 打车用什么 / 高铁怎么买票 /
# 共享单车怎么租 / 手机没网怎么办。真 bug（2026-10-06 日常功能探针实测）：其中 4 题
# **四道闸门全灭**（打车 / 高铁买票 / 共享单车 / 没网）→ 掉回槽位体检被回四连问。
DAILY_QUESTIONS = [
    "北京地铁怎么坐",
    "中国地铁怎么买票",
    "怎么用支付宝扫码坐地铁",
    "在中国怎么用手机支付",
    "微信支付怎么绑定外国银行卡",
    "打车用哪个软件",
    "中国高铁怎么买票",
    "怎么租共享单车",
    "手机没网怎么办",
    "在中国怎么上网",
    "外国人怎么办手机卡",
    "地铁末班车几点",
    "How do I take the metro in Beijing?",
    "Which app should I use for a taxi in China?",
    "How do I scan a QR code to pay?",
    "北京の地下鉄はどう乗りますか",
    "中国のスマホ決済はどう使いますか",
    "중국에서 지하철은 어떻게 타나요?",
    # 韩文支付问句原先掉四连问（触发词里没有 결제 / 決済）
    "중국에서 휴대폰 결제는 어떻게 하나요?",
]


def test_daily_function_questions_are_routed_to_the_fact_gate():
    """地铁 / 支付 / 打车 / 单车 / 高铁 / 没网 这类日常功能问句必须进事实闸门。"""
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    for msg in DAILY_QUESTIONS:
        ki = detect_knowledge_intent(
            msg, settings, extract_slots(msg, settings)
        )
        assert ki is not None, msg


def test_daily_function_trip_request_still_plans(client):
    """「规划一条地铁沿线三日行程」是**排行程**诉求，不能被出行触发词抢走。"""
    body = post_plan(
        client, "帮我规划一条北京地铁沿线三日行程，两个人，预算六千", session_id="df1"
    )
    assert body["type"] == "plan", body


# ---------------------------------------------------------------- 卡片标题裁剪（收遗留①②）
def test_long_chinese_question_is_trimmed_to_the_topic():
    """中文问句前垫的个人背景不该进卡片标题（真 bug：标签成了「我第一次来中国，吃饭礼仪」）。"""
    from app.intent import detect_knowledge_intent
    from app.knowledge import display_topic

    settings = get_settings()
    for msg, want in [
        ("我第一次来中国，吃饭有什么礼仪要注意吗", "吃饭礼仪"),
        ("我是外国人，在中国用手机支付怎么弄", "手机支付"),
        ("进寺庙要注意什么", "寺庙"),
    ]:
        ki = detect_knowledge_intent(msg, settings, extract_slots(msg, settings))
        assert ki is not None, msg
        topic = display_topic(ki.subject, msg, ki.matched)
        assert topic == want, (msg, topic)


def test_followup_label_drops_the_leading_verb():
    """追问标签别把前半截的光杆动词带进来（真 bug：标签成了「那去餐厅要给小费」）。"""
    from app.intent import detect_knowledge_intent
    from app.knowledge import display_topic

    settings = get_settings()
    msg = "那去餐厅要给小费吗"
    ki = detect_knowledge_intent(msg, settings, {}, hint_subject="中国小费文化")
    assert ki is not None, msg
    topic = display_topic(ki.subject, msg, ki.matched)
    assert topic == "餐厅小费", topic


def test_focus_ignores_filler_adverbs():
    """焦点别取到「一般 / 通常」这类填充副词（真 bug：标签「末班车一般到」）。"""
    from app.intent import detect_knowledge_intent
    from app.knowledge import display_topic

    settings = get_settings()
    msg = "末班车一般到几点"
    ki = detect_knowledge_intent(msg, settings, extract_slots(msg, settings))
    assert ki is not None, msg
    assert display_topic(ki.subject, msg, ki.matched) == "末班车"


def test_glue_spaces_han_latin_but_not_kana():
    """汉字 ↔ 拉丁才补空格；日文假名不该被当成拉丁（真 bug：「北京の 地下鉄」）。"""
    from app.knowledge import display_topic

    assert display_topic("Forbidden City", "When does the Forbidden City open",
                         scope="北京") == "北京 Forbidden City"
    assert display_topic("北京の", "北京の地下鉄はどう乗りますか", "地下鉄") == "北京の地下鉄"


def test_preamble_trim_never_eats_a_real_topic():
    """裁剪只对**铺垫**生效：主体本身不能变（「上海外卡支付」「上海博物馆」照旧）。"""
    from app.intent import trim_topic_preamble

    for src, want in [
        ("上海", "上海"),
        ("杭州西湖", "杭州西湖"),
        ("上海博物馆", "上海博物馆"),
        ("共享单车", "共享单车"),
        ("手机支付", "手机支付"),
        ("离境退税", "离境退税"),
        ("打车", "打车"),
        ("来华", "来华"),
    ]:
        assert trim_topic_preamble(src) == want, src
    # 整句都是铺垫 → 剥空（调用方自会退回命中片段）
    assert trim_topic_preamble("怎么用") == ""


# ---------------------------------------------------------------- 关联问题个性化（收遗留③）
def test_next_questions_use_session_slots():
    """建议栏要**跟着会话槽位变**：天数进「把第 N 天…」、目的地进「X 附近…」。

    真 bug（收遗留③）：模板写死「把第 3 天…」「附近还有哪些值得去」，
    同一会话连问几轮建议一字不差，用户看不出系统记住了什么。
    """
    from app.followups import next_questions

    three = next_questions(decision="plan", slots={"destination": "杭州", "days": 3})
    five = next_questions(decision="plan", slots={"destination": "西安", "days": 5})
    assert three[0] == "把第 3 天安排得轻松一点", three
    assert five[0] == "把第 5 天安排得轻松一点", five

    with_place = next_questions(decision="answer", slots={"destination": "北京"})
    assert with_place[1] == "北京附近还有哪些值得去", with_place
    without = next_questions(decision="answer", slots={})
    assert without[1] == "附近还有哪些值得去", without
    # 日 / 韩也要个性化，不能只改中文
    assert "北京" in next_questions(decision="answer", language="ja",
                                   slots={"destination": "北京"})[1]
    assert "Beijing" in next_questions(decision="answer", language="en",
                                       slots={"destination": "Beijing"})[1]


def test_next_questions_are_personalized_end_to_end(client):
    """整链验证：出稿后建议栏里的天数 = 这一版稿子的天数（不是写死的 3）。"""
    body = post_plan(client, "我想去北京玩五天，两个人，预算一万", session_id="nq1")
    assert body["type"] == "plan", body
    n = len(body["itinerary"]["days"])
    assert body["next_questions"][0].startswith(f"把第 {n} 天"), body["next_questions"]


def test_next_questions_follow_the_companions():
    """同行人进了槽位，行程稿的第一条建议就要照顾到 —— 带孩子 / 带长者各一套。

    真缺口（收遗留③·续）：槽位里明明有 `has_children`，建议栏还在劝「把第 N 天安排得
    轻松一点」；三代同游时**儿童节奏是更硬的约束**，孩子优先。
    """
    from app.followups import next_questions

    base = next_questions(decision="plan", slots={"destination": "杭州", "days": 4})
    assert base[0] == "把第 4 天安排得轻松一点", base

    kids = next_questions(decision="plan",
                          slots={"destination": "杭州", "days": 4, "has_children": True})
    assert kids[0] == "把第 4 天安排得适合孩子", kids
    elder = next_questions(decision="plan",
                           slots={"destination": "西安", "days": 5, "has_elder": True})
    assert elder[0] == "把第 5 天安排得轻松些、少走路", elder
    # 孩子 + 长者同时带 → 孩子优先
    both = next_questions(decision="plan", slots={"destination": "西安", "days": 5,
                                                  "has_children": True, "has_elder": True})
    assert both[0] == kids[0].replace("4", "5"), both
    # 只换一条，后面两条不塌
    assert kids[1:] == base[1:], kids


def test_next_questions_follow_the_interests():
    """会话里出现过的兴趣，要换掉常识答复里那条泛推荐（「附近还有哪些值得去」）。

    真缺口（收遗留③·续）：用户在会话里说了「想吃本地菜」，建议栏还是那句万金油。
    只在**有目的地**时启用 —— 没地名时缺主语，不如给通用文案。
    """
    from app.followups import next_questions

    food = next_questions(decision="answer", slots={"destination": "成都", "interests": ["food"]})
    assert food[-1] == "成都有哪些必吃的本地菜", food
    assert food[0] == "按这个帮我排进行程", food          # 前面那条不动

    # 多兴趣取词典里最靠前的（food 在 history 之前）
    multi = next_questions(decision="answer",
                           slots={"destination": "西安", "interests": ["food", "history"]})
    assert multi[-1] == "西安有哪些必吃的本地菜", multi

    # 没目的地 / 没兴趣 / 认不出的兴趣 → 一条都不换
    assert next_questions(decision="answer",
                          slots={"interests": ["food"]})[-1] == "附近还有哪些值得去"
    assert next_questions(decision="answer",
                          slots={"destination": "北京"})[-1] == "北京附近还有哪些值得去"
    assert next_questions(decision="answer",
                          slots={"destination": "北京", "interests": ["书法"]})[-1] \
        == "北京附近还有哪些值得去"

    # 四语言都要换，不能只改中文
    assert "写真" in next_questions(decision="answer", language="ja",
                                    slots={"destination": "北京", "interests": ["photo"]})[-1]
    assert "Must-try" in next_questions(decision="answer", language="en",
                                        slots={"destination": "Chengdu",
                                               "interests": ["food"]})[-1]

# ---------------------------------------------------------------- 交通情况（第十轮）
# 游客不会问「怎么坐地铁」就完事 —— 紧接着一定是**什么时候最挤 / 哪种方式更值 /
# 这个景点几点去人才少**。真 bug（2026-10-06 交通探针实测）：
#   ① 「国庆期间景区人流量大吗」被**上网**那条里的「流量」先命中，标题成了联网问题；
#   ② 英文「When is the least busy time to visit?」被**实时**闸门的 `when is` 抢走，
#      回了一条「我不知道今天的实时情况」而不是常识答案；
#   ③ 上一轮韩文结转下来的主体被下一句**日文**追问原样承接，整段答案写成韩文。
TRAFFIC_QUESTIONS = [
    ("北京地铁早高峰一般是几点到几点？", ""),
    ("那晚高峰呢", "北京地铁早高峰"),
    ("从首都机场到市区，打车方便还是坐地铁方便？", ""),
    ("那几点出发能避开堵车？", "从首都机场到市区"),
    ("故宫一般什么时候人最多？", ""),
    ("那几点去人会少一点？", "故宫"),
    ("国庆期间景区人流量大吗？", ""),
    ("上海地铁最挤的是哪条线？", ""),
    ("杭州西湖周末人多吗？", ""),
    ("打车高峰时段会加价吗？", ""),
    ("What are the rush hour times on the Beijing subway?", ""),
    ("How crowded is the Forbidden City in October?", ""),
    ("When is the least busy time to visit?", "Forbidden City"),
    ("北京の地下鉄のラッシュアワーは何時ですか？", ""),
    ("베이징 지하철 혼잡 시간대는 언제인가요?", ""),
    ("観光地は週末混みますか？", ""),
]


def test_traffic_condition_questions_are_routed_to_the_fact_gate():
    """高峰时段 / 人流量 / 推荐交通方式 全部要走常识闸门（不能被实时或排行程抢走）。"""
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    for msg, hint in TRAFFIC_QUESTIONS:
        ki = detect_knowledge_intent(
            msg, settings, extract_slots(msg, settings), hint_subject=hint
        )
        assert ki is not None, msg


def test_crowd_level_question_is_not_stolen_by_the_network_trigger():
    """「人流量」不能被上网那条里的「流量」先命中（标题会变成 Wi-Fi/上网问题）。"""
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    msg = "国庆期间景区人流量大吗？"
    ki = detect_knowledge_intent(msg, settings, extract_slots(msg, settings))
    assert ki is not None, msg
    assert ki.matched != "流量", ki
    assert "人流量" in ki.matched or "流" in ki.matched, ki


def test_busy_time_question_is_not_stolen_by_the_realtime_gate():
    """「什么时候人少」是**常识**，不是当日实时状态 —— 实时闸门不得接手。"""
    from app.intent import detect_realtime_intent

    settings = get_settings()
    for msg in [
        "When is the least busy time to visit?",
        "When is the least busy time to visit the Forbidden City?",
    ]:
        assert detect_realtime_intent(msg, settings, {}) is None, msg
    # 真正的时刻/日程类实时问句照旧要命中，别把这条改坏
    for msg in ["When does the museum open?", "What time is the sunset today?"]:
        assert detect_realtime_intent(msg, settings, {}) is not None, msg


def test_cross_language_followup_does_not_inherit_the_previous_script():
    """上一句是韩文、这一句是日文时，不能把韩文主体原样结转（整段答案会写成韩文）。"""
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    ki = detect_knowledge_intent(
        "観光地は週末混みますか？", settings, {}, hint_subject="지하철"
    )
    assert ki is not None
    assert "지하철" not in (ki.subject or ""), ki
    assert "観光地" in ki.subject, ki


def test_traffic_trip_request_still_plans():
    """「帮我规划一条避开早晚高峰的三天上海行程」是**排行程**，不被高峰词抢走。"""
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    msg = "帮我规划一条避开早晚高峰的三天上海行程"
    ki = detect_knowledge_intent(msg, settings, extract_slots(msg, settings))
    assert ki is None, ki


def test_comparison_question_label_stops_at_the_choice():
    """「打车方便还是坐地铁方便」的标签要停在第一个分句，不能拖出半截 predicate。"""
    from app.intent import detect_knowledge_intent
    from app.knowledge import display_topic

    settings = get_settings()
    msg = "从首都机场到市区，打车方便还是坐地铁方便？"
    ki = detect_knowledge_intent(msg, settings, extract_slots(msg, settings))
    assert ki is not None, msg
    topic = display_topic(ki.subject, msg, ki.matched)
    assert "还是" not in topic and "方便" not in topic, topic


def test_english_traffic_label_uses_the_proper_noun_not_the_generic_word():
    """英文标题别落成**单个泛词**，也别落成**单个地名**。

    两侧都是实测出来的（2026-10-06）：
    · `crowded` 是**属性**（「How crowded **is** the Forbidden City」问的是故宫），
      单独当焦点就成了「Forbidden City crowded」—— 判据是「单词 + 后紧跟系动词」。
    · `subway` 是**对象**，这时标题该是「地点 + 对象」。这条断言在 2026-10-06 菜品轮
      被**有意放宽**：原写死 `== "Beijing"`，但那正是用户报的另一半问题 —— 英文标题
      一律落成单个地点单词（「Chengdu」「China」「Beijing」），看不出这一问在聊什么。
      放宽后「Beijing subway」保留了地点，信息量严格大于「Beijing」。
    """
    from app.knowledge import display_topic

    assert (
        display_topic("crowded", "How crowded is the Forbidden City in October?")
        == "Forbidden City"
    )
    assert (
        display_topic("subway", "What are the rush hour times on the Beijing subway?")
        == "Beijing subway"
    )
    # 有会话地点时地点优先，这条救援逻辑不介入
    assert (
        display_topic("crowded", "How crowded is it?", scope="北京") == "北京"
    )


def test_chinese_subject_has_no_stray_space():
    """中文标题里不该出现空格（骨架替换留下的痕迹，真 bug「那 出发能避开」）。"""
    from app.knowledge import display_topic

    assert " " not in display_topic("那 出发能避开", "那几点出发能避开堵车？", "堵车")

def test_filler_adverbs_are_not_retrieval_terms():
    """检索侧的实词也要摘掉「一般 / 通常」——它们会跨条目命中把无关条目顶上来。

    真 bug（2026-10-06 交通探针 + 真浏览器复核同时看见）：「故宫一般什么时候人最多？」
    的相关条目列了 3 条，多出来的是「银行卡手续费（一般 1%-3%）」和「SIM 实名制
    （一般需要）」；去掉句里的「一般」只剩 1 条。与展示层 `_DISPLAY_ADVERB` 同一个病。
    """
    from app.knowledge import _query_terms

    terms = _query_terms("故宫一般什么时候人最多？", {"故宫"})
    assert not any("一般" in t for t in terms), terms
    # 真内容词照旧要留下
    assert any("时候" in t or "最多" in t for t in terms), terms


def test_diet_questions_keep_their_own_subject():
    """「清真餐厅好找吗」必须保住自己的主体 —— 不能被上一轮的「对花生过敏」盖掉。

    真 bug（2026-10-06 菜品探针 C3）：这一句原先**一个触发词都不命中** → 走了 followup
    承接兜底 → 主体被上一轮盖掉，答案通篇还在讲花生过敏 —— 用户问清真，得到的是花生。
    **这才是补触发词的核心收益**：不是"能不能答"，而是保住这一句自带的主体。
    """
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    ki = detect_knowledge_intent(
        "清真餐厅好找吗？", settings, {}, hint_subject="对花生过敏"
    )
    assert ki is not None
    assert "花生" not in (ki.subject or ""), ki
    assert "清真" in ki.subject, ki


def test_how_to_eat_a_dish_keeps_the_dish_as_subject():
    """「小笼包怎么吃才不会被烫到」自带主体 —— 标题不能被上一轮的城市盖掉。

    原 title 恒为上一轮的「上海」，用户连问三句看到的都是同一张卡。
    """
    from app.intent import detect_knowledge_intent
    from app.knowledge import display_topic

    settings = get_settings()
    msg = "小笼包怎么吃才不会被烫到？"
    ki = detect_knowledge_intent(msg, settings, {}, hint_subject="上海")
    assert ki is not None, msg
    assert "上海" not in (ki.subject or ""), ki
    topic = display_topic(ki.subject, msg, ki.matched)
    assert topic.startswith("小笼包"), topic


def test_foreign_food_questions_reach_the_knowledge_gate():
    """英文 / 日文 / 韩文的菜品与饮食限制问句原先四连问（四道闸门全灭）。"""
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    for msg in [
        "What local dishes should I try in Chengdu?",
        "I don't eat pork — can I still find things to eat in China?",
        "How do I read a Chinese menu and order food?",
        "I'm vegetarian. Is that a problem in China?",
        "四川料理はとても辛いですか？",
        "北京で有名な料理を教えてください",
        "중국 음식은 다 매운가요?",
        "채식주의자인데 중국에서 먹을 수 있는 게 있나요?",
    ]:
        ki = detect_knowledge_intent(msg, settings, {})
        assert ki is not None, msg


def test_food_words_do_not_steal_a_planning_request():
    """「帮我安排一条成都三天的美食行程」是**排行程**，美食词不得抢走。

    plan_verbs 在 triggers 之前跑；补菜品触发词时必须复验这条没被顺手吃掉。
    """
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    for msg in [
        "帮我安排一条成都三天的美食行程",
        "推荐一下上海的美食路线，玩三天",
    ]:
        ki = detect_knowledge_intent(msg, settings, extract_slots(msg, settings))
        assert ki is None, (msg, ki)


def test_food_labels_are_not_half_judgements():
    """「川菜是不是都很辣」的标签是「川菜」，不是半截判断句「川菜都很辣」。"""
    from app.intent import detect_knowledge_intent
    from app.knowledge import display_topic

    settings = get_settings()
    msg = "川菜是不是都很辣？"
    ki = detect_knowledge_intent(msg, settings, {})
    assert ki is not None, msg
    topic = display_topic(ki.subject, msg, ki.matched)
    assert topic == "川菜", topic


def test_short_focus_is_not_glued_onto_the_subject():
    """评价词摘完后焦点只剩一个字就别用 —— 「素食者 + 饭」拼不成「素食者饭」。"""
    from app.intent import detect_knowledge_intent
    from app.knowledge import display_topic

    settings = get_settings()
    msg = "我是素食者，在中国吃饭方便吗？"
    ki = detect_knowledge_intent(msg, settings, {})
    assert ki is not None, msg
    topic = display_topic(ki.subject, msg, ki.matched)
    assert topic == "素食者", topic


def test_demonstrative_before_a_counter_is_not_a_discourse_marker():
    """「这**道**菜里有没有猪肉」的「这」是指示代词的一部分，剥掉就成了「道菜里猪肉」。"""
    from app.intent import trim_topic_preamble

    assert trim_topic_preamble("这道菜里") == "这道菜里"
    assert trim_topic_preamble("那家店") == "那家店"
    # 「那 + 动词」的承接标记照旧要剥
    assert trim_topic_preamble("那去餐厅") == "餐厅"


def test_focus_stripping_must_not_invent_a_word():
    """剥前缀必须切在原话里连续的一段上 —— 「特色菜」不许被切成「色菜」。"""
    from app.knowledge import display_topic

    topic = display_topic("上海", "上海有什么值得吃的特色菜？", "有什么值得")
    assert topic == "上海特色菜", topic


# ============================================================ 饮食 / 跨语言检索
# 这一组守 2026-10-06 菜品探针那一轮的三类缺陷：
#   ① 英文虚词在中文正文里靠**英文专名的子串**命中（签证 / 支付被列进「相关条目」）；
#   ② 种子是中文、检索是字面匹配，外国人用母语问就永远「本库没有相关材料」；
#   ③ 找店类问句的主体被切成前半截疑问骨架，只能跟着上一轮话题走。


def _hits_for(store, message: str):
    from app.config import get_settings
    from app.intent import detect_knowledge_intent
    from app.knowledge import search_knowledge

    settings = get_settings()
    ki = detect_knowledge_intent(message, settings, None)
    if ki is None:
        return None, []
    hits = search_knowledge(
        settings=settings, store=store, intent=ki, message=message, limit=3
    )
    return ki, [c.chunk_id for c in hits]


def test_english_stopwords_do_not_hit_latin_substrings_in_chinese_text(store):
    """`is` / `in` 不能靠 `Visa`、`visaforchina.cn` 的子串命中。

    修前：英文问句抽出的实词只剩虚词，而中文条目里的英文专名正好含这些子串 ——
    「vegetarian 在中国方便吗」的相关条目列出了**签证办理流程**与**外卡支付**。
    卡片叫「相关条目」，列签证比列空更糟。
    """
    _ki, ids = _hits_for(store, "I'm vegetarian. Is that a problem in China?")
    assert any(i.startswith("gen-in-food") for i in ids), ids
    assert not any(i.startswith(("vis-", "gen-in-pay")) for i in ids), ids


def test_latin_terms_are_matched_whole_word(store):
    """拉丁实词按**整词**匹配：`china` 不该命中 `visaforchina.cn`。"""
    from app.knowledge import _term_count

    assert _term_count("在中国签证申请服务中心（visaforchina.cn）在线填写", "china") == 0
    assert _term_count("Visa、Mastercard 或银联标识的 POS 终端", "visa") == 1
    assert _term_count("可刷境外卡", "is") == 0


def test_foreign_language_questions_reach_the_chinese_food_seed(store):
    """英 / 日 / 韩问句要能命中中文种子。

    判据「库里对这个主体一无所知就返回空」本身是对的，缺的是**主体的语言对齐** ——
    外文主体（`vegetarian` / `ベジタリアン` / `채식`）在中文条目里永远搜不到字面，
    于是每一句外文问句都被判成「库里没有」。
    """
    for msg in (
        "I'm vegetarian. Is that a problem in China?",
        "ベジタリアンでも中国で食事できますか？",
        "중국에서 채식이 가능한가요?",
    ):
        ki, ids = _hits_for(store, msg)
        assert ki is not None, msg
        assert any(i.startswith("gen-in-food") for i in ids), (msg, ids)


def test_inbound_food_seeds_cover_the_common_diet_questions(store):
    """在华饮食种子必须覆盖素食 / 清真 / 过敏 / 饮水这几类最常被问的。"""
    chunks = [
        c for c in store.scan(filters={}, limit=500) if c.chunk_id.startswith("gen-in-food")
    ]
    assert len(chunks) >= 8, len(chunks)
    for c in chunks:
        assert (c.metadata or {}).get("kind") == "food", c.chunk_id
        assert c.source, c.chunk_id
    joined = "".join(c.text for c in chunks)
    for need in ("素食", "清真", "致敏", "自来水"):
        assert need in joined, need


def test_where_to_eat_question_keeps_its_own_subject():
    """「哪里能吃到本地人常去的馆子」的主体必须是**这一句自己的**，不是上一轮的上海。

    修前：命中词在句尾，前半截整段是疑问骨架（「哪里能吃到本地人常」），
    `has_own_subject` 判不出来 → 掉承接 → 跟着上一轮话题答。
    """
    from app.config import get_settings
    from app.intent import detect_knowledge_intent

    settings = get_settings()
    ki = detect_knowledge_intent(
        "哪里能吃到本地人常去的馆子？", settings, None, hint_subject="上海"
    )
    assert ki is not None
    assert ki.subject.startswith("本地人常"), ki.subject
    assert "上海" not in ki.subject, ki.subject
    assert "哪里" not in ki.subject, ki.subject


def test_food_titles_drop_the_verbal_complement():
    """动补 / 被动残片不是焦点：问的是那道菜本身。"""
    from app.knowledge import display_topic

    assert (
        display_topic("小笼包", "小笼包怎么吃才不会被烫到？", "小笼包") == "小笼包"
    )
    assert (
        display_topic("北京烤鸭", "北京烤鸭去哪家吃比较正宗？", "北京烤鸭") == "北京烤鸭"
    )
    assert display_topic("清真餐厅", "清真餐厅好找吗？", "清真") == "清真餐厅"


def test_focus_is_taken_after_the_subject_when_the_prefix_is_a_question():
    """主体前面是疑问引导时，焦点只能取主体**之后**（否则抓回「哪里能吃」）。"""
    from app.knowledge import display_topic

    topic = display_topic("本地人常", "哪里能吃到本地人常去的馆子？", "馆子")
    assert topic == "本地人常去的馆子", topic


def test_english_title_keeps_the_focus_not_only_the_place():
    """英文标题别只落成单个地名 —— 焦点短语优先，地名补位。"""
    from app.knowledge import display_topic

    assert (
        display_topic("local dishes", "What local dishes should I try in Chengdu?", "local dishes")
        == "Chengdu local dishes"
    )
    assert (
        display_topic("tap water", "Is tap water safe to drink in China?", "tap water")
        == "China tap water"
    )


def test_next_questions_follow_the_topic_of_this_turn():
    """建议栏按**这一轮的话题**整套换：饮食话题连问四轮不再一字不差。"""
    from app.followups import next_questions

    food = next_questions(
        decision="answer", language="zh", slots={"destination": "上海"}, subject="素食者"
    )
    assert len(food) == 3
    assert any("菜" in q for q in food), food
    metro = next_questions(decision="answer", language="zh", slots={}, subject="地铁")
    assert food != metro, (food, metro)
    assert any("地铁" in q for q in metro), metro
    # 认不出话题时回到通用模板，不能露 key、也不能空
    generic = next_questions(decision="answer", language="zh", slots={}, subject="")
    assert generic and not any("nq." in q for q in generic)


def test_next_questions_tidy_dangling_particles_without_a_place():
    """没有目的地时，日韩模板句首的裸助词 / 英文句末的悬空介词要收掉。"""
    from app.followups import next_questions

    ko = next_questions(decision="answer", language="ko", slots={}, subject="채식")
    assert not ko[0].startswith("에서"), ko
    ja = next_questions(decision="answer", language="ja", slots={}, subject="料理")
    assert not ja[0].startswith("で"), ja
    en = next_questions(decision="answer", language="en", slots={}, subject="tap water")
    assert not en[0].rstrip("?").endswith("in"), en


import pytest  # noqa: E402  （本文件顶部没导入，这批参数化用例要用）

# ---------------------------------------------------------------------------
# 购物 / 就医 / 礼仪与生活惯例（2026-10-06 补种子 + 触发词 + 话题化建议）
# 原先这三类**四道闸门全灭**：「免税和退税有什么区别」「在中国看病怎么办」
# 「中国要给小费吗」全掉回槽位体检，被回「想去的城市？玩几天？预算多少？」。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    ["免税和退税有什么区别？", "丝绸店能砍价吗？", "买到假货怎么维权？", "带什么伴手礼回去比较合适？"],
)
def test_shopping_questions_route_to_answer(client, msg):
    """购物长尾：只有「离境退税」三个字有触发词时，其余全部掉四连问。"""
    body = post_plan(client, msg, session_id="life-shop")
    assert body["type"] == "answer", (msg, body)


@pytest.mark.parametrize(
    "msg",
    ["在中国看病怎么办？", "药店能买到抗生素吗？", "中国要给小费吗？", "公共厕所好找吗？", "中国的插座是几伏的？"],
)
def test_health_and_manner_questions_route_to_answer(client, msg):
    """就医与礼仪：补触发词前这两个话题**一个词都没有** → 掉回槽位体检。"""
    body = post_plan(client, msg, session_id="life-manner")
    assert body["type"] == "answer", (msg, body)


@pytest.mark.parametrize(
    "msg",
    [
        "Are public toilets easy to find?",
        "What should I do if I get sick in China?",
        "中国で病気になったらどうすればいいですか？",
        "中国で税金還付は受けられますか？",
        "중국에서 아프면 어떡하나요?",
        "중국에서 팁을 줘야 하나요?",
    ],
)
def test_cross_language_life_questions_open_the_gate(client, msg):
    """外文问生活场景也要进闸门。三处缺口都是实测出来的：
    英文 `toilets`（复数，`\\btoilet\\b` 匹配不到）、日文「病気」（不是「病院」）、
    韩文「아프다」（不是「병원」）。"""
    body = post_plan(client, msg, session_id="life-i18n")
    assert body["type"] == "answer", (msg, body)


def test_event_lead_fragment_is_not_used_as_subject():
    """「买到**假货**」「不小心**发烧**了」的主体不能是动作引导。

    真 bug（2026-10-06）：主体是「买到 / 不小心」时，库里永远搜不到字面 →
    `search_knowledge` 的「本库对这个主体一无所知」闸门直接返回空，用户看到
    「本库没有相关材料」，而库里其实有假货维权与急救电话两条。
    """
    from app.config import get_settings
    from app.intent import detect_knowledge_intent

    st = get_settings()
    assert detect_knowledge_intent("买到假货怎么维权？", st, None).subject == "假货"
    assert detect_knowledge_intent("不小心发烧了该打什么电话？", st, None).subject == "发烧"


def test_display_topic_keeps_the_event_verb_and_drops_the_modal():
    """「吃饭的时候」的动词不能剥（剥了成「饭的时候」）；「该打」这类情态残片不算焦点。"""
    from app.knowledge import display_topic

    assert display_topic("吃饭的时候", "吃饭的时候有什么禁忌吗？", "禁忌") == "吃饭的时候禁忌"
    assert display_topic("发烧", "不小心发烧了该打什么电话？", "发烧") == "发烧"


def test_life_topics_swap_the_whole_suggestion_set():
    """建议栏按话题整套换：购物 / 就医 / 礼仪各一套，且退税归购物而不是支付。"""
    from app.followups import next_questions

    shop = next_questions(decision="answer", language="zh", slots={"destination": "上海"}, subject="退税")
    assert any("退税" in x for x in shop) and any("砍价" in x for x in shop)

    health = next_questions(decision="answer", language="zh", slots={}, subject="医院")
    assert any("看病" in x for x in health) and any("药店" in x for x in health)

    manner = next_questions(decision="answer", language="zh", slots={}, subject="小费")
    assert any("小费" in x for x in manner) and any("禁忌" in x for x in manner)


def test_life_topics_follow_the_ui_language():
    """四语言都要换成同话题的那一套，不能只换中文。"""
    from app.followups import next_questions

    for lang, marker in (("en", "tip"), ("ja", "チップ"), ("ko", "팁")):
        items = next_questions(decision="answer", language=lang, slots={}, subject="小费")
        assert any(marker in x for x in items), (lang, items)


# ---------------------------------------------------------------- 遗留项收尾（2026-10-06 第 4 轮）
def test_english_title_keeps_the_focus_when_the_subject_is_just_a_place(store):
    """英文标题别只剩地点 / 只剩否定残片 —— 焦点词兜底。

    两条都是实测出来的：
    · `What should I do if I get sick in China?` 的标题落成「China」—— 聊的是生病，
      标签却只有一个国名（原来「介词 + 专名」被当成焦点本身）。
    · `I don't eat pork` 抽出的主体是 "don't eat"，一路落到最后就成了标题
      「don't eat」，看不出这一问聊的是猪肉。
    """
    from app.knowledge import display_topic

    assert (
        display_topic("China", "What should I do if I get sick in China?", "China")
        == "China sick"
    )
    # 真实链路抽出的主体是 "get sick"（夹着虚词 get），同一句要走同一条路
    assert (
        display_topic("get sick", "What should I do if I get sick in China?", "get sick")
        == "China sick"
    )
    assert display_topic("don't eat", "I don't eat pork", "don't eat") == "pork"
    # 泛义动词（order）说明不了这一问在聊什么 —— 退回更具体的实词
    assert (
        display_topic("order", "I don't eat pork, what should I order?", "order") == "pork"
    )


def test_chinese_sickness_question_hits_the_health_seed(store):
    """「在中国生病了怎么办」不能因为主体吃掉了实词就一条都查不到。

    真 bug（2026-10-06）：主体是「中国生病」，`_query_terms` 先把主体从问句里摘掉，
    摘完只剩「了怎么办」→ terms 为空 → 整库没有一条够格进池，界面上写着
    「本库没有相关材料」，而库里有急救、看病、理赔三条。
    """
    _ki, ids = _hits_for(store, "在中国生病了怎么办")
    assert any(i.startswith("gen-in-health") for i in ids), ids


def test_medical_cost_question_hits_the_claim_entry(store):
    """「看病花钱能报吗」要命中理赔专条（原先只有 1 条，且答不到报销材料）。"""
    _ki, ids = _hits_for(store, "看病花钱能报吗")
    assert "gen-in-health-005" in ids, ids


def test_how_to_eat_a_dish_hits_the_food_seed(store):
    """「小笼包怎么吃」要命中名菜吃法那条（原先 hits=0，全靠模型常识瞎答）。"""
    _ki, ids = _hits_for(store, "小笼包怎么吃")
    assert any(i.startswith("gen-in-food") for i in ids), ids


def test_foreign_subject_is_not_read_as_nothing_in_the_library(store):
    """外文主体不能因为「库里搜不到这个字面」就被判成没有相关材料。

    真 bug（2026-10-06）：`I don't eat pork` 的主体是英文残片 "don't eat"，
    中文条目里永远搜不到它 → 「本库没有相关材料」；而问句里的 pork 换成「猪肉」
    后，忌口与清真两条都真的在讲这件事。
    """
    _ki, ids = _hits_for(store, "I don't eat pork")
    assert any(i.startswith("gen-in-food") for i in ids), ids


def test_latin_place_subject_does_not_hit_an_app_name(store):
    """「Nihao **China**」这种专名里的国名，不能当成"这条在讲中国"。

    真 bug（2026-10-06）：问「get sick in China」，相关条目里混进一条讲银联 App 的
    支付条目 —— 主体 `China` 是**子串**命中的，而地名到处都是，还会长在别的专名里。
    """
    _ki, ids = _hits_for(store, "What should I do if I get sick in China?")
    assert any(i.startswith("gen-in-health") for i in ids), ids
    assert not any(i.startswith("gen-in-pay") for i in ids), ids


# --------------------------------------------------------------------------
# 来华生活主题第二批：应用 / 天气 / 安全 / 节假日 / 语言（2026-10-06）
# --------------------------------------------------------------------------
def test_in_china_app_questions_hit_the_app_seed(store):
    """境外应用能不能用、打车用什么软件 —— 四语都要落到应用条目。

    这是来华落地最高频的一问（Google 地图用不了怎么导航），库里原先一条都没有。
    """
    for msg in (
        "Google 地图在中国能用吗",
        "Does Google Maps work in China",
        "中国でGoogleマップは使えますか",
        "중국에서 지도 앱 뭐 써요",
    ):
        _ki, ids = _hits_for(store, msg)
        assert any(i.startswith("gen-in-apps") for i in ids), (msg, ids)


def test_weather_and_packing_questions_hit_the_weather_seed(store):
    """几月来最舒服 / 带什么衣服 / 空气 —— 中英日韩都要命中天气条目。"""
    for msg in (
        "几月来中国最舒服",
        "What clothes should I pack for Beijing",
        "中国の空気汚染はひどいですか",
        "중국 미세먼지 심한가요",
    ):
        _ki, ids = _hits_for(store, msg)
        assert any(i.startswith("gen-in-weather") for i in ids), (msg, ids)


def test_safety_questions_hit_the_safety_seed(store):
    """丢护照 / 骗局 / 治安 —— 四语都要命中安全条目。"""
    for msg in (
        "在中国护照丢了怎么办",
        "What should I do if I lose my passport",
        "中国で財布をなくしたらどうする",
        "중국에서 여권 잃어버렸어요",
    ):
        _ki, ids = _hits_for(store, msg)
        assert any(i.startswith("gen-in-safety") for i in ids), (msg, ids)


def test_holiday_questions_hit_the_holiday_seed(store):
    """黄金周 / 闭馆 / 预约 —— 四语都要命中节假日条目。"""
    for msg in (
        "国庆去北京会不会人太多",
        "Should I avoid Chinese New Year",
        "中国の国慶節は混みますか",
        "중국 국경절에 많이 붐비나요",
    ):
        _ki, ids = _hits_for(store, msg)
        assert any(i.startswith("gen-in-holiday") for i in ids), (msg, ids)


def test_language_questions_hit_the_language_seed(store):
    """英文够不够用 / 菜单看不懂 —— 四语都要命中语言条目。"""
    for msg in (
        "中国人会说英文吗",
        "Do people speak English in China",
        "中国では英語は通じますか",
        "중국에서 영어로 통하나요",
    ):
        _ki, ids = _hits_for(store, msg)
        assert any(i.startswith("gen-in-language") for i in ids), (msg, ids)


def test_same_alias_translations_share_one_discriminative_weight(store):
    """同一外文词译成的多个中文写法，判别力必须一致。

    真 bug（2026-10-06）：`english` 译成「英文 / 英语」两个 term，而种子里
    「可出具英文诊断证明」的就医条目恰好写了「英语」（更稀有），于是问语言沟通
    却先列出就医与防骗条目 —— 哪个写法更稀有只是措辞的偶然，不该决定答案。
    """
    _ki, ids = _hits_for(store, "Do people speak English in China")
    assert ids and ids[0].startswith("gen-in-language"), ids


def test_question_skeleton_subject_still_reaches_the_seed(store):
    """主体榨不出成分时（「几月来」），命中问句实词的条目就算相关。

    真 bug（2026-10-06）：「几月来中国最舒服」命中了天气条目，却因为
    `related_keys` 为空被判成「本库没有相关材料」。
    """
    _ki, ids = _hits_for(store, "几月来中国最舒服")
    assert any(i.startswith("gen-in-weather") for i in ids), ids


def test_a_place_named_in_an_example_does_not_prove_the_entry_is_about_it(store):
    """举例里提一句城市名，不能证明这条在讲那个城市。

    真 bug（2026-10-06）：天气条目写着「多数城市（…、杭州）」，于是问
    「杭州西湖要门票吗」时闸门放行，一条通用门票省钱规则被列成了「相关条目」。
    地点另有作用域通道，不该靠正文里出现一次城市名当证据。
    """
    _ki, ids = _hits_for(store, "杭州西湖要门票吗")
    assert not any(i.startswith("bud-gen") for i in ids), ids


def test_new_topics_get_their_own_suggestions():
    """新增五个话题的建议栏：四语都要整套换成同话题的三条。"""
    from app.followups import next_questions
    from app.i18n import t

    cases = (
        ("apps", ("谷歌地图", "Google Maps", "グーグルマップ", "구글 지도")),
        ("weather", ("天气", "weather", "天気", "날씨")),
        ("safety", ("护照丢了", "lose my passport", "パスポートをなくした", "여권을 잃어버렸")),
        ("holiday", ("黄金周", "golden week", "ゴールデンウィーク", "국경절")),
        ("language", ("英文", "english", "英語", "영어")),
    )
    for topic, subjects in cases:
        for lang, subject in zip(("zh", "en", "ja", "ko"), subjects):
            items = next_questions(
                decision="answer", language=lang, slots={}, subject=subject
            )
            head = t(f"nq.topic_{topic}", lang).split("|")[0].split("{place}")[0].strip()
            assert any(head[:6] in x for x in items), (topic, lang, subject, items)
