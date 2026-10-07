"""模型适配器。

- 没有 `LLM_API_KEY` → `FakeModel`，不读网络，`verdict.live` 记 false。
- 有 `LLM_API_KEY` → 走 `LLM_BASE_URL` 的 OpenAI 兼容 chat/completions。
密钥只从环境变量读。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class ModelTimeout(Exception):
    pass


class ModelServerError(Exception):
    pass


class ModelParseError(Exception):
    pass


class FakeModel:
    """规格 B 线实现顺序第 1 条：输入里包含任务 prompt 时返回固定 JSON。"""

    name = "fake-model-v1"
    live = False

    def complete(self, prompt: str, skill_text: str | None = None) -> tuple[str, dict]:
        text = json.dumps({"decision": "unknown", "sources": []}, ensure_ascii=False)
        usage = {
            "input_tokens": max(1, len(prompt) // 3),
            "output_tokens": max(1, len(text) // 3),
        }
        return text, usage


class HttpModel:
    live = True

    def __init__(self, base_url: str, api_key: str, model: str, temperature=0, max_tokens=2000, timeout=60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.name = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, prompt: str, skill_text: str | None = None) -> tuple[str, dict]:
        body = {
            "model": self.name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": skill_text or ""},
                {"role": "user", "content": prompt},
            ],
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            raw = urllib.request.urlopen(req, timeout=self.timeout).read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if 500 <= exc.code < 600:
                raise ModelServerError(f"HTTP {exc.code}") from exc
            raise ModelParseError(f"HTTP {exc.code}") from exc
        except TimeoutError as exc:
            raise ModelTimeout("请求超时") from exc
        except urllib.error.URLError as exc:
            raise ModelServerError(str(exc.reason)) from exc

        try:
            payload = json.loads(raw)
            text = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {})
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ModelParseError(f"响应解析失败: {raw[:200]}") from exc
        return text, {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
        }


def build_model(cfg: dict):
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        return FakeModel()
    return HttpModel(
        base_url=os.environ.get("LLM_BASE_URL", "").strip() or "https://api.openai.com/v1",
        api_key=api_key,
        model=os.environ.get("LLM_MODEL", "").strip() or "gpt-4o-mini",
        temperature=cfg["temperature"],
        max_tokens=int(cfg["max_tokens"]),
        timeout=float(cfg["timeout_seconds"]),
    )
