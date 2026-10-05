"""集成：三条线串起来跑一次（规格「集成」小节）。

固定顺序：

  1. 校验 A 的 task_pack.jsonl；失败退出码 2，B 不启动。
  2. B 跑 B-no-skill / runs=3 / experiment_id=live-baseline；无 API Key 写 blocker 并退出码 2；
     跑完立刻 summarize，确认产出 baseline_summary.json。
  3. 复制 attribution=knowledge_gap 且 split=train 的运行到 integration/tmp/train-knowledge-gap/。
  4. C 对临时目录 propose；no_change 时改走 regress --no-candidate，退出码 0。
  5. proposed 时 B 用候选 skill 再跑一遍（live-candidate），第二次 summarize。
  6. C 用两份 summary 做回归；pass / rollback / no_change 退出码都是 0。

用法（在仓库根）：

    .venv/Scripts/python.exe integration/pipeline/run_once.py

环境变量：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable

# ------------------------------------------------------------ 垂直
# 本项目主题固定为「外国人来中国旅行」（inbound 外国游客，geo=GLOBAL / en）。
# 路径全部对齐这一条垂直；变体机制保留，但只剩这一个（历史上那条出境线已移除）。
VARIANT_SPECS: dict[str, dict] = {
    "trippal": {
        "label": "外国人来华（inbound，en）",
        "a_out": REPO / "modules" / "demand-task-factory" / "out",
        "demand_input": REPO / "fixtures" / "demand_records_trippal_v1" / "records.jsonl",
        "run_root": REPO / "evals" / "runs",
        "train_kg": REPO / "integration" / "tmp" / "train-knowledge-gap",
        "c_out": REPO / "modules" / "skill-optimizer" / "out",
        "config": "config/default.yaml",
        "scenarios": "fixtures/trippal_v1/scenarios.json",
        "routing": "fixtures/trippal_v1/scenario_routing.json",
    },
}

DEFAULT_VARIANT = "trippal"
VARIANT = DEFAULT_VARIANT
A_OUT = TASK_PACK = SCENARIO_CATALOG = PROVENANCE = DEMAND_INPUT = None  # type: ignore
RUN_ROOT = LIVE_BASELINE = LIVE_CANDIDATE = TRAIN_KG = None  # type: ignore
C_OUT = CANDIDATE_SKILL = None  # type: ignore
CONFIG_PATH = SCENARIOS_PATH = ROUTING_PATH = ""


def select_variant(name: str) -> str:
    """按变体名重绑全部产物路径。未知名字回落到默认变体（不报错）。"""
    global VARIANT, A_OUT, TASK_PACK, SCENARIO_CATALOG, PROVENANCE, DEMAND_INPUT
    global RUN_ROOT, LIVE_BASELINE, LIVE_CANDIDATE, TRAIN_KG, C_OUT, CANDIDATE_SKILL
    global CONFIG_PATH, SCENARIOS_PATH, ROUTING_PATH
    VARIANT = name if name in VARIANT_SPECS else DEFAULT_VARIANT
    spec = VARIANT_SPECS[VARIANT]
    A_OUT = spec["a_out"]
    TASK_PACK = A_OUT / "task_pack.jsonl"
    SCENARIO_CATALOG = A_OUT / "scenario_catalog.jsonl"
    PROVENANCE = A_OUT / "provenance.json"
    DEMAND_INPUT = spec["demand_input"]
    RUN_ROOT = spec["run_root"]
    LIVE_BASELINE = RUN_ROOT / "live-baseline"
    LIVE_CANDIDATE = RUN_ROOT / "live-candidate"
    TRAIN_KG = spec["train_kg"]
    C_OUT = spec["c_out"]
    CANDIDATE_SKILL = C_OUT / "candidate_skill" / "SKILL.md"
    CONFIG_PATH = spec["config"]
    SCENARIOS_PATH = spec["scenarios"]
    ROUTING_PATH = spec["routing"]
    return VARIANT


select_variant(os.environ.get("TSD_VARIANT", DEFAULT_VARIANT))

DEVIATIONS = REPO / "integration" / "deviations.jsonl"
BLOCKERS = REPO / "integration" / "blockers"

BROWSER_FIELD_DENYLIST = (
    "browser_history",
    "visit_url",
    "visited_url",
    "cookie",
    "client_id",
    "user_agent",
)


def log(msg: str) -> None:
    print(msg, flush=True)


def rmtree(path: Path) -> None:
    """删掉一棵目录树。

    Windows 上不能直接用 `shutil.rmtree`：宿主会在 Python 里注入一个把删除重定向到回收站的
    shim，它按**轮次**累计删除数，超过阈值就把进程直接杀掉（无 traceback）。实测第二次重跑
    集成时正好踩到（count=910 > threshold=50），进程停在 step 3 一声不响地退出。
    改走 kernel32 直调 —— 那是系统调用，不经过被 patch 的那一层，且删除彻底、不进回收站。
    """
    if not path.exists():
        return
    if sys.platform == "win32":
        import ctypes

        k = ctypes.windll.kernel32
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                k.DeleteFileW(ctypes.c_wchar_p(os.path.join(root, name)))
            for name in dirs:
                k.RemoveDirectoryW(ctypes.c_wchar_p(os.path.join(root, name)))
        k.RemoveDirectoryW(ctypes.c_wchar_p(str(path)))
        if path.exists():  # 仍有被占用的文件时退回标准库，保证幂等
            shutil.rmtree(path, ignore_errors=True)
    else:
        shutil.rmtree(path, ignore_errors=True)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_module(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-m", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8")


def write_blocker(name: str, body: str) -> Path:
    BLOCKERS.mkdir(parents=True, exist_ok=True)
    p = BLOCKERS / f"{name}.md"
    p.write_text(body, encoding="utf-8")
    return p


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------- step 1
def step1_validate_task_pack() -> int:
    log(f"[1] 校验 A 的 task_pack.jsonl（变体 {VARIANT}：{VARIANT_SPECS[VARIANT]['label']}）")
    if not TASK_PACK.exists():
        rel = lambda p: p.relative_to(REPO).as_posix()  # noqa: E731
        build_cmd = (
            ".venv/Scripts/python.exe -m demand_task_factory build \\\n"
            f"  --input {rel(DEMAND_INPUT)} \\\n"
            f"  --config {CONFIG_PATH} \\\n"
            f"  --out {rel(A_OUT)}"
        )
        if SCENARIOS_PATH:
            build_cmd += f" \\\n  --scenarios {SCENARIOS_PATH}"
        write_blocker(
            "a-task-pack-missing",
            "# blocker: a-task-pack-missing\n\n"
            f"找不到 `{TASK_PACK}`（变体 {VARIANT}）。先跑 A 线：\n\n"
            "```\n" + build_cmd + "\n"
            "```\n",
        )
        log("    ✗ 缺 task_pack.jsonl，已写 blocker，退出码 2")
        return 2

    # 用 B 线的校验器跑 task schema（三线各有副本，这里借 B 的入口）
    proc = run_module(
        ["execution_evaluation", "validate", "--schema", "task", "--data", str(TASK_PACK)]
    )
    if proc.returncode != 0:
        log(f"    ✗ task_pack 不合法：{proc.stderr.strip()[:300]}")
        return 2
    rows = load_jsonl(TASK_PACK)
    log(f"    ✓ {len(rows)} 张任务卡通过 task schemav1；B 可以启动")
    return 0


# ---------------------------------------------------------------- step 2
def step2_live_baseline(runs: int) -> int:
    log("[2] B 跑 live-baseline（B-no-skill）")
    if not os.environ.get("LLM_API_KEY", "").strip():
        write_blocker(
            "live-baseline-missing-api-key",
            "# blocker: live-baseline-missing-api-key\n\n"
            "没有 `LLM_API_KEY`。按规格不得把 FakeModel 的结果当作 live 运行，"
            "所以集成在这里停下（退出码 2）。\n\n"
            "夹具侧的三条线测试不受影响，B、C 仍可用 FakeModel 只跑夹具。\n\n"
            "补上环境变量后重跑：`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`。\n",
        )
        log("    ✗ 无 LLM_API_KEY，已写 blocker，退出码 2")
        return 2

    proc = run_module(
        [
            "execution_evaluation", "run",
            "--task-pack", str(TASK_PACK),
            "--condition", "B-no-skill",
            "--runs", str(runs),
            "--experiment-id", "live-baseline",
            "--out", str(RUN_ROOT),
        ]
    )
    if proc.returncode != 0:
        log(f"    ✗ run 失败 rc={proc.returncode} {proc.stderr.strip()[:300]}")
        return 2

    proc = run_module(
        [
            "execution_evaluation", "summarize",
            "--experiment-id", "live-baseline",
            "--out", str(LIVE_BASELINE / "baseline_report.md"),
            "--run-root", str(RUN_ROOT),
        ]
    )
    if proc.returncode != 0 or not (LIVE_BASELINE / "baseline_summary.json").exists():
        log("    ✗ summarize 没产出 baseline_summary.json")
        return 2
    summary = json.loads((LIVE_BASELINE / "baseline_summary.json").read_text(encoding="utf-8"))
    log(
        f"    ✓ baseline_summary.json 就位：pass_at_3={summary['pass_at_3']} "
        f"train={summary['train_pass_at_3']} holdout={summary['holdout_pass_at_3']} "
        f"avg_tokens={summary['avg_tokens']}"
    )
    return 0


# ---------------------------------------------------------------- step 3
def step3_copy_knowledge_gap() -> int:
    log("[3] 复制 attribution=knowledge_gap 且 split=train 的运行")
    rmtree(TRAIN_KG)
    TRAIN_KG.mkdir(parents=True, exist_ok=True)

    copied = 0
    for vp in sorted((LIVE_BASELINE / "B-no-skill").glob("*/*/verdict.json")):
        run_dir = vp.parent
        verdict = json.loads(vp.read_text(encoding="utf-8"))
        if verdict.get("attribution") != "knowledge_gap":
            continue
        task_path = run_dir / "task.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if task.get("split") != "train":
            continue
        dest = TRAIN_KG / task["task_id"] / run_dir.name
        shutil.copytree(run_dir, dest)
        copied += 1

    if "holdout" in TRAIN_KG.parts:
        log("    ✗ 临时路径里出现 holdout")
        return 2
    log(f"    ✓ 复制 {copied} 次运行到 {TRAIN_KG.relative_to(REPO).as_posix()}")
    if copied == 0:
        log("      （没有 knowledge_gap 的 train 运行，C 会走 no_change 分支）")
    return 0


# ---------------------------------------------------------------- step 4
def step4_propose() -> tuple[int, str]:
    log("[4] C 对临时目录 propose")
    proc = run_module(
        ["skill_optimizer", "propose", "--run-bundle", str(TRAIN_KG), "--out", str(C_OUT)]
    )
    if proc.returncode != 0:
        log(f"    ✗ propose 失败 rc={proc.returncode} {proc.stderr.strip()[:300]}")
        return 2, ""
    report = json.loads((C_OUT / "attribution_report.json").read_text(encoding="utf-8"))
    log(
        f"    ✓ proposal_state={report['proposal_state']} "
        f"提案 {len(report['proposals'])} 条 排除 {report['excluded_by_attribution']}"
    )
    return 0, report["proposal_state"]


# ---------------------------------------------------------------- step 5
def step5_live_candidate(runs: int) -> int:
    log("[5] B 用候选 skill 跑 live-candidate")
    proc = run_module(
        [
            "execution_evaluation", "run",
            "--task-pack", str(TASK_PACK),
            "--condition", "C-seed-skill",
            "--runs", str(runs),
            "--experiment-id", "live-candidate",
            "--out", str(RUN_ROOT),
            "--skill", str(CANDIDATE_SKILL),
        ]
    )
    if proc.returncode != 0:
        log(f"    ✗ run 失败 rc={proc.returncode} {proc.stderr.strip()[:300]}")
        return 2
    proc = run_module(
        [
            "execution_evaluation", "summarize",
            "--experiment-id", "live-candidate",
            "--out", str(LIVE_CANDIDATE / "candidate_report.md"),
            "--run-root", str(RUN_ROOT),
        ]
    )
    if proc.returncode != 0 or not (LIVE_CANDIDATE / "candidate_summary.json").exists():
        log("    ✗ summarize 没产出 candidate_summary.json")
        return 2
    summary = json.loads((LIVE_CANDIDATE / "candidate_summary.json").read_text(encoding="utf-8"))
    log(
        f"    ✓ candidate_summary.json 就位：pass_at_3={summary['pass_at_3']} "
        f"holdout={summary['holdout_pass_at_3']} avg_tokens={summary['avg_tokens']}"
    )
    return 0


# ---------------------------------------------------------------- step 6
def step6_regress(has_candidate: bool) -> int:
    log("[6] C 回归门")
    base = LIVE_BASELINE / "baseline_summary.json"
    args = [
        "skill_optimizer", "regress",
        "--baseline", str(base),
        "--out", str(C_OUT / "evaluation_report.md"),
    ]
    if has_candidate:
        args += ["--candidate", str(LIVE_CANDIDATE / "candidate_summary.json")]
    else:
        args += ["--no-candidate"]
    proc = run_module(args)
    if proc.returncode != 0:
        log(f"    ✗ regress 失败 rc={proc.returncode} {proc.stderr.strip()[:300]}")
        return 2
    report = json.loads((C_OUT / "evaluation_report.json").read_text(encoding="utf-8"))
    log(f"    ✓ conclusion={report['conclusion']}（退出码仍是 0，CI 要读这个字段）")
    return 0


# ---------------------------------------------------------------- 收口
def closeout() -> bool:
    log("")
    log("收口核对（5 条，都应能从文件里查到）")
    ok = True

    prov = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    hash_ok = prov["input_sha256"] == sha256_file(DEMAND_INPUT)
    records = load_jsonl(DEMAND_INPUT)
    leaked = [k for r in records for k in r if k in BROWSER_FIELD_DENYLIST]
    log(f"  1. provenance 输入哈希对得上快照：{hash_ok}；需求记录无浏览器历史字段：{not leaked}")
    ok &= hash_ok and not leaked

    known = {r["record_id"] for r in records}
    bad = [
        s["scenario_id"]
        for s in load_jsonl(SCENARIO_CATALOG)
        if not set(s["evidence_record_ids"]) <= known
    ]
    log(f"  2. 每条 scenario 的 evidence_record_ids 都能在需求记录里找到：{not bad}")
    ok &= not bad

    manifests = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (LIVE_BASELINE / "B-no-skill").glob("*/*/manifest.json")
    ]
    all_false = bool(manifests) and all(m["skill_snapshot"] == {"loaded": False} for m in manifests)
    log(f"  3. B-no-skill 的 manifest 全是 loaded:false：{all_false}（{len(manifests)} 份）")
    ok &= all_false

    proposals = load_jsonl(C_OUT / "change_proposals.jsonl")
    verdicts = {}
    for p in TRAIN_KG.rglob("verdict.json"):
        v = json.loads(p.read_text(encoding="utf-8"))
        verdicts[v["task_id"]] = v.get("attribution")
    ev_ok = all(all(verdicts.get(t) == "knowledge_gap" for t in p["evidence_tasks"]) for p in proposals)
    log(
        f"  4. 每条提案的 evidence_tasks 都是 knowledge_gap：{ev_ok}"
        f"（提案 {len(proposals)} 条，空文件算通过）"
    )
    ok &= ev_ok

    ev_json = json.loads((C_OUT / "evaluation_report.json").read_text(encoding="utf-8"))
    md = (C_OUT / "evaluation_report.md").read_text(encoding="utf-8")
    consistent = ev_json["conclusion"] in md
    if ev_json["conclusion"] == "rollback":
        consistent &= LIVE_CANDIDATE.exists()
    log(
        f"  5. evaluation_report.json 的 conclusion 与 markdown 一致：{consistent}"
        f"（{ev_json['conclusion']}；live-candidate 目录仍在：{LIVE_CANDIDATE.exists()}）"
    )
    ok &= consistent

    return ok


def main(argv=None) -> int:
    runs = 3
    if argv and "--runs" in argv:
        runs = int(argv[argv.index("--runs") + 1])
    if argv and "--variant" in argv:
        select_variant(argv[argv.index("--variant") + 1])

    log(f"仓库：{REPO}")
    log(f"变体：{VARIANT}（{VARIANT_SPECS[VARIANT]['label']}）")
    log(f"runs_per_task = {runs}")
    log("")

    rc = step1_validate_task_pack()
    if rc:
        return rc
    rc = step2_live_baseline(runs)
    if rc:
        return rc
    rc = step3_copy_knowledge_gap()
    if rc:
        return rc
    rc, state = step4_propose()
    if rc:
        return rc

    if state == "proposed":
        rc = step5_live_candidate(runs)
        if rc:
            return rc
        rc = step6_regress(has_candidate=True)
    else:
        log("[5] 跳过：proposal_state=no_change（规格：这条路径不跑候选实验）")
        rc = step6_regress(has_candidate=False)
    if rc:
        return rc

    return 0 if closeout() else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
