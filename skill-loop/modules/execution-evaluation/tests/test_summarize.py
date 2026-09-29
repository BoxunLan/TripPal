"""B 线汇总测试：文件三件套、分母口径、污染排除、examples 条数。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution_evaluation import summarize as sm

REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_PACK = REPO_ROOT / "fixtures" / "task_pack_trippal_v1" / "task_pack.jsonl"
SEED_SKILL = REPO_ROOT / "fixtures" / "seed_skill_v1" / "SKILL.md"


def craft_run(
    root: Path,
    experiment: str,
    condition: str,
    task_id: str,
    run_n: int,
    split: str,
    overall_pass: bool,
    live: bool = True,
    contaminated: bool = False,
    model: str = "m1",
    attribution: str | None = None,
    tokens: int = 100,
) -> Path:
    d = root / experiment / condition / task_id / str(run_n)
    (d / "artifacts").mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(json.dumps({"task_id": task_id, "split": split}), encoding="utf-8")
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "experiment_id": experiment,
                "condition": condition,
                "model": model,
                "temperature": 0,
                "timeout_seconds": 60,
                "max_tokens": 2000,
                "tool_snapshot": {"name": "lookup_local", "sha256": "a" * 64},
                "skill_snapshot": {"loaded": condition == "C-seed-skill"},
                "task_pack_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    (d / "verdict.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "task_id": task_id,
                "run_n": run_n,
                "live": live,
                "status": "pass" if overall_pass else "fail",
                "overall_pass": overall_pass,
                "attribution": attribution or ("none" if overall_pass else "knowledge_gap"),
                "contaminated": contaminated,
                "scores": {"outcome": 1.0 if overall_pass else 0.0, "process": 0.5, "evidence": 0.5, "efficiency": 0.5},
            }
        ),
        encoding="utf-8",
    )
    (d / "trace.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "step": 1,
                "timestamp": "2026-09-29T10:00:00+08:00",
                "action": "read_task",
                "observation": "",
                "decision": "",
                "skipped_or_abandoned": "",
                "cost": {"input_tokens": tokens // 2, "output_tokens": tokens // 2, "tool_calls": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return d


def test_summarize_writes_three_files_for_baseline_only(tmp_path):
    runs_root = tmp_path / "runs"
    craft_run(runs_root, "exp1", "B-no-skill", "T1", 1, "train", True)
    out = runs_root / "exp1" / "baseline_report.md"
    assert sm.summarize("exp1", out, runs_root) == 0
    for name in ("baseline_report.md", "summary.json", "baseline_summary.json"):
        assert (runs_root / "exp1" / name).exists(), name
    assert not (runs_root / "exp1" / "candidate_summary.json").exists()
    data = json.loads((runs_root / "exp1" / "summary.json").read_text(encoding="utf-8"))
    assert data["condition"] == "B-no-skill"
    assert data["task_count"] == 1
    assert data["pass_at_3"] == 1.0


def test_summarize_both_conditions_writes_prefixed_files(tmp_path):
    runs_root = tmp_path / "runs"
    craft_run(runs_root, "exp2", "B-no-skill", "T1", 1, "train", False)
    craft_run(runs_root, "exp2", "C-seed-skill", "T1", 1, "train", True)
    out = runs_root / "exp2" / "report.md"
    assert sm.summarize("exp2", out, runs_root) == 0
    assert (runs_root / "exp2" / "baseline_summary.json").exists()
    assert (runs_root / "exp2" / "candidate_summary.json").exists()
    note = json.loads((runs_root / "exp2" / "summary.json").read_text(encoding="utf-8"))
    assert note == {"schema_version": "v1", "note": "see prefixed files"}


def test_contaminated_and_not_live_runs_are_excluded(tmp_path):
    runs_root = tmp_path / "runs"
    craft_run(runs_root, "exp3", "B-no-skill", "T1", 1, "train", True)
    craft_run(runs_root, "exp3", "B-no-skill", "T2", 1, "train", False, contaminated=True)
    craft_run(runs_root, "exp3", "B-no-skill", "T3", 1, "train", False, live=False)
    out = runs_root / "exp3" / "baseline_report.md"
    assert sm.summarize("exp3", out, runs_root) == 0
    data = json.loads((runs_root / "exp3" / "summary.json").read_text(encoding="utf-8"))
    assert data["pass_at_3"] == 1.0, "只应统计那条干净且 live 的运行"
    assert data["task_count"] == 3
    assert data["attribution_counts"] == {"none": 1}


def test_zero_denominator_is_null_and_na(tmp_path):
    runs_root = tmp_path / "runs"
    craft_run(runs_root, "exp4", "B-no-skill", "T1", 1, "holdout", False, live=False)
    out = runs_root / "exp4" / "baseline_report.md"
    assert sm.summarize("exp4", out, runs_root) == 0
    data = json.loads((runs_root / "exp4" / "summary.json").read_text(encoding="utf-8"))
    assert data["pass_at_3"] is None
    assert data["holdout_pass_at_3"] is None
    assert data["avg_tokens"] is None
    assert "n/a" in out.read_text(encoding="utf-8")


def test_train_and_holdout_split_rates(tmp_path):
    runs_root = tmp_path / "runs"
    craft_run(runs_root, "exp5", "B-no-skill", "T1", 1, "train", True)
    craft_run(runs_root, "exp5", "B-no-skill", "T1", 2, "train", False)
    craft_run(runs_root, "exp5", "B-no-skill", "T2", 1, "holdout", False)
    craft_run(runs_root, "exp5", "B-no-skill", "T2", 2, "holdout", False)
    out = runs_root / "exp5" / "baseline_report.md"
    assert sm.summarize("exp5", out, runs_root) == 0
    data = json.loads((runs_root / "exp5" / "summary.json").read_text(encoding="utf-8"))
    assert data["pass_at_3"] == 0.25
    assert data["train_pass_at_3"] == 0.5
    assert data["holdout_pass_at_3"] == 0.0


def test_e7_zero_pass_rate_gives_exactly_three_examples(tmp_path):
    runs_root = tmp_path / "runs"
    for i in range(1, 4):
        craft_run(runs_root, "exp6", "B-no-skill", "T1", i, "train", False)
    out = runs_root / "exp6" / "baseline_report.md"
    assert sm.summarize("exp6", out, runs_root) == 0
    data = json.loads((runs_root / "exp6" / "summary.json").read_text(encoding="utf-8"))
    assert data["pass_at_3"] == 0.0
    assert len(data["examples"]) == 3
    assert all(set(e) == {"task_id", "run_n", "status", "attribution", "verdict_path"} for e in data["examples"])
    text = out.read_text(encoding="utf-8")
    assert "## examples" in text
    assert data["examples"][0]["verdict_path"] in text


def test_empty_examples_section_says_none(tmp_path):
    runs_root = tmp_path / "runs"
    craft_run(runs_root, "exp7", "B-no-skill", "T1", 1, "train", True)
    craft_run(runs_root, "exp7", "B-no-skill", "T2", 1, "train", False)
    out = runs_root / "exp7" / "baseline_report.md"
    assert sm.summarize("exp7", out, runs_root) == 0
    data = json.loads((runs_root / "exp7" / "summary.json").read_text(encoding="utf-8"))
    assert data["pass_at_3"] == 0.5
    text = out.read_text(encoding="utf-8")
    assert "## examples" in text


def test_avg_tokens_is_input_plus_output(tmp_path):
    runs_root = tmp_path / "runs"
    craft_run(runs_root, "exp8", "B-no-skill", "T1", 1, "train", True, tokens=200)
    craft_run(runs_root, "exp8", "B-no-skill", "T1", 2, "train", True, tokens=400)
    out = runs_root / "exp8" / "baseline_report.md"
    assert sm.summarize("exp8", out, runs_root) == 0
    data = json.loads((runs_root / "exp8" / "summary.json").read_text(encoding="utf-8"))
    assert data["avg_tokens"] == 300.0


def test_avg_input_output_split_is_reported(tmp_path):
    """回归门的替代成本口径要用这两个读数，规格口径 avg_tokens 必须等于两者之和。"""
    runs_root = tmp_path / "runs"
    craft_run(runs_root, "exp10", "B-no-skill", "T1", 1, "train", True, tokens=200)
    craft_run(runs_root, "exp10", "B-no-skill", "T1", 2, "train", True, tokens=400)
    out = runs_root / "exp10" / "baseline_report.md"
    assert sm.summarize("exp10", out, runs_root) == 0
    data = json.loads((runs_root / "exp10" / "summary.json").read_text(encoding="utf-8"))
    # craft_run 把 tokens 对半分给 input / output
    assert data["avg_input_tokens"] == 150.0
    assert data["avg_output_tokens"] == 150.0
    assert data["avg_tokens"] == data["avg_input_tokens"] + data["avg_output_tokens"]


def test_missing_experiment_exit_2(tmp_path):
    assert sm.summarize("nope", tmp_path / "r.md", tmp_path / "runs") == 2


def test_summarize_cli(tmp_path):
    import subprocess
    import sys

    runs_root = tmp_path / "runs"
    craft_run(runs_root, "exp9", "B-no-skill", "T1", 1, "train", True)
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "execution_evaluation",
            "summarize",
            "--experiment-id",
            "exp9",
            "--out",
            str(runs_root / "exp9" / "baseline_report.md"),
            "--run-root",
            str(runs_root),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    assert (runs_root / "exp9" / "baseline_summary.json").exists()


@pytest.mark.parametrize("tokens", [0, 10])
def test_tokens_and_pointer_shapes(tmp_path, tokens):
    runs_root = tmp_path / "runs"
    craft_run(runs_root, f"exp-t{tokens}", "B-no-skill", "T1", 1, "train", False, tokens=tokens)
    out = runs_root / f"exp-t{tokens}" / "baseline_report.md"
    assert sm.summarize(f"exp-t{tokens}", out, runs_root) == 0
    data = json.loads((runs_root / f"exp-t{tokens}" / "summary.json").read_text(encoding="utf-8"))
    assert data["avg_tokens"] == float(tokens)
    assert data["examples"][0]["run_n"] == 1
