"""场景分类节点。

小模型只负责「语义判断」：哪些场景相关、置信度多少、改写后的检索查询。
槽位里的数字（天数/预算/人数）以确定性抽取为准，模型给的对应字段会被丢弃 ——
见 slots.py 顶部的说明。
"""

from __future__ import annotations

from typing import Any

from .config import Settings
from .i18n import detect_language
from .llm import LLMClient
from .prompts import build_classify_prompt
from .schemas import SceneClassificationResult, SceneLabel
from .slots import (
    build_clarify_question,
    extract_slots,
    merge_slots,
    missing_slots,
    required_slots_for,
    scene_hints,
)

# 这些键一律以确定性抽取为准，模型返回的同名值丢弃
DETERMINISTIC_KEYS = {
    "destination",
    "destination_country",
    "destination_aliases",
    "destination_surface",
    "date_range",
    "days",
    "start_date",
    "budget",
    "daily_budget",
    "budget_basis",
    "party",
    "party_size",
    "party_raw",
    "has_children",
    "child_age",
    "has_elder",
}


def classify(
    *,
    settings: Settings,
    llm: LLMClient,
    message: str,
    session_slots: dict[str, Any],
    request_id: str,
) -> SceneClassificationResult:
    deterministic = extract_slots(message, settings)
    merged = merge_slots(session_slots, deterministic)
    hints = scene_hints(message, settings)

    ctx = {
        "message": message,
        "session_slots": session_slots,
        "slots": merged,
        "hints": hints,
        "scene_ids": settings.scene_ids,
        "scene_keywords": {sid: settings.scene_cfg(sid).get("keywords", []) for sid in settings.scene_ids},
        "fallback_scene": settings.fallback_scene,
        "classifier_version": settings.classifier_version,
    }
    raw = llm.complete_json(
        role="classifier",
        prompt=build_classify_prompt(
            settings=settings, message=message, session_slots=session_slots, hinted=hints
        ),
        schema=SceneClassificationResult.model_json_schema(),
        context=ctx,
    )

    labels: list[SceneLabel] = []
    for item in raw.get("labels") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("scene_id") or "")
        # 只认 routes.yaml 里定义的场景；蜜月/商务/老年之类一律丢弃，不新开场景
        if sid not in settings.scene_ids:
            continue
        try:
            conf = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            conf = 0.0
        labels.append(SceneLabel(scene_id=sid, confidence=round(conf, 4)))
    labels.sort(key=lambda lb: (-lb.confidence, settings.scene_ids.index(lb.scene_id) if lb.scene_id in settings.scene_ids else 99))

    # 模型补空缺：只接受非确定性键，且不覆盖已抽到的值
    for k, v in (raw.get("slots") or {}).items():
        if k in DETERMINISTIC_KEYS or k in merged or v in (None, "", []):
            continue
        merged[k] = v

    primary = labels[0].scene_id if labels else settings.fallback_scene
    if labels:
        required = required_slots_for([lb.scene_id for lb in labels], settings)
    else:
        required = list(settings.required_slots(settings.fallback_scene))
    miss = missing_slots(merged, required)

    # 这个字段现在只作记录（图里的追问一律由 route_node 确定性装配，见 graph.py）：
    # 模型给的问句语言没保证，追问要跟用户语言。
    question = raw.get("clarify_question") or (
        build_clarify_question(miss, merged, language=detect_language(message)) if miss else None
    )
    rewritten = str(raw.get("rewritten_query") or "").strip() or message

    return SceneClassificationResult(
        request_id=request_id,
        labels=labels,
        primary_scene=primary,
        slots=merged,
        missing_slots=miss,
        clarify_question=question,
        rewritten_query=rewritten,
        classifier_version=settings.classifier_version,
    )
