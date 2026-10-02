"""Build a task pack containing only tasks that failed in an earlier run.

A task is considered failed when the selected verdict tree contains at least one
verdict for its task_id and none of those verdicts has ``overall_pass=true``.
This matches pass@N semantics when an earlier experiment used multiple runs.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select tasks that did not pass in an earlier verdict tree."
    )
    parser.add_argument("--task-pack", type=Path, required=True)
    parser.add_argument("--verdict-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    verdict_paths = sorted(args.verdict_root.rglob("verdict.json"))
    if not verdict_paths:
        parser.error(f"no verdict.json files found under {args.verdict_root}")

    outcomes: dict[str, list[bool]] = defaultdict(list)
    previous_tasks: dict[str, dict] = {}
    for path in verdict_paths:
        verdict = read_json(path)
        task_id = verdict.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            parser.error(f"verdict has no task_id: {path}")
        outcomes[task_id].append(verdict.get("overall_pass") is True)
        task_path = path.parent / "task.json"
        if task_path.is_file():
            previous_tasks[task_id] = read_json(task_path)

    failed_ids = {task_id for task_id, runs in outcomes.items() if not any(runs)}
    pack_rows: list[tuple[str, str, dict]] = []
    pack_ids: set[str] = set()
    for line_number, line in enumerate(
        args.task_pack.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        card = json.loads(line)
        task_id = card.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            parser.error(f"task pack row {line_number} has no task_id")
        if task_id in pack_ids:
            parser.error(f"duplicate task_id in task pack: {task_id}")
        pack_ids.add(task_id)
        pack_rows.append((task_id, line, card))

    # A-family IDs were historically sequential.  Adding or removing a fact in an
    # earlier FAQ can shift every later ID, so an ID-only retry can silently run the
    # wrong question.  Remap those tasks by the real asked question; other families
    # have stable pattern/template IDs and keep direct matching even when their
    # source query is intentionally improved.
    selected_set: set[str] = set()
    remapped: dict[str, list[str]] = {}
    missing_ids: list[str] = []
    for failed_id in sorted(failed_ids):
        matched: list[str] = []
        old_task = previous_tasks.get(failed_id, {})
        old_question = (old_task.get("initial_state") or {}).get("asked_question")
        if failed_id.startswith("V2A-") and old_question:
            same_id = next((card for task_id, _, card in pack_rows if task_id == failed_id), None)
            if same_id and (same_id.get("initial_state") or {}).get("asked_question") == old_question:
                matched = [failed_id]
            else:
                candidates = [
                    (task_id, card)
                    for task_id, _, card in pack_rows
                    if (card.get("initial_state") or {}).get("asked_question") == old_question
                ]
                if candidates:
                    old_fact = str((old_task.get("expected_end_state") or {}).get("fact_full") or "")
                    best_id, _ = max(
                        candidates,
                        key=lambda item: difflib.SequenceMatcher(
                            None,
                            old_fact,
                            str((item[1].get("expected_end_state") or {}).get("fact_full") or ""),
                        ).ratio(),
                    )
                    matched = [best_id]
        elif failed_id in pack_ids:
            matched = [failed_id]
        if not matched:
            missing_ids.append(failed_id)
            continue
        selected_set.update(matched)
        if matched != [failed_id]:
            remapped[failed_id] = matched

    rows = [(task_id, line) for task_id, line, _ in pack_rows if task_id in selected_set]

    selected_ids = [task_id for task_id, _ in rows]
    if not selected_ids:
        parser.error("none of the failed task IDs exist in the current task pack")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(line for _, line in rows) + "\n", encoding="utf-8")

    family_counts: dict[str, int] = defaultdict(int)
    for task_id in selected_ids:
        family = task_id[2] if len(task_id) > 2 and task_id.startswith("V2") else "other"
        family_counts[family] += 1

    manifest = {
        "schema_version": "v1",
        "selection": "failed_without_any_passing_run",
        "source_task_pack": str(args.task_pack.resolve()),
        "source_task_pack_sha256": sha256(args.task_pack),
        "source_verdict_root": str(args.verdict_root.resolve()),
        "verdict_files": len(verdict_paths),
        "task_ids_with_verdicts": len(outcomes),
        "failed_task_ids": len(failed_ids),
        "selected_task_count": len(selected_ids),
        "missing_task_ids": missing_ids,
        "remapped_task_ids": remapped,
        "family_counts": dict(sorted(family_counts.items())),
        "selected_pack_sha256": sha256(args.out),
    }
    manifest_path = args.manifest or args.out.with_suffix(".manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"selected {len(selected_ids)} current tasks for {len(failed_ids)} prior failed task IDs "
        f"from {len(verdict_paths)} verdicts; missing={len(missing_ids)}"
    )
    print(f"families: {json.dumps(manifest['family_counts'], sort_keys=True)}")
    print(f"pack: {args.out}")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
