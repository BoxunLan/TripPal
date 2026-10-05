"""事实问询闸门：不许再把事实问询当成「缺槽的行程请求」拦下。

同一个 bug 有两种形态，各有一道闸门。

**形态一（实时）**：用户输入「天安门什么时候升旗」，得到的回答是：

    还需要确认：想去的城市或国家是哪里？计划玩几天…？这次大概的预算是多少…？
    一行几个人，有小孩或长者同行吗？

四个问题全问错了方向：用户没打算去哪里玩几天，他问的是一个**每天都会变的时刻**。
行程链路要那四个槽位，事实问询只需要「问的是什么 + 知识库有没有没过期的答案 + 去哪查」。

**形态二（静态，2026-09-28 用户复查发现）**：「西湖有多大」不随时间变，不含任何
时间/开放/天气/交通词，所以连实时闸门都不命中 → 照样掉回槽位体检 → 照样回那四问。
故补第二道闸门 `detect_knowledge_intent` 与旁路 `knowledge`。
"""

from __future__ import annotations

import json

import pytest
from conftest import post_plan

from app.intent import (
    KnowledgeIntent,
    detect_knowledge_intent,
    detect_realtime_intent,
    knowledge_cfg,
    realtime_cfg,
)
from app.graph import carry_context
from app.knowledge import build_answer_prompt
from app.session import Session, Turn
from app.slots import extract_slots

FLAG_Q = "天安门什么时候升旗"
KNOW_Q = "西湖有多大"


# ---------------------------------------------------------------- 主回归
def test_flag_question_is_answered_as_a_fact_not_a_trip(client):
    body = post_plan(client, FLAG_Q, session_id="rt-flag")

    assert body["type"] == "realtime"
    assert body["info_type"] == "schedule"
    assert body["subject"] == "天安门"
    assert body["place"] == "北京"
    # 事实问询没有「缺槽」这个概念 —— 不能出现行程四问里的任何一句
    assert "missing_slots" not in body
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in body["note"], f"事实问询里不该出现行程追问：{word}"


def test_flag_question_returns_the_seeded_fact_with_a_source(client):
    body = post_plan(client, FLAG_Q, session_id="rt-src")

    keys = {f["chunk_id"] for f in body["found"]}
    assert "rt-cn-flag-001" in keys, "「升旗＝当日日出时间」这条规则必须在库里"
    for fact in body["found"]:
        assert fact["source"], fact
        assert fact["source_url"].startswith("https://"), fact
        assert fact["fresh_until"] >= "2026-09-25"


def test_realtime_path_does_not_use_the_model_or_vector_search(client, store):
    """事实断言不该由模型生成、也不该由相似度决定候选：这条旁路只做元数据扫描。"""
    store.search_calls = 0
    store.scan_calls = 0
    post_plan(client, FLAG_Q, session_id="rt-nosearch")

    assert store.search_calls == 0, "实时事实链路不该走向量检索"
    assert store.scan_calls > 0, "应该用 scan 做元数据扫描"


# ---------------------------------------------------------------- 相关性闸门
def test_weather_question_does_not_get_flag_raising_facts(client):
    """地点对、类型不对 → 一条都不收。

    「北京明天天气」曾召回 destination=北京 的升旗条目：向量检索永远会返回 top_k，
    它不知道什么叫「不相关」。
    """
    body = post_plan(client, "北京明天天气怎么样", session_id="rt-weather")

    assert body["type"] == "realtime" and body["info_type"] == "weather"
    assert body["found"] == []


def test_more_specific_subject_must_appear_in_the_text(client):
    """主体比地点更具体时（天安门 vs 北京），正文里必须出现主体。"""
    body = post_plan(client, "北京故宫今天开放吗", session_id="rt-gugong")

    assert body["type"] == "realtime" and body["info_type"] == "opening_hours"
    assert body["found"] == [], "升旗条目与故宫开放状态无关，不该被召回"


def test_unknown_subject_is_honestly_empty_with_authoritative_entries(client):
    """查不到就说查不到，但必须给权威入口 —— 这是这个 skill 最核心的一条纪律。"""
    body = post_plan(client, "鼓浪屿明天天气怎么样", session_id="rt-unknown")

    assert body["info_type"] == "weather"
    assert body["found"] == []
    assert body["channels"], "查不到时必须给出权威入口"
    assert all(c["url"].startswith("https://") for c in body["channels"])
    assert body["unverified"], "没核实到的那一项必须显式写出来"


