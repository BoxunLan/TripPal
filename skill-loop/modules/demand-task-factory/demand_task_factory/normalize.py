"""查询归一化与聚类。

规格：`build` 第 2 步 —— 规范化去掉首尾空白、英文小写、全角转半角；两条查询在去掉
「怎么办 / 需要什么 / 材料」这些功能词后，字符 bigram Jaccard 相似度 ≥ 0.6 则归入同一簇。
相似度函数与阈值写死在代码里，并在 tests/test_cluster.py 里用两对例子锁定。
"""

from __future__ import annotations

import re
import unicodedata

SIMILARITY_THRESHOLD = 0.6

# 规格点名了「怎么办 / 需要什么 / 材料」，其余按同一口径补齐（全部是疑问/询问句式词与
# 通用度量词，不承载主题信息）。实现时按长度从长到短依次剥离，避免「需要什么」被
# 「什么」先切掉。
FUNCTION_WORDS = [
    "需要什么",
    "需要哪些",
    "怎么办",
    "怎么走",
    "怎么买",
    "怎么用",
    "要多久",
    "多少",
    "多久",
    "价格",
    "推荐",
    "哪些",
    "什么",
    "怎么",
    "需要",
    "材料",
    "费用",
    "吗",
    "呢",
    "?",
    "？",
]


def normalize(text: str) -> str:
    """去首尾空白 · 全角转半角 · 英文小写 · 去所有空白 · 剥离功能词。"""
    s = unicodedata.normalize("NFKC", str(text))
    s = s.strip().lower()
    s = re.sub(r"\s+", "", s)
    for w in sorted(FUNCTION_WORDS, key=len, reverse=True):
        s = s.replace(w, "")
    return s.strip()


def bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def jaccard(a: str, b: str) -> float:
    ga, gb = bigrams(a), bigrams(b)
    if not ga and not gb:
        return 1.0
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def similar(a: str, b: str, threshold: float = SIMILARITY_THRESHOLD) -> bool:
    return jaccard(normalize(a), normalize(b)) >= threshold


def cluster_keys(records: list[dict]) -> list[list[str]]:
    """按归一化 key 的相似度做单链聚类，返回每个簇的 record_id 列表（顺序稳定）。

    单链（而不是跟簇心比）是为了让「上海到北京怎么走 / 上海到北京交通 / 上海到北京要多久」
    这类长短不一的变体归到一起。
    """
    clusters: list[dict] = []
    for rec in records:
        key = normalize(rec["query_or_topic"])
        placed = False
        for c in clusters:
            if any(jaccard(key, k) >= SIMILARITY_THRESHOLD for k in c["keys"]):
                c["keys"].append(key)
                c["ids"].append(rec["record_id"])
                placed = True
                break
        if not placed:
            clusters.append({"keys": [key], "ids": [rec["record_id"]]})
    return [c["ids"] for c in clusters]
