"""LLM 访问层。

分类与生成走两个不同角色（小模型 / 强推理模型），模型名与 API Key 全部来自环境变量。

`complete_json` 同时接收 `prompt` 与 `context`：
- 真实客户端用 `prompt`（自然语言提示词）调用 API；
- 离线假客户端忽略 `prompt`，只用结构化的 `context` 做确定性推演。
这个显式缝隙让测试可以在无密钥、无网络的条件下跑通整条链路，而不需要假装解析提示词。

按档位分超时（2026-10-09 修「有时候响应慢」）
--------------------------------------------
过去所有调用共用 `TRAVEL_LLM_TIMEOUT_S`（默认 60s）。但这条链路上有两类**性质完全不同**
的调用，一个 60s 同时害了两边：

- **分类**（小模型）：正常只要 2–4s。端点偶发卡死时要**白等满 60s** 才降级 —— 实测到的
  63s 尾延迟（60s 超时 + 3s 降级成功）就是这么来的。
- **行程生成**（强模型）：正常就是 45–105s（长 JSON，约 4KB）。阈值与正常耗时**贴得太近**，
  稍慢一点就被判超时，再从头重跑一遍。

于是超时按**档位**给。档位 = `role` + `context["task"]`：

| 档位 | 谁在调 | 默认超时 | 尝试上限 |
|---|---|---|---|
| `classifier` | classifier.py | 15s | 3 |
| `generator:plan` | generate.py | 180s | 2 |
| `generator:answer` | knowledge.py | 90s | 2 |
| `generator:guide` | guide.py（**仅 `meta`**，套话已直答不调模型） | 6s | 2 |

另外两件事：

1. **记住最近一次成功的 `response_format`**（实例级、进程内）。过去每次都从最慢的
   `json_schema` 重新试一遍；现在记下成功的那一档，下次直接从它开始 —— 一次卡死之后就
   绕开那一档。这是刻意的取舍：宁可退到宽松档，也不要每次白等。进程重启后重新学习。
2. **每次尝试都记日志**（档位 / 耗时 / 超时 / 结果）。失败或「耗时超过该档超时一半」的
   尝试记 WARNING（默认可见）；正常的记 INFO。下次再出现「有时候慢」，看日志就知道
   卡在哪一档、卡了多久，不必再像 2026-10-07 那样反推。

注：`response_format` 三档是**降级**（严格 → 宽松），不是同参数重试，所以「尝试上限」
就是单次请求内最多发几次 HTTP。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Literal, Protocol

import httpx

from .config import LLMSettings

Role = Literal["classifier", "generator"]

logger = logging.getLogger("travel")

# response_format 的三档降级，从严格到宽松。名字只用于日志与「档位记忆」。
_TIER_NAMES = ("json_schema", "json_object", "text")

# 档位 → 单次请求内最多发几次（对应三档降级）。
_PROFILE_MAX_ATTEMPTS: dict[str, int] = {
    "classifier": 3,
    "generator:plan": 2,
    "generator:answer": 2,
    "generator:guide": 2,
}


class LLMClient(Protocol):
    provider: str

    def complete_json(
        self, *, role: Role, prompt: str, schema: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]: ...


class LLMError(RuntimeError):
    pass


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = _FENCE.sub("", text or "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise LLMError(f"模型返回内容无法解析为 JSON：{cleaned[:200]}")


def profile_of(role: str, context: dict[str, Any] | None) -> str:
    """调用档位 = `role` + `context.task`。generator 无 task 时按行程生成算（plan）。"""
    if role == "classifier":
        return "classifier"
    task = str((context or {}).get("task") or "").strip().lower()
    if task in ("guide", "answer"):
        return f"generator:{task}"
    return "generator:plan"


def _tier_format(tier: str, role: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    """档位名 → 写进 payload 的 `response_format`（text 档返回 None，即不带该字段）。"""
    if tier == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": role, "schema": schema, "strict": False}}
    if tier == "json_object":
        return {"type": "json_object"}
    return None


class ChatLLM:
    """OpenAI 兼容的 chat/completions 客户端。"""

    provider = "openai"

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        # 档位 → 最近一次成功过的档位名。每次成功都更新，供下一次调用起步用。
        self._preferred: dict[str, str] = {}

    def _model_and_key(self, role: Role) -> tuple[str, str]:
        if role == "classifier":
            model = self.settings.classifier_model
        else:
            model = self.settings.generator_model
        key = self.settings.key_for(role)
        if not model:
            raise LLMError(
                f"缺少模型名：请设置 "
                f"{'TRAVEL_CLASSIFIER_MODEL' if role == 'classifier' else 'TRAVEL_GENERATOR_MODEL'}"
            )
        if not key:
            raise LLMError("缺少 API Key：请设置 TRAVEL_LLM_API_KEY 或角色专属 Key")
        return model, key

    def status(self) -> dict[str, Any]:
        """当前学到的档位偏好。给探针 / 诊断用（不参与业务逻辑）。"""
        return dict(self._preferred)

    def _timeout_for(self, profile: str) -> float:
        s = self.settings
        return {
            "classifier": s.classifier_timeout_s,
            "generator:plan": s.generator_timeout_s,
            "generator:answer": s.answer_timeout_s,
            "generator:guide": s.guide_timeout_s,
        }.get(profile, s.generator_timeout_s)

    def _attempt_order(self, profile: str, max_attempts: int) -> list[str]:
        """优先从记忆里那一档开始，再按降级顺序往下取，最多 max_attempts 档。"""
        names = list(_TIER_NAMES)
        preferred = self._preferred.get(profile)
        if preferred in names:
            i = names.index(preferred)
            names = names[i:] + names[:i]
        return names[: max(1, max_attempts)]

    def complete_json(
        self, *, role: Role, prompt: str, schema: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        model, key = self._model_and_key(role)
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        profile = profile_of(role, context)
        timeout = self._timeout_for(profile)
        order = self._attempt_order(profile, _PROFILE_MAX_ATTEMPTS.get(profile, 2))
        base_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你只输出符合给定 JSON Schema 的 JSON，不含任何解释文字。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2 if role == "generator" else 0.0,
        }

        last_err: Exception | None = None
        for i, tier in enumerate(order, start=1):
            payload = dict(base_payload)
            rf = _tier_format(tier, role, schema)
            if rf is not None:
                payload["response_format"] = rf

            started = time.monotonic()
            out: dict[str, Any] | None = None
            err: Exception | None = None
            try:
                resp = httpx.post(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=timeout,
                )
                if resp.status_code >= 400:
                    err = LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    out = _extract_json(resp.json()["choices"][0]["message"]["content"])
            except Exception as exc:  # noqa: BLE001 - 逐级降级，最后一并抛出
                err = exc
            elapsed = time.monotonic() - started

            if out is not None:
                self._preferred[profile] = tier
                slow = elapsed >= timeout * 0.5
                (logger.warning if slow else logger.info)(
                    "llm 成功 role=%s profile=%s format=%s attempt=%d/%d 耗时=%.2fs 超时=%.0fs%s",
                    role, profile, tier, i, len(order), elapsed, timeout, " (偏慢)" if slow else "",
                )
                return out

            last_err = err
            logger.warning(
                "llm 失败 role=%s profile=%s format=%s attempt=%d/%d 耗时=%.2fs 超时=%.0fs 错误=%s",
                role, profile, tier, i, len(order), elapsed, timeout, err,
            )

        logger.error(
            "llm 全部档位失败 role=%s profile=%s 尝试=%d 末次错误=%s",
            role, profile, len(order), last_err,
        )
        raise LLMError(f"调用 {role} 模型失败：{last_err}")


def build_llm(settings) -> LLMClient:
    """settings 为完整 Settings（假实现需要 routes.yaml 里的场景词典）。"""
    if (settings.llm.provider or "").lower() == "openai":
        return ChatLLM(settings.llm)
    from .fakes import FakeLLM

    return FakeLLM(settings)
