"""B 线夹具选择：任务卡声明的 `initial_state.tool_data` 必须真正生效（deviations dev-009）。

原先 `execute_one` 把夹具路径硬编码成 DEFAULT_PAGES，任务卡写什么都没用 ——
声明了一份夹具却仍读另一份，`lookup_local` 产出与 verifier 断言对不上。
"""

from __future__ import annotations

import json
from pathlib import Path

from execution_evaluation import runner
from execution_evaluation.tool import DEFAULT_PAGES, lookup_local

REPO_ROOT = Path(__file__).resolve().parents[3]
TRIPPAL_PAGES = REPO_ROOT / "fixtures" / "tool_data_trippal_v1" / "pages.json"
TRIPPAL_REL = "fixtures/tool_data_trippal_v1/pages.json"
SHARED_PAGES = REPO_ROOT / "fixtures" / "tool_data_shared_v1" / "pages.json"
SHARED_REL = "fixtures/tool_data_shared_v1/pages.json"


# ------------------------------------------------------------------ 单元
def test_resolve_tool_data_defaults_when_undeclared():
    p, rel = runner.resolve_tool_data({"initial_state": {}})
    assert p == DEFAULT_PAGES
    assert rel.endswith("tool_data_trippal_v1/pages.json")


def test_resolve_tool_data_honors_declared_relative_path():
    p, rel = runner.resolve_tool_data({"initial_state": {"tool_data": SHARED_REL}})
    assert p == SHARED_PAGES
    assert rel == SHARED_REL


def test_resolve_tool_data_does_not_silently_fall_back():
    """声明了不存在的文件时**不许**回退到默认夹具：否则「读错文件」和「没命中」长得一样。"""
    p, _rel = runner.resolve_tool_data({"initial_state": {"tool_data": "fixtures/nope/pages.json"}})
    assert p != DEFAULT_PAGES
    assert not p.exists()


def test_tool_snapshot_reflects_declared_files():
    snap = runner.tool_snapshot_for(
        [{"initial_state": {}}, {"initial_state": {"tool_data": SHARED_REL}}]
    )
    assert snap["name"] == "lookup_local"
    assert {f["path"] for f in snap["files"]} == {
        "fixtures/tool_data_trippal_v1/pages.json",
        SHARED_REL,
    }
    assert len(snap["sha256"]) == 64


# ------------------------------------------------------------------ 端到端
def _task_pack(tmp_path: Path, query: str, tool_data: str = SHARED_REL) -> Path:
    task = {
        "schema_version": "v1",
        "task_id": "T-tool",
        "scenario_id": "S-tool",
        "split": "train",
        "task_type": "fact_lookup",
        "demand_evidence": {
            "source": "other",
            "query_cluster": [query],
            "geo": "GLOBAL",
            "time_window": "2025-09-29/2026-09-29",
        },
        "prompt": f'A user searched for "{query}". First restate the question, then answer.',
        "initial_state": {"tool_data": tool_data},
        "allowed_tools": ["lookup_local"],
        "expected_end_state": {"type": "string"},
        "verifier": {"kind": "deterministic", "assertions": [{"op": "contains", "value": query}]},
        "prohibited_leakage": ["skill", "answer_derivation"],
    }
    tp = tmp_path / "task_pack.jsonl"
    tp.write_text(json.dumps(task, ensure_ascii=False) + "\n", encoding="utf-8")
    return tp


def test_declared_tool_data_actually_drives_lookup(tmp_path):
    pages = json.loads(SHARED_PAGES.read_text(encoding="utf-8"))
    query = sorted(pages)[0]
    expected_url = pages[query]["source_url"]

    # 先用默认夹具反证：这条查询在默认夹具里查不到 —— 命中的那次一定是声明的夹具给的
    assert lookup_local(query, DEFAULT_PAGES)["text"] == ""

    tp = _task_pack(tmp_path, query)
    runs = tmp_path / "runs"
    assert runner.run(tp, "B-no-skill", 1, "tool-data-e2e", runs) == 0

    run_dir = next((runs / "tool-data-e2e" / "B-no-skill").glob("*/*"))
    lookup = json.loads((run_dir / "artifacts" / "lookup.json").read_text(encoding="utf-8"))
    assert lookup["source_url"] == expected_url
    assert lookup["text"]

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tool_snapshot"]["path"] == SHARED_REL

    # trace / log 里也要能看到用的是哪份夹具，而不是只有一个「命中」
    log = (run_dir / "log.md").read_text(encoding="utf-8")
    assert SHARED_REL in log
