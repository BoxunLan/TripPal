"""A 线夹具测试（规格「A 线」实现顺序第 1 步的断言逐条落地）。

本项目主题是「外国人来华」（inbound，en），所以这里所有夹具都指 tripPal 那套。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from demand_task_factory import contracts, build

REPO_ROOT = Path(__file__).resolve().parents[3]
INPUT = REPO_ROOT / "fixtures" / "demand_records_trippal_v1" / "records.jsonl"
SCENARIOS = REPO_ROOT / "fixtures" / "trippal_v1" / "scenarios.json"
CONFIG = REPO_ROOT / "config" / "default.yaml"
TASK_PACK = REPO_ROOT / "fixtures" / "task_pack_trippal_v1" / "task_pack.jsonl"
LEAKAGE_TERMS = ["SKILL.md", "seed skill", "标准答案"]


def run_build(inp: Path, out: Path, config: Path = CONFIG, scenarios: Path = SCENARIOS):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "demand_task_factory",
            "build",
            "--input",
            str(inp),
            "--config",
            str(config),
            "--scenarios",
            str(scenarios),
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("a-out")
    proc = run_build(INPUT, out)
    assert proc.returncode == 0, proc.stderr
    return out


def test_outputs_exist(built):
    for name in ("scenario_catalog.jsonl", "task_pack.jsonl", "provenance.json", "leakage_report.json"):
        assert (built / name).exists(), name


def test_output_against_schemas(built):
    for row in contracts.load_jsonl(built / "scenario_catalog.jsonl"):
        assert contracts.iter_errors("scenario", row) == []
    for row in contracts.load_jsonl(built / "task_pack.jsonl"):
        assert contracts.iter_errors("task", row) == []


def test_evidence_ids_exist_in_input(built):
    known = {r["record_id"] for r in contracts.load_jsonl(INPUT)}
    for row in contracts.load_jsonl(built / "scenario_catalog.jsonl"):
        assert row["evidence_record_ids"], row["scenario_id"]
        assert set(row["evidence_record_ids"]) <= known


def test_prompt_has_no_leakage(built):
    for row in contracts.load_jsonl(built / "task_pack.jsonl"):
        for term in LEAKAGE_TERMS:
            assert term not in row["prompt"], (row["task_id"], term)
    assert json.loads((built / "leakage_report.json").read_text(encoding="utf-8"))[
        "prompt_violations"
    ] == []


def test_fact_verifier_values_come_from_fixtures_not_answers(built):
    """规格 A 线第 4 步：fact_lookup 的 verifier 至少有一条 contains，值来自证据的 query_or_topic。

    在其之上我们再加了一条「必须引用检索到的来源站点」。这条同样必须是**夹具派生**的
    （取自 pages.json 的 source_url），不能是手写答案 —— 否则就等于把答案写进判据。
    """
    catalog = {s["scenario_id"]: s for s in contracts.load_jsonl(built / "scenario_catalog.jsonl")}
    hosts = {
        build.source_host((page or {}).get("source_url", ""))
        for page in build.load_tool_pages().values()
    }
    for row in contracts.load_jsonl(built / "task_pack.jsonl"):
        if row["task_type"] != "fact_lookup":
            continue
        values = [a["value"] for a in row["verifier"]["assertions"] if a["op"] == "contains"]
        assert values, row["task_id"]
        cluster = catalog[row["scenario_id"]]["query_cluster"]
        assert any(v in cluster for v in values), row["task_id"]
        for extra in (v for v in values if v not in cluster):
            assert extra in hosts, (row["task_id"], extra)


def test_every_fact_card_has_a_retrievable_source(built):
    """来源断言要能被满足：该场景的代表查询必须在工具夹具里查得到页面。"""
    pages = build.load_tool_pages()
    for row in contracts.load_jsonl(built / "task_pack.jsonl"):
        if row["task_type"] != "fact_lookup":
            continue
        representative = row["demand_evidence"]["query_cluster"][0]
        assert representative in pages, f"{row['task_id']} 的 {representative!r} 在工具夹具里没有页面"
        expected_host = build.source_host(pages[representative]["source_url"])
        values = [a["value"] for a in row["verifier"]["assertions"] if a["op"] == "contains"]
        assert expected_host in values, row["task_id"]


def test_fact_prompt_states_its_output_contract(built):
    """verifier 查「复述了用户问题」，题面就必须要求复述 —— 判据不能是隐藏的。

    本项目输出语言是 en，所以题面用英文表述同一件事。
    """
    for row in contracts.load_jsonl(built / "task_pack.jsonl"):
        if row["task_type"] != "fact_lookup":
            continue
        assert "restate the user's question" in row["prompt"], row["task_id"]


def test_train_holdout_same_scenario_id(built):
    rows = contracts.load_jsonl(built / "task_pack.jsonl")
    assert {r["split"] for r in rows} == {"train", "holdout"}
    by_split = {}
    for r in rows:
        by_split.setdefault(r["split"], set()).add(r["scenario_id"])
    assert not (by_split["train"] & by_split["holdout"])
    catalog = {s["scenario_id"]: s for s in contracts.load_jsonl(built / "scenario_catalog.jsonl")}
    train_clusters = {tuple(catalog[s]["query_cluster"]) for s in by_split["train"]}
    holdout_clusters = {tuple(catalog[s]["query_cluster"]) for s in by_split["holdout"]}
    assert not (train_clusters & holdout_clusters)


def test_two_task_types_and_two_cards_per_scenario(built):
    rows = contracts.load_jsonl(built / "task_pack.jsonl")
    assert {r["task_type"] for r in rows} == {"fact_lookup", "conditional_decision"}
    per_scenario: dict[str, set[str]] = {}
    for r in rows:
        per_scenario.setdefault(r["scenario_id"], set()).add(r["task_type"])
    assert all(len(v) == 2 for v in per_scenario.values())


def test_bad_row_is_skipped_and_reported(built, tmp_path):
    bad = tmp_path / "with_bad.jsonl"
    lines = INPUT.read_text(encoding="utf-8").splitlines()
    broken = json.loads(lines[0])
    broken["record_id"] = "r-broken"
    del broken["metric_value"]
    bad.write_text("\n".join(lines + [json.dumps(broken, ensure_ascii=False)]) + "\n", encoding="utf-8")

    out = tmp_path / "out"
    proc = run_build(bad, out)
    assert proc.returncode == 0, proc.stderr
    errors = json.loads(proc.stderr)
    assert any(e["record_id"] == "r-broken" and "metric_value" in e["message"] for e in errors)
    assert (out / "task_pack.jsonl").exists()

    provenance = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["record_count"] == len(lines)


def test_below_floor_exits_2(tmp_path):
    tiny = tmp_path / "tiny.jsonl"
    tiny.write_text(
        json.dumps(json.loads(INPUT.read_text(encoding="utf-8").splitlines()[0]), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    proc = run_build(tiny, out)
    assert proc.returncode == 2
    errors = json.loads(proc.stderr)
    assert any("scenario_floor" in e["message"] for e in errors)
    assert not (out / "task_pack.jsonl").exists()


def test_import_snapshot_missing_file_writes_blocker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "integration" / "blockers").mkdir(parents=True)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "demand_task_factory",
            "import-snapshot",
            "--raw",
            "data/snapshots/trends/raw.csv",
            "--geo",
            "GLOBAL",
            "--language",
            "en",
            "--time-window",
            "2026-08-01/2026-08-31",
            "--retrieved-at",
            "2026-09-29T09:00:00+08:00",
            "--out",
            "data/snapshots/trends/records.jsonl",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 2
    assert (tmp_path / "integration" / "blockers" / "missing-trends-snapshot.md").exists()


def test_provenance_hashes(built):
    prov = json.loads((built / "provenance.json").read_text(encoding="utf-8"))
    assert prov["schema_version"] == "v1"
    assert len(prov["input_sha256"]) == 64
    assert len(prov["config_sha256"]) == 64
    assert prov["scenario_count"] >= 1


def test_out_dir_is_created_when_missing(tmp_path):
    out = tmp_path / "deep" / "nested"
    proc = run_build(INPUT, out)
    assert proc.returncode == 0, proc.stderr
    assert (out / "scenario_catalog.jsonl").exists()
    shutil.rmtree(out)


# --- 主题校验：本项目是「外国人来华」，场景里不许混进非中国语境 ----------------


def test_scenarios_are_in_china_context():
    """内置场景集必须全部落在中国语境里（这是主题约束，不是数据恰好如此）。"""
    cfg = build.load_config(CONFIG)
    records, _ = build.load_records(INPUT)
    doc = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    scenarios, _dropped = build.build_scenarios_from_import(doc, records, cfg)
    catalog = build.scenario_catalog(scenarios, cfg)
    assert catalog, "场景集不该为空"
    assert build.china_context_violations(catalog) == []


def test_china_context_check_catches_off_topic_rows(tmp_path):
    """校验器要真的会报错：把一条境外语境的场景塞进去，必须被点出来。"""
    catalog = [
        {
            "scenario_id": "S99",
            "title": "用户需求簇：Kyoto",
            "query_cluster": ["Kyoto temple opening hours", "Tokyo subway pass"],
        }
    ]
    routing = tmp_path / "routing.json"
    routing.write_text(
        json.dumps(
            {
                "schema_version": "trippal-routing-v1",
                "china_terms": ["china", "chinese"],
                "china_context": ["great wall", "beijing"],
                "china_context_phrases": [],
                "scenarios": {"S99": {"pages": ["kyoto-temples.txt"]}},
            }
        ),
        encoding="utf-8",
    )
    violations = build.china_context_violations(catalog, routing)
    assert violations and violations[0]["scenario_id"] == "S99"
    assert any("Kyoto temple opening hours" in r for r in violations[0]["reasons"])


def test_off_topic_build_exits_2(tmp_path):
    """require_china_context=true 时，混入非中国语境的场景必须整单失败（exit 2）。"""
    bad_scenarios = tmp_path / "bad_scenarios.json"
    bad_scenarios.write_text(
        json.dumps(
            {
                "schema_version": "trippal-scenarios-v1",
                "vertical": "inbound_foreign_tourists_to_china",
                "scenarios": [
                    {
                        "tp_id": "S01",
                        "name": "境外场景",
                        "query_cluster": ["Kyoto temple opening hours", "Tokyo subway pass"],
                    },
                    {
                        "tp_id": "S02",
                        "name": "境外场景二",
                        "query_cluster": ["Kyoto temple opening hours", "Tokyo subway pass"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    proc = run_build(INPUT, out, scenarios=bad_scenarios)
    assert proc.returncode == 2
    # 可能被「没有任何场景能对上需求记录」先拦下，也可能被主题闸门拦下 —— 两条都不产出任务包。
    assert not (out / "task_pack.jsonl").exists()


def test_conditional_prompt_is_english_for_this_theme(built):
    for row in contracts.load_jsonl(built / "task_pack.jsonl"):
        if row["task_type"] != "conditional_decision":
            continue
        assert "decision" in row["prompt"].lower()
        values = [
            a["value"]
            for a in row["verifier"]["assertions"]
            if a["op"] == "json_path_equals" and a["path"] == "$.decision"
        ]
        assert values, row["task_id"]
        assert values[0] in {"confirmed", "unknown", "denied"}


def test_fixture_task_pack_is_trippal_themed():
    """随仓库交付的那份 task_pack 也要是来华主题、且过 schema。"""
    rows = contracts.load_jsonl(TASK_PACK)
    assert rows, TASK_PACK
    assert {r["split"] for r in rows} == {"train", "holdout"}
    for row in rows:
        assert contracts.iter_errors("task", row) == [], row["task_id"]
        assert row["task_id"].startswith("fx-cn-"), row["task_id"]
        if row["task_type"] == "fact_lookup":
            assert row["initial_state"]["tool_data"].endswith("tool_data_trippal_v1/pages.json")
