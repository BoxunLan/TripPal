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


SKILL_ENTRY_FILENAME = "SKILL.md"
SKILL_REFERENCE_GLOBS = ("references/**/*.md", "references/**/*.txt")


def load_skill(skill) -> tuple[str | None, dict]:
    """读 SKILL 的内容与快照，支持**单文件**与**目录**两种形态。

    原先这里是 `Path(skill).read_text()`：**单文件读取，不展开目录、不跟随引用**。
    而真实的产品件本身就是多文件包（`SKILL.md` + `references/*.md`），
    于是 `references/**` **静默注入不到、也不报错**。
    实测同一个产品件：只注入 `SKILL.md` 时 Δ=+0.0 pt；把 `references/` 一并拼进去后 Δ=+0.6 pt。
    两者差的不是模型能力，是**注入范围** —— 而旧版没有任何地方能把这件事看出来。

    目录形态的拼接顺序固定为 `SKILL.md` 在前、`references/**` 按路径排序在后，
    每段前加一行 `<!-- 相对路径 -->`，让轨迹里看得出这段文字来自哪个文件。
    快照记**实际注入的文件清单 + 各自 sha256**，否则
    「同一实验的 skill_snapshot 一致」并不能证明注入内容完整。

    单文件形态的快照与旧版**逐字段相同**（`path` 指向该文件、`sha256` 是该文件哈希），
    以保持既有产物、既有测试与下游统计的兼容；目录形态才额外带 `files`。
    """
    if not skill:
        return None, {"loaded": False}
    root = Path(skill)
    if root.is_dir():
        files: list[Path] = []
        entry = root / SKILL_ENTRY_FILENAME
        if entry.is_file():
            files.append(entry)
        for pattern in SKILL_REFERENCE_GLOBS:
            files.extend(sorted(p for p in root.glob(pattern) if p.is_file()))
        ordered: list[Path] = []
        seen: set[str] = set()
        for f in files:
            key = str(f.resolve())
            if key not in seen:
                seen.add(key)
                ordered.append(f)
        if not ordered:
            raise FileNotFoundError(f"skill 目录里没有可注入的文件：{root}")
        text = "\n\n".join(
            f"<!-- {p.relative_to(root).as_posix()} -->\n{p.read_text(encoding='utf-8')}"
            for p in ordered
        )
        return text, {
            "loaded": True,
            "path": str(root).replace("\\", "/"),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "files": [
                {"path": p.relative_to(root).as_posix(), "sha256": sha256_file(p)}
                for p in ordered
            ],
        }
    if root.is_file():
        return root.read_text(encoding="utf-8"), {
            "loaded": True,
            "path": str(root).replace("\\", "/"),
            "sha256": sha256_file(root),
        }
    raise FileNotFoundError(f"skill 路径不存在：{root}")


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


