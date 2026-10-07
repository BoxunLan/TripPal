"""LLM 访问层。

分类与生成走两个不同角色（小模型 / 强推理模型），模型名与 API Key 全部来自环境变量。

`complete_json` 同时接收 `prompt` 与 `context`：
- 真实客户端用 `prompt`（自然语言提示词）调用 API；
- 离线假客户端忽略 `prompt`，只用结构化的 `context` 做确定性推演。
这个显式缝隙让测试可以在无密钥、无网络的条件下跑通整条链路，而不需要假装解析提示词。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Protocol

import httpx

from .config import LLMSettings

Role = Literal["classifier", "generator"]


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


class ChatLLM:
    """OpenAI 兼容的 chat/completions 客户端。"""

    provider = "openai"

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

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

    def complete_json(
        self, *, role: Role, prompt: str, schema: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        model, key = self._model_and_key(role)
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        base_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你只输出符合给定 JSON Schema 的 JSON，不含任何解释文字。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2 if role == "generator" else 0.0,
        }
        # 先试严格 json_schema，失败再退到 json_object、最后退到纯文本
        attempts: list[dict[str, Any]] = [
            {"type": "json_schema", "json_schema": {"name": role, "schema": schema, "strict": False}},
            {"type": "json_object"},
            None,
        ]
        last_err: Exception | None = None
        for rf in attempts:
            payload = dict(base_payload)
            if rf is not None:
                payload["response_format"] = rf
            try:
                resp = httpx.post(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=self.settings.timeout_s,
                )
                if resp.status_code >= 400:
                    last_err = LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    continue
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return _extract_json(content)
            except Exception as exc:  # noqa: BLE001 - 逐级降级，最后一并抛出
                last_err = exc
                continue
        raise LLMError(f"调用 {role} 模型失败：{last_err}")


def build_llm(settings) -> LLMClient:
    """settings 为完整 Settings（假实现需要 routes.yaml 里的场景词典）。"""
    if (settings.llm.provider or "").lower() == "openai":
        return ChatLLM(settings.llm)
    from .fakes import FakeLLM

    return FakeLLM(settings)
