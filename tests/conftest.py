"""测试夹具：整套外部依赖换成假实现（假 LLM + 内存向量库 + 冻结时钟）。

无密钥、无网络、无 docker 也能跑完 5 条验收。
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在 import app.* 之前设置：模块级 `app = create_app()` 会读这些环境变量。
# load_dotenv 不覆盖已存在的环境变量，所以这里的设置优先于仓库里的 .env。
os.environ.setdefault("TRAVEL_VECTOR_BACKEND", "memory")
os.environ.setdefault("TRAVEL_LLM_PROVIDER", "fake")
# 显式清掉 embedding 模型：仓库里的 .env 一旦配了真实 embedding 端点，任何调用
# build_embedder 的代码路径都会去连网络。测试必须永远走本地 hash。
os.environ.setdefault("TRAVEL_EMBEDDING_MODEL", "")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.deps import build_deps  # noqa: E402
from app.embed import HashingEmbedder  # noqa: E402
from app.fakes import FakeLLM  # noqa: E402
from app.main import create_app  # noqa: E402
from app.session import SessionStore  # noqa: E402
from app.store import InMemoryVectorStore  # noqa: E402

# 冻结「今天」，让 realtime 层的时效过滤在任何人机器上都得到同一结果
FROZEN_TODAY = date(2026, 9, 25)


class CountingStore(InMemoryVectorStore):
    """记录检索调用次数 —— 验收 1 要断言「缺槽时不得触发检索」。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.search_calls = 0
        self.scan_calls = 0

    def search(self, *args, **kwargs):
        self.search_calls += 1
        return super().search(*args, **kwargs)

    def scan(self, *args, **kwargs):
        self.scan_calls += 1
        return super().scan(*args, **kwargs)


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture
def store(settings):
    embedder = HashingEmbedder(settings.embedding.dim)
    return CountingStore.from_seed_dir(settings.seed_dir, embedder)


@pytest.fixture
def deps(settings, store):
    embedder = HashingEmbedder(settings.embedding.dim)
    return build_deps(
        settings,
        embedder=embedder,
        llm=FakeLLM(settings),
        store=store,
        sessions=SessionStore(),
        today=lambda: FROZEN_TODAY,
    )


@pytest.fixture
def client(deps):
    with TestClient(create_app(deps)) as c:
        yield c


def post_plan(client: TestClient, message: str, session_id: str = "s1") -> dict:
    resp = client.post("/plan", json={"session_id": session_id, "message": message})
    assert resp.status_code == 200, resp.text
    return resp.json()
