"""dataset v2 生成器的门禁测试。

这些测试把 `docs/dataset-v2.md` 里的七道门变成可执行断言：
任何人都不能在不触发失败的情况下把数据集改退化。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demand_task_factory import dataset_v2 as v2


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("out-v2")
    pages = tmp_path_factory.mktemp("pages") / "pages.json"
    report = v2.build_dataset_v2(out, pages)
    cards = [json.loads(l) for l in (out / "task_pack.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return report, cards, out, pages


def test_all_gates_pass(built):
    report, _, _, _ = built
    assert report["ok"] is True, report["failures"]
    for name, gate in report["gates"].items():
        if name == "G2_unique":
            assert gate["dup_task_id"] == 0 and gate["dup_prompt_verifier"] == 0
        if name == "G1_schema_v2":
            assert gate["invalid"] == 0
        if name == "G4_substantive":
            assert gate["weak_cards"] == 0
        if name == "G6_leakage":
            assert gate["leaked"] == 0
        if name == "G7_group_split":
            assert gate["leaky_groups"] == 0


def test_scale_and_family_mix(built):
    report, _, _, _ = built
    counts = report["counts"]
    assert counts["total"] >= 300, "目标 ~300 张"
    assert counts["by_family"]["A"] >= 30, "A 族（有据事实答）：受 12345 官方问答与规则页供给上限约束"
    assert counts["by_family"]["B"] >= 100
    assert counts["by_family"]["C"] >= 80
    assert counts["by_family"]["D"] >= 35
    assert counts["by_family"]["E"] == 20
    assert counts["by_family"]["F"] == 24, "F 族：到达后义务/票务决策（动作空间不同于 B）"
    assert counts["by_type"]["artifact_card"] == counts["by_family"]["C"]


def test_every_scenario_has_cards(built):
    """12 个场景都要有卡，否则 SKILL 会在某些场景上无据可依。"""
    report, _, _, _ = built
    covered = set(report["counts"]["by_scenario"])
    expected = {f"TP-S{i:02d}" for i in range(1, 13)}
    assert expected <= covered, f"缺场景：{sorted(expected - covered)}"


def test_fact_assertions_are_verbatim_from_their_page(built):
    """A 族的实质断言必须是所给页面正文的精确子串 —— 这是"有据"二字的全部含义。"""
    _, cards, _, pages_path = built
    pages = json.loads(Path(pages_path).read_text(encoding="utf-8"))
    checked = 0
    for c in cards:
        if not c["task_id"].startswith("V2A"):
            continue
        page = pages.get(c["demand_evidence"]["query_cluster"][0])
        assert page is not None, c["task_id"]
        for a in c["verifier"]["assertions"]:
            if a["op"] == "contains" and len(str(a["value"])) > 12:
                assert str(a["value"]) in page["text"], f"{c['task_id']} 的断言不在页面正文里"
                checked += 1
        date = c["expected_end_state"].get("date")
        if date:
            assert date in page["text"], f"{c['task_id']} 的日期不在页面正文里"
    assert checked >= 25, f"逐字事实断言过少：{checked}"


def test_splits_are_group_level_and_cover_every_family(built):
    report, cards, _, _ = built
    groups: dict[str, set] = {}
    for c in cards:
        groups.setdefault(c["split_group"], set()).add(c["split"])
    assert all(len(s) == 1 for s in groups.values()), "同一分组不得跨 train/holdout"
    holdout_fams = {c["task_id"][2] for c in cards if c["split"] == "holdout"}
    assert holdout_fams == {"A", "B", "C", "D", "E", "F"}, f"holdout 必须覆盖每个族，实际 {holdout_fams}"
    train, hold = report["counts"]["by_split"]["train"], report["counts"]["by_split"]["holdout"]
    assert 0.12 <= hold / (train + hold) <= 0.35, f"holdout 占比异常：{hold}/{train + hold}"


def test_rule_table_matches_every_pattern_intent():
    """规则表是唯一真值来源：每个 B 模式的期望值都必须由 decide() 推导出来。"""
    for pattern, base, count, want, pool in v2.B_PATTERNS:
        passports = pool or sorted(v2.TRANSIT_OK if not base.get("hainan_only") else v2.HAINAN_OK)
        for i in range(count):
            params = v2._b_variant_params(base, passports[i % len(passports)], i)
            decision, rule_id = v2.decide(params)
            assert decision == want, f"{pattern}[{i}] 期望 {want}，规则表给 {decision}（{rule_id}）"


def test_every_rule_page_exists_and_is_citable():
    for rule in v2.RULES.values():
        body, url, eff = v2.load_page(rule.slug)
        assert len(body) >= v2.MIN_PAGE_CHARS, f"{rule.slug} 正文过短"
        assert url.startswith("http"), f"{rule.slug} 缺 source_url"
    pages = v2.build_pages()
    assert len(pages) >= 10
    assert all(p["text"].startswith("[source] http") for p in pages.values())


def test_verifiers_are_substantive_not_echo():
    """回归测试：v1 的病是"断言只查问题原串"。v2 任何一张卡都不许这样。"""
    pages = v2.build_pages()
    cards = v2.build_family_b(pages) + v2.build_family_c(pages) + v2.build_family_e(pages) + v2.build_family_d(pages)
    for c in cards:
        question = c["demand_evidence"]["query_cluster"][0]
        for a in c["verifier"]["assertions"]:
            value = a.get("value")
            if isinstance(value, str) and value == question and a["op"] == "contains":
                raise AssertionError(f"{c['task_id']} 又出现了「复述问题」式断言")


def test_b_family_decision_balance(built):
    report, _, _, _ = built
    dist = report["gates"]["G3_balance"]["family_B_decisions"]
    total = sum(dist.values())
    for decision, n in dist.items():
        assert n / total >= 0.15, f"{decision} 占比 {n / total:.2f} 低于 15%"


def test_oracle_answer_satisfies_every_card(built):
    """题目可解性：按 expected 构造的完美答案必须通过每张卡的全部断言。

    这是最能说明"评测有意义"的一条 —— 如果连标准答案都过不了，卡本身是坏的，
    模型答错就不能算模型的错。
    """
    _, cards, _, pages_path = built
    pages = json.loads(Path(pages_path).read_text(encoding="utf-8"))
    bad = []
    for c in cards:
        text, parsed = v2.oracle_answer(c, pages)
        results = v2._eval_assertions(c["verifier"]["assertions"], text, parsed)
        if not all(results):
            failed = [a for a, ok in zip(c["verifier"]["assertions"], results) if not ok]
            bad.append((c["task_id"], [a.get("op") for a in failed]))
    assert not bad, f"标准答案过不了的卡：{bad[:5]}（共 {len(bad)}）"


def test_local_evaluator_matches_b_line_verifier():
    """A 线为了不 import B 线，自己复刻了 5 个算子；这里与 B 线真实现对拍，防止语义漂移。

    跨模块 import 只出现在测试里（运行时三条线仍然互不 import）。
    """
    from execution_evaluation import verifier as real

    cases = [
        ([{"op": "contains", "value": "abc"}], "xx abc yy", None),
        ([{"op": "contains", "value": "zzz"}], "xx abc yy", None),
        ([{"op": "regex", "value": r"(?i)240|10 days"}], "within 10 DAYS of entry", None),
        ([{"op": "json_path_equals", "path": "$.decision", "value": "use_visa"}],
         '{"decision": "use_visa"}', {"decision": "use_visa"}),
        ([{"op": "json_path_equals", "path": "$.decision", "value": "eligible"}],
         '{"decision": "use_visa"}', {"decision": "use_visa"}),
        ([{"op": "not_contains", "value": ["banned", "also"]}], "clean text", None),
        ([{"op": "not_contains", "value": ["banned", "also"]}], "this is banned", None),
        ([{"op": "any_of", "value": ["first", "second"]}], "the second one", None),
        ([{"op": "any_of", "value": ["first", "second"]}], "neither", None),
    ]
    for assertions, text, parsed in cases:
        mine = v2._eval_assertions(assertions, text, parsed)
        theirs = [r["passed"] for r in real.evaluate(assertions, text, parsed)]
        assert mine == theirs, f"{assertions} 语义漂移：本地={mine} B线={theirs}"


def test_generation_is_deterministic(tmp_path):
    first = v2.build_dataset_v2(tmp_path / "a", tmp_path / "a" / "pages.json")
    second = v2.build_dataset_v2(tmp_path / "b", tmp_path / "b" / "pages.json")
    assert first["counts"] == second["counts"]
    a = (tmp_path / "a" / "provenance.json").read_text(encoding="utf-8")
    b = (tmp_path / "b" / "provenance.json").read_text(encoding="utf-8")
    assert json.loads(a)["pack_sha256"] == json.loads(b)["pack_sha256"]


def test_generation_is_deterministic_across_processes(tmp_path):
    """跨进程确定性：用不同 PYTHONHASHSEED 各构建一次，pack 摘要必须一致。

    为什么必须跨进程：`set`/`dict` 的迭代顺序依赖 PYTHONHASHSEED，同一进程里连续构建两次
    会得到相同的顺序，于是"同进程比对"漏掉了这类漂移 —— D2 的页面配对就这样漂过一轮
    （17 张卡的 split_group/split 每次构建都不同）。
    """
    import os
    import subprocess
    import sys

    hashes = []
    for seed in ("1", "7"):
        out = tmp_path / f"seed{seed}"
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8", PYTHONPATH="")
        proc = subprocess.run(
            [sys.executable, "-m", "demand_task_factory", "build-dataset-v2",
             "--out", str(out), "--pages", str(out / "pages.json")],
            env=env, capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr[-500:]
        prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
        hashes.append(prov["pack_sha256"])
    assert hashes[0] == hashes[1], f"跨进程不可复现：{hashes}"


def test_artifact_cards_declare_dimensions(built):
    _, cards, _, _ = built
    art = [c for c in cards if c["task_type"] == "artifact_card"]
    assert art
    for c in art:
        assert c["artifact"]["format"] == "checklist"
        assert c["artifact"]["dimensions"], c["task_id"]


def test_v1_pack_still_reproducible():
    """v2 不得影响 v1：v1 的 build 入口与 schema 保持可用。"""
    from demand_task_factory import contracts

    schema = Path(contracts.schema_path("task"))
    assert schema.exists() and "v1" in str(schema)
    assert "artifact_card" not in json.dumps(contracts.load_schema("task"))
