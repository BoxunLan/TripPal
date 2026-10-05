"""离线假 LLM（provider=fake）。

用途有二：
1. 让 `pytest` 在**无密钥、无网络、无数据库**的条件下跑通 5 条验收；
2. 作为真实模型的对照组 —— 断言「链路是否连对」时，不该被模型随机性干扰。

它只消费 `context`（结构化输入），不解析提示词。所以它不是在「假装理解自然语言」，
而是把「分类」和「排行程」这两步用确定性规则重写了一遍：分类走 routes.yaml 的关键词，
排行程走检索结果的 cost 元数据 + 工具结果。
"""

from __future__ import annotations

from typing import Any

from .config import Settings
from .i18n import contains, detect_language
from .pricing import category_of, line_total
from .slots import build_clarify_question, missing_slots, required_slots_for

CONF_BASE = 0.50
CONF_PER_HIT = 0.22
CONF_CAP = 0.95


def _line_total(meta: dict[str, Any], days: int, pax: int) -> float | None:
    return line_total(meta, days, pax)


def _category(chunk: dict[str, Any]) -> str:
    return category_of(chunk)


class FakeLLM:
    provider = "fake"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------- 分类
    def _classify(self, ctx: dict[str, Any]) -> dict[str, Any]:
        message: str = ctx.get("message", "")
        slots: dict[str, Any] = dict(ctx.get("slots") or {})
        scene_ids: list[str] = list(ctx.get("scene_ids") or [])
        keywords: dict[str, list[str]] = ctx.get("scene_keywords") or {}
        hints = set(ctx.get("hints") or [])
        fallback = ctx.get("fallback_scene") or self.settings.fallback_scene

        labels: list[dict[str, Any]] = []
        for sid in scene_ids:
            hits = sum(1 for kw in keywords.get(sid, []) if contains(message, kw))
            if sid in hints:
                hits = max(hits, 1)
            if hits <= 0:
                continue
            labels.append({"scene_id": sid, "confidence": round(min(CONF_CAP, CONF_BASE + CONF_PER_HIT * hits), 2)})
        labels.sort(key=lambda x: -x["confidence"])  # 稳定排序：同分保持 routes.yaml 顺序

        primary = labels[0]["scene_id"] if labels else fallback
        if labels:
            req = required_slots_for([lb["scene_id"] for lb in labels], self.settings)
        else:
            req = list(self.settings.required_slots(fallback))
        miss = missing_slots(slots, req)

        dest = slots.get("destination") or slots.get("destination_country") or ""
        names = "、".join(self.settings.scene_cfg(lb["scene_id"]).get("display_name", lb["scene_id"]) for lb in labels)
        parts = [p for p in [dest, f"{slots['days']} 天" if slots.get("days") else "", names, "行程规划"] if p]

        return {
            "labels": labels,
            "primary_scene": primary,
            "slots": slots,
            "missing_slots": miss,
            "clarify_question": build_clarify_question(miss, slots, language=detect_language(message)) if miss else None,
            "rewritten_query": " ".join(parts) or message,
            "classifier_version": ctx.get("classifier_version") or self.settings.classifier_version,
        }

    # ------------------------------------------------------------- 生成
    def complete_json(
        self, *, role: str, prompt: str, schema: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        if role == "classifier":
            return self._classify(context)
        # 常识作答与行程生成都走 generator 角色，靠 context 里的 task 区分 ——
        # 真实客户端按 prompt 调用，桩按结构化 context 推演（见 app/llm.py 的显式缝隙）。
        if context.get("task") == "answer":
            return self._answer(context)
        if context.get("task") == "guide":
            return self._guide(context)
        return self._plan(context)

    # ------------------------------------------------------------- 常识作答
    def _answer(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """离线桩的常识作答。

        桩**不生成任何具体事实** —— 那正是这一层要防的东西（编一个面积比说不知道坏）。
        它只回一句说明，用来验证链路：answer 被填充、页面渲染答案块、复核入口拼得出来。
        真实答案由真模型给；要验证「答不出」那一支，看 `test_answer.py` 里的桩替换。
        """
        subject = str(ctx.get("subject") or "")
        if not subject:
            return {"answer": "", "confidence": "low", "unknown_reason": ""}
        return {
            "answer": f"（离线桩）关于「{subject}」的常识说明：本桩不生成具体数字，只验证链路。",
            "confidence": "low",
            "unknown_reason": "",
        }

    # ------------------------------------------------------------- 寒暄引导
    def _guide(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """离线桩的寒暄答复。

        桩不生成真实引导语，只回一句能验证链路的话（reply 被填充、页面渲染引导块、
        starters 拼得出来）。真话术由真模型给；模型不可用时退回 i18n 定稿（见 app/guide.py）。
        """
        kind = str(ctx.get("kind") or "greeting")
        return {"reply": f"（离线桩）收到一句「{kind}」，这里回一句把人引到旅行话题的引导语。"}

    def _plan(self, ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx.get("primary_scene") == "visa":
            return self._visa_plan(ctx)
        return self._trip_plan(ctx)

    # ------------------------------------------------------------- 行程
    def _trip_plan(self, ctx: dict[str, Any]) -> dict[str, Any]:
        slots: dict[str, Any] = ctx.get("slots") or {}
        scenes: list[str] = list(ctx.get("scenes") or [])
        chunks: list[dict[str, Any]] = list(ctx.get("chunks") or [])
        tools: dict[str, Any] = ctx.get("tool_results") or {}
        guardrail_texts: dict[str, str] = ctx.get("guardrail_texts") or {}
        findings: list[str] = list(ctx.get("validation_findings") or [])

        days = int(slots.get("days") or 1)
        pax = int(slots.get("party_size") or 2)
        dest = str(slots.get("destination") or slots.get("destination_country") or "")
        family = "family" in scenes
        budget_scene = "budget" in scenes

        buckets: dict[str, list[dict[str, Any]]] = {"住宿": [], "餐饮": [], "交通": [], "门票": [], "其他": []}
        for c in chunks:
            buckets[_category(c)].append(c)

        def cheapest(cat: str) -> dict[str, Any] | None:
            items = [c for c in buckets[cat] if _line_total(c.get("metadata") or {}, days, pax) is not None]
            if not items:
                return None
            return min(items, key=lambda c: _line_total(c["metadata"], days, pax) or 0.0)

        lines: list[dict[str, Any]] = []
        for cat in ("住宿", "餐饮", "交通", "其他"):
            picked = cheapest(cat)
            if picked is None:
                continue
            amount = _line_total(picked.get("metadata") or {}, days, pax) or 0.0
            lines.append(
                {
                    "name": f"{cat}：{picked['text'][:28]}",
                    "amount": round(amount, 2 if amount % 1 else 0),
                    "category": cat,
                }
            )

        # 行程点：budget 场景优先选便宜项，否则按检索分排序
        poi_pool = list(buckets["门票"])
        for c in buckets["其他"]:
            if str((c.get("metadata") or {}).get("kind")) in {"poi", "activity"}:
                poi_pool.append(c)
        if budget_scene:
            poi_pool.sort(key=lambda c: (_line_total(c.get("metadata") or {}, days, pax) or 0.0, -float(c.get("score", 0.0))))
        else:
            poi_pool.sort(key=lambda c: -float(c.get("score", 0.0)))
        placed = poi_pool[: max(days + 1, 3)]

        tool_by_name = {k.lower(): v for k, v in tools.items()}

        def cite_for(chunk: dict[str, Any]) -> str | None:
            """有 opening_hours 工具结果时优先用工具凭据（凭据强度更高）。"""
            meta = chunk.get("metadata") or {}
            if meta.get("opening") and tool_by_name.get("opening_hours", {}).get("ok"):
                payload = tool_by_name["opening_hours"].get("payload") or {}
                if payload.get("citation_key"):
                    return str(payload["citation_key"])
            return chunk.get("citation_key")

        nap_detail = "儿童节奏：午餐后回酒店休息 1–1.5 小时，下午再出发，避免情绪崩溃。"
        day_plans: list[dict[str, Any]] = []
        per_day = 2 if len(placed) >= 2 else len(placed)
        for i in range(days):
            acts: list[dict[str, Any]] = []
            # 景点数少于天数时按序循环复用，而不是从固定下标原地重复
            todays = [placed[(i * per_day + j) % len(placed)] for j in range(per_day)] if placed else []
            for j, c in enumerate(todays):
                meta = c.get("metadata") or {}
                amount = _line_total(meta, days, pax)
                acts.append(
                    {
                        "time": "09:30" if j == 0 else "15:00",
                        "name": str(c.get("text", ""))[:22],
                        "detail": str(c.get("text", "")),
                        "cost": round(amount / max(pax, 1) / max(days, 1), 2) if amount else None,
                        "citation_key": cite_for(c),
                    }
                )
            if family:
                acts.insert(1, {"time": "12:30", "name": "回酒店午休", "detail": nap_detail, "cost": None, "citation_key": None})
                acts.append({"time": "17:30", "name": "住处周边晚餐与散步", "detail": "在住处步行范围内解决晚餐，避免当日再次长途移动。", "cost": None, "citation_key": None})
            areas = [str((c.get("metadata") or {}).get("city") or dest) for c in todays]
            day_plans.append(
                {
                    "day": i + 1,
                    "date": None,
                    "theme": ("儿童节奏日" if family else "行程日") + f" · {areas[0] if areas else dest}",
                    "area": areas[0] if areas else dest,
                    "activities": acts,
                }
            )

        ticket_total = sum(
            _line_total(c.get("metadata") or {}, days, pax) or 0.0 for c in placed
        )
        if ticket_total:
            lines.append({"name": "门票：行程内景点合计", "amount": round(ticket_total, 2 if ticket_total % 1 else 0), "category": "门票"})

        total = float(sum(float(l["amount"]) for l in lines))
        user_budget = slots.get("budget")
        notes: list[str] = []
        if family:
            age = slots.get("child_age")
            age_txt = f"（{age} 岁）" if age else ""
            notes.append(
                "儿童节奏：每日 2–3 个主要活动，中午留 1–1.5 小时午休；"
                f"同行儿童{age_txt}"
                "相邻活动移动控制在 1.5 小时内，避免长时间排队与碎石路面。"
            )
        if budget_scene:
            notes.append(
                "省钱动作：住宿选青旅或民宿多人间、跨城交通用夜巴或慢车、"
                "免费点位替代付费景区、用城市通票替代单次购票。"
            )
        if slots.get("has_elder"):
            notes.append(guardrail_texts.get("elder_pacing", "同行有长者时请把单日行程压到 2–3 个点并预留午休。"))
        if user_budget:
            notes.append(
                f"预算核对：分项合计 {total:.0f} 元，用户预算 {float(user_budget):.0f} 元，"
                f"含 10% 缓冲上限 {float(user_budget) * 1.1:.0f} 元。"
            )

        disclaimers = [guardrail_texts.get("safety_disclaimer", "行程为参考建议，出行前请复核天气、交通与景区公告。")]
        if family:
            disclaimers.append(guardrail_texts.get("minor_pacing", ""))

        suggestions = self._trip_suggestions(scenes, dest, findings)

        itinerary = {
            "title": f"{dest}{days} 天行程" + ("（亲子）" if family and not budget_scene else ""),
            "destination": dest,
            "party": str(slots.get("party") or ""),
            "date_range": str(slots.get("date_range") or ""),
            "scenes": scenes,
            "days": day_plans,
            "budget": {
                "currency": "CNY",
                "lines": lines,
                "total": round(total, 2),
                "user_budget": float(user_budget) if user_budget else None,
                "buffer_ratio": 0.1,
                "within_budget": (total <= float(user_budget) * 1.1) if user_budget else True,
            },
            "notes": [n for n in notes if n],
            "disclaimers": [d for d in disclaimers if d],
        }
        return {"itinerary": itinerary, "suggestions": suggestions}

    def _trip_suggestions(self, scenes: list[str], dest: str, findings: list[str]) -> list[str]:
        out = [
            f"出发前 3 天复核 {dest} 的天气与景区公告，闭园或停运信息以官方渠道为准。",
            "把证件、订单与保险单各存一份手机离线副本，另留一份给同行人。",
        ]
        if "family" in scenes:
            out.insert(0, "带孩子优先订可免费取消的住宿，给行程留出因孩子状态临时调整的余地。")
        if "budget" in scenes:
            out.insert(0, "住宿连住不换店，选含早餐的房型，把省下的钱留给门票与交通。")
        if "roadtrip" in scenes:
            out.insert(0, "取车时拍车身视频并记录油量与里程，还车争议基本都出在这一步。")
        if findings:
            out.append("上一版未通过校验的部分已按校验意见修改，具体见 validation.repair_actions。")
        return out

    # ------------------------------------------------------------- 签证
    def _visa_plan(self, ctx: dict[str, Any]) -> dict[str, Any]:
        slots: dict[str, Any] = ctx.get("slots") or {}
        chunks: list[dict[str, Any]] = list(ctx.get("chunks") or [])
        tools: dict[str, Any] = ctx.get("tool_results") or {}
        guardrail_texts: dict[str, str] = ctx.get("guardrail_texts") or {}

        country = str(slots.get("destination_country") or slots.get("destination") or "")
        policy = tools.get("visa_policy") or {}
        materials = (policy.get("payload") or {}).get("materials") or []

        days = int(slots.get("days") or 0)
        notes = [
            f"{country}签证以官方公告与指定代办机构说明为准，本清单为公开信息整理。",
            "材料一致性比金额更重要：行程日期、机票、酒店、在职证明四者须互相衔接。",
        ]
        if materials:
            notes.append(f"共整理到 {len(materials)} 项材料，逐项来源与生效日见 checklist。")

        itinerary = {
            "title": f"{country}签证材料清单",
            "destination": country,
            "party": str(slots.get("party") or ""),
            "date_range": str(slots.get("date_range") or ""),
            "scenes": list(ctx.get("scenes") or ["visa"]),
            "days": [
                {
                    "day": 1,
                    "date": None,
                    "theme": "材料准备（无固定次序，按代办机构要求提交）",
                    "area": "",
                    "activities": [
                        {
                            "time": "—",
                            "name": str(m.get("name") or "材料"),
                            "detail": str(m.get("requirement") or ""),
                            "cost": None,
                            "citation_key": m.get("citation_key"),
                        }
                        for m in materials
                    ],
                }
            ][:1] if materials else [],
            "budget": None,
            "notes": notes,
            "disclaimers": [
                guardrail_texts.get(
                    "visa_not_legal_advice",
                    "以上为公开信息整理，不构成法律意见；请以使领馆官方公告为准。",
                )
            ],
        }
        suggestions = [
            "先向指定代办机构确认所在领区的材料清单再准备，避免按旧清单跑空。",
            "签证材料请保证行程日期、机票、酒店、在职证明四者一致，矛盾项是补件与拒签的首要原因。",
            "旺季（寒暑假前、长假前）办理时长会延长，建议至少提前 3-4 周启动。",
        ]
        return {"itinerary": itinerary, "suggestions": suggestions}
