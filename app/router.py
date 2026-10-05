"""路由节点：置信度门控 + 多标签融合 + RouteConfig 生成。

门控规则（阈值来自 routes.yaml 的 classifier 段）：
- 最高分 < clarify_below(0.45)      → 缺槽追问；槽齐 → route.general
- clarify_below ~ soft_fusion_max   → 软融合：所有 ≥ clarify_below 的标签都参与
- > soft_fusion_max(0.70)           → 提交路由：参与标签需 ≥ max(0.70, top*0.6)

融合策略：
- 单标签                          → weighted_union
- 多标签且头名领先 ≥ 0.20         → priority_override（主场景结构覆盖，合规层优先）
- 多标签且接近                    → weighted_union（真并集）

资料库与工具取并集；输出 Schema 章节顺序跟主场景（置信度最高的标签）。
"""

from __future__ import annotations

from .config import Settings
from .i18n import DEFAULT as DEFAULT_LANGUAGE
from .schemas import PromptRefs, RouteConfig, SceneClassificationResult

PRIORITY_GAP = 0.20


def build_route(
    *, settings: Settings, classification: SceneClassificationResult, language: str = DEFAULT_LANGUAGE
) -> RouteConfig | None:
    scores = classification.confidences()
    top = max(scores.values(), default=0.0)
    fallback = settings.fallback_scene

    if top < settings.threshold_clarify:
        participating = [fallback]
    elif top <= settings.threshold_hard_route:
        participating = [s for s in settings.scene_ids if scores.get(s, 0.0) >= settings.threshold_clarify]
    else:
        floor = max(settings.threshold_hard_route, top * 0.6)
        participating = [s for s in settings.scene_ids if scores.get(s, 0.0) >= floor]
    if not participating:
        participating = [fallback]

    ordered = sorted(
        participating,
        key=lambda s: (-scores.get(s, 0.0), settings.scene_ids.index(s) if s in settings.scene_ids else 99),
    )

    # 主场景保留 classification 的判定，但必须落在参与集合内
    primary = classification.primary_scene if classification.primary_scene in ordered else ordered[0]
    if primary != ordered[0]:
        ordered = [primary] + [s for s in ordered if s != primary]

    second = scores.get(ordered[1], 0.0) if len(ordered) > 1 else 0.0
    if len(ordered) > 1 and (scores.get(ordered[0], 0.0) - second) >= PRIORITY_GAP:
        fusion = "priority_override"
    else:
        fusion = "weighted_union"

    knowledge_scopes: list[str] = []
    tool_allowlist: list[str] = []
    guardrail_ids: list[str] = []
    for sid in ordered:
        cfg = settings.scene_cfg(sid)
        for scope in cfg.get("knowledge_scopes", []) or []:
            if scope not in knowledge_scopes:
                knowledge_scopes.append(scope)
        for tool in cfg.get("tool_allowlist", []) or []:
            if tool not in tool_allowlist and tool in settings.available_tools:
                tool_allowlist.append(tool)
        for gid in cfg.get("guardrail_ids", []) or []:
            if gid not in guardrail_ids:
                guardrail_ids.append(gid)

    schema_id = settings.scene_cfg(ordered[0]).get("output_schema_id", "travel_plan_v1")

    from .prompts import build_overlay_refs

    safety_ref = settings.prompt_paths.get("safety_overlay", "prompts/safety.md")
    overlays = [safety_ref] + build_overlay_refs(settings, ordered)

    return RouteConfig(
        route_id=f"route.{or_join(ordered)}",
        scenes=ordered,
        fusion_policy=fusion,  # type: ignore[arg-type]
        prompt_refs=PromptRefs(base=settings.prompt_paths.get("base", "prompts/base.md"), overlays=overlays),
        knowledge_scopes=knowledge_scopes,
        tool_allowlist=tool_allowlist,
        output_schema_id=schema_id,
        guardrail_ids=guardrail_ids,
        planner_mode="plan_execute",
        max_repair_rounds=1,
        version_pin=settings.version_pin,
        output_language=language,
    )


def or_join(items: list[str]) -> str:
    return "+".join(items) if items else "general"
