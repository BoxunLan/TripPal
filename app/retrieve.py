"""retrieve 节点：元数据预过滤 → 向量召回 → 按层配额截断。

另外承担「信息类工具」的调用（opening_hours / visa_policy）—— 它们和检索同属取数阶段，
必须在 generate 之前拿到结果。算术类工具 budget_sum 放在 validate 阶段，因为它要消费
生成出来的分项费用。三个工具都没有越出 RouteConfig.tool_allowlist。

刻意不做：BM25、Cross-encoder 重排、MCP、SSE 流式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .config import Settings
from .schemas import Chunk, RetrievedChunk, RetrievedContext, RouteConfig, ToolResult


@dataclass
class RetrievalBundle:
    context: RetrievedContext
    chunk_index: dict[str, Chunk] = field(default_factory=dict)
    tools: dict[str, ToolResult] = field(default_factory=dict)


def _destinations_for(slots: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("destination_aliases", "destination", "destination_country"):
        val = slots.get(key)
        if isinstance(val, (list, tuple)):
            out.extend(str(v) for v in val)
        elif val:
            out.append(str(val))
    seen: list[str] = []
    for d in out:
        if d and d not in seen:
            seen.append(d)
    return seen


def _quota_slots(top_k: int, quota: dict[str, float]) -> dict[str, int]:
    """按配额分配名额，四舍五入后把余数补给配额最大的层，保证总和等于 top_k。"""
    raw = {layer: top_k * q for layer, q in quota.items()}
    alloc = {layer: int(v) for layer, v in raw.items()}
    remainder = top_k - sum(alloc.values())
    for layer in sorted(raw, key=lambda k: -raw[k]):
        if remainder <= 0:
            break
        alloc[layer] += 1
        remainder -= 1
    return alloc


def _truncate_by_quota(
    scored: list[Chunk], *, alloc: dict[str, int], top_k: int
) -> list[Chunk]:
    by_layer: dict[str, list[Chunk]] = {layer: [] for layer in alloc}
    for c in scored:
        by_layer.setdefault(c.layer, []).append(c)

    selected: list[Chunk] = []
    taken: set[str] = set()
    for layer, budget in alloc.items():
        for c in by_layer.get(layer, [])[:budget]:
            selected.append(c)
            taken.add(c.chunk_id)
    # 某层不足额时，余量按全局分数序补给其它层，避免总条数缩水
    if len(selected) < top_k:
        for c in scored:
            if len(selected) >= top_k:
                break
            if c.chunk_id not in taken:
                selected.append(c)
                taken.add(c.chunk_id)
    return selected


def retrieve_context(
    *,
    settings: Settings,
    store,
    embedder,
    route: RouteConfig,
    query: str,
    slots: dict[str, Any],
    today: date,
) -> RetrievalBundle:
    destinations = _destinations_for(slots)
    filters: dict[str, Any] = {"scopes": route.knowledge_scopes, "destinations": destinations}
    candidate_k = settings.top_k * settings.candidate_multiplier
    vector = embedder.embed([query])[0]

    rows = store.search(vector, filters=filters, top_k=candidate_k)
    relaxed = False
    if not rows and destinations:
        # 目的地词典没覆盖到时放开地理过滤，否则整条链路只能输出「待核实」
        relaxed = True
        filters = {"scopes": route.knowledge_scopes}
        rows = store.search(vector, filters=filters, top_k=candidate_k)

    dropped_stale = 0
    fresh: list[tuple[float, Chunk]] = []
    for item in rows:
        chunk = item.chunk
        # 超过 fresh_until 的条目**一律丢弃**，不参与排序，也不进上下文。
        #
        # 原来只拦 layer=="realtime"，理由是「常识条目不随日期变」。但那是错的：
        # general / scene 层里同样有**会过期**的内容 —— 门票价、开放时间、预约规则、
        # 车次与价格带。这些条目过期后照样被召回，等于拿去年（或更早）的价签答今天的
        # 问题，而用户看不出它已经过期。宁可这一条不进上下文（generate 会退化成
        # 「待核实」），也不要给一个看起来确定、实际陈旧的值。
        # 丢弃条数记进 dropped_stale，便于在响应/日志里看见闸门真的在工作。
        if chunk.fresh_until and chunk.fresh_until < today:
            dropped_stale += 1
            continue
        fresh.append((item.score, chunk))

    quota = settings.layer_quota(route.scenes[0] if route.scenes else None)
    alloc = _quota_slots(settings.top_k, quota)
    ordered_chunks = [c for _, c in fresh]
    selected = _truncate_by_quota(ordered_chunks, alloc=alloc, top_k=settings.top_k)
    score_by_id = {c.chunk_id: s for s, c in fresh}

    retrieved = [
        RetrievedChunk(
            chunk_id=c.chunk_id,
            layer=c.layer,  # type: ignore[arg-type]
            score=round(score_by_id.get(c.chunk_id, 0.0), 6),
            text=c.text,
            source=c.source,
            fresh_until=c.fresh_until,
            citation_key=c.citation_key,
        )
        for c in selected
    ]

    record_filters = {
        "scopes": route.knowledge_scopes,
        "destinations": destinations,
        "destination_relaxed": relaxed,
        "layer_alloc": alloc,
    }

    return RetrievalBundle(
        context=RetrievedContext(
            query=query,
            filters=record_filters,
            chunks=retrieved,
            layer_quota=quota,
            dropped_stale=dropped_stale,
            retriever_version=settings.retriever_version,
        ),
        chunk_index={c.chunk_id: c for c in selected},
    )


def run_info_tools(
    *, settings: Settings, toolbox, route: RouteConfig, slots: dict[str, Any], today: date
) -> dict[str, ToolResult]:
    """调用白名单里的信息类工具。未授权就不调用，并留下一条失败记录。"""
    out: dict[str, ToolResult] = {}
    destination = str(slots.get("destination") or slots.get("destination_country") or "")
    country = str(slots.get("destination_country") or slots.get("destination") or "")

    if "opening_hours" in route.tool_allowlist:
        out["opening_hours"] = toolbox.call("opening_hours", {"destination": destination}, route.tool_allowlist)
    if "visa_policy" in route.tool_allowlist and country:
        out["visa_policy"] = toolbox.call("visa_policy", {"country": country, "as_of": today}, route.tool_allowlist)
    return out
