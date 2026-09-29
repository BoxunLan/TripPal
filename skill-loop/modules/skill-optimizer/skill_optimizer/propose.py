"""C 线 propose：从一批轨迹里提候选 skill。

规格「C 线」实现顺序：
1. 只读 `attribution == knowledge_gap` 的运行；其余计入排除数，不生成提案。
2. 相同 `failure_pattern` 的 **train** 任务并成一条提案。
3. `candidate_skill/SKILL.md` 由提案渲染；提案为 0 时写 seed 的副本。
4. 输入路径里若出现名为 `holdout` 的目录，立即退出码 2，且不写 `out/`。

第 2、3 步各有一处「看着合规、实测吃成本」的口子，都在下面显式堵住了：
- `pattern_key()`：把模式里引号包着的**具体取值**抹成占位符再合并。同批失败只是「漏了哪个站点」
  时，那些站点属于证据、不属于模式，走原样合并会得到 N 条只差域名的提案。
- `renderable()`：同一 `(target_section, proposed_change)` 只渲染一次。`proposed_change` 目前
  是常量，两条提案就是**同一次编辑**；贴两遍不增加信息，却按固定长度重复吃每次调用的输入。
"""

from __future__ import annotations

import json
import re
import shutil
from collections import OrderedDict
from pathlib import Path

from .config import load_config, DEFAULT_CONFIG

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_SKILL = REPO_ROOT / "fixtures" / "seed_skill_v1" / "SKILL.md"
TARGET_SECTION = "来源与生效日期检查"
MAX_PATTERN = 80

PROPOSED_CHANGE = (
    "在回答政策、材料、费用、时点类问题前，先确认权威来源与生效日期；"
    "二者缺一就不给结论，改写「待核实」并说明去哪里核实。"
)
EXPECTED_EFFECT = "减少没有来源的断言，把「看起来知道」换成「有出处或明说不知道」。"
REGRESSION_RISK = "回答会变长，且在没有官方出处的长尾问题上会更频繁地回「待核实」。"
ACCEPTANCE_TEST = "回答里每一条政策/材料/费用/时点断言，都能追到一个来源 URL 与一个生效日期。"

QUOTED = re.compile(r"['\"「『]([^'\"」』]{1,60})['\"」』]")


def has_holdout_component(path: Path) -> bool:
    return any(part == "holdout" for part in path.parts)


def run_dirs(bundle: Path, skip_holdout: bool = True) -> list[Path]:
    """所有含 verdict.json 的单次运行目录；默认跳过任何名为 holdout 的子树。"""
    out = []
    for vp in sorted(bundle.rglob("verdict.json")):
        if skip_holdout and "holdout" in vp.relative_to(bundle).parts:
            continue
        out.append(vp.parent)
    return out


FAILURE_MARKERS = ("知识缺失", "没有来源", "未核实", "断言未全部命中", "失败")


def failure_pattern(run_dir: Path) -> str:
    """轨迹里第一条 observation 含 `no source` 的 decision；否则退到知识缺失的 decision 摘要。

    兜底取**最后一条**非空 decision（那才是判成败的那一步）。取第一条会抽到「开始执行」
    这种没有任何诊断价值的前言 —— 实测踩过。
    """
    trace = run_dir / "trace.jsonl"
    events = []
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    for ev in events:
        if "no source" in (ev.get("observation") or ""):
            text = (ev.get("decision") or "").strip()
            return (text or "no source")[:MAX_PATTERN]
    for ev in events:
        blob = f"{ev.get('observation') or ''}{ev.get('decision') or ''}"
        if any(m in blob for m in FAILURE_MARKERS):
            return ((ev.get("decision") or "").strip() or "knowledge_gap")[:MAX_PATTERN]
    for ev in reversed(events):
        if (ev.get("decision") or "").strip():
            return ev["decision"].strip()[:MAX_PATTERN]
    return "knowledge_gap"


def pattern_key(text: str) -> str:
    """合并用的归一化 key：抹掉模式里引号包着的具体取值。

    实测同一批失败是 `断言未全部命中：contains 文本不含 'govt.chinadaily.com.cn'` /
    `'english.www.gov.cn'` / `'www.gov.cn'` / `'en.nia.gov.cn'` —— 四条只差「漏了哪个站点」。
    站点是证据，不是模式（模式是「回答没引来源」），所以归一化后四条并成一条，
    `evidence_tasks` 变成 4 个；被抹掉的取值仍留在 `attribution_report.json` 的
    `pattern_variants` 里，诊断信息不丢。
    """
    return QUOTED.sub("<…>", text).strip() or text


