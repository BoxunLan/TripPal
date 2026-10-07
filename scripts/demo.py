"""带密钥时打真实接口的演示脚本。

    python scripts/demo.py                 # 跑 5 条验收消息，走真实 LLM + pgvector
    python scripts/demo.py "带孩子穷游厦门 3 天，预算 2000"
    python scripts/demo.py --offline       # 无密钥时用假实现走一遍同样的链路

需要 .env 里配好 TRAVEL_*_MODEL / TRAVEL_LLM_API_KEY，并先执行：
    docker compose up -d && python scripts/load_seed.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEMO_MESSAGES = [
    "想去泰国玩",
    "带 6 岁孩子去厦门 5 天，预算 8000",
    "两人穷游清迈 4 天，每天 300",
    "带孩子穷游厦门 3 天，预算 2000",
    "240 小时过境免签适用哪些国家",
]


def preview(body: dict) -> str:
    kind = body.get("type")
    if kind == "clarify":
        return f"[clarify] {body['question']}\n  missing_slots={body['missing_slots']}"
    if kind == "degraded":
        return f"[degraded] {body['reason']}\n  unresolved={body['validation']['unresolved']}"
    route = body["route"]
    budget = body["itinerary"].get("budget") or {}
    return (
        f"[plan] route={route['route_id']} scenes={route['scenes']} "
        f"fusion={route['fusion_policy']} tools={route['tool_allowlist']}\n"
        f"  天数={len(body['itinerary']['days'])} 引用={len(body['citations'])} "
        f"清单={len(body['checklist'])} 合计={budget.get('total')} 预算={budget.get('user_budget')}\n"
        f"  validation: passed={body['validation']['passed']} round={body['validation']['round']} "
        f"degraded={body['validation']['degraded']} unresolved={body['validation']['unresolved']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("message", nargs="*", default=None, help="自定义消息，留空跑 5 条验收消息")
    parser.add_argument("--offline", action="store_true", help="强制使用假 LLM + 内存向量库")
    parser.add_argument("--json", action="store_true", help="打印完整响应 JSON")
    args = parser.parse_args()

    if args.offline:
        import os

        os.environ["TRAVEL_LLM_PROVIDER"] = "fake"
        os.environ["TRAVEL_VECTOR_BACKEND"] = "memory"

    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    messages = args.message or DEMO_MESSAGES

    problems = 0
    for idx, message in enumerate(messages, 1):
        print(f"\n=== {idx}. {message}")
        resp = client.post("/plan", json={"session_id": f"demo-{idx}", "message": message})
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code}: {resp.text[:300]}")
            problems += 1
            continue
        body = resp.json()
        print("  " + preview(body).replace("\n", "\n  "))
        if args.json:
            print(json.dumps(body, ensure_ascii=False, indent=2))

    print(f"\n完成。异常 {problems} 条。")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
