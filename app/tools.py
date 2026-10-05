"""工具白名单。本期只实现 3 个：opening_hours / budget_sum / visa_policy。

调用闸门在 `ToolBox.call`：`RouteConfig.tool_allowlist` 之外的名字一律拒绝，
并且白名单也只认 routes.yaml 的 `tools.available`，两道都过才执行。

引用凭据统一用 `tool:<name>:<slug>` 前缀，与知识库的 chunk_id 区分开，
便于校验时判断凭据来源。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any

from .schemas import Chunk, ToolResult

TOOL_CITATION_PREFIX = "tool:"


def _slug(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts if p not in (None, ""))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def _src(chunk: Chunk) -> str:
    return chunk.source or ""


def _evidence(chunk: Chunk, prefix: str, label: str = "") -> dict[str, Any]:
    return {
        "citation_key": f"{TOOL_CITATION_PREFIX}{prefix}:{chunk.chunk_id}",
        "chunk_id": chunk.chunk_id,
        "label": label or chunk.text[:24],
        "source": chunk.source,
        "source_url": chunk.source_url,
        "effective_date": chunk.effective_date.isoformat() if chunk.effective_date else None,
        "fresh_until": chunk.fresh_until.isoformat() if chunk.fresh_until else None,
    }


def evidence_map(tool_results: dict[str, "ToolResult"]) -> dict[str, dict[str, Any]]:
    """把多次工具调用的凭据汇总成 citation_key -> 凭据 的索引。

    正文里引用的 `tool:<tool>:<chunk_id>` 必须能在这里查到，否则事实校验会把它判为
    不可溯源并降级为「待核实」。
    """
    out: dict[str, dict[str, Any]] = {}
    for tr in tool_results.values():
        if not tr.ok:
            continue
        if tr.citation_key:
            out.setdefault(
                tr.citation_key,
                {
                    "citation_key": tr.citation_key,
                    "chunk_id": None,
                    "label": f"{tr.tool} 调用结果",
                    "source": tr.source or f"{tr.tool} 工具",
                    "source_url": tr.source_url,
                    "effective_date": tr.effective_date.isoformat() if tr.effective_date else None,
                    "fresh_until": None,
                },
            )
        for ev in tr.evidence or []:
            key = ev.get("citation_key")
            if key:
                out.setdefault(str(key), dict(ev))
    return out


class ToolNotAllowed(PermissionError):
    pass


class ToolBox:
    def __init__(self, store, settings) -> None:
        self.store = store
        self.settings = settings

    # ------------------------------------------------------------ 闸门
    def call(self, name: str, args: dict[str, Any], allowlist: list[str]) -> ToolResult:
        if name not in self.settings.available_tools:
            return ToolResult(tool=name, ok=False, citation_key="", error=f"工具 {name} 不在本期实现范围")
        if name not in (allowlist or []):
            return ToolResult(
                tool=name, ok=False, citation_key="",
                error=f"RouteConfig.tool_allowlist 未授权 {name}，拒绝调用",
            )
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            return ToolResult(tool=name, ok=False, citation_key="", error=f"工具 {name} 未实现")
        try:
            return fn(**args)
        except TypeError as exc:
            return ToolResult(tool=name, ok=False, citation_key="", error=f"参数不匹配：{exc}")

    # ------------------------------------------------------------ 1. opening_hours
    def _t_opening_hours(self, *, destination: str = "", attraction: str = "", **_ignored: Any) -> ToolResult:
        filters = {"scopes": ["scene:family", "scene:budget", "scene:roadtrip", "scene:visa", "general"], "destinations": [destination] if destination else []}
        rows = self.store.scan(filters=filters, limit=400)
        hits = [c for c in rows if (c.metadata or {}).get("opening")]
        if attraction:
            narrowed = [c for c in hits if attraction in c.text or attraction == (c.metadata or {}).get("city")]
            hits = narrowed or hits
        if not hits:
            return ToolResult(
                tool="opening_hours", ok=False, citation_key=f"{TOOL_CITATION_PREFIX}opening_hours:{_slug(destination, attraction)}",
                error=f"知识库未收录 {destination or attraction} 的开放时间，请以景区官方当日公告为准，或参考下方权威入口核实",
            )
        entries = [
            {
                "name": c.text[:22],
                "opening": (c.metadata or {}).get("opening"),
                "closed_days": (c.metadata or {}).get("closed_days"),
                "source": _src(c),
                "chunk_id": c.chunk_id,
            }
            for c in hits
        ]
        primary = hits[0]
        return ToolResult(
            tool="opening_hours",
            ok=True,
            citation_key=f"{TOOL_CITATION_PREFIX}opening_hours:{primary.chunk_id}",
            payload={"destination": destination, "entries": entries},
            source=_src(primary),
            evidence=[_evidence(c, "opening_hours") for c in hits],
        )

    # ------------------------------------------------------------ 2. budget_sum
    def _t_budget_sum(
        self, *, items: list[dict[str, Any]], budget: float | None = None, buffer_ratio: float = 0.1, **_ignored: Any
    ) -> ToolResult:
        by_category: dict[str, float] = {}
        total = 0.0
        for item in items or []:
            amount = float(item.get("amount") or 0.0)
            total += amount
            cat = str(item.get("category") or "其他")
            by_category[cat] = round(by_category.get(cat, 0.0) + amount, 2)
        limit = round(float(budget) * (1 + buffer_ratio), 2) if budget is not None else None
        payload = {
            "total": round(total, 2),
            "by_category": by_category,
            "user_budget": float(budget) if budget is not None else None,
            "buffer_ratio": buffer_ratio,
            "limit_with_buffer": limit,
            "over_budget": bool(limit is not None and total > limit),
        }
        return ToolResult(
            tool="budget_sum",
            ok=True,
            citation_key=f"{TOOL_CITATION_PREFIX}budget_sum:{_slug(json.dumps(payload, sort_keys=True))}",
            payload=payload,
            source="budget_sum 工具（对行程分项求和）",
        )

    # ------------------------------------------------------------ 3. visa_policy
    def _t_visa_policy(self, *, country: str = "", as_of: date | None = None, **_ignored: Any) -> ToolResult:
        as_of = as_of or date.today()
        filters = {"scopes": ["realtime:visa"], "destinations": [country] if country else []}
        rows = [c for c in self.store.scan(filters=filters, limit=200) if c.layer == "realtime"]
        fresh = [c for c in rows if not c.fresh_until or c.fresh_until >= as_of]
        if not fresh:
            return ToolResult(
                tool="visa_policy", ok=False,
                citation_key=f"{TOOL_CITATION_PREFIX}visa_policy:{_slug(country)}",
                error=f"没有 {country} 的有效期内的签证政策条目，无法给出材料清单",
            )
        materials = []
        missing_meta: list[str] = []
        for c in fresh:
            if not c.source_url or not c.effective_date:
                missing_meta.append(c.chunk_id)
            materials.append(
                {
                    "name": _material_name(c),
                    "requirement": c.text,
                    "citation_key": f"{TOOL_CITATION_PREFIX}visa_policy:{c.chunk_id}",
                    "chunk_id": c.chunk_id,
                    "source": c.source,
                    "source_url": c.source_url,
                    "effective_date": c.effective_date.isoformat() if c.effective_date else None,
                    "fresh_until": c.fresh_until.isoformat() if c.fresh_until else None,
                }
            )
        return ToolResult(
            tool="visa_policy",
            ok=True,
            citation_key=f"{TOOL_CITATION_PREFIX}visa_policy:{_slug(country, as_of.isoformat())}",
            payload={
                "country": country,
                "as_of": as_of.isoformat(),
                "materials": materials,
                "missing_source_meta": missing_meta,
            },
            source=f"{country} 签证政策摘录（realtime 层）",
            source_url=fresh[0].source_url,
            effective_date=fresh[0].effective_date,
            evidence=[_evidence(c, "visa_policy", _material_name(c)) for c in fresh],
        )


def _material_name(chunk: Chunk) -> str:
    """从政策摘录里取一个材料/规则名，用于 checklist 条目的前缀。"""
    meta = chunk.metadata or {}
    category = str(meta.get("category") or "签证信息")
    head = re.split(r"[：:，,。；;（(]", chunk.text, maxsplit=1)[0].strip()
    if not head:
        head = chunk.text[:12]
    if len(head) > 12:
        head = head[:12] + "…"
    return f"{category}·{head}"
