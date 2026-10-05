"""LangGraph 编排。

节点固定为 clarify → classify → route → retrieve → generate → validate → output，
外加两个**旁路**节点 realtime / knowledge（见下）。条件边全部是短路/绕行/回环必需，不新增业务阶段：

    clarify --(实时事实问询)--> realtime  --> output
    clarify --(静态事实问询)--> knowledge --> output
    clarify --(寒暄/闲话/元问题)--> guide   --> output
    clarify --(缺槽)--> output        clarify --(槽齐)--> classify
    route   --(分类后仍缺槽)--> output route   --(槽齐)--> retrieve
    validate --(repair 且未用满轮次)--> generate   --(否则)--> output

realtime 存在的理由：「天安门什么时候升旗」不是「缺 4 个槽位」，它根本不是排行程的请求。
用行程槽位去拦它，用户会看到「想去哪、玩几天、预算多少、几个人」四个全都问错的问题。

knowledge 存在的理由（同一错误的第二形态）：「西湖有多大」不随时间变，连实时闸门都不命中，
于是照样掉回槽位体检、照样回那四问。见 `app/intent.py` 的模块 docstring。

validate 失败最多回到 generate 一次（max_repair_rounds 固定 1，见 RouteConfig 校验器）。
不做第 2 轮回环，不做独立安全引擎。
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .classifier import classify
from .deps import Deps
from .generate import GenerationError, assemble_checklist, assemble_citations, generate_plan
from .guide import build_guide_response, compose_guide
from .i18n import detect_language, t
from .intent import (
    KnowledgeIntent,
    RealtimeIntent,
    SocialIntent,
    carry_over_subject,
    detect_knowledge_intent,
    detect_realtime_intent,
    detect_social_intent,
    locative_referent,
)
from .knowledge import (
    answer_question,
    build_answer_response,
    query_terms,
    referent_context,
    search_knowledge,
    subject_place,
)
from .llm import LLMError
from .realtime import build_realtime_response, search_realtime_facts
from .retrieve import retrieve_context, run_info_tools
from .router import build_route
from .schemas import (
    AnswerDraft,
    Chunk,
    ClarifyResponse,
    DegradedResponse,
    GeneratorOutput,
    PlanResponse,
    RealtimeFact,
    RetrievedContext,
    RouteConfig,
    SceneClassificationResult,
    ToolResult,
)
from .slots import (
    build_clarify_question,
    extract_slots,
    merge_slots,
    missing_slots,
    required_slots_for,
    scene_hints,
    slots_from_form,
)
from .validate import run_validation


def carry_context(session: Any, referent: str = "", *, carried: bool = False) -> str:
    """交给模型的**对话上下文**：解开的方位指代 + 上一轮的**原话与答复**。

    真 bug（2026-10-04 用户实测，审计 session `7p1fi1`）：
    「它还有个滴水湖校区对吗」→「具体位置在哪儿」。「具体位置」是个**占位名词**，
    指的就是上一轮那个东西 —— 主体接上了（华东师范大学），但模型**看不到上一轮答复**，
    只能答学校的通用校区位置，答不到用户真正在问的那个校区。
    `prompts/answer.md` 规则 2 要求「只回答问的那一件事」，前提是它知道上文在说什么。

    只在**承接态**（`carried`）挂上一轮：非承接句自带完整信息，多给反而稀释。
    """
    parts: list[str] = []
    base = referent_context(referent)
    if base:
        parts.append(base)
    if carried and session is not None:
        recent = list(getattr(session, "recent", None) or [])
        prev = recent[-1] if recent else None
        if prev is not None and (prev.message or prev.reply):
            said = (prev.reply or "").strip()
            tail = f"系统当时的回答是「{said[:200]}」" if said else "系统当时没有给出内容"
            parts.append(
                f"上一轮用户问的是「{(prev.message or '').strip()}」，{tail}。"
                "这一句是对它的追问，主体与范围都按上一轮算。"
            )
    return " ".join(parts)


class PlanState(TypedDict, total=False):
    request_id: str
    session_id: str
    message: str
    # 可选：调用方强制指定的输出语言（zh/en/ja/ko）。空则按 message 字符集判定。
    # 只影响**输出**语言，检索查询恒为中文（见 app/i18n.py 的边界）。
    forced_output_language: str
    decision: str  # "clarify" | "continue" | "realtime" | "knowledge" | "social"
    clarify_question: str
    missing_slots: list[str]
    # 界面「行程信息卡」里填的结构化信息（不经过抽取，见 `slots.slots_from_form`）。
    # 给那些「口语怎么说都抽不准」的字段留一条确定能通的路；三条来源的优先级是
    # 本句 > 表单 > 会话历史，详见 `clarify_node`。
    slot_overrides: dict[str, Any]
    slots: dict[str, Any]
    # 这一句是不是在**承接上一轮**（短、无地名、带疑问，主体来自会话）。
    # 是的话，知识检索要把上一轮的实词一起带上（对话式查询扩展）。
    carry_over: bool
    hint_subject: str
    # 旁路 1：判定为实时事实问询时的意图与检索结果（见 app/intent.py）
    realtime_intent: RealtimeIntent
    realtime_found: list[RealtimeFact]
    realtime_expired: int
    # 旁路 2：判定为静态事实问询（常识）时的意图、命中的知识库条目与模型作答
    knowledge_intent: KnowledgeIntent
    knowledge_hits: list[Chunk]
    knowledge_draft: AnswerDraft
    # 旁路 3：判定为寒暄 / 闲话 / 元问题时的意图与模型给出的引导语
    social_intent: SocialIntent
    guide_reply: str
    classification: SceneClassificationResult
    route_config: RouteConfig
    context: RetrievedContext
    chunk_index: dict[str, Any]
    tool_results: dict[str, ToolResult]
    draft: GeneratorOutput
    report: Any
    round: int
    validation_findings: list[str]
    response: Any


def build_graph(deps: Deps):
    settings = deps.settings

    def output_language(state: PlanState) -> str:
        """输出语言：调用方显式指定优先，否则按 message 的字符集判定。"""
        return state.get("forced_output_language") or detect_language(state.get("message") or "")

    def _reply_digest(response) -> str:
        """一句话摘要 —— 落进会话审计，也供页面「会话记忆」面板显示。"""
        kind = getattr(response, "type", "")
        if kind == "answer":
            return getattr(response, "answer", "") or "（未给出答案）"
        if kind == "guide":
            return getattr(response, "reply", "")
        if kind == "realtime":
            found = list(getattr(response, "found", None) or [])
            return (found[0].text if found else "") or getattr(response, "note", "")
        if kind == "clarify":
            return getattr(response, "question", "")
        if kind == "plan":
            days = len(getattr(getattr(response, "itinerary", None), "days", None) or [])
            return f"行程 {days} 天"
        return str(getattr(response, "reason", "") or "")

    def _recall_reply(session_id: str, language: str) -> str:
        """把「刚刚聊了什么」如实拼出来：列出本会话最近的用户原话。

        只列**用户自己说过的话**，不列系统的回答摘要 —— 用户要核对的是「你有没有在听」，
        复述他问过什么是最直接的证据（这也是为什么它必须确定性、不能由模型润色）。
        """
        messages = deps.sessions.recent_messages(session_id, limit=5)
        if not messages:
            return t("gd.recall.empty", language)
        items = " / ".join(f"{i}. {m}" for i, m in enumerate(messages, start=1))
        return t("gd.recall.list", language, count=len(messages), items=items)

    def _merged_terms(state: PlanState, subject: str) -> list[str]:
        """这一轮的实词 ∪ 承接来的实词 —— 存下来给**再下一句**用。

        承接链不能只留最近一句：「那要预约吗」自己只带「预约」，若只存它，
        再下一句「多少钱」就再也回不到「博物馆」上了。
        """
        session = deps.sessions.get(state["session_id"])
        here = query_terms(subject, state.get("message") or "")
        carried = list(session.last_fact_terms) if state.get("carry_over") else []
        return sorted(set(here) | set(carried))

    def _carry_context(state: PlanState, referent: str) -> str:
        session = deps.sessions.get(state["session_id"])
        return carry_context(session, referent, carried=bool(state.get("carry_over")))

    # ------------------------------------------------------------- clarify
    def clarify_node(state: PlanState) -> PlanState:
        message = state["message"]
        session = deps.sessions.get(state["session_id"])
        extracted = extract_slots(message, settings)
        # 信息来源的优先级：**本句 > 表单 > 会话历史**。
        # 本句最优先是因为它最新、最明确；表单是用户在点发送前刚填的（也是这一轮的意图），
        # 但它是「面板」，可能停留着上一次的值；会话历史最旧，只补前两者没提到的部分。
        overrides = slots_from_form(state.get("slot_overrides") or {}, settings)
        prior = merge_slots(session.slots, overrides)

        # 会话承接：追问句自己**不含主体**（「那要预约吗」「开放时间呢」）。
        # 先把它接到上一轮的事实主体上，再判意图 —— 否则这三道闸门都在对着一句
        # 没有主语的话做判断，结果就是回一句四连问（用户报的「没有对话记忆」）。
        # 这里刻意只用本句抽出的槽位：意图判定看的是**这句话在说什么**，
        # 表单里留着的目的地不该让一句追问不再承接上一轮主体。
        hint_subject, hint_place = carry_over_subject(message, settings, session, extracted)
        carried = bool(hint_subject)

        # 先判意图，再做槽位体检。「天安门什么时候升旗」不是缺 4 个槽位，
        # 它压根不是排行程的请求 —— 顺序反了就会问出 4 个方向全错的问题。
        #
        # 顺序也有讲究：实时在前、常识在后。「西湖什么时候开门」两边都像，
        # 但它要的是**当日状态**（会过期），归实时更准。
        intent = detect_realtime_intent(
            message, settings, extracted, hint_subject=hint_subject or "", hint_place=hint_place or ""
        )
        if intent is not None:
            merged = merge_slots(prior, extracted)
            deps.sessions.update_slots(state["session_id"], merged)
            return {
                "decision": "realtime",
                "slots": merged,
                "missing_slots": [],
                "realtime_intent": intent,
                "carry_over": carried,
                "hint_subject": hint_subject or "",
            }

        # 「西湖有多大」不含任何时间/开放/天气/交通词 → 上面那道闸门不接管，
        # 于是会掉进下面的槽位体检、被回以「目的地？天数？预算？人数？」。
        # 所以需要这道并列的闸门：静态事实问询同样不是排行程。
        knowledge = detect_knowledge_intent(
            message, settings, extracted, hint_subject=hint_subject or ""
        )
        if knowledge is not None:
            merged = merge_slots(prior, extracted)
            deps.sessions.update_slots(state["session_id"], merged)
            return {
                "decision": "knowledge",
                "slots": merged,
                "missing_slots": [],
                "knowledge_intent": knowledge,
                "carry_over": carried,
                "hint_subject": hint_subject or "",
            }

        # 前两道闸门都没接管，也不代表这就是排行程 —— 「你好」既不是事实也不是行程。
        # 第三道闸门（最弱、放最后）：短句寒暄 / 闲话 / 元问题 → 走 guide 旁路，
        # 回一句把人引到旅游话题的话，而不是甩出四连问（见 app/intent.py 的寒暄闸门）。
        social = detect_social_intent(message, settings, extracted)
        if social is not None:
            merged = merge_slots(prior, extracted)
            deps.sessions.update_slots(state["session_id"], merged)
            return {
                "decision": "social",
                "slots": merged,
                "missing_slots": [],
                "social_intent": social,
                "carry_over": False,
                "hint_subject": "",
            }

        merged = merge_slots(prior, extracted)
        hints = scene_hints(message, settings)
        required = required_slots_for(hints, settings)
        miss = missing_slots(merged, required)
        deps.sessions.update_slots(state["session_id"], merged)
        if miss:
            return {
                "decision": "clarify",
                "slots": merged,
                "missing_slots": miss,
                # 追问是给终端用户看的 → 跟输出语言；校验 findings 是给开发看的 → 恒为中文
                "clarify_question": build_clarify_question(miss, merged, language=output_language(state)),
            }
        return {"decision": "continue", "slots": merged, "missing_slots": []}

    def clarify_gate(state: PlanState) -> str:
        decision = state.get("decision")
        if decision == "realtime":
            return "realtime"
        if decision == "knowledge":
            return "knowledge"
        if decision == "social":
            return "guide"
        return "output" if decision == "clarify" else "classify"

    # ------------------------------------------------------------- realtime（旁路）
    def realtime_node(state: PlanState) -> PlanState:
        """只查实时层，不做模型调用、不做向量检索 —— 事实断言不该由模型或相似度生成。

        这也让这条旁路**很快**：没有分类、没有检索编排、没有成稿。
        用户问「今天几点升旗」不该等 20 秒。
        """
        found, expired = search_realtime_facts(
            settings=settings,
            store=deps.store,
            intent=state["realtime_intent"],
            today=deps.today(),
        )
        # 记下这一轮的主体与实词：下一句若是在追问（「开放时间呢」），就靠它接上。
        intent = state["realtime_intent"]
        deps.sessions.remember_fact(
            state["session_id"],
            subject=intent.subject,
            place=intent.place or "",
            terms=_merged_terms(state, intent.subject),
        )
        return {"realtime_found": found, "realtime_expired": expired}

    # ------------------------------------------------------------- knowledge（旁路）
    def knowledge_node(state: PlanState) -> PlanState:
        """先扫 scene / general 层，再让模型用通用常识作答。

        顺序是刻意的：知识库命中只是「同主题材料」，**不是答案**（问「鼓浪屿有多大」命中
        的是一堆行程攻略，谈的是怎么玩）。所以答案由模型给，命中条目并列作相关参考。

        为什么这一层允许调模型而 realtime 层不许：时刻会变，模型只能猜（今天的升旗时间
        是 06:06 还是 06:07，它不知道）；而面积 / 海拔 / 历史年份稳定，是模型语料里最
        扎实的部分，且这类长尾常识没有任何本地资料库能覆盖。
        代价用三道闸兜住：文案标注「未经核实」、给复核入口、时效性信息在提示词里拒答。
        详见 `app/knowledge.py` 的模块 docstring。
        """
        intent = state["knowledge_intent"]
        session = deps.sessions.get(state["session_id"])
        # 方位指代先解开。真 bug（2026-10-04 用户报）：「上海临港有哪些大学」→
        # 「华东师范大学是不是有个新校区在那里」—— 这一句**自带新主体**，整体承接会把
        # 主体覆盖成上一轮的（错），但里面的「那里」必须解得开，否则模型不知道新校区在哪儿。
        # 所以指代只当**地点上下文**用，不动主体。
        referent = locative_referent(state["message"], settings, session)
        if referent == intent.subject:
            referent = ""  # 与主体同指，不必再说一遍
        # 传**用户原话**：相关条目的排序要按这句话里的实词来（只按主体找，同一个城市的
        # 任何问题都会列出同样那几条 —— 2026-10-04 用户报的那类问题）。
        # 承接来的追问（「那要预约吗」）再并上**上一轮的实词** —— 否则它自己那几个词
        # （「预约」）落在别的条目上，跟上一句问的根本不是一回事。
        extra = list(session.last_fact_terms) if state.get("carry_over") else []
        hits = search_knowledge(
            settings=settings,
            store=deps.store,
            intent=intent,
            message=state["message"],
            extra_terms=extra,
        )
        deps.sessions.remember_fact(
            state["session_id"],
            subject=intent.subject,
            # 有指代时**所在地就是那个指代对象**：再下一句「那里有什么好吃的」靠它回到
            # 「上海临港」。问的是「华东师范大学」时，词典查不出它的所在地，只有指代解得出来。
            place=referent or subject_place(settings, intent.subject),
            terms=_merged_terms(state, intent.subject),
        )
        draft = answer_question(
            settings=settings,
            llm=deps.llm,
            intent=intent,
            hits=hits,
            message=state["message"],
            context=_carry_context(state, referent),
            language=output_language(state),
        )
        return {"knowledge_hits": hits, "knowledge_draft": draft}

    # ------------------------------------------------------------- guide（旁路）
    def guide_node(state: PlanState) -> PlanState:
        """寒暄旁路：不检索、不调工具，只让通用模型回一句引导语。

        它不产生任何可核实的事实断言 —— 所以没有时效闸门、也没有「未经核实」标注。
        模型不可用时 `compose_guide` 返回空串，`output` 会退回 i18n 里的定稿话术；
        绝不能因为模型挂掉就退化成那四连问（降级，但不是降级成错误的东西）。
        """
        intent = state["social_intent"]
        language = output_language(state)
        # 「我们刚才聊了什么」这类**记忆类元问题**不调模型：它的唯一正确来源就是我们
        # 自己记的那几轮。交给模型只有两种结果 —— 看不到历史时编，看得到历史时也可能
        # 答歪；而这句问话是用户**在验证系统记不记得住**，答错比不答更糟。
        if intent.kind == "recall":
            return {"guide_reply": _recall_reply(state["session_id"], language)}
        reply = compose_guide(
            settings=settings,
            llm=deps.llm,
            intent=intent,
            message=state["message"],
            language=language,
        )
        return {"guide_reply": reply}

    # ------------------------------------------------------------- classify
    def classify_node(state: PlanState) -> PlanState:
        result = classify(
            settings=settings,
            llm=deps.llm,
            message=state["message"],
            session_slots=state.get("slots") or {},
            request_id=state["request_id"],
        )
        return {"classification": result, "slots": result.slots}

    # ------------------------------------------------------------- route
    def route_node(state: PlanState) -> PlanState:
        classification = state["classification"]
        route = build_route(
            settings=settings,
            classification=classification,
            language=output_language(state),
        )
        assert route is not None
        # 二次门控：visa 的 destination_country 只有分类之后才确定用哪个槽位策略
        required = [s for s in route.scenes if s in settings.scene_ids]
        miss = missing_slots(classification.slots, required_slots_for(required, settings))
        if miss:
            return {
                "route": route,
                "decision": "clarify",
                "missing_slots": miss,
                # 不用模型给的那句：它是模型自由发挥的，语言没保证。
                # 追问要跟用户语言，这里必须走确定性装配。
                "clarify_question": build_clarify_question(
                    miss, classification.slots, language=output_language(state)
                ),
            }
        return {"route_config": route, "decision": "continue"}

    def route_gate(state: PlanState) -> str:
        return "output" if state.get("decision") == "clarify" else "retrieve"

    # ------------------------------------------------------------- retrieve
    def retrieve_node(state: PlanState) -> PlanState:
        route = state["route_config"]
        classification = state["classification"]
        bundle = retrieve_context(
            settings=settings,
            store=deps.store,
            embedder=deps.embedder,
            route=route,
            query=classification.rewritten_query,
            slots=classification.slots,
            today=deps.today(),
        )
        tools = run_info_tools(
            settings=settings,
            toolbox=deps.toolbox,
            route=route,
            slots=classification.slots,
            today=deps.today(),
        )
        return {"context": bundle.context, "chunk_index": bundle.chunk_index, "tool_results": tools}

    # ------------------------------------------------------------- generate
    def generate_node(state: PlanState) -> PlanState:
        try:
            output = generate_plan(
                settings=settings,
                llm=deps.llm,
                route=state["route_config"],
                classification=state["classification"],
                context=state["context"],
                chunk_index=state.get("chunk_index") or {},
                tool_results=state.get("tool_results") or {},
                round_no=state.get("round", 0),
                validation_findings=state.get("validation_findings") or [],
            )
        except (GenerationError, LLMError) as exc:
            # 模型/接口失败不抛 500，按契约降级为 degraded
            return {"error": str(exc)}  # type: ignore[typeddict-unknown-key]
        return {"draft": output}

    # ------------------------------------------------------------- validate
    def validate_node(state: PlanState) -> PlanState:
        if state.get("draft") is None:
            return {"validation_findings": []}
        route = state["route_config"]
        round_no = state.get("round", 0)
        tools = dict(state.get("tool_results") or {})
        outcome = run_validation(
            settings=settings,
            toolbox=deps.toolbox,
            store=deps.store,
            route=route,
            itinerary=state["draft"].itinerary,
            context=state["context"],
            chunk_index=state.get("chunk_index") or {},
            tool_results=tools,
            slots=state["classification"].slots,
            round_no=round_no,
        )
        tools.update(outcome.tool_results)
        repaired = GeneratorOutput(
            itinerary=outcome.itinerary, suggestions=state["draft"].suggestions
        )
        findings: list[str] = []
        for check in outcome.report.checks:
            if check.status == "repair":
                findings.extend(check.findings)
        patch: PlanState = {"draft": repaired, "report": outcome.report, "tool_results": tools}
        if findings and round_no < route.max_repair_rounds:
            patch["round"] = round_no + 1
            patch["validation_findings"] = findings
        return patch

    def validate_gate(state: PlanState) -> str:
        """有 repair 就回 generate。轮次由 validate_node 递增，所以最多只回一次。"""
        report = state.get("report")
        if state.get("draft") is None or report is None:
            return "output"
        if any(c.status == "repair" for c in report.checks):
            return "generate"
        return "output"

    # ------------------------------------------------------------- output
    def output_node(state: PlanState) -> PlanState:
        session_id = state["session_id"]
        decision = state.get("decision")
        report = state.get("report")

        if state.get("error") and state.get("draft") is None:
            response = DegradedResponse(
                reason=f"生成阶段失败：{state['error']}",
                partial={
                    "route": state["route_config"].model_dump(mode="json")
                    if state.get("route_config")
                    else None
                },
                validation=_empty_report(),
            )
        elif decision == "realtime" and state.get("realtime_intent") is not None:
            # 事实问询的答复完全由 realtime 判定 + 检索结果决定，没有模型参与，
            # 所以这里能确定地装配；也不进 citations/checklist 那套（那是行程的东西）。
            response = build_realtime_response(
                settings=settings,
                intent=state["realtime_intent"],
                found=state.get("realtime_found") or [],
                expired=state.get("realtime_expired") or 0,
                message=state["message"],
                language=output_language(state),
            )
        elif decision == "knowledge" and state.get("knowledge_intent") is not None:
            # 常识问询：答案来自一次模型调用（`knowledge_node`），这里只做装配；
            # 也不进 citations/checklist 那套（那是行程的东西）。
            response = build_answer_response(
                settings=settings,
                intent=state["knowledge_intent"],
                hits=state.get("knowledge_hits") or [],
                draft=state.get("knowledge_draft"),
                message=state["message"],
                language=output_language(state),
            )
        elif decision == "social" and state.get("social_intent") is not None:
            # 寒暄 / 闲话 / 元问题：引导语来自一次模型调用（`guide_node`），这里只做装配；
            # 同样不进 citations/checklist 那套（那是行程的东西）。
            response = build_guide_response(
                settings=settings,
                intent=state["social_intent"],
                reply=state.get("guide_reply") or "",
                message=state["message"],
                language=output_language(state),
            )
        elif decision == "clarify" and state.get("draft") is None:
            response = ClarifyResponse(
                question=state.get("clarify_question") or "请补充关键信息。",
                missing_slots=state.get("missing_slots") or [],
            )
        else:
            output = state["draft"]
            route = state["route_config"]
            context = state["context"]
            tools = state.get("tool_results") or {}
            citations = assemble_citations(
                itinerary=output.itinerary,
                context=context,
                chunk_index=state.get("chunk_index") or {},
                tool_results=tools,
            )
            checklist = assemble_checklist(
                settings=settings,
                route=route,
                context=context,
                chunk_index=state.get("chunk_index") or {},
                tool_results=tools,
                slots=state["classification"].slots,
            )
            if report is not None and report.degraded:
                # block：停止成稿，只交材料清单 + 官方链接
                links = sorted(
                    {
                        c.source_url
                        for c in citations
                        if c.source_url
                    }
                )
                response = DegradedResponse(
                    reason="签证政策缺少来源或生效日，按 block 规则停止成稿，改为材料清单 + 官方链接。",
                    partial={
                        "checklist": checklist,
                        "official_links": links,
                        "disclaimer": settings.guardrail_text("visa_not_legal_advice"),
                        "itinerary_draft": output.itinerary.model_dump(mode="json"),
                    },
                    validation=report,
                )
            else:
                response = PlanResponse(
                    route=route,
                    itinerary=output.itinerary,
                    suggestions=output.suggestions,
                    checklist=checklist,
                    citations=citations,
                    validation=report or _empty_report(),
                )

        # 记进会话：类型 + 主体 + 答复摘要。主体是**下一句追问要承接的东西**，
        # 摘要给「刚才我说了什么」之外还留了余地（页面「会话记忆」面板也看得到）。
        fact_subject = ""
        if decision == "knowledge" and state.get("knowledge_intent") is not None:
            fact_subject = state["knowledge_intent"].subject
        elif decision == "realtime" and state.get("realtime_intent") is not None:
            fact_subject = state["realtime_intent"].subject
        deps.sessions.record(
            session_id,
            state["message"],
            getattr(response, "type", "unknown"),
            state.get("missing_slots") or [],
            subject=fact_subject,
            reply=_reply_digest(response),
        )
        return {"response": response}

    # ------------------------------------------------------------- 组装
    graph = StateGraph(PlanState)
    graph.add_node("clarify", clarify_node)
    graph.add_node("realtime", realtime_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("guide", guide_node)
    graph.add_node("classify", classify_node)
    graph.add_node("route", route_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_node("validate", validate_node)
    graph.add_node("output", output_node)

    graph.set_entry_point("clarify")
    graph.add_conditional_edges(
        "clarify",
        clarify_gate,
        {
            "output": "output",
            "classify": "classify",
            "realtime": "realtime",
            "knowledge": "knowledge",
            "guide": "guide",
        },
    )
    graph.add_edge("realtime", "output")
    graph.add_edge("knowledge", "output")
    graph.add_edge("guide", "output")
    graph.add_edge("classify", "route")
    graph.add_conditional_edges("route", route_gate, {"output": "output", "retrieve": "retrieve"})
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges("validate", validate_gate, {"generate": "generate", "output": "output"})
    graph.add_edge("output", END)
    return graph.compile()


def _empty_report():
    from .schemas import ValidationReport

    return ValidationReport(passed=False, round=0, checks=[], unresolved=["链路未跑到校验阶段"])
