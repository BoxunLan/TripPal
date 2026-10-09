"""FastAPI 入口：POST /plan，以及托管 web/index.html 的试运行界面。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import REPO_ROOT
from .deps import Deps, build_deps
from .graph import build_graph
from .schemas import (
    ClarifyResponse,
    DegradedResponse,
    PlanRequest,
    PlanResponse,
    AnswerResponse,
    GuideResponse,
    RealtimeResponse,
    ValidationReport,
)

logger = logging.getLogger("travel")

# 「travel」这一族的日志默认**没有任何 handler** —— 只会经 root 的 lastResort 吐出
# WARNING 及以上，于是 `app/llm.py` 里那些 INFO 级的「每次模型调用（档位 / 耗时 / 超时）」
# 在服务日志里根本看不见，「下次再慢，看日志就知道」就落空了（2026-10-09 实测确认）。
# 这里给这个 logger 单独挂 handler，不动 root，免得把 httpx 之类第三方库一起放大。
# 级别用 TRAVEL_LOG_LEVEL 调（默认 INFO；设 WARNING 就只留失败与偏慢的）。
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(
    getattr(logging, os.environ.get("TRAVEL_LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
)

WEB_INDEX = REPO_ROOT / "web" / "index.html"

# 节点 → 用户看得懂的中文标签。给 /plan/stream 用；顺序必须与 graph 的执行顺序一致，
# 页面靠它渲染「走到第几步」。
# realtime / knowledge 是两条旁路：命中事实问询时走 clarify → 旁路 → output，
# 上面的 classify / route / retrieve / generate / validate 五步一个都不亮。
NODE_LABELS: dict[str, str] = {
    "clarify": "体检必填槽位",
    "realtime": "判定为实时事实问询",
    "knowledge": "判定为常识问询",
    "guide": "判定为寒暄/引导",
    "classify": "判断场景分类",
    "route": "选择路线与叠加层",
    "retrieve": "分层检索知识",
    "generate": "生成行程草稿",
    "validate": "校验预算 / 事实 / 护栏",
    "output": "装配最终答复",
}


def _stage_detail(node: str, delta: dict) -> str:
    """把节点产出压成一句中文短注。

    进度信息是锦上添花，绝不能把主链路带崩 —— 所以整段吞异常。
    开发期文案恒中文（与 findings/日志同一口径），用户可见的输出才跟语言。
    """
    try:
        if node == "clarify":
            miss = delta.get("missing_slots") or []
            if delta.get("realtime_intent") is not None:
                it = delta["realtime_intent"]
                return f"实时事实（{it.info_type}）· 主体 {it.subject}"
            if delta.get("knowledge_intent") is not None:
                return f"常识问询 · 主体 {delta['knowledge_intent'].subject}"
            if delta.get("social_intent") is not None:
                return f"寒暄引导 · {delta['social_intent'].kind}"
            if delta.get("carry_over"):
                return f"承接上一轮主体「{delta.get('hint_subject') or ''}」"
            return f"缺 {len(miss)} 项：{' / '.join(miss)}" if miss else "槽位齐全"
        if node == "realtime":
            found = delta.get("realtime_found") or []
            expired = delta.get("realtime_expired") or 0
            parts = [f"实时层命中 {len(found)} 条"]
            if expired:
                parts.append(f"已过期丢弃 {expired} 条")
            elif not found:
                parts.append("未查到未过期记录")
            return " · ".join(parts)
        if node == "knowledge":
            hits = delta.get("knowledge_hits") or []
            draft = delta.get("knowledge_draft")
            # 常量「非答案」必须留着：命中的是行程材料，不是面积 / 海拔这类答案。
            head = f"知识库相关条目 {len(hits)} 条（非答案）" if hits else "知识库无相关条目"
            answered = bool(getattr(draft, "answer", ""))
            return f"{head} · {'模型已作答' if answered else '模型未给出答案'}"
        if node == "guide":
            reply = delta.get("guide_reply") or ""
            return f"引导语 {len(reply)} 字" if reply else "引导语（退回定稿）"
        if node == "classify":
            c = delta.get("classification")
            if c is None:
                return ""
            primary = getattr(c, "primary_scene", None) or "-"
            # SceneClassificationResult 没有 scenes/confidence 字段：
            # 场景列表在 labels 里，置信度取与 primary_scene 对应那一条。
            hit = next(
                (lb for lb in (getattr(c, "labels", None) or [])
                 if getattr(lb, "scene_id", None) == primary),
                None,
            )
            conf = getattr(hit, "confidence", None)
            return f"{primary}　置信 {conf:.2f}" if conf is not None else primary
        if node == "route":
            # 成功走 retrieve 时键是 route_config；缺槽短路时键是 route（两种都可能到这一步）
            r = delta.get("route_config") or delta.get("route")
            if r is None:
                return ""
            scenes = list(getattr(r, "scenes", None) or [])
            return f"{getattr(r, 'route_id', '-')}（{' + '.join(scenes)}）"
        if node == "retrieve":
            ctx = delta.get("context")
            n = len(getattr(ctx, "chunks", None) or [])
            tools = dict(delta.get("tool_results") or {})
            return f"召回 {n} 条 · 工具 {len(tools)} 个" if tools else f"召回 {n} 条"
        if node == "generate":
            if delta.get("error"):
                return f"失败：{str(delta['error'])[:40]}"
            draft = delta.get("draft")
            days = len(getattr(getattr(draft, "itinerary", None), "days", None) or [])
            return f"草稿 {days} 天" if days else "草稿（清单型，无逐日）"
        if node == "validate":
            rep = delta.get("report")
            if rep is None:
                return ""
            names = [f"{c.name}:{c.status}" for c in (getattr(rep, "checks", None) or [])]
            # validate_node 只在要回炉时才把 round 加进 delta —— 所以有 round 就是「还要重跑一轮」
            again = f" · 触发回炉（第 {delta['round']} 轮）" if delta.get("round") else ""
            return f"{' / '.join(names) or '无检查项'}{again}"
        if node == "output":
            return f"type={getattr(delta.get('response'), 'type', '?')}"
    except Exception:  # noqa: BLE001 - 详见 docstring
        logger.debug("stage detail 失败 node=%s", node, exc_info=True)
    return ""


def create_app(deps: Deps | None = None) -> FastAPI:
    deps = deps or build_deps()
    compiled = build_graph(deps)

    app = FastAPI(
        title="旅行 Agent — 两周垂直切片",
        version="0.1.0",
        description=(
            "clarify → (realtime 旁路 | knowledge 旁路 | "
            "classify → route → retrieve → generate → validate) → output"
        ),
    )
    app.state.deps = deps
    app.state.graph = compiled

    # 本地开发工具，只监听 127.0.0.1；放开 CORS 是为了让直接用文件/预览面板打开的
    # 页面也能打 /plan，省得为了看个界面还去起代理。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        """试运行界面。每次读盘，改 HTML 不用重启服务。"""
        if not WEB_INDEX.exists():
            return HTMLResponse("<h1>找不到 web/index.html</h1>", status_code=404)
        # no-store 而不是 no-cache：这个页面每次改都在原位生效，但浏览器不知道。
        # 踩过一次——用户拿到的是改造前的旧 HTML，症状是「新功能没生效」。
        return HTMLResponse(
            WEB_INDEX.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "llm_provider": deps.llm.provider,
            "vector_backend": getattr(deps.store, "name", "unknown"),
            "chunks": deps.store.count(),
            # embedding 是「有没有真实跑起来」最容易配错的一环（模型/维度/批量），
            # 放在这里一眼可见：维度必须与建表语句的 vector(N) 相同。
            "embedding": deps.settings.embedding.model or "local-hash",
            "embedding_dim": getattr(deps.embedder, "dim", None),
            "embedding_batch": deps.settings.embedding.batch,
            "routes_version": deps.settings.version_pin,
            "retriever_version": deps.settings.retriever_version,
            "classifier_version": deps.settings.classifier_version,
        }

    @app.get("/session/{session_id}", include_in_schema=False)
    def session_state(session_id: str) -> dict:
        """这条会话**现在记住了什么** —— 供页面「会话记忆」面板显示。

        存在的理由（2026-10-04 用户报「没有对话记忆」）：那一栏原先在服务端根本不存在，
        连日志都没有 —— 用户想回看自己刚问过什么，只能翻浏览器。会话状态既然是我们
        自己维护的，就应该能看一眼。
        """
        return deps.sessions.snapshot(session_id)

    @app.post(
        "/plan",
        response_model=ClarifyResponse | PlanResponse | RealtimeResponse | AnswerResponse | GuideResponse | DegradedResponse,
    )
    def plan(request: PlanRequest):
        state = _initial_state(request)
        try:
            final = compiled.invoke(state)
        except Exception as exc:  # noqa: BLE001 - 接口契约只承诺五种响应类型，异常也要落进去
            logger.exception("图执行失败 session=%s", request.session_id)
            return DegradedResponse(
                reason=f"链路执行失败：{type(exc).__name__}: {exc}",
                partial={"message": request.message},
                validation=ValidationReport(
                    passed=False, round=state["round"], checks=[], unresolved=[str(exc)]
                ),
            )
        response = final.get("response")
        if response is None:
            raise RuntimeError("图没有产出响应，请检查节点实现")
        logger.info(
            "session=%s type=%s route=%s",
            request.session_id,
            getattr(response, "type", "?"),
            getattr(getattr(response, "route", None), "route_id", "-"),
        )
        return response

    def _initial_state(request: PlanRequest) -> dict:
        """两个端点必须构造完全相同的初始状态，否则会调出「stream 和一次性结果不一致」的怪事。"""
        state: dict = {
            "request_id": uuid.uuid4().hex,
            "session_id": request.session_id,
            "message": request.message,
            "round": 0,
        }
        # 表单里填的信息随这一句一起发。**必须原样带过去**：它要么在这轮生效、
        # 要么一点都不生效 —— 半截生效比完全不支持更让人无从判断。
        if request.slot_overrides:
            state["slot_overrides"] = dict(request.slot_overrides)
        if request.output_language:
            # 调用方强制输出语言（开发联调用）：只换输出语言，检索仍走中文。
            state["forced_output_language"] = request.output_language
        return state

    # --- 后台任务表：给「start + 轮询」这套接口用 ---------------------------------
    # 为什么在 NDJSON 流之外还要第二套传输方式：
    #   流会被「不知道流是什么」的中间层缓冲到结束才一次性吐出（编辑器内嵌预览、
    #   公司代理、某些 CDN 都干得出来），那时页面上的事件全在最后一刻到达，
    #   用户依然无从区分「卡住」和「慢」。
    #   短轮询每段都是独立的小请求，再笨的代理也拦不住 —— 它是最笨但最稳的进度通道。
    # 页面优先走轮询；/plan/stream 保留给 curl 调试与测试。
    tasks: dict[str, dict] = {}
    keep_last = 50

    def _reap() -> None:
        """只保留最近若干个任务。这是进程内的临时字典，挂久了会涨。"""
        while len(tasks) > keep_last:
            oldest = min(tasks.values(), key=lambda s: s["started"])
            tasks.pop(oldest["task_id"], None)

    async def _collect(task_id: str, state: dict) -> None:
        """把图跑完，边跑边把节点事件 append 进任务槽。调用方不必 await 它。"""
        slot = tasks[task_id]
        started = slot["started"]
        try:
            async for chunk in compiled.astream(state):
                for node, delta in (chunk or {}).items():
                    delta = delta or {}
                    if "response" in delta:
                        slot["response"] = delta["response"]
                    slot["stages"].append(
                        {
                            "stage": node,
                            "label": NODE_LABELS.get(node, node),
                            "detail": _stage_detail(node, delta),
                            "ms": int((time.perf_counter() - started) * 1000),
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - 后台协程没人 await，异常必须落到槽里
            logger.exception("后台任务失败 task=%s", task_id)
            slot["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            slot["done"] = True
            slot["ms"] = int((time.perf_counter() - started) * 1000)

    @app.post("/plan/start", include_in_schema=False)
    async def plan_start(request: PlanRequest) -> dict:
        """起一个后台任务，**立即**返回 task_id（不等链路跑完）。

        页面靠它把「发请求」和「等结果」拆开：拿到 id 后短轮询 /plan/progress/{id}，
        每一步都比上一步多点亮一格 —— 用户看到的进度是服务端跑出来的，不是本地定时器。
        """
        task_id = uuid.uuid4().hex
        tasks[task_id] = {
            "task_id": task_id,
            "session_id": request.session_id,
            "stages": [],
            "done": False,
            "response": None,
            "error": None,
            "ms": 0,
            "started": time.perf_counter(),
        }
        _reap()
        # 不 await：让它自己在后台跑完。协程内部已经有兜底，不会把异常丢到外面。
        asyncio.create_task(_collect(task_id, _initial_state(request)))
        return {"task_id": task_id}

    @app.get("/plan/progress/{task_id}", include_in_schema=False)
    def plan_progress(task_id: str):
        """查进度。返回 `{done, ms, stages[], response?, error?}`。

        `response` 只在跑完后出现，字段与 `POST /plan` 完全一致。
        """
        slot = tasks.get(task_id)
        if slot is None:
            return JSONResponse({"error": "任务不存在或已被清理"}, status_code=404)
        return {
            "task_id": task_id,
            "done": slot["done"],
            "ms": int((time.perf_counter() - slot["started"]) * 1000),
            "stages": slot["stages"],
            "response": slot["response"].model_dump(mode="json") if slot["response"] else None,
            "error": slot["error"],
        }

    @app.post("/plan/stream", include_in_schema=False)
    async def plan_stream(request: PlanRequest) -> StreamingResponse:
        """和 /plan 同一条链路，但每跑完一个节点就推一行 NDJSON。

        存在的理由：真实模型一条链路 11–115s（2026-10-07 实测 5 条，中位 ~75s），
        回炉重生成时可达 3 分钟；分档超时为分类 ≤15s、生成 ≤180s。这段等待里如果只有
        客户端计时，浏览器会把不可见标签页的定时器
        节流到分钟级 —— 用户看到的就是「一直转圈、已等 0s」，无法区分是慢还是挂了。
        由服务端来推事件，进度才是真的。

        行格式：
          {"stage":节点名,"label":中文名,"detail":短注,"ms":累计毫秒}
          {"done":true,"ms":总耗时,"response":与 /plan 完全相同的响应体}
          {"error":"..."}                      执行失败（只在异常时出现）
        """

        async def emit() -> AsyncIterator[str]:
            started = time.perf_counter()
            response = None
            try:
                async for chunk in compiled.astream(_initial_state(request)):
                    for node, delta in (chunk or {}).items():
                        delta = delta or {}
                        if "response" in delta:
                            response = delta["response"]
                        yield json.dumps(
                            {
                                "stage": node,
                                "label": NODE_LABELS.get(node, node),
                                "detail": _stage_detail(node, delta),
                                "ms": int((time.perf_counter() - started) * 1000),
                            },
                            ensure_ascii=False,
                        ) + "\n"
            except Exception as exc:  # noqa: BLE001 - 生成期已开始推流，只能把错误写成一行
                logger.exception("流式执行失败 session=%s", request.session_id)
                yield json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False) + "\n"
                return
            if response is None:
                yield json.dumps({"error": "图没有产出响应，请检查节点实现"}, ensure_ascii=False) + "\n"
                return
            yield json.dumps(
                {
                    "done": True,
                    "ms": int((time.perf_counter() - started) * 1000),
                    "response": response.model_dump(mode="json"),
                },
                ensure_ascii=False,
            ) + "\n"

        # X-Accel-Buffering: no —— 任何一层反向代理（含某些编辑器内嵌预览）都可能把
        # 流缓冲到结束才吐出来，那样 NDJSON 的事件就全在最后一刻到达，等于没有进度。
        return StreamingResponse(
            emit(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    return app


app = create_app()
