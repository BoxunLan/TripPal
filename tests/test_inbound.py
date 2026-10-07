"""「外国人来华」主题的知识与意图闸门回归。

产品定位是**外国人来华**（入境游），但知识库曾是「中国人出境」的结构 ——
来华签证 / 过境免签 / 外卡支付 / 联网 / 住宿接待 / 退税几乎为零，
于是三条最该答的问题（签证、过境免签、支付）全部断：
F1「美国人来北京 5 天要签证吗」被做成「签证办理 5 天日程」、
F2「240 小时过境免签适用哪些国家」被反问「你要去哪国」、
F3「能不能用外卡支付」被问行程四件套。

这一组用例守两件事：
1. **数据**：来华条目必须在库里，且带 source + effective_date + fresh_until（政策类判时效要靠它）。
2. **接线**：这两类问题不许再掉回行程槽位体检 —— 政策走实时层（查不到也给官方入口），
   实用问答走常识层（模型作答 + 未经核实标注 + 复核入口）。
"""

from __future__ import annotations

import pytest
from conftest import post_plan

from app.i18n import detect_language, t
from app.intent import detect_knowledge_intent, detect_realtime_intent
from app.slots import extract_slots, resolve_destinations

TRANSIT_Q = "240 小时过境免签适用于哪些国家"
PAY_Q = "I'm flying into Shanghai, can I pay with my foreign credit card?"


# ---------------------------------------------------------------- 数据完整性
def test_inbound_realtime_seeds_carry_source_and_freshness(store):
    """政策条目必须自证时效：来源 + 生效日 + 有效期缺一不可。

    实时层的合法产出是「查到没过期的来源」——没有 effective_date/fresh_until
    的条目既进不了时效闸门，也没法回答「这条是不是现行口径」。
    """
    chunks = store.scan(filters={}, limit=500)
    inbound = [c for c in chunks if c.chunk_id.startswith("rt-in-")]
    assert len(inbound) >= 6, f"来华政策条目过少：{len(inbound)}"
    for c in inbound:
        assert c.layer == "realtime", c.chunk_id
        assert (c.metadata or {}).get("kind") == "policy", c.chunk_id
        assert c.source and c.source_url and c.source_url.startswith("https://"), c.chunk_id
        assert c.effective_date and c.fresh_until, c.chunk_id


def test_inbound_knowledge_covers_payment_net_transport_stay_refund(store):
    """来华实用知识要覆盖支付 / 联网 / 交通 / 住宿 / 退税五类，缺一类就有一类问题答不上。"""
    chunks = store.scan(filters={}, limit=500)
    inbound = [c for c in chunks if c.chunk_id.startswith(("gen-in-", "vis-cn-"))]
    kinds = {(c.metadata or {}).get("kind") for c in inbound}
    for need in ("payment", "connectivity", "transport", "service", "process"):
        assert need in kinds, f"来华知识缺「{need}」类：{sorted(kinds)}"


# ---------------------------------------------------------------- 政策闸门
def test_transit_visa_free_is_a_realtime_policy_question(client):
    """过境免签是国家政策、且数字会变 → 走实时层，凭生效日与官方来源作答。"""
    body = post_plan(client, TRANSIT_Q, session_id="in-transit")

    assert body["type"] == "realtime", body
    assert body["info_type"] == "policy"
    assert body["found"], "240 小时过境免签在库里，必须查到"
    ids = {f["chunk_id"] for f in body["found"]}
    assert "rt-in-240h-001" in ids
    for fact in body["found"]:
        assert fact["source"] and fact["effective_date"], fact
    assert any("nia.gov.cn" in c["url"] for c in body["channels"]), "必须给国家移民管理局入口"
    # 不许再出现行程四问
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in body["note"], word


def test_transit_question_without_a_space_still_matches(client):
    """「240小时」与「240 小时」是同一条政策 —— 少打一个空格不能查不到。"""
    body = post_plan(client, "240小时过境免签适用哪些国家", session_id="in-transit-nospace")

    assert body["type"] == "realtime"
    assert body["found"], "空白归一后应当命中同一条政策"


