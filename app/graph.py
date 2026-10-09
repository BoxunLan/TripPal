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

import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .classifier import classify
from .deps import Deps
from .followups import clean_chips, next_questions
from .generate import GenerationError, assemble_checklist, assemble_citations, generate_plan
from .guide import build_guide_response, compose_guide, scripted_reply
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
    answer_from_itinerary,
    apply_delegation_defaults,
    budget_adjust_direction,
    build_clarify_question,
    clear_trip_slots,
    extract_slots,
    first_missing,
    is_ack_only,
    is_budget_adjust,
    is_budget_underuse,
    is_delegating,
    is_itinerary_question,
    is_itinerary_recall,
    is_plan_revision,
    is_trip_reset,
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
    # 放手表达（「随便 / 你看着办」）时用常规默认补齐骨架，这是要**说给用户听**的说明，
    # 插在行程响应的 suggestions 首条。空串 = 本轮不是放手表达。
    delegation_note: str
    # 行程内迭代：这一句是**对上一版行程的修改**（「第 3 天太紧凑了」）。非空时
    # 生成阶段会拿到 `previous_itinerary`，在原稿上改而不是从零重排。
    plan_revision: str
    # 上一版行程稿（`Itinerary.model_dump`），只在 `plan_revision` 非空时有意义。
    previous_itinerary: dict[str, Any]
    # 「把行程再给我看看」时要**原样重放**的那一版完整出稿响应
    # （`PlanResponse.model_dump`）。重放不重新生成 —— 用户要的是"刚才那份"。
    recall_plan: dict[str, Any]
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
    # 这一问借用的**会话目的地**（主体自己不带地点时）。用途：检索作用域、标题前缀、
    # 提示词里的上下文 —— 否则会话在聊「上海」，问「景点推荐呢」会被当成「没指明城市」。
    knowledge_scope: str
    # 行程细节问句（「那第三天上午安排什么」）的**确定性**答复：直接从会话里存着的上一版
    # 行程里取第 N 天拼出来，不调模型、也不重排整份稿。非空时 `knowledge_node` **短路**，
    # 直接把答案交给 `output_node` 装配成 answer 响应。
    itinerary_answer: str
    # 旁路 3：判定为寒暄 / 闲话 / 元问题时的意图与模型给出的引导语
    social_intent: SocialIntent
    guide_reply: str
    # 「试着这样问」的示例提问：模型在同一次寒暄调用里写的（见 `app/guide.py`）。
    # 为空 = 退回 i18n 定稿 `gd.starters`。
    guide_starters: list[str]
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
    # 生成阶段的失败原因（`generate_node` 捕获 GenerationError / LLMError 后写这里）。
    # **必须注册成通道** —— 否则 LangGraph 会把它丢掉，`output_node` 拿不到错误、
    # 又拿不到 draft，一路走到 `state["draft"]` 抛 KeyError，整条链路以
    # 「链路执行失败：KeyError: 'draft'」收场（真 bug 2026-10-06 英文实测）。
    error: str


