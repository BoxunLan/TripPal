"""可调参数：只在 config/default.yaml，允许被环境变量覆盖（同名大写）。"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULTS = {
    "geo": "CN",
    "language": "zh-CN",
    "time_window": "2026-08-01/2026-08-31",
    "scenario_target": 8,
    "scenario_floor": 3,
    "tasks_per_scenario": 2,
    "runs_per_task": 3,
    "max_proposals": 5,
    "temperature": 0,
    "timeout_seconds": 60,
    "max_tokens": 2000,
    "cost_budget_ratio": 1.5,
    "cost_budget_basis": "total",
    "cost_delta_allowance": 400,
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


DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "default.yaml"
