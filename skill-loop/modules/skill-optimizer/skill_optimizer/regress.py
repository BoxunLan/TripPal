"""C 线 regress：回归门。

- 通过条件：候选的 `holdout_pass_at_3` ≥ 基线，且成本按 `cost_budget_basis` 选定的口径没超预算。
- 同一份 summary 上有三个成本读数，一次算完、全部写进报告，只用其中一个做判定：

  | 口径 | 读数 | 门限 |
  |---|---|---|
  | `total`（**默认，规格口径**） | `candidate.avg_tokens / baseline.avg_tokens` | ≤ `cost_budget_ratio` |
  | `output` | `candidate.avg_output_tokens / baseline.avg_output_tokens` | ≤ `cost_budget_ratio` |
  | `delta` | `candidate.avg_tokens - baseline.avg_tokens`（每次运行绝对增量） | ≤ `cost_delta_allowance` |

  `total` 就是规格「B 线」第 6 条写死的口径（`avg_tokens` = input+output，阈值 1.5），
  默认值不动它。另两个口径是给「基线 prompt 很短」的场景准备的：一份非平凡 SKILL.md 的常量
  注入会把输入顶到数倍，此时比值口径事实上等于「不许加载技能」，见 `integration/deviations.jsonl`
  的 dev-006。`output` 只比「模型真的多干了多少活」；`delta` 把技能自身的固定开销当作额度而不是乘数。
- `holdout_pass_at_3` 为 `null` → 退出码 2，不把空分母当成通过；所选口径需要的读数缺失
  （例如 basis=output 但 summary 里没有 `avg_output_tokens`）→ 同为退出码 2，不静默降级；
  `cost_budget_basis` 取值不在集合内 → 同样退出码 2。
- 不通过 → `conclusion: rollback`，只把 `candidate_skill/SKILL.md` 恢复成 seed 副本，
  不删除任何 `evals/runs/` 目录。
- `--no-candidate` 无条件写 `conclusion: no_change`，**不读取** `attribution_report.json`
  的 `proposal_state`，也不从上游文件推断。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import DEFAULT_CONFIG, load_config
from .propose import SEED_SKILL

BASE_REQUIRED = ("holdout_pass_at_3", "avg_tokens")

BASES = ("total", "output", "delta")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(out_md: Path, report: dict, body: str) -> None:
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(body, encoding="utf-8")
    (out_md.parent / "evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _rollback(candidate_skill: Path) -> None:
    candidate_skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SEED_SKILL, candidate_skill)


def _round(v):
    return None if v is None else round(v, 6)


def _fmt(v):
    return "n/a" if v is None else v


def _cost_readings(base: dict, cand: dict) -> dict:
    """三个口径的读数。任一输入缺了就写 None —— 不猜、不补零。"""
    b_tot, c_tot = base.get("avg_tokens"), cand.get("avg_tokens")
    b_out, c_out = base.get("avg_output_tokens"), cand.get("avg_output_tokens")
    return {
        "total_ratio": (c_tot / b_tot) if (b_tot and c_tot is not None) else None,
        "output_ratio": (c_out / b_out) if (b_out and c_out is not None) else None,
        "delta_total": (c_tot - b_tot) if (b_tot is not None and c_tot is not None) else None,
        "baseline_avg_input_tokens": base.get("avg_input_tokens"),
        "candidate_avg_input_tokens": cand.get("avg_input_tokens"),
        "baseline_avg_output_tokens": b_out,
        "candidate_avg_output_tokens": c_out,
    }


def _gate(basis: str, readings: dict, cfg: dict) -> tuple[bool | None, float, float | None]:
    """→ (是否过门 / None 表示读数缺失, 门限, 参与判定的读数)。"""
    limit = cfg["cost_delta_allowance"] if basis == "delta" else cfg["cost_budget_ratio"]
    value = {
        "total": readings["total_ratio"],
        "output": readings["output_ratio"],
        "delta": readings["delta_total"],
    }[basis]
    if value is None:
        return None, limit, None
    return value <= limit, limit, value


def regress(baseline, out, candidate=None, no_candidate=False, config_path=None) -> int:
    out_md = Path(out)
    candidate_skill = out_md.parent / "candidate_skill" / "SKILL.md"
    cfg = load_config(config_path or DEFAULT_CONFIG)
    basis = str(cfg.get("cost_budget_basis", "total")).strip().lower()
    if basis not in BASES:
        return 2

    base_path = Path(baseline)
    if not base_path.exists():
        return 2
    base = _read(base_path)

    if no_candidate:
        report = {
            "schema_version": "v1",
            "conclusion": "no_change",
            "reason": "上游没有生成候选 skill（proposal_state=no_change），本路径不读取候选分数，也不改 skill 文件",
            "baseline": {k: base.get(k) for k in BASE_REQUIRED},
            "candidate": None,
            "cost_ratio": None,
            "cost_budget_ratio": cfg["cost_budget_ratio"],
            "cost_budget": {
                "basis": basis,
                "gate_value": None,
                "gate_limit": None,
                "delta_allowance": cfg["cost_delta_allowance"],
            },
        }
        _write(out_md, report, _markdown(report))
        return 0

    if not candidate:
        return 2
    cand_path = Path(candidate)
    if not cand_path.exists():
        return 2
    cand = _read(cand_path)

    if base.get("holdout_pass_at_3") is None or cand.get("holdout_pass_at_3") is None:
        return 2
    if cand.get("avg_tokens") is None or not base.get("avg_tokens"):
        return 2

    readings = _cost_readings(base, cand)
    cost_ok, limit, value = _gate(basis, readings, cfg)
    if cost_ok is None:  # 选定口径的读数缺失
        return 2

    pass_ok = cand["holdout_pass_at_3"] >= base["holdout_pass_at_3"]
    conclusion = "pass" if (pass_ok and cost_ok) else "rollback"

    report = {
        "schema_version": "v1",
        "conclusion": conclusion,
        "baseline": {k: base.get(k) for k in BASE_REQUIRED},
        "candidate": {k: cand.get(k) for k in BASE_REQUIRED},
        "cost_ratio": _round(readings["total_ratio"]),
        "cost_budget_ratio": cfg["cost_budget_ratio"],
        "cost_budget": {
            "basis": basis,
            "gate_value": _round(value),
            "gate_limit": limit,
            "total_ratio": _round(readings["total_ratio"]),
            "output_ratio": _round(readings["output_ratio"]),
            "delta_total_tokens": _round(readings["delta_total"]),
            "delta_allowance": cfg["cost_delta_allowance"],
            "baseline_avg_input_tokens": readings["baseline_avg_input_tokens"],
            "candidate_avg_input_tokens": readings["candidate_avg_input_tokens"],
            "baseline_avg_output_tokens": readings["baseline_avg_output_tokens"],
            "candidate_avg_output_tokens": readings["candidate_avg_output_tokens"],
        },
        "checks": {
            "holdout_not_regressed": pass_ok,
            "within_cost_budget": cost_ok,
        },
    }
    if conclusion == "rollback":
        _rollback(candidate_skill)
        report["rolled_back"] = str(candidate_skill)
        report["note"] = "只恢复 candidate_skill/SKILL.md；evals/runs/ 下的运行全部保留"
    _write(out_md, report, _markdown(report))
    return 0


def _markdown(report: dict) -> str:
    cb = report.get("cost_budget") or {}
    basis = cb.get("basis")
    lines = [
        "# 回归门报告",
        "",
        f"- conclusion: **{report['conclusion']}**",
        f"- cost_budget_basis: `{basis}`（本次判定用这一行）",
        f"- cost_budget_ratio: {report['cost_budget_ratio']}",
    ]
    if report.get("reason"):
        lines.append(f"- reason: {report['reason']}")
    if report.get("checks"):
        for k, v in report["checks"].items():
            lines.append(f"- {k}: {v}")
    if report.get("cost_ratio") is not None:
        lines.append(f"- 实际 cost_ratio: {report['cost_ratio']}")
    lines += [
        "",
        "| 指标 | baseline | candidate |",
        "|---|---|---|",
        f"| holdout_pass_at_3 | {report['baseline']['holdout_pass_at_3']} | "
        f"{(report['candidate'] or {}).get('holdout_pass_at_3', 'n/a')} |",
        f"| avg_tokens（input+output） | {report['baseline']['avg_tokens']} | "
        f"{(report['candidate'] or {}).get('avg_tokens', 'n/a')} |",
        f"| avg_input_tokens | {_fmt(cb.get('baseline_avg_input_tokens'))} | "
        f"{_fmt(cb.get('candidate_avg_input_tokens'))} |",
        f"| avg_output_tokens | {_fmt(cb.get('baseline_avg_output_tokens'))} | "
        f"{_fmt(cb.get('candidate_avg_output_tokens'))} |",
        "",
    ]
    if report["conclusion"] != "no_change":
        lines += [
            "## 三个成本口径（同一份 summary；只有一行参与判定）",
            "",
            "| 口径 | 读数 | 门限 | 本次判定用它 |",
            "|---|---|---|---|",
            f"| `total`（规格口径，input+output 比值） | {_fmt(cb.get('total_ratio'))} | "
            f"≤ {report['cost_budget_ratio']} | {basis == 'total'} |",
            f"| `output`（只比输出，向量 = 真正的工作量） | {_fmt(cb.get('output_ratio'))} | "
            f"≤ {report['cost_budget_ratio']} | {basis == 'output'} |",
            f"| `delta`（每次运行绝对增量） | {_fmt(cb.get('delta_total_tokens'))} tok | "
            f"≤ {cb.get('delta_allowance')} tok | {basis == 'delta'} |",
            "",
            f"门限来自 `config/default.yaml` 的 `cost_budget_ratio` / `cost_delta_allowance`，"
            f"口径来自 `cost_budget_basis`（当前 `{basis}`）。换口径属于口径变更，记 `integration/deviations.jsonl`。",
            "",
        ]
    if report.get("rolled_back"):
        lines.append(f"已把 `{report['rolled_back']}` 恢复成 seed 副本。")
    lines.append(
        "CI 不能用退出码判断回归有没有过门，必须读 `evaluation_report.json` 的 `conclusion`。"
    )
    return "\n".join(lines) + "\n"