def test_unilateral_visa_free_question_is_a_policy_question(client):
    """"单方面免签来华能待多久" 归政策闸门，命中 30 天 + 施行期限那条。"""
    body = post_plan(client, "单方面免签来华可以停留多久", session_id="in-unilat")

    assert body["type"] == "realtime" and body["info_type"] == "policy"
    ids = {f["chunk_id"] for f in body["found"]}
    assert ids & {"rt-in-unilat-004", "rt-in-unilat-005"}, ids


def test_policy_question_without_a_seed_still_gives_the_official_entry(client):
    """库里没有现行口径时，合法产出是「明说查不到 + 给官方入口」，不是编一个数字。"""
    body = post_plan(client, "72 小时过境免签适用哪些国家", session_id="in-72h")

    assert body["type"] == "realtime" and body["info_type"] == "policy"
    assert body["found"] == [], "库里没有 72 小时口径，不许拿 240 小时那条顶上"
    assert body["channels"], "查不到时必须给 NIA / 12367 官方入口"
    assert all("nia.gov.cn" in c["url"] for c in body["channels"])


def test_outbound_visa_free_question_is_not_hijacked(deps):
    """反向保护：「哪些国家对中国免签」是**出境**问题，不能因为带「免签」二字就被政策闸门吞掉。"""
    assert detect_realtime_intent("哪些国家对中国免签", deps.settings, {}) is None
    assert detect_realtime_intent("办泰国签证要准备什么", deps.settings, {}) is None


# ---------------------------------------------------------------- 实用问答闸门
@pytest.mark.parametrize(
    "message",
    [
        PAY_Q,
        "外国人来中国怎么上网，要买 SIM 卡吗",
        "离境退税需要什么条件",
        "外国旅客怎么用护照买高铁票",
    ],
)
def test_inbound_practical_questions_are_answered_not_clarified(client, message):
    """支付 / 联网 / 退税 / 购票 都不是排行程 —— 不许回「目的地？天数？预算？人数？」。"""
    body = post_plan(client, message, session_id="in-faq")

    assert body["type"] == "answer", f"{message} 掉回追问了：{body}"
    assert "missing_slots" not in body
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in body["note"], f"{message} 里出现了行程追问：{word}"
    # 通用常识作答必须自证来源性质。文案跟**输出语言**（英文问句会得到英文 note），
    # 所以断言要按 message 判定的语言取那一条，而不是写死中文串。
    marker = t("kn.general_knowledge", detect_language(message))
    assert marker and marker in body["note"], f"{message} 缺「未核实」标注"
    assert body["verify"], "未核实的作答必须给复核入口"


def test_payment_question_reaches_the_knowledge_gate(deps):
    assert detect_knowledge_intent(PAY_Q, deps.settings, {}) is not None


def test_payment_seed_is_reachable_for_a_chinese_question(client):
    """中文问法要能真的命中库里的来华支付条目（不只是让模型凭空答）。"""
    body = post_plan(client, "外国人能用外卡支付吗", session_id="in-pay-zh")

    assert body["type"] == "answer"
    ids = {h["chunk_id"] for h in body["hits"]}
    assert "gen-in-pay-001" in ids, f"没命中来华支付条目：{ids}"


# ---------------------------------------------------------------- 国籍 ≠ 目的地
def test_inbound_nationality_is_not_the_destination(deps):
    """「我是美国人，想去北京玩 5 天」—— 美国是国籍，目的地是中国。

    这是 P0-③ 漏到检索层的那一半：destination_country 落成「美国」时，
    来华问题检索到的是**美国出境签证**知识，答案方向整个错了。
    """
    s = extract_slots("我是美国人，想去北京玩 5 天，需要签证吗", deps.settings)

    assert s["destination"] == "北京"
    assert s["destination_country"] == "中国"
    assert "美国" not in s.get("destination_aliases", []), "别名会当检索 token 用，不能留国籍"


def test_inbound_without_a_city_still_points_at_china(deps):
    s = extract_slots("I want to visit China for 5 days", deps.settings)
    assert s["destination_country"] == "中国"
    assert s.get("destination_aliases")


def test_outbound_china_is_still_only_the_origin(deps):
    """反向保护：出境问题里中国依旧只是签发护照的国家，不能变成目的地。"""
    assert resolve_destinations("哪些国家对中国免签", deps.settings)[0] is None
    assert resolve_destinations("从中国出发去泰国 5 天，预算 8000", deps.settings)[0] == "泰国"


