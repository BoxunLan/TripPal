"""用两对例子锁定相似度函数与阈值（规格 A 线实现顺序第 2 步）。

例子换成项目主题（外国人来华）的查询串。阈值与规则没变。
"""

from __future__ import annotations

from demand_task_factory.normalize import (
    SIMILARITY_THRESHOLD,
    cluster_keys,
    jaccard,
    normalize,
    similar,
)


def test_normalize_strips_function_words():
    assert normalize("  中国签证需要什么材料 ") == "中国签证"
    assert normalize("中国签证怎么办") == "中国签证"
    assert normalize("故宫门票怎么买") == "故宫门票"
    assert normalize("China ESim　推荐") == "chinaesim"


def test_pair_that_must_cluster():
    a, b = "上海到北京怎么走", "上海到北京交通"
    assert jaccard(normalize(a), normalize(b)) >= SIMILARITY_THRESHOLD
    assert similar(a, b) is True


def test_pair_that_must_not_cluster():
    a, b = "中国签证怎么办", "中国上网卡推荐"
    assert jaccard(normalize(a), normalize(b)) < SIMILARITY_THRESHOLD
    assert similar(a, b) is False


def test_threshold_is_locked():
    assert SIMILARITY_THRESHOLD == 0.6


def test_cluster_keys_groups_variants():
    records = [
        {"record_id": "a", "query_or_topic": "上海到北京怎么走"},
        {"record_id": "b", "query_or_topic": "上海到北京交通"},
        {"record_id": "c", "query_or_topic": "上海到北京要多久"},
        {"record_id": "d", "query_or_topic": "中国上网卡推荐"},
    ]
    clusters = cluster_keys(records)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 3]
