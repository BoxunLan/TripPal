"""B 线：把任务包跑成 evals/runs/ 下的单次目录，并做污染检查。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import contracts, verifier
from .config import DEFAULT_CONFIG, load_config
from .model import FakeModel, ModelParseError, ModelServerError, ModelTimeout, build_model
from .tool import DEFAULT_PAGES, lookup_local

CONDITIONS = ("B-no-skill", "C-seed-skill")
MAX_ATTEMPTS = 2  # E6：每题最多 2 次尝试
REPO_ROOT = Path(__file__).resolve().parents[3]


def sha256_file(path) -> str:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else hashlib.sha256(b"").hexdigest()


def resolve_tool_data(task) -> tuple[Path, str]:
    """任务卡 `initial_state.tool_data` 声明的检索夹具 → (绝对路径, 仓库内相对路径)。

    声明了就用声明的那份；找不到就返回那个不存在的路径（`lookup_local` 会返回空命中），
    **不静默回退**到默认夹具体 —— 否则「没命中」与「读错文件」看起来一模一样。
    没声明时回退到默认夹具体，保持对早期任务卡的兼容。
    """
    declared = str(((task.get("initial_state") or {}).get("tool_data")) or "").strip()
    if not declared:
        return DEFAULT_PAGES, DEFAULT_PAGES.relative_to(REPO_ROOT).as_posix()
    p = Path(declared)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p, declared


def tool_snapshot_for(tasks: list[dict]) -> dict:
    """manifest 的 tool_snapshot：哈希任务包**实际用到的**页面夹具，而不是固定的那一份。"""
    files: list[dict] = []
    for t in tasks:
        p, rel = resolve_tool_data(t)
        if all(f["path"] != rel for f in files):
            files.append({"path": rel, "sha256": sha256_file(p)})
    if len(files) == 1:
        return {"name": "lookup_local", **files[0]}
    joined = "\n".join(f"{f['path']}:{f['sha256']}" for f in files)
    return {
        "name": "lookup_local",
        "sha256": hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        "files": files,
    }


def extract_json(text: str):
    """从模型输出里抽第一个 JSON 对象；抽不到返回 None。"""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _row(step, ts, action, observation, decision, skipped, cost, artifact=""):
    return {
        "schema_version": "v1",
        "step": step,
        "timestamp": ts,
        "action": action,
        "observation": observation,
        "decision": decision,
        "skipped_or_abandoned": skipped,
        "cost": cost,
        "artifact": artifact,
    }


def cost(input_tokens=0, output_tokens=0, tool_calls=0):
    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "tool_calls": int(tool_calls),
    }


def retrieval_block(query: str, tool_result: dict | None) -> str:
    """把 lookup_local 的命中结果拼成提示词里的一段。

    这是修一个真缺陷：`lookup_local` 的结果原先只写进 artifacts/lookup.json 与日志，
    **从没进过提示词** —— 于是 `allowed_tools: ["lookup_local"]` 是装饰性的，
    模型手上根本没有来源，skill 要求「给出来源与生效日期」时它只能写「待核实」。
    两个 condition 都注入同一段，保证对照公平。
    """
    if not tool_result or not tool_result.get("text"):
        return ""
    lines = ["", "[检索结果 · lookup_local]", f"查询：{query}", f"正文：{tool_result['text']}"]
    lines.append(f"来源：{tool_result.get('source_url') or '（未提供）'}")
    lines.append(f"生效日期：{tool_result.get('effective_date') or '（页面未标注）'}")
    return "\n".join(lines)


def log_md(rows) -> str:
    head = "| step | timestamp | action | observation | decision | skipped_or_abandoned | cost | artifact |\n"
    sep = "|---|---|---|---|---|---|---|---|\n"
    body = ""
    for r in rows:
        c = r["cost"]
        cost_s = f"in={c['input_tokens']} out={c['output_tokens']} tools={c['tool_calls']}"
        obs = r["observation"].replace("|", "\\|").replace("\n", " ")
        body += (
            f"| {r['step']} | {r['timestamp']} | {r['action']} | {obs} | {r['decision']} "
            f"| {r['skipped_or_abandoned']} | {cost_s} | {r['artifact']} |\n"
        )
    return head + sep + body


def execute_one(task, run_n, run_dir, model, cfg, skill_text, manifest_base):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts").mkdir(exist_ok=True)
    (run_dir / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    manifest = dict(manifest_base)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    rows = []
    ts = "2026-09-29T10:00:00+08:00"
    rows.append(_row(1, ts, "read_task", f"task={task['task_id']}", "开始执行", "", cost(120, 8)))

    query = (task.get("demand_evidence") or {}).get("query_cluster") or [""]
    tool_result = None
    if "lookup_local" in task.get("allowed_tools", []):
        # 任务卡声明的 `initial_state.tool_data` 必须真的被用上。
        # 原先这里硬编码 DEFAULT_PAGES，于是任务卡写哪个夹具体都没用 ——
        # 和修 `allowed_tools` 纯装饰是同一类缺陷（声明了但没生效）。
        pages_path, pages_label = resolve_tool_data(task)
        tool_result = lookup_local(query[0], pages_path)
        obs = f"hit: {pages_label}" if tool_result["text"] else f"empty ({pages_label})"
        if tool_result["text"] and not tool_result["effective_date"]:
            obs += "；来源缺生效日期"
        rows.append(
            _row(
                2,
                ts,
                f"lookup_local({query[0]})",
                obs,
                "按工具返回值决定下一步" if tool_result["text"] else "没有命中，改走模型自身知识",
                "" if tool_result["text"] else "跳过了「先确认权威来源」这一步",
                cost(60, 10, 1),
                "artifacts/lookup.json",
            )
        )
        (run_dir / "artifacts" / "lookup.json").write_text(
            json.dumps(tool_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # skill 只经 `model.complete(prompt, skill_text)` 走 system 通道注入一次。
    # 这里**不要**再拼进用户消息：原先两处都发，同一份 skill 每轮多付一遍输入 token ——
    # 实测把 conditional_decision 卡的输入从 104 顶到 865（×8.3），cost_ratio 被推到 2.32，
    # 让回归门把一份「质量确实变好」的 skill 判成 rollback。技能文本是常量成本，重复发纯浪费。
    prompt = task["prompt"] + retrieval_block(query[0], tool_result)

    text, usage, status, attribution = "", cost(), None, None
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            text, usage = model.complete(prompt, skill_text)
            status, attribution, last_error = None, None, None
            break
        except ModelTimeout as exc:
            status, attribution, last_error = "timeout", "missing_tool_or_data", str(exc)
        except ModelServerError as exc:
            status, attribution, last_error = "error", "execution_defect", str(exc)
        except ModelParseError as exc:
            status, attribution, last_error = "error", "execution_defect", str(exc)
        if attempt < MAX_ATTEMPTS:
            rows.append(
                _row(
                    3,
                    ts,
                    "retry",
                    f"第 {attempt} 次尝试失败：{last_error}",
                    "按 E6 原样再试 1 次",
                    "",
                    cost(),
                )
            )

    if status is None:
        parsed = extract_json(text)
        if verifier.needs_json(task) and parsed is None:
            status, attribution = "error", "execution_defect"
            last_error = "响应里抽不出 JSON，不重试解析"
            rows.append(
                _row(3, ts, "answer", text[:200], "响应解析失败，按 execution_defect 落盘", "", cost(usage["input_tokens"], usage["output_tokens"]))
            )
        else:
            results = verifier.evaluate(task["verifier"]["assertions"], text, parsed)
            passed = verifier.overall_pass(results)
            status = "pass" if passed else "fail"
            first_bad = next((r for r in results if not r["passed"]), None)
            fail_reason = (
                "断言全部命中"
                if passed
                else f"断言未全部命中：{first_bad['op']} {first_bad['detail']}"[:120]
            )
            # 归因口径按设计稿 §5 的判定表：先问「数据里有吗」，再问「模型知道该做哪一步吗」。
            # 工具命中了数据却仍然失败 → 知识缺（唯一该写 skill 的一支）；
            # 数据本身就没有 → missing_tool_or_data（写 skill 无用）。
            data_available = bool((tool_result or {}).get("text"))
            attribution = "none" if passed else (
                "knowledge_gap" if data_available else "missing_tool_or_data"
            )
            rows.append(
                _row(
                    3,
                    ts,
                    "answer",
                    text[:200],
                    fail_reason,
                    "",
                    cost(usage["input_tokens"], usage["output_tokens"]),
                    "artifacts/answer.json",
                )
            )
            (run_dir / "artifacts" / "answer.json").write_text(
                json.dumps({"text": text, "parsed": parsed, "assertions": results}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
    else:
        rows.append(_row(3, ts, "answer", str(last_error), "按 E6 落盘", "", cost()))

    if status in ("timeout", "error"):
        (run_dir / "artifacts" / "error.txt").write_text(str(last_error) + "\n", encoding="utf-8")

    (run_dir / "trace.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    (run_dir / "log.md").write_text(log_md(rows), encoding="utf-8")

    totals = cost(
        sum(r["cost"]["input_tokens"] for r in rows),
        sum(r["cost"]["output_tokens"] for r in rows),
        sum(r["cost"]["tool_calls"] for r in rows),
    )
    scores = {
        "outcome": 1.0 if status == "pass" else 0.0,
        "process": 0.8 if status == "pass" else (0.4 if status == "fail" else 0.2),
        "evidence": 1.0 if (tool_result or {}).get("text") else 0.0,
        "efficiency": max(0.0, min(1.0, 1.0 - max(0, totals["input_tokens"] + totals["output_tokens"]) / 4000)),
    }
    verdict = {
        "schema_version": "v1",
        "task_id": task["task_id"],
        "run_n": run_n,
        "live": bool(getattr(model, "live", False)),
        "status": status,
        "overall_pass": status == "pass",
        "attribution": "none" if status == "pass" else (attribution or "execution_defect"),
        "contaminated": False,
        "scores": scores,
    }
    (run_dir / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return verdict


def mark_contamination(experiment_dir: Path) -> list[str]:
    """同 experiment_id 下两个 condition 的 manifest 关键字段不一致 → 双方 contaminated。"""
    manifest_by_condition: dict[str, dict] = {}
    dirs_by_condition: dict[str, list[Path]] = {}
    for cond_dir in sorted(p for p in experiment_dir.iterdir() if p.is_dir()):
        if cond_dir.name == "holdout":
            continue
        runs = sorted(cond_dir.glob("*/*/manifest.json"))
        if not runs:
            continue
        manifest_by_condition[cond_dir.name] = json.loads(runs[0].read_text(encoding="utf-8"))
        dirs_by_condition[cond_dir.name] = [p.parent for p in sorted(cond_dir.glob("*/*/verdict.json"))]

    fields = ("model", "temperature", "timeout_seconds", "tool_snapshot", "task_pack_sha256")
    dirty: list[str] = []
    names = sorted(manifest_by_condition)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = manifest_by_condition[names[i]], manifest_by_condition[names[j]]
            if any(a.get(f) != b.get(f) for f in fields):
                dirty.extend([names[i], names[j]])

    for name in sorted(set(dirty)):
        for run_dir in dirs_by_condition.get(name, []):
            vp = run_dir / "verdict.json"
            if not vp.exists():
                continue
            v = json.loads(vp.read_text(encoding="utf-8"))
            v["contaminated"] = True
            vp.write_text(json.dumps(v, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sorted(set(dirty))


def run(task_pack, condition, runs, experiment_id, out_root, skill=None, config_path=None) -> int:
    if condition not in CONDITIONS:
        return 2
    if condition == "C-seed-skill" and not skill:
        return 2
    task_pack = Path(task_pack)
    if not task_pack.exists():
        return 2

    cfg = load_config(config_path or DEFAULT_CONFIG)
    tasks = contracts.load_jsonl(task_pack)
    model = build_model(cfg)
    skill_text = Path(skill).read_text(encoding="utf-8") if skill else None

    skill_snapshot = (
        {"loaded": True, "path": str(skill).replace("\\", "/"), "sha256": sha256_file(skill)}
        if skill
        else {"loaded": False}
    )
    manifest_base = {
        "schema_version": "v1",
        "experiment_id": experiment_id,
        "condition": condition,
        "model": model.name,
        "temperature": cfg["temperature"],
        "timeout_seconds": cfg["timeout_seconds"],
        "max_tokens": cfg["max_tokens"],
        "tool_snapshot": tool_snapshot_for(tasks),
        "skill_snapshot": skill_snapshot,
        "task_pack_sha256": sha256_file(task_pack),
    }

    base = Path(out_root) / experiment_id / condition
    for task in tasks:
        for run_n in range(1, int(runs) + 1):
            execute_one(
                task,
                run_n,
                base / task["task_id"] / str(run_n),
                model,
                cfg,
                skill_text,
                manifest_base,
            )
    mark_contamination(Path(out_root) / experiment_id)
    return 0
