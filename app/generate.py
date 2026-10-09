"""generate 节点：Plan-and-Execute 的产物组装。

模型只负责产出行程正文（Itinerary + suggestions）。`citations` 与 `checklist` 由程序
从 citation_key 反查组装 —— 引用必须可追溯到具体条目，不能让模型自己编一串凭据出来。
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from pydantic import ValidationError

from .config import Settings
from .i18n import DEFAULT as DEFAULT_LANGUAGE
from .i18n import guardrail_text, t
from .llm import LLMClient
from .prompts import build_generate_prompt
from .schemas import (
    Citation,
    GeneratorOutput,
    Itinerary,
    RetrievedContext,
    RouteConfig,
    SceneClassificationResult,
    ToolResult,
)
from .tools import evidence_map


def _as_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class GenerationError(RuntimeError):
    pass


def _fake_context(
    *,
    slots: dict[str, Any],
    route: RouteConfig,
    classification: SceneClassificationResult,
    context: RetrievedContext,
    chunk_index: dict[str, Any],
    tool_results: dict[str, ToolResult],
    settings: Settings,
    validation_findings: list[str],
    round_no: int,
) -> dict[str, Any]:
    """给离线假 LLM 的结构化输入。真实客户端走 prompt，不看这个。"""
    chunks = []
    for rc in context.chunks:
        chunk = chunk_index.get(rc.chunk_id)
        chunks.append(
            {
                "chunk_id": rc.chunk_id,
                "citation_key": rc.citation_key,
                "layer": rc.layer,
                "score": rc.score,
                "text": rc.text,
                "source": rc.source,
                "fresh_until": rc.fresh_until.isoformat() if rc.fresh_until else None,
                "metadata": dict(getattr(chunk, "metadata", {}) or {}),
            }
        )
    return {
        "slots": slots,
        "primary_scene": classification.primary_scene,
        "scenes": route.scenes,
        "chunks": chunks,
        "tool_results": {name: tr.model_dump(mode="json") for name, tr in tool_results.items()},
        "guardrail_ids": route.guardrail_ids,
        # guardrail 文案会直接写进用户可见的 notes / disclaimers → 跟输出语言
        "guardrail_texts": {
            gid: guardrail_text(settings, gid, route.output_language) for gid in route.guardrail_ids
        },
        "validation_findings": validation_findings,
        "round": round_no,
        "fusion_policy": route.fusion_policy,
    }


def _coerce_flattened(raw: Any) -> GeneratorOutput | None:
    """把「Itinerary 字段摊在顶层」的模型输出包回 `{itinerary: …, suggestions: …}`。

    只在能明确看出它是一份行程（含 days / title / destination 之一）时才认；
    其余情况返回 None，让调用方照旧抛 GenerationError（不掩盖真正的 schema 错误）。
    """
    if not isinstance(raw, dict) or "itinerary" in raw:
        return None
    # `suggestions` 与 `followups` 是**顶层**的附带数组，不属于 Itinerary ——
    # 摊平时必须把它们摘出来原样带过去，否则模型写的建议会被当成 Itinerary 的多余字段。
    carry = {k: raw[k] for k in ("suggestions", "followups") if k in raw}
    inner = {k: v for k, v in raw.items() if k not in ("suggestions", "followups")}
    if not ({"days", "title", "destination"} & set(inner)):
        return None
    try:
        return GeneratorOutput.model_validate({"itinerary": inner, **carry})
    except ValidationError:
        return None


def generate_plan(
    *,
    settings: Settings,
    llm: LLMClient,
    route: RouteConfig,
    classification: SceneClassificationResult,
    context: RetrievedContext,
    chunk_index: dict[str, Any],
    tool_results: dict[str, ToolResult],
    round_no: int = 0,
    validation_findings: list[str] | None = None,
    locked_segments: list[str] | None = None,
    previous_itinerary: dict[str, Any] | None = None,
    revision_note: str = "",
) -> GeneratorOutput:
    findings = list(validation_findings or [])
    prompt = build_generate_prompt(
        settings=settings,
        scenes=route.scenes,
        primary_scene=classification.primary_scene,
        rewritten_query=classification.rewritten_query,
        slots=classification.slots,
        context=context,
        tool_results=list(tool_results.values()),
        guardrail_ids=route.guardrail_ids,
        validation_findings=findings,
        locked_segments=locked_segments,
        output_language=route.output_language,
        previous_itinerary=previous_itinerary,
        revision_note=revision_note,
    )

    raw = llm.complete_json(
        role="generator",
        prompt=prompt,
        schema=GeneratorOutput.model_json_schema(),
        context=_fake_context(
            slots=classification.slots,
            route=route,
            classification=classification,
            context=context,
            chunk_index=chunk_index,
            tool_results=tool_results,
            settings=settings,
            validation_findings=findings,
            round_no=round_no,
        ),
    )

    try:
        output = GeneratorOutput.model_validate(raw)
    except ValidationError as exc:
        # 容错：模型偶尔把 Itinerary 的字段**摊在顶层**（没有包进 `itinerary`）。
        # 提示词里 `{{output_schema}}` 给的是 Itinerary 的 schema，而这里校验的是包一层
        # 的 `GeneratorOutput` —— 两份 schema 不是同一个形状，模型照着提示词写就会对不上。
        # 旧行为直接抛 GenerationError，而那个错误当时又没落到状态通道上，于是整条链路以
        # KeyError('draft') 收场（真 bug 2026-10-06 P11 英文实测：整句请求返回 degraded）。
        coerced = _coerce_flattened(raw)
        if coerced is None:
            raise GenerationError(f"生成结果不符合 JSON Schema：{exc.errors()[:5]}") from exc
        output = coerced

    _postprocess(output.itinerary, settings=settings, route=route, classification=classification)
    _reconcile_revision_days(output.itinerary, revision_note=revision_note,
                             previous_itinerary=previous_itinerary)
    return output


# 迭代里「删掉某一天」时，模型经常**只改那天的内容 / 只改标题**，却把 `days` 数组
# 原样留着 —— 用户说「去掉第 4 天」而行程仍是 5 天（真 bug 2026-10-06 长会话 C1，
# 中文偶尔能跟、英文基本不跟）。天数变化是**确定性可算**的，不该指望模型每次都听。
_REMOVE_DAY_RE = re.compile(
    r"(?:去掉|删掉|删除|拿掉|取消|不要|remove|drop|delete)\s*(?:第\s*|day\s*)?"
    r"(\d+|[一二三四五六七八九十两])\s*(?:天|日)?",
    re.IGNORECASE,
)
_DAY_WORD = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _reconcile_revision_days(
    itinerary: Itinerary, *, revision_note: str, previous_itinerary: dict[str, Any] | None
) -> None:
    """迭代时把「删掉第 N 天」**落实**到 `days` 数组（长度没变才动手）。

    只在**明确要删**、且模型**没缩数组**时兜底 —— 其余情况一概不碰，免得与模型打架。
    """
    if not revision_note or not previous_itinerary:
        return
    m = _REMOVE_DAY_RE.search(revision_note)
    if not m:
        return
    token = m.group(1)
    n = int(token) if token.isdigit() else _DAY_WORD.get(token)
    if not n:
        return
    prev_days = (previous_itinerary or {}).get("days") or []
    days = list(itinerary.days or [])
    if not days or len(days) != len(prev_days):
        return  # 模型已经缩过了（或结构不同）→ 不动
    keep = [d for i, d in enumerate(days) if getattr(d, "day", i + 1) != n]
    if len(keep) == len(days):
        keep = [d for i, d in enumerate(days) if i != n - 1]
    if not keep or len(keep) == len(days):
        return
    for i, d in enumerate(keep, start=1):
        d.day = i
    itinerary.days = keep


def _postprocess(
    itinerary: Itinerary, *, settings: Settings, route: RouteConfig, classification: SceneClassificationResult
) -> None:
    """补齐模型容易漏掉的硬性字段。只补不删。"""
    if not itinerary.destination:
        itinerary.destination = str(
            classification.slots.get("destination") or classification.slots.get("destination_country") or ""
        )
    if not itinerary.scenes:
        itinerary.scenes = list(route.scenes)
    for gid in route.guardrail_ids:
        # guardrail 正文直接进 `disclaimers`（用户可见）→ 必须跟输出语言。
        # 中文仍以 routes.yaml 为唯一真源，见 i18n.guardrail_text。
        text = guardrail_text(settings, gid, route.output_language)
        if text and text not in itinerary.disclaimers:
            itinerary.disclaimers.append(text)
    # 日期闭合：有出发日就逐日推出来，不要让模型自由发挥
    start = classification.slots.get("start_date")
    if start:
        from datetime import date, timedelta

        try:
            base = date.fromisoformat(str(start))
        except ValueError:
            return
        for idx, day in enumerate(itinerary.days):
            day.date = (base + timedelta(days=idx)).isoformat()
    if itinerary.budget is not None:
        recomputed = round(sum(float(line.amount) for line in itinerary.budget.lines), 2)
        itinerary.budget.total = recomputed
        if itinerary.budget.user_budget is not None:
            itinerary.budget.within_budget = recomputed <= itinerary.budget.user_budget * (
                1 + itinerary.budget.buffer_ratio
            )


# ------------------------------------------------------------------ 引用
def assemble_citations(
    *,
    itinerary: Itinerary,
    context: RetrievedContext,
    chunk_index: dict[str, Any],
    tool_results: dict[str, ToolResult],
) -> list[Citation]:
    used: list[str] = []
    for day in itinerary.days:
        for act in day.activities:
            if act.citation_key and act.citation_key not in used:
                used.append(act.citation_key)

    chunk_by_key = {rc.citation_key: rc for rc in context.chunks}
    evidence = evidence_map(tool_results)

    out: list[Citation] = []
    for key in used:
        if key in chunk_by_key:
            rc = chunk_by_key[key]
            chunk = chunk_index.get(rc.chunk_id)
            out.append(
                Citation(
                    citation_key=key,
                    chunk_id=rc.chunk_id,
                    source=rc.source,
                    source_url=getattr(chunk, "source_url", None),
                    effective_date=getattr(chunk, "effective_date", None),
                    fresh_until=rc.fresh_until,
                    origin="knowledge_base",
                )
            )
        elif key in evidence:
            ev = evidence[key]
            out.append(
                Citation(
                    citation_key=key,
                    chunk_id=ev.get("chunk_id"),
                    source=str(ev.get("source") or ""),
                    source_url=ev.get("source_url"),
                    effective_date=_as_date(ev.get("effective_date")),
                    fresh_until=_as_date(ev.get("fresh_until")),
                    origin="tool",
                )
            )
    # 工具凭据即使没被正文引用也保留：checklist 与降级响应需要它们
    for key, ev in evidence.items():
        if all(c.citation_key != key for c in out):
            out.append(
                Citation(
                    citation_key=key,
                    chunk_id=ev.get("chunk_id"),
                    source=str(ev.get("source") or ""),
                    source_url=ev.get("source_url"),
                    effective_date=_as_date(ev.get("effective_date")),
                    fresh_until=_as_date(ev.get("fresh_until")),
                    origin="tool",
                )
            )
    return out


# ------------------------------------------------------------------ checklist
def assemble_checklist(
    *,
    settings: Settings,
    route: RouteConfig,
    context: RetrievedContext,
    chunk_index: dict[str, Any],
    tool_results: dict[str, ToolResult],
    slots: dict[str, Any],
) -> list[str]:
    if "visa" in route.scenes:
        return _visa_checklist(tool_results, context, chunk_index, route.output_language)
    return _generic_checklist(context, chunk_index, slots, route)


def _visa_checklist(
    tool_results: dict[str, ToolResult],
    context: RetrievedContext,
    chunk_index: dict[str, Any],
    language: str = DEFAULT_LANGUAGE,
) -> list[str]:
    """格式固定：材料名 — 要求说明（来源：<source_url>，生效日：<effective_date>）。

    标签随输出语言变（zh 下仍是「来源：」「生效日：」），但**来源原文与日期不翻译** ——
    引用要能对回原文，翻了就断链。
    """
    src_label = t("cl.source", language)
    eff_label = t("cl.effective", language)
    missing = t("cl.missing", language)
    items: list[str] = []
    seen: set[str] = set()
    policy = tool_results.get("visa_policy")
    if policy and policy.ok:
        for m in (policy.payload or {}).get("materials", []):
            key = str(m.get("chunk_id") or m.get("name"))
            if key in seen:
                continue
            seen.add(key)
            items.append(
                f"{m.get('name')} — {str(m.get('requirement') or '').strip()}"
                f"（{src_label}：{m.get('source_url') or missing}，{eff_label}：{m.get('effective_date') or missing}）"
            )
    for rc in context.chunks:
        if rc.layer != "realtime" or rc.chunk_id in seen:
            continue
        chunk = chunk_index.get(rc.chunk_id)
        if chunk is None:
            continue
        seen.add(rc.chunk_id)
        url = getattr(chunk, "source_url", None) or missing
        eff = getattr(chunk, "effective_date", None)
        items.append(f"{rc.text[:40]}（{src_label}：{url}，{eff_label}：{eff.isoformat() if eff else missing}）")
    if not items:
        items.append(t("cl.no_realtime", language))
    return items


# kind → 文案 key。标签随输出语言，资料库正文（来源原文）保持中文。
_GENERIC_WANTS = {
    "doc": "cl.kind.doc",
    "packing": "cl.kind.packing",
    "insurance": "cl.kind.insurance",
    "connectivity": "cl.kind.connectivity",
    "ticketing": "cl.kind.ticketing",
    "money": "cl.kind.money",
}


def _generic_checklist(
    context: RetrievedContext, chunk_index: dict[str, Any], slots: dict[str, Any], route: RouteConfig
) -> list[str]:
    language = route.output_language
    src_label = t("cl.source", language)
    items: list[str] = []
    seen_kinds: set[str] = set()
    for rc in context.chunks:
        chunk = chunk_index.get(rc.chunk_id)
        if chunk is None:
            continue
        kind = str((getattr(chunk, "metadata", {}) or {}).get("kind") or "")
        label_key = _GENERIC_WANTS.get(kind)
        if not label_key or label_key in seen_kinds:
            continue
        seen_kinds.add(label_key)
        items.append(f"{t(label_key, language)}：{rc.text[:44]}（{src_label}：{rc.source}）")
    if "family" in route.scenes:
        items.append(t("cl.family", language))
    if "budget" in route.scenes:
        items.append(t("cl.budget", language))
    if "roadtrip" in route.scenes:
        items.append(t("cl.roadtrip", language))
    return items or [t("cl.default", language)]
