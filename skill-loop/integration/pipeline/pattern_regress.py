"""按模式配对比较两批运行，抓"整体不退步、但某类能力崩掉"的回归。

为什么需要它（R8 实测）：
    闭环候选 SKILL（"没有来源就不给结论"）在 24 张子集上被 C 线回归门判为 `pass`
    （holdout pass@3 0.25 → 0.375、成本比 1.257 ≤ 1.5），但全量配对一跑：
    `V2B-already_holds_visa` 12/18 → **0/18**，整卡通过 15/56 → 5/56。
    根因是门只看**全局 holdout**：一个全局数字可以因为composition 变化而上升，
    同时某一整类能力（这里是"旅客自身状态 × 规则"的合并判断）已经崩掉。

用法：
    python integration/pipeline/pattern_regress.py \
        --baseline <baseline 运行根> --candidate <candidate 运行根> \
        --out <报告路径.md> [--min-cards 3] [--max-pass-drop 0.15]

    # 运行根的形态：<root>/<experiment>/<condition>/<task_id>/<run_n>/{verdict,artifacts/answer}.json
    # （即 run_v2_parallel.ps1 或 run_once.py 产出的目录）

退出码：0 = 没有超过阈值的按模式回归；1 = 有回归（CI 应视为失败）；2 = 输入不可用。

注意两条**会把「没查」读成「通过」**的口径，报告里都已显式写出：
1. 同一 task 的多次运行**取最好一次**（见 `load_runs`）—— 「3 次里挂 2 次」在表里显示为通过。
2. 模式张数 < `--min-cards` 时**不参与判定**，表里标 `skip` 而不是 `ok`。
   卡数少而模式碎的任务包（例如 16 张分属 16 个模式、各 1 张）会让整张表都是 `skip`：
   此时「没有发现回归」的真实含义是「**没有做判定**」。该情形会额外往 stderr 打一行警告。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PATTERN_SUFFIX = re.compile(r"-\d+$")


def pattern_of(task_id: str) -> str:
    """`V2B-already_holds_visa-07` -> `V2B-already_holds_visa`（去掉末尾序号）。"""
    return PATTERN_SUFFIX.sub("", task_id)


def load_runs(root: Path) -> dict[str, dict]:
    """收集 root 下所有 verdict.json，按 task_id 聚合。

    **聚合口径：同一 task 的多次运行取「最好一次」**（pass 优先，否则保留先见到的那条）。
    也就是说「3 次里挂 2 次」在表里会显示成通过 —— 本模块只看**模式级方向**，
    不能用来判断稳定性。`n_runs` 记下每个 task 实际见到几次运行，报告头会写出来，
    避免把「取最好」读成「稳定通过」。
    """
    runs: dict[str, dict] = {}
    for vp in sorted(root.rglob("verdict.json")):
        try:
            verdict = json.loads(vp.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        task_id = verdict.get("task_id") or vp.parent.parent.name
        passed = bool(verdict.get("overall_pass"))
        a = b = 0
        answer = vp.parent / "artifacts" / "answer.json"
        if answer.exists():
            try:
                asserts = json.loads(answer.read_text(encoding="utf-8")).get("assertions") or []
                a, b = sum(1 for x in asserts if x.get("passed")), len(asserts)
            except (ValueError, OSError):
                a = b = 0
        prev = runs.get(task_id)
        total = (prev["n_runs"] if prev else 0) + 1
        cand = {"pass": passed, "a": a, "b": b, "n_runs": total}
        if prev is None or (cand["pass"] and not prev["pass"]):
            runs[task_id] = cand
        else:
            prev["n_runs"] = total
    return runs


def group(runs: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for task_id, r in runs.items():
        g = out.setdefault(pattern_of(task_id), {"n": 0, "pass": 0, "a": 0, "b": 0, "runs": 0})
        g["n"] += 1
        g["pass"] += 1 if r["pass"] else 0
        g["a"] += r["a"]
        g["b"] += r["b"]
        g["runs"] += r.get("n_runs", 1)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="按模式配对比较两批运行，抓局部回归")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--min-cards", type=int, default=3, help="少于这么多张的模式不参与判定")
    ap.add_argument("--max-pass-drop", type=float, default=0.15, help="允许的整卡通过率下降幅度")
    args = ap.parse_args(argv)

    base_root, cand_root = Path(args.baseline), Path(args.candidate)
    if not base_root.exists() or not cand_root.exists():
        print(f"输入不可用：baseline={base_root} candidate={cand_root}", file=sys.stderr)
        return 2

    base, cand = load_runs(base_root), load_runs(cand_root)
    common = sorted(set(base) & set(cand))
    if not common:
        print("两批运行没有共同任务，无法配对", file=sys.stderr)
        return 2

    bg = group({k: base[k] for k in common})
    cg = group({k: cand[k] for k in common})

    base_runs = sum(r.get("n_runs", 1) for r in base.values())
    cand_runs = sum(r.get("n_runs", 1) for r in cand.values())
    lines = [
        "# 按模式回归报告",
        "",
        f"- 配对任务：**{len(common)}** 张"
        f"（baseline {len(base)} 张 / {base_runs} 次运行，candidate {len(cand)} 张 / {cand_runs} 次运行）",
        f"- 判定阈值：模式张数 ≥ {args.min_cards}，整卡通过率下降 > {args.max_pass_drop:.0%} 视为回归",
        "",
        "> **聚合口径**：同一 task 的多次运行取**最好一次**（见 `load_runs`）。"
        "所以「3 次里挂 2 次」在表里显示为通过 —— 本表只看**模式级方向**，不能用来判断稳定性。",
        "",
        "| 模式 | 张数 | 基线通过 | 候选通过 | Δ通过率（候选−基线） | 基线断言率 | 候选断言率 | 判定 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    regressions: list[tuple[str, float]] = []
    skipped: list[str] = []
    judged = 0
    for pat in sorted(bg):
        b, c = bg[pat], cg.get(pat)
        if c is None:
            continue
        n = min(b["n"], c["n"])
        br, cr = b["pass"] / b["n"], c["pass"] / c["n"]
        delta = cr - br          # 负数 = 候选更差
        bar = b["a"] / b["b"] if b["b"] else 0.0
        car = c["a"] / c["b"] if c["b"] else 0.0
        if n < args.min_cards:
            # 张数不足时**显式**标成 skip。旧版这里写 `ok`，与「查过且没问题」长得一模一样 ——
            # 一个 16 张、分属 16 个模式（各 1 张）的任务包会让整张表全 `ok`，
            # 读起来像「全面验证通过」，实际是**一次判定都没做**。
            # 实测就是这么被放过的：train 通过率掉了 14.3 pt，门仍判 `pass`。
            verdict = f"skip（张数 {n} < {args.min_cards}）"
            skipped.append(pat)
        elif delta < -args.max_pass_drop:
            verdict = "**REGRESSION**"
            regressions.append((pat, delta))
            judged += 1
        else:
            verdict = "ok"
            judged += 1
        lines.append(
            f"| `{pat}` | {n} | {b['pass']}/{b['n']} | {c['pass']}/{c['n']} | "
            f"{delta:+.0%} | {bar:.0%} | {car:.0%} | {verdict} |"
        )

    lines += ["", f"结论：{'**发现按模式回归**' if regressions else '没有超过阈值的按模式回归'}", ""]
    if skipped:
        skipped_cards = sum(min(bg[p]["n"], cg[p]["n"]) for p in skipped)
        shown = "、".join(f"`{p}`" for p in skipped[:12])
        lines.append(
            f"> ⚠️ **{len(skipped)} 个模式因张数不足未参与判定**（合计 {skipped_cards} 张）："
            f"{shown}{'…' if len(skipped) > 12 else ''}"
        )
        lines.append(
            "> 这些模式**一条都没查**。如果它们占了总卡数的多数，"
            "「没有发现回归」的真实含义是「**没有做判定**」，不是「验证通过」。"
        )
        lines.append("")
    if regressions:
        lines.append("回归模式：" + "、".join(f"`{p}`（{d:+.0%}）" for p, d in regressions))
        lines.append("")
        lines.append(
            "> 注意：全局 holdout pass@3 可能同时是上升的（R8 实测就是这个情形）。"
            "回归门必须同时看这一张表，否则会把整类能力的崩塌放过去。"
        )

    if judged == 0:
        # 退出码保持 0/1 不变（编排脚本按它分流），但必须让人在 stderr 看到
        # 「这次没有任何模式达标」—— 否则 0 会被读成「验证通过」。
        print(
            f"警告：没有任何模式达到 min-cards={args.min_cards}，本报告**未做任何判定**",
            file=sys.stderr,
            flush=True,
        )

    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
