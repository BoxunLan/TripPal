"""C 线 regress 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill_optimizer import regress as regress_mod
from skill_optimizer.propose import SEED_SKILL


def summary(path: Path, holdout, tokens, condition="B-no-skill", **extra) -> Path:
    payload = {
        "schema_version": "v1",
        "experiment_id": "exp",
        "condition": condition,
        "task_count": 4,
        "pass_at_3": 0.5,
        "train_pass_at_3": 0.5,
        "holdout_pass_at_3": holdout,
        "avg_tokens": tokens,
        "attribution_counts": {},
        "examples": [],
    }
    payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def out_path(tmp_path: Path) -> Path:
    return tmp_path / "out" / "evaluation_report.md"


def read_report(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "out" / "evaluation_report.json").read_text(encoding="utf-8"))


def test_pass_when_not_regressed_and_within_budget(tmp_path):
    base = summary(tmp_path / "b.json", 0.5, 1000)
    cand = summary(tmp_path / "c.json", 0.5, 1400, "C-seed-skill")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand) == 0
    report = read_report(tmp_path)
    assert report["conclusion"] == "pass"
    assert report["checks"] == {"holdout_not_regressed": True, "within_cost_budget": True}
    assert (tmp_path / "out" / "evaluation_report.md").exists()
    assert "conclusion" in report


def test_report_never_contains_proposal_state(tmp_path):
    base = summary(tmp_path / "b.json", 0.5, 1000)
    cand = summary(tmp_path / "c.json", 0.6, 1000, "C-seed-skill")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand) == 0
    text = (tmp_path / "out" / "evaluation_report.md").read_text(encoding="utf-8")
    assert "proposal_state" not in json.dumps(read_report(tmp_path))
    assert "proposal_state" not in text


def test_rollback_when_holdout_drops(tmp_path):
    base = summary(tmp_path / "b.json", 0.5, 1000)
    cand = summary(tmp_path / "c.json", 0.25, 1000, "C-seed-skill")
    skill = tmp_path / "out" / "candidate_skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 被改坏的候选 skill\n", encoding="utf-8")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand) == 0
    assert read_report(tmp_path)["conclusion"] == "rollback"
    assert skill.read_text(encoding="utf-8") == SEED_SKILL.read_text(encoding="utf-8")


def test_rollback_when_over_cost_budget(tmp_path):
    base = summary(tmp_path / "b.json", 0.5, 1000)
    cand = summary(tmp_path / "c.json", 0.9, 1600, "C-seed-skill")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand) == 0
    report = read_report(tmp_path)
    assert report["conclusion"] == "rollback"
    assert report["checks"]["within_cost_budget"] is False
    assert report["checks"]["holdout_not_regressed"] is True


def test_null_holdout_exits_2(tmp_path):
    base = summary(tmp_path / "b.json", None, 1000)
    cand = summary(tmp_path / "c.json", 0.5, 1000, "C-seed-skill")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand) == 2
    assert not (tmp_path / "out" / "evaluation_report.md").exists()


def test_missing_summary_exits_2(tmp_path):
    base = summary(tmp_path / "b.json", 0.5, 1000)
    assert regress_mod.regress(base, out_path(tmp_path), candidate=tmp_path / "nope.json") == 2
    assert regress_mod.regress(tmp_path / "nope.json", out_path(tmp_path), candidate=base) == 2


def test_no_candidate_is_unconditional_no_change(tmp_path):
    base = summary(tmp_path / "b.json", 0.5, 1000)
    out = out_path(tmp_path)
    (tmp_path / "out").mkdir(parents=True)
    (tmp_path / "out" / "attribution_report.json").write_text(
        json.dumps({"proposal_state": "proposed"}), encoding="utf-8"
    )
    skill = tmp_path / "out" / "candidate_skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 不该被改动\n", encoding="utf-8")

    assert regress_mod.regress(base, out, no_candidate=True) == 0
    report = read_report(tmp_path)
    assert report["conclusion"] == "no_change"
    assert report["candidate"] is None
    assert skill.read_text(encoding="utf-8") == "# 不该被改动\n", "--no-candidate 不改 skill 文件"
    assert (tmp_path / "out" / "evaluation_report.md").exists()


def test_regress_cli_no_candidate(tmp_path):
    import subprocess
    import sys

    base = summary(tmp_path / "b.json", 0.5, 1000)
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "skill_optimizer",
            "regress",
            "--baseline",
            str(base),
            "--no-candidate",
            "--out",
            str(out_path(tmp_path)),
        ],
        cwd=SEED_SKILL.parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    assert read_report(tmp_path)["conclusion"] == "no_change"


@pytest.mark.parametrize("ratio,expected", [(1.5, "pass"), (1.51, "rollback")])
def test_cost_budget_threshold(tmp_path, ratio, expected):
    base = summary(tmp_path / "b.json", 0.5, 1000)
    cand = summary(tmp_path / "c.json", 0.5, 1000 * ratio, "C-seed-skill")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand) == 0
    assert read_report(tmp_path)["conclusion"] == expected


# ---------------------------------------------------------------------------
# 成本口径：同一份读数上三个口径，默认仍是规格口径 `total`
# 下面这组数字取自真模型实测（live-baseline / live-candidate）：
# 基线 prompt 很短（输入 286），技能的常量注入把输入顶到 663，输出反而从 203 降到 151。
# ---------------------------------------------------------------------------
BASE_IN, BASE_OUT = 286.1, 202.6
CAND_IN, CAND_OUT = 663.1, 150.7
BASE_TOT, CAND_TOT = BASE_IN + BASE_OUT, CAND_IN + CAND_OUT


def real_pair(tmp_path: Path) -> tuple[Path, Path]:
    base = summary(tmp_path / "b.json", 0.5, BASE_TOT, avg_input_tokens=BASE_IN, avg_output_tokens=BASE_OUT)
    cand = summary(
        tmp_path / "c.json", 1.0, CAND_TOT, "C-seed-skill",
        avg_input_tokens=CAND_IN, avg_output_tokens=CAND_OUT,
    )
    return base, cand


def test_default_basis_is_total_and_still_rolls_back(tmp_path):
    base, cand = real_pair(tmp_path)
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand) == 0
    report = read_report(tmp_path)
    assert report["cost_budget"]["basis"] == "total", "默认不许悄悄换成别的口径"
    assert report["conclusion"] == "rollback"
    assert report["checks"]["holdout_not_regressed"] is True
    assert report["checks"]["within_cost_budget"] is False
    assert report["cost_ratio"] == round(CAND_TOT / BASE_TOT, 6)


def test_output_basis_flips_to_pass(tmp_path):
    base, cand = real_pair(tmp_path)
    cfg = write_cfg(tmp_path, "cost_budget_basis: output\n")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand, config_path=cfg) == 0
    report = read_report(tmp_path)
    assert report["cost_budget"]["basis"] == "output"
    assert report["cost_budget"]["output_ratio"] == round(CAND_OUT / BASE_OUT, 6)
    assert report["conclusion"] == "pass"
    assert report["checks"] == {"holdout_not_regressed": True, "within_cost_budget": True}
    assert report["cost_ratio"] == round(CAND_TOT / BASE_TOT, 6), "规格口径的比值仍须照写"
    md = (tmp_path / "out" / "evaluation_report.md").read_text(encoding="utf-8")
    for token in ("`total`", "`output`", "`delta`"):
        assert token in md, token


def test_delta_basis_uses_absolute_allowance(tmp_path):
    base, cand = real_pair(tmp_path)
    delta = CAND_TOT - BASE_TOT  # ≈ 325 token/次

    cfg = write_cfg(tmp_path, f"cost_budget_basis: delta\ncost_delta_allowance: {int(delta) + 1}\n")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand, config_path=cfg) == 0
    report = read_report(tmp_path)
    assert report["cost_budget"]["delta_total_tokens"] == round(delta, 6)
    assert report["conclusion"] == "pass"

    cfg = write_cfg(tmp_path, f"cost_budget_basis: delta\ncost_delta_allowance: {int(delta) - 1}\n")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand, config_path=cfg) == 0
    report = read_report(tmp_path)
    assert report["conclusion"] == "rollback"
    assert report["checks"]["holdout_not_regressed"] is True
    assert report["checks"]["within_cost_budget"] is False


def test_output_basis_without_output_reading_exits_2(tmp_path):
    """选了 output 口径但 summary 里没有 avg_output_tokens → 退出码 2，不静默降级。"""
    base = summary(tmp_path / "b.json", 0.5, 1000)
    cand = summary(tmp_path / "c.json", 1.0, 1000, "C-seed-skill")
    cfg = write_cfg(tmp_path, "cost_budget_basis: output\n")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand, config_path=cfg) == 2
    assert not (tmp_path / "out" / "evaluation_report.md").exists()


def test_unknown_basis_exits_2(tmp_path):
    base = summary(tmp_path / "b.json", 0.5, 1000)
    cand = summary(tmp_path / "c.json", 1.0, 1000, "C-seed-skill")
    cfg = write_cfg(tmp_path, "cost_budget_basis: median\n")
    assert regress_mod.regress(base, out_path(tmp_path), candidate=cand, config_path=cfg) == 2
    assert not (tmp_path / "out" / "evaluation_report.md").exists()


def test_no_candidate_report_still_records_basis(tmp_path):
    base = summary(tmp_path / "b.json", 0.5, 1000)
    cfg = write_cfg(tmp_path, "cost_budget_basis: output\n")
    (tmp_path / "out").mkdir(parents=True)
    assert regress_mod.regress(base, out_path(tmp_path), no_candidate=True, config_path=cfg) == 0
    report = read_report(tmp_path)
    assert report["conclusion"] == "no_change"
    assert report["cost_budget"]["basis"] == "output"
