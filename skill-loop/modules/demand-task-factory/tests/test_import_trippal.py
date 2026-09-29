"""TripPal 导入器（A 线新增的 `import-trippal`）与「导入场景目录」构建路径。

对应 deviations 的 dev-008（场景边界改用导入的策展目录）与 dev-010（新增 TripPal 垂直）。
全部用合成的最小 TripPal 目录跑，不依赖 TripPal-main 是否在机器上。
"""

from __future__ import annotations

import json
from pathlib import Path

from demand_task_factory import build, contracts, trippal

REPO_ROOT = Path(__file__).resolve().parents[3]


# ------------------------------------------------------------------ 最小来源树
PAIN_MD = """# TripPal 调研：外国人来华

## 6. 场景切分建议

| 编号 | 场景名 | 触发时点 | 用户目标 | 关键输入 | 成功判据 | 痛点编号 |
|---|---|---|---|---|---|---|
| S01 | 签证资格判定 | 行前 | 判定是否需要签证 | 护照、行程 | 得出明确结论 | P1 |
| S02 | 景区预约 | 行前 72h | 抢到门票 | 证件 | 拿到预约凭证 | P2,P3 |

首批试跑顺序：`S01 → S02`

## 7. 其他
"""

TSV = "\t".join(["id", "site", "title", "views", "score", "answers", "link"]) + "\n" + "\n".join(
    [
        "\t".join(["q1", "travel", "Do I need a Chinese visa for a 10-day trip?", "5000", "10", "2", "http://a"]),
        "\t".join(["q2", "travel", "How to book Forbidden City tickets in China as a foreigner?", "3000", "8", "1", "http://b"]),
        "\t".join(["q3", "travel", "Where to eat pizza in Italy?", "9000", "20", "5", "http://c"]),
    ]
) + "\n"

TRENDS_MD = """# Google Trends 采集结果（geo=US，today 12-m）

## 一、有完整时间线的词条（1 个）

| 词条 | 全年均值 | 前13周均值 | 近13周均值 | 峰值周 | 峰值 |
|---|---|---|---|---|---|
| china visa | 59.8 | 51.2 | 41.1 | May 31, 2026 | 100 |

## 二、相关查询与地区
"""

VISa_PAGE = """# source: https://example.cn/visa
# title: China visa FAQ
# 页面
China visa rules were updated on 2025-05-30. Most travellers need a visa.
"""

FC_PAGE = """# source: https://example.cn/forbidden-city
# title: Forbidden City ticket guide
# 页面
Book Forbidden City tickets in advance; they sell out quickly.
"""

ROUTING = {
    "schema_version": "trippal-routing-v1",
    "china_terms": ["china", "chinese"],
    "china_context": ["forbidden city"],
    "china_context_phrases": ["chinese visa", "in china"],
    "scenarios": {
        "S01": {"keywords": ["visa"], "pages": ["visa-faq.txt"], "trend_terms": ["china visa"]},
        "S02": {
            "keywords": ["forbidden city", "ticket"],
            "exclude": ["air ticket"],
            "pages": ["forbidden-city.txt"],
            "trend_terms": [],
        },
    },
}


def make_tree(root: Path) -> None:
    (root / "research" / "raw" / "clean").mkdir(parents=True)
    (root / "research" / "pain_points.md").write_text(PAIN_MD, encoding="utf-8")
    (root / "research" / "se-questions.tsv").write_text(TSV, encoding="utf-8")
    (root / "research" / "trends-summary.md").write_text(TRENDS_MD, encoding="utf-8")
    (root / "research" / "raw" / "clean" / "visa-faq.txt").write_text(VISa_PAGE, encoding="utf-8")
    (root / "research" / "raw" / "clean" / "forbidden-city.txt").write_text(FC_PAGE, encoding="utf-8")