def test_knowledge_subject_at_sentence_start_comes_from_the_match(deps):
    """「离境退税需要什么条件」句首就命中，切不出前半截 —— 主语要取命中的咨询词本身。"""
    it = detect_knowledge_intent("离境退税需要什么条件", deps.settings, {})

    assert it is not None and it.subject == "离境退税", it


# ---------------------------------------------------------------- 出境日本已整块删除
def test_japan_is_no_longer_a_plannable_destination(deps):
    """主题收口为「外国人来华」后，出境日本整块已删（2026-10-05）。

    日本仍会作为**国籍 / 政策清单里的国名**出现（来华签证、过境免签 57 国），
    但不能再被解析成目的地，否则「去日本 5 天」会绕回来华链路之外。
    """
    assert resolve_destinations("去日本 5 天，预算 8000", deps.settings)[0] is None
    assert resolve_destinations("从中国出发去日本 5 天，预算 8000", deps.settings)[0] is None
    table = deps.settings.routes["destinations"]
    for gone in ("日本", "京都", "大阪", "东京", "北海道"):
        assert gone not in table, f"{gone} 已随出境日本一起删除"


def test_no_japan_outbound_content_left_in_the_kb(store):
    """知识库里不许再有出境日本内容 —— 只剩政策清单里的国名「日本」。"""
    chunks = store.scan(filters={}, limit=500)
    leftover = [
        c.chunk_id
        for c in chunks
        if any(k in (c.text or "") for k in ("京都", "大阪", "北海道", "日元", "关西", "金阁寺"))
    ]
    assert leftover == [], f"仍有出境日本条目：{leftover}"


def test_japan_still_appears_in_inbound_policy_lists(store):
    """反向保护：日本是**来华政策清单里的适用国**，这层不能跟着删。

    240 小时过境免签与单方面免签的适用国清单里都有日本；删了日本，
    这两条政策就不成立 —— 前面那次清理只该删「出境日本」，不该动政策数据。
    """
    chunks = {c.chunk_id: (c.text or "") for c in store.scan(filters={}, limit=500)}
    assert "日本" in chunks.get("rt-in-240h-002", ""), "过境免签适用国清单应含日本"
    assert "日本" in chunks.get("rt-in-unilat-005", ""), "单方面免签国家清单应含日本"


# ---------------------------------------------------------------- 政策类不吃跨轮承接
def test_policy_question_does_not_inherit_the_previous_city(deps):
    """先问城市里的事、再问国家政策：政策不许继承上一轮的城市。

    真 bug（2026-10-05 实测）：先问「天安门什么时候升旗」→ 再问
    「240 小时过境免签适用哪些国家」，place 被承接成「北京」，而 policy 条目的
    destination 是「中国」→ 检索的地点过滤把它们全滤掉，found 从 2 掉到 0。
    政策是全国口径的，**地点与上一轮那个城市无关**。
    """
    it = detect_realtime_intent(
        TRANSIT_Q, deps.settings, {}, hint_subject="天安门", hint_place="北京"
    )
    assert it is not None
    assert it.place != "北京", "政策类不该继承上一轮的城市（会把政策条目全滤掉）"
    assert it.subject != "天安门", "政策类的主体是政策名本身，不是上一轮的主体"


def test_policy_question_still_finds_its_rules_after_a_city_question(client):
    """端到端：城市问题之后的政策问题，仍要查到现行口径（不是只剩官网链接）。"""
    post_plan(client, "天安门什么时候升旗", session_id="policy-after-city")
    body = post_plan(client, TRANSIT_Q, session_id="policy-after-city")

    assert body["found"], "库里有现行政策，被上一轮的城市过滤掉就只剩链接了"


def test_subject_based_realtime_still_inherits_the_city(deps):
    """反向保护：**非政策类**照旧承接 —— 「门票多少钱」还是要落回上一轮那个城市。"""
    it = detect_realtime_intent(
        "今天几点开门", deps.settings, {}, hint_subject="天安门", hint_place="北京"
    )
    assert it is not None and it.place == "北京" and it.subject == "天安门"
