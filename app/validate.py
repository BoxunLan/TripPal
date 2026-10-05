"""validate 节点：并行三项校验（Budget / Fact / Consistency）+ 严重分级。

严重级语义（三条同时成立才叫严重）：
- warn  ：原样输出，写进报告
- repair：回到 generate，只改失败段落，**最多 1 轮**
- block ：停止成稿，改为「材料清单 + 官方链接」，并声明不构成法律意见

`ValidationCheck.severity` 是该检查的**配置上限**（稳定属性），`status` 是本次实际结果。
所以一条通过的 budget 检查仍然报 severity=repair —— 它表达的是「这个检查最坏能要求回炉」。

不做第 2 轮回环，不做独立安全引擎。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .pricing import KINDS_BY_CATEGORY, category_of, line_total
from .schemas import (
    Itinerary,
    RetrievedContext,
    RouteConfig,
    ToolResult,
    ValidationCheck,
    ValidationReport,
)
from .tools import evidence_map

BUFFER = 0.10

# 「事实型」的判据：这句里有没有一句**能拿去和资料对质的具体断言**。
# 不用「出现了某个关键词」做判据 —— 那样「费用和时间需自行确认」这种对冲句会被
# 误判成事实型，进而降级、白吃掉唯一一次回环（真实跑批时反复出现，见
# .workbuddy/memory/2026-09-25.md）。这里要求出现具体时间 / 场馆开放票务 /
# 带数字的金额 / 签证政策，且整句不是对冲措辞。
FACT_PATTERNS = (
    r"\d{1,2}[:：]\d{2}",                            # 10:00 开放
    r"开馆|闭馆|开放时间|营业时间|门票|票价|免票",
    r"\d[\d\s~\-至]*\s*(?:元|块|美元|日元|港币|欧元)",   # 人均 20-30 元
    r"(?:价格|费用|人均)[^。；！？]{0,10}?\d",           # 价格约 350 元
    # 签证政策类：这些词一出现就是可对质的断言（不能用太宽的词，否则误伤）
    r"签证|材料|领区|代办|递签|在职证明|银行流水|工作日|办理时长",
)

# 出现这些词 = 该条没有下断言，即使句中出现「费用/价格」也不必溯源
HEDGE_RE = re.compile(r"自行确认|需确认|请自行|视情况|以[^。；]{0,10}为准|以实际|待定")

CHECK_SEVERITY = {"budget": "repair", "fact": "block", "consistency": "repair"}


@dataclass
class ValidationOutcome:
    itinerary: Itinerary
    report: ValidationReport
    tool_results: dict[str, ToolResult] = field(default_factory=dict)


def is_fact_bearing(activity) -> bool:
    """这条活动有没有可对质的具体断言？

    `cost` 非空一律算事实（价格是实打实的主张，跟措辞无关）；否则看 detail。
    """
    if activity.cost is not None:
        return True
    detail = activity.detail or ""
    if HEDGE_RE.search(detail):
        return False
    return any(re.search(pattern, detail) for pattern in FACT_PATTERNS)


# ------------------------------------------------------------------ Budget
def check_budget(
    *,
    itinerary: Itinerary,
    route: RouteConfig,
    toolbox,
    slots: dict[str, Any],
    store,
    round_no: int,
) -> tuple[ValidationCheck, list[str], list[str], ToolResult | None]:
    name = "budget"
    actions: list[str] = []
    unresolved: list[str] = []
    budget = itinerary.budget
    user_budget = slots.get("budget")

    if "budget_sum" not in route.tool_allowlist:
        return (
            ValidationCheck(name=name, status="pass", severity=CHECK_SEVERITY[name],
                            findings=["本路由未授权 budget_sum，跳过预算校验（签证场景不强制预算）。"]),
            actions, unresolved, None,
        )
    if budget is None or not budget.lines:
        return (
            ValidationCheck(name=name, status="pass", severity=CHECK_SEVERITY[name],
                            findings=["行程未给出分项费用，无法做预算校验。"]),
            actions, unresolved, None,
        )

    items = [line.model_dump() for line in budget.lines]
    result = toolbox.call(
        "budget_sum",
        {"items": items, "budget": float(user_budget) if user_budget else None, "buffer_ratio": BUFFER},
        route.tool_allowlist,
    )
    if not result.ok:
        return (
            ValidationCheck(name=name, status="warn", severity=CHECK_SEVERITY[name],
                            findings=[f"budget_sum 调用失败：{result.error}"]),
            actions, unresolved, result,
        )

    payload = result.payload
    total = float(payload["total"])
    if user_budget is None:
        return (
            ValidationCheck(name=name, status="pass", severity=CHECK_SEVERITY[name],
                            findings=[f"用户未给预算，仅给出分项合计 {total:.0f} 元。"]),
            actions, unresolved, result,
        )

    limit = float(payload["limit_with_buffer"])
    if total <= limit:
        findings = [
            f"分项合计 {total:.0f} 元 ≤ 预算 {float(user_budget):.0f} 元的 110% 上限 {limit:.0f} 元。"
        ]
        status = "pass"
    else:
        findings = [f"分项合计 {total:.0f} 元 超过 110% 上限 {limit:.0f} 元，需把高价项换成种子库中的低价项。"]
        swapped = _swap_expensive(
            itinerary=itinerary, store=store, route=route, slots=slots, limit=limit
        )
        actions.extend(swapped)
        total = float(itinerary.budget.total or 0.0)
        if swapped and total <= limit:
            status = "warn"
            findings.append(f"已执行 {len(swapped)} 项替换，合计降至 {total:.0f} 元，回到缓冲上限内。")
        else:
            status = "repair" if round_no < route.max_repair_rounds else "warn"
            if status == "warn":
                unresolved.append(f"预算仍超出 110% 上限：合计 {total:.0f} 元 / 上限 {limit:.0f} 元，已达最大回环轮次。")

    return ValidationCheck(name=name, status=status, severity=CHECK_SEVERITY[name], findings=findings), actions, unresolved, result


def _swap_expensive(
    *, itinerary: Itinerary, store, route: RouteConfig, slots: dict[str, Any], limit: float
) -> list[str]:
    """把最贵的一行换成种子库中同类低价项，最多换 3 行。"""
    budget = itinerary.budget
    if budget is None or not budget.lines:
        return []
    days = int(slots.get("days") or 1)
    pax = int(slots.get("party_size") or 2)
    actions: list[str] = []

    for _ in range(3):
        total = sum(float(l.amount) for l in budget.lines)
        if total <= limit:
            break
        expensive = max(budget.lines, key=lambda l: float(l.amount))
        candidate = _cheaper_alternative(
            category=expensive.category, current=float(expensive.amount),
            store=store, route=route, days=days, pax=pax,
        )
        if candidate is None:
            break
        old_name = expensive.name
        old_amount = float(expensive.amount)
        expensive.name = candidate[0]
        expensive.amount = candidate[1]
        actions.append(
            f"高价项替换：{old_name[:24]}（{old_amount:.0f} 元）→ {candidate[0][:24]}（{candidate[1]:.0f} 元）。"
        )

    budget.total = round(sum(float(l.amount) for l in budget.lines), 2)
    if budget.user_budget is not None:
        budget.within_budget = budget.total <= budget.user_budget * (1 + budget.buffer_ratio)
    return actions


def _cheaper_alternative(
    *, category: str, current: float, store, route: RouteConfig, days: int, pax: int
) -> tuple[str, float, float] | None:
    kinds = list(KINDS_BY_CATEGORY.get(category) or ())
    if not kinds:
        return None
    rows = store.scan(filters={"scopes": route.knowledge_scopes, "kinds": kinds}, limit=300)
    best: tuple[str, float, float] | None = None
    for chunk in rows:
        if category_of(chunk) != category:
            continue
        amount = line_total(getattr(chunk, "metadata", {}) or {}, days, pax)
        if amount is None or amount >= current or amount <= 0:
            continue
        if best is None or amount < best[1]:
            best = (f"{category}：{chunk.text[:28]}", round(amount, 2), current)
    return best


# ------------------------------------------------------------------ Fact
def check_fact(
    *,
    itinerary: Itinerary,
    context: RetrievedContext,
    chunk_index: dict[str, Any],
    tool_results: dict[str, ToolResult],
    route: RouteConfig,
    round_no: int,
) -> tuple[ValidationCheck, list[str], list[str], list[str]]:
    name = "fact"
    evidence = evidence_map(tool_results)
    valid = set(context.keys()) | set(evidence.keys())

    findings: list[str] = []
    actions: list[str] = []
    unresolved: list[str] = []
    verified: list[str] = []
    downgraded = 0

    for day in itinerary.days:
        for act in day.activities:
            if not is_fact_bearing(act):
                continue
            key = act.citation_key
            if key and key in valid:
                if key not in verified:
                    verified.append(key)
                continue
            downgraded += 1
            reason = "未给出 citation_key" if not key else f"citation_key={key} 不在本轮检索/工具结果中"
            findings.append(f"第 {day.day} 天「{act.name[:16]}」缺少可溯源凭据（{reason}），已降级为待核实。")
            act.citation_key = None
            act.cost = None
            if not str(act.detail).startswith("待核实"):
                act.detail = f"待核实：{act.detail}"

    actions.extend(f"事实降级 {i+1}：{f}" for i, f in enumerate(findings))

    status = "pass"
    if downgraded:
        status = "repair" if round_no < route.max_repair_rounds else "warn"
        if status == "warn":
            unresolved.append(f"{downgraded} 条事实仍未能溯源，已按「待核实」输出。")

    # 签证 block 条件：政策缺少来源或生效日
    if "visa" in route.scenes:
        missing: list[str] = []
        policy = tool_results.get("visa_policy")
        if policy and policy.ok:
            missing.extend(str(x) for x in (policy.payload or {}).get("missing_source_meta") or [])
        for rc in context.chunks:
            if rc.layer != "realtime":
                continue
            chunk = chunk_index.get(rc.chunk_id)
            if chunk is None:
                continue
            if not getattr(chunk, "source_url", None) or not getattr(chunk, "effective_date", None):
                missing.append(rc.chunk_id)
        if missing:
            status = "block"
            findings.append(
                "签证政策条目缺少来源或生效日（"
                + "、".join(dict.fromkeys(missing))
                + "），停止成稿，改为材料清单 + 官方链接。"
            )
            unresolved.append("签证政策来源/生效日缺失，无法作为确定事实输出。")

    if not findings:
        findings = [f"全部具体事实均可溯源，共校验 {len(verified)} 条凭据。"]

    return ValidationCheck(name=name, status=status, severity=CHECK_SEVERITY[name], findings=findings), actions, unresolved, verified


# ------------------------------------------------------------------ Consistency
def check_consistency(
    *,
    itinerary: Itinerary,
    slots: dict[str, Any],
    route: RouteConfig,
    round_no: int,
    city_by_key: dict[str, str] | None = None,
) -> tuple[ValidationCheck, list[str]]:
    """日期闭合 + 同一天地点地理上接得上。

    区域取自 citation_key 对应条目的 city 元数据，而不是让模型自己声明 ——
    模型声明「今天在厦门」是廉价的，凭据里的 city 才是可核对的。
    """
    name = "consistency"
    city_by_key = city_by_key or {}
    findings: list[str] = []
    fatal = False

    days = itinerary.days
    numbers = [d.day for d in days]
    if numbers and numbers != list(range(1, len(days) + 1)):
        findings.append(f"日期序号不连续：{numbers}，应为 1..{len(days)}。")
        fatal = True
    expect = slots.get("days")
    if expect and len(days) != int(expect):
        findings.append(f"天数不闭合：槽位要求 {int(expect)} 天，实际给出 {len(days)} 天。")
        fatal = True

    dated = [d.date for d in days if d.date]
    if len(dated) > 1:
        from datetime import date as _date

        try:
            parsed = [_date.fromisoformat(d) for d in dated]
        except ValueError:
            parsed = []
        gaps = [(parsed[i + 1] - parsed[i]).days for i in range(len(parsed) - 1)]
        if gaps and any(g != 1 for g in gaps):
            findings.append(f"日期不连续，相邻日期间隔为 {gaps} 天。")
            fatal = True

    day_cities: list[list[str]] = []
    for day in days:
        cities: list[str] = []
        for act in day.activities:
            city = city_by_key.get(act.citation_key or "", "")
            if city and city not in cities:
                cities.append(city)
        day_cities.append(cities)
        if len(cities) > 1:
            findings.append(
                f"第 {day.day} 天出现多个活动城市（{'、'.join(cities)}），同一天地理上接不上。"
            )
        if not day.activities:
            findings.append(f"第 {day.day} 天没有任何活动。")

    sequence = [c[0] for c in day_cities if c]
    transitions = sum(1 for i in range(len(sequence) - 1) if sequence[i] != sequence[i + 1])
    if transitions > 2 and "roadtrip" not in route.scenes:
        findings.append(f"行程中城市切换 {transitions} 次，非自驾场景下转场偏多。")

    if not findings:
        findings = ["日期闭合、区域连续，未发现矛盾。"]

    status = "pass"
    if findings and findings != ["日期闭合、区域连续，未发现矛盾。"]:
        status = "repair" if (fatal and round_no < route.max_repair_rounds) else "warn"

    return ValidationCheck(name=name, status=status, severity=CHECK_SEVERITY[name], findings=findings), (
        findings if status == "repair" else []
    )


# ------------------------------------------------------------------ 编排
def _city_index(context: RetrievedContext, chunk_index: dict[str, Any]) -> dict[str, str]:
    """citation_key -> city，用于地理一致性检查。"""
    out: dict[str, str] = {}
    for rc in context.chunks:
        chunk = chunk_index.get(rc.chunk_id)
        city = str((getattr(chunk, "metadata", {}) or {}).get("city") or "")
        if city:
            out[rc.citation_key] = city
    return out


def run_validation(
    *,
    settings: Settings,
    toolbox,
    store,
    route: RouteConfig,
    itinerary: Itinerary,
    context: RetrievedContext,
    chunk_index: dict[str, Any],
    tool_results: dict[str, ToolResult],
    slots: dict[str, Any],
    round_no: int,
) -> ValidationOutcome:
    budget_check, budget_actions, budget_unresolved, budget_tool = check_budget(
        itinerary=itinerary, route=route, toolbox=toolbox, slots=slots, store=store, round_no=round_no
    )
    fact_check, fact_actions, fact_unresolved, verified = check_fact(
        itinerary=itinerary, context=context, chunk_index=chunk_index,
        tool_results=tool_results, route=route, round_no=round_no,
    )
    consistency_check, consistency_actions = check_consistency(
        itinerary=itinerary,
        slots=slots,
        route=route,
        round_no=round_no,
        city_by_key=_city_index(context, chunk_index),
    )

    checks = [budget_check, fact_check, consistency_check]
    unresolved = list(dict.fromkeys(budget_unresolved + fact_unresolved))
    blocked = any(c.status == "block" for c in checks)
    passed = all(c.status in {"pass", "warn"} for c in checks)

    report = ValidationReport(
        passed=passed,
        round=round_no,
        checks=checks,
        repair_actions=budget_actions + fact_actions + consistency_actions,
        unresolved=unresolved,
        citations_verified=verified,
        degraded=blocked,
    )
    extra: dict[str, ToolResult] = {}
    if budget_tool is not None:
        extra["budget_sum"] = budget_tool
    return ValidationOutcome(itinerary=itinerary, report=report, tool_results=extra)
