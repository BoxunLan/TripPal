"""C 线 propose 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill_optimizer import contracts, propose as propose_mod

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLE = REPO_ROOT / "fixtures" / "run_bundle_v1"
SEED_SKILL = REPO_ROOT / "fixtures" / "seed_skill_v1" / "SKILL.md"


@pytest.fixture(scope="module")
def proposed(tmp_path_factory):
    out = tmp_path_factory.mktemp("c-out")
    assert propose_mod.propose(BUNDLE, out) == 0
    return out


def test_writes_three_files(proposed):
    assert (proposed / "attribution_report.json").exists()
    assert (proposed / "change_proposals.jsonl").exists()
    assert (proposed / "candidate_skill" / "SKILL.md").exists()


def test_propose_does_not_write_evaluation_report(proposed):
    assert not (proposed / "evaluation_report.md").exists()
    assert not (proposed / "evaluation_report.json").exists()


def test_only_knowledge_gap_train_runs_become_proposals(proposed):
    rows = contracts.load_jsonl(proposed / "change_proposals.jsonl")
    assert len(rows) == 1, "两条同 pattern 的 train run 应并成一条"
    assert rows[0]["evidence_tasks"] == ["fx-cn-visa-fact-001"]
    assert contracts.iter_errors("skill-change", rows[0]) == []


def test_non_knowledge_gap_attributions_have_no_change_id(proposed):
    rows = contracts.load_jsonl(proposed / "change_proposals.jsonl")
    assert all(r["change_id"] not in ("missing_tool", "task_defect", "success") for r in rows)
    report = json.loads((proposed / "attribution_report.json").read_text(encoding="utf-8"))
    assert report["excluded_by_attribution"].get("missing_tool_or_data") == 1
    assert report["excluded_by_attribution"].get("task_defect") == 1
    assert report["excluded_by_attribution"].get("none") == 1


def test_holdout_directory_is_not_read(proposed):
    report = json.loads((proposed / "attribution_report.json").read_text(encoding="utf-8"))
    assert report["runs_read"] == 5, "夹具里 5 次非 holdout 运行"
    assert report["excluded_holdout_split_runs"] == 0


def test_failure_pattern_is_truncated(proposed):
    for row in contracts.load_jsonl(proposed / "change_proposals.jsonl"):
        assert len(row["failure_pattern"]) <= 80
        assert row["failure_pattern"]


def test_failure_pattern_falls_back_to_the_deciding_step(tmp_path):
    """没有 `no source` 标记时，要取判成败那一步的 decision，不能取开场的「开始执行」。"""
    run = tmp_path / "run1"
    run.mkdir()
    rows = [
        {"step": 1, "observation": "task=T1", "decision": "开始执行"},
        {"step": 2, "observation": "empty", "decision": "没有命中，改走模型自身知识"},
        {"step": 3, "observation": "模型输出正文", "decision": "断言未全部命中：contains 文本不含 'China visa-free transit policy'"},
    ]
    (run / "trace.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    pattern = propose_mod.failure_pattern(run)
    assert pattern.startswith("断言未全部命中")
    assert pattern != "开始执行"


def test_failure_pattern_prefers_no_source_observation(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    rows = [
        {"step": 1, "observation": "task=T1", "decision": "开始执行"},
        {"step": 2, "observation": "no source；直接给出了结论", "decision": "把无来源的推断当结论"},
    ]
    (run / "trace.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    assert propose_mod.failure_pattern(run) == "把无来源的推断当结论"


def test_proposed_change_does_not_copy_task_prompt(proposed):
    tasks = {t["task_id"]: t for t in contracts.load_jsonl(REPO_ROOT / "fixtures" / "task_pack_trippal_v1" / "task_pack.jsonl")}
    for row in contracts.load_jsonl(proposed / "change_proposals.jsonl"):
        for tid in row["evidence_tasks"]:
            assert tasks[tid]["prompt"] not in row["proposed_change"]
            assert tasks[tid]["prompt"] not in row["failure_pattern"]


def test_candidate_skill_extends_the_seed(proposed):
    seed = SEED_SKILL.read_text(encoding="utf-8")
    cand = (proposed / "candidate_skill" / "SKILL.md").read_text(encoding="utf-8")
    assert seed.strip() in cand
    assert cand.count("## ") >= seed.count("## ") + 1, "每条提案应渲染一个二级标题"


def test_proposal_state_field(proposed):
    report = json.loads((proposed / "attribution_report.json").read_text(encoding="utf-8"))
    assert report["proposal_state"] == "proposed"


def test_no_change_writes_seed_copy(tmp_path):
    bundle = tmp_path / "bundle"
    run = bundle / "success" / "run1"
    (run / "artifacts").mkdir(parents=True)
    (run / "verdict.json").write_text(
        json.dumps({"attribution": "none", "overall_pass": True, "task_id": "T1"}), encoding="utf-8"
    )
    out = tmp_path / "out"
    assert propose_mod.propose(bundle, out) == 0
    report = json.loads((out / "attribution_report.json").read_text(encoding="utf-8"))
    assert report["proposal_state"] == "no_change"
    assert contracts.load_jsonl(out / "change_proposals.jsonl") == []
    assert (out / "candidate_skill" / "SKILL.md").read_text(encoding="utf-8") == SEED_SKILL.read_text(encoding="utf-8")


def test_holdout_path_exits_2_and_writes_nothing(tmp_path):
    out = tmp_path / "out"
    assert propose_mod.propose(BUNDLE / "holdout", out) == 2
    assert not out.exists()


def test_max_proposals_is_capped(tmp_path):
    bundle = tmp_path / "bundle"
    for i in range(1, 8):
        run = bundle / "knowledge_gap" / f"run{i}"
        (run / "artifacts").mkdir(parents=True)
        (run / "task.json").write_text(
            json.dumps({"task_id": f"T{i}", "split": "train"}), encoding="utf-8"
        )
        (run / "verdict.json").write_text(
            json.dumps({"attribution": "knowledge_gap", "overall_pass": False, "task_id": f"T{i}"}),
            encoding="utf-8",
        )
        (run / "trace.jsonl").write_text(
            json.dumps(
                {
                    "schema_version": "v1",
                    "step": 1,
                    "timestamp": "t",
                    "action": "a",
                    "observation": "no source " + "x" * i,
                    "decision": f"模式{i}" + "y" * 90,
                    "skipped_or_abandoned": "",
                    "cost": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    out = tmp_path / "out"
    assert propose_mod.propose(bundle, out) == 0
    rows = contracts.load_jsonl(out / "change_proposals.jsonl")
    assert len(rows) == 5
    report = json.loads((out / "attribution_report.json").read_text(encoding="utf-8"))
    assert report["dropped_over_max_proposals"] == 2


def test_propose_cli(tmp_path):
    import subprocess
    import sys

    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, "-m", "skill_optimizer", "propose", "--run-bundle", str(BUNDLE), "--out", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    assert (out / "candidate_skill" / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# 合并与渲染：这两处曾各有一个「看着合规、实测吃成本」的口子
# ---------------------------------------------------------------------------
def craft_kg_run(bundle: Path, task_id: str, decision: str, run_n: int = 1) -> Path:
    run = bundle / "knowledge_gap" / task_id / str(run_n)
    (run / "artifacts").mkdir(parents=True)
    (run / "task.json").write_text(
        json.dumps({"task_id": task_id, "split": "train"}), encoding="utf-8"
    )
    (run / "verdict.json").write_text(
        json.dumps({"attribution": "knowledge_gap", "overall_pass": False, "task_id": task_id}),
        encoding="utf-8",
    )
    (run / "trace.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "step": 1,
                "timestamp": "t",
                "action": "answer",
                "observation": "模型输出正文",
                "decision": decision,
                "skipped_or_abandoned": "",
                "cost": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return run


def test_same_normalized_pattern_merges_into_one_proposal(tmp_path):
    """只差「漏了哪个站点」的四条失败是**同一个模式**（回答没引来源），应并成一条。"""
    bundle = tmp_path / "bundle"
    domains = [
        "govt.chinadaily.com.cn",
        "english.www.gov.cn",
        "www.gov.cn",
        "en.nia.gov.cn",
    ]
    for i, d in enumerate(domains, start=1):
        craft_kg_run(bundle, f"T{i}", f"断言未全部命中：contains 文本不含 '{d}'")
    out = tmp_path / "out"
    assert propose_mod.propose(bundle, out) == 0

    rows = contracts.load_jsonl(out / "change_proposals.jsonl")
    assert len(rows) == 1, "同模式不该各占一条提案"
    assert rows[0]["evidence_tasks"] == ["T1", "T2", "T3", "T4"]
    assert "govt.chinadaily.com.cn" not in rows[0]["failure_pattern"]
    assert contracts.iter_errors("skill-change", rows[0]) == []

    report = json.loads((out / "attribution_report.json").read_text(encoding="utf-8"))
    assert report["merged_patterns"] == 1
    assert len(next(iter(report["pattern_variants"].values()))) == 4, "被抹掉的具体取值仍要留档"


def test_identical_sections_render_once():
    """`proposed_change` 是常量 → 两条提案就是同一次编辑，正文里只能出现一遍。"""
    proposals = [
        {"change_id": "ch-001", "target_section": "X", "proposed_change": "甲；乙。"},
        {"change_id": "ch-002", "target_section": "X", "proposed_change": "甲；乙。"},
        {"change_id": "ch-003", "target_section": "X", "proposed_change": "丙。"},
    ]
    assert len(propose_mod.renderable(proposals)) == 2
    text = propose_mod.render_candidate("# seed\n", proposals)
    assert text.count("## X") == 2, "同一次编辑一次，不同的编辑各一次"


def test_candidate_skill_growth_is_bounded_by_distinct_edits(tmp_path):
    """回归保护：这份正文按固定长度进**每次**调用的输入，增长只能来自互不相同的编辑。"""
    bundle = tmp_path / "bundle"
    for i, d in enumerate(["a.example", "b.example", "c.example", "d.example"], start=1):
        craft_kg_run(bundle, f"T{i}", f"断言未全部命中：contains 文本不含 '{d}'")
    out = tmp_path / "out"
    assert propose_mod.propose(bundle, out) == 0
    report = json.loads((out / "attribution_report.json").read_text(encoding="utf-8"))
    assert report["candidate_skill_chars"] < report["seed_skill_chars"] * 1.6, (
        f"4 条同模式提案把正文从 {report['seed_skill_chars']} 撑到 "
        f"{report['candidate_skill_chars']} 字"
    )
