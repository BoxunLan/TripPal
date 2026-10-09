"""提示词装配：一份基座 + 按优先级拼接的场景叠加层。

叠加顺序固定为 安全与免责 → 签证合规 → 预算 → 场景体验（见 routes.yaml 的
fusion.overlay_priority）。场景文案全部来自 prompts/*.md，图节点里没有硬编码。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, Settings
from .i18n import DEFAULT as DEFAULT_LANGUAGE
from .i18n import guardrail_text, output_language_directive
from .schemas import Itinerary, RetrievedChunk, RetrievedContext, ToolResult


@lru_cache(maxsize=64)
def load_prompt(rel_path: str) -> str:
    path = Path(rel_path)
    if not path.is_absolute():
        path = REPO_ROOT / rel_path
    return path.read_text(encoding="utf-8")


def overlay_sort_key(settings: Settings, scene_id: str) -> tuple[int, int]:
    cfg = settings.scene_cfg(scene_id)
    slot = cfg.get("overlay_slot", "scene")
    order = settings.overlay_priority
    idx = order.index(slot) if slot in order else len(order)
    return (idx, settings.scene_ids.index(scene_id) if scene_id in settings.scene_ids else 99)


def build_overlay_refs(settings: Settings, scenes: list[str]) -> list[str]:
    """按优先级返回叠加层文件路径列表（去掉安全层，安全层由调用方单独加在最前）。"""
    ordered = sorted(scenes, key=lambda s: overlay_sort_key(settings, s))
    refs: list[str] = []
    for sid in ordered:
        for fname in settings.scene_cfg(sid).get("prompts", []) or []:
            ref = f"{settings.prompt_paths.get('dir', 'prompts')}/{fname}"
            if ref not in refs:
                refs.append(ref)
    return refs


def _render_chunks_block(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "（无检索结果。所有具体事实都必须写成「待核实」，citation_key 一律填 null。）"
    lines = []
    for c in chunks:
        parts = [f"- citation_key={c.citation_key} | layer={c.layer} | source={c.source}"]
        if c.fresh_until:
            parts.append(f"fresh_until={c.fresh_until}")
        lines.append(" ".join(parts))
        lines.append(f"  {c.text}")
    return "\n".join(lines)


def _render_tools_block(results: list[ToolResult]) -> str:
    if not results:
        return "（本轮未调用工具。）"
    lines = []
    for r in results:
        if r.ok:
            lines.append(f"- citation_key={r.citation_key} | tool={r.tool}")
            lines.append(f"  {json.dumps(r.payload, ensure_ascii=False)}")
            if r.source_url:
                lines.append(f"  source_url={r.source_url} effective_date={r.effective_date}")
        else:
            lines.append(f"- tool={r.tool} 调用失败：{r.error}")
    return "\n".join(lines)


def build_generate_prompt(
    *,
    settings: Settings,
    scenes: list[str],
    primary_scene: str,
    rewritten_query: str,
    slots: dict[str, Any],
    context: RetrievedContext,
    tool_results: list[ToolResult],
    guardrail_ids: list[str],
    validation_findings: list[str] | None = None,
    locked_segments: list[str] | None = None,
    output_language: str = DEFAULT_LANGUAGE,
    previous_itinerary: dict[str, Any] | None = None,
    revision_note: str = "",
) -> str:
    base_ref = settings.prompt_paths.get("base", "prompts/base.md")
    safety_ref = settings.prompt_paths.get("safety_overlay", "prompts/safety.md")

    # 这段会被 safety.md 的 {{guardrail_disclaimer}} 占位符注进提示词，
    # 模型经常**原样抄进 disclaimers** —— 所以必须跟输出语言，否则韩语/英文响应里
    # 会夹一段中文免责声明（实测过）。
    disclaimer = " ".join(
        txt
        for g in guardrail_ids
        if (txt := guardrail_text(settings, g, output_language))
    )
    schema = json.dumps(Itinerary.model_json_schema(), ensure_ascii=False, indent=2)

    base = load_prompt(base_ref)
    body = base.replace("{{rewritten_query}}", rewritten_query)
    body = body.replace("{{slots_json}}", json.dumps(slots, ensure_ascii=False, indent=2))
    body = body.replace("{{primary_scene}}", primary_scene)
    body = body.replace("{{scenes_csv}}", "、".join(scenes) or primary_scene)
    body = body.replace("{{chunks_block}}", _render_chunks_block(context.chunks))
    body = body.replace("{{tool_results_block}}", _render_tools_block(tool_results))
    body = body.replace("{{output_schema}}", schema)

    safety = load_prompt(safety_ref).replace("{{guardrail_disclaimer}}", disclaimer)

    sections = [body, safety]
    for ref in build_overlay_refs(settings, scenes):
        sections.append(load_prompt(ref).replace("{{guardrail_disclaimer}}", disclaimer))

    # 语言段放在**最后**（回炉修正之前）。场景叠加层全是中文，把语言段夹在它们中间，
    # 模型最后读到的是中文细则 —— 实测韩语请求有时会整段输出中文。
    # 中文是默认语言，不加这一段（避免给中文路径塞无意义的指令）。
    directive = output_language_directive(output_language)
    if directive:
        sections.append(directive)

    # 行程内迭代（市场对标）：这一轮是**改上一版行程**，不是从零重排。把上一版稿
    # 连同本次修改诉求一起给模型 —— 没有「上一版」就没有「改」，只能重排（会被用户
    # 当成"又从头来一遍"，M1 实测标题里就写着"第 3 天重点"却换掉了整份稿）。
    if revision_note:
        prev = ""
        if previous_itinerary:
            prev = (
                "\n\n上一版行程（JSON，请**只改本次诉求涉及的部分**，其余原样保留）：\n"
                + json.dumps(previous_itinerary, ensure_ascii=False, indent=1)
            )
        sections.append(
            "# 行程修改（重要）\n\n"
            f"用户要修改的正是这一版行程，诉求是：**{revision_note}**。\n"
            "规则：\n"
            "- **在原稿基础上改**，只动诉求指向的部分（某一天 / 住宿 / 预算 / 节奏），"
            "其余段落与顺序原样保留，不要另起一份全新行程。\n"
            "- 若诉求指向「第 N 天」且是**重排 / 放松 / 加时长**，只重排那一天；天数、目的地、"
            "其余天不变。\n"
            "- 若诉求是**增删天数**（「去掉第 4 天」「缩短到三天」「再加一天」），`days` 数组长度"
            "必须相应变化（去掉一天 = 少一个 day 对象、`day` 序号重排连续），`date_range` 同步改；"
            "**不要**只删内容却保留同样多的 day。\n"
            "- 若诉求是「下雨 / 天气备选」，为对应那天补一条**备选方案**（室内或不受天气影响），"
            "不要回答实时天气。\n"
            "- 若诉求是「预算怎么分配」，据此把 `budget.lines` 拆细（住宿 / 餐饮 / 交通 / 门票 / 其他）。\n"
            "- 若诉求是**提高预算 / 开销**（「多花点」「提高开销」「预算没用完」）：把 "
            "`budget.lines` 各项**朝上改**（住宿升级、加入体验 / 演出 / 深度项目、餐饮提档、"
            "可含购物与伴手礼），使 `total` **明显贴近** `user_budget`，并让行程档次与预算相称；"
            "**绝不能反而更省**。用户若给了目标金额，就贴着它排；确实花不到时才在 notes 里说明。\n"
            "- 若诉求是**降低预算 / 开销**（「太贵了」「便宜点」）：相应下调 `budget.lines`，"
            "使 `total` 落到诉求指向的水平，其余内容尽量保留。\n"
            "- 若诉求是「把 A 城换成 B 城」（多城行程），把 A 换掉、保留其余城市与天数。"
            + prev
        )

    if validation_findings:
        locked = ""
        if locked_segments:
            locked = (
                "\n\n上次已通过校验的段落，请**原样保留**，不要重写：\n"
                + "\n".join(f"- {s}" for s in locked_segments)
            )
        sections.append(
            "# 回炉修正（第 1 轮，也是唯一一轮）\n\n"
            "上一版校验未通过，原因如下。**只修改失败的部分，其余段落原样保留。**\n"
            + "\n".join(f"- {f}" for f in validation_findings)
            + locked
        )
    return "\n\n---\n\n".join(sections)


def build_classify_prompt(*, settings: Settings, message: str, session_slots: dict[str, Any], hinted: list[str]) -> str:
    scene_lines = []
    for sid in settings.scene_ids:
        cfg = settings.scene_cfg(sid)
        kws = "、".join(cfg.get("keywords", []) or [])
        scene_lines.append(
            f"- scene_id={sid}（{cfg.get('display_name', sid)}）：适合 {kws}"
        )
    return (
        "你是旅行请求分类器。只输出 JSON，不要 markdown 围栏。\n\n"
        f"用户消息：{message}\n"
        f"上一轮已确认槽位：{json.dumps(session_slots, ensure_ascii=False)}\n"
        f"关键词初筛命中（仅供参考，可推翻）：{hinted}\n\n"
        "可选场景（只能从这里选，不要发明新场景）：\n"
        + "\n".join(scene_lines)
        + "\n\n注意：蜜月、商务、老年**不是**独立场景，不要创建；老年只作为安全提示。\n"
        "多标签是允许且必要的，例如「带孩子穷游」应同时给出 family 与 budget。\n\n"
        "输出字段：\n"
        "{\n"
        '  "labels": [{"scene_id": "...", "confidence": 0.0-1.0}],\n'
        '  "primary_scene": "置信度最高的 scene_id",\n'
        '  "slots": {"destination": "...", "date_range": "...", "budget": 0, "party": "...", "destination_country": "...", "nationality": "..."},\n'
        '  "missing_slots": ["关键槽位里仍然缺失的项"],\n'
        '  "clarify_question": "需要追问时的一句话，否则 null",\n'
        '  "rewritten_query": "把用户诉求改写成一句检索友好的中文查询"\n'
        "}\n"
        f"关键槽位定义：普通场景为 {settings.required_slots(settings.fallback_scene)}；"
        f"visa 场景为 {settings.required_slots('visa')}，且不强制 budget 与 date_range。\n"
        "**destination_country 与 nationality 是两件事，不要混**："
        "destination_country = 要去/要办签证的那个国家（通常是中国）；"
        "nationality = **游客本人来自哪个国家**（「我是德国人」「I'm from France」）。"
        "用户没明说来源国时，nationality 一律留空，**不要猜**。\n"
        "confidence 要给真实区分度，不要一律 0.9。"
    )
