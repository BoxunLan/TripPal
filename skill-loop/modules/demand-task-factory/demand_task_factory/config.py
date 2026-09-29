"""可调参数：只在 config/default.yaml，允许被环境变量覆盖（同名大写）。"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULTS = {
    # 项目主题：外国人来华。默认口径固定为入境游客（geo=GLOBAL / en），不再有出境线。
    "geo": "GLOBAL",
    "language": "en",
    "time_window": "2025-09-29/2026-09-29",
    "scenario_target": 8,
    "scenario_floor": 3,
    "tasks_per_scenario": 2,
    "runs_per_task": 3,
    "max_proposals": 5,
    "temperature": 0,
    "timeout_seconds": 60,
    "max_tokens": 2000,
    "cost_budget_ratio": 1.5,
    # 任务卡声明的检索夹具体（相对仓库根）。
    "tool_data": "fixtures/tool_data_trippal_v1/pages.json",
    # 题面语言：en（英语入境游客）。
    "prompt_language": "en",
    # 场景边界必须落在中国语境里；A 线按这个开关做硬校验（违例报错退出）。
    "require_china_context": True,
}


def load_config(path: str | Path) -> dict:
    cfg = dict(DEFAULTS)
    p = Path(path)
    if p.exists():
        import yaml

        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k, v in loaded.items():
            if k in cfg:
                cfg[k] = v
    for k in list(cfg.keys()):
        env = os.environ.get(k.upper())
        if env is None or env == "":
            continue
        if isinstance(cfg[k], bool):
            cfg[k] = env.strip().lower() in ("1", "true", "yes")
        elif isinstance(cfg[k], int):
            cfg[k] = int(env)
        elif isinstance(cfg[k], float):
            cfg[k] = float(env)
        else:
            cfg[k] = env
    return cfg