def test_opening_hours_generic_entry_is_region_blank_not_city_bound(deps, client):
    """日本清理后不再有城市级入口；但允许 region 留空的通用权威核实入口（不绑定城市，不会给错地方）。

    原先这一档只有「京都观光导航」一条（region: 日本）。它对中国景点本就筛不出来
    （北京故宫问开放，region 不匹配 → 一样是空），所以删掉它对来华链路无损；
    现在补的是 region 留空的「去哪儿查」通用入口，不指向任何具体城市，不会给错地方。
    """
    channels = deps.settings.routes["intents"]["realtime_fact"]["channels"]
    assert channels["opening_hours"], "应填一个通用权威核实入口"
    for c in channels["opening_hours"]:
        assert c.get("region", "") == "", "通用入口不许绑定具体城市（避免给错地方）"

    body = post_plan(client, "鼓浪屿今天开放吗", session_id="rt-open-gap")
    assert body["info_type"] == "opening_hours"
    assert body["found"] == []
    # 入口存在且都是 region 留空的通用入口，不是冒充的城市入口
    assert body["channels"], "应给出通用权威核实入口"
    for c in body["channels"]:
        assert c.get("region", "") == "", "不许拿别的城市的入口冒充"


def test_bare_kai_is_recognized_as_opening_hours(client):
    """真 bug（2026-10-05 用户实测「豫园几点开」）：触发词只认「开放/开门/开馆」，

    裸「开」（`几点开`）一个字不差地漏掉 → 实时闸门不命中 → 回一句问天数/预算/人数。
    修法：opening_hours 触发词加一条「时间词 + 开/开放/开门/开园/开馆/营业/关门/闭馆/关闭」，
    并把「开门/关门/开馆/闭馆/开放」从 schedule 触发词 1 移走（景点开闭本就属于 opening_hours，
    归 schedule 会去搜升旗条目、还把杭州问题错给北京入口）。
    """
    for q in ("豫园几点开", "豫园今天开吗", "西湖什么时候开门", "上海博物馆几点关门"):
        body = post_plan(client, q, session_id="rt-barekai-" + str(abs(hash(q)) % 9999))
        assert body["type"] == "realtime", f"{q} 被当成了行程：{body}"
        assert body["info_type"] == "opening_hours", f"{q} 应判为开放时间，实际 {body.get('info_type')}"
        assert body["place"], f"{q} 应解析出所在地"


def test_opening_time_phrase_still_routes_to_schedule(client):
    """「开放时间」是「X时间」构造，仍归 schedule（既有契约，test_request_class 守着）。

    不能因为上面把「开放」移出 schedule 触发词 1 就误伤这一条 —— 它走的是 trigger 2
    「(升国旗|…|开放|…)\\s*时间」。
    """
    body = post_plan(client, "故宫开放时间", session_id="rt-opentime-keep")
    assert body["type"] == "realtime" and body["info_type"] == "schedule", body


def test_channels_are_filtered_by_the_question_place(client):
    cn = post_plan(client, "北京明天天气怎么样", session_id="rt-region-bj")
    th = post_plan(client, "清迈明天天气怎么样", session_id="rt-region-cm")

    assert any("weather.com.cn" in c["url"] for c in cn["channels"]), "中国城市问天气要给中国天气网"
    assert all("weather.com.cn" not in c["url"] for c in th["channels"]), "出境泰国问天气不该给中国天气网"


# ---------------------------------------------------------------- 让路规则
def test_plan_requests_are_untouched(client):
    body = post_plan(client, "带 6 岁孩子去厦门 5 天，预算 8000", session_id="rt-plan")
    assert body["type"] == "plan"


@pytest.mark.parametrize(
    "message",
    [
        "去厦门 5 天，预算 8000，顺便问下鼓浪屿几点开门",
        "带 6 岁孩子去厦门 5 天，预算 8000，想看升旗",
        "两个人去北京 3 天，预算 5000",
    ],
)
def test_plan_request_with_a_fact_sub_question_is_not_hijacked(client, message):
    """有天数/预算 → 实时闸门让路。开放时间由 opening_hours 工具与行前清单承接。"""
    body = post_plan(client, message, session_id="rt-yield")
    assert body["type"] != "realtime", f"{message} 被实时闸门吞掉了"


