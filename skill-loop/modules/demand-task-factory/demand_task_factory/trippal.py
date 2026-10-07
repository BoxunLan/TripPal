"""A 线：把 TripPal 的「入境中国外国游客」场景导入成需求记录 + 检索夹具。

本项目主题就是「外国人来华」（inbound，geo=GLOBAL / en），所以这套夹具就是**主线**；
原先那条中文出境游（geo=CN / zh-CN）的日本线已按主题要求整体移除。
导入产物落一套夹具 `fixtures/trippal_v1/`，不再有并行的第二垂直。

数据来源（全部来自 TripPal 自己的调研产物，本模块**不编造任何查询词或数字**）：

| 来源 | 取什么 |
|---|---|
| `research/pain_points.md` §6 | 12 个手工策展场景的边界与成功判据 |
| `research/se-questions.tsv` | 真实问题串 + `views` / `score`（需求证据） |
| `research/trends-summary.md` | 真实 Google Trends 相对热度（全年均值） |
| `research/raw/clean/*.txt` 的 `# source:` 头 | 权威来源 URL（检索夹具用） |

映射关系（场景 → 关键词 / 页面）写在 `fixtures/trippal_v1/scenario_routing.json`，
是人工映射但不是人工数据：关键词取自 TripPal §6 的场景名/用户目标与 §4 的痛点标题，
页面 slug 取自 `raw/clean/` 的真实文件名。该文件参与 provenance 哈希。

规格口径说明：`metric_value` 一律取来源里的真实数字（SE 的 `views`、Trends 的全年均值），
不做归一、不与其它来源相加。
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from . import contracts

SCENARIOS_SCHEMA = "trippal-scenarios-v1"
ROUTING_SCHEMA = "trippal-routing-v1"

SECTION_HEAD = re.compile(r"^##\s*6\.\s*场景切分建议")
NEXT_SECTION = re.compile(r"^##\s")
ROW_ID = re.compile(r"^S\d{2}$")
TRIAL_ORDER = re.compile(r"`([^`]*S\d{2}[^`]*)`")

# 每一列的语义，与 §6 表头一致。
COLUMNS = ["tp_id", "name", "trigger_timing", "user_goal", "key_inputs", "success_criteria", "pain_point_ids"]

# 页面正文里的日期：只认这几种明确写法，认不出就留空（留空 = 页面未标注生效日期，
# 这正是 skill 要求「无生效日期则待核实」的触发条件，不能拿抓取时间冒名顶替）。
MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
)
DATE_PATTERNS = [
    re.compile(r"(20\d{2})-(\d{2})-(\d{2})"),
    re.compile(r"(20\d{2})/(\d{2})/(\d{2})"),
    re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", re.I),
]
MONTH_NUM = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    p = Path(path)
    return sha256_text(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else ""


# ------------------------------------------------------------------ 来源解析
def parse_scenarios(md_text: str) -> tuple[list[dict], list[str]]:
    """从 pain_points.md 抽出 §6 的场景表与「首批试跑顺序」。"""
    lines = md_text.splitlines()
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if start is None and SECTION_HEAD.match(line):
            start = i
            continue
        if start is not None and NEXT_SECTION.match(line):
            end = i
            break
    if start is None:
        return [], []

    rows: list[dict] = []
    for line in lines[start:end]:
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < len(COLUMNS) or not ROW_ID.match(cells[0]):
            continue
        row = dict(zip(COLUMNS, cells[: len(COLUMNS)]))
        row["pain_point_ids"] = [p.strip() for p in row["pain_point_ids"].split(",") if p.strip()]
        rows.append(row)

    order: list[str] = []
    for line in lines[start:end]:
        m = TRIAL_ORDER.search(line)
        if m and "→" in m.group(1):
            order = re.findall(r"S\d{2}", m.group(1))
            break
    return rows, order


def parse_se_questions(tsv_text: str) -> list[dict]:
    out: list[dict] = []
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    for d in reader:
        title = html.unescape((d.get("title") or "").strip())
        if not title:
            continue
        out.append(
            {
                "id": (d.get("id") or "").strip(),
                "site": (d.get("site") or "").strip(),
                "title": title,
                "views": _int(d.get("views")),
                "score": _int(d.get("score")),
                "answers": _int(d.get("answers")),
                "link": (d.get("link") or "").strip(),
            }
        )
    return out


def _int(raw) -> int:
    try:
        return int(str(raw).strip() or 0)
    except ValueError:
        return 0


def parse_trends(md_text: str) -> dict[str, float]:
    """抽「一、有完整时间线的词条」表里的词条 → 全年均值。只有这一节是可用数值。"""
    out: dict[str, float] = {}
    in_table = False
    for line in md_text.splitlines():
        if line.startswith("## 一、"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("词条", "") or set(cells[0]) <= set("-: "):
            continue
        try:
            out[cells[0].lower()] = float(cells[1])
        except ValueError:
            continue
    return out


def read_page(trippal_root: Path, slug: str, limit: int = 700) -> dict:
    """读一页语料：URL 来自 `# source:` 头，正文取真正的第一段，日期只认明确写法。

    同时保留页面自己的 `# title:`：它是**选代表问题**的锚点 —— 代表问题会和这条
    标题一起进提示词，两者必须讲同一件事，否则这张卡就是「问 A 给 B」。
    """
    for sub in ("clean", "web"):
        p = trippal_root / "research" / "raw" / sub / slug
        if p.exists():
            page = _page_from_text(p.read_text(encoding="utf-8", errors="replace"), limit)
            page["slug"] = slug
            return page
    return {"text": "", "source_url": "", "effective_date": "", "title": "", "slug": slug}


def _page_from_text(raw: str, limit: int) -> dict:
    url = ""
    title = ""
    body: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("# source:"):
            url = s.split(":", 1)[1].strip()
            continue
        if s.startswith("# title:"):
            title = s.split(":", 1)[1].strip()
            continue
        if s.startswith("#"):
            continue
        if s:
            body.append(s)
    text = " ".join(body)
    text = re.sub(r"\s+", " ", text).strip()[:limit]
    return {
        "text": text,
        "source_url": url,
        "effective_date": find_date(text),
        "title": title,
    }


def find_date(text: str) -> str:
    for pat in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if m.group(1).isdigit():  # 两种 ISO 写法
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        mon = MONTH_NUM.get(m.group(1).lower())
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    return ""


# ------------------------------------------------------------------ 路由
def load_routing(path: str | Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema_version") != ROUTING_SCHEMA:
        raise ValueError(f"routing schema_version 必须是 {ROUTING_SCHEMA}")
    return doc


def is_china_question(title: str, weak_terms: list[str], context: list[str], phrases: list[str]) -> bool:
    """判断一条问题是否真的落在中国语境里。

    只用 `china` / `chinese` 当闸门太松：实测放进来「在欧洲招待中国客人」「在泰国把
    Alipay 余额换成现金」这类提问 —— 词面上有 China，语境完全不在中国。所以要求
    同时满足：① 命中弱词表；② 命中一个**中国境内的地点/服务**词，或一个明确的中国语境短语。
    弱词表与语境表都写在 routing 里，可逐条复核。
    """
    low = title.lower()
    if not any(t.lower() in low for t in weak_terms):
        return False
    if not context and not phrases:
        return True  # routing 没给语境表时退回旧行为（夹具测试用最小 routing）
    if any(p.lower() in low for p in phrases):
        return True
    return any(t.lower() in low for t in context)


WORD_RE = re.compile(r"[a-z0-9']+")
STOPWORDS = {
    "the", "how", "can", "you", "what", "for", "with", "and", "in", "to", "of", "on",
    "my", "is", "it", "do", "does", "not", "when", "where", "why", "which", "are", "be",
    "at", "from", "that", "this", "if", "or", "as", "no", "one", "her", "his", "they",
}


def content_tokens(text: str) -> set[str]:
    return {t for t in WORD_RE.findall(text.lower()) if len(t) >= 3 and t not in STOPWORDS}


def keyword_hits(title: str, keywords: list[str]) -> int:
    """一条标题命中几个关键词。返回命中数（而不是 bool），代表问题的挑选要用它排序。

    单字/短词（≤4 字符）必须**整词命中**，长词按词首前缀命中：
    否则 `book` 会命中 `notebooks`、`bus` 会命中 `business` —— 实测这两种都发生过，
    结果是把「海关查不查 Kindle」这种问题分给了景区购票场景。
    """
    low = title.lower()
    tokens = WORD_RE.findall(low)
    hits = 0
    for k in keywords:
        k = k.lower().strip()
        if not k:
            continue
        if " " in k or "-" in k:
            # 多词/带连字符的写法按整串匹配，避免再拆词造成误伤
            if k in low:
                hits += 1
        elif len(k) <= 4:
            if k in tokens:
                hits += 1
        elif any(t.startswith(k) for t in tokens):
            hits += 1
    return hits


def pair_score(question: dict, page: dict, keywords: list[str]) -> tuple:
    """问题的排序键：先看它与该场景权威页面讲的是不是同一件事，再看对题程度与热度。

    只看浏览量会挑出「在欧洲招待中国客人」这种既真实又跑题的问题，
    而它的 `contains` 还是要进 verifier 的 —— 卡必须自洽。
    """
    page_tokens = content_tokens(page.get("title", ""))
    page_tokens |= content_tokens(str(page.get("slug", "")).replace("-", " ").replace(".txt", ""))
    overlap = len(content_tokens(question["title"]) & page_tokens)
    return (-overlap, -keyword_hits(question["title"], keywords), -question["views"], -question["score"], question["id"])


def route_questions(
    questions: list[dict], spec: dict, gate: tuple, pages: list[dict], top_k: int
) -> tuple[list[dict], dict | None]:
    """给一个场景挑「问题 × 页面」：返回（按相关度排序的问题, 与该场景最配的那一页）。

    确定性判定，不调模型。代表问题取排第一的那条 —— 它同时是 verifier 断言的那条
    `contains`，所以必须挑最对题的，不能只挑最热的。

    一条问题可能命中多个场景（TripPal 的痛点本身也跨场景），这里允许重复归属 ——
    与语料采集时的做法一致（README 明确说同一问答会出现在多个切片里）。
    """
    keywords = list(spec.get("keywords", []))
    excludes = list(spec.get("exclude", []))
    if not keywords or not pages:
        return [], None
    hits = []
    for q in questions:
        if not is_china_question(q["title"], *gate):
            continue
        # 同场景内的消歧：`reserve` 同时命中「预订机票办签证」与「预约景区门票」，
        # 只加 exclude 才能把抢票场景的候选锁在「景区/博物馆」这条线上。
        if excludes and keyword_hits(q["title"], excludes):
            continue
        if keyword_hits(q["title"], keywords):
            hits.append(q)

    best: tuple | None = None
    chosen_page: dict | None = None
    best_ranked: list[dict] = []
    for page in pages:
        ranked = sorted(hits, key=lambda q: pair_score(q, page, keywords))
        if not ranked:
            continue
        key = pair_score(ranked[0], page, keywords)
        if best is None or key < best:
            best, chosen_page, best_ranked = key, page, ranked
    if best is None:
        return [], None
    return best_ranked[:top_k], chosen_page


# ------------------------------------------------------------------ 主流程
def import_trippal(
    trippal_root: str | Path,
    routing_path: str | Path,
    out_dir: str | Path,
    records_path: str | Path,
    tool_data_path: str | Path,
    time_window: str = "2025-09-29/2026-09-29",
    retrieved_at: str = "2026-09-29T00:00:00+08:00",
    top_k: int = 6,
    scenario_target: int = 8,
) -> int:
    root = Path(trippal_root)
    research = root / "research"
    pain = research / "pain_points.md"
    seq = research / "se-questions.tsv"
    trends_md = research / "trends-summary.md"

    missing = [str(p) for p in (pain, seq, trends_md) if not p.exists()]
    if missing:
        sys.stderr.write(
            json.dumps({"error": "TripPal 来源缺失", "missing": missing}, ensure_ascii=False) + "\n"
        )
        return 2

    pain_text = pain.read_text(encoding="utf-8", errors="replace")
    scenarios, trial_order = parse_scenarios(pain_text)
    if not scenarios:
        sys.stderr.write(
            json.dumps(
                {"error": "在 pain_points.md 里没抽到 §6 场景表", "file": str(pain)}, ensure_ascii=False
            )
            + "\n"
        )
        return 2

    questions = parse_se_questions(seq.read_text(encoding="utf-8", errors="replace"))
    trends = parse_trends(trends_md.read_text(encoding="utf-8", errors="replace"))
    routing = load_routing(routing_path)
    gate = (
        routing.get("china_terms", []),
        routing.get("china_context", []),
        routing.get("china_context_phrases", []),
    )
    spec_by_id = routing.get("scenarios", {})

    records: list[dict] = []
    pages: dict[str, dict] = {}
    catalog: list[dict] = []
    skipped: list[dict] = []

    for sc in scenarios:
        sid = sc["tp_id"]
        spec = spec_by_id.get(sid, {})
        rec_ids: list[str] = []
        queries: list[str] = []

        loaded = [read_page(root, s) for s in spec.get("pages", [])]
        loaded = [p for p in loaded if p["text"]]
        if not loaded:
            skipped.append({"tp_id": sid, "name": sc["name"], "reason": "no_page_available"})
            continue

        hits, page = route_questions(questions, spec, gate, loaded, top_k)
        if page is None:
            page = loaded[0]

        for n, q in enumerate(hits, start=1):
            rid = f"tp-{sid.lower()}-q{n}"
            records.append(
                {
                    "schema_version": "v1",
                    "record_id": rid,
                    "source": "other",
                    "query_or_topic": q["title"],
                    "geo": "GLOBAL",
                    "language": "en",
                    "time_window": time_window,
                    "metric_type": "impressions",
                    "metric_value": q["views"],
                    "access_scope": "public_aggregate",
                    "retrieved_at": retrieved_at,
                }
            )
            rec_ids.append(rid)
            queries.append(q["title"])

        # Trends 证据：只在该场景映射到的词条**有真实全年均值**时生成，没有就跳过。
        for n, term in enumerate(spec.get("trend_terms", []), start=1):
            value = trends.get(term.lower())
            if value is None:
                continue
            rid = f"tp-{sid.lower()}-t{n}"
            records.append(
                {
                    "schema_version": "v1",
                    "record_id": rid,
                    "source": "google_trends",
                    "query_or_topic": term,
                    "geo": "US",
                    "language": "en",
                    "time_window": time_window,
                    "metric_type": "relative_interest",
                    "metric_value": value,
                    "access_scope": "public_aggregate",
                    "retrieved_at": retrieved_at,
                }
            )
            rec_ids.append(rid)
            queries.append(term)

        if not rec_ids:
            skipped.append({"tp_id": sid, "name": sc["name"], "reason": "no_evidence_route"})
            continue

        representative = queries[0]
        pages[representative] = {
            "text": page["text"],
            "source_url": page["source_url"],
            "effective_date": page["effective_date"],
        }

        catalog.append(
            {
                **sc,
                "query_cluster": queries,
                "record_ids": rec_ids,
                "representative": representative,
                "page": {"slug": page["slug"], "title": page["title"], "url": page["source_url"]},
            }
        )

    if not catalog:
        sys.stderr.write(
            json.dumps({"error": "没有任何场景拿到可核对的证据与页面"}, ensure_ascii=False) + "\n"
        )
        return 2

    dropped = []
    if len(catalog) > scenario_target:
        dropped = [{"tp_id": c["tp_id"], "name": c["name"]} for c in catalog[scenario_target:]]
        catalog = catalog[:scenario_target]

    scenarios_doc = {
        "schema_version": SCENARIOS_SCHEMA,
        "source": {
            "corpus": "TripPal-main/research",
            "pain_points_sha256": sha256_file(pain),
            "se_questions_sha256": sha256_file(seq),
            "trends_summary_sha256": sha256_file(trends_md),
            "routing_sha256": sha256_file(routing_path),
        },
        "vertical": "inbound_foreign_tourists_to_china",
        "extracted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "trial_order": trial_order,
        "scenarios": catalog,
    }

    report = {
        "schema_version": "trippal-import-report-v1",
        "questions_total": len(questions),
        "questions_china_relevant": len(
            [q for q in questions if is_china_question(q["title"], *gate)]
        ),
        "trend_terms_with_values": sorted(trends.keys()),
        "scenarios_in_source": len(scenarios),
        "scenarios_kept": len(catalog),
        "dropped_by_scenario_target": dropped,
        "skipped": skipped,
        "records": len(records),
        "records_by_source": {
            "se_questions": len([r for r in records if r["source"] == "other"]),
            "google_trends": len([r for r in records if r["source"] == "google_trends"]),
        },
        "notes": [
            "query_or_topic 全部取自 se-questions.tsv 的真实标题或 trends-summary.md 的词条，无人工编写。",
            "metric_value 取真实 views（source=other）或 Trends 全年均值（source=google_trends），未归一、未相加。",
            "SE 语料以英语技术型用户为主（TripPal README §3 已声明该偏倚），故 geo 记 GLOBAL 而非 US。",
            "Trends 记录的 geo=US 是照抄 trends-summary.md 首行自报的采集口径（geo=US, today 12-m），未改动。",
            "被 scenario_target 截掉的场景不是「无证据」，是规格的规模上限 5–8 决定的。",
        ],
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "scenarios.json").write_text(
        json.dumps(scenarios_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "import_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    contracts.write_jsonl(records_path, records)
    Path(tool_data_path).parent.mkdir(parents=True, exist_ok=True)
    Path(tool_data_path).write_text(
        json.dumps(pages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    sys.stderr.write(json.dumps(report, ensure_ascii=False) + "\n")
    return 0
