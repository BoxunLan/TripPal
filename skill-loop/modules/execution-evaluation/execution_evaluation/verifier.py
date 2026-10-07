"""确定性 verifier：执行任务卡里的 assertions，不做任何 LLM 打分。"""

from __future__ import annotations

import re


def json_path_get(doc, path: str):
    """支持 $.a.b 与 $['a'] 的最小子集；取不到返回 _MISSING。"""
    if not path or not path.startswith("$"):
        return _MISSING
    cur = doc
    rest = path[1:]
    for token in re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[['\"]([^'\"]+)['\"]\]", rest):
        key = token[0] or token[1]
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return _MISSING
    return cur


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<missing>"


_MISSING = _Missing()


def evaluate(assertions: list[dict], output_text: str, output_json) -> list[dict]:
    results = []
    for a in assertions:
        op = a.get("op")
        if op == "contains":
            passed = str(a.get("value")) in (output_text or "")
            detail = f"文本{'包含' if passed else '不含'} {a.get('value')!r}"
        elif op == "regex":
            passed = re.search(str(a.get("value")), output_text or "") is not None
            detail = f"正则 {a.get('value')!r} {'命中' if passed else '未命中'}"
        elif op == "json_path_equals":
            got = json_path_get(output_json, a.get("path", ""))
            passed = got is not _MISSING and got == a.get("value")
            detail = f"{a.get('path')} = {got!r}（期望 {a.get('value')!r}）"
        else:
            passed = False
            detail = f"未知 op {op!r}"
        results.append({"op": op, "value": a.get("value"), "passed": passed, "detail": detail})
    return results


def overall_pass(results: list[dict]) -> bool:
    return bool(results) and all(r["passed"] for r in results)


def needs_json(task: dict) -> bool:
    return any(a.get("op") == "json_path_equals" for a in task["verifier"]["assertions"])