def test_visa_questions_stay_on_the_visa_chain(client):
    """签证政策走的是专门的 visa 场景（清单 + 官方链接 + 生效日），不该被实时闸门接管。"""
    body = post_plan(client, "办泰国签证要准备什么", session_id="rt-visa")
    assert body["type"] == "plan"
    assert "visa" in body["route"]["scenes"]


def test_detect_intent_yields_when_plan_slots_are_present(deps):
    settings = deps.settings
    slots = extract_slots(FLAG_Q, settings)
    assert detect_realtime_intent(FLAG_Q, settings, slots) is not None
    assert detect_realtime_intent(FLAG_Q, settings, {"days": 5}) is None
    assert detect_realtime_intent(FLAG_Q, settings, {"budget": 8000}) is None
    assert detect_realtime_intent(FLAG_Q, settings, {"party_size": 3}) is None


@pytest.mark.parametrize(
    "message",
    ["办泰国签证要准备什么", "哪些国家对中国免签", "想去泰国玩", "泰国签证需要什么材料"],
)
def test_non_fact_questions_are_not_realtime_intents(deps, message):
    assert detect_realtime_intent(message, deps.settings, {}) is None, message


# ---------------------------------------------------------------- 配置契约
def test_realtime_scope_is_the_realtime_layer_only(deps):
    cfg = realtime_cfg(deps.settings)
    assert cfg["scopes"] == ["realtime:*"], "事实核验只查实时层，别把攻略当当日状态"


def test_every_trigger_declares_the_kinds_it_accepts(deps):
    """kinds 是相关性闸门的一半：没有它，「北京天气」会命中北京的升旗条目。"""
    for trigger in realtime_cfg(deps.settings)["triggers"]:
        assert trigger.get("kinds"), f"{trigger.get('type')} 没声明 kinds"


def test_every_channel_is_a_clickable_authoritative_link(deps):
    channels = realtime_cfg(deps.settings)["channels"]
    for info_type, entries in channels.items():
        for c in entries:
            assert c["url"].startswith("https://"), c
            assert c.get("name") and c.get("what"), c
            # 没实测过的入口不许写进来：给一个点不开的链接比不给更坏。
            # 因此每条都必须声明覆盖地区（或显式留空表示通用）。
            assert "region" in c, c


def test_realtime_progress_stream_reports_the_bypass(client):
    """/plan/stream 上也要如实反映这条旁路：clarify → realtime → output，五步不亮。"""
    resp = client.post("/plan/stream", json={"session_id": "rt-stream", "message": FLAG_Q})
    assert resp.status_code == 200
    lines = [json.loads(x) for x in resp.text.splitlines() if x.strip()]
    stages = [x["stage"] for x in lines if "stage" in x]
    assert stages == ["clarify", "realtime", "output"]
    done = next(x for x in lines if x.get("done"))
    assert done["response"]["type"] == "realtime"


# ================================================================ 常识闸门（形态二）
def test_static_fact_question_is_answered_as_a_fact_not_a_trip(client):
    """主回归：「西湖有多大」曾回「目的地？天数？预算？人数？」—— 四问全错方向。"""
    body = post_plan(client, KNOW_Q, session_id="kn-westlake")

    assert body["type"] == "answer"
    assert body["subject"] == "西湖"
    assert "missing_slots" not in body
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in body["note"], f"常识问询里不该出现行程追问：{word}"


def test_yes_no_question_is_answered_as_a_fact_not_a_trip(client):
    """真 bug（2026-10-04 用户报）：「华东师范大学是不是有个新校区在那里」→ 行程四连问。

    根因与「西湖有多大」同源，只是形态又变了一次：骨架清单收了「有没有 / 是否有」，
    漏了同样常用的**是非问系词「是不是」**；学校又不是词典里的地名（解析不出目的地），
    于是三道闸门全灭 → 掉回槽位体检 → 回「已记下目的地 上海。还需要确认：计划玩几天？
    这次大概的预算是多少？一行几个人？」。用户问的是一个**是非判断**，被当成了排行程。
    """
    body = post_plan(client, "华东师范大学是不是有个新校区在那里", session_id="kn-yesno")

    assert body["type"] == "answer", body.get("question") or body
    assert body["subject"] == "华东师范大学", body
    assert "missing_slots" not in body
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in body["note"], f"事实问询里不该出现行程追问：{word}"


