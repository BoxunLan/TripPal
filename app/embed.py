"""Embedding。

默认走本地确定性 hash embedding（字符 bigram + 英文分词），好处是：
- 测试与离线演示不需要任何密钥；
- 同一份种子库在任何人机器上召回结果一致，验收断言才不会飘。

配了 TRAVEL_EMBEDDING_MODEL 就切到 OpenAI 兼容的 /embeddings 接口。
注意：hash embedding 只有词汇级召回能力，同义改写召回不到 —— 这是刻意接受的降级，
不是设计目标。真实召回质量依赖向量模型。
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Protocol

import httpx

from .config import EmbeddingSettings

logger = logging.getLogger("travel")

_ASCII_WORD = re.compile(r"[a-zA-Z0-9]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    tokens: list[str] = []
    for run in _CJK_RUN.findall(text):
        tokens.extend(run)  # 单字
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))  # 双字
    tokens.extend(_ASCII_WORD.findall(text))
    return tokens


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    name = "hash-v1"

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in tokenize(text):
            h = hashlib.md5(tok.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            # 双字比单字信息量大，权重更高
            vec[idx] += sign * (2.0 if len(tok) > 1 else 1.0)
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


class APIEmbedder:
    name = "api-v1"

    def __init__(self, settings: EmbeddingSettings) -> None:
        self.settings = settings
        self.dim = settings.dim
        self.batch = max(1, int(settings.batch or 1))

    def embed(self, texts: list[str]) -> list[list[float]]:
        """分批调用。端点对单次 input 数组长度有上限（实测 ecnu-embedding-small：
        32 条通过、64 条直接 500），种子库 130+ 条必须分批才能灌进去。"""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch):
            vectors.extend(self._embed_batch(texts[start : start + self.batch]))
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        url = self.settings.base_url.rstrip("/") + "/embeddings"
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"},
            json={"model": self.settings.model, "input": texts},
            timeout=60.0,
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        vectors = [d["embedding"] for d in data]
        if vectors and len(vectors[0]) != self.dim:
            # 不在这里报错的话，错误会推迟到 pgvector 写入时才炸（列类型 vector(N) 与向量长度不符），
            # 那时错误信息里只有 pg 的列定义，看不出是 embedding 配置的问题。
            raise EmbeddingDimMismatch(
                f"embedding 模型 {self.settings.model!r} 返回 {len(vectors[0])} 维，"
                f"但 TRAVEL_EMBEDDING_DIM={self.dim}。二者必须一致，且要与 "
                f"docker/init/01_schema.sql 里 embedding 列的 vector({self.dim}) 相同。"
            )
        return vectors


class EmbeddingDimMismatch(RuntimeError):
    pass


def build_embedder(settings: EmbeddingSettings) -> Embedder:
    if settings.model and settings.api_key:
        return APIEmbedder(settings)
    if settings.model and not settings.api_key:
        # 静默降级会让召回质量与配置预期不符，且 hash 向量与 API 向量不可混用，
        # 必须显式告警（与 build_store 回退内存库时的处理保持一致）。
        logger.warning(
            "已配置 TRAVEL_EMBEDDING_MODEL=%s 但缺少 TRAVEL_EMBEDDING_API_KEY（也没配 "
            "TRAVEL_LLM_API_KEY），回退到本地 hash embedding，召回质量仅供跑通链路。",
            settings.model,
        )
    return HashingEmbedder(settings.dim)