def renderable(proposals: list[dict]) -> list[dict]:
    """去掉「同一次编辑说了两遍」的提案：key = (`target_section`, `proposed_change`)。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for p in proposals:
        key = (p["target_section"], p["proposed_change"])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def render_candidate(seed_text: str, proposals: list[dict]) -> str:
    if not proposals:
        return seed_text
    parts = [seed_text.rstrip(), ""]
    for p in renderable(proposals):
        parts.append(f"## {p['target_section']}")
        parts.append("")
        steps = p["proposed_change"].split("；")
        for i, step in enumerate(steps, start=1):
            step = step.strip().rstrip("。")
            if step:
                parts.append(f"{i}. {step}。")
        parts.append("")
    return "\n".join(parts)


def propose(run_bundle, out_dir, config_path=None) -> int:
    bundle = Path(run_bundle)
    if has_holdout_component(bundle):
        return 2
    if not bundle.exists():
        return 2

    cfg = load_config(config_path or DEFAULT_CONFIG)
    max_proposals = int(cfg["max_proposals"])

    included: list[dict] = []
    excluded: dict[str, int] = {}
    skipped_holdout_split = 0

    for rd in run_dirs(bundle):
        verdict = json.loads((rd / "verdict.json").read_text(encoding="utf-8"))
        attr = verdict.get("attribution")
        if attr != "knowledge_gap":
            excluded[attr or "unknown"] = excluded.get(attr or "unknown", 0) + 1
            continue
        task_path = rd / "task.json"
        task = json.loads(task_path.read_text(encoding="utf-8")) if task_path.exists() else {}
        split = task.get("split", "train")
        if split != "train":
            skipped_holdout_split += 1
            excluded["holdout_split"] = excluded.get("holdout_split", 0) + 1
            continue
        included.append(
            {
                "task_id": verdict.get("task_id") or task.get("task_id") or rd.parent.name,
                "split": split,
                "pattern": failure_pattern(rd),
                "run_dir": rd,
            }
        )

    merged: "OrderedDict[str, dict]" = OrderedDict()
    for item in included:
        key = pattern_key(item["pattern"])
        bucket = merged.setdefault(key, {"pattern": key, "task_ids": [], "variants": []})
        if item["task_id"] not in bucket["task_ids"]:
            bucket["task_ids"].append(item["task_id"])
        if item["pattern"] not in bucket["variants"]:
            bucket["variants"].append(item["pattern"])

    proposals = []
    for n, bucket in enumerate(list(merged.values())[:max_proposals], start=1):
        proposals.append(
            {
                "schema_version": "v1",
                "change_id": f"ch-{n:03d}",
                "operation": "add",
                "target_section": TARGET_SECTION,
                "evidence_tasks": bucket["task_ids"],
                "failure_pattern": bucket["pattern"],
                "proposed_change": PROPOSED_CHANGE,
                "expected_effect": EXPECTED_EFFECT,
                "regression_risk": REGRESSION_RISK,
                "acceptance_test": ACCEPTANCE_TEST,
            }
        )
    overflow = max(0, len(merged) - max_proposals)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from . import contracts

    contracts.write_jsonl(out / "change_proposals.jsonl", proposals)

    seed_text = SEED_SKILL.read_text(encoding="utf-8")
    (out / "candidate_skill").mkdir(exist_ok=True)
    candidate_text = render_candidate(seed_text, proposals)
    (out / "candidate_skill" / "SKILL.md").write_text(candidate_text, encoding="utf-8")

    report = {
        "schema_version": "v1",
        "runs_read": len(run_dirs(bundle)),
        "knowledge_gap_train_runs": len(included),
        "excluded_by_attribution": excluded,
        "excluded_holdout_split_runs": skipped_holdout_split,
        "proposal_state": "proposed" if proposals else "no_change",
        "proposals": [p["change_id"] for p in proposals],
        "merged_patterns": len(merged),
        "dropped_over_max_proposals": overflow,
        "pattern_variants": {k: v["variants"] for k, v in merged.items()},
        "rendered_sections": [p["change_id"] for p in renderable(proposals)],
        "candidate_skill_chars": len(candidate_text),
        "seed_skill_chars": len(seed_text),
    }
    (out / "attribution_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


def seed_copy_if_missing(candidate_path: Path) -> None:
    if not candidate_path.exists():
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SEED_SKILL, candidate_path)