def test_knowledge_gate_accepts_other_yes_no_shapes(deps):
    """同一骨架的其他写法也要收：「上海博物馆是不是免费的」以前也掉进行程链路。"""
    intent = detect_knowledge_intent("上海博物馆是不是免费的", deps.settings, {})

    assert intent is not None, "「是不是」骨架没被常识闸门收走"
    assert intent.subject == "上海博物馆", intent


def test_a_bare_question_skeleton_is_never_a_subject(deps):
    """命中片段落在**句首**时，主体提取会退化成骨架本身 —— 骨架不是主题。

    这条守着「补骨架」的副作用：`_subject` 对「离境退税」这类**主题名**用命中片段当主体
    是对的，但骨架（「是不是」）没有任何可检索内容，拿它去查只会得到噪声、卡片标题
    变成「关于「是不是」」。退化时退回槽位体检，比假装答了一个句法成分好。
    """
    assert detect_knowledge_intent("是不是要提前预约", deps.settings, {}) is None


# --------------------------------------------------- 是非问「接着说」的第三形态
# 真 bug（2026-10-04 用户实测，审计 session `7p1fi1`）：
#     1) 华东师范大学          → 行程四连问（裸地名，与「厦门」一致，属既定行为）
#     2) 华东师范大学在哪儿     → answer ✅
#     3) 是不是还有一个校区     → **行程四连问** ❌ 骨架在句首、句尾没有「吗/呢/？」，接不上上一句
#     4) 它还有个滴水湖校区对吗  → answer ✅
#     5) 具体位置在哪儿         → subject=「具体位置」、**答案空串** ❌ 占位名词被当成了主体
# ①② 与 ③④⑤ 的差别只在**这一句自己有没有主体** —— 补的判据就是这一条。
def test_a_skeleton_led_followup_inherits_the_previous_subject(deps):
    """③「是不是还有一个校区」：骨架在句首 = 本句没给主体 → 必须接上一轮。"""
    intent = detect_knowledge_intent(
        "是不是还有一个校区", deps.settings, {}, hint_subject="华东师范大学"
    )

    assert intent is not None, "骨架开头的追问没接上上一轮主体"
    assert intent.subject == "华东师范大学", intent


def test_a_placeholder_noun_followup_inherits_the_previous_subject(deps):
    """⑤「具体位置在哪儿」：「具体位置」是占位名词，指的就是上一轮那个东西。"""
    intent = detect_knowledge_intent(
        "具体位置在哪儿", deps.settings, {}, hint_subject="华东师范大学"
    )

    assert intent is not None
    assert intent.subject == "华东师范大学", intent
    # 没有可承接的对象时，宁可退回槽位体检，也不能拿占位名词当主体答一个空串。
    assert detect_knowledge_intent("具体位置在哪儿", deps.settings, {}) is None


def test_a_self_supplied_subject_is_never_overridden_by_the_session(deps):
    """承接的**反面**（守副作用）：「华东师范大学是不是有个新校区在那里」自带新主体。

    它只在句尾夹了个方位指代，主体是自己给的 —— 若被上一轮的「上海临港」顶掉，
    用户问的那所学校就从答案里丢了。`app/intent.py::locative_referent` 也只把它当
    **地点上下文**、不动主体，两处是同一口径。
    """
    intent = detect_knowledge_intent(
        "华东师范大学是不是有个新校区在那里", deps.settings, {}, hint_subject="上海临港"
    )

    assert intent is not None
    assert intent.subject == "华东师范大学", intent


def test_anaphoric_fragments_are_not_subjects(deps):
    """代词打头的碎片同样是**指代**，不是这一句自己给的主体。

    真 bug（2026-10-04 实测，15 轮模拟对话里冒出来的回归）：把「本句自带主体就不接上一轮」
    这条判据加严之后，「那个学校怎么样」的主体留成了「那个学校」、「那它值得去吗」留成了
    「那它」—— 卡片标题成了「关于「那个学校」」，复核链接变成 Bing 搜「那个学校」。
    代词不是主体，它指的是上一轮那个东西。
    """
    for message, hint in [
        ("那个学校怎么样", "上海海事大学"),
        ("那它值得去吗", "上海博物馆"),
        ("这个地方有什么好吃的", "上海临港"),
    ]:
        intent = detect_knowledge_intent(message, deps.settings, {}, hint_subject=hint)
        assert intent is not None, message
        assert intent.subject == hint, (message, intent)


