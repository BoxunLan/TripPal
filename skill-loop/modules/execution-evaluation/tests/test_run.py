"""B 线运行测试：落盘形状、退出码、超时口径、污染标记。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from execution_evaluation import contracts, runner
from execution_evaluation.model import ModelTimeout

REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_PACK = REPO_ROOT / "fixtures" / "task_pack_trippal_v1" / "task_pack.jsonl"
SEED_SKILL = REPO_ROOT / "fixtures" / "seed_skill_v1" / "SKILL.md"
RUN_FILES = ["task.json", "manifest.json", "log.md", "trace.jsonl", "verdict.json", "artifacts"]


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "execution_evaluation", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def runs_root(tmp_path_factory):
    out = tmp_path_factory.mktemp("b-runs")
    assert runner.run(TASK_PACK, "B-no-skill", 3, "fixture-b1", out) == 0
    return out


def run_dirs(runs_root, experiment="fixture-b1", condition="B-no-skill"):
    return sorted((Path(runs_root) / experiment / condition).glob("*/*"))


def test_every_run_has_six_items(runs_root):
    dirs = run_dirs(runs_root)
    assert len(dirs) == 4 * 3, "4 张卡 × 3 次"
    for d in dirs:
        for name in RUN_FILES:
            assert (d / name).exists(), f"{d} 缺 {name}"


def test_verdict_and_manifest_pass_schema(runs_root):
    for d in run_dirs(runs_root):
        assert contracts.iter_errors("verdict", json.loads((d / "verdict.json").read_text(encoding="utf-8"))) == []
        assert contracts.iter_errors("run-manifest", json.loads((d / "manifest.json").read_text(encoding="utf-8"))) == []


def test_trace_events_pass_schema(runs_root):
    for d in run_dirs(runs_root):
        for line in (d / "trace.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert contracts.iter_errors("trace-event", json.loads(line)) == []


def test_no_skill_manifest_is_loaded_false(runs_root):
    for d in run_dirs(runs_root):
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["condition"] == "B-no-skill"
        assert manifest["skill_snapshot"] == {"loaded": False}


def test_fake_model_runs_are_not_live(runs_root):
    for d in run_dirs(runs_root):
        assert json.loads((d / "verdict.json").read_text(encoding="utf-8"))["live"] is False


def test_log_md_has_the_agreed_columns(runs_root):
    header = (run_dirs(runs_root)[0] / "log.md").read_text(encoding="utf-8").splitlines()[0]
    for col in ["step", "timestamp", "action", "observation", "decision", "skipped_or_abandoned", "cost", "artifact"]:
        assert col in header


def test_handwritten_fixture_bundle_passes_schemas():
    """夹具 run_bundle_v1 是 C 线的输入，必须自己先合法 —— 曾经漏过 schema_version。"""
    bundle = REPO_ROOT / "fixtures" / "run_bundle_v1"
    checked = 0
    for vp in sorted(bundle.rglob("verdict.json")):
        assert contracts.iter_errors("verdict", json.loads(vp.read_text(encoding="utf-8"))) == [], vp
        assert contracts.iter_errors(
            "run-manifest", json.loads((vp.parent / "manifest.json").read_text(encoding="utf-8"))
        ) == [], vp
        assert contracts.iter_errors("task", json.loads((vp.parent / "task.json").read_text(encoding="utf-8"))) == [], vp
        for line in (vp.parent / "trace.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert contracts.iter_errors("trace-event", json.loads(line)) == [], vp
        checked += 1
    assert checked >= 4


def test_seed_skill_condition_sets_snapshot(tmp_path):
    assert runner.run(TASK_PACK, "C-seed-skill", 1, "fixture-c1", tmp_path, skill=SEED_SKILL) == 0
    d = sorted((tmp_path / "fixture-c1" / "C-seed-skill").glob("*/*"))[0]
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skill_snapshot"]["loaded"] is True
    assert manifest["skill_snapshot"]["path"].endswith("seed_skill_v1/SKILL.md")
    assert len(manifest["skill_snapshot"]["sha256"]) == 64


def test_cli_exit_codes(tmp_path):
    assert cli("run", "--task-pack", str(TASK_PACK), "--condition", "C-seed-skill",
               "--runs", "1", "--experiment-id", "x", "--out", str(tmp_path)).returncode == 2
    assert cli("run", "--task-pack", str(TASK_PACK), "--condition", "A-whatever",
               "--runs", "1", "--experiment-id", "x", "--out", str(tmp_path)).returncode == 2
    assert cli("run", "--task-pack", str(tmp_path / "missing.jsonl"), "--condition", "B-no-skill",
               "--runs", "1", "--experiment-id", "x", "--out", str(tmp_path)).returncode == 2


def test_timeout_still_writes_all_files(tmp_path, monkeypatch):
    class TimeoutModel:
        name = "timeout-model"
        live = True

        def complete(self, prompt, skill_text=None):
            raise ModelTimeout("请求超时")

    monkeypatch.setattr(runner, "build_model", lambda cfg: TimeoutModel())
    assert runner.run(TASK_PACK, "B-no-skill", 1, "timeout-exp", tmp_path) == 0
    dirs = sorted((tmp_path / "timeout-exp" / "B-no-skill").glob("*/*"))
    assert dirs
    for d in dirs:
        for name in RUN_FILES:
            assert (d / name).exists(), f"{d} 缺 {name}"
        verdict = json.loads((d / "verdict.json").read_text(encoding="utf-8"))
        assert verdict["status"] == "timeout"
        assert verdict["overall_pass"] is False
        assert verdict["attribution"] == "missing_tool_or_data"
        assert contracts.iter_errors("verdict", verdict) == []


def test_server_error_uses_execution_defect(tmp_path, monkeypatch):
    from execution_evaluation.model import ModelServerError

    class BoomModel:
        name = "boom-model"
        live = True

        def complete(self, prompt, skill_text=None):
            raise ModelServerError("HTTP 502")

    monkeypatch.setattr(runner, "build_model", lambda cfg: BoomModel())
    assert runner.run(TASK_PACK, "B-no-skill", 1, "boom-exp", tmp_path) == 0
    verdict = json.loads(
        next((tmp_path / "boom-exp" / "B-no-skill").glob("*/*/verdict.json")).read_text(encoding="utf-8")
    )
    assert verdict["status"] == "error"
    assert verdict["attribution"] == "execution_defect"


def test_retrieval_result_reaches_the_prompt(tmp_path, monkeypatch):
    """lookup_local 的命中必须进提示词。

    曾经只写进 artifacts/lookup.json 与日志、**不进 prompt** —— 于是 allowed_tools 是装饰性的，
    模型手上根本没有来源，skill 要求「给出来源」时它只能写「待核实」，看起来像 skill 有害。
    """
    prompts: list[str] = []

    class CaptureModel:
        name = "capture-model"
        live = True

        def complete(self, prompt, skill_text=None):
            prompts.append(prompt)
            return '{"decision":"unknown","sources":[]}', {"input_tokens": 1, "output_tokens": 1}

    monkeypatch.setattr(runner, "build_model", lambda cfg: CaptureModel())
    assert runner.run(TASK_PACK, "B-no-skill", 1, "capture-exp", tmp_path) == 0

    grounded = [p for p in prompts if "[检索结果 · lookup_local]" in p]
    assert grounded, "命中的检索结果应至少注入一次"
    for p in grounded:
        assert "来源：" in p
        assert "生效日期：" in p
    assert any("govt.chinadaily.com.cn" in p for p in grounded)


def test_skill_is_injected_once_not_duplicated(tmp_path, monkeypatch):
    """skill 只走 system 通道。

    曾经 system 与用户消息各发一份全文：同一份常量文本每轮多付一遍输入 token，
    把 cond 卡的输入从 104 顶到 865，cost_ratio 直接翻倍并使回归门误判 rollback。
    """
    prompts: list[str] = []
    seen: dict = {}

    class CaptureModel:
        name = "cap"
        live = True

        def complete(self, prompt, skill_text=None):
            prompts.append(prompt)
            seen["skill"] = skill_text
            return '{"decision":"unknown","sources":[]}', {"input_tokens": 1, "output_tokens": 1}

    monkeypatch.setattr(runner, "build_model", lambda cfg: CaptureModel())
    assert runner.run(TASK_PACK, "C-seed-skill", 1, "dup-exp", tmp_path, skill=SEED_SKILL) == 0

    body = Path(SEED_SKILL).read_text(encoding="utf-8").strip()
    assert seen["skill"] and seen["skill"].strip() == body
    assert all(body not in p for p in prompts), "skill 正文不应再出现在用户消息里"


def test_contamination_marks_both_conditions(tmp_path, monkeypatch):
    """两边 manifest 的模型不同 → 两份 verdict 都 contaminated。"""
    class ModelA:
        name = "model-a"
        live = True

        def complete(self, prompt, skill_text=None):
            return '{"decision":"unknown","sources":[]}', {"input_tokens": 10, "output_tokens": 5}

    monkeypatch.setattr(runner, "build_model", lambda cfg: ModelA())
    assert runner.run(TASK_PACK, "B-no-skill", 1, "mix-exp", tmp_path) == 0

    class ModelB:
        name = "model-b"
        live = True

        def complete(self, prompt, skill_text=None):
            return '{"decision":"unknown","sources":[]}', {"input_tokens": 10, "output_tokens": 5}

    monkeypatch.setattr(runner, "build_model", lambda cfg: ModelB())
    assert runner.run(TASK_PACK, "C-seed-skill", 1, "mix-exp", tmp_path, skill=SEED_SKILL) == 0

    for cond in ("B-no-skill", "C-seed-skill"):
        for vp in (tmp_path / "mix-exp" / cond).glob("*/*/verdict.json"):
            assert json.loads(vp.read_text(encoding="utf-8"))["contaminated"] is True