def _visitor_nationality(state: PlanState, session_slots: dict[str, Any], settings) -> str:
    """这一问的**游客来源国**：本句 > 会话历史（与槽位优先级一致）。

    常识旁路不经过 `clarify_node`，`state["slots"]` 可能压根没被填过 —— 所以这里自己
    抽一次本句（确定性正则，毫秒级）。抽不到返回空串，而空串在提示词里的含义是
    **「不知道」→ 禁止类比**：宁可这一问没有类比，也不要按猜出来的国家比。
    """
    current = str((state.get("slots") or {}).get("nationality") or "").strip()
    if current:
        return current
    extracted: dict[str, Any] = {}
    try:
        extracted = extract_slots(state.get("message") or "", settings) or {}
    except Exception:
        extracted = {}
    return str(extracted.get("nationality") or session_slots.get("nationality") or "").strip()


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
        # 上一轮追问的是哪几项（`Session.record` 在出答复时写入）。这一轮要拿它判断
        # 「用户刚刚是不是在回答我的问题」—— 见下面预算调整分支里的 `budget_fresh`。
        # 必须在任何 `update_slots` 之前读：`update_slots` 是合并语义，会把上一轮的值冲掉。
        prev_missing = set(session.missing_slots or [])
        extracted = extract_slots(message, settings)
        # 信息来源的优先级：**本句 > 表单 > 会话历史**。
        # 本句最优先是因为它最新、最明确；表单是用户在点发送前刚填的（也是这一轮的意图），
        # 但它是「面板」，可能停留着上一次的值；会话历史最旧，只补前两者没提到的部分。
        overrides = slots_from_form(state.get("slot_overrides") or {}, settings)
        # 撤销当前行程（「算了，先问下签证」「不去了，改去成都」）：先把**会话里**的行程
        # 槽位清掉再并本句，否则旧行程的天数 / 预算会被当残留槽位带进新诉求 —— 真 bug
        # （2026-10-06 实测）：「西安四天」后说「算了，先问下签证」，出了一份「西安 4 日
        # 行程（含签证清单）」。表单不清：它是用户**这一轮**刚填的意图。与放手有交集时
        # 让放手优先 —— 「算了，就按你说的办」是放手，不是撤销（见 `slots.is_trip_reset`）。
        if is_trip_reset(message) and not is_delegating(message):
            # 必须**覆盖**（不是合并）—— `update_slots` 是合并语义，清空后旧键仍在
            # （见 `SessionStore.set_slots`）。
            session = deps.sessions.set_slots(
                state["session_id"], clear_trip_slots(session.slots)
            )
        prior = merge_slots(session.slots, overrides)

        # 会话承接：追问句自己**不含主体**（「那要预约吗」「开放时间呢」）。
        # 先把它接到上一轮的事实主体上，再判意图 —— 否则这三道闸门都在对着一句
        # 没有主语的话做判断，结果就是回一句四连问（用户报的「没有对话记忆」）。
        # 这里刻意只用本句抽出的槽位：意图判定看的是**这句话在说什么**，
        # 表单里留着的目的地不该让一句追问不再承接上一轮主体。
        hint_subject, hint_place = carry_over_subject(message, settings, session, extracted)
        carried = bool(hint_subject)

        # 「把之前的行程再给我看看」—— **重放**已出稿的那一版（不重新生成、也不追问）。
        # 真 bug（2026-10-06 长会话 E1）：这句被当成缺槽请求回四连问；而在槽位齐时它还会
        # 落进生成节点**重排一份**（用户要的是"刚才那份"，重排既浪费一次调用、内容也变了）。
        # 重放优先于下面所有分支：它既不是改稿，也不是问事实，更不是缺槽。
        if is_itinerary_recall(message):
            stored = dict(session.last_plan or {})
            want = str(extracted.get("destination") or prior.get("destination") or "").strip()
            have = str((stored.get("itinerary") or {}).get("destination") or "").strip()
            # 点名的目的地与存档对不上（「上海的行程再给我看看」，存档是北京）→ 不重放，
            # 如实说没有；含糊时（没点名 / 存档没写目的地）按「就是它」处理。
            if stored and (not want or not have or want == have or want in have or have in want):
                deps.sessions.update_slots(state["session_id"], prior)
                return {
                    "decision": "recall",
                    "slots": prior,
                    "missing_slots": [],
                    "recall_plan": stored,
                }
            deps.sessions.update_slots(state["session_id"], prior)
            return {
                "decision": "knowledge",
                "slots": prior,
                "missing_slots": [],
                "knowledge_intent": KnowledgeIntent(
                    subject="", matched="", question_zh=message, source="recall"
                ),
                "knowledge_hits": [],
                "itinerary_answer": t("clarify.no_plan_yet", output_language(state)),
                "knowledge_scope": str(prior.get("destination") or ""),
            }

        # 行程细节**问句**：「那第三天上午安排什么」「第一天几点开始比较合适」
        # 「What is planned for day 3 morning」—— 命中「第 N 天」的问句会被下面那条**改稿**
        # 判据误当成「要改稿」，于是重排整份行程：既没回答问题，又白跑一次生成
        # （真 bug 2026-10-06 长会话 B1/B5）。有已出稿的行程就**直接从稿子里答**
        # （确定性、不调模型、不重排）；没有稿子则照旧往下走。
        if is_itinerary_question(message) and (session.last_itinerary or {}).get("days"):
            ans = answer_from_itinerary(message, session.last_itinerary, output_language(state))
            if ans:
                deps.sessions.update_slots(state["session_id"], prior)
                return {
                    "decision": "knowledge",
                    "slots": prior,
                    "missing_slots": [],
                    "knowledge_intent": KnowledgeIntent(
                        subject="", matched="", question_zh=message, source="itinerary"
                    ),
                    "knowledge_hits": [],
                    "itinerary_answer": ans,
                    "knowledge_scope": str(prior.get("destination") or ""),
                }

        # 行程内迭代（市场对标，2026-10-06）：「第 3 天太紧凑了」「把第 2 天换成博物馆」
        # 「住宿换成便宜点的」「第 2 天下雨有备选吗」—— 这些是**改当前行程**，不是给新槽位，
        # 也不是问事实。旧行为一律掉进缺槽追问（M2/M4/M5/M10 全中：把已答过的预算/人数
        # 重刷一遍，甚至把「第 2 天下雨」判成查天气、主体切成「第 2 天」）。
        # 只在会话**确有行程骨架**（目的地 + 天数）时改道；没有行程可改时退回常规追问。
        # 必须排在实时/常识闸门**之前**：带「第 N 天」锚点的句子属于行程，不属于事实检索。
        if (
            is_plan_revision(message)
            and not is_trip_reset(message)
            and prior.get("destination")
            and prior.get("days")
        ):
            # 迭代时缺的骨架项（人数/预算）用常规默认补齐 —— 用户是在改稿，不是在
            # 逐项报数；把他已经交代过的东西再问一遍是交互上最刺眼的错（同「放手」语义）。
            revised = apply_delegation_defaults(dict(prior))
            revised["plan_revision"] = (message or "").strip()
            deps.sessions.update_slots(state["session_id"], revised)
            return {
                "decision": "continue",
                "slots": revised,
                "missing_slots": [],
                "plan_revision": (message or "").strip(),
                "previous_itinerary": dict(session.last_itinerary or {}),
            }

        # 先判意图，再做槽位体检。「天安门什么时候升旗」不是缺 4 个槽位，
        # 它压根不是排行程的请求 —— 顺序反了就会问出 4 个方向全错的问题。
        #
        # 顺序也有讲究：实时在前、常识在后。「西湖什么时候开门」两边都像，
        # 但它要的是**当日状态**（会过期），归实时更准。
        intent = detect_realtime_intent(
            message, settings, extracted, hint_subject=hint_subject or "", hint_place=hint_place or ""
        )
        if intent is not None:
            # 事实旁路**不并入本句抽出的槽位**：这一句里的「目的地」是**问句的主体**，
            # 不是用户要去的城市。真 bug（2026-10-06 变卦探针实测，跨城必现）：
            # 会话在聊「北京 5 天的行程」，用户插一句「外滩几点关门」——
            # 旧行为把行程目的地改成了**上海**（同城时则是 destination_surface 被改，
            # 追问文案变成「已记下目的地 故宫」）。用户回到行程时发现目的地已经变了。
            # 事实主体由 `remember_fact` 单独记（追问承接走它），与行程槽位互不干扰。
            deps.sessions.update_slots(state["session_id"], prior)
            return {
                "decision": "realtime",
                "slots": prior,
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
            # 同 realtime 旁路：问句里的地名是**主体**，不是行程目的地，不并入行程槽位。
            deps.sessions.update_slots(state["session_id"], prior)
            return {
                "decision": "knowledge",
                "slots": prior,
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
            # 寒暄句更不该动行程槽位（「谢谢」「你好」里没有地名，保持既有会话状态）。
            deps.sessions.update_slots(state["session_id"], prior)
            return {
                "decision": "social",
                "slots": prior,
                "missing_slots": [],
                "social_intent": social,
                "carry_over": False,
                "hint_subject": "",
            }

        merged = merge_slots(prior, extracted)
        hints = scene_hints(message, settings)
        required = required_slots_for(hints, settings)

        # ---- 交互流畅度（2026-10-06 用户要求）：这一句**没给出任何槽位信息**时的两条出路。
        # 旧行为是一律回同一个四连问 —— 用户说「随便」「好的」都被原样问四句：既不像对话，
        # 也把已经交代过的东西再问一遍。两种输入要分开对待：
        #   · 放手表达（随便/都行/你看着办）→ 用常规默认补齐骨架，让用户**改一项**而不是填四项；
        #   · 纯应答（好的/嗯/可以）→ 收窄成**一个问题**，把节奏拉回一问一答。
        delegating = is_delegating(message)
        ack_only = is_ack_only(message) and not extracted
        if delegating or ack_only:
            if delegating:
                merged = apply_delegation_defaults(merged)
            miss = missing_slots(merged, required)
            if ack_only:
                miss = first_missing(miss)
            # 放手表达不砍 missing：目的地是唯一不能替用户猜的槽位（猜错整份行程跑偏），
            # 缺它就得问 —— 但此时其余三项已被默认补上，通常只剩它一个。
            if miss:
                deps.sessions.update_slots(state["session_id"], merged)
                return {
                    "decision": "clarify",
                    "slots": merged,
                    "missing_slots": miss,
                    "clarify_question": build_clarify_question(miss, merged, language=output_language(state)),
                }
            # 放手且不再缺槽 → 直接出方案；默认值写进 suggestions 首条，用户看得到也改得动。
            note = ""
            if delegating:
                note = t(
                    "clarify.delegate_note",
                    output_language(state),
                    days=merged.get("days"),
                    budget=int(merged.get("budget") or 0),
                    party=merged.get("party_size") or "",
                )
            deps.sessions.update_slots(state["session_id"], merged)
            return {
                "decision": "continue",
                "slots": merged,
                "missing_slots": [],
                "delegation_note": note,
            }

        # 模糊的预算调整诉求（「太贵了，能便宜点吗」「开销太少，多花点」）：想改、但没给数。
        # 旧行为是槽位齐全 → 直接重出一份稿，模型自己**编一个用户没说过的数字**
        # （实测：原预算 8000，重出成 1128 元）。那不是"调整"，是替用户拍了板。
        # 追问一句目标预算，既省一轮来回，也不会把价压到用户没要求的位置。
        #
        # 2026-10-09 用户实测「提高开销反被改成穷游」（2260 → 2190 → 2160，标题滑成"穷游"），
        # 暴露两个必须分开处理的形态：
        #   ① **方向**：「太贵了」问"想控制在多少以内"，「开销太少」问"想提到多少" ——
        #      旧实现只有降向文案（词表也只有"便宜 / 省"这一半），对提高方向是**反着问**。
        #   ② **已给预算 + 抱怨没花完**（「我有 11111 元怎么花不完」）：他不缺数字，
        #      再问一句就是明知故问 —— 直接带原稿把开销抬上去。
        #
        # ② 必须放在 `"budget" not in extracted` **之外**：这类句子通常**带着**那个数
        # （11111 会被槽位抽取器抓进 `extracted`），塞进下层条件就会被挡回普通生成、
        # 又"重排一份更省的" —— 正是本次 bug 的复现路径。
        #
        # 2026-10-09 第三个形态（用户实测）：「提高开销」→ 追问卡里填好预算 → 点
        # **「按填写的信息重发本句」** → 又看到同一句追问，点几次都是它（死循环）。
        # 原因是判据只看「**原话里**有没有数」：重发时原话仍是「提高开销」（前端刻意保留
        # 原话，只把表单值一起带上），于是在这一步永远判「没给数」→ 再问一次 →
        # 用户再点再重发…… 实测连续 3 次返回同一个 clarify。
        #
        # `budget_fresh` = 「用户**这一轮**确实把数字交上来了」，两条来源：
        #   - 本句自己说了数（本句 > 表单，见上面的优先级注释）；
        #   - 表单里填了数，**且上一轮追问的正是预算** ← 这个附加条件是必须的：
        #     「行程信息卡」是**常驻**的，上一次填过的预算会一直留在表单里。只看
        #     「表单里有预算」的话，常驻旧值会冒充「他已经给了数」，用户说「提高开销」时
        #     系统会直接拿旧值改稿、不再问他到底想提到多少 —— 又一个反着来的行为。
        #     反过来，少了这一支，重发本句就永远填不上这个槽（就是上面那个死循环）。
        budget_fresh = ("budget" in extracted) or (
            "budget" in overrides and "budget" in prev_missing
        )
        if (is_budget_adjust(message) and merged.get("budget")
                and (budget_fresh or is_budget_underuse(message))
                and session.last_itinerary):
            # 数字到手了（本句说的 / 刚在追问卡里填的 / 抱怨没花完但预算本就有了）
            # → 不再问，直接带原稿把这一版改出来。
            revised = apply_delegation_defaults(dict(merged))
            revised["plan_revision"] = (message or "").strip()
            deps.sessions.update_slots(state["session_id"], revised)
            return {
                "decision": "continue",
                "slots": revised,
                "missing_slots": [],
                "plan_revision": (message or "").strip(),
                "previous_itinerary": dict(session.last_itinerary or {}),
            }

        if (is_budget_adjust(message) and merged.get("budget")
                and not budget_fresh):
            # 想改、但确实没给数 → 追问一句目标预算（按方向选文案）
            question_key = (
                "clarify.budget_raise"
                if budget_adjust_direction(message) == "raise"
                else "clarify.budget_adjust"
            )
            deps.sessions.update_slots(state["session_id"], merged)
            return {
                "decision": "clarify",
                "slots": merged,
                "missing_slots": ["budget"],
                "clarify_question": t(question_key, output_language(state)),
            }

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
        # 「重看行程」不经过分类/检索/生成 —— 直接把存档的那一版重放（见 output_node）。
        if decision == "recall":
            return "output"
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
        # 行程细节问句：答案已由 `clarify_node` 从**已出稿的行程**里确定性地取好
        # （见 `slots.answer_from_itinerary`）。这里直接放行，不检索、不调模型 ——
        # 问的是"你自己刚排的那份稿"，去知识库检索反而是离题的。
        if state.get("itinerary_answer"):
            return {
                "knowledge_hits": [],
                "knowledge_draft": AnswerDraft(answer=state["itinerary_answer"]),
                "knowledge_scope": state.get("knowledge_scope") or "",
            }
        session = deps.sessions.get(state["session_id"])
        # 会话里记着的**行程目的地** —— 这一问自己没带地点时借它当检索作用域与上下文。
        # 真 bug（2026-10-06 变卦探针实测）：会话在聊「上海 5 天」，用户问「景点推荐呢」，
        # 检索与提示词都拿不到地点，于是答复「问题没有指明具体地点或城市」、hits=0 ——
        # 用户明明刚说了上海。
        session_slots = getattr(session, "slots", None) or {}
        session_dest = str(session_slots.get("destination") or "").strip()
        # 游客来源国：决定这一问要不要做中外类比（文化 / 饮食类问句尤其需要）。
        nationality = _visitor_nationality(state, session_slots, settings)
        # **记住它**：用户不会每一句都重报国籍。常识旁路不经过槽位合并（`clarify_node`），
        # 所以「我是德国人，怎么点菜」说完就得在这里落库 —— 否则下一句「那要给小费吗」
        # 又要用户重新自我介绍一遍，类比也跟着断掉。
        if nationality and str(session_slots.get("nationality") or "").strip() != nationality:
            deps.sessions.update_slots(state["session_id"], {"nationality": nationality})
        # 主体**自带地点**时以它为准（「上海有什么好玩的」→ 上海），不借用会话地点，
        # 免得会话在上海、问「西湖有多大」时被套上错误的作用域。
        scope_dest = "" if subject_place(settings, intent.subject) else session_dest
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
            scope_destination=scope_dest,
        )
        deps.sessions.remember_fact(
            state["session_id"],
            subject=intent.subject,
            # 有指代时**所在地就是那个指代对象**：再下一句「那里有什么好吃的」靠它回到
            # 「上海临港」。问的是「华东师范大学」时，词典查不出它的所在地，只有指代解得出来。
            place=referent or subject_place(settings, intent.subject),
            terms=_merged_terms(state, intent.subject),
        )
        context = _carry_context(state, referent)
        if scope_dest:
            # 模型看不到会话槽位，得显式告诉它「这一问在聊哪儿」—— 否则它会说
            # 「没指明地点」。只在主体不带地点时加，避免与主体自己的地点打架。
            scope_line = (
                f"本次会话正在规划「{scope_dest}」的行程，这一问没有另指地点 —— "
                f"凡涉及地点都按「{scope_dest}」理解。"
            )
            context = f"{context} {scope_line}".strip() if context else scope_line
        draft = answer_question(
            settings=settings,
            llm=deps.llm,
            intent=intent,
            hits=hits,
            message=state["message"],
            context=context,
            language=output_language(state),
            nationality=nationality,
        )
        return {"knowledge_hits": hits, "knowledge_draft": draft, "knowledge_scope": scope_dest}

    # ------------------------------------------------------------- guide（旁路）
    def guide_node(state: PlanState) -> PlanState:
        """寒暄旁路：不检索、不调工具，只回一句把人引到旅行话题的话。

        它不产生任何可核实的事实断言 —— 所以没有时效闸门、也没有「未经核实」标注。
        模型不可用时 `compose_guide` 返回空串，`output` 会退回 i18n 里的定稿话术；
        绝不能因为模型挂掉就退化成那四连问（降级，但不是降级成错误的东西）。

        **三类输入不调模型**（前两类都在下面短路，第三类在 `guide.py` 里）：
        `recall`（记忆类元问题）、`greeting` / `thanks` / `farewell` / `cancel` / `chitchat`
        （封闭式套话 / 纯笑声闲话）。
        """
        intent = state["social_intent"]
        language = output_language(state)
        # 「我们刚才聊了什么」这类**记忆类元问题**不调模型：它的唯一正确来源就是我们
        # 自己记的那几轮。交给模型只有两种结果 —— 看不到历史时编，看得到历史时也可能
        # 答歪；而这句问话是用户**在验证系统记不记得住**，答错比不答更糟。
        if intent.kind == "recall":
            return {"guide_reply": _recall_reply(state["session_id"], language)}
        # 「你好 / 谢谢 / 再见 / 算了 / 哈哈哈」这类**封闭式套话 / 纯笑声**同理不调模型：
        # 回复与用户具体说了什么无关，定稿就是答案本身。实测（2026-10-09）模型写的引导语与
        # 定稿几乎逐字相同，却出现过 11.1s 的长尾 —— 一句「你好」不该等大模型。
        scripted = scripted_reply(intent, language)
        if scripted is not None:
            return {"guide_reply": scripted}
        # 只剩 `meta`（你是谁 / 能做什么）—— 七类里唯一的真实问句，值得一次润色。
        draft = compose_guide(
            settings=settings,
            llm=deps.llm,
            intent=intent,
            message=state["message"],
            language=language,
        )
        if draft is None:
            # 模型没给 / 挂了 → 空 patch，`output` 退 i18n 定稿（reply 与 starters 都是）。
            return {}
        return {"guide_reply": draft.reply, "guide_starters": list(draft.starters or [])}

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
                previous_itinerary=state.get("previous_itinerary") or None,
                revision_note=state.get("plan_revision") or "",
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
            itinerary=outcome.itinerary,
            suggestions=state["draft"].suggestions,
            # 校验重建 draft 时**必须**把模型写的 followups 带上 —— 少了它，建议栏会
            # 悄悄退回模板（真 bug 2026-10-09 实测：模型建议写到了 draft，却到不了 output）。
            followups=list(getattr(state["draft"], "followups", []) or []),
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
                scope=state.get("knowledge_scope") or "",
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
                starters_override=list(state.get("guide_starters") or []),
                # 示例提问兜底按轮次轮换窗口（模型没给 starters 的套话类才用得到）——
                # 同一会话连点两次「你好」，示例提问不再一字不差。见 `app/guide.py:starters`。
                starters_offset=deps.sessions.get(session_id).turn,
            )
        elif decision == "clarify" and state.get("draft") is None:
            response = ClarifyResponse(
                question=state.get("clarify_question") or "请补充关键信息。",
                missing_slots=state.get("missing_slots") or [],
            )
        elif decision == "recall" and state.get("recall_plan"):
            # 「把行程再给我看看」：原样重放上一版出稿的卡片（不重新生成、不调模型）。
            # 只在最前面加一句「这是最近这一版」——用户才知道自己看到的是哪一版。
            replayed = PlanResponse.model_validate(state["recall_plan"])
            title = replayed.itinerary.title or replayed.itinerary.destination or ""
            note = t("clarify.recall_note", output_language(state), v=title)
            response = replayed.model_copy(
                update={"suggestions": [note] + list(replayed.suggestions)}
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
                # 放手表达时，先把「我按什么默认来安排」说在前面 —— 用户才能知道该改哪一项。
                suggestions = list(output.suggestions)
                note = state.get("delegation_note")
                if note:
                    suggestions = [note] + suggestions
                # 行程内迭代：把「这次改了什么」说在最前 —— 用户据此确认这版相对上一版的变化。
                revision = state.get("plan_revision")
                if revision:
                    suggestions = [
                        t("clarify.revision_note", output_language(state), v=revision)
                    ] + suggestions
                # 记下这一版稿，供下一句「改第 N 天」在原稿上迭代（见 `Session.last_itinerary`）。
                deps.sessions.remember_itinerary(
                    session_id, output.itinerary.model_dump(mode="json")
                )
                response = PlanResponse(
                    route=route,
                    itinerary=output.itinerary,
                    suggestions=suggestions,
                    checklist=checklist,
                    citations=citations,
                    validation=report or _empty_report(),
                )
                # 记下**完整响应**，供「行程再给我看看」原样重放（见 `Session.last_plan`）。
                deps.sessions.remember_plan(session_id, response.model_dump(mode="json"))
                # 行程出稿后，把「上一轮的事实主体」推进到这一版的**目的地**。
                # 省略式追问（「那要预约吗」）承接的是「最近在聊的那个地方」；出稿不推进
                # 话题，它就会接到**更早**那一轮的事实主体上。真 bug（2026-10-09 多语言
                # 对话探针实测）：会话「西湖有多大」→「北京玩 3 天」出稿 →「那要预约吗」
                # 被答成了**西湖**（主体停在两轮之前）。
                plan_dest = (getattr(response.itinerary, "destination", "") or "").strip()
                if not plan_dest:
                    plan_dest = str(
                        deps.sessions.get(session_id).slots.get("destination") or ""
                    ).strip()
                if plan_dest:
                    # terms 也一并重置：留着上一轮（西湖）的实词会把检索拉偏。
                    deps.sessions.remember_fact(
                        session_id, subject=plan_dest, place=plan_dest, terms=[plan_dest]
                    )
                # 让会话槽位的天数跟着**稿子**走。迭代里「去掉第 4 天」只改稿、不改槽位，
                # 不跟的话下一句「再加一天」会按旧的 5 天算（真 bug 2026-10-06 长会话 E2）。
                # `date_range` 只在它是「N 天」这种时长写法时才跟着改（日期区间不动）。
                n_days = len(response.itinerary.days)
                session_now = deps.sessions.get(session_id)
                if n_days and session_now.slots.get("days") != n_days:
                    patch: dict[str, Any] = {"days": n_days}
                    if re.fullmatch(r"\d+\s*天", str(session_now.slots.get("date_range") or "")):
                        patch["date_range"] = f"{n_days} 天"
                    deps.sessions.update_slots(session_id, patch)

        # 关联问题推荐（市场对标）：答完给 2–4 条「接着可以问」的可点建议。
        # **模型为主、模板兜底**（2026-10-09 用户反馈「改的自由点儿，别死板」）：
        # plan / answer 两条路本来就在调模型，于是让**同一次调用**顺手写建议（零额外
        # 延迟），跟着这一轮的真实内容走；模型没给 / 格式不合法 / realtime（不调模型）
        # 才退回 `app/followups.py` 的定稿模板。guide 的「试着这样问」在 guide.py 同理。
        nq_decision = {
            "continue": "plan",
            "recall": "plan",
            "realtime": "realtime",
            "knowledge": "answer",
        }.get(str(decision))
        if nq_decision and hasattr(response, "next_questions"):
            # 模型写的建议：plan 取 `state["draft"].followups`；answer 那份已由
            # `build_answer_response` 落到 `response.next_questions`。先洗净：去空 /
            # 去重 / 去复读用户原话（`clean_chips`）。
            if nq_decision == "plan":
                model_chips = list(getattr(state.get("draft"), "followups", []) or [])
            else:
                model_chips = list(getattr(response, "next_questions", []) or [])
            nq = clean_chips(
                model_chips,
                avoid=[state.get("message") or ""],
                limit=3,
            )
            if nq:
                # 模型给了 → 直接用它，不再叠模板（叠上去两边会打架、还盖掉最贴题的那条）。
                response = response.model_copy(update={"next_questions": nq})
            else:
                # `subject` 用来认话题（饮食 / 交通 / 支付…），认得出就整套换成同话题的建议
                # —— 少了它，饮食话题连问四轮建议栏一字不差（真 bug 2026-10-06 科目探针实测）。
                nq_subject = ""
                nq_matched = ""
                if state.get("knowledge_intent") is not None:
                    nq_subject = state["knowledge_intent"].subject
                    nq_matched = getattr(state["knowledge_intent"], "matched", "") or ""
                elif state.get("realtime_intent") is not None:
                    nq_subject = state["realtime_intent"].subject
                    nq_matched = getattr(state["realtime_intent"], "matched", "") or ""
                # 命中片段也要参与认话题：找店句「哪里能吃到本地人常去的**馆子**」的主体
                # 是定语（「本地人常」），真正的话题词在命中片段里 —— 只看主体会认不出饮食。
                nq_subject = f"{nq_subject} {nq_matched}".strip()
                # 天数取**这一版稿子**的天数，不取 `state["slots"]`（2026-10-09 用户反馈
                # 「可以接着问模块是死的」）：改稿分支（「再加一天」）里 state 的槽位可能没有
                # days，`_context` 就退回写死的 3 —— 实测 5 天的稿子建议栏写着「把第 3 天…」，
                # 跟眼前的稿子对不上，看起来就像模块没在跟着稿子走。
                nq_slots = dict(state.get("slots") or {})
                n_plan_days = (
                    len(response.itinerary.days) if hasattr(response, "itinerary") else 0
                )
                if n_plan_days:
                    nq_slots["days"] = n_plan_days
                nq = next_questions(
                    decision=nq_decision,
                    language=output_language(state),
                    slots=nq_slots,
                    subject=nq_subject,
                    # 刚做过的那条不再原样出现（用户点完还看到同一条，就像点了没反应）。
                    avoid=[state.get("message") or ""],
                    # 轮次当轮换偏移，让相邻两版的建议栏不再一字不差。
                    offset=deps.sessions.get(session_id).turn,
                )
                if nq:
                    response = response.model_copy(update={"next_questions": nq})

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
