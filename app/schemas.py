"""全部 Pydantic 模型。四个「必备模型」在本文件上半部分，字段与任务书一一对应。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .i18n import SUPPORTED as SUPPORTED_LANGUAGES

Layer = Literal["general", "scene", "realtime"]
CheckStatus = Literal["pass", "warn", "repair", "block"]
Severity = Literal["none", "warn", "repair", "block"]
FusionPolicy = Literal["weighted_union", "priority_override"]


# --------------------------------------------------------------------------
# 1. SceneClassificationResult
# --------------------------------------------------------------------------
class SceneLabel(BaseModel):
    scene_id: str
    confidence: float = Field(ge=0.0, le=1.0)


class SceneClassificationResult(BaseModel):
    request_id: str
    labels: list[SceneLabel]
    primary_scene: str
    slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    clarify_question: str | None = None
    rewritten_query: str
    classifier_version: str

    def confidences(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for lb in self.labels:
            out[lb.scene_id] = max(out.get(lb.scene_id, 0.0), lb.confidence)
        return out

    def top_confidence(self) -> float:
        return max((lb.confidence for lb in self.labels), default=0.0)


# --------------------------------------------------------------------------
# 2. RouteConfig
# --------------------------------------------------------------------------
class PromptRefs(BaseModel):
    base: str
    overlays: list[str] = Field(default_factory=list)


class RouteConfig(BaseModel):
    route_id: str
    scenes: list[str]
    fusion_policy: FusionPolicy
    prompt_refs: PromptRefs
    knowledge_scopes: list[str]
    tool_allowlist: list[str]
    output_schema_id: str
    guardrail_ids: list[str]
    planner_mode: Literal["plan_execute"] = "plan_execute"
    max_repair_rounds: int = 1
    version_pin: str
    # 输出语言跟用户（zh/en/ja/ko）。注意：**检索查询恒为中文**，知识库是中文语料，
    # 这里只控制生成出来的正文/清单用什么语言写。判定见 app/i18n.py。
    output_language: str = "zh"

    @field_validator("max_repair_rounds")
    @classmethod
    def _pin_repair_rounds(cls, v: int) -> int:
        if v != 1:
            raise ValueError("本期固定 max_repair_rounds = 1，不做第 2 轮回环")
        return v


# --------------------------------------------------------------------------
# 3. RetrievedContext
# --------------------------------------------------------------------------
class RetrievedChunk(BaseModel):
    chunk_id: str
    layer: Layer
    score: float
    text: str
    source: str
    fresh_until: date | None = None
    citation_key: str


class RetrievedContext(BaseModel):
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    layer_quota: dict[str, float] = Field(default_factory=dict)
    dropped_stale: int = 0
    retriever_version: str

    def keys(self) -> set[str]:
        return {c.citation_key for c in self.chunks}

    def by_key(self) -> dict[str, RetrievedChunk]:
        return {c.citation_key: c for c in self.chunks}


# --------------------------------------------------------------------------
# 4. ValidationReport
# --------------------------------------------------------------------------
class ValidationCheck(BaseModel):
    name: str
    status: CheckStatus
    # severity 是该检查的**配置上限**（稳定属性），status 是本次运行的**实际结果**。
    # 例：budget 的 severity = repair（最坏可以要求回炉一轮），本次 status = pass。
    severity: Severity
    findings: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    passed: bool
    round: int = 0
    checks: list[ValidationCheck] = Field(default_factory=list)
    repair_actions: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    citations_verified: list[str] = Field(default_factory=list)
    degraded: bool = False

    def worst_status(self) -> CheckStatus:
        order = {"pass": 0, "warn": 1, "repair": 2, "block": 3}
        worst: CheckStatus = "pass"
        for c in self.checks:
            if order[c.status] > order[worst]:
                worst = c.status
        return worst


# --------------------------------------------------------------------------
# 生成产物（LLM 结构化输出目标）
# --------------------------------------------------------------------------
class Activity(BaseModel):
    time: str = Field(description="HH:MM，可用 '上午'/'下午' 等粗粒度写法")
    name: str
    detail: str = ""
    cost: float | None = Field(default=None, description="单人费用，CNY；无法确定填 null")
    citation_key: str | None = Field(
        default=None, description="支撑该条事实的 chunk citation_key；没有引用必须填 null"
    )


class DayPlan(BaseModel):
    day: int
    date: str | None = None
    theme: str = ""
    area: str = Field(default="", description="当日主要活动区域，用于一致性检查")
    activities: list[Activity] = Field(default_factory=list)


class BudgetLine(BaseModel):
    name: str
    amount: float
    category: str = ""


class BudgetSummary(BaseModel):
    currency: str = "CNY"
    lines: list[BudgetLine] = Field(default_factory=list)
    total: float = 0.0
    user_budget: float | None = None
    buffer_ratio: float = 0.1
    within_budget: bool = True


class Itinerary(BaseModel):
    title: str
    destination: str = ""
    party: str = ""
    date_range: str = ""
    scenes: list[str] = Field(default_factory=list)
    days: list[DayPlan] = Field(default_factory=list)
    budget: BudgetSummary | None = None
    notes: list[str] = Field(default_factory=list)
    disclaimers: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    citation_key: str
    chunk_id: str | None = None
    source: str = ""
    source_url: str | None = None
    effective_date: date | None = None
    fresh_until: date | None = None
    origin: Literal["knowledge_base", "tool"] = "knowledge_base"


class GeneratorOutput(BaseModel):
    """生成节点的结构化输出契约；citations 由程序从 citation_key 反查组装，不交给模型编。"""

    itinerary: Itinerary
    suggestions: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 接口模型
# --------------------------------------------------------------------------
class PlanRequest(BaseModel):
    session_id: str
    message: str
    # 可选：界面上「行程信息卡」里填的结构化信息（目的地 / 天数 / 预算 / 同行人）。
    #
    # 存在的理由（2026-10-05 用户提出）：口语的写法是枚举不完的，同一个意思可以说成
    # 「只有我一个人 / 就我一个成年人 / 1 位成人」，靠加正则只是换一种说法再漏一次。
    # 表单不经过任何抽取，填了就直接是槽位 —— 它给的是一条**确定能通**的路：
    # 用户不必猜系统听得懂哪一句。正规化见 `app/slots.py::slots_from_form`。
    #
    # 优先级：**本句 > 表单 > 会话历史**（见 `graph.clarify_node`）。
    slot_overrides: dict[str, Any] = Field(default_factory=dict)
    # 可选：强制输出语言（zh/en/ja/ko）。不传就按 message 的字符集自动判定。
    # 用途是**开发与联调**：非中文请求也想直接读中文输出时，不用改输入、不用翻页。
    output_language: str | None = None

    @field_validator("output_language")
    @classmethod
    def _check_language(cls, v: str | None) -> str | None:
        if v is None:
            return None
        code = v.strip().lower()
        if code not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"output_language 只支持 {'/'.join(SUPPORTED_LANGUAGES)}，收到 {v!r}"
            )
        return code


class RealtimeChannel(BaseModel):
    """一处**权威入口 / 复核入口**：在哪儿查、查什么字段。

    两处在用：实时事实的官方入口（`RealtimeResponse.channels`）与常识作答的复核入口
    （`AnswerResponse.verify`）。定义放在这里是因为后者要先引用它。

    `name` / `url` 是**引用**，不随用户语言翻译（与 citations 同一口径）；
    名字里的机构全称就是它可信的理由 —— 所以 `name` 必须如实描述点开落在哪儿，
    不许承诺 `url` 打不开的东西（真 bug：名字写《…观看指南》、url 写站点根）。
    """

    name: str
    url: str
    what: str = ""


class KnowledgeHit(BaseModel):
    """一条**与问题主体相关**的知识库条目（scene / general 层）。

    带 `source_url` 是为了让用户能点回原文 —— 只给一个 `chunk_id` 等于给了个编号，
    用户没法自己核对。
    """

    chunk_id: str
    text: str
    source: str = ""
    source_url: str | None = None
    destination: str = ""


class AnswerDraft(BaseModel):
    """常识作答的**模型输出契约**（提示词见 `prompts/answer.md`）。

    只有三个字段，因为这一层的任务只有一个：答那件事，或者承认答不出。
    `unknown_reason` 与 `answer` 互斥 —— 强行让模型「总要给点什么」是幻觉的来源。
    """

    answer: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
    unknown_reason: str = ""


class AnswerResponse(BaseModel):
    """静态事实问询的响应（「西湖有多大」）。**不是**行程：没有行程、没有预算、没有槽位追问。

    存在的理由：「西湖有多大」不含任何时间 / 开放 / 天气 / 交通词，实时闸门不接管，
    于是掉回行程链路的槽位体检，被回以「目的地？天数？预算？人数？」—— 四问全错方向。

    与 `RealtimeResponse` 分开是有意的：这里的答案**不会过期**（西湖的面积不随日期变），
    所以没有时效闸门、没有 `fresh_until`。但**作答口径**与实时链路相反：

    - 实时链路不生成任何句子（时刻会变，模型必然错，只摆来源）；
    - 这一层**允许用通用常识作答**，因为面积 / 海拔 / 历史年份这类知识稳定、
      且没有任何本地资料库能覆盖这个长尾。代价是必须如实标注「未经本知识库核实」，
      并给出**复核入口**（`verify`）—— 让用户能自己核一遍，比替他保证更诚实。
    - 时效性信息（时刻 / 票价 / 开放状态 / 天气）在这一层**拒答**，只指回实时核实：
      那是防幻觉的双保险，闸门漏掉的句子也不会被编出一个值。
    """

    type: Literal["answer"] = "answer"
    subject: str                        # 问的主体（用于**检索作用域**）：西湖 / 上海
    # 展示用的标签 = 主体 + 这一问的焦点（「上海博物馆」而不是「上海」）。
    # 与 `subject` 分开的理由：检索要的是能对得上语料的**地点/主体**，而卡片标题要的是
    # 用户看得懂的那一件事。合成一个字段，两者必然有一个是错的（真 bug：标题恒为
    # 「关于「上海」」，同一个城市的每次提问都长一样）。
    topic: str = ""
    message_echo: str = ""              # 用户原话
    question_zh: str = ""               # 规范化的中文检索词（检索恒中文）
    # 常识作答正文。空 = 本次没给答案（原因见下方，条文在 i18n 的 kn.unknown）。
    answer: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
    unknown_reason: str = ""            # answer 为空时说明为什么（用户可见文案，跟语言）
    # 两个区块标题也跟输出语言 —— 它们会直接显示给用户，不属于「引用」那一类。
    answer_title: str = ""              # 「关于「西湖」」
    verify_title: str = ""              # 「复核入口（点开自己核一遍）」
    # 知识库中与主体相关的条目。**不是答案**，只是同主题材料 —— 标题里写明这一点。
    hits: list[KnowledgeHit] = Field(default_factory=list)
    # 复核入口：模型答的常识未经本知识库核实，必须让用户能自己核一遍。
    verify: list[RealtimeChannel] = Field(default_factory=list)
    note: str = ""                      # 为什么没给行程（用户可见文案，跟语言）
    plan_hint: str = ""
    disclaimer: str = ""


class GuideDraft(BaseModel):
    """寒暄引导的**模型输出契约**（提示词见 `prompts/guide.md`）。

    只有一个字段，因为这一层的任务只有一个：回一句自然的话并邀请提问。
    它不复述事实、不给数字，所以没有 `confidence` / `unknown_reason` 那套。
    """

    reply: str = ""


class GuideResponse(BaseModel):
    """寒暄 / 闲话 / 元问题的响应（「你好」「谢谢」「你是谁」）。

    **不是**行程、也**不是**事实断言：不检索、不调工具、不追问槽位。
    存在的理由：「你好」曾被行程链路的槽位体检拦下，回了一句「想去的城市？玩几天？
    预算多少？一行几个人？」—— 四问全错。问候要的是一句引导，不是一张需求表单。

    与另两条旁路分开是有意的：这里既没有时效闸门（无可过期的事实），
    也不需要「未经核实」标注（本来就无可核实的事实）。
    """

    type: Literal["guide"] = "guide"
    kind: str = "greeting"              # greeting / thanks / farewell / meta
    message_echo: str = ""              # 用户原话
    reply: str = ""                     # 引导话术（跟输出语言）；空则用 i18n 定稿
    starters: list[str] = Field(default_factory=list)  # 建议提问（点击即发）
    note: str = ""                      # 为什么按引导处理（用户可见文案，跟语言）
    disclaimer: str = ""


class ClarifyResponse(BaseModel):
    type: Literal["clarify"] = "clarify"
    question: str
    missing_slots: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    type: Literal["plan"] = "plan"
    route: RouteConfig
    itinerary: Itinerary
    suggestions: list[str] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    validation: ValidationReport


class RealtimeFact(BaseModel):
    """一条**没过有效期**的实时事实。

    与 `Citation` 分开：引用只需要能对回原文（key + 来源 + 链接），而事实问询的答复
    必须把正文摆给用户看 —— 「查到了一条 06:06 的记录」这种回答等于没回答。
    """

    chunk_id: str
    text: str
    source: str = ""
    source_url: str | None = None
    effective_date: date | None = None
    fresh_until: date | None = None


class RealtimeResponse(BaseModel):
    """实时事实问询的响应。**不是**行程：没有 itinerary、没有预算、没有槽位追问。

    存在的理由：「天安门什么时候升旗」曾被行程链路的槽位体检拦下，回了一句
    「想去的城市或国家是哪里？玩几天？预算多少？一行几个人？」—— 四问全错。
    问事实只需要三件事：问的是什么、知识库里有没有没过期的答案、该去哪查。
    """

    type: Literal["realtime"] = "realtime"
    info_type: str                      # schedule / opening_hours / weather / transport
    label: str                          # 信息类型显示名（跟输出语言）
    subject: str                        # 天安门
    place: str | None = None            # 北京（能解析出来才有）
    message_echo: str = ""              # 用户原话
    question_zh: str = ""               # 规范化的中文检索式（检索恒中文）
    # 知识库里没过有效期的事实。空列表 = 本次没查到 → 必须走「去哪查」那条路，
    # 这一点要在响应里说清楚，不能让调用方以为「查到了但没写」。
    found: list[RealtimeFact] = Field(default_factory=list)
    # 实时层里已过期、被时效闸门挡掉的条数。>0 说明「不是知识库没有，是有但过期了」，
    # 这两种情况对用户的含义完全不同。
    expired_dropped: int = 0
    channels: list[RealtimeChannel] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)
    # 为什么没给行程、以及「要行程的话给我四个槽位」（用户可见文案，跟语言）
    note: str = ""
    plan_hint: str = ""
    disclaimer: str = ""


class DegradedResponse(BaseModel):
    type: Literal["degraded"] = "degraded"
    reason: str
    partial: dict[str, Any] = Field(default_factory=dict)
    validation: ValidationReport


PlanResponseT = ClarifyResponse | PlanResponse | RealtimeResponse | AnswerResponse | GuideResponse | DegradedResponse


# --------------------------------------------------------------------------
# 内部检索文档
# --------------------------------------------------------------------------
class Chunk(BaseModel):
    chunk_id: str
    layer: Layer
    scene: str = "general"
    destination: str = ""
    text: str
    source: str = ""
    source_url: str | None = None
    effective_date: date | None = None
    fresh_until: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def citation_key(self) -> str:
        return self.chunk_id


class ScoredChunk(BaseModel):
    chunk: Chunk
    score: float


class ToolResult(BaseModel):
    tool: str
    ok: bool
    citation_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    source_url: str | None = None
    effective_date: date | None = None
    error: str | None = None
    # 工具返回的逐条凭据。citation_key 指向「这次调用」，evidence 指向「调用里的每一条事实」。
    # 少了它，正文引用 tool:xxx:<chunk_id> 就解析不到来源 —— 会被事实校验判为不可溯源。
    evidence: list[dict[str, Any]] = Field(default_factory=list)


def utcnow() -> datetime:
    return datetime.now()
