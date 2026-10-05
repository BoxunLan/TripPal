"""会话状态：槽位 + **结构化对话历史**。按 session_id 保存在进程内；逐轮追加审计。

历史为什么要留
--------------
（2026-10-04 用户报）「没有对话记忆」。具体表现是：问完「上海有没有免费的博物馆」，
再问「那要预约吗？」，系统回一句「想去的城市？玩几天？预算多少？一行几个人？」——
四个问题。因为**每一句都被当成独立的一句**：`clarify_node` 只拿当前 message 做意图判定，
上一轮解析出的主体（上海 / 上海博物馆）就地丢了。

这里的修法与成熟对话系统一致（Rasa 的 `tracker` + slot carry-over、Dialogflow 的
output context）：**把上一轮解析出的主体与检索实词存下来，供下一句承接**。
判定仍然是确定性的（见 `app/intent.py::carry_over_subject`），不调模型。

三个字段各有分工
----------------
- `last_fact_subject` / `last_fact_place`：上一轮**事实类**问询的主体与所在地。
  追问句（「那要预约吗」）自己没有主体，就接这两个。
- `last_fact_terms`：上一轮的判别性实词。追问句往往只补一个属性（「预约」），
  单靠自己那几个词检索不到东西，要**并上一轮**才能落在同一批条目上
  （对话式查询扩展，见 `app/knowledge.py::search_knowledge` 的 `extra_terms`）。
- `recent`：最近若干轮的原话与答复摘要。元问题（「刚才我说了什么」）由它**确定性**回答 ——
  这种问题最不能被模型自由发挥。

落盘（`audit_dir`）只追加、失败不影响主链路；它解决的是「事后想回看某次对话」。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .slots import merge_slots

logger = logging.getLogger("travel")

# 会话里保留的最近轮数。元问题只可能问「刚才」，留 12 轮足够，也避免内存无限涨。
MAX_RECENT = 12


@dataclass
class Turn:
    """一轮问答。`subject` 只在事实类（realtime / knowledge）问询时才有值。"""

    turn: int
    message: str
    type: str
    subject: str = ""
    reply: str = ""


@dataclass
class Session:
    session_id: str
    slots: dict[str, Any] = field(default_factory=dict)
    turn: int = 0
    missing_slots: list[str] = field(default_factory=list)
    # 兼容旧字段（旧代码/测试读它）。新逻辑一律读 `recent`。
    history: list[dict[str, Any]] = field(default_factory=list)
    recent: list[Turn] = field(default_factory=list)
    last_fact_subject: str = ""
    last_fact_place: str = ""
    last_fact_terms: list[str] = field(default_factory=list)


class SessionStore:
    """进程内会话表 + 逐轮审计落盘。

    `audit_dir` 为空则不落盘（测试默认如此 —— 测试不该往仓库里写文件）。
    """

    def __init__(self, audit_dir: str | Path | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._audit_dir = Path(audit_dir) if audit_dir else None

    def get(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id=session_id)
        return self._sessions[session_id]

    def update_slots(self, session_id: str, new_slots: dict[str, Any]) -> Session:
        session = self.get(session_id)
        session.slots = merge_slots(session.slots, new_slots)
        return session

    def remember_fact(
        self,
        session_id: str,
        *,
        subject: str = "",
        place: str = "",
        terms: list[str] | None = None,
    ) -> None:
        """记下这一轮事实类问询的承接材料（主体 / 所在地 / 实词）。"""
        session = self.get(session_id)
        if subject:
            session.last_fact_subject = subject
        if place:
            session.last_fact_place = place
        if terms:
            session.last_fact_terms = list(terms)

    def record(
        self,
        session_id: str,
        message: str,
        response_type: str,
        missing: list[str] | None = None,
        subject: str = "",
        reply: str = "",
    ) -> None:
        session = self.get(session_id)
        session.turn += 1
        session.missing_slots = list(missing or [])
        session.history.append({"turn": session.turn, "message": message, "type": response_type})
        session.recent.append(
            Turn(
                turn=session.turn,
                message=message,
                type=response_type,
                subject=subject,
                reply=(reply or "")[:280],
            )
        )
        del session.recent[:-MAX_RECENT]
        self._audit(session, session.recent[-1])

    def snapshot(self, session_id: str) -> dict[str, Any]:
        """给 `GET /session/{sid}`：这条会话现在记住了什么（供页面「会话记忆」面板）。"""
        session = self.get(session_id)
        return {
            "session_id": session.session_id,
            "turn": session.turn,
            "slots": session.slots,
            "last_fact_subject": session.last_fact_subject,
            "recent": [
                {"turn": t.turn, "message": t.message, "type": t.type, "subject": t.subject}
                for t in session.recent
            ],
        }

    def recent_messages(self, session_id: str, limit: int = 5) -> list[str]:
        """最近几轮的**用户原话**（元问题用它作答，不经过模型）。"""
        session = self.get(session_id)
        return [t.message for t in session.recent[-limit:]]

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------ 审计
    def _audit(self, session: Session, turn: Turn) -> None:
        """追加一行到 `<audit_dir>/turns-YYYY-MM-DD.jsonl`。

        审计是**观察手段**，不是链路的一部分 —— 任何失败都只记日志，不影响答复。
        （踩过：日志写入抛异常会把整个 /plan 变成 500，用户看到的是「服务挂了」，
        而他其实只是问了一句话。）
        """
        if self._audit_dir is None:
            return
        try:
            self._audit_dir.mkdir(parents=True, exist_ok=True)
            path = self._audit_dir / f"turns-{date.today().isoformat()}.jsonl"
            line = json.dumps(
                {
                    "session_id": session.session_id,
                    "turn": turn.turn,
                    "message": turn.message,
                    "type": turn.type,
                    "subject": turn.subject,
                },
                ensure_ascii=False,
            )
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001 - 见 docstring
            logger.debug("审计写入失败 session=%s", session.session_id, exc_info=True)
