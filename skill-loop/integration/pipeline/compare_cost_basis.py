"""在同一份 summary 上并列三个成本口径，看「换个口径，结论会不会翻」。

不重跑模型、不改仓库状态：每次调用 `skill_optimizer regress` 都用临时目录做 `--out`，
而 rollback 只会写 `<out 的父目录>/candidate_skill/SKILL.md`，所以落在临时目录里。

用法（仓库根）：

    .venv/Scripts/python.exe integration/pipeline/compare_cost_basis.py \
        --baseline evals/runs/live-baseline/baseline_summary.json \
        --candidate evals/runs/live-candidate/candidate_summary.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASES = ("total", "output", "delta")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="compare_cost_basis")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--delta-allowance", type=int, default=400)
    args = ap.parse_args(argv)

    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    cand = json.loads(Path(args.candidate).read_text(encoding="utf-8"))

    print(f"baseline : {args.baseline}")
    print(f"candidate: {args.candidate}")
    print()
    print("| 口径 | baseline 读数 | candidate 读数 | 比值 / 增量 | 门限 | conclusion |")
    print("|---|---|---|---|---|---|")

    results = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for basis in BASES:
            d = tmp / basis
            d.mkdir()
            cfg = d / "cfg.yaml"
            cfg.write_text(
                f"cost_budget_basis: {basis}\ncost_delta_allowance: {args.delta_allowance}\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["COST_BUDGET_BASIS"] = ""  # 别让外层环境变量盖掉这次显式指定的口径
            r = subprocess.run(
                [
                    sys.executable, "-m", "skill_optimizer", "regress",
                    "--baseline", str(Path(args.baseline).resolve()),
                    "--candidate", str(Path(args.candidate).resolve()),
                    "--config", str(cfg),
                    "--out", str(d / "evaluation_report.md"),
                ],
                cwd=REPO, capture_output=True, text=True, encoding="utf-8", env=env,
            )
            if r.returncode != 0:
                print(f"| `{basis}` | — | — | — | — | 退出码 {r.returncode}：{r.stderr.strip()[:120]} |")
                continue
            rep = json.loads((d / "evaluation_report.json").read_text(encoding="utf-8"))
            cb = rep["cost_budget"]
            if basis == "total":
                b, c, v, limit = base.get("avg_tokens"), cand.get("avg_tokens"), cb["total_ratio"], rep["cost_budget_ratio"]
            elif basis == "output":
                b, c, v, limit = (
                    base.get("avg_output_tokens"), cand.get("avg_output_tokens"),
                    cb["output_ratio"], rep["cost_budget_ratio"],
                )
            else:
                b, c, v, limit = (
                    base.get("avg_tokens"), cand.get("avg_tokens"),
                    cb["delta_total_tokens"], cb["delta_allowance"],
                )
            unit = " tok" if basis == "delta" else ""
            print(f"| `{basis}` | {b}{unit} | {c}{unit} | {v} | ≤ {limit} | **{rep['conclusion']}** |")
            results.append((basis, rep["conclusion"]))

    print()
    passed = [b for b, c in results if c == "pass"]
    if len({c for _, c in results}) == 1:
        print(f"三个口径结论一致：{results[0][1]} —— 口径不是这次结论的瓶颈。")
    else:
        print("口径不同结论不同：" + "、".join(f"{b}={c}" for b, c in results))
        print(
            f"放行的口径：{'、'.join(passed) if passed else '无'}"
            " —— 判定口径该不该改，就是判定「技能自身的固定开销」算不算成本、"
            "以及成本该按工作量还是按总量收。"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
