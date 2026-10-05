"""寒暄引导：用一句话把用户引到「旅行」这个话题上。

它在这套链路里是**第三条旁路**，与另两条的分工

| 旁路 | 答什么 | 调模型 | 约束 |
|---|---|---|---|
| realtime | 会变的事实（今天几点升旗） | 否 | 时效闸门 + 权威入口 |
| knowledge | 不变的事实（西湖多大） | 是（一次） | 三道闸：未核实标注 + 复核入口 + 时效拒答 |
| **guide** | 都不是 —— 寒暄 / 闲话 / 元问题 | 是（一次） | 无 —— 它本来就不含可核实的事实 |

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
from .i18n import DEFAULT, answer_language_directive, t
from .intent import SocialIntent
from .llm import LLMError
from .prompts import load_prompt
from .schemas import GuideDraft, GuideResponse

# 引导末尾附的「建议提问」（点击即发）。文案在 i18n，跟输出语言。
STARTER_SEP = "|"


def starters(language: str = DEFAULT) -> list[str]:
    raw = t("gd.starters", language)
    return [s.strip() for s in raw.split(STARTER_SEP) if s.strip()]


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
) -> str:
    """让通用模型写一句引导语。**失败不抛异常，返回空串**（调用方退回定稿文案）。"""
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
        return ""
    if not isinstance(raw, dict):
        return ""
    try:
        return (GuideDraft.model_validate(raw).reply or "").strip()
    except ValidationError:
        return ""


def build_guide_response(
    *,
    settings: Settings,
    intent: SocialIntent,
    reply: str = "",
    message: str = "",
    language: str = DEFAULT,
) -> GuideResponse:
    """组装答复。整段用户可见文案跟输出语言。模型没给话术就退回 i18n 定稿。"""
    text = (reply or "").strip() or t(f"gd.reply.{intent.kind}", language) or t(
        "gd.reply.greeting", language
    )
    return GuideResponse(
        kind=intent.kind,
        message_echo=message,
        reply=text,
        starters=starters(language),
        note=t("gd.note", language),
        disclaimer=t("gd.disclaimer", language),
    )
