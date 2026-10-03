"""本地跑「真模型」集成：从 `.env` 读 TRAVEL_*，映射成 run_once 要的 LLM_*。

用法（skill-loop 目录，用本目录的 venv）：

    .venv/Scripts/python.exe integration/pipeline/run_live.py

- 端点/Key 只来自 `.env`（先找 `skill-loop/.env`，再找其父目录），不落盘、不打印。
- 清掉 http(s)_proxy 等代理变量：宿主注册表里可能有系统代理，会让本地直连每次多等 ~2s。
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _has_llm_key(path: Path) -> bool:
    try:
        return "TRAVEL_LLM_API_KEY" in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def find_env_file() -> Path:
    """`.env` 优先取 skill-loop 自己的，其次**逐级向上**找。

    原先只向上找一级（`skill-loop/.env` -> `skill-loop/../.env`），
    而常见摆法是把 `.env` 放在**工作区根**——比 `skill-loop/` 高两级——
    于是直接跑必然 `exit 2`，且报错只说"没有 TRAVEL_LLM_API_KEY"，
    看起来像"没配 Key"，实际是"没找到装着 Key 的那个文件"。
    这里逐级向上，并在中途遇到**不含 Key** 的同名文件时继续往上找，
    避免某个无关的 `.env` 把真正的配置挡住。
    """
    candidates = [c / ".env" for c in (REPO, *REPO.parents)]
    for c in candidates:
        if c.is_file() and _has_llm_key(c):
            return c
    for c in candidates:  # 兜底：都不含 Key 就返回第一个存在的，由 main 报错说明
        if c.is_file():
            return c
    return REPO / ".env"


ENV_FILE = find_env_file()


def load_env(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    env = load_env(ENV_FILE)
    api_key = env.get("TRAVEL_LLM_API_KEY", "").strip()
    base_url = env.get("TRAVEL_LLM_BASE_URL", "").strip()
    model = (env.get("TRAVEL_GENERATOR_MODEL") or env.get("TRAVEL_CLASSIFIER_MODEL") or "").strip()

    if not api_key:
        print(f"[run_live] {ENV_FILE} 里没有 TRAVEL_LLM_API_KEY，无法跑 live 集成", flush=True)
        return 2

    os.environ["LLM_API_KEY"] = api_key
    if base_url:
        os.environ["LLM_BASE_URL"] = base_url
    if model:
        os.environ["LLM_MODEL"] = model

    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        os.environ.pop(k, None)

    print(f"[run_live] endpoint={base_url} model={model} key=***{api_key[-4:]}", flush=True)
    runpy.run_path(str(REPO / "integration" / "pipeline" / "run_once.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
