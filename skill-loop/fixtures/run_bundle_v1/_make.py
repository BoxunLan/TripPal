"""生成 fixtures/run_bundle_v1/ 的夹具运行。

规格要求「至少 4 次运行，目录名即类别」，每次运行含六件套
（task.json / manifest.json / log.md / trace.jsonl / verdict.json / artifacts/），
另有一份 holdout/。这里用脚本生成，保证六件套字段一致、可复现。

运行（在仓库根）：.venv/Scripts/python.exe fixtures/run_bundle_v1/_make.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
TASK_PACK = REPO / "fixtures" / "task_pack_trippal_v1" / "task_pack.jsonl"
TOOL_DATA = REPO / "fixtures" / "tool_data_trippal_v1" / "pages.json"

TASKS = {t["task_id"]: t for t in (json.loads(l) for l in TASK_PACK.read_text(encoding="utf-8").splitlines() if l.strip())}


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest() -> dict:
    return {
        "schema_version": "v1",
        "experiment_id": "fixture-b1",
        "condition": "B-no-skill",
        "model": "fake-model-v1",
        "temperature": 0,
        "timeout_seconds": 60,
        "max_tokens": 2000,
        "tool_snapshot": {"name": "lookup_local", "sha256": sha256_file(TOOL_DATA)},
        "skill_snapshot": {"loaded": False},
        "task_pack_sha256": sha256_file(TASK_PACK),
    }


def trace(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def events(kind: str, task_id: str) -> list[dict]:
    """按类别造轨迹。第一条 observation 决定 C 线能抽到的 failure_pattern。"""
    base = [
        {
            "step": 1,
            "timestamp": "2026-09-29T10:00:00+08:00",
            "action": "read_task",
            "observation": f"task={task_id}",
            "decision": "开始执行",
            "skipped_or_abandoned": "",
            "cost": {"input_tokens": 210, "output_tokens": 12, "tool_calls": 0},
            "artifact": "",
        },
        {
            "step": 2,
            "timestamp": "2026-09-29T10:00:04+08:00",
            "action": "lookup_local(query)",
            "observation": "empty",
            "decision": "本机数据里没有这条，改走模型自身知识",
            "skipped_or_abandoned": "",
            "cost": {"input_tokens": 60, "output_tokens": 8, "tool_calls": 1},
            "artifact": "artifacts/lookup.json",
        },
    ]
    if kind == "success":
        return base[:1] + [
            {
                "step": 2,
                "timestamp": "2026-09-29T10:00:04+08:00",
                "action": "lookup_local(query)",
                "observation": "hit: fixtures/tool_data_trippal_v1/pages.json",
                "decision": "拿到来源与生效日期，可以作答",
                "skipped_or_abandoned": "",
                "cost": {"input_tokens": 60, "output_tokens": 8, "tool_calls": 1},
                "artifact": "artifacts/lookup.json",
            },
            {
                "step": 3,
                "timestamp": "2026-09-29T10:00:06+08:00",
                "action": "answer",
                "observation": '{"decision":"unknown","sources":["https://govt.chinadaily.com.cn/s/202502/25/WS67bd6c5a498eec7e1f730389/"]}',
                "decision": "输出终态",
                "skipped_or_abandoned": "",
                "cost": {"input_tokens": 320, "output_tokens": 42, "tool_calls": 0},
                "artifact": "artifacts/answer.json",
            },
        ]
    if kind == "knowledge_gap":
        return base + [
            {
                "step": 3,
                "timestamp": "2026-09-29T10:00:07+08:00",
                "action": "answer",
                "observation": "no source；直接给出了具体材料清单，没有任何出处",
                "decision": "把没有来源的推断当成结论输出",
                "skipped_or_abandoned": "跳过了「先确认权威来源」这一步",
                "cost": {"input_tokens": 300, "output_tokens": 88, "tool_calls": 0},
                "artifact": "artifacts/answer.json",
            },
        ]
    if kind == "missing_tool":
        return [
            base[0],
            {
                "step": 2,
                "timestamp": "2026-09-29T10:00:03+08:00",
                "action": "lookup_local(query)",
                "observation": "timeout after 60s",
                "decision": "工具不可达，停止",
                "skipped_or_abandoned": "放弃了整条查询链",
                "cost": {"input_tokens": 60, "output_tokens": 0, "tool_calls": 1},
                "artifact": "",
            },
        ]
    return [
        base[0],
        {
            "step": 2,
            "timestamp": "2026-09-29T10:00:05+08:00",
            "action": "answer",
            "observation": "题面里的 decision 取值约定与 verifier 期望不一致",
            "decision": "按题面作答，结果被判为 fail",
            "skipped_or_abandoned": "",
            "cost": {"input_tokens": 240, "output_tokens": 30, "tool_calls": 0},
            "artifact": "artifacts/answer.json",
        },
    ]


def log_md(rows: list[dict]) -> str:
    head = "| step | timestamp | action | observation | decision | skipped_or_abandoned | cost | artifact |\n"
    sep = "|---|---|---|---|---|---|---|---|\n"
    body = ""
    for r in rows:
        c = r["cost"]
        cost = f"in={c['input_tokens']} out={c['output_tokens']} tools={c['tool_calls']}"
        obs = r["observation"].replace("|", "\\|")
        body += (
            f"| {r['step']} | {r['timestamp']} | {r['action']} | {obs} | {r['decision']} "
            f"| {r['skipped_or_abandoned']} | {cost} | {r['artifact']} |\n"
        )
    return head + sep + body


def verdict(kind: str, task_id: str, run_n: int) -> dict:
    table = {
        "success": ("pass", True, "none"),
        "knowledge_gap": ("fail", False, "knowledge_gap"),
        "missing_tool": ("timeout", False, "missing_tool_or_data"),
        "task_defect": ("fail", False, "task_defect"),
    }
    status, overall_pass, attribution = table[kind]
    scores = {
        "success": {"outcome": 1.0, "process": 0.8, "evidence": 1.0, "efficiency": 0.7},
        "knowledge_gap": {"outcome": 0.0, "process": 0.5, "evidence": 0.0, "efficiency": 0.6},
        "missing_tool": {"outcome": 0.0, "process": 0.3, "evidence": 0.0, "efficiency": 0.2},
        "task_defect": {"outcome": 0.0, "process": 0.6, "evidence": 0.4, "efficiency": 0.5},
    }[kind]
    return {
        "schema_version": "v1",
        "task_id": task_id,
        "run_n": run_n,
        "live": False,
        "status": status,
        "overall_pass": overall_pass,
        "attribution": attribution,
        "contaminated": False,
        "scores": scores,
    }


RUNS = [
    ("success", 1, "fx-cn-visa-cond-002", 1),
    ("knowledge_gap", 1, "fx-cn-visa-fact-001", 1),
    ("knowledge_gap", 2, "fx-cn-visa-fact-001", 2),
    ("missing_tool", 1, "fx-cn-visa-cond-002", 1),
    ("task_defect", 1, "fx-cn-visa-cond-002", 1),
]


def write_run(parent: Path, name: str, kind: str, task_id: str, run_n: int) -> None:
    d = parent / name
    (d / "artifacts").mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(
        json.dumps(TASKS[task_id], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (d / "manifest.json").write_text(
        json.dumps(manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rows = events(kind, task_id)
    for r in rows:
        r.setdefault("schema_version", "v1")
    (d / "log.md").write_text(log_md(rows), encoding="utf-8")
    (d / "trace.jsonl").write_text(trace(rows), encoding="utf-8")
    (d / "verdict.json").write_text(
        json.dumps(verdict(kind, task_id, run_n), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (d / "artifacts" / "answer.json").write_text(
        json.dumps({"decision": "unknown", "sources": []}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (d / "artifacts" / "lookup.json").write_text(
        json.dumps({"text": "", "source_url": "", "effective_date": ""}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    for kind, idx, task_id, run_n in RUNS:
        write_run(OUT / kind, f"run{idx}", kind, task_id, run_n)
    write_run(OUT / "holdout", "run1", "knowledge_gap", "fx-cn-pay-fact-003", 1)
    print(f"wrote {len(RUNS)} runs + 1 holdout run into {OUT}")


if __name__ == "__main__":
    main()
