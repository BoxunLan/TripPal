"""向量库：内存实现（测试/离线）与 pgvector 实现（docker compose 起的那套）。"""

from __future__ import annotations

import json
import logging
import math
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Protocol

from .embed import Embedder, EmbeddingDimMismatch
from .schemas import Chunk, ScoredChunk

logger = logging.getLogger("travel")

LAYER_FILE_MAP = {
    "general.jsonl": ("general", "general"),
    "scene_family.jsonl": ("scene", "family"),
    "scene_budget.jsonl": ("scene", "budget"),
    "scene_roadtrip.jsonl": ("scene", "roadtrip"),
    "scene_visa.jsonl": ("scene", "visa"),
    "realtime.jsonl": ("realtime", "visa"),
}


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def loads_chunk(raw: dict[str, Any], *, layer: str, scene: str) -> Chunk:
    return Chunk(
        chunk_id=raw["chunk_id"],
        layer=raw.get("layer") or layer,  # type: ignore[arg-type]
        scene=raw.get("scene") or scene,
        destination=raw.get("destination") or "",
        text=raw["text"],
        source=raw.get("source") or "",
        source_url=raw.get("source_url"),
        effective_date=_as_date(raw.get("effective_date")),
        fresh_until=_as_date(raw.get("fresh_until")),
        metadata=raw.get("metadata") or {},
    )