def now_ts() -> str:
    """每步真实时间戳。旧版是常量 `2026-09-29T10:00:00+08:00`，轨迹里看不出耗时与先后。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _row(step, ts, action, observation, decision, skipped, cost, artifact=""):
    return {
        "schema_version": "v1",
        "step": step,
        "timestamp": now_ts(),
        "action": action,
        "observation": observation,
        "decision": decision,
        "skipped_or_abandoned": skipped,
        "cost": cost,
        "artifact": artifact,
    }


def cost(input_tokens=0, output_tokens=0, tool_calls=0, synthetic=False):
    """一步的 token 成本。

    `synthetic=True` 表示这一步**没有真的调用模型**，数字是常量占位
    （read_task / lookup_local 两步就是这样）。报告里必须与真实 token 分开统计，
    否则 avg_tokens / efficiency / 成本回归门都建立在混合值上。
    """
    out = {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "tool_calls": int(tool_calls),
    }
    if synthetic:
        out["synthetic"] = True
    return out


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
    rows.append(_row(1, ts, "read_task", f"task={task['task_id']}", "开始执行", "", cost(120, 8, synthetic=True)))

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
                cost(60, 10, 1, synthetic=True),
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
    # `failure_kind` / `http_status` 把「为什么没过」结构化，供报告按类分桶。
    # 光看 `attribution` 是不够的：`execution_defect` 一桶里混着
    # 「HTTP 402 没钱」「HTTP 429 限流」「抽不出 JSON」「输出语种不符」四种东西，
    # 实测只按 `attribution` 统计会把「端点欠费」读成「模型能力差」。
    failure_kind: str | None = None
    http_status: int | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            text, usage = model.complete(prompt, skill_text)
            status, attribution, last_error = None, None, None
            failure_kind, http_status = None, None
            break
        except ModelTimeout as exc:
            status, attribution, last_error = "timeout", "missing_tool_or_data", str(exc)
            http_status, failure_kind = getattr(exc, "http_status", None), "timeout"
        except ModelServerError as exc:
            status, attribution, last_error = "error", "execution_defect", str(exc)
            http_status = getattr(exc, "http_status", None)
            failure_kind = f"http_{http_status}" if http_status else "server_error"
        except ModelParseError as exc:
            status, attribution, last_error = "error", "execution_defect", str(exc)
            # 4xx 走的就是这一支：402（余额不足）/429（限流）时**根本没发生推理**，
            # 必须与「响应抽不出 JSON」分开，否则会把「没钱」读成「模型差」。
            http_status = getattr(exc, "http_status", None)
            failure_kind = f"http_{http_status}" if http_status else "parse_error"
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
        finish = str((usage or {}).get("finish_reason") or "")
        if finish == "length" and (not text.strip() or parsed is None):
            # 推理模型（reasoning tokens 占大头）常在预算耗尽前吐不出 JSON。
            # 这是"预算不够"，不是"模型不会"，更不是知识缺口 —— 必须单独定位。
            status, attribution = "error", "execution_defect"
            failure_kind = "budget_exhausted"
            last_error = (
                f"输出被 max_tokens 截断（finish_reason=length, out={usage.get('output_tokens')}, "
                f"reasoning={usage.get('reasoning_tokens')}）：failure_kind=budget_exhausted，提高预算后重跑"
            )
            rows.append(
                _row(3, ts, "answer", text[:200] or "<空响应>", last_error, "提高 max_tokens 后重跑",
                     cost(usage["input_tokens"], usage["output_tokens"]))
            )
        elif verifier.needs_json(task) and parsed is None:
            status, attribution = "error", "execution_defect"
            failure_kind = "format_contract"
            last_error = "响应里抽不出 JSON，不重试解析：failure_kind=format_contract"
            rows.append(
                _row(3, ts, "answer", text[:200], "响应解析失败，按 execution_defect 落盘（failure_kind=format_contract）", "", cost(usage["input_tokens"], usage["output_tokens"]))
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
            cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
            language_mismatch = bool(text) and cjk / max(1, len(text)) > 0.15 and any(
                r["op"] in ("regex", "contains") and not r["passed"] for r in results
            )
            if passed:
                attribution = "none"
            elif language_mismatch:
                # 输出语言不符（英文题面答中文）是契约问题，不是知识缺口。
                attribution = "execution_defect"
                failure_kind = "language_mismatch"
                fail_reason = ("输出语言不符（failure_kind=language_mismatch）：" + fail_reason)[:160]
            else:
                attribution = "knowledge_gap" if data_available else "missing_tool_or_data"
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
        # `failure_kind` 是 `attribution` 的**细分**：`execution_defect` 会被拆成
        # `http_402` / `http_429` / `budget_exhausted` / `format_contract` /
        # `language_mismatch` / `server_error` / `parse_error`，其余情况与 `attribution` 同值。
        # 报告按它分桶，才能把「端点欠费」与「模型答不出契约」分开。
        # 注意 `verdict.schema.json` 里 `attribution` 是 5 值 enum ——
        # 所以细分**只能**靠这个新字段，不能去改 `attribution` 的取值。
        "failure_kind": "none" if status == "pass" else (failure_kind or attribution or "execution_defect"),
        "http_status": http_status,
        "contaminated": False,
        "scores": scores,
    }
    (run_dir / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return verdict


def mark_contamination(experiment_dir: Path) -> list[str]:
    """同 experiment_id 下两个 condition 的 manifest 关键字段不一致 → 双方 contaminated。

    「任务集是不是同一份」只比**整包**哈希 `task_pack_sha256_full`，**不比** `task_pack_sha256`：
    后者是本次实际消费的**切片**文件（`packN.jsonl`）的摘要，两条臂 `--jobs` 不同时必然不同，
    拿它跨臂比会把任务集完全相同的两臂判成互相污染。调用方没提供整包哈希时**跳过这一项**
    并打印提示 —— 静默放松检查比误判更危险。
    """
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

    fields = ("model", "temperature", "timeout_seconds", "tool_snapshot")
    dirty: list[str] = []
    names = sorted(manifest_by_condition)
    missing_full_hash: set[str] = set()
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = manifest_by_condition[names[i]], manifest_by_condition[names[j]]
            mismatched = [f for f in fields if a.get(f) != b.get(f)]
            # 「任务集是不是同一份」只认**整包**哈希：分片哈希在两条臂 `--jobs` 不同时
            # 必然不同，拿它跨臂比会把同一数据集判成互相污染。
            fa = (a.get("task_pack_sha256_full") or "").strip()
            fb = (b.get("task_pack_sha256_full") or "").strip()
            if fa and fb:
                if fa != fb:
                    mismatched.append("task_pack_sha256_full")
            else:
                missing_full_hash.update([names[i], names[j]])
            if mismatched:
                dirty.extend([names[i], names[j]])

    if missing_full_hash:
        print(
            "[mark_contamination] 这些 condition 的 manifest 没有整包哈希 task_pack_sha256_full，"
            f"已**跳过**任务集一致性检查：{', '.join(sorted(missing_full_hash))}",
            flush=True,
        )

    for name in sorted(set(dirty)):
        for run_dir in dirs_by_condition.get(name, []):
            vp = run_dir / "verdict.json"
            if not vp.exists():
                continue
            v = json.loads(vp.read_text(encoding="utf-8"))
            v["contaminated"] = True
            vp.write_text(json.dumps(v, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sorted(set(dirty))


def run(
    task_pack,
    condition,
    runs,
    experiment_id,
    out_root,
    skill=None,
    config_path=None,
    task_pack_sha256_full: str | None = None,
) -> int:
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
    skill_text, skill_snapshot = load_skill(skill)

    manifest_base = {
        "schema_version": "v1",
        "experiment_id": experiment_id,
        "condition": condition,
        "model": model.name,
        "temperature": cfg["temperature"],
        # 记**真实生效**的预算：build_model 支持 LLM_MAX_TOKENS / LLM_TIMEOUT_SECONDS 覆盖，
        # 只写 cfg 默认值会让 manifest 撒谎（实测用 4096/180 跑，manifest 却写 2000/60）。
        # 用 getattr 容忍测试里的 FakeModel（它只实现 name/complete，没有预算属性）。
        "timeout_seconds": getattr(model, "timeout", None) or cfg["timeout_seconds"],
        "max_tokens": getattr(model, "max_tokens", None) or cfg["max_tokens"],
        "config_timeout_seconds": cfg["timeout_seconds"],
        "config_max_tokens": cfg["max_tokens"],
        "tool_snapshot": tool_snapshot_for(tasks),
        "skill_snapshot": skill_snapshot,
        # 注意：这是**本次运行实际消费的那个文件**的摘要；4 路并行时是切片文件（pack0.jsonl…），
        # 不是完整数据集包的摘要。数据集包的摘要在 out-v2/provenance.json 的 pack_sha256。
        "task_pack_sha256": sha256_file(task_pack),
        # 「整包」哈希由调用方传入（只有驱动脚本知道完整数据集包在哪）。加这一项是因为
        # **分片哈希不能跨臂比**：两条臂 `--jobs` 不同时切片文件名与内容必然不同，
        # `task_pack_sha256` 也就必然不同 —— 拿它判断「是否同一任务集」，
        # 会把任务集完全相同的两臂误判成互相污染。`mark_contamination` 因此只看这一项。
        "task_pack_sha256_full": (task_pack_sha256_full or "").strip(),
        "task_pack_path": str(task_pack).replace("\\", "/"),
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
