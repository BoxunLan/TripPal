"""契约 v1 校验。

规格「不许改」第 6 条要求三条线不 import 彼此的 Python 包，所以本文件在三个模块里
各有一份副本，内容一致。改动时三份要同步。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIR = REPO_ROOT / "contracts" / "v1"


def schema_path(name: str) -> Path:
    """接受契约文件名（task / task.schema.json）或任意可读路径。"""
    p = Path(name)
    if p.exists():
        return p
    if not name.endswith(".json"):
        name = name + ".schema.json"
    return CONTRACT_DIR / name


def load_schema(name: str) -> dict:
    return json.loads(schema_path(name).read_text(encoding="utf-8"))


def iter_errors(name: str, data) -> list[dict]:
    import jsonschema

    validator = jsonschema.Draft202012Validator(load_schema(name))
    out = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path = "/" + "/".join(str(p) for p in err.path)
        out.append({"path": path, "message": err.message})
    return out


def is_valid(name: str, data) -> bool:
    return not iter_errors(name, data)


def load_jsonl(path: str | Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def _read_data(raw: str) -> list:
    p = Path(raw)
    if not p.exists():
        return [{"__raw__": raw}]
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".jsonl":
        return [json.loads(l) for l in text.splitlines() if l.strip()]
    return [json.loads(text)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="validate")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--data", required=True)
    args = ap.parse_args(argv)

    errors: list[dict] = []
    for i, doc in enumerate(_read_data(args.data), start=1):
        for err in iter_errors(args.schema, doc):
            if args.data.endswith(".jsonl"):
                err = {"path": f"line{i}:{err['path']}", "message": err["message"]}
            errors.append(err)

    if errors:
        sys.stderr.write(json.dumps(errors, ensure_ascii=False) + "\n")
        return 2
    return 0
