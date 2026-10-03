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


class ModelCallError(Exception):
    """模型调用失败的基类，携带 **HTTP 状态码**。

    记 `http_status` 是为了把「账户欠费 / 被限流」与「模型答不出来」分开：
    两者原先都落进 `execution_defect`，于是一次 `HTTP 402`（余额不足、根本没发生推理）
    会被读成「这个模型很差」。状态码透传后，verdict 可以按码分桶统计。
    """

    def __init__(self, message: str = "", http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


class ModelTimeout(ModelCallError):
    pass


class ModelServerError(ModelCallError):
    pass


class ModelParseError(ModelCallError):
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
        # 固定 seed：temperature=0 在 llama.cpp 下也不完全确定（KV 复用/批处理会引入漂移），
        # 而基准评测需要"同输入同判定"。可用 LLM_SEED 覆盖或置空关闭。
        seed = os.environ.get("LLM_SEED", "0")
        if seed != "":
            body["seed"] = int(seed)
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
            # 把服务端返回的正文一并带上：只有 "HTTP 400" 时无法区分「上下文超长」「请求体不合法」
            # 「模型没加载」等情况，实测就是这样把 12/12 张卡的 400 查了半天。
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace").strip()[:300]
            except Exception:  # noqa: BLE001 - 读不到正文不该掩盖原始错误
                detail = ""
            message = f"HTTP {exc.code}" + (f": {detail}" if detail else "")
            if 500 <= exc.code < 600:
                raise ModelServerError(message, http_status=exc.code) from exc
            # 4xx 里有两类完全不同的东西：402/429 是**计费与限流**（请求没被受理），
            # 而请求体不合法才是真的调用错误。状态码原样带出去，由上层分桶。
            raise ModelParseError(message, http_status=exc.code) from exc
        except TimeoutError as exc:
            raise ModelTimeout("请求超时") from exc
        except urllib.error.URLError as exc:
            raise ModelServerError(str(exc.reason)) from exc

        try:
            payload = json.loads(raw)
            choice = payload["choices"][0]
            message = choice["message"]
            text = message["content"]
            usage = payload.get("usage", {})
            finish_reason = choice.get("finish_reason") or ""
            reasoning = message.get("reasoning_content") or ""
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ModelParseError(f"响应解析失败: {raw[:200]}") from exc
        return text, {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            # 推理模型的三件套：截断原因、思考 token 数、思考字符数。
            # 旧版只取 message.content，于是「预算耗尽」和「模型答错」在轨迹里长得一样。
            "finish_reason": finish_reason,
            "reasoning_tokens": int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)),
            "reasoning_chars": len(reasoning),
        }


def build_model(cfg: dict):
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        return FakeModel()
    # 推理模型会把预算烧在 reasoning 上，所以允许用 LLM_MAX_TOKENS / LLM_TIMEOUT_SECONDS
    # 临时抬高预算与超时，而不改配置文件（本地 2.6B 模型实测会撞 60s 上限）。
    max_tokens = int(os.environ.get("LLM_MAX_TOKENS") or cfg["max_tokens"])
    timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS") or cfg["timeout_seconds"])
    return HttpModel(
        base_url=os.environ.get("LLM_BASE_URL", "").strip() or "https://api.openai.com/v1",
        api_key=api_key,
        model=os.environ.get("LLM_MODEL", "").strip() or "gpt-4o-mini",
        temperature=cfg["temperature"],
        max_tokens=max_tokens,
        timeout=timeout,
    )
