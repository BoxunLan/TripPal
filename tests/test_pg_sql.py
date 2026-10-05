"""pgvector 后端的 SQL 校验（不需要真实数据库）。

本机没有 docker，无法端到端跑 pgvector。但这里最容易出错的地方不是 SQL 语义，
而是**占位符与参数的顺序/数量错位** —— `search` 的 SQL 里 `%s::vector` 出现两次，
中间夹着 WHERE 的参数，顺序错了不会报错，只会静默返回错误的召回。
所以用桩连接把生成的 SQL 与参数抓下来逐项核对。
"""

from __future__ import annotations

import pytest

from app.embed import HashingEmbedder
from app.store import PgVectorStore


class _Cur:
    def __init__(self, log: list) -> None:
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, list(params or [])))

    def fetchall(self):
        return []

    def fetchone(self):
        return (0,)


class _Conn:
    def __init__(self) -> None:
        self.log: list[tuple[str, list]] = []

    def cursor(self):
        return _Cur(self.log)


@pytest.fixture
def pg_store():
    store = object.__new__(PgVectorStore)  # 跳过 __init__，避免真的连库
    store.dsn = "postgresql://unused"
    store.dim = 512
    store.embedder = HashingEmbedder(512)
    store._conn = _Conn()
    return store


def test_search_placeholders_match_param_count(pg_store):
    vector = [0.1] * 512
    pg_store.search(
        vector,
        filters={"scopes": ["scene:family", "general"], "destinations": ["中国", "厦门"], "kinds": ["poi"]},
        top_k=7,
    )
    sql, params = pg_store._conn.log[-1]
    assert sql.count("%s") == len(params), (sql, params)
    # 首个参数是向量字面量，最后一个是 limit
    assert params[0].startswith("[") and params[0].endswith("]")
    assert params[-1] == 7
    # WHERE 参数介于两次 vector 占位之间：索引 1..n-2
    assert any("ILIKE" in sql for _ in [0])
    assert params[1:-1], "元数据预过滤参数不能为空"


def test_search_sql_uses_cosine_operator_and_metadata_prefilter(pg_store):
    pg_store.search([0.0] * 512, filters={"scopes": ["realtime:visa"]}, top_k=3)
    sql, _ = pg_store._conn.log[-1]
    assert "<=>" in sql, "向量召回必须走余弦距离算子"
    assert "layer = 'realtime'" in sql and "scene = %s" in sql
    assert "ORDER BY embedding <=> %s::vector" in sql


def test_scan_placeholders_match_param_count(pg_store):
    pg_store.scan(filters={"scopes": ["scene:budget"], "destinations": ["清迈"]}, limit=50)
    sql, params = pg_store._conn.log[-1]
    assert sql.count("%s") == len(params)
    assert params[-1] == 50
    assert "embedding" not in sql, "scan 用于工具取数，不必拉向量"


def test_empty_filters_produce_no_where_clause(pg_store):
    pg_store.search([0.0] * 512, filters={}, top_k=3)
    sql, params = pg_store._conn.log[-1]
    assert "WHERE" not in sql
    assert sql.count("%s") == len(params) == 3  # vec + ORDER BY vec + limit


def test_scope_filter_covers_all_three_layers(pg_store):
    pg_store.search(
        [0.0] * 512,
        filters={"scopes": ["general", "scene:visa", "realtime:visa"]},
        top_k=3,
    )
    sql, params = pg_store._conn.log[-1]
    assert "layer = 'general'" in sql
    assert "layer = 'scene' AND scene = %s" in sql
    assert "layer = 'realtime' AND scene = %s" in sql
    assert "visa" in params


def test_wildcard_scope_expands_to_the_whole_layer(pg_store):
    """`scene:*` 是 general 场景用的整层通配 —— 语义必须与内存侧 _scope_match 一致。"""
    pg_store.search([0.0] * 512, filters={"scopes": ["general", "scene:*"]}, top_k=5)
    sql, params = pg_store._conn.log[-1]
    assert sql.count("%s") == len(params)
    assert "layer = 'general'" in sql
    assert "layer = %s" in sql
    assert "*" not in sql, "通配必须在构造 SQL 时就展开成层名，不能把 * 当场景名交给数据库"
    assert "scene" in params
    assert "general" not in params, "general 走字面量分支，不该占参数位"
