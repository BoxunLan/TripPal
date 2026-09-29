"""快照导入：把手工导出的 Trends CSV 变成 demand-record.jsonl。

规格：`raw.csv` 只认表头 `query,metric_type,metric_value`。缺文件时退出码 2，
并写 blocker `integration/blockers/missing-trends-snapshot.md`。
不要自己生成看起来像真数据的行。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from . import contracts

HEADER = ["query", "metric_type", "metric_value"]
BLOCKER = "integration/blockers/missing-trends-snapshot.md"
ALLOWED_METRICS = {"relative_interest", "rising_rate"}


def blocker_text(raw: str) -> str:
    return (
        "# blocker: missing-trends-snapshot\n\n"
        f"`import-snapshot` 找不到原始快照文件 `{raw}`。\n\n"
        "按规格弹性规则 E1，A2（真实快照导入）标为跳过，A1 仍须用夹具通过。\n"
        "**不得自行编造看起来像真数据的需求记录行。**\n\n"
        "补上快照的办法：在浏览器里打开 Google Trends，手工导出 CSV，"
        f"放到 `{raw}`，表头必须是 `query,metric_type,metric_value`。\n"
    )


def import_snapshot(raw, geo, language, time_window, retrieved_at, out_path) -> int:
    src = Path(raw)
    if not src.exists():
        blocker = Path(BLOCKER)
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text(blocker_text(raw), encoding="utf-8")
        sys.stderr.write(
            json.dumps(
                [{"path": str(src), "message": "原始快照文件不存在，已写 blocker"}],
                ensure_ascii=False,
            )
            + "\n"
        )
        return 2

    rows = list(csv.DictReader(src.read_text(encoding="utf-8").splitlines()))
    if not rows:
        sys.stderr.write(json.dumps([{"path": str(src), "message": "CSV 没有数据行"}], ensure_ascii=False) + "\n")
        return 2
    header = list(rows[0].keys())
    if header != HEADER:
        sys.stderr.write(
            json.dumps(
                [{"path": str(src), "message": f"表头必须是 {HEADER}，实际是 {header}"}],
                ensure_ascii=False,
            )
            + "\n"
        )
        return 2

    records, errors = [], []
    for i, row in enumerate(rows, start=1):
        metric_type = (row.get("metric_type") or "").strip()
        try:
            metric_value = float(row.get("metric_value") or "")
        except ValueError:
            errors.append({"line": i, "record_id": "", "message": "metric_value 不是数字"})
            continue
        if metric_type not in ALLOWED_METRICS:
            errors.append(
                {
                    "line": i,
                    "record_id": "",
                    "message": f"metric_type={metric_type} 不在 {sorted(ALLOWED_METRICS)} 内",
                }
            )
            continue
        records.append(
            {
                "schema_version": "v1",
                "record_id": f"snap-{i:03d}",
                "source": "google_trends",
                "query_or_topic": (row.get("query") or "").strip(),
                "geo": geo,
                "language": language,
                "time_window": time_window,
                "metric_type": metric_type,
                "metric_value": metric_value,
                "access_scope": "public_aggregate",
                "retrieved_at": retrieved_at,
            }
        )

    bad = [(i, r) for i, r in enumerate(records, start=1) if not contracts.is_valid("demand-record", r)]
    if bad:
        sys.stderr.write(
            json.dumps([{"path": f"line{i}", "message": "生成的行不符合 demand-record v1"} for i, _ in bad], ensure_ascii=False)
            + "\n"
        )
        return 2

    contracts.write_jsonl(out_path, records)
    sys.stderr.write(json.dumps(errors, ensure_ascii=False) + "\n")
    return 0
