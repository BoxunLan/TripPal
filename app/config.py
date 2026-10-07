"""环境变量 + routes.yaml 的唯一读取入口。密钥只从环境变量来，代码里不出现密钥。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 读取，避免为一行配置引入额外依赖。已存在的环境变量优先。"""
    path = path or REPO_ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "fake"
    classifier_model: str = ""
    classifier_api_key: str = ""
    generator_model: str = ""
    generator_api_key: str = ""
    shared_api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    timeout_s: float = 60.0

    def key_for(self, role: str) -> str:
        if role == "classifier":
            return self.classifier_api_key or self.shared_api_key
        return self.generator_api_key or self.shared_api_key


@dataclass(frozen=True)
class EmbeddingSettings:
    model: str = ""
    dim: int = 1024
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    batch: int = 16


@dataclass(frozen=True)
class Settings:
    llm: LLMSettings
    embedding: EmbeddingSettings
    database_url: str
    vector_backend: str
    routes: dict[str, Any] = field(default_factory=dict)
    routes_path: Path = REPO_ROOT / "routes.yaml"
    seed_dir: Path = REPO_ROOT / "seed"

    # --- routes.yaml 便捷访问 ---
    @property
    def version_pin(self) -> str:
        return str(self.routes.get("version_pin", "routes@unversioned"))

    @property
    def classifier_version(self) -> str:
        return str(self.routes.get("classifier", {}).get("version", "cls-unknown"))

    @property
    def scenes(self) -> dict[str, Any]:
        return self.routes.get("scenes", {})

    @property
    def scene_ids(self) -> list[str]:
        return [s for s in self.scenes if not self.scenes[s].get("fallback")]

    @property
    def threshold_clarify(self) -> float:
        return float(self.routes.get("classifier", {}).get("clarify_below", 0.45))

    @property
    def threshold_hard_route(self) -> float:
        return float(self.routes.get("classifier", {}).get("soft_fusion_max", 0.70))

    @property
    def fallback_scene(self) -> str:
        for sid, cfg in self.scenes.items():
            if cfg.get("fallback"):
                return sid
        return "general"

    def scene_cfg(self, scene_id: str) -> dict[str, Any]:
        return self.scenes.get(scene_id, {})

    def required_slots(self, scene_id: str) -> list[str]:
        cfg = self.scene_cfg(scene_id)
        return list(cfg.get("required_slots", []))

    def tool_allowlist(self, scene_id: str) -> list[str]:
        return list(self.scene_cfg(scene_id).get("tool_allowlist", []))

    def layer_quota(self, scene_id: str | None) -> dict[str, float]:
        retr = self.routes.get("retrieval", {})
        base = dict(retr.get("layer_quota", {"general": 0.2, "scene": 0.5, "realtime": 0.3}))
        overrides = retr.get("layer_quota_overrides", {}) or {}
        override = self.scene_cfg(scene_id or "").get("layer_quota") or overrides.get(scene_id or "")
        if override:
            base.update({k: float(v) for k, v in override.items()})
        total = sum(base.values()) or 1.0
        return {k: v / total for k, v in base.items()}

    @property
    def top_k(self) -> int:
        return int(self.routes.get("retrieval", {}).get("top_k", 14))

    @property
    def candidate_multiplier(self) -> int:
        return int(self.routes.get("retrieval", {}).get("candidate_multiplier", 6))

    @property
    def retriever_version(self) -> str:
        return str(self.routes.get("retrieval", {}).get("version", "retr-unknown"))

    @property
    def available_tools(self) -> list[str]:
        return list(self.routes.get("tools", {}).get("available", []))

    def guardrail_text(self, guardrail_id: str) -> str:
        return str(self.routes.get("guardrails", {}).get(guardrail_id, {}).get("text", ""))

    @property
    def overlay_priority(self) -> list[str]:
        return list(self.routes.get("fusion", {}).get("overlay_priority", []))

    @property
    def prompt_paths(self) -> dict[str, str]:
        return dict(self.routes.get("prompts", {}))

    @property
    def slot_patterns(self) -> dict[str, list[str]]:
        return dict(self.routes.get("slot_extraction", {}))


def _seed_dir() -> Path:
    return Path(os.environ.get("TRAVEL_SEED_DIR", REPO_ROOT / "seed"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    routes_path = Path(os.environ.get("TRAVEL_ROUTES", REPO_ROOT / "routes.yaml"))
    routes = yaml.safe_load(routes_path.read_text(encoding="utf-8")) or {}
    shared = os.environ.get("TRAVEL_LLM_API_KEY", "")
    provider = os.environ.get("TRAVEL_LLM_PROVIDER", "").strip().lower()
    if not provider:
        provider = "openai" if (shared or os.environ.get("TRAVEL_GENERATOR_API_KEY")) else "fake"
    return Settings(
        llm=LLMSettings(
            provider=provider,
            classifier_model=os.environ.get("TRAVEL_CLASSIFIER_MODEL", ""),
            classifier_api_key=os.environ.get("TRAVEL_CLASSIFIER_API_KEY", ""),
            generator_model=os.environ.get("TRAVEL_GENERATOR_MODEL", ""),
            generator_api_key=os.environ.get("TRAVEL_GENERATOR_API_KEY", ""),
            shared_api_key=shared,
            base_url=os.environ.get("TRAVEL_LLM_BASE_URL", "https://api.openai.com/v1"),
            timeout_s=float(os.environ.get("TRAVEL_LLM_TIMEOUT_S", "60")),
        ),
        embedding=EmbeddingSettings(
            model=os.environ.get("TRAVEL_EMBEDDING_MODEL", ""),
            dim=int(os.environ.get("TRAVEL_EMBEDDING_DIM", "1024")),
            api_key=os.environ.get("TRAVEL_EMBEDDING_API_KEY", shared),
            base_url=os.environ.get("TRAVEL_EMBEDDING_BASE_URL", os.environ.get("TRAVEL_LLM_BASE_URL", "https://api.openai.com/v1")),
            batch=int(os.environ.get("TRAVEL_EMBEDDING_BATCH", "16")),
        ),
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://travel:travel@localhost:5433/travel"
        ),
        vector_backend=os.environ.get("TRAVEL_VECTOR_BACKEND", "pg").strip().lower(),
        routes=routes,
        routes_path=routes_path,
        seed_dir=_seed_dir(),
    )
