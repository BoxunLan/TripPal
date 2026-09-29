"""契约测试：每份 schema 都要有「合法样例通过 / 缺必填字段失败 / 非法取值失败」。"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from demand_task_factory import contracts

REPO_ROOT = Path(__file__).resolve().parents[3]

TIME_WINDOW = "2026-08-01/2026-08-31"
HEX64 = "a" * 64

SAMPLES: dict[str, dict] = {
    "demand-record": {
        "schema_version": "v1",
        "record_id": "r-1",
        "source": "google_trends",
        "query_or_topic": "China visa-free transit policy",
        "geo": "GLOBAL",
        "language": "en",
        "time_window": TIME_WINDOW,
        "metric_type": "relative_interest",
        "metric_value": 100,
        "access_scope": "public_aggregate",
        "retrieved_at": "2026-09-29T09:00:00+08:00",
    },
    "scenario": {
        "schema_version": "v1",
        "scenario_id": "S01",
        "title": "用户需求簇：China visa",
        "pool": "high_frequency",
        "query_cluster": ["China visa-free transit policy"],
        "evidence_record_ids": ["r-1"],
        "geo": "GLOBAL",
        "language": "en",
        "time_window": TIME_WINDOW,
    },
    "task": {
        "schema_version": "v1",
        "task_id": "S01-fact",
        "scenario_id": "S01",
        "split": "train",
        "task_type": "fact_lookup",
        "demand_evidence": {
            "source": "google_trends",
            "query_cluster": ["China visa-free transit policy"],
            "geo": "GLOBAL",
            "time_window": TIME_WINDOW,
        },
        "prompt": "用户搜索了「China visa-free transit policy」。请给出结论。",
        "initial_state": {},
        "allowed_tools": ["lookup_local"],
        "expected_end_state": {"type": "string"},
        "verifier": {"kind": "deterministic", "assertions": [{"op": "contains", "value": "China visa-free transit policy"}]},
        "prohibited_leakage": ["skill", "answer_derivation"],
    },
    "run-manifest": {
        "schema_version": "v1",
        "experiment_id": "fixture-b1",
        "condition": "B-no-skill",
        "model": "fake-model-v1",
        "temperature": 0,
        "timeout_seconds": 60,
        "max_tokens": 2000,
        "tool_snapshot": {"name": "lookup_local", "sha256": HEX64},
        "skill_snapshot": {"loaded": False},
        "task_pack_sha256": HEX64,
    },
    "trace-event": {
        "schema_version": "v1",
        "step": 1,
        "timestamp": "2026-09-29T10:00:00+08:00",
        "action": "read_task",
        "observation": "task=S01-fact",
        "decision": "开始执行",
        "skipped_or_abandoned": "",
        "cost": {"input_tokens": 10, "output_tokens": 2, "tool_calls": 0},
    },
    "verdict": {
        "schema_version": "v1",
        "task_id": "S01-fact",
        "run_n": 1,
        "live": False,
        "status": "fail",
        "overall_pass": False,
        "attribution": "knowledge_gap",
        "contaminated": False,
        "scores": {"outcome": 0.0, "process": 0.5, "evidence": 0.0, "efficiency": 0.6},
    },
    "skill-change": {
        "schema_version": "v1",
        "change_id": "ch-001",
        "operation": "add",
        "target_section": "来源检查",
        "evidence_tasks": ["S01-fact"],
        "failure_pattern": "no source",
        "proposed_change": "回答政策问题必须给出来源与生效日期",
        "expected_effect": "减少无来源断言",
        "regression_risk": "可能让回答变长",
        "acceptance_test": "回答里每条政策断言都能追到来源与生效日期",
    },
}

MISSING_FIELD: dict[str, str] = {
    "demand-record": "metric_value",
    "scenario": "query_cluster",
    "task": "verifier",
    "run-manifest": "skill_snapshot",
    "trace-event": "cost",
    "verdict": "attribution",
    "skill-change": "acceptance_test",
}

BAD_VALUE: dict[str, tuple[str, object]] = {
    "demand-record": ("source", "google_search_console"),
    "scenario": ("pool", "medium_frequency"),
    "task": ("split", "validation"),
    "run-manifest": ("condition", "A-no-skill"),
    "trace-event": ("step", "one"),
    "verdict": ("status", "passed"),
    "skill-change": ("operation", "update"),
}

SCHEMA_NAMES = sorted(SAMPLES)


def test_contract_files_exist():
    for name in SCHEMA_NAMES:
        assert (REPO_ROOT / "contracts" / "v1" / f"{name}.schema.json").exists()


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_valid_sample_passes(name):
    assert contracts.iter_errors(name, SAMPLES[name]) == []


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_missing_required_field_fails(name):
    doc = copy.deepcopy(SAMPLES[name])
    del doc[MISSING_FIELD[name]]
    errors = contracts.iter_errors(name, doc)
    assert errors, f"{name} 缺 {MISSING_FIELD[name]} 竟然通过了"
    assert any(MISSING_FIELD[name] in e["message"] for e in errors)


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_bad_value_fails(name):
    field, bad = BAD_VALUE[name]
    doc = copy.deepcopy(SAMPLES[name])
    doc[field] = bad
    assert contracts.iter_errors(name, doc), f"{name}.{field}={bad!r} 竟然通过了"


def test_validate_cli_exit_codes(tmp_path):
    good = tmp_path / "good.json"
    bad = tmp_path / "bad.json"
    good.write_text(__import__("json").dumps(SAMPLES["verdict"]), encoding="utf-8")
    broken = copy.deepcopy(SAMPLES["verdict"])
    del broken["scores"]
    bad.write_text(__import__("json").dumps(broken), encoding="utf-8")
    assert contracts.main(["--schema", "verdict", "--data", str(good)]) == 0
    assert contracts.main(["--schema", "verdict", "--data", str(bad)]) == 2
