"""「知识库里的相关条目」的官方核实入口：数据形状 + 前后端契约。

用户的原话是「网址要可以直接在 web 端点击」。这条链上有两处会断，两处都要钉：
① 数据里没有 `source_url`（条目根本没网址）；
② 前端拿不到 —— 行程卡的 `citations` 字段名是 `source_url`，而 chip 只读 `url`。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.generate import assemble_citations
from app.schemas import Activity, Chunk, Citation, DayPlan, Itinerary, RetrievedChunk, RetrievedContext

WEB = Path(__file__).resolve().parent.parent / "web" / "index.html"
SEED_HOST_FLOOR = 58          # 低于这个数说明回填被回退了
INBOUND_URL_FLOOR = 48        # 来华条目里带官方入口的下限（回填后为 51/99）


# ---------------------------------------------------------------- 数据层
def _all_chunks(store):
    return store.scan(filters={}, limit=1000)


def test_every_seed_url_is_an_absolute_http_link(store):
    for c in _all_chunks(store):
        if not c.source_url:
            continue
        assert c.source_url.startswith(("http://", "https://")), (c.chunk_id, c.source_url)
        host = c.source_url.split("/")[2]
        assert "." in host, f"{c.chunk_id} 的链接没有主机名：{c.source_url}"


def test_official_entry_points_are_not_silently_dropped(store):
    """钉死几条：它们各自的官方入口是**实地探测可达**才写进去的。"""
    expected = {
        "gen-in-weather-001": "www.cma.gov.cn",       # 中国气象局
        "gen-in-holiday-002": "www.mct.gov.cn",       # 文化和旅游部
        "gen-cn-001": "www.12306.cn",                 # 中国铁路 12306
        "fam-cn-cd-001": "www.panda.org.cn",          # 成都大熊猫繁育研究基地
        "fam-xm-001": "www.xmstm.com.cn",             # 厦门科技馆
        "gen-cn-xm-001": "www.xmferry.com",           # 厦门轮渡
        "gen-in-shop-005": "www.12315.cn",            # 全国 12315 平台
        "rt-in-240h-001": "www.nia.gov.cn",           # 国家移民管理局
    }
    by_id = {c.chunk_id: c for c in _all_chunks(store)}
    for cid, host in expected.items():
        chunk = by_id.get(cid)
        assert chunk is not None, f"种子库里没有 {cid}"
        assert chunk.source_url and host in chunk.source_url, (cid, chunk.source_url)


def test_inbound_coverage_does_not_regress(store):
    chunks = _all_chunks(store)
    with_url = [c for c in chunks if c.source_url]
    assert len(with_url) >= SEED_HOST_FLOOR, f"带官方入口的条目只剩 {len(with_url)} 条"

    def is_inbound(c):
        dest = c.destination or ""
        cid = c.chunk_id
        return (
            "中国" in dest
            or "-cn" in cid
            or cid.startswith("gen-in")
            or any(city in dest for city in ("厦门", "成都", "青岛", "北京", "上海", "西安"))
        )

    inbound = [c for c in chunks if is_inbound(c)]
    n = sum(1 for c in inbound if c.source_url)
    assert n >= INBOUND_URL_FLOOR, f"来华条目里只有 {n}/{len(inbound)} 条带官方入口"


# ---------------------------------------------------------------- 后端 → 前端的契约
def test_citations_carry_source_url_through_to_the_response():
    """行程引用必须把 chunk 的 `source_url` 带到 `Citation` 上 —— 否则前端无从渲染。"""
    chunk = Chunk(
        chunk_id="gen-in-weather-001",
        layer="general",
        scene="general",
        destination="中国",
        text="几月来中国最舒服",
        source="在华旅行天气与季节整理",
        source_url="https://www.cma.gov.cn",
        fresh_until=date(2027, 3, 31),
    )
    context = RetrievedContext(
        query="q",
        filters={},
        chunks=[
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                layer="general",
                score=0.9,
                text=chunk.text,
                source=chunk.source,
                fresh_until=chunk.fresh_until,
                citation_key=chunk.chunk_id,
            )
        ],
        layer_quota={},
        dropped_stale=0,
        retriever_version="t",
    )
    itinerary = Itinerary(
        title="t",
        destination="北京",
        days=[
            DayPlan(
                day=1,
                activities=[
                    Activity(time="09:00", name="故宫", detail="", citation_key=chunk.chunk_id)
                ],
            )
        ],
    )
    cites = assemble_citations(
        itinerary=itinerary,
        context=context,
        chunk_index={chunk.chunk_id: chunk},
        tool_results={},
    )
    assert cites and isinstance(cites[0], Citation)
    assert cites[0].source_url == "https://www.cma.gov.cn"
    assert cites[0].source == chunk.source


# ---------------------------------------------------------------- 前端渲染
def _web() -> str:
    return WEB.read_text(encoding="utf-8")


def test_chip_accepts_both_field_names():
    """真 bug：行程卡 citations 用 `source_url`，chip 只读 `url` → 行程引用全军点不动。"""
    assert "c.url || c.source_url" in _web()


def test_activity_chips_resolve_through_the_citation_index():
    """逐日活动上的引用只有编号，要经索引换出来源与链接，否则永远是不可点的编号。"""
    html = _web()
    assert "citeIndex" in html
    assert "renderDays(it, citeIndex)" in html


def test_unlinked_entry_is_labelled_instead_of_silently_dead():
    """编辑整理类条目没有官方入口 —— 这种「点不动」必须写在明面上。"""
    html = _web()
    assert "nolink" in html
    assert "无官方入口可点" in html


def test_legend_explains_what_the_link_actually_is():
    """链接是「官方核实入口」，不是知识库原文；不说清就等于在骗点击。"""
    html = _web()
    assert "官方核实入口" in html