def load_seed_dir(seed_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for fname, (layer, scene) in LAYER_FILE_MAP.items():
        path = seed_dir / fname
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            chunks.append(loads_chunk(json.loads(line), layer=layer, scene=scene))
    return chunks


class VectorStore(Protocol):
    def search(self, vector: list[float], *, filters: dict[str, Any], top_k: int) -> list[ScoredChunk]: ...
    def scan(self, *, filters: dict[str, Any], limit: int = 200) -> list[Chunk]: ...
    def count(self) -> int: ...


def _scope_match(chunk: Chunk, scopes: Iterable[str]) -> bool:
    for scope in scopes:
        if scope == "general" and chunk.layer == "general":
            return True
        # `scene:*` / `realtime:*` = 该层的全部场景。general 场景需要它：
        # 绝大多数目的地知识落在 scene 层，只查 general 层会让最普通的请求拿到最少的知识。
        if scope.endswith(":*") and chunk.layer == scope.split(":", 1)[0]:
            return True
        if scope.startswith("scene:") and chunk.layer == "scene" and chunk.scene == scope.split(":", 1)[1]:
            return True
        if scope.startswith("realtime:") and chunk.layer == "realtime" and chunk.scene == scope.split(":", 1)[1]:
            return True
    return False


def match_filters(chunk: Chunk, filters: dict[str, Any]) -> bool:
    scopes = filters.get("scopes")
    if scopes and not _scope_match(chunk, scopes):
        return False
    destinations = filters.get("destinations")
    if destinations:
        tokens = set()
        for d in destinations:
            tokens.update(str(d).replace(",", " ").split())
        cd = set((chunk.destination or "").replace(",", " ").split())
        if cd and not (cd & tokens):
            return False
    kinds = filters.get("kinds")
    if kinds and str(chunk.metadata.get("kind") or "") not in set(kinds):
        return False
    return True


class InMemoryVectorStore:
    """测试与离线模式用。数据量在种子库规模（百条）时性能足够。"""

    name = "memory"

    def __init__(self, chunks: list[Chunk], embedder: Embedder) -> None:
        self.embedder = embedder
        self.chunks = list(chunks)
        self._vectors: list[list[float]] = embedder.embed([c.text for c in self.chunks]) if self.chunks else []

    @classmethod
    def from_seed_dir(cls, seed_dir: Path, embedder: Embedder) -> "InMemoryVectorStore":
        return cls(load_seed_dir(seed_dir), embedder)

    def count(self) -> int:
        return len(self.chunks)

    def scan(self, *, filters: dict[str, Any], limit: int = 200) -> list[Chunk]:
        out = [c for c in self.chunks if match_filters(c, filters)]
        return out[:limit]

    def search(self, vector: list[float], *, filters: dict[str, Any], top_k: int) -> list[ScoredChunk]:
        scored: list[ScoredChunk] = []
        for chunk, vec in zip(self.chunks, self._vectors):
            if not match_filters(chunk, filters):
                continue
            scored.append(ScoredChunk(chunk=chunk, score=_cosine(vector, vec)))
        scored.sort(key=lambda s: (-s.score, s.chunk.chunk_id))
        return scored[:top_k]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class PgVectorStore:
    """PostgreSQL + pgvector。元数据预过滤下推到 SQL，向量距离走 <=> 余弦算子。"""

    name = "pg"

    def __init__(self, dsn: str, embedder: Embedder) -> None:
        import psycopg  # 延迟导入，离线模式不需要装

        self.dsn = dsn
        self.embedder = embedder
        self.dim = embedder.dim
        self._conn = psycopg.connect(dsn, autocommit=True)

    def _cursor(self):
        return self._conn.cursor()

    def count(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks")
            return int(cur.fetchone()[0])

    def upsert(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        vectors = self.embedder.embed([c.text for c in chunks])
        rows = [
            (
                c.chunk_id,
                c.layer,
                c.scene,
                c.destination,
                c.text,
                c.source,
                c.source_url,
                c.effective_date,
                c.fresh_until,
                json.dumps(c.metadata, ensure_ascii=False),
                _to_pgvector(v),
            )
            for c, v in zip(chunks, vectors)
        ]
        sql = """
            INSERT INTO chunks (chunk_id, layer, scene, destination, text, source, source_url,
                                effective_date, fresh_until, metadata, embedding)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
            ON CONFLICT (chunk_id) DO UPDATE SET
                layer = EXCLUDED.layer, scene = EXCLUDED.scene, destination = EXCLUDED.destination,
                text = EXCLUDED.text, source = EXCLUDED.source, source_url = EXCLUDED.source_url,
                effective_date = EXCLUDED.effective_date, fresh_until = EXCLUDED.fresh_until,
                metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding
        """
        with self._cursor() as cur:
            cur.executemany(sql, rows)
        return len(rows)

    def _where(self, filters: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        scopes = filters.get("scopes") or []
        if scopes:
            ors = []
            for scope in scopes:
                if scope == "general":
                    ors.append("layer = 'general'")
                # `scene:*` / `realtime:*`：整层不限场景。语义必须与 _scope_match 等价。
                elif scope.endswith(":*"):
                    ors.append("layer = %s")
                    params.append(scope.split(":", 1)[0])
                elif scope.startswith("scene:"):
                    ors.append("(layer = 'scene' AND scene = %s)")
                    params.append(scope.split(":", 1)[1])
                elif scope.startswith("realtime:"):
                    ors.append("(layer = 'realtime' AND scene = %s)")
                    params.append(scope.split(":", 1)[1])
            if ors:
                clauses.append("(" + " OR ".join(ors) + ")")
        destinations = filters.get("destinations") or []
        tokens: set[str] = set()
        for d in destinations:
            tokens.update(str(d).replace(",", " ").split())
        if tokens:
            ors = ["destination = ''"]
            for tok in sorted(tokens):
                ors.append("destination ILIKE %s")
                params.append(f"%{tok}%")
            clauses.append("(" + " OR ".join(ors) + ")")
        kinds = filters.get("kinds") or []
        if kinds:
            clauses.append("(metadata->>'kind') = ANY(%s)")
            params.append(list(kinds))
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    def _row_to_chunk(self, row: tuple[Any, ...]) -> Chunk:
        return Chunk(
            chunk_id=row[0],
            layer=row[1],
            scene=row[2],
            destination=row[3] or "",
            text=row[4],
            source=row[5] or "",
            source_url=row[6],
            effective_date=row[7],
            fresh_until=row[8],
            metadata=row[9] or {},
        )

    def search(self, vector: list[float], *, filters: dict[str, Any], top_k: int) -> list[ScoredChunk]:
        where, params = self._where(filters)
        sql = (
            "SELECT chunk_id, layer, scene, destination, text, source, source_url, effective_date,"
            " fresh_until, metadata, 1 - (embedding <=> %s::vector) AS score"
            f" FROM chunks{where}"
            " ORDER BY embedding <=> %s::vector LIMIT %s"
        )
        vec = _to_pgvector(vector)
        with self._cursor() as cur:
            cur.execute(sql, [vec, *params, vec, top_k])
            return [ScoredChunk(chunk=self._row_to_chunk(r[:10]), score=float(r[10])) for r in cur.fetchall()]

    def scan(self, *, filters: dict[str, Any], limit: int = 200) -> list[Chunk]:
        where, params = self._where(filters)
        sql = (
            "SELECT chunk_id, layer, scene, destination, text, source, source_url, effective_date,"
            f" fresh_until, metadata FROM chunks{where} LIMIT %s"
        )
        with self._cursor() as cur:
            cur.execute(sql, [*params, limit])
            return [self._row_to_chunk(r) for r in cur.fetchall()]


def _to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def build_store(settings, embedder: Embedder):
    if (settings.vector_backend or "").lower() == "pg":
        try:
            store = PgVectorStore(settings.database_url, embedder)
            if store.count() == 0:
                # 表在但没数据：自动灌种子，免得 demo 一上来检索全空
                store.upsert(load_seed_dir(settings.seed_dir))
            return store
        except EmbeddingDimMismatch as exc:
            # 这不是「库连不上」，是 embedding 配置与表结构不符。分开报，避免
            # 被下面那句「pgvector 不可用」带偏到去查 docker。
            logger.warning(
                "embedding 维度与向量表结构不一致，pgvector 路径不可用，回退到内存向量库：%s",
                str(exc)[:300],
            )
        except Exception as exc:  # 数据库没起时不要砸掉整个服务，退到内存库
            logger.warning(
                "pgvector 不可用（%s），回退到内存向量库。请确认 `docker compose up -d` 已执行。",
                str(exc).splitlines()[0][:160],
            )
    return InMemoryVectorStore.from_seed_dir(settings.seed_dir, embedder)
