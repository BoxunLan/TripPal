"""A 线 CLI。

    python -m demand_task_factory build --input <jsonl> --config <yaml> --out <dir>
    python -m demand_task_factory import-snapshot --raw <csv> ...
    python -m demand_task_factory import-trippal --trippal <dir> --out <dir> ...
    python -m demand_task_factory validate --schema <schema.json> --data <file>

`build` 加 `--scenarios <scenarios.json>` 时，场景边界用导入的策展目录，不再做聚类
（见 integration/deviations.jsonl 的 dev-008）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import build as build_mod
from . import contracts
from . import snapshot as snapshot_mod
from . import trippal as trippal_mod

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROUTING = REPO_ROOT / "fixtures" / "trippal_v1" / "scenario_routing.json"


def _default_trippal_root() -> Path:
    """Locate the TripPal corpus root (the directory that contains `research/`).

    Two layouts are supported so the same command works both inside the TripPal
    repo (this module lives at `<repo>/skill-loop/modules/...`) and inside the
    standalone skill-loop checkout:

    1. `<REPO_ROOT>/..`          -> skill-loop/ is a sibling of research/
    2. `<REPO_ROOT>/../TripPal-main` -> keeps working when skill-loop/ is
       checked out next to a separate TripPal-main/ directory

    Callers can always override with `--trippal`.
    """
    candidates = [REPO_ROOT.parent, REPO_ROOT.parent / "TripPal-main"]
    for c in candidates:
        if (c / "research" / "pain_points.md").exists():
            return c
    return candidates[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m demand_task_factory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build")
    p_build.add_argument("--input", required=True)
    p_build.add_argument("--config", required=True)
    p_build.add_argument("--out", required=True)
    p_build.add_argument(
        "--scenarios",
        default="",
        help="导入的场景目录（scenarios.json）。给了就用它当场景边界，跳过聚类",
    )

    p_snap = sub.add_parser("import-snapshot")
    p_snap.add_argument("--raw", required=True)
    p_snap.add_argument("--geo", required=True)
    p_snap.add_argument("--language", required=True)
    p_snap.add_argument("--time-window", required=True)
    p_snap.add_argument("--retrieved-at", required=True)
    p_snap.add_argument("--out", required=True)

    p_tp = sub.add_parser("import-trippal")
    p_tp.add_argument(
        "--trippal",
        default=None,
        help="含 research/ 的 TripPal 仓根目录（默认自动探测 <REPO_ROOT>/..）",
    )
    p_tp.add_argument("--routing", default=str(DEFAULT_ROUTING))
    p_tp.add_argument("--out", required=True, help="scenarios.json + import_report.json 的目录")
    p_tp.add_argument("--records", required=True, help="产出的 demand records jsonl")
    p_tp.add_argument("--tool-data", required=True, help="产出的 lookup_local 页面夹具")
    p_tp.add_argument("--time-window", default="2025-09-29/2026-09-29")
    p_tp.add_argument("--retrieved-at", default="2026-09-29T00:00:00+08:00")
    p_tp.add_argument("--top-k", type=int, default=6)
    p_tp.add_argument("--scenario-target", type=int, default=8)

    p_val = sub.add_parser("validate")
    p_val.add_argument("--schema", required=True)
    p_val.add_argument("--data", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "build":
        return build_mod.build(args.input, args.config, args.out, scenarios_path=args.scenarios or None)
    if args.cmd == "import-snapshot":
        return snapshot_mod.import_snapshot(
            args.raw, args.geo, args.language, args.time_window, args.retrieved_at, args.out
        )
    if args.cmd == "import-trippal":
        return trippal_mod.import_trippal(
            args.trippal or str(_default_trippal_root()),
            args.routing,
            args.out,
            args.records,
            args.tool_data,
            time_window=args.time_window,
            retrieved_at=args.retrieved_at,
            top_k=args.top_k,
            scenario_target=args.scenario_target,
        )
    if args.cmd == "validate":
        return contracts.main(["--schema", args.schema, "--data", args.data])
    return 2


if __name__ == "__main__":
    sys.exit(main())
