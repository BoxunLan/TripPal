"""A 线：需求记录 → 场景目录 + 任务包。

对应规格「A 线」的 5 步实现顺序。所有阈值与规则写死在代码里，不调用 LLM。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from . import contracts, trippal
from .config import load_config
from .normalize import cluster_keys, normalize

LEAKAGE_TERMS = ["SKILL.md", "seed skill", "标准答案"]

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_DATA = REPO_ROOT / "fixtures" / "tool_data_trippal_v1" / "pages.json"

# 项目主题是「外国人来中国旅行」。场景边界必须落在中国语境里，
# 闸门复用 trippal.is_china_question 的那套词表（routing 文件里可逐条复核）。
DEFAULT_ROUTING = REPO_ROOT / "fixtures" / "trippal_v1" / "scenario_routing.json"

# conditional_decision 卡的期望终态只描述字段结构，取值用桩模型能给出的哨兵值。
# 见模块 README「解释口径」第 3 条。
CONDITIONAL_SENTINEL = "unknown"

# 题面必须写明输出契约：verifier 要查「复述了用户问题」，题面就得要求复述。
# 原先题面只说「请给出结论」，却拿 contains(查询串) 判分 —— 判据没被传达，
# 同一张卡在 3 次运行里时过时不过，全是噪声。
# 英文模板给 TripPal 那条入境游客垂直用（cfg.prompt_language=en）。
FACT_PROMPT = {
    "zh": "用户搜索了「{q}」。请先复述用户搜索的问题，再给出结论。",
    "en": (
        "A user searched for \"{q}\". First restate the user's question verbatim, "
        "then give the conclusion and cite the source provided in the lookup result."
    ),
}
COND_PROMPT = {
    "zh": (
        "根据初始状态输出一个 JSON。这个任务只检查字段结构，不要求你给出任何"
        "具体的政策结论；在你没有可靠权威来源时，decision 字段填 unknown。"
    ),
    "en": (
        "Output a JSON object based on the initial state. This task only checks the field "
        "structure and does not require any concrete policy conclusion; when you have no "
        "reliable authoritative source, set the decision field to unknown."
    ),
}


def load_tool_pages(path: str | Path = TOOL_DATA) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def source_host(source_url: str) -> str:
    """从 source_url 里取出可核对的站点标识（去掉协议、路径与 www.）。

    用站点而不是整条 URL 做断言：模型常把链接写成 markdown、或不带尾部斜杠，
    整串匹配会误杀；站点标识（govt.chinadaily.com.cn）只要它引用了这个来源就一定出现。
    """
    if not source_url:
        return ""
    rest = source_url.split("://", 1)[-1]
    host = rest.split("/", 1)[0].split("?", 1)[0]
    return host[4:] if host.startswith("www.") else host


def fact_assertions(representative: str, pages: dict) -> list[dict]:
    """fact_lookup 的断言。

    规格 A 线第 4 步要求「至少有一条 contains，值来自该场景证据记录的 query_or_topic，
    不是手写答案」—— 第一条照做。在其之上再加一条：答案必须引用检索到的权威来源。

    为什么要加：只断言「复述了用户问题」时，一个答得再好的回答只要没原样复述查询串就判 0，
    而归因还会落到 knowledge_gap（写 skill 的那一支）—— 实测就是这个口径让回归门
    把一份本来合理的 skill 判成 rollback。加了来源断言，判据才从「像在回答问题」
    变成「答案可追溯到来源」。两条都由夹具派生，仍然不写手写答案。
    """
    assertions: list[dict] = [{"op": "contains", "value": representative}]
    host = source_host(((pages.get(representative) or {}).get("source_url") or ""))
    if host:
        assertions.append({"op": "contains", "value": host})
    return assertions



def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    p = Path(path)
    return sha256_bytes(p.read_bytes()) if p.exists() else sha256_bytes(b"")


def load_records(path: str | Path) -> tuple[list[dict], list[dict]]:
    """返回 (合法记录, 错误数组)。不合法的行被跳过，但错误要落到 stderr。"""
    valid: list[dict] = []
    errors: list[dict] = []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({"line": i, "record_id": "", "message": f"JSON 解析失败: {exc.msg}"})
            continue
        errs = contracts.iter_errors("demand-record", doc)
        if errs:
            errors.append(
                {
                    "line": i,
                    "record_id": doc.get("record_id", ""),
                    "message": "; ".join(f"{e['path']} {e['message']}" for e in errs),
                }
            )
            continue
        valid.append(doc)
    return valid, errors


def build_scenarios(records: list[dict], cfg: dict) -> list[dict]:
    by_id = {r["record_id"]: r for r in records}
    clusters = cluster_keys(records)

    scenarios: list[dict] = []
    for ids in clusters:
        recs = [by_id[i] for i in ids]
        pool = "rising" if any(r["metric_type"] == "rising_rate" for r in recs) else "high_frequency"
        scenarios.append(
            {
                "pool": pool,
                "record_ids": ids,
                "query_cluster": [r["query_or_topic"] for r in recs],
                "key": normalize(recs[0]["query_or_topic"]),
                "first_index": min(records.index(r) for r in recs),
                "evidence_source": "google_trends",
            }
        )

    # 场景池顺序：high_frequency 先，rising 后；池内按首次出现次序。
    scenarios.sort(key=lambda s: (0 if s["pool"] == "high_frequency" else 1, s["first_index"]))
    return scenarios


def trim_to_target(scenarios: list[dict], cfg: dict) -> list[dict]:
    """超过 `scenario_target` 时，rising 与 high_frequency 交替保留（规格 A 线第 3 步）。

    被截掉的场景不在这里报错：调用方（`build`）负责把它们写进 provenance，
    免得「规模上限」变成一次静默丢弃。
    """
    target = int(cfg["scenario_target"])
    if len(scenarios) <= target:
        return scenarios
    buckets = {
        "rising": [s for s in scenarios if s["pool"] == "rising"],
        "high_frequency": [s for s in scenarios if s["pool"] == "high_frequency"],
    }
    kept: list[dict] = []
    while len(kept) < target:
        progressed = False
        for p in ("rising", "high_frequency"):
            if buckets[p] and len(kept) < target:
                kept.append(buckets[p].pop(0))
                progressed = True
        if not progressed:
            break
    order = {id(s): i for i, s in enumerate(scenarios)}
    kept.sort(key=lambda s: order[id(s)])
    return kept


def assign_scenario_ids(scenarios: list[dict], title_prefix: str = "用户需求簇：") -> list[dict]:
    for n, s in enumerate(scenarios, start=1):
        s["scenario_id"] = f"S{n:02d}"
        s["title"] = f"{title_prefix}{s['key']}"
    return scenarios


def build_scenarios_from_import(doc: dict, records: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """用导入的策展场景目录当场景边界，跳过 bigram 聚类。

    为什么不聚类：TripPal §6 的场景是按「用户决策时点」手工切的，带触发时点、用户目标与
    成功判据 —— 这是比 bigram Jaccard 更强的上游产物。用真实问题串去聚类只会切出一堆
    只差几个字的微簇（同一场景内的问法彼此相似度远低于 0.6），把策展边界打散。
    这是对规格 A 线第 3 步的偏离，记 dev-008。
    """
    known = {r["record_id"]: r for r in records}
    out: list[dict] = []
    dropped: list[dict] = []
    for sc in doc.get("scenarios", []):
        ids = [i for i in sc.get("record_ids", []) if i in known]
        if not ids:
            dropped.append({"tp_id": sc.get("tp_id", ""), "reason": "no_evidence_record_in_input"})
            continue
        recs = [known[i] for i in ids]
        if any(r["metric_type"] == "rising_rate" for r in recs):
            pool = "rising"
        else:
            # 导入侧的 metric_type 是 impressions / relative_interest，没有 rising_rate：
            # 都归 high_frequency，不为凑池子编造飙升数据。
            pool = "high_frequency"
        sources = {r["source"] for r in recs}
        out.append(
            {
                "scenario_id": f"TP-{sc.get('tp_id', '')}",
                "title": f"TripPal {sc.get('tp_id', '')}：{sc.get('name', '')}",
                "pool": pool,
                "record_ids": ids,
                "query_cluster": sc.get("query_cluster") or [known[i]["query_or_topic"] for i in ids],
                "key": sc.get("tp_id", ""),
                "first_index": min(records.index(r) for r in recs),
                # 只有一个来源就写那个来源，混了来源就写 other —— 不把混合证据说成单一来源。
                "evidence_source": sources.pop() if len(sources) == 1 else "other",
                "origin": "imported",
                "tripal": {
                    "trigger_timing": sc.get("trigger_timing", ""),
                    "user_goal": sc.get("user_goal", ""),
                    "success_criteria": sc.get("success_criteria", ""),
                    "pain_point_ids": sc.get("pain_point_ids", []),
                },
            }
        )
    out.sort(key=lambda s: s["first_index"])
    return out, dropped



def build_tasks(scenarios: list[dict], cfg: dict) -> list[dict]:
    tasks: list[dict] = []
    tool_data = cfg.get("tool_data") or str(TOOL_DATA.relative_to(REPO_ROOT)).replace("\\", "/")
    pages_path = Path(tool_data)
    if not pages_path.is_absolute():
        pages_path = REPO_ROOT / pages_path
    pages = load_tool_pages(pages_path)
    lang = "en" if str(cfg.get("prompt_language", "zh")).startswith("en") else "zh"
    for s in scenarios:
        # 需求证据的来源按场景记实：导入的场景混了 Stack Exchange 与 Trends，
        # 不能一律写成 google_trends（旧日本线那批记录确实全是 Trends，行为不变）。
        evidence = {
            "source": s.get("evidence_source", "google_trends"),
            "query_cluster": s["query_cluster"],
            "geo": cfg["geo"],
            "time_window": cfg["time_window"],
        }
        representative = s["query_cluster"][0]
        common = {
            "schema_version": "v1",
            "scenario_id": s["scenario_id"],
            "demand_evidence": evidence,
            "allowed_tools": ["lookup_local"],
            "prohibited_leakage": ["skill", "answer_derivation"],
        }
        tasks.append(
            {
                **common,
                "task_id": f"{s['scenario_id']}-fact",
                "split": "train",
                "task_type": "fact_lookup",
                "prompt": FACT_PROMPT[lang].format(q=representative),
                "initial_state": {"tool_data": tool_data},
                "expected_end_state": {
                    "type": "string",
                    "note": "终态须先复述用户原查询串，并引用检索到的权威来源；见 verifier",
                },
                "verifier": {
                    "kind": "deterministic",
                    "assertions": fact_assertions(representative, pages),
                },
            }
        )
        tasks.append(
            {
                **common,
                "task_id": f"{s['scenario_id']}-cond",
                "split": "train",
                "task_type": "conditional_decision",
                "prompt": COND_PROMPT[lang],
                "initial_state": {"applicant_region": "华东", "trip_days": 5},
                "expected_end_state": {
                    "type": "object",
                    "required": ["decision"],
                    "note": "只描述字段结构，不写具体政策结论",
                },
                "verifier": {
                    "kind": "deterministic",
                    "assertions": [
                        {"op": "json_path_equals", "path": "$.decision", "value": CONDITIONAL_SENTINEL}
                    ],
                },
            }
        )

    # split：按 scenario_id 排序，最后一个场景的两张卡进 holdout。
    ordered = sorted(scenarios, key=lambda s: s["scenario_id"])
    last_id = ordered[-1]["scenario_id"] if ordered else None
    for t in tasks:
        t["split"] = "holdout" if t["scenario_id"] == last_id else "train"
    return tasks


def scenario_catalog(scenarios: list[dict], cfg: dict) -> list[dict]:
    rows: list[dict] = []
    for s in scenarios:
        row = {
            "schema_version": "v1",
            "scenario_id": s["scenario_id"],
            "title": s["title"],
            "pool": s["pool"],
            "query_cluster": s["query_cluster"],
            "evidence_record_ids": s["record_ids"],
            "geo": cfg["geo"],
            "language": cfg["language"],
            "time_window": cfg["time_window"],
        }
        if s.get("origin") == "imported":
            # scenario v1 没有禁止附加字段，策展来源就挂在 origin/tripal 下，
            # 需要进 v1 正式字段的部分（触发时点/成功判据/卡点维度）留给 contracts/v2。
            row["origin"] = "imported"
            row["tripal"] = s.get("tripal", {})
        rows.append(row)
    return rows


def china_context_violations(
    catalog: list[dict], routing_path: str | Path = DEFAULT_ROUTING
) -> list[dict]:
    """项目主题是「外国人来中国旅行」：每个场景都必须落在中国语境里。

    判据分两层，两层都过才算合规：

    1. **代表查询**（query_cluster 第一条，也就是会被写进题面的那条）必须过
       `trippal.is_china_question`（弱词 + 中国语境双命中）。这条是硬要求。
    2. **场景的检索面**必须是中国内容：该场景在 routing 里挂的页面 slug 至少有一个
       含 `china`（`mcg-best-esim-china.txt` 这种），或该场景本身就带 `trend_terms`
       里的中国词。场景是「来华」的，它检索的页面就该是讲中国的页面。

    为什么不去逐条查整簇：一个场景的 query_cluster 常常是从真实问答串里截的碎片
    （`china esim` 这种短关键词），它们**只出现在 routing 的 trend_terms 里**，
    不是题面用的代表查询。拿它们当违例依据会误伤合法的来华场景（实测 S08 上网场景
    的 6 条真实问题全部命中中国语境，却因为 3 个短关键词被误判）。

    违例**不静默放过**：调用方读到非空就报错退出，免得跑出一批「在泰国用 Alipay」
    这种词面带中国、语境不在中国的场景。
    """
    r = trippal.load_routing(routing_path)
    gate = (
        r.get("china_terms", []),
        r.get("china_context", []),
        r.get("china_context_phrases", []),
    )
    scenarios = r.get("scenarios", {})
    out: list[dict] = []
    for row in catalog:
        cluster = row.get("query_cluster") or []
        if not cluster:
            continue
        reasons: list[str] = []
        representative = cluster[0]
        if not trippal.is_china_question(representative, *gate):
            reasons.append(f"代表查询不在中国语境：{representative!r}")
        spec = scenarios.get(row.get("scenario_id", "")) or {}
        pages = spec.get("pages") or []
        if pages and not any("china" in p.lower() for p in pages):
            reasons.append(f"检索面不是中国内容（slug 全无 china）：{pages}")
        if reasons:
            out.append(
                {
                    "scenario_id": row.get("scenario_id", ""),
                    "title": row.get("title", ""),
                    "reasons": reasons,
                }
            )
    return out


def leakage_report(tasks: list[dict], catalog: list[dict]) -> dict:
    violations = []
    for t in tasks:
        for term in LEAKAGE_TERMS:
            if term in t["prompt"]:
                violations.append({"task_id": t["task_id"], "term": term, "field": "prompt"})
    key_of = {s["scenario_id"]: "|".join(sorted(normalize(q) for q in s["query_cluster"])) for s in catalog}
    train = {key_of[t["scenario_id"]] for t in tasks if t["split"] == "train"}
    holdout = {key_of[t["scenario_id"]] for t in tasks if t["split"] == "holdout"}
    return {
        "schema_version": "v1",
        "tasks_checked": len(tasks),
        "checked_terms": LEAKAGE_TERMS,
        "prompt_violations": violations,
        "split_cluster_overlap": sorted(train & holdout),
    }


def build(
    input_path: str | Path,
    config_path: str | Path,
    out_dir: str | Path,
    scenarios_path: str | Path | None = None,
) -> int:
    """`scenarios_path` 给了就用导入的策展场景目录当边界（dev-008），否则走聚类。"""
    cfg = load_config(config_path)
    records, errors = load_records(input_path)
    floor = int(cfg["scenario_floor"])

    if len(records) < floor:
        sys.stderr.write(
            json.dumps(
                errors
                + [
                    {
                        "line": 0,
                        "record_id": "",
                        "message": f"合法需求记录 {len(records)} 条，少于 scenario_floor={floor}，无法生成真实任务包",
                    }
                ],
                ensure_ascii=False,
            )
            + "\n"
        )
        return 2

    imported_dropped: list[dict] = []
    if scenarios_path:
        doc = json.loads(Path(scenarios_path).read_text(encoding="utf-8"))
        scenarios, imported_dropped = build_scenarios_from_import(doc, records, cfg)
        if not scenarios:
            sys.stderr.write(
                json.dumps(
                    errors
                    + [
                        {
                            "line": 0,
                            "record_id": "",
                            "message": "导入的场景目录里没有任何场景能对上需求记录",
                        }
                    ],
                    ensure_ascii=False,
                )
                + "\n"
            )
            return 2
    else:
        scenarios = assign_scenario_ids(build_scenarios(records, cfg))

    before = len(scenarios)
    trimmed = trim_to_target(scenarios, cfg)
    kept_ids = {s["scenario_id"] for s in trimmed}
    dropped_by_target = [
        {"scenario_id": s["scenario_id"], "title": s["title"]}
        for s in scenarios
        if s["scenario_id"] not in kept_ids
    ]
    scenarios = trimmed

    tasks = build_tasks(scenarios, cfg)
    catalog = scenario_catalog(scenarios, cfg)

    # 项目主题校验：场景必须落在中国语境里。违例就报错退出，不产出任务包。
    if cfg.get("require_china_context"):
        violations = china_context_violations(catalog)
        if violations:
            sys.stderr.write(
                json.dumps(
                    {
                        "error": "场景里有不在中国语境的需求（本项目主题是外国人来华）",
                        "violations": violations,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            return 2

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    contracts.write_jsonl(out / "scenario_catalog.jsonl", catalog)
    contracts.write_jsonl(out / "task_pack.jsonl", tasks)

    provenance = {
        "schema_version": "v1",
        "input_sha256": sha256_file(input_path),
        "config_sha256": sha256_file(config_path),
        "record_count": len(records),
        "scenario_count": len(catalog),
        "scenario_source": {
            "mode": "imported" if scenarios_path else "clustered",
            "path": str(scenarios_path) if scenarios_path else "",
            "sha256": sha256_file(scenarios_path) if scenarios_path else "",
            "scenarios_before_target": before,
            "dropped_by_scenario_target": dropped_by_target,
            "dropped_by_import": imported_dropped,
        },
    }
    (out / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = leakage_report(tasks, catalog)
    (out / "leakage_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if dropped_by_target:
        sys.stderr.write(
            json.dumps(
                {"scenario_target": int(cfg["scenario_target"]), "dropped": dropped_by_target},
                ensure_ascii=False,
            )
            + "\n"
        )
    sys.stderr.write(json.dumps(errors, ensure_ascii=False) + "\n")
    return 0