def test_a_real_name_that_looks_anaphoric_is_still_a_subject(deps):
    """守副作用：「那不勒斯」也以「那」开头，但它是真专名 —— 不许被当成指代碎片。"""
    intent = detect_knowledge_intent("那不勒斯有什么好吃的", deps.settings, {})

    assert intent is not None
    assert intent.subject == "那不勒斯", intent


def test_locative_adverbs_inherit_the_previous_subject(deps):
    """「附近有什么好玩的」：「附近」指着上一轮那个地方，不是主体。"""
    intent = detect_knowledge_intent(
        "附近有什么好玩的", deps.settings, {}, hint_subject="上海海事大学"
    )

    assert intent is not None
    assert intent.subject == "上海海事大学", intent


def test_followup_turn_stays_on_the_knowledge_chain(client):
    """端到端：同一会话里先问学校、再说「是不是还有一个校区」，不许掉回行程四连问。"""
    post_plan(client, "华东师范大学在哪儿", session_id="kn-carry")
    body = post_plan(client, "是不是还有一个校区", session_id="kn-carry")

    assert body["type"] == "answer", body.get("question") or body
    assert body["subject"] == "华东师范大学", body
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in str(body), f"事实问询里不该出现行程追问：{word}"


def test_carry_context_hands_the_previous_turn_to_the_model():
    """承接态要把**上一轮的原话与答复**一起交给模型（对话记忆）。

    「具体位置在哪儿」本身只有三个字的信息量，不知道上一轮在说什么就答不到点上；
    `prompts/answer.md` 的 `{{context}}` 就是为此留的槽。
    """
    session = Session(session_id="ctx")
    session.recent.append(
        Turn(
            turn=1,
            message="它还有个滴水湖校区对吗",
            type="answer",
            subject="华东师范大学",
            reply="是的，华东师范大学有滴水湖校区，位于上海市浦东新区临港新城。",
        )
    )

    carried = carry_context(session, carried=True)
    assert "它还有个滴水湖校区对吗" in carried
    assert "滴水湖校区" in carried, "上一轮的答复要带上，否则答不到用户问的那个校区"

    # 非承接句自带完整信息，不该再挂上一轮（多给反而稀释）
    assert carry_context(session, carried=False) == ""
    # 方位指代与上一轮可以同时在场
    merged = carry_context(session, "上海临港", carried=True)
    assert "上海临港" in merged and "滴水湖校区" in merged


def test_static_fact_is_actually_answered_not_deferred(client):
    """关键口径：用户要的是**答案**，不是一份「我没有」。

    这条是 2026-09-28 用户复查报出的第二层 bug —— 修好「不再追问」之后，
    回答变成了「知识库里没有关于「西湖」的条目」，依然没告诉他西湖有多大。
    """
    body = post_plan(client, KNOW_Q, session_id="kn-empty")

    assert body["hits"] == [], "西湖不在种子里，不该有任何命中"
    assert body["answer"], "必须给出作答正文 —— 只说「知识库没有」等于没回答"
    assert body["confidence"] in {"high", "medium", "low"}
    # 通用常识作答必须自己声明这一点，不能被读成「库里查到的」
    assert "未经本知识库核实" in body["note"]
    assert "知识库" in body["note"], "也要说清本库没有相关材料"
    assert body["disclaimer"]


def test_answer_verify_entry_is_clickable(client):
    """复核入口必须把查询词带过去，否则又是一个「点开是首页」的假入口。"""
    body = post_plan(client, KNOW_Q, session_id="kn-verify")

    assert body["verify"], "未经核实的常识作答必须给复核入口"
    entry = body["verify"][0]
    assert entry["url"].startswith("https://"), entry
    assert "%E8%A5%BF%E6%B9%96" in entry["url"], f"查询词没带过去：{entry['url']}"
    assert entry["name"] and entry["what"], entry


