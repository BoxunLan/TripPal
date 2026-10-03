"""Quality gate for a TripPal assessment payload.

Declared in `manifest.yaml` under `quality_tools` and run before the assessment is returned to
the user or handed back through `render_skill_assessment`.

It exists because of one measured failure mode: models produce confident policy claims with no
source and no date. Field research and the evaluation set both showed that an unsourced number
is worse than an explicit "unresolved — check with the authority".

Checks
  1. contract   — the payload matches `references/web-profile-contract.md`
  2. evidence   — policy claims carry a source URL and a check date, or are marked unresolved
  3. red lines  — no visa/entry/admission decision, no guarantee language
  4. sensitive  — no passport/card-like identifiers in the payload
  5. language   — the declared language matches the text

Usage:
    python scripts/verify_assessment.py --assessment assessment.json [--json]
Exit code 1 when any error-level finding is present, 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SEVERITIES = {"critical", "important", "verify"}
TIMINGS = {"now", "this_week", "before_departure", "24h_before", "arrival_day", "travel_day"}
STATUSES = {"needs_attention", "mostly_ready", "ready"}
LANGUAGES = {"en", "zh"}
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# A sentence is treated as a policy claim when it carries a changeable-rule marker.
POLICY_MARKERS = re.compile(
    r"(visa-?free|visa free|transit without|240[- ]?hour|144[- ]?hour|72[- ]?hour|port visa|"
    r"eligible|eligibility|permit|registration|within \d+\s*(hour|day|week)|"
    r"\d+\s*(hour|day|week)s?\b|\b(?:free|fee|limit|deposit|fine|penalt)\w*|"
    r"免签|签证|过境|口岸|停留|登记|超期|罚款|预约|放票|限额|费用|工作日|小时|天)",
    re.I,
)
UNRESOLVED = re.compile(
    r"(unresolved|not (?:settled|confirmed|verified|covered|addressed)|insufficient|"
    r"needs? verification|check with|verif\w+ (?:with|at)|待核实|未确认|以官方为准|需核实|无法确认)",
    re.I,
)
DECISION_LANGUAGE = re.compile(
    r"\b(you (?:are|will be) (?:eligible|admitted|granted)|guaranteed|guarantee\b|"
    r"100% (?:sure|certain)|definitely (?:will|can)|certainly (?:can|will)|"
    r"without a doubt|no problem at all|approved)\b|"
    r"保证(?:能|可以|过)|一定(?:能|可以|过)|肯定(?:能|可以|过|没问题)|绝对(?:能|可以|过|没问题)|"
    r"没(?:问题|事)\b|包(?:能|过)",
    re.I,
)
PASSPORT_LIKE = re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")
CARD_LIKE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
HTTP = re.compile(r"https?://", re.I)
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?$")


class Report:
    def __init__(self) -> None:
        self.findings: list[dict] = []

    def add(self, level: str, code: str, where: str, detail: str) -> None:
        self.findings.append({"level": level, "code": code, "where": where, "detail": detail})

    @property
    def errors(self) -> list[dict]:
        return [f for f in self.findings if f["level"] == "error"]


def check_contract(payload: dict, rep: Report) -> None:
    required = ("submissionId", "source", "generatedAt", "language", "score", "status",
                "summary", "risks", "actions", "officialChecks", "assumptions")
    for key in required:
        if key not in payload:
            rep.add("error", "contract.missing_key", key, "该键是 webpage 契约要求的必填项")
    if payload.get("source") != "trippal-skill":
        rep.add("error", "contract.source", "source", '必须是 "trippal-skill"')
    if payload.get("language") not in LANGUAGES:
        rep.add("error", "contract.language", "language", f"必须是 {sorted(LANGUAGES)} 之一")
    if payload.get("status") not in STATUSES:
        rep.add("error", "contract.status", "status", f"必须是 {sorted(STATUSES)} 之一")
    score = payload.get("score")
    if not isinstance(score, int) or not 0 <= score <= 100:
        rep.add("error", "contract.score", "score", "必须是 0–100 的整数")
    if not ISO.match(str(payload.get("generatedAt", ""))):
        rep.add("error", "contract.generatedAt", "generatedAt", "必须是 ISO-8601 时间戳")

    risks = payload.get("risks") or []
    actions = payload.get("actions") or []
    if len(risks) > 8:
        rep.add("error", "contract.risks_max", "risks", f"最多 8 条，当前 {len(risks)}")
    if len(actions) > 12:
        rep.add("error", "contract.actions_max", "actions", f"最多 12 条，当前 {len(actions)}")
    for i, r in enumerate(risks):
        where = f"risks[{i}]"
        if r.get("severity") not in SEVERITIES:
            rep.add("error", "contract.severity", where, f"severity 必须是 {sorted(SEVERITIES)} 之一")
        if not KEBAB.match(str(r.get("id", ""))):
            rep.add("error", "contract.id", where, "id 必须是 stable kebab-case")
        for key in ("title", "detail", "evidence"):
            if not str(r.get(key, "")).strip():
                rep.add("error", "contract.risk_field", where, f"缺 {key}")
    for i, a in enumerate(actions):
        where = f"actions[{i}]"
        if a.get("timing") not in TIMINGS:
            rep.add("error", "contract.timing", where, f"timing 必须是 {sorted(TIMINGS)} 之一")
        if not KEBAB.match(str(a.get("id", ""))):
            rep.add("error", "contract.id", where, "id 必须是 stable kebab-case")
        for key in ("title", "detail"):
            if not str(a.get(key, "")).strip():
                rep.add("error", "contract.action_field", where, f"缺 {key}")
    for i, c in enumerate(payload.get("officialChecks") or []):
        where = f"officialChecks[{i}]"
        if not HTTP.search(str(c.get("url", ""))):
            rep.add("error", "contract.check_url", where, "url 必须是 HTTP(S)")
        if not DATE.match(str(c.get("checkedAt", ""))):
            rep.add("error", "contract.check_date", where, "checkedAt 必须是 YYYY-MM-DD")
        if not str(c.get("topic", "")).strip() or not str(c.get("result", "")).strip():
            rep.add("error", "contract.check_field", where, "缺 topic 或 result")


def check_evidence(payload: dict, rep: Report) -> None:
    """Policy claims must be sourced at the entry level, or explicitly marked unresolved."""
    check_urls = [str(c.get("url", "")) for c in payload.get("officialChecks") or []]
    buckets = [("summary", payload.get("summary", ""))]
    buckets += [(f"risks[{i}].detail", r.get("detail", "")) for i, r in enumerate(payload.get("risks") or [])]
    buckets += [(f"risks[{i}].evidence", r.get("evidence", "")) for i, r in enumerate(payload.get("risks") or [])]
    buckets += [(f"actions[{i}].detail", a.get("detail", "")) for i, a in enumerate(payload.get("actions") or [])]
    for where, text in buckets:
        text = str(text)
        if not POLICY_MARKERS.search(text):
            continue
        if HTTP.search(text) or UNRESOLVED.search(text) or check_urls:
            continue
        rep.add("error", "evidence.unsourced", where,
                "该处含可变更规则的字样，但既没有内联来源，也没有 officialChecks，也没写「待核实」")
    if not check_urls:
        rep.add("warn", "evidence.no_checks", "officialChecks",
                "没有任何官方核查记录：若本次确实涉及可变更规则，应补上 URL 与日期")


def check_red_lines(payload: dict, rep: Report) -> None:
    blobs = [("summary", payload.get("summary", ""))]
    blobs += [(f"risks[{i}]", json.dumps(r, ensure_ascii=False))
              for i, r in enumerate(payload.get("risks") or [])]
    blobs += [(f"actions[{i}]", json.dumps(a, ensure_ascii=False))
              for i, a in enumerate(payload.get("actions") or [])]
    for where, text in blobs:
        m = DECISION_LANGUAGE.search(str(text))
        if m:
            rep.add("error", "redline.decision", where,
                    f"不得下签证/入境/准入结论或使用保证性措辞：{m.group(0)!r}")


def check_sensitive(payload: dict, rep: Report) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for pattern, code, label in ((PASSPORT_LIKE, "sensitive.passport", "疑似护照号"),
                                 (CARD_LIKE, "sensitive.card", "疑似卡号")):
        for m in list(pattern.finditer(text))[:3]:
            rep.add("error", code, "payload", f"{label}：{m.group(0)!r}")


def check_language(payload: dict, rep: Report) -> None:
    text = " ".join(str(payload.get(k, "")) for k in ("summary",)) + " " + " ".join(
        str(x.get("detail", "")) for x in (payload.get("risks") or []))
    if not text.strip():
        return
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ratio = cjk / max(1, len(text))
    declared = payload.get("language")
    if declared == "zh" and ratio < 0.15:
        rep.add("warn", "language.mismatch", "language", f"声明 zh 但正文 CJK 占比仅 {ratio:.0%}")
    if declared == "en" and ratio > 0.30:
        rep.add("warn", "language.mismatch", "language", f"声明 en 但正文 CJK 占比达 {ratio:.0%}")


def main(argv: list[str]) -> int:
    # Windows 上 stdout 默认是 GBK，中文报告会把调用方（按 UTF-8 读）打挂。
    # 这个坑本项目已经踩过一次（子进程 stderr 被按 cp936 写出），这里主动兜住。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="校验 TripPal 评估载荷与来源纪律")
    ap.add_argument("--assessment", required=True, help="评估 JSON 路径，或 - 从 stdin 读")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出 findings")
    args = ap.parse_args(argv)

    raw = sys.stdin.read() if args.assessment == "-" else Path(args.assessment).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        print(f"assessment 不是合法 JSON：{exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("assessment 顶层必须是对象", file=sys.stderr)
        return 2

    rep = Report()
    check_contract(payload, rep)
    check_evidence(payload, rep)
    check_red_lines(payload, rep)
    check_sensitive(payload, rep)
    check_language(payload, rep)

    if args.json:
        print(json.dumps({"errors": len(rep.errors), "findings": rep.findings},
                         ensure_ascii=False, indent=1))
    else:
        if not rep.findings:
            print("verify_assessment: 通过（契约、来源、红线、敏感数据、语言 5 项无发现）")
        for f in rep.findings:
            mark = "ERROR" if f["level"] == "error" else "warn "
            print(f"{mark} {f['code']:<26} {f['where']:<22} {f['detail']}")
        print(f"\n合计：error {len(rep.errors)}，warn {len(rep.findings) - len(rep.errors)}")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
