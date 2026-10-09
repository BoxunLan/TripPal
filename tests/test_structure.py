"""图结构与装配的机械性检查：节点集合/顺序、提示词叠加顺序、配额归一。

这些断言把「任务书里写死的结构」变成可执行约束，防止后续改动悄悄漂移。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace

import httpx
import pytest

from app.config import EmbeddingSettings
from app.embed import APIEmbedder, EmbeddingDimMismatch, HashingEmbedder, build_embedder
from app.graph import build_graph
from app.prompts import build_overlay_refs
from app.store import InMemoryVectorStore


def test_graph_has_exactly_the_declared_nodes(deps):
    """七个主干节点 + 三条旁路（realtime / knowledge / guide）。

    三条旁路都绕开 classify/route/retrieve/generate/validate：
    realtime 答「会变的事实」，knowledge 答「不会变的常识」，
    guide 接「寒暄 / 闲话 / 元问题」（「你好」「谢谢」「你是谁」）。
    """
    compiled = build_graph(deps)
    nodes = set(compiled.get_graph().nodes)
    expected = {
        "clarify", "realtime", "knowledge", "guide",
        "classify", "route", "retrieve", "generate", "validate", "output",
    }
    assert expected <= nodes, f"缺少节点：{expected - nodes}"
    # 「恰好」：多出来的节点说明有人往图里塞了没被文档化的阶段
    extra = nodes - expected - {"__start__", "__end__"}
    assert not extra, f"出现了未声明的节点：{extra}"


def test_graph_edges_follow_the_declared_chain(deps):
    """主干顺序固定，条件边只用于缺槽短路、两条事实旁路与 repair 回环。"""
    compiled = build_graph(deps)
    edges = {(e.source, e.target) for e in compiled.get_graph().edges}
    # 主干
    assert ("__start__", "clarify") in edges
    assert ("classify", "route") in edges
    assert ("retrieve", "generate") in edges
    assert ("generate", "validate") in edges
    assert ("output", "__end__") in edges
    # 缺槽短路
    assert ("clarify", "classify") in edges and ("clarify", "output") in edges
    assert ("route", "retrieve") in edges and ("route", "output") in edges
    # 实时事实旁路：clarify 直接进 realtime，realtime 直接进 output（不碰检索与生成）
    assert ("clarify", "realtime") in edges and ("realtime", "output") in edges
    assert {t for s, t in edges if s == "realtime"} == {"output"}
    # 常识旁路：clarify 直接进 knowledge，knowledge 直接进 output
    assert ("clarify", "knowledge") in edges and ("knowledge", "output") in edges
    assert {t for s, t in edges if s == "knowledge"} == {"output"}
    # 寒暄旁路：clarify 直接进 guide，guide 直接进 output
    assert ("clarify", "guide") in edges and ("guide", "output") in edges
    assert {t for s, t in edges if s == "guide"} == {"output"}
    # repair 回环：validate 只回到 generate 或直接出稿
    assert ("validate", "generate") in edges
    assert ("validate", "output") in edges
    # 不允许出现 generate 之外的节点指向 generate（即不允许别的回环）
    assert {s for s, t in edges if t == "generate"} == {"retrieve", "validate"}


def test_overlay_order_follows_priority(deps):
    """叠加层顺序固定：安全与免责 → 签证合规 → 预算 → 场景体验。"""
    refs = build_overlay_refs(deps.settings, ["family", "budget", "visa", "roadtrip"])
    assert refs == ["prompts/visa.md", "prompts/budget.md", "prompts/family.md", "prompts/roadtrip.md"]


def test_safety_overlay_is_always_first_in_route_prompt_refs(deps):
    from app.router import build_route
    from app.schemas import SceneClassificationResult, SceneLabel

    classification = SceneClassificationResult(
        request_id="r",
        labels=[SceneLabel(scene_id="family", confidence=0.9)],
        primary_scene="family",
        slots={"destination": "厦门", "date_range": "5 天", "budget": 8000, "party": "3 位成人"},
        missing_slots=[],
        rewritten_query="厦门 5 天 亲子",
        classifier_version="t",
    )
    route = build_route(settings=deps.settings, classification=classification)
    assert route.prompt_refs.overlays[0] == "prompts/safety.md"
    assert route.prompt_refs.base == "prompts/base.md"


def test_layer_quota_is_normalised(deps):
    for scene in ["family", "budget", "roadtrip", "visa", None]:
        quota = deps.settings.layer_quota(scene)
        assert abs(sum(quota.values()) - 1.0) < 1e-9
        assert set(quota) == {"general", "scene", "realtime"}


def test_available_tools_are_exactly_three(deps):
    assert set(deps.settings.available_tools) == {"opening_hours", "budget_sum", "visa_policy"}


def test_health_exposes_embedding_configuration(client, deps):
    """embedding 是最容易配错的一环（模型 / 维度 / 批量），必须在 /health 一眼可见。"""
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["chunks"] > 0
    # 报的维度必须与实际用的 embedder 一致，否则这个字段反而误导人
    assert body["embedding_dim"] == getattr(deps.embedder, "dim") == deps.settings.embedding.dim
    assert body["embedding_batch"] == deps.settings.embedding.batch
    assert body["embedding"]  # 真实模型名，或 "local-hash"


def test_web_ui_is_served_at_root(client):
    """试用界面挂在 `/` 上：浏览器打开就能用，不用另起前端服务。

    页面是同源调用 `/plan` 与 `/health`，所以必须由同一个服务托管 ——
    只给一个 html 文件路径会让人以为还要装个静态服务器。
    """
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    html = resp.text
    # 品牌名在 2026-10-06 界面重做成对话式时换成了 TripPal，
    # 这里的意图是「`/` 返回的是本站页面」，不是死记旧标题。
    assert "<title>TripPal" in html
    # 同源调用：改成绝对 URL（http://127.0.0.1:8000/plan）会在换端口时静默失效
    # 页面绝不能硬编码 "/xxx"：以 file:// 双击打开时需要回落到 http://127.0.0.1:8000
    assert 'fetch("/plan"' not in html
    assert 'fetch("/health"' not in html
    assert 'API("/plan/start")' in html
    assert 'API("/health")' in html
    # 页面可能被拷到别的静态端口打开（编辑器预览就是这么干的），
    # 那时同源的 /plan 根本不存在 —— 必须自动寻址到真服务，否则就是「点了没反应」。
    assert "resolveBase" in html
    assert "http://127.0.0.1:8000" in html
    assert "const baseReady" in html
    # 防回归：addResult 曾写成 `appendChild(d.firstElementChild)` 之后又读
    # `d.firstElementChild` —— 节点已被移走所以是 null，抛 TypeError，
    # send() 在第一步就中断，**请求从未发出**（症状：转圈、已等 0s、七步不亮）。
    # 必须先把节点接进变量再用它。
    # 防回归：addResult 曾写成 `appendChild(d.firstElementChild)` 之后又读
    # `d.firstElementChild` —— 节点已被移走所以是 null，抛 TypeError。
    # 对话式界面（2026-10-06）改成先求值再交给 appendChild：
    # `holder.appendChild(card.firstElementChild)`。
    # 判据不是「只许出现一次」—— 2026-10-09 加了会话重放（`appendAiCard`），
    # 卡片插入点变成两处（send 跑完 / 重放），两处都必须是同一个正确写法。
    assert html.count("firstElementChild") == 2
    assert html.count(".appendChild(card.firstElementChild)") == 2
    assert "firstElementChild.scrollIntoView" not in html   # 历史 bug 的原始形态
    assert "scrollBottom" in html


# ---------------------------------------------------------------- 流式进度
def _read_stream(client, payload):
    """读一个 NDJSON 流，返回 (中间 stage 事件列表, 收尾事件)。"""
    stages, last = [], None
    with client.stream("POST", "/plan/stream", json=payload) as resp:
        assert resp.status_code == 200, resp.text
        assert "ndjson" in resp.headers["content-type"], resp.headers["content-type"]
        for line in resp.iter_lines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("stage"):
                stages.append(ev)
            else:
                last = ev
    return stages, last


def test_plan_stream_reports_every_node_as_it_finishes(client):
    """/plan/stream 每跑完一个节点推一行，最后一行是完整响应。

    存在的理由：真实模型一条链路 15–40s，纯客户端计时会被浏览器节流到分钟级，
    用户只能看到「一直转圈」。进度必须由服务端推。
    """
    stages, last = _read_stream(client, {
        "session_id": "s-stream", "message": "带孩子穷游厦门 3 天，预算 2000",
    })

    assert [s["stage"] for s in stages] == [
        "clarify", "classify", "route", "retrieve", "generate", "validate", "output",
    ], "进度必须逐个节点推进，缺一个就等于用户看不到那一步的进度"
    assert all(s["label"] and s["ms"] >= 0 for s in stages)
    # 短注必须带现场信息：这些数大多不在最终响应里，是排障时真正想要的。
    # （键名写错过一次：当成 retrieved/validation/scenes，实际是 context/report/labels）
    detail = {s["stage"]: s["detail"] for s in stages}
    assert "family" in detail["classify"], "分类短注要有主场景与置信度"
    assert detail["route"].startswith("route.") and "family" in detail["route"]
    assert int(detail["retrieve"].split()[1]) > 0, "召回 0 条等于检索没干活"
    assert detail["generate"].startswith("草稿")
    assert "budget:" in detail["validate"], "校验短注要出每个 check 的 status"
    assert last and last["done"] is True
    assert last["response"]["type"] == "plan"


def test_plan_stream_short_circuits_without_retrieval(client, store):
    """缺槽短路同样得有进度：只经过 clarify → output，且不碰检索。"""
    stages, last = _read_stream(client, {"session_id": "s-stream2", "message": "想去泰国玩"})

    assert [s["stage"] for s in stages] == ["clarify", "output"]
    assert last["response"]["type"] == "clarify"
    assert store.search_calls == 0, "缺槽时不得触发检索（验收 1）"


# ------------------------------------------------------- 起步 + 轮询（页面走的通道）
def _poll_until_done(client, task_id, budget_s=30.0):
    """模拟页面轮询：反复拉 /plan/progress，直到 done。返回最后一次的状态。"""
    deadline = time.monotonic() + budget_s
    st = None
    while time.monotonic() < deadline:
        resp = client.get(f"/plan/progress/{task_id}")
        assert resp.status_code == 200, resp.text
        st = resp.json()
        if st["done"]:
            return st
        time.sleep(0.05)
    raise AssertionError(f"轮询超时，最后一次：{st}")


def test_plan_start_returns_immediately_and_progress_accumulates(client):
    """/plan/start 只负责起步，/plan/progress 逐步吐出已跑完的节点。

    这是页面真正使用的通道。/plan/stream 那条 NDJSON 流会被不清楚「流是什么」的
    中间层缓冲到结束才吐，那时事件全在最后一刻到达，等于没有进度 —— 所以留了这套
    最笨但最稳的短轮询路径。两条通道的事件内容必须一致。
    """
    started = client.post("/plan/start", json={
        "session_id": "s-poll", "message": "带孩子穷游厦门 3 天，预算 2000"})

    assert started.status_code == 200, started.text
    task_id = started.json()["task_id"]
    assert task_id, "拿不到 task_id 页面就没法轮询"

    early = client.get(f"/plan/progress/{task_id}")
    assert early.status_code == 200
    assert "stages" in early.json(), "未跑完也必须回得起 stages（页面要逐步点亮）"

    st = _poll_until_done(client, task_id)
    assert st["done"] is True
    assert [s["stage"] for s in st["stages"]] == [
        "clarify", "classify", "route", "retrieve", "generate", "validate", "output",
    ], "轮询看到的节点序列必须与流式一致，否则两条通道会各自漂移"
    assert st["response"]["type"] == "plan", st.get("error")
    assert st["ms"] >= 0


def test_plan_start_short_circuits_without_retrieval(client, store):
    """缺槽的请求：轮询同样只经过 clarify → output，且不碰检索。"""
    task_id = client.post(
        "/plan/start", json={"session_id": "s-poll2", "message": "想去泰国玩"}
    ).json()["task_id"]

    st = _poll_until_done(client, task_id)
    assert [s["stage"] for s in st["stages"]] == ["clarify", "output"]
    assert st["response"]["type"] == "clarify"
    assert store.search_calls == 0, "缺槽时不得触发检索（验收 1）"


def test_plan_progress_404s_on_unknown_task(client):
    """查一个不存在的任务必须给 404，而不是静默返回空进度 —— 页面靠它回退旧接口。"""
    resp = client.get("/plan/progress/nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------- embedding 降级
def test_embedding_dim_matches_schema_column(deps):
    """无密钥路径（hash embedding）的维度必须与建表语句的 vector(N) 一致。

    真实踩过：`ecnu-embedding-small` 返回 1024 维，而当时 schema 写的是 vector(512)。
    这条断言把「换 embedding 模型」和「改建表维度」两个动作绑在一起 ——
    只改一个就会在写入时炸，且报错信息看不出是配置问题。
    """
    import re
    from pathlib import Path

    sql = (Path(deps.settings.routes_path).parent / "docker" / "init" / "01_schema.sql").read_text(encoding="utf-8")
    dims = {int(d) for d in re.findall(r"vector\((\d+)\)", sql)}

    assert dims, "建表语句里找不到 vector(N)"
    assert HashingEmbedder(deps.settings.embedding.dim).dim in dims, (
        f"hash 默认维 {deps.settings.embedding.dim} 与 schema 的 {dims} 不一致"
    )


def test_embedder_warns_when_model_is_configured_without_key(caplog):
    """配了模型却没配 Key → 降级为 hash，必须留告警，不能一声不响。"""
    with caplog.at_level(logging.WARNING, logger="travel"):
        embedder = build_embedder(EmbeddingSettings(model="text-embedding-3-small", dim=512, api_key=""))

    assert isinstance(embedder, HashingEmbedder)
    assert any("TRAVEL_EMBEDDING_API_KEY" in rec.getMessage() for rec in caplog.records)


def test_embedder_is_silent_when_model_and_key_are_present(caplog):
    """配置齐全时不告警，否则告警会被噪音淹没。"""
    with caplog.at_level(logging.WARNING, logger="travel"):
        embedder = build_embedder(EmbeddingSettings(model="text-embedding-3-small", dim=512, api_key="k"))

    assert isinstance(embedder, APIEmbedder)
    assert not caplog.records


class _StubEmbeddingResponse:
    def __init__(self, dim: int, count: int) -> None:
        self._dim, self._count = dim, count

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"data": [{"index": i, "embedding": [float(i)] * self._dim} for i in range(self._count)]}


def _install_embedding_stub(monkeypatch, dim: int) -> list[int]:
    """把 httpx.post 换成本地桩，返回「每次调用的 input 长度」列表。"""
    calls: list[int] = []

    def fake_post(url, **kwargs):
        size = len(kwargs["json"]["input"])
        calls.append(size)
        return _StubEmbeddingResponse(dim, size)

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_api_embedder_rejects_dim_mismatch_early(monkeypatch):
    """API 实际维度与 TRAVEL_EMBEDDING_DIM 不符时立刻报错，别拖到写库那一步。

    真实踩过：`ecnu-embedding-small` 返回 1024 维，而当时 schema 是 vector(512)。
    若不在这一层拦住，错误会在 pgvector 写入时以「列类型不符」的形式出现，
    看不出是 embedding 配置的问题。
    """
    _install_embedding_stub(monkeypatch, 1024)

    embedder = APIEmbedder(EmbeddingSettings(model="ecnu-embedding-small", dim=512, api_key="k"))
    with pytest.raises(EmbeddingDimMismatch) as excinfo:
        embedder.embed(["带孩子穷游厦门"])

    message = str(excinfo.value)
    assert "1024" in message and "512" in message


def test_api_embedder_preserves_order_and_dim(monkeypatch):
    _install_embedding_stub(monkeypatch, 512)

    embedder = APIEmbedder(EmbeddingSettings(model="m", dim=512, api_key="k"))
    vectors = embedder.embed(["a", "b"])

    assert [v[0] for v in vectors] == [0.0, 1.0], "返回顺序必须与输入一致"
    assert all(len(v) == 512 for v in vectors)


def test_api_embedder_splits_long_input_into_batches(monkeypatch):
    """端点单次 input 有上限（实测 ecnu-embedding-small：32 条通过、64 条 HTTP 500），
    种子库 130+ 条一次调用必炸，必须客户端分批，且分批不能打乱顺序。"""
    calls = _install_embedding_stub(monkeypatch, 512)

    embedder = APIEmbedder(EmbeddingSettings(model="m", dim=512, api_key="k", batch=16))
    vectors = embedder.embed([f"t{i}" for i in range(40)])

    assert calls == [16, 16, 8], f"分批大小不对：{calls}"
    assert len(vectors) == 40, "分批后条数必须与输入一致"
    assert [v[0] for v in vectors] == [float(i % 16) for i in range(40)], "批次拼接后顺序错乱"


def test_build_store_reports_dim_mismatch_without_blaming_docker(deps, monkeypatch, caplog):
    """维度不一致时仍回退内存库（服务不砸），但告警必须指向配置而不是 docker。"""
    import app.store as store_mod

    class _ExplodingStore:
        def __init__(self, *args, **kwargs):
            raise EmbeddingDimMismatch("embedding 模型 'ecnu-embedding-small' 返回 1024 维，但 TRAVEL_EMBEDDING_DIM=512")

    monkeypatch.setattr(store_mod, "PgVectorStore", _ExplodingStore)
    settings = replace(deps.settings, vector_backend="pg")

    with caplog.at_level(logging.WARNING, logger="travel"):
        store = store_mod.build_store(settings, deps.embedder)

    assert isinstance(store, InMemoryVectorStore)
    message = " ".join(rec.getMessage() for rec in caplog.records)
    assert "维度与向量表结构不一致" in message
    assert "docker compose up -d" not in message, "配置错不该把人引去查 docker"


# ---------------------------------------------------------------- 检索 scope 通配
def test_scope_wildcard_matches_whole_layer(store):
    """`scene:*` = 该层的全部场景，且不得越层（realtime / general 都不该被它放进）。"""
    from app.store import _scope_match

    chunks = store.scan(filters={}, limit=500)
    scene_chunk = next(c for c in chunks if c.layer == "scene")
    general_chunk = next(c for c in chunks if c.layer == "general")
    realtime_chunk = next(c for c in chunks if c.layer == "realtime")

    assert _scope_match(scene_chunk, ["scene:*"])
    assert not _scope_match(general_chunk, ["scene:*"])
    assert not _scope_match(realtime_chunk, ["scene:*"])
    # 精确 scope 的语义不变
    assert _scope_match(scene_chunk, [f"scene:{scene_chunk.scene}"])
    other_scene = next(c for c in chunks if c.layer == "scene" and c.scene != scene_chunk.scene)
    assert not _scope_match(other_scene, [f"scene:{scene_chunk.scene}"])


def test_general_scene_can_reach_the_scene_layer(deps):
    """普通请求（general 场景）必须能召回 scene 层内容。

    种子里厦门 12 条落在 scene 层、只有 3 条在 general 层；只查 general 层会让
    最常见的请求拿到最少的知识 —— 层配额里给 scene 留的 0.5 也就永远空着。
    """
    from app.retrieve import retrieve_context
    from app.router import build_route
    from app.schemas import SceneClassificationResult, SceneLabel
    from conftest import FROZEN_TODAY

    classification = SceneClassificationResult(
        request_id="r",
        labels=[SceneLabel(scene_id="general", confidence=0.9)],
        primary_scene="general",
        slots={"destination": "厦门", "destination_country": "中国"},
        missing_slots=[],
        rewritten_query="厦门 5 天行程",
        classifier_version="t",
    )
    route = build_route(settings=deps.settings, classification=classification)
    bundle = retrieve_context(
        settings=deps.settings,
        store=deps.store,
        embedder=deps.embedder,
        route=route,
        query=classification.rewritten_query,
        slots=classification.slots,
        today=FROZEN_TODAY,
    )
    layers = {c.layer for c in bundle.context.chunks}
    assert "scene" in layers, f"general 场景没召回到 scene 层：{layers}"
    assert len(bundle.context.chunks) > 3, f"召回条数过少：{len(bundle.context.chunks)}"
