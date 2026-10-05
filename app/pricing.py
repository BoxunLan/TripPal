"""费用折算与分类。fakes（排行程）与 validate（预算校验/替换）共用同一套口径 ——
两处各写一遍必然分叉，预算校验就会得出和正文不一致的结论。
"""

from __future__ import annotations

from typing import Any

CATEGORY_BY_KIND = {
    "lodging": "住宿",
    "camping": "住宿",
    "food": "餐饮",
    "transport": "交通",
    "car_rental": "交通",
    "fuel": "交通",
    "poi": "门票",
    "activity": "门票",
    "ticketing": "门票",
    "insurance": "其他",
    "connectivity": "其他",
    "money": "其他",
}

KINDS_BY_CATEGORY = {
    "住宿": ("lodging", "camping"),
    "餐饮": ("food",),
    "交通": ("transport", "car_rental", "fuel"),
    "门票": ("poi", "activity", "ticketing"),
    "其他": ("insurance", "connectivity", "money"),
}


def line_total(meta: dict[str, Any], days: int, pax: int) -> float | None:
    """把一条种子的 cost + cost_unit 折算成整趟行程的金额。"""
    cost = meta.get("cost")
    if cost is None:
        return None
    unit = str(meta.get("cost_unit") or "人")
    d, p = max(int(days or 1), 1), max(int(pax or 1), 1)
    if unit in {"人/晚", "人/天", "人/日"}:
        return float(cost) * p * d
    if unit in {"间/晚", "晚", "日"}:
        return float(cost) * d
    if unit == "人/餐":
        return float(cost) * p * 3 * d
    if unit == "人/单程":
        return float(cost) * p * 2
    if unit == "人/往返":
        return float(cost) * p
    if unit == "人/程":
        return float(cost) * p * 2 * d
    if unit in {"人/周", "人/半日", "人", "成人", "儿童/餐"}:
        return float(cost) * p
    if unit == "次停车":
        return float(cost) * d
    if unit == "次取现手续费":
        return float(cost)
    if unit == "公里油费":
        return float(cost) * 40 * d
    return float(cost) * p


def category_of(chunk: Any) -> str:
    meta = getattr(chunk, "metadata", None)
    if meta is None and isinstance(chunk, dict):
        meta = chunk.get("metadata")
    kind = str((meta or {}).get("kind") or "")
    return CATEGORY_BY_KIND.get(kind, "其他")