def run_import(tmp_path: Path, scenario_target: int = 8):
    root = tmp_path / "trippal"
    make_tree(root)
    routing = tmp_path / "routing.json"
    routing.write_text(json.dumps(ROUTING, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    records = tmp_path / "records.jsonl"
    tool_data = tmp_path / "pages.json"
    rc = trippal.import_trippal(
        root,
        routing,
        out,
        records,
        tool_data,
        time_window="2025-09-29/2026-09-29",
        retrieved_at="2026-09-29T00:00:00+08:00",
        scenario_target=scenario_target,
    )
    return rc, out, records, tool_data


# ------------------------------------------------------------------ 单元
def test_keyword_hits_short_word_is_whole_word():
    # `book` 不能命中 `notebooks`（实测把「海关查不查 Kindle」分给了景区购票场景）
    assert trippal.keyword_hits("Do customs check my notebooks?", ["book"]) == 0
    assert trippal.keyword_hits("How to book attraction tickets", ["book"]) == 1
    # 长词按前缀命中
    assert trippal.keyword_hits("Ticket reservation", ["reserv"]) == 1
    # 多词/带连字符整体匹配
    assert trippal.keyword_hits("A high-speed rail guide", ["high-speed"]) == 1


def test_is_china_question_needs_china_context():
    weak, ctx, phrases = ["china", "chinese"], ["beijing", "alipay"], ["in china", "chinese visa"]
    # 词面有 China，语境却不在中国 -> 拒
    assert not trippal.is_china_question("Host a Chinese guest in Europe", weak, ctx, phrases)
    assert not trippal.is_china_question("Exchange Alipay money for cash in Thailand", weak, ctx, phrases)
    # 中国语境 -> 收
    assert trippal.is_china_question("Is Alipay widely accepted in China?", weak, ctx, phrases)
    assert trippal.is_china_question("How to apply for a Chinese visa", weak, ctx, phrases)
    # routing 没给语境表时退回旧行为（夹具里可能只给弱词表）
    assert trippal.is_china_question("anything about china", weak, [], [])


def test_route_questions_excludes_disambiguation():
    questions = [
        {"id": "1", "title": "How to reserve an air ticket for a Chinese visa", "views": 900, "score": 9},
        {"id": "2", "title": "The Great Wall of China: where to start a visit", "views": 100, "score": 1},
    ]
    spec = {"keywords": ["great wall", "reserve"], "exclude": ["air ticket", "flight"]}
    pages = [
        {
            "title": "Book China attraction tickets without a Chinese ID",
            "slug": "x.txt",
            "text": "",
            "source_url": "",
            "effective_date": "",
        }
    ]
    gate = (["china", "chinese"], ["great wall"], ["in china"])
    hits, page = trippal.route_questions(questions, spec, gate, pages, 6)
    assert [h["id"] for h in hits] == ["2"]
    assert page is pages[0]


def test_find_date_only_accepts_explicit_formats():
    assert trippal.find_date("effective 2025-05-30 per notice") == "2025-05-30"
    assert trippal.find_date("published 2026/01/07") == "2026-01-07"
    assert trippal.find_date("updated May 31, 2026 in the post") == "2026-05-31"
    # 认不出就留空 —— 不许拿抓取时间冒名顶替
    assert trippal.find_date("no date here") == ""


def test_parse_scenarios_reads_section6():
    rows, order = trippal.parse_scenarios(PAIN_MD)
    assert [r["tp_id"] for r in rows] == ["S01", "S02"]
    assert rows[1]["pain_point_ids"] == ["P2", "P3"]
    assert rows[0]["name"] == "签证资格判定"
    assert order == ["S01", "S02"]


def test_parse_trends_only_uses_the_dated_section():
    trends = trippal.parse_trends(TRENDS_MD)
    assert trends == {"china visa": 59.8}


# ------------------------------------------------------------------ 端到端
def test_import_produces_v1_valid_records_and_scenarios(tmp_path):
    rc, out, records_path, tool_data_path = run_import(tmp_path)
    assert rc == 0, out

    records = contracts.load_jsonl(records_path)
    assert records, "没有产出需求记录"
    for r in records:
        assert contracts.iter_errors("demand-record", r) == [], r
        assert r["source"] in ("other", "google_trends")
        # 不许把 SE 的 views 说成搜索次数：metric_value 取真实 views，metric_type=impressions
        if r["source"] == "other":
            assert r["metric_type"] == "impressions"

    doc = json.loads((out / "scenarios.json").read_text(encoding="utf-8"))
    assert doc["vertical"] == "inbound_foreign_tourists_to_china"
    tp_ids = [s["tp_id"] for s in doc["scenarios"]]
    assert tp_ids == ["S01", "S02"]
    for s in doc["scenarios"]:
        assert s["record_ids"], s["tp_id"]
        known = {r["record_id"] for r in records}
        assert set(s["record_ids"]) <= known
        # 代表问题必须落在中国语境里，且配到了权威页面
        assert trippal.is_china_question(s["representative"], *(
            ROUTING["china_terms"], ROUTING["china_context"], ROUTING["china_context_phrases"]
        )) or "Forbidden City" in s["representative"]
        assert s["page"]["url"].startswith("http")


def test_import_tool_data_matches_representatives_and_carries_source_url(tmp_path):
    rc, out, _records, tool_data_path = run_import(tmp_path)
    assert rc == 0
    doc = json.loads((out / "scenarios.json").read_text(encoding="utf-8"))
    pages = json.loads(Path(tool_data_path).read_text(encoding="utf-8"))
    for s in doc["scenarios"]:
        # 代表问题就是 lookup_local 的查询键：它必须在夹具里有页面，否则卡无法自洽
        assert s["representative"] in pages, s["tp_id"]
        assert pages[s["representative"]]["source_url"] == s["page"]["url"]
        assert pages[s["representative"]]["text"]


def test_import_report_is_honest_about_sources(tmp_path):
    rc, out, _records, _tool = run_import(tmp_path)
    assert rc == 0
    report = json.loads((out / "import_report.json").read_text(encoding="utf-8"))
    assert report["scenarios_in_source"] == 2
    assert report["records_by_source"]["se_questions"] >= 1
    # 离线率：三张真实问题里只有两张落在中国语境，第三张（意大利披萨）必须被剔掉
    assert report["questions_total"] == 3
    assert report["questions_china_relevant"] == 2


def test_scenario_target_drops_but_reports(tmp_path):
    rc, out, _records, _tool = run_import(tmp_path, scenario_target=1)
    assert rc == 0
    report = json.loads((out / "import_report.json").read_text(encoding="utf-8"))
    assert report["scenarios_kept"] == 1
    assert [d["tp_id"] for d in report["dropped_by_scenario_target"]] == ["S02"]


def test_missing_sources_exit_2(tmp_path, capsys):
    root = tmp_path / "empty"
    root.mkdir()
    rc = trippal.import_trippal(root, tmp_path / "r.json", tmp_path / "out", tmp_path / "r.jsonl", tmp_path / "p.json")
    assert rc == 2
    err = json.loads(capsys.readouterr().err)
    assert err["error"] == "TripPal 来源缺失"
    assert len(err["missing"]) == 3


# ------------------------------------------------------------------ 导入场景 → 任务包
def test_build_with_imported_scenarios_uses_curated_boundaries(tmp_path):
    rc, out, records_path, tool_data_path = run_import(tmp_path)
    assert rc == 0

    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "geo: GLOBAL\n"
        "language: en\n"
        "time_window: 2025-09-29/2026-09-29\n"
        "scenario_target: 8\n"
        "scenario_floor: 3\n"
        f"tool_data: {tool_data_path.as_posix()}\n"
        "prompt_language: en\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "a-out"
    rc = build.build(records_path, cfg, out_dir, scenarios_path=out / "scenarios.json")
    assert rc == 0

    tasks = contracts.load_jsonl(out_dir / "task_pack.jsonl")
    for t in tasks:
        assert contracts.iter_errors("task", t) == [], t
        # v1 只允许两种 task_type：策展场景也必须落回 v1（contracts/v2 的 artifact_card 本期不启用）
        assert t["task_type"] in ("fact_lookup", "conditional_decision")
    assert {t["task_id"] for t in tasks} == {
        "TP-S01-fact", "TP-S01-cond", "TP-S02-fact", "TP-S02-cond"
    }

    catalog = {s["scenario_id"]: s for s in contracts.load_jsonl(out_dir / "scenario_catalog.jsonl")}
    for row in catalog.values():
        assert contracts.iter_errors("scenario", row) == [], row
        assert row["origin"] == "imported"
        assert row["tripal"]["success_criteria"]

    prov = json.loads((out_dir / "provenance.json").read_text(encoding="utf-8"))
    assert prov["scenario_source"]["mode"] == "imported"
    assert prov["scenario_count"] == 2
    # split：场景按 id 排序，最后一个场景的两张卡进 holdout
    assert {t["split"] for t in tasks if t["scenario_id"] == "TP-S02"} == {"holdout"}
    assert {t["split"] for t in tasks if t["scenario_id"] == "TP-S01"} == {"train"}


def test_build_without_scenarios_still_clusters(tmp_path):
    """不传 --scenarios 时行为与旧版一致：走聚类，场景 id 是 S01... 而非 TP-..."""
    records_path = REPO_ROOT / "fixtures" / "demand_records_trippal_v1" / "records.jsonl"
    out_dir = tmp_path / "clustered"
    rc = build.build(records_path, REPO_ROOT / "config" / "default.yaml", out_dir)
    assert rc == 0
    catalog = contracts.load_jsonl(out_dir / "scenario_catalog.jsonl")
    assert all(not s["scenario_id"].startswith("TP-") for s in catalog)
    assert all("origin" not in s for s in catalog)
    prov = json.loads((out_dir / "provenance.json").read_text(encoding="utf-8"))
    assert prov["scenario_source"]["mode"] == "clustered"
