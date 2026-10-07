"""验收 5 条。全部使用假 LLM + 内存向量库，无密钥也能跑。"""

from __future__ import annotations

from conftest import post_plan

# ---------------------------------------------------------------- 验收 1
def test_01_missing_slots_asks_and_does_not_retrieve(client, store):
    """「想去泰国玩」→ type=clarify，且不触发检索与工具。"""
    store.search_calls = 0
    store.scan_calls = 0
    body = post_plan(client, "想去泰国玩", session_id="acc1")

    assert body["type"] == "clarify"
    assert body["question"]
    # destination 已抽到，缺 date_range / budget / party
    assert set(body["missing_slots"]) == {"date_range", "budget", "party"}
    assert store.search_calls == 0, "缺关键槽位时不得调用检索"
    assert store.scan_calls == 0, "缺关键槽位时不得调用工具"


# ---------------------------------------------------------------- 验收 2
def test_02_family_xiamen_child_pacing_and_citations(client):
    """「带 6 岁孩子去厦门 5 天，预算 8000」→ 命中 family，含儿童节奏，事实有 citation。"""
    body = post_plan(client, "带 6 岁孩子去厦门 5 天，预算 8000", session_id="acc2")

    assert body["type"] == "plan"
    assert "family" in body["route"]["scenes"]
    assert body["route"]["fusion_policy"] in {"weighted_union", "priority_override"}
    assert body["route"]["planner_mode"] == "plan_execute"
    assert body["route"]["max_repair_rounds"] == 1

    itinerary = body["itinerary"]
    assert len(itinerary["days"]) == 5
    assert itinerary["destination"] == "厦门"

    joined_notes = " ".join(itinerary["notes"])
    assert "儿童节奏" in joined_notes and "午休" in joined_notes
    assert any("午休" in act["name"] for day in itinerary["days"] for act in day["activities"])

    # 具体事实必须有 citation，且 citation 必须在 citations 清单里
    keys = {c["citation_key"] for c in body["citations"]}
    assert keys, "成稿必须带引用"
    for day in itinerary["days"]:
        for act in day["activities"]:
            if act["cost"] is not None or any(
                token in act["detail"] for token in ("开馆", "闭馆", "开放", "门票")
            ):
                assert act["citation_key"], f"具体事实缺引用：{act['name']}"
                assert act["citation_key"] in keys

    fact = next(c for c in body["validation"]["checks"] if c["name"] == "fact")
    assert fact["status"] == "pass"


# ---------------------------------------------------------------- 验收 3
def test_03_budget_chiangmai_stays_within_buffer(client):
    """「两人穷游清迈 4 天，每天 300」→ 命中 budget，费用不超过预算 + 10%。"""
    body = post_plan(client, "两人穷游清迈 4 天，每天 300", session_id="acc3")

    assert body["type"] == "plan"
    assert "budget" in body["route"]["scenes"]

    budget = body["itinerary"]["budget"]
    assert budget is not None and budget["lines"]
    # 「每天 300」按人均每日理解：300 × 4 天 × 2 人
    assert budget["user_budget"] == 2400
    limit = budget["user_budget"] * (1 + budget["buffer_ratio"])
    assert budget["total"] <= limit, f"合计 {budget['total']} 超过上限 {limit}"
    assert budget["within_budget"] is True

    recheck = next(c for c in body["validation"]["checks"] if c["name"] == "budget")
    assert recheck["status"] in {"pass", "warn"}


# ---------------------------------------------------------------- 验收 4
def test_04_family_and_budget_fuse(client):
    """「带孩子穷游厦门 3 天，预算 2000」→ family 与 budget 同时在 RouteConfig.scenes。"""
    body = post_plan(client, "带孩子穷游厦门 3 天，预算 2000", session_id="acc4")

    assert body["type"] == "plan"
    scenes = body["route"]["scenes"]
    assert "family" in scenes and "budget" in scenes

    # 资料库与工具取并集
    scopes = set(body["route"]["knowledge_scopes"])
    assert {"scene:family", "scene:budget"} <= scopes
    assert set(body["route"]["tool_allowlist"]) >= {"opening_hours", "budget_sum"}

    # 输出 Schema 章节顺序跟主场景
    assert body["route"]["output_schema_id"] == "travel_plan_v1"
    assert body["itinerary"]["scenes"][0] == scenes[0]

    # 预算约束在多标签融合下依然生效
    budget = body["itinerary"]["budget"]
    assert budget["user_budget"] == 2000
    assert budget["total"] <= budget["user_budget"] * 1.1


# ---------------------------------------------------------------- 验收 5
def test_05_visa_checklist_has_source_and_effective_date(client):
    """「办泰国签证要准备什么」→ checklist 含来源与生效日，正文含不构成法律意见。"""
    body = post_plan(client, "办泰国签证要准备什么", session_id="acc5")

    assert body["type"] == "plan"
    assert "visa" in body["route"]["scenes"]
    assert body["route"]["knowledge_scopes"].count("realtime:visa") == 1

    checklist = body["checklist"]
    assert checklist
    for item in checklist:
        assert "来源：" in item, item
        assert "生效日：" in item, item
        assert "来源：缺失" not in item and "生效日：缺失" not in item, item

    import json

    dumped = json.dumps(body, ensure_ascii=False)
    assert "不构成法律意见" in dumped

    # 行程里每条材料的引用都必须能在 citations 里查到（工具返回的逐条凭据要接上）
    keys = {c["citation_key"] for c in body["citations"]}
    cited = [
        act["citation_key"]
        for day in body["itinerary"]["days"]
        for act in day["activities"]
        if act["citation_key"]
    ]
    assert cited, "签证正文必须带引用"
    assert set(cited) <= keys, f"有引用解析不到来源：{set(cited) - keys}"

    # 签证场景不强制 budget / date_range
    assert body["itinerary"]["budget"] is None
    assert body["validation"]["passed"] is True
    assert body["validation"]["degraded"] is False
    # 没有触发无谓回环
    assert body["validation"]["round"] == 0
    fact = next(c for c in body["validation"]["checks"] if c["name"] == "fact")
    assert fact["status"] == "pass"
