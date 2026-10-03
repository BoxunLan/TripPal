"""B 线 CLI。

    python -m execution_evaluation run --task-pack <jsonl> --condition B-no-skill \
        --runs 3 --experiment-id <id> --out evals/runs
    python -m execution_evaluation summarize --experiment-id <id> --out <report.md>
    python -m execution_evaluation validate --schema <schema.json> --data <file>
"""

from __future__ import annotations

import argparse
import sys

from . import contracts, runner, summarize as summarize_mod


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m execution_evaluation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--task-pack", required=True)
    p_run.add_argument("--condition", required=True)
    p_run.add_argument("--runs", type=int, required=True)
    p_run.add_argument("--experiment-id", required=True)
    p_run.add_argument("--out", required=True)
    p_run.add_argument(
        "--skill",
        default=None,
        help="SKILL 文件或目录；传目录时 SKILL.md 与 references/** 会按序一并注入",
    )
    p_run.add_argument("--config", default=None)
    p_run.add_argument(
        "--task-pack-sha256-full",
        default=None,
        help="完整数据集包的 sha256，用于跨臂判断是否同一任务集（--task-pack 是切片时必传）",
    )

    p_sum = sub.add_parser("summarize")
    p_sum.add_argument("--experiment-id", required=True)
    p_sum.add_argument("--out", required=True)
    p_sum.add_argument("--run-root", default="evals/runs")

    p_val = sub.add_parser("validate")
    p_val.add_argument("--schema", required=True)
    p_val.add_argument("--data", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "run":
        return runner.run(
            args.task_pack,
            args.condition,
            args.runs,
            args.experiment_id,
            args.out,
            skill=args.skill,
            config_path=args.config,
            task_pack_sha256_full=args.task_pack_sha256_full,
        )
    if args.cmd == "summarize":
        return summarize_mod.summarize(args.experiment_id, args.out, args.run_root)
    if args.cmd == "validate":
        return contracts.main(["--schema", args.schema, "--data", args.data])
    return 2


if __name__ == "__main__":
    sys.exit(main())
