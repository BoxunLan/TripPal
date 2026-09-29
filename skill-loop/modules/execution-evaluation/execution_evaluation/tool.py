"""唯一的工具：lookup_local(query)。读夹具里的 pages.json，不加任何外部依赖。"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PAGES = REPO_ROOT / "fixtures" / "tool_data_trippal_v1" / "pages.json"
EMPTY = {"text": "", "source_url": "", "effective_date": ""}


def lookup_local(query: str, pages_path: str | Path | None = None) -> dict:
    p = Path(pages_path) if pages_path else DEFAULT_PAGES
    if not p.exists():
        return dict(EMPTY)
    data = json.loads(p.read_text(encoding="utf-8"))
    hit = data.get(query)
    if not hit:
        return dict(EMPTY)
    return {
        "text": hit.get("text", ""),
        "source_url": hit.get("source_url", ""),
        "effective_date": hit.get("effective_date", ""),
    }


def has_effective_source(result: dict) -> bool:
    return bool(result.get("source_url")) and bool(result.get("effective_date"))