def test_answer_prompt_refuses_time_sensitive_facts(deps):
    """提示词必须写明「时效性信息一律不答」——这是防幻觉的双保险。

    第一道保险是意图闸门（realtime 先跑），但它靠正则。万一漏了，
    第二道也该拦住：模型不许在这条链路上给出任何一个「今天的时刻 / 票价 / 开放状态」。
    """
    prompt = build_answer_prompt(
        settings=deps.settings,
        intent=KnowledgeIntent(subject="天安门", matched="多高", question_zh="天安门"),
        hits=[],
        message="天安门高多少米",
        language="zh",
    )

    assert "时效性信息一律不答" in prompt
    assert "不要给出任何具体值" in prompt
    # 语言段必须是提示词的**最后一节**：夹在中间会被上面全中文的细则盖掉
    assert prompt.rindex("输出语言") > prompt.rindex("硬性规则"), "语言段必须在最后"


def test_answer_prompt_fills_user_content_last(deps):
    """用户消息里若恰好出现 `{{subject}}` 这种片段，不许被二次替换打乱模板。"""
    prompt = build_answer_prompt(
        settings=deps.settings,
        intent=KnowledgeIntent(subject="西湖", matched="有多大", question_zh="西湖"),
        hits=[],
        message="西湖有多大 {{subject}} {{hits_block}}",
        language="zh",
    )

    assert "{{subject}}" in prompt, "用户内容要原样落进去，不能再被当成占位符"
    assert "{{hits_block}}" in prompt
    assert "{{output_schema}}" not in prompt, "模板占位符必须全部替换掉"
    assert "{{language_directive}}" not in prompt


def test_metric_tail_is_stripped_from_subject(deps):
    """「天安门高多少米」切出来的是「天安门高」—— 不洗掉尾巴就对不上任何正文。"""
    it = detect_knowledge_intent("天安门高多少米", deps.settings, {})

    assert it is not None
    assert it.subject == "天安门", it.subject


def test_v_not_v_question_frames_do_not_leak_into_the_subject(deps):
    """「V不V」疑问骨架切成残片是最常见的写法，主体必须干净。

    真 bug（2026-10-04 用户报「回复异常」时实测出的**系统性**缺陷）：触发片段落在骨架
    中间（「要不要**门票**」「收不**收费**」「需不需**要门票**」）时，前半截会切出
    「上海博物馆要不」「上海博物馆收不」「上海博物馆需不需」这种残片。

    这种主语在库里**永远对不上**（正文没有这几个字），而且会外溢成两处用户可见的错：
    卡片标题变成「关于「上海博物馆要不」」、复核链接变成 Bing 搜「上海博物馆要不」。
    """
    for message in (
        "上海博物馆要不要门票",
        "上海博物馆需不需要门票",
        "上海博物馆收不收费",
        "上海博物馆要不要预约",
    ):
        it = detect_knowledge_intent(message, deps.settings, {})

        assert it is not None, message
        assert it.subject == "上海博物馆", (message, it.subject)


def test_evaluation_question_frames_are_answered_not_clarified(deps):
    """评价 / 属性类「V不V」（贵不贵 / 值不值得 / 能不能带包）问的是事实，不是行程。

    真 bug（2026-10-04 实测）：这类问句原先三道闸门全灭 → 掉回槽位体检 →
    回「想去的城市？玩几天？预算多少？一行几个人？」。与「是不是」「有没有」同源，
    靠**按问句骨架收**解决。
    """
    for message in (
        "上海博物馆贵不贵",
        "上海博物馆值不值得去",
        "上海博物馆能不能带包",
        "上海博物馆开不开放",
    ):
        it = detect_knowledge_intent(message, deps.settings, {})

        assert it is not None, f"{message} 掉回了行程澄清"
        assert it.subject == "上海博物馆", (message, it.subject)


def test_evaluation_question_frames_yield_to_real_trip_and_payment_requests(deps):
    """新增骨架不能抢走**真请求**：支付场景与订酒店祈使句各归各的链路。"""
    assert detect_knowledge_intent("能不能帮我订个酒店", deps.settings, {}) is None
    assert detect_knowledge_intent("能不能推荐个酒店", deps.settings, {}) is None
    # 支付场景由更具体的触发词接走，主体不能退化成骨架本身。
    pay = detect_knowledge_intent("能不能用信用卡支付", deps.settings, {})
    assert pay is not None and pay.subject not in {"能不能", "能不能用"}, pay


