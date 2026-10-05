"""实时事实问询：查知识库的实时层 + 组装答复。

与行程链路的分工很干脆：
- 行程链路（retrieve → generate）产出**编排**（逐日安排、预算、清单），允许「大致这样」。
- 这一层产出**断言**（今天几点升旗、现在开不开门），只有两种合法结果：
  查到没过期的来源，或者明确说查不到并给出权威入口。中间态（拿旧攻略编一个时刻）
  是最坏的结果 —— 它读起来像答案。

三条硬规矩
----------
1. **只查实时层**（`realtime:*`）。scene/general 是攻略与常识，用来回答「今天开不开门」
   会拿过期材料冒充当日状态。
2. **相关性闸门**（`_is_relevant`）：候选必须「主体出现在正文里」或「条目目的地就是问的地点」，
   两者都不满足一律丢弃。少了这道闸门，「金阁寺开放情况」会召回一堆签证条目 ——
   向量检索永远会返回 top_k，它不知道什么叫「不相关」。
3. **无模型、无向量**：用 `store.scan` 做元数据扫描 + 确定性排序（新者在前），
   而不是 `store.search`。事实断言不该由模型生成，也不该由相似度决定谁进候选。
   顺带这条链路很快：用户问「今天几点升旗」不该等 20 秒。
"""

from __future__ import annotations

import re
from datetime import date

from .config import Settings
from .i18n import DEFAULT, t
from .intent import RealtimeIntent, realtime_cfg
from .schemas import Chunk, RealtimeChannel, RealtimeFact, RealtimeResponse

MAX_FOUND = 4


def _kind_ok(chunk: Chunk, intent: RealtimeIntent) -> bool:
    """条目声明的 kind 必须在该信息类型允许的名单里。

    没声明 kind 的条目一律不参与 —— 一条说不清自己是什么的记录，不该被拿来断言事实。
    """
    if not intent.kinds:
        return True
    return str((chunk.metadata or {}).get("kind") or "") in intent.kinds


def _norm(text: str) -> str:
    """去掉所有空白后的比较键：「240 小时」≡「240小时」。

    中文里的空格是人随手打的，同一条政策写成「240小时」和「240 小时」都常见。
    不归一的话，用户少打一个空格就查不到库里明明有的条目 —— 而这条链路的
    合法性完全押在「主体出现在正文里」这一个字符串判断上。
    """
    return re.sub(r"\s+", "", text or "")


def _is_relevant(chunk: Chunk, intent: RealtimeIntent) -> bool:
    """相关性闸门。分两条路，取决于「主体」是否比「地点」更具体：

    - **主体更具体**（天安门 vs 北京）：正文里必须出现主体。
      天安门什么时候升旗 → 正文含「天安门」→ 收；
      北京故宫今天开放吗 → 正文都不含「北京故宫」→ 一条不收（诚实说查不到）。
    - **主体就是地点**（北京明天天气）：靠 metadata.kind 区分信息类型。
      destination=北京 且 kind 在 weather 名单里 → 收；升旗条目的 kind 是 rule → 不收。

    少了这道闸门，「北京明天天气」会拿升旗条目当答案 —— 地点对、类型不对，
    而向量检索永远会返回 top_k，它不知道什么叫「不相关」。
    """
    text = chunk.text or ""
    subject, place = intent.subject, intent.place
    if intent.relevance == "matched":
        # 政策类：主体是**政策名**（「过境免签」「单方面免签」），不是地名。
        # 拿命中片段之前那一截当主体是错的 ——「240 小时过境免签适用哪些国家」会切出
        # 一整串原话，「单方面免签来华能待多久」更是切出空串，两者都必然判成「不相关」。
        # 所以这里改用命中的政策名本身做相关性判据。
        return _norm(intent.matched) in _norm(text)
    if subject and subject != place:
        # 空白归一后再比：中文里「240 小时」与「240小时」指同一条政策，字面却不等。
        return _norm(subject) in _norm(text)
    if place and chunk.destination == place:
        return _kind_ok(chunk, intent)
    return False


