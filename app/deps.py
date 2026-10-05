"""依赖容器。所有外部依赖（LLM / 向量库 / Embedding / 工具 / 会话）都从这里注入，
测试可以在不改业务代码的前提下整套换成假实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from .config import REPO_ROOT, Settings, get_settings
from .embed import Embedder, build_embedder
from .llm import LLMClient, build_llm
from .session import SessionStore
from .store import build_store
from .tools import ToolBox

# 会话审计落点。放在 .workbuddy/ 下（那是本项目的运行期目录，不进 git）。
AUDIT_DIR = REPO_ROOT / ".workbuddy" / "audit"


@dataclass
class Deps:
    settings: Settings
    embedder: Embedder
    llm: LLMClient
    store: object
    toolbox: ToolBox
    sessions: SessionStore = field(default_factory=lambda: SessionStore(AUDIT_DIR))
    today: Callable[[], date] = date.today


def build_deps(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
    store: object | None = None,
    sessions: SessionStore | None = None,
    today: Callable[[], date] | None = None,
) -> Deps:
    settings = settings or get_settings()
    embedder = embedder or build_embedder(settings.embedding)
    store = store if store is not None else build_store(settings, embedder)
    return Deps(
        settings=settings,
        embedder=embedder,
        llm=llm or build_llm(settings),
        store=store,
        toolbox=ToolBox(store=store, settings=settings),
        # 真实运行时把会话审计写进 .workbuddy/audit/；测试传入自己的 store（通常是
        # 不落盘的 SessionStore()），免得跑一次测试就在仓库里留一堆日期文件。
        sessions=sessions or SessionStore(AUDIT_DIR),
        today=today or date.today,
    )