def test_knowledge_path_scans_metadata_instead_of_vector_search(client, store):
    """常识链路不走向量检索 —— 相似度为了凑 top_k 一定会返回东西，那些东西会被读成答案。

    （**会**调一次模型，那是有意的：长尾常识没有任何本地资料库能覆盖，
    见 `app/knowledge.py` 的模块 docstring。别照着旧注释改成「不调模型」。）
    """
    store.search_calls = 0
    store.scan_calls = 0
    post_plan(client, KNOW_Q, session_id="kn-nosearch")

    assert store.search_calls == 0, "常识链路不该走向量检索"
    assert store.scan_calls > 0, "应该用 scan 做元数据扫描"


@pytest.mark.parametrize(
    "message",
    [
        "厦门有什么好吃的",     # 目的地咨询，跟预算/人数没有半点关系
        "厦门好玩吗",
        "厦门大学怎么样",
        "想了解一下杭州",
    ],
)
def test_consult_questions_are_answered_not_clarified(client, message):
    """真 bug（2026-09-28 用户报）：这四句原先全部回「缺 日期 / 预算 / 人数」。

    它们不含行程槽位、也不含规划动词，掉回槽位体检就必然被追问。
    现在走的是常识旁路的**咨询兜底**（有明确目的地 + 疑问语气）。
    """
    body = post_plan(client, message, session_id="kn-consult")

    assert body["type"] == "answer", f"{message} 掉回追问了：{body}"
    for word in ("想去的城市或国家", "计划玩几天", "预算是多少", "一行几个人"):
        assert word not in body["note"], f"咨询类问询里不该出现行程追问：{word}"


@pytest.mark.parametrize(
    "message",
    [
        "去厦门",             # 出行意图、没有疑问语气 → 不该被咨询兜底吞掉
        "厦门三日游",          # 有天数槽位
        "帮我安排一下厦门",      # 规划动词「安排」
        "厦门有什么好吃的，帮我规划厦门三日游",  # 有目的地 + 咨询词 + 规划动词 → 规划优先
    ],
)
def test_consult_fallback_does_not_swallow_trip_requests(client, message):
    body = post_plan(client, message, session_id="kn-consult-yield")

    assert body["type"] != "answer", f"{message} 被咨询兜底吞掉了"


@pytest.mark.parametrize(
    "message",
    [
        "厦门怎么玩",           # 规划动词 → 让路（这是排行程，不是问常识）
        "帮我规划厦门三日游",   # 规划动词
        "去厦门 5 天，预算 8000，西湖有多大",  # 有行程槽位 → 让路
    ],
)
def test_knowledge_gate_yields_to_planning(client, message):
    body = post_plan(client, message, session_id="kn-yield")
    assert body["type"] != "answer", f"{message} 被常识闸门吞掉了"


def test_knowledge_gate_yields_when_plan_slots_are_present(deps):
    settings = deps.settings
    assert detect_knowledge_intent(KNOW_Q, settings, {}) is not None
    assert detect_knowledge_intent(KNOW_Q, settings, {"days": 5}) is None
    assert detect_knowledge_intent(KNOW_Q, settings, {"budget": 8000}) is None
    # 规划动词单独就能让路（没有槽位也一样）
    assert detect_knowledge_intent("厦门怎么玩", settings, {}) is None


@pytest.mark.parametrize(
    "message",
    ["想去泰国玩", "办泰国签证要准备什么", "带 6 岁孩子去厦门 5 天，预算 8000"],
)
def test_ordinary_requests_are_not_knowledge_intents(deps, message):
    assert detect_knowledge_intent(message, deps.settings, {}) is None, message


def test_knowledge_scope_excludes_the_realtime_layer(deps):
    """常识问题查 scene/general（攻略与常识）；realtime 是当日状态，对它没有意义。"""
    scopes = knowledge_cfg(deps.settings)["scopes"]
    assert all(not s.startswith("realtime:") for s in scopes), scopes
    assert knowledge_cfg(deps.settings)["plan_verbs"], "让路规则不能为空"


