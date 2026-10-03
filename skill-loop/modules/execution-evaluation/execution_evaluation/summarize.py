"""B 线：汇总。

口径（规格「B 线」第 6 条）：
- `pass_at_3` / `train_pass_at_3` / `holdout_pass_at_3` 的**分母是运行数**，
  且不含 `contaminated=true` 与 `live=false` 的运行；分母为 0 时写 `null`，markdown 写 `n/a`。
- `avg_tokens` = 纳入分母的每次运行「input_tokens + output_tokens」的算术平均。
- `avg_input_tokens` / `avg_output_tokens` = 上面那个和的分解。规格只钉了 `avg_tokens`，
  这两项是给回归门的替代成本口径留的读数（C 线默认仍用 `avg_tokens`，见 `regress.py`）。
- `examples` 只放指针，不复制轨迹正文。
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

CONDITION_B = "B-no-skill"
CONDITION_C = "C-seed-skill"


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load_runs(experiment_dir: Path) -> list[dict]:
    runs = []
    for vp in sorted(experiment_dir.rglob("verdict.json")):
        run_dir = vp.parent
        verdict = json.loads(vp.read_text(encoding="utf-8"))
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        task_path = run_dir / "task.json"
        task = json.loads(task_path.read_text(encoding="utf-8")) if task_path.exists() else {}
        in_tokens = 0
        out_tokens = 0
        trace_path = run_dir / "trace.jsonl"
        if trace_path.exists():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    ev = json.loads(line)
                    c = ev.get("cost", {})
                    in_tokens += int(c.get("input_tokens", 0))
                    out_tokens += int(c.get("output_tokens", 0))
        # 逐条断言结果（runner 已写进 artifacts/answer.json）：用于连续指标，
        # 不再只看"过没过"这个会翻转的布尔值。
        a_passed, a_total = 0, 0
        answer_path = run_dir / "artifacts" / "answer.json"
        if answer_path.exists():
            try:
                asserts = json.loads(answer_path.read_text(encoding="utf-8")).get("assertions") or []
                a_total = len(asserts)
                a_passed = sum(1 for a in asserts if a.get("passed"))
            except (ValueError, AttributeError):
                a_passed, a_total = 0, 0
        runs.append(
            {
                "condition": manifest.get("condition", run_dir.parents[1].name),
                "task_id": verdict.get("task_id", run_dir.parent.name),
                "run_n": verdict.get("run_n", int(run_dir.name) if run_dir.name.isdigit() else 1),
                "split": task.get("split", ""),
                "verdict": verdict,
                "tokens": in_tokens + out_tokens,
                "in_tokens": in_tokens,
                "out_tokens": out_tokens,
                "assertions_passed": a_passed,
                "assertions_total": a_total,
                "verdict_path": rel(vp),
            }
        )
    return runs


def eligible(runs: list[dict]) -> list[dict]:
    return [r for r in runs if not r["verdict"].get("contaminated") and r["verdict"].get("live")]


def rate(runs: list[dict], subset) -> float | None:
    picked = [r for r in runs if subset(r)]
    if not picked:
        return None
    return round(sum(1 for r in picked if r["verdict"].get("overall_pass")) / len(picked), 6)


def avg_tokens(runs: list[dict]) -> float | None:
    if not runs:
        return None
    return round(sum(r["tokens"] for r in runs) / len(runs), 3)


def _avg(runs: list[dict], key: str) -> float | None:
    if not runs:
        return None
    return round(sum(r[key] for r in runs) / len(runs), 3)


def mean_assertion_pass(runs: list[dict]) -> float | None:
    """连续指标：平均"断言通过率"（本卡通过的断言数 / 断言总数）。

    为什么需要它：实测同一张卡在同配置下重复三次，**布尔判定会翻转**
    （例如 `fail(6/7) → fail(6/7) → pass(7/7)`），而部分分逐次复现。
    `pass@3` 还会因方差虚高（实测 25% → 41.7%）。所以"离做对还差多远"是比
    "过没过"更稳、更适合衡量 SKILL 注入效果的量。
    """
    picked = [r for r in runs if r.get("assertions_total")]
    if not picked:
        return None
    return round(
        sum(r["assertions_passed"] / r["assertions_total"] for r in picked) / len(picked), 6
    )


def assertion_totals(runs: list[dict]) -> tuple[int, int]:
    return (
        sum(r.get("assertions_passed", 0) for r in runs),
        sum(r.get("assertions_total", 0) for r in runs),
    )


def avg_input_tokens(runs: list[dict]) -> float | None:
    return _avg(runs, "in_tokens")


def avg_output_tokens(runs: list[dict]) -> float | None:
    return _avg(runs, "out_tokens")


def pick_examples(runs: list[dict], pass_at_3: float | None) -> list[dict]:
    if not runs:
        return []
    fails = [r for r in runs if not r["verdict"].get("overall_pass")]
    passes = [r for r in runs if r["verdict"].get("overall_pass")]
    if pass_at_3 is None:
        return []
    if pass_at_3 in (0.0, 1.0):
        # E7：命中时正好 3 条（真实运行不足 3 条就把现有的全写上）。
        ordered = (fails + passes) if pass_at_3 == 0.0 else (passes + fails)
        return [pointer(r) for r in ordered[:3]]
    ordered = fails if fails else passes
    return [pointer(r) for r in ordered[:3]]


def pointer(r: dict) -> dict:
    return {
        "task_id": r["task_id"],
        "run_n": r["run_n"],
        "status": r["verdict"].get("status"),
        "attribution": r["verdict"].get("attribution"),
        "verdict_path": r["verdict_path"],
    }


def condition_summary(condition: str, runs: list[dict], experiment_id: str) -> dict:
    keep = eligible(runs)
    counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    for r in keep:
        key = r["verdict"].get("attribution", "")
        counts[key] = counts.get(key, 0) + 1
        # `failure_kind` 是 `attribution` 的细分（`execution_defect` → `http_402` /
        # `budget_exhausted` / `format_contract` …）。旧产物没有这个键，回退到 `attribution`，
        # 所以历史目录与新目录可以放在同一张表里比。
        kind = r["verdict"].get("failure_kind") or key
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    pass_at_3 = rate(keep, lambda r: True)
    return {
        "schema_version": "v1",
        "experiment_id": experiment_id,
        "condition": condition,
        "task_count": len({r["task_id"] for r in runs}),
        "pass_at_3": pass_at_3,
        "train_pass_at_3": rate(keep, lambda r: r["split"] == "train"),
        "holdout_pass_at_3": rate(keep, lambda r: r["split"] == "holdout"),
        "avg_tokens": avg_tokens(keep),
        "avg_input_tokens": avg_input_tokens(keep),
        "avg_output_tokens": avg_output_tokens(keep),
        "mean_assertion_pass": mean_assertion_pass(keep),
        "assertions_passed_total": assertion_totals(keep)[0],
        "assertions_total": assertion_totals(keep)[1],
        "attribution_counts": counts,
        "failure_kind_counts": kind_counts,
        "examples": pick_examples(keep, pass_at_3),
    }


def markdown(experiment_id: str, all_runs: list[dict], summaries: list[dict]) -> str:
    lines = [f"# {experiment_id} 汇总", ""]
    lines.append(f"运行总数（含未计入分母的）：{len(all_runs)}")
    lines.append("")

    def fmt(v):
        return "n/a" if v is None else v

    lines.append("| 指标 | " + " | ".join(s["condition"] for s in summaries) + " |")
    lines.append("|" + "---|" * (len(summaries) + 1))
    rows = [
        ("task_count", lambda s: s["task_count"]),
        ("pass_at_3", lambda s: fmt(s["pass_at_3"])),
        ("mean_assertion_pass（连续指标，比 pass@3 稳）", lambda s: fmt(s.get("mean_assertion_pass"))),
        ("assertions_passed/总断言数", lambda s: f"{s.get('assertions_passed_total')}/{s.get('assertions_total')}"),
        ("train_pass_at_3", lambda s: fmt(s["train_pass_at_3"])),
        ("holdout_pass_at_3", lambda s: fmt(s["holdout_pass_at_3"])),
        ("avg_tokens（平均 input_tokens+output_tokens）", lambda s: fmt(s["avg_tokens"])),
        ("avg_input_tokens", lambda s: fmt(s["avg_input_tokens"])),
        ("avg_output_tokens", lambda s: fmt(s["avg_output_tokens"])),
        ("attribution_counts（仅计入分母的运行）", lambda s: json.dumps(s["attribution_counts"], ensure_ascii=False)),
        (
            "failure_kind_counts（attribution 的细分：http_402 / budget_exhausted / format_contract …）",
            lambda s: json.dumps(s.get("failure_kind_counts") or {}, ensure_ascii=False),
        ),
    ]
    for name, getter in rows:
        lines.append("| " + name + " | " + " | ".join(str(getter(s)) for s in summaries) + " |")
    lines.append("")

    total_counts: dict[str, int] = {}
    for r in all_runs:
        key = r["verdict"].get("attribution", "")
        total_counts[key] = total_counts.get(key, 0) + 1
    lines.append(f"全部运行的归因分布（含 live=false / contaminated）：{json.dumps(total_counts, ensure_ascii=False)}")
    total_kinds: dict[str, int] = {}
    for r in all_runs:
        v = r["verdict"]
        key = v.get("failure_kind") or v.get("attribution", "")
        total_kinds[key] = total_kinds.get(key, 0) + 1
    lines.append(
        f"全部运行的 failure_kind 分布（attribution 的细分，旧产物回退到 attribution）："
        f"{json.dumps(total_kinds, ensure_ascii=False)}"
    )
    excluded = len(all_runs) - sum(len(eligible([r for r in all_runs if r['condition'] == s['condition']])) for s in summaries)
    if excluded:
        lines.append(
            f"未计入分母的运行：{excluded} 条（`live=false` 或 `contaminated=true` —— 假模型运行不计入真实通过率）"
        )
    lines.append("")

    lines.append("## examples")
    lines.append("")
    any_example = False
    for s in summaries:
        if s["examples"]:
            any_example = True
            lines.append(f"### {s['condition']}")
            lines.append("")
            for e in s["examples"]:
                lines.append(
                    f"- `{e['task_id']}` run {e['run_n']} · status={e['status']} · "
                    f"attribution={e['attribution']} · `{e['verdict_path']}`"
                )
            lines.append("")
    if not any_example:
        lines.append("无")
        lines.append("")
    return "\n".join(lines)


def summarize(experiment_id: str, out_path, out_root="evals/runs") -> int:
    exp = Path(out_root) / experiment_id
    if not exp.exists():
        return 2
    runs = load_runs(exp)
    conditions = sorted({r["condition"] for r in runs})
    if not runs:
        return 2

    summaries = [condition_summary(c, [r for r in runs if r["condition"] == c], experiment_id) for c in conditions]

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown(experiment_id, runs, summaries), encoding="utf-8")

    def dump(path: Path, payload) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    only_b = conditions == [CONDITION_B]
    only_c = conditions == [CONDITION_C]
    if only_b:
        dump(out.parent / "summary.json", summaries[0])
        dump(out.parent / "baseline_summary.json", summaries[0])
    elif only_c:
        dump(out.parent / "summary.json", summaries[0])
        dump(out.parent / "candidate_summary.json", summaries[0])
    else:
        for s in summaries:
            name = "baseline_summary.json" if s["condition"] == CONDITION_B else "candidate_summary.json"
            dump(out.parent / name, s)
        dump(out.parent / "summary.json", {"schema_version": "v1", "note": "see prefixed files"})
    return 0