def _fresher_first(chunk: Chunk) -> tuple[date, str]:
    """排序键：生效日新的靠前（同样新旧的按 chunk_id 稳定排序）。

    事实类信息就该让「最近生效的」先说话 —— 相似度高低对「今天几点」毫无意义。
    """
    return (chunk.effective_date or date.min, chunk.chunk_id)


def search_realtime_facts(
    *,
    settings: Settings,
    store,
    intent: RealtimeIntent,
    today: date,
    limit: int = MAX_FOUND,
) -> tuple[list[RealtimeFact], int]:
    """返回 (未过期的事实, 相关但已过期的条数)。

    过期条数单独返回：对用户来说「知识库没有这条」和「有但过期了」含义不同 ——
    后者说明这个信息源确实在库里，只是该更新了。
    """
    scopes = list(realtime_cfg(settings).get("scopes") or ["realtime:*"])

    def scan(place: str | None) -> list[Chunk]:
        filters: dict = {"scopes": scopes}
        if place:
            filters["destinations"] = [place]
        return list(store.scan(filters=filters))

    rows = scan(intent.place)
    if not rows and intent.place:
        # 词典没覆盖到这个主体时放开地理过滤，让相关性闸门来判。
        # 空/非空 destination 两边都放行的语义要求见 app/store.py 的 `_scope_match` / `_where`。
        rows = scan(None)

    relevant = [c for c in rows if c.layer == "realtime" and _is_relevant(c, intent)]
    fresh = [
        c for c in relevant if not (c.fresh_until and c.fresh_until < today)
    ]
    expired = len(relevant) - len(fresh)

    found = [
        RealtimeFact(
            chunk_id=c.chunk_id,
            text=c.text,
            source=c.source,
            source_url=c.source_url,
            effective_date=c.effective_date,
            fresh_until=c.fresh_until,
        )
        for c in sorted(fresh, key=_fresher_first, reverse=True)[:limit]
    ]
    return found, expired


def build_realtime_response(
    *,
    settings: Settings,
    intent: RealtimeIntent,
    found: list[RealtimeFact],
    expired: int = 0,
    message: str = "",
    language: str = DEFAULT,
) -> RealtimeResponse:
    """组装答复。整段用户可见文案跟输出语言；`channels[].name/url` 是引用，保持原样。"""
    label_key = f"rt.type.{intent.info_type}"
    label = t(label_key, language)
    # 显示用属性走 i18n；进检索式的 attr 是配置里那个恒中文的 attr
    attr = t(f"rt.attr.{intent.info_type}", language)

    channels = [
        RealtimeChannel(
            name=str(c.get("name", "")), url=str(c.get("url", "")), what=str(c.get("what", ""))
        )
        for c in intent.channels
        if c.get("name") and c.get("url")
    ]

    note = t("rt.head", language)
    if not found:
        note += " " + t("rt.found_none", language)
    if expired:
        note += " " + t("rt.expired", language, n=expired)
    if not channels:
        # 没入口必须说出来。留一片空白会让人以为「系统在查了」。
        note += " " + t("rt.no_channel", language)

    return RealtimeResponse(
        info_type=intent.info_type,
        label=label if label != label_key else intent.info_type,
        subject=intent.subject,
        place=intent.place,
        message_echo=message,
        question_zh=intent.question_zh,
        found=found,
        expired_dropped=expired,
        channels=channels,
        # 本次**没有**核实的东西。写出来是为了让「我们没查这个」显式可见 ——
        # 用户有权知道哪一项还是空的。
        unverified=[attr] if attr and not attr.startswith("rt.attr.") else [],
        note=note,
        plan_hint=t("rt.plan_hint", language),
        disclaimer=t("rt.disclaimer", language),
    )
