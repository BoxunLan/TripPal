"""把 seed/ 下的 JSONL 灌进 pgvector。docker compose up 之后跑一次即可。

    python scripts/load_seed.py

幂等：chunk_id 冲突时覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.embed import build_embedder  # noqa: E402
from app.store import PgVectorStore, load_seed_dir  # noqa: E402


def main() -> int:
    settings = get_settings()
    chunks = load_seed_dir(settings.seed_dir)
    if not chunks:
        print(f"seed 目录为空：{settings.seed_dir}")
        return 1

    embedder = build_embedder(settings.embedding)
    store = PgVectorStore(settings.database_url, embedder)
    written = store.upsert(chunks)

    by_layer: dict[str, int] = {}
    for c in chunks:
        by_layer[c.layer] = by_layer.get(c.layer, 0) + 1
    print(f"写入 {written} 条，分库统计：{by_layer}")
    print(f"embedding={embedder.name} dim={embedder.dim}，表内总计 {store.count()} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
