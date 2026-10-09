"""寒暄引导：用一句话把用户引到「旅行」这个话题上。

它在这套链路里是**第三条旁路**，与另两条的分工

| 旁路 | 答什么 | 调模型 | 约束 |
|---|---|---|---|
| realtime | 会变的事实（今天几点升旗） | 否 | 时效闸门 + 权威入口 |
| knowledge | 不变的事实（西湖多大） | 是（一次） | 三道闸：未核实标注 + 复核入口 + 时效拒答 |
| **guide** | 都不是 —— 寒暄 / 闲话 / 元问题 | **仅 `meta` 调**（其余直答） | 无 —— 它本来就不含可核实的事实 |

**哪几类不调模型（2026-10-09 修「输入你好要等很久」）
------------------------------------------------
寒暄的七类里，`greeting` / `thanks` / `farewell` / `cancel` / `chitchat` 是**封闭式社交套话**：
它们的正确答复与用户**具体说了什么**无关 —— 「你好」的合适回复就是那句招呼 + 能力邀请，
i18n 定稿（`gd.reply.*`）**就是答案本身**，而且 zh/en/ja/ko 四语言齐全
（`app/i18n.py::detect_language` 只判得出这四种，非中日韩的拉丁文本一律归 `en`）——
所以「用定稿直答」在语言覆盖上**零损失**。

实测（2026-10-09，真模型 ecnu-max + `prompts/guide.md`）把这件事证死了：

| | 结果 |
|---|---|
| 模型为「你好」写的引导语 | 与定稿**几乎逐字相同**（只换了几个连接词） |
| 它的耗时 | 中位约 1s，**但有 2.7 / 3.0 / 6.9 / 11.1s 的长尾** |

也就是说，为一个可以直答的套话付出了最高 11 秒的等待。这与 `recall` 已确立的规则同源
（见 `app/graph.py::guide_node`）：**当确定性来源就是权威答案时，不要问模型。**
保留 `meta`（你是谁 / 能做什么）是模型调用 —— 它是这七类里唯一的**真实问句**，
值得一次润色；它走快档且超时收到 6s，超时即退回定稿。

真 bug（这一层的存在理由）
------------------------
（2026-10-04 用户报）输入「你好」，系统直接回：

    还需要确认：想去的城市或国家是哪里？计划玩几天，或者大致哪几天出发？
    这次大概的预算是多少（人民币）？一行几个人，有小孩或长者同行吗？

这是「把不是排行程的请求当成排行程」的**第三形态**（前两种是实时 / 常识）。
问候要的是一句引导，不是一份需求表单。成熟做法（Rasa 的 chitchat intent +
utter_chitchat，Dialogflow 的 help + fallback intent）都是给这类输入**独立意图 + 独立话术**。

为什么这一层允许调模型而 realtime 不许
------------------------------------
寒暄答复里**没有任何可核实的事实断言** —— 它不复述时刻、不给数字、不下政策结论。
所以既没有时效闸门要过，也不需要「未经核实」标注：本来就没什么可核实的。

模型不可用时**必须**退回 i18n 里的定稿文案（`gd.reply.*`）——
寒暄绝不能因为模型挂掉而退化成那四连问。这正是「优雅失败」：降级，但不是降级成错误的东西。
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from .config import Settings
from .followups import clean_chips
from .i18n import DEFAULT, answer_language_directive, t
from .intent import SocialIntent
from .llm import LLMError
from .prompts import load_prompt
from .schemas import GuideDraft, GuideResponse

# 引导末尾附的「建议提问」（点击即发）。文案在 i18n，跟输出语言。
STARTER_SEP = "|"
# 每屏显示几条。`gd.starters`（主）+`gd.starters_extra`（备用池）合成一个池子，
# 按轮次**轮换窗口**取这么多条 —— 只有一套定稿时，连点两次「你好」示例提问一字不差（死板）。
STARTER_COUNT = 4


def starters(language: str = DEFAULT, offset: int = 0) -> list[str]:
    """「试着这样问」的**兜底**文案：模型没给 starters 时才用它（见 `build_guide_response`）。

    `gd.starters` 与 `gd.starters_extra` 合成一个池子，按 `offset` 轮换出一个
    `STARTER_COUNT` 条的窗口 —— 同一会话连点几次「你好」，示例提问不再一字不差。
    `offset` 取会话轮次（`graph.output_node` 传 `sessions.turn`）。
    """
    pool: list[str] = []
    for key in ("gd.starters", "gd.starters_extra"):
        raw = t(key, language)
        if raw == key:  # i18n 缺这条文案 → 跳过（别把 key 本身当建议）
            continue
        pool.extend(s.strip() for s in raw.split(STARTER_SEP) if s.strip())
    if not pool:
        return []
    if offset and len(pool) > STARTER_COUNT:
        k = offset % (len(pool) - STARTER_COUNT + 1)
        pool = pool[k:] + pool[:k]
    return pool[:STARTER_COUNT]


# 封闭式社交套话：问候 / 致谢 / 道别 / 取消 / **纯笑声闲话**。它们的答复是固定的，定稿即答案
# （理由与实测见模块顶部「哪几类不调模型」）。**新增 kind 时先问一句：
# 它的回复会随用户说的话变化吗？** 不会，就别往这里加模型调用。
SCRIPTED_KINDS = ("greeting", "thanks", "farewell", "cancel", "chitchat")


def scripted_reply(intent: SocialIntent, language: str = DEFAULT) -> str | None:
    """套话类直接给定稿（0 模型调用）；`meta` 等其余返回 None，交给 `compose_guide`。"""
    if intent.kind not in SCRIPTED_KINDS:
        return None
    return t(f"gd.reply.{intent.kind}", language) or t("gd.reply.greeting", language)


def build_guide_prompt(
    *,
    settings: Settings,
    intent: SocialIntent,
    message: str = "",
    language: str = DEFAULT,
) -> str:
    """装配提示词。**先填 schema 与语言段，再填用户内容**（与 answer.md 同一纪律：

    反过来做，用户消息里恰好出现 `{{kind}}` 就会被二次替换，把模板结构打乱）。
    """
    tmpl = load_prompt(settings.prompt_paths.get("guide", "prompts/guide.md"))
    schema = json.dumps(GuideDraft.model_json_schema(), ensure_ascii=False, indent=2)
    return (
        tmpl.replace("{{output_schema}}", schema)
        .replace("{{language_directive}}", answer_language_directive(language))
        .replace("{{kind}}", intent.kind or "greeting")
        .replace("{{message}}", message or "")
    )


def compose_guide(
    *,
    settings: Settings,
    llm,
    intent: SocialIntent,
    message: str = "",
    language: str = DEFAULT,
) -> GuideDraft | None:
    """让通用模型写一句引导语（外加「试着这样问」的示例提问）。

    **失败不抛异常，返回 None**（调用方退回定稿文案与定稿 starters）。
    返回整个 draft 而不是只返回 reply，是为了把模型顺手写的 `starters` 一并带出去。
    """
    try:
        raw = llm.complete_json(
            role="generator",
            prompt=build_guide_prompt(
                settings=settings, intent=intent, message=message, language=language
            ),
            schema=GuideDraft.model_json_schema(),
            context={
                "task": "guide",
                "kind": intent.kind,
                "message": message,
                "language": language,
            },
        )
    except LLMError:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        draft = GuideDraft.model_validate(raw)
    except ValidationError:
        return None
    draft.reply = (draft.reply or "").strip()
    return draft if (draft.reply or draft.starters) else None


def build_guide_response(
    *,
    settings: Settings,
    intent: SocialIntent,
    reply: str = "",
    message: str = "",
    language: str = DEFAULT,
    starters_override: list[str] | None = None,
    starters_offset: int = 0,
) -> GuideResponse:
    """组装答复。整段用户可见文案跟输出语言。模型没给话术就退回 i18n 定稿。

    「试着这样问」以**模型同一次调用里写的几条**为主（`starters_override`），
    为空才退回 i18n 定稿 —— 定稿现在也只是兜底：`starters()` 会把两套池子按
    `starters_offset`（会话轮次）轮换，不再是一成不变的那 4 条。
    """
    text = (reply or "").strip() or t(f"gd.reply.{intent.kind}", language) or t(
        "gd.reply.greeting", language
    )
    chips = clean_chips(starters_override, limit=STARTER_COUNT) or starters(
        language, offset=starters_offset
    )
    return GuideResponse(
        kind=intent.kind,
        message_echo=message,
        reply=text,
        starters=chips,
        note=t("gd.note", language),
        disclaimer=t("gd.disclaimer", language),
    )
