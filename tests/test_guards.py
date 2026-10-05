"""守护行为的定向测试：预算回环、事实降级、工具闸门、时效过滤、模型字段钉死。

这些不是 5 条验收的一部分，但它们是「校验有牙齿」的证据 —— 少了它们，
验收全绿也可能只是因为假 LLM 恰好没触发这些分支。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import FROZEN_TODAY, post_plan

from app.main import create_app
from app.retrieve import retrieve_context
from app.router import build_route
from app.schemas import (
    Activity,
    BudgetLine,
    BudgetSummary,
    DayPlan,
    Itinerary,
    RetrievedChunk,
    RetrievedContext,
    RouteConfig,
    SceneClassificationResult,
    SceneLabel,
)
from app.validate import check_fact


# ---------------------------------------------------------------- 预算回环
def test_budget_overrun_triggers_exactly_one_repair_round(client):
    """预算硬超支 → 回到 generate 一轮 → 仍超支则记 unresolved，不做第 2 轮。"""
    body = post_plan(client, "带孩子穷游厦门 2 天，预算 500", session_id="g1")

    assert body["type"] == "plan"
    report = body["validation"]
    assert report["round"] == 1, "应当恰好发生一次回环"
    budget_check = next(c for c in report["checks"] if c["name"] == "budget")
    assert budget_check["status"] in {"warn", "repair"}
    assert any("预算" in u for u in report["unresolved"])


def test_budget_overrun_repaired_by_swapping_expensive_line(client):
    """有更便宜的同类项时，直接替换高价项即可回到缓冲上限内（不触发回环）。"""
    body = post_plan(client, "两人穷游清迈 3 天，预算 1300", session_id="g2")

    report = body["validation"]
    budget_check = next(c for c in report["checks"] if c["name"] == "budget")
    assert budget_check["status"] == "warn"
    assert any("高价项替换" in a for a in report["repair_actions"])

    budget = body["itinerary"]["budget"]
    assert budget["total"] <= budget["user_budget"] * 1.1
    assert report["round"] == 0


# ---------------------------------------------------------------- 事实降级
def _context_with(*chunks: tuple) -> tuple[RetrievedContext, dict]:
    """chunks: (chunk_id, text) 或 (chunk_id, text, layer)。"""
    rc = []
    index = {}
    for item in chunks:
        cid, text = item[0], item[1]
        layer = item[2] if len(item) > 2 else "scene"
        rc.append(
            RetrievedChunk(
                chunk_id=cid, layer=layer, score=1.0, text=text,
                source="测试来源", fresh_until=None, citation_key=cid,
            )
        )
        index[cid] = None
    ctx = RetrievedContext(
        query="q", filters={}, chunks=rc, layer_quota={}, dropped_stale=0, retriever_version="t"
    )
    return ctx, index


def _itinerary_with(activity: Activity) -> Itinerary:
    return Itinerary(
        title="t",
        destination="厦门",
        days=[DayPlan(day=1, area="厦门", activities=[activity])],
        budget=BudgetSummary(lines=[BudgetLine(name="门票", amount=100, category="门票")], total=100),
    )


def _route(scenes: list[str], **kw) -> RouteConfig:
    from app.schemas import PromptRefs

    base = dict(
        route_id="route.test",
        scenes=scenes,
        fusion_policy="weighted_union",
        prompt_refs=PromptRefs(base="prompts/base.md", overlays=[]),
        knowledge_scopes=["scene:family"],
        tool_allowlist=["opening_hours"],
        output_schema_id="travel_plan_v1",
        guardrail_ids=[],
        max_repair_rounds=1,
        version_pin="v",
    )
    base.update(kw)
    return RouteConfig(**base)


def test_fact_check_downgrades_uncited_fact_to_pending():
    ctx, index = _context_with(("c1", "某馆 10:00-18:00 开放"))
    itinerary = _itinerary_with(
        Activity(time="10:00", name="某馆", detail="该馆 10:00 开放，门票 100 元", cost=None, citation_key="编造的key")
    )

    check, actions, unresolved, verified = check_fact(
        itinerary=itinerary, context=ctx, chunk_index=index, tool_results={}, route=_route(["family"]), round_no=0
    )

    assert check.status == "repair"
    act = itinerary.days[0].activities[0]
    assert act.citation_key is None
    assert act.cost is None
    assert act.detail.startswith("待核实")
    assert actions and not verified


def test_fact_check_passes_on_verified_citation():
    ctx, index = _context_with(("c1", "某馆 10:00-18:00 开放"))
    itinerary = _itinerary_with(
        Activity(time="10:00", name="某馆", detail="该馆 10:00 开放", cost=None, citation_key="c1")
    )

    check, _, _, verified = check_fact(
        itinerary=itinerary, context=ctx, chunk_index=index, tool_results={}, route=_route(["family"]), round_no=0
    )

    assert check.status == "pass"
    assert verified == ["c1"]
    assert itinerary.days[0].activities[0].detail == "该馆 10:00 开放"


def test_hedged_detail_is_not_treated_as_a_fact():
    """「费用和时间需自行确认」不是事实陈述 —— 判它降级会白吃掉唯一一次回环。

    真实跑批里模型几乎每份行程都带一条「返程 / 自由活动」，早期用关键词做判据
    （detail 里出现「费用」就算事实）会稳定误判，把那次回环用在没意义的地方。
    """
    hedged = "根据返程交通方式，费用和时间需自行确认。"
    ctx, index = _context_with(("c1", "某馆 10:00-18:00 开放"))
    itinerary = _itinerary_with(Activity(time="傍晚", name="返程", detail=hedged, cost=None, citation_key=None))

    check, actions, unresolved, _ = check_fact(
        itinerary=itinerary, context=ctx, chunk_index=index, tool_results={}, route=_route(["family"]), round_no=0
    )

    assert check.status == "pass", "对冲句不该触发降级"
    assert actions == [] and unresolved == []
    assert itinerary.days[0].activities[0].detail == hedged, "正文不该被加上「待核实」"


def test_fact_bearing_requires_a_concrete_assertion():
    """判据要两头都对：带数字的金额算事实，纯散步不算，cost 非空一律算。"""
    from app.validate import is_fact_bearing

    # 旧版漏判：没有「价格/费用」字样但有具体金额
    assert is_fact_bearing(Activity(time="中午", name="午餐", detail="岛上小吃人均20-30元/餐。", cost=None))
    assert is_fact_bearing(Activity(time="上午", name="博物馆", detail="开放时间 9:00-17:00。", cost=None))
    assert is_fact_bearing(Activity(time="上午", name="科技馆", detail="周一闭馆，门票 40 元。", cost=None))
    # 签证域词汇也是可对质的断言
    assert is_fact_bearing(Activity(time="—", name="在职证明", detail="需提交在职证明与近 6 个月银行流水。", cost=None))
    # 没有任何可对质的内容
    assert not is_fact_bearing(Activity(time="傍晚", name="散步", detail="沿江边走走，看看夜景。", cost=None))
    assert not is_fact_bearing(Activity(time="傍晚", name="返程", detail="按返程班次自行安排。", cost=None))
    # cost 是结构化金额字段，与措辞无关（0 也表示「免费」这个主张）
    assert is_fact_bearing(Activity(time="上午", name="休息", detail="随便走走。", cost=0))


def test_visa_check_blocks_when_policy_lacks_source_or_effective_date():
    ctx, index = _context_with(("rt-x", "某国签证需材料 A", "realtime"))

    class _Chunk:
        source_url = None
        effective_date = None
        metadata: dict = {}

    check, _, unresolved, _ = check_fact(
        itinerary=_itinerary_with(Activity(time="—", name="材料A", detail="需材料 A", citation_key="rt-x")),
        context=ctx,
        chunk_index={"rt-x": _Chunk()},
        tool_results={},
        route=_route(["visa"], knowledge_scopes=["realtime:visa"], tool_allowlist=["visa_policy"]),
        round_no=0,
    )

    assert check.status == "block"
    assert check.severity == "block"
    assert unresolved


# ---------------------------------------------------------------- 工具闸门
def test_tool_outside_allowlist_is_refused(deps):
    result = deps.toolbox.call("visa_policy", {"country": "泰国"}, allowlist=["opening_hours"])
    assert result.ok is False
    assert "未授权" in (result.error or "")


def test_tool_outside_scope_is_refused(deps):
    result = deps.toolbox.call("search_flights", {}, allowlist=["search_flights"])
    assert result.ok is False
    assert "不在本期实现范围" in (result.error or "")


# ---------------------------------------------------------------- 时效过滤
def test_stale_realtime_chunks_are_dropped(deps):
    classification = SceneClassificationResult(
        request_id="r",
        labels=[SceneLabel(scene_id="visa", confidence=0.9)],
        primary_scene="visa",
        slots={"destination_country": "泰国", "destination": "泰国"},
        missing_slots=[],
        rewritten_query="泰国 签证 材料清单 办理",
        classifier_version="t",
    )
    route = build_route(settings=deps.settings, classification=classification)

    bundle = retrieve_context(
        settings=deps.settings,
        store=deps.store,
        embedder=deps.embedder,
        route=route,
        query=classification.rewritten_query,
        slots=classification.slots,
        today=FROZEN_TODAY,
    )

    assert bundle.context.dropped_stale >= 1, "种子库里的过期 realtime 条目必须被丢弃"
    for chunk in bundle.context.chunks:
        if chunk.layer == "realtime" and chunk.fresh_until:
            assert chunk.fresh_until >= FROZEN_TODAY


def test_visa_route_raises_realtime_quota(deps):
    assert deps.settings.layer_quota("visa")["realtime"] == pytest.approx(0.6)
    assert deps.settings.layer_quota("family")["realtime"] == pytest.approx(0.3)


# ---------------------------------------------------------------- 字段钉死
def test_route_config_pins_repair_rounds_to_one():
    with pytest.raises(Exception):
        _route(["family"], max_repair_rounds=2)


def test_only_the_four_declared_scenes_exist(deps):
    assert set(deps.settings.scene_ids) == {"family", "budget", "roadtrip", "visa"}
    assert deps.settings.fallback_scene == "general"


# ---------------------------------------------------------------- 场景边界
class _BogusLabelLLM:
    """故意返回任务书禁止的独立场景名。"""

    provider = "bogus"

    def complete_json(self, *, role, prompt, schema, context):
        if role == "classifier":
            return {
                "labels": [
                    {"scene_id": "honeymoon", "confidence": 0.95},
                    {"scene_id": "business", "confidence": 0.9},
                    {"scene_id": "elderly", "confidence": 0.88},
                    {"scene_id": "family", "confidence": 0.6},
                ],
                "primary_scene": "honeymoon",
                "slots": {},
                "missing_slots": [],
                "clarify_question": None,
                "rewritten_query": "蜜月亲子",
            }
        raise AssertionError("本用例不应走到生成节点")


def test_honeymoon_business_elderly_are_not_scenes(deps):
    from app.classifier import classify

    result = classify(
        settings=deps.settings,
        llm=_BogusLabelLLM(),
        message="带孩子去厦门 5 天，预算 8000，两个人",
        session_slots={},
        request_id="r",
    )
    ids = {lb.scene_id for lb in result.labels}
    assert ids == {"family"}, f"未声明的场景必须被丢弃，实际保留：{ids}"
    assert result.primary_scene == "family"


def test_low_confidence_with_complete_slots_falls_back_to_general(deps):
    """最高分 < 0.45 且槽位已齐 → route.general，不追问。"""
    client = TestClient(create_app(deps))
    body = post_plan(client, "去青岛玩 4 天，预算 5000，两个人", session_id="g-general")

    assert body["type"] == "plan"
    assert body["route"]["route_id"] == "route.general"
    assert body["route"]["scenes"] == ["general"]


# ---------------------------------------------------------------- 置信度门控分支
def _classification(*labels: tuple[str, float], primary: str | None = None) -> SceneClassificationResult:
    """labels: (scene_id, confidence) 序列；primary 默认取最高分标签。"""
    items = [SceneLabel(scene_id=s, confidence=c) for s, c in labels]
    top = max((c for _, c in labels), default=0.0)
    top_scene = next((s for s, c in labels if c == top), "general")
    return SceneClassificationResult(
        request_id="r",
        labels=items,
        primary_scene=primary or top_scene,
        slots={"destination": "厦门", "date_range": "4 天", "budget": 8000, "party": "2 位成人"},
        missing_slots=[],
        rewritten_query="带孩子穷游厦门 4 天",
        classifier_version="t",
    )


def test_gating_thresholds_match_the_spec(deps):
    """阈值是有出处的常量（routes.yaml 的 classifier 段），不能随手改。"""
    assert deps.settings.threshold_clarify == pytest.approx(0.45)
    assert deps.settings.threshold_hard_route == pytest.approx(0.70)


def test_soft_fusion_band_keeps_both_labels(deps):
    """0.45–0.70：所有 ≥ 0.45 的标签都参与，不因低于 0.70 被裁掉。"""
    route = build_route(
        settings=deps.settings,
        classification=_classification(("family", 0.62), ("budget", 0.58)),
    )

    assert route.scenes == ["family", "budget"]
    assert route.route_id == "route.family+budget"
    assert route.fusion_policy == "weighted_union"  # 分差 0.04 < 0.20 → 真并集


def test_soft_band_upper_edge_includes_second_label_hard_route_would_drop(deps):
    """top == 0.70 仍落在软融合区间（判定用 <=）。

    区间上界是语义分水岭：软融合只看 clarify_below，0.50 的二标签参与；
    硬提交分支的 floor 是 max(0.70, top*0.6)，同一个 0.50 会被剔除。
    同一组标签在两分支下的结果必须不同，否则阈值等于没起作用。
    """
    soft = build_route(
        settings=deps.settings,
        classification=_classification(("family", 0.70), ("budget", 0.50)),
    )
    hard = build_route(
        settings=deps.settings,
        classification=_classification(("family", 0.71), ("budget", 0.50)),
    )

    assert soft.scenes == ["family", "budget"], "0.70 应走软融合"
    assert hard.scenes == ["family"], "0.71 走硬提交，0.50 低于 floor 被剔除"


def test_hard_route_prunes_second_label_below_floor(deps):
    """top > 0.70：参与标签须 ≥ max(0.70, top*0.6)，0.50 的二标签被裁掉。"""
    route = build_route(
        settings=deps.settings,
        classification=_classification(("family", 0.95), ("budget", 0.50)),
    )

    assert route.scenes == ["family"]
    assert route.fusion_policy == "weighted_union"  # 单标签


def test_priority_override_when_top_leads_by_the_gap(deps):
    """多标签且头名领先 ≥ 0.20 → priority_override（主场景结构覆盖）。"""
    route = build_route(
        settings=deps.settings,
        classification=_classification(("family", 0.95), ("budget", 0.72)),
    )

    assert route.scenes == ["family", "budget"]
    assert route.fusion_policy == "priority_override"


def test_weighted_union_when_top_and_second_are_close(deps):
    """两标签都过 floor 但分差 < 0.20 → weighted_union（真并集）。"""
    route = build_route(
        settings=deps.settings,
        classification=_classification(("family", 0.95), ("budget", 0.90)),
    )

    assert route.scenes == ["family", "budget"]
    assert route.fusion_policy == "weighted_union"


def test_fusion_takes_union_of_scopes_tools_and_guardrails(deps):
    """融合后资料库范围 / 工具白名单 / 守卫项取并集，顺序跟主场景。"""
    route = build_route(
        settings=deps.settings,
        classification=_classification(("family", 0.62), ("budget", 0.58)),
    )

    assert route.knowledge_scopes == ["scene:family", "general", "scene:budget"]
    assert set(route.tool_allowlist) == {"opening_hours", "budget_sum"}
    assert route.guardrail_ids == ["safety_disclaimer", "minor_pacing"]


def test_labels_below_clarify_threshold_are_excluded_even_if_primary(deps):
    """低于 0.45 的标签不参与融合，即便模型把它标成 primary_scene。"""
    route = build_route(
        settings=deps.settings,
        classification=_classification(("family", 0.60), ("budget", 0.30), primary="budget"),
    )

    assert route.scenes == ["family"]
    assert "budget" not in route.knowledge_scopes
    assert route.fusion_policy == "weighted_union"


def test_bottom_edge_at_exactly_045_routes_to_scene_but_044_falls_back(deps):
    """top == 0.45 不算低置信度（只有 < 0.45 才回落 general）。"""
    at_edge = build_route(
        settings=deps.settings,
        classification=_classification(("roadtrip", 0.45), ("budget", 0.20)),
    )
    below = build_route(
        settings=deps.settings,
        classification=_classification(("roadtrip", 0.44), ("budget", 0.20)),
    )

    assert at_edge.scenes == ["roadtrip"]
    assert below.scenes == ["general"]
    assert below.route_id == "route.general"