def test_knowledge_progress_stream_reports_the_bypass(client):
    resp = client.post("/plan/stream", json={"session_id": "kn-stream", "message": KNOW_Q})
    assert resp.status_code == 200
    lines = [json.loads(x) for x in resp.text.splitlines() if x.strip()]
    stages = [x["stage"] for x in lines if "stage" in x]
    assert stages == ["clarify", "knowledge", "output"]
    done = next(x for x in lines if x.get("done"))
    assert done["response"]["type"] == "answer"


# ================================================================ 权威入口的名实一致
def test_city_specific_entry_is_not_offered_to_another_city(client):
    """真 bug：问西湖开门，给的是北京旅游网（天安门升旗指南）—— 给错地方的入口比不给更坏。

    根因两条：① 地名（西湖）解析不出来 → 渠道整段跳过过滤；
    ② 那条北京入口的 region 写成「中国」，等于宣称"全国都能用"。
    """
    body = post_plan(client, "西湖什么时候开门", session_id="rt-westlake")

    assert body["type"] == "realtime"
    assert all("visitbeijing" not in c["url"] for c in body["channels"]), (
        "北京的入口不该出现在杭州的问题里"
    )


def test_beijing_entry_still_reaches_beijing_questions(client):
    """收紧 region 不能把该给的也砍掉。"""
    body = post_plan(client, FLAG_Q, session_id="rt-bj-keep")

    assert any("visitbeijing" in c["url"] for c in body["channels"]), "北京问升旗必须还能拿到北京旅游网"


def test_landmark_aliases_resolve_to_their_city(client):
    """地标别名归城市：解析不出地名就没法按地区筛入口（上面那条 bug 的根因 ①）。

    日本那批地标别名（金阁寺 / 清水寺 / 岚山…）随出境日本一起删了，这里改用中国地标
    守同一条判据：地标解析成城市 → 该城市的入口就能筛出来。
    """
    xiamen = post_plan(client, "鼓浪屿今天开放吗", session_id="rt-alias-xiamen")
    assert xiamen["place"] == "厦门", "鼓浪屿应当解析为厦门"

    beijing = post_plan(client, FLAG_Q, session_id="rt-alias-bj")
    assert beijing["place"] == "北京", "天安门应当解析为北京"
    assert any("visitbeijing" in c["url"] for c in beijing["channels"]), (
        "地标→城市通了，该城市的入口就必须能筛出来"
    )

    hangzhou = post_plan(client, "西湖什么时候开门", session_id="rt-alias-hz")
    assert hangzhou["place"] == "杭州"


def test_channel_region_is_not_a_whole_country_for_city_entries(deps):
    """region 必须写到入口实际覆盖的范围。写「中国」时杭州的问题也会拿到北京的入口。"""
    schedule = realtime_cfg(deps.settings)["channels"]["schedule"]
    assert schedule, "schedule 至少要有一条已实测入口"
    for c in schedule:
        assert c["region"] == "北京", f"{c['name']} 的 region 太粗：{c['region']}"


def test_channel_name_does_not_promise_a_document_the_url_cannot_open(deps):
    """name 必须描述点击后落在哪儿。

    原写法：name = 《天安门广场升旗仪式观看指南》，url = 站点根 —— 点开必然跳首页
    （用户报过「点了它给的网址，但是调到了网站首页而非对应文件」）。

    判据**不是**「落在站点根就错」：`中国天气网（中国气象局公共气象服务中心）`
    指向站点根是诚实的 —— 它说的是「哪个机构」。错的是名字承诺了一份**具体文档**
    （有《》书名号，或写着 指南/公告/通知 之类）却只给站点根。
    """
    doc_markers = ("《", "》", "指南", "公告", "通知", "须知")
    for info_type, entries in realtime_cfg(deps.settings)["channels"].items():
        for c in entries:
            url = c["url"].rstrip("/")
            is_site_root = url.count("/") == 2  # https://host
            if not is_site_root:
                continue
            promised = [w for w in doc_markers if w in c["name"]]
            if not promised:
                continue
            # 例外：名字里明说「站内搜…」就等于告诉用户「这条链接到站点，你要自己搜」，
            # 不再是「点开就是那份文件」的承诺。
            assert "站内" in c["name"] or "搜" in c["name"], (
                f"{info_type} 的入口 {c['name']} 承诺了一份具体文档（{promised}），"
                f"但 url 是站点根 {c['url']} —— 点开只会到首页"
            )
