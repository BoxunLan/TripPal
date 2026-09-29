"""contracts/v2 的定义（本期不启用，但定义要可执行，并锁住 v1 没被动过）。

规格「不许改」第 1 条：不修改 contracts/v1；字段不够就新建 contracts/v2 并在 blocker 里说明，
**本期实现仍以 v1 为准**。这份测试就盯这两件事：v2 真的能表达 TripPal 的缺口，v1 真的还是老的。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from demand_task_factory import contracts

REPO_ROOT = Path(__file__).resolve().parents[3]
V2 = REPO_ROOT / "contracts" / "v2"
V1 = REPO_ROOT / "contracts" / "v1"


def _base_task() -> dict:
    return {
        "schema_version": "v2",
        "task_id": "t",
        "scenario_id": "s",
        "split": "train",
        "task_type": "artifact_card",
        "demand_evidence": {
            "source": "other",
            "query_cluster": ["q"],
            "geo": "GLOBAL",
            "time_window": "2025-09-29/2026-09-29",
        },
        "prompt": "make me a checklist",
        "initial_state": {},
        "allowed_tools": ["lookup_local"],
        "expected_end_state": {"type": "string"},
        "verifier": {"kind": "deterministic", "assertions": [{"op": "contains", "value": "q"}]},
        "prohibited_leakage": ["skill", "answer_derivation"],
        "artifact": {"dimensions": ["payment", "tooling"], "format": "checklist"},
    }


def test_v2_files_are_valid_json_schema():
    for name in ("task.schema.json", "scenario.schema.json"):
        doc = json.loads((V2 / name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(doc)


def test_v2_task_allows_artifact_card():
    assert contracts.iter_errors(V2 / "task.schema.json", _base_task()) == []


def test_v2_task_rejects_unknown_dimension():
    t = _base_task()
    t["artifact"]["dimensions"] = ["telepathy"]
    assert contracts.iter_errors(V2 / "task.schema.json", t)


def test_v1_task_still_rejects_artifact_card():
    """v1 一个字节都不能改：artifact_card 在 v1 里仍不合法。"""
    t = _base_task()
    t["schema_version"] = "v1"
    t.pop("artifact")
    assert contracts.iter_errors("task", t), "v1 竟然接受了 artifact_card —— v1 被改过了？"


def test_v2_scenario_accepts_curated_fields():
    row = {
        "schema_version": "v2",
        "scenario_id": "TP-S01",
        "title": "签证资格判定",
        "pool": "high_frequency",
        "query_cluster": ["Do I need a Chinese visa?"],
        "evidence_record_ids": ["tp-s01-q1"],
        "geo": "GLOBAL",
        "language": "en",
        "time_window": "2025-09-29/2026-09-29",
        "origin": "imported",
        "vertical": "inbound_foreign_tourists_to_china",
        "trigger_timing": "行前",
        "success_criteria": "得出明确结论",
        "pain_point_ids": ["P1"],
    }
    assert contracts.iter_errors(V2 / "scenario.schema.json", row) == []

    # v1 里策展字段**不是必填**：去掉它们 v1 仍通过（v1 不禁止附加字段，所以带它们也通过）。
    drop = {"origin", "vertical", "trigger_timing", "success_criteria", "pain_point_ids", "schema_version"}
    row_v1 = {**{k: v for k, v in row.items() if k not in drop}, "schema_version": "v1"}
    assert contracts.iter_errors("scenario", row_v1) == []


def test_v1_required_field_sets_are_unchanged():
    """把 v1 的必填集合钉住 —— 无意中把 v1 加严也会在这里炸出来。"""
    task = json.loads((V1 / "task.schema.json").read_text(encoding="utf-8"))
    scenario = json.loads((V1 / "scenario.schema.json").read_text(encoding="utf-8"))
    assert task["properties"]["task_type"]["enum"] == ["fact_lookup", "conditional_decision"]
    assert set(scenario["required"]) == {
        "schema_version",
        "scenario_id",
        "title",
        "pool",
        "query_cluster",
        "evidence_record_ids",
        "geo",
        "language",
        "time_window",
    }
