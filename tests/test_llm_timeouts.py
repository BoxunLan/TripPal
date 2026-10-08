"""`app/llm.py` 的分档超时 / 档位记忆 / 逐次日志。

对应 2026-10-07「有时候响应慢」的诊断：分类（小模型，正常 2–4s）与生成（强模型，
正常 45–105s）共用了一个 60s 超时 —— 前者卡死时要白等满 60s 才降级（实测 63s 尾延迟），
后者贴着阈值跑、稍慢就被误判超时再重跑一遍。

这里把三件修复钉死：① 超时按档位给；② 记住最近成功的 response_format；
③ 每次尝试都记日志。全部用 `httpx.post` 桩，无网络。
"""

from __future__ import annotations

import json
import logging

import pytest

import app.llm as llm_mod
from app.config import LLMSettings, get_settings
from app.llm import ChatLLM, LLMError, profile_of


def _settings(**over) -> LLMSettings:
    base = dict(
        provider="openai",
        classifier_model="small",
        generator_model="big",
        shared_api_key="k",
        base_url="http://llm.test/v1",
    )
    base.update(over)
    return LLMSettings(**base)


class _Resp:
    def __init__(self, status_code: int = 200, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _ok(body) -> _Resp:
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(body)}}]})


def _install_post(monkeypatch, handler) -> list[dict]:
    """把 httpx.post 换成本地桩；handler(payload, timeout) -> _Resp。返回调用记录。"""
    calls: list[dict] = []

    def fake_post(url, *, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return handler(json, timeout)

    monkeypatch.setattr(llm_mod.httpx, "post", fake_post)
    return calls


def _tier_of(payload) -> str:
    rf = (payload or {}).get("response_format")
    return "text" if rf is None else str(rf.get("type"))


class _Clock:
    """替掉 app.llm 里的 time 模块，让「耗时」可控（只用到 monotonic）。"""

    def __init__(self, values: list[float]) -> None:
        self._it = iter(values)

    def monotonic(self) -> float:
        return next(self._it)


# ------------------------------------------------------------------ 档位划分
@pytest.mark.parametrize(
    "role,context,expected",
    [
        ("classifier", {}, "classifier"),
        ("classifier", {"task": "answer"}, "classifier"),
        ("generator", {}, "generator:plan"),
        ("generator", {"task": "plan"}, "generator:plan"),
        ("generator", {"task": "answer"}, "generator:answer"),
        ("generator", {"task": "guide"}, "generator:guide"),
        ("generator", {"task": "ANSWER"}, "generator:answer"),
        ("generator", None, "generator:plan"),
    ],
)
def test_profile_splits_by_role_and_task(role, context, expected):
    assert profile_of(role, context) == expected


def test_default_timeouts_match_real_cost():
    """超时按「该档正常耗时 + 兜底质量」给，不再一刀切。

    生成最长（正常就到 105s，且没有兜底）；寒暄**最紧** —— 它现在只服务 `meta` 一种
    输入、只产出一句话，而且定稿文案本身就是完整答复，多等一秒都是净损失。
    """
    s = LLMSettings()
    assert s.guide_timeout_s < s.classifier_timeout_s < s.answer_timeout_s < s.generator_timeout_s
    assert s.guide_timeout_s <= 10, "寒暄有完整定稿兜底，超时要收得很紧"
    assert s.classifier_timeout_s <= 20, "分类正常 2–4s，超时不够短就会在卡死时白等"
    assert s.generator_timeout_s >= 150, "生成正常就到 105s，超时必须留足余量"


def test_settings_reads_profile_timeouts_from_env(monkeypatch):
    monkeypatch.setenv("TRAVEL_LLM_TIMEOUT_CLASSIFIER_S", "7")
    monkeypatch.setenv("TRAVEL_LLM_TIMEOUT_GENERATOR_S", "200")
    monkeypatch.setenv("TRAVEL_LLM_TIMEOUT_ANSWER_S", "77")
    monkeypatch.setenv("TRAVEL_LLM_TIMEOUT_GUIDE_S", "9")
    get_settings.cache_clear()
    try:
        llm = get_settings().llm
        assert (llm.classifier_timeout_s, llm.generator_timeout_s) == (7.0, 200.0)
        assert (llm.answer_timeout_s, llm.guide_timeout_s) == (77.0, 9.0)
    finally:
        get_settings.cache_clear()


def test_single_shared_timeout_knob_is_gone():
    """`TRAVEL_LLM_TIMEOUT_S` 那个一刀切旋钮必须拆掉，否则慢的根因还在。"""
    assert not hasattr(LLMSettings(), "timeout_s")


# ------------------------------------------------------------------ 超时按档位下发
def test_timeout_sent_to_endpoint_matches_profile(monkeypatch):
    client = ChatLLM(
        _settings(
            classifier_timeout_s=11,
            generator_timeout_s=222,
            answer_timeout_s=88,
            guide_timeout_s=33,
        )
    )
    calls = _install_post(monkeypatch, lambda p, t: _ok({"labels": []}))

    client.complete_json(role="classifier", prompt="p", schema={}, context={})
    assert calls[-1]["timeout"] == 11

    client.complete_json(role="generator", prompt="p", schema={}, context={})
    assert calls[-1]["timeout"] == 222

    client.complete_json(role="generator", prompt="p", schema={}, context={"task": "answer"})
    assert calls[-1]["timeout"] == 88

    client.complete_json(role="generator", prompt="p", schema={}, context={"task": "guide"})
    assert calls[-1]["timeout"] == 33


# ------------------------------------------------------------------ 档位记忆
def test_cold_start_tries_json_schema_first(monkeypatch):
    client = ChatLLM(_settings())
    calls = _install_post(monkeypatch, lambda p, t: _ok({}))
    client.complete_json(role="classifier", prompt="p", schema={}, context={})
    assert _tier_of(calls[0]["json"]) == "json_schema"


def test_remembers_last_working_tier_and_starts_from_it(monkeypatch):
    """json_schema 卡死一次后，后续直接从 json_object 起步，不再白等。"""
    client = ChatLLM(_settings())

    def handler(payload, timeout):
        if _tier_of(payload) == "json_schema":
            raise TimeoutError("simulated read timeout")
        return _ok({"labels": []})

    calls = _install_post(monkeypatch, handler)
    client.complete_json(role="classifier", prompt="p", schema={}, context={})

    assert client.status()["classifier"] == "json_object"
    assert [_tier_of(c["json"]) for c in calls] == ["json_schema", "json_object"]

    calls.clear()
    client.complete_json(role="classifier", prompt="p", schema={}, context={})
    assert _tier_of(calls[0]["json"]) == "json_object", "第二次应从成功过的那档开始"


def test_tier_memory_is_per_profile(monkeypatch):
    """分类学到 json_object 不该连累生成：两个角色的模型不同。"""
    client = ChatLLM(_settings())

    def handler(payload, timeout):
        if _tier_of(payload) == "json_schema":
            raise TimeoutError("boom")
        return _ok({})

    _install_post(monkeypatch, handler)
    client.complete_json(role="classifier", prompt="p", schema={}, context={})
    assert client.status() == {"classifier": "json_object"}

    calls = _install_post(monkeypatch, lambda p, t: _ok({}))
    client.complete_json(role="generator", prompt="p", schema={}, context={})
    assert _tier_of(calls[0]["json"]) == "json_schema", "生成档位的记忆应独立于分类"


# ------------------------------------------------------------------ 尝试上限
def test_attempts_are_bounded_per_profile(monkeypatch):
    client = ChatLLM(_settings())
    calls = _install_post(monkeypatch, lambda p, t: _Resp(500, text="boom"))

    with pytest.raises(LLMError):
        client.complete_json(role="classifier", prompt="p", schema={}, context={})
    assert len(calls) == 3, "分类三档降级"

    calls.clear()
    with pytest.raises(LLMError):
        client.complete_json(role="generator", prompt="p", schema={}, context={})
    assert len(calls) == 2, "生成超时长，最多两档，避免最坏 3×180s"


def test_text_tier_used_when_json_modes_rejected(monkeypatch):
    client = ChatLLM(_settings())

    def handler(payload, timeout):
        if _tier_of(payload) != "text":
            return _Resp(400, text="response_format unsupported")
        return _Resp(200, {"choices": [{"message": {"content": "```json\n{\"labels\": []}\n```"}}]})

    calls = _install_post(monkeypatch, handler)
    out = client.complete_json(role="classifier", prompt="p", schema={}, context={})

    assert out == {"labels": []}
    assert [_tier_of(c["json"]) for c in calls] == ["json_schema", "json_object", "text"]
    assert calls[-1]["json"].get("response_format") is None


def test_error_carries_last_failure(monkeypatch):
    client = ChatLLM(_settings())
    _install_post(monkeypatch, lambda p, t: _Resp(418, text="teapot"))
    with pytest.raises(LLMError) as excinfo:
        client.complete_json(role="classifier", prompt="p", schema={}, context={})
    assert "418" in str(excinfo.value)


# ------------------------------------------------------------------ 日志
def test_each_failed_attempt_is_logged(monkeypatch, caplog):
    client = ChatLLM(_settings())
    _install_post(monkeypatch, lambda p, t: _Resp(500, text="boom"))
    with caplog.at_level(logging.WARNING, logger="travel"):
        with pytest.raises(LLMError):
            client.complete_json(role="classifier", prompt="p", schema={}, context={})

    msgs = [r.getMessage() for r in caplog.records]
    assert sum("llm 失败" in m for m in msgs) == 3, "每次尝试都要留痕"
    assert any("format=json_schema" in m and "profile=classifier" in m for m in msgs)
    assert any("全部档位失败" in m for m in msgs)


def test_success_is_logged(monkeypatch, caplog):
    client = ChatLLM(_settings())
    _install_post(monkeypatch, lambda p, t: _ok({}))
    with caplog.at_level(logging.INFO, logger="travel"):
        client.complete_json(role="classifier", prompt="p", schema={}, context={})

    msgs = [r.getMessage() for r in caplog.records]
    assert any("llm 成功" in m and "耗时=" in m for m in msgs)


def test_slow_success_is_warned(monkeypatch, caplog):
    """成功但耗时超过该档超时一半 → WARNING（默认日志级别就能看见）。"""
    client = ChatLLM(_settings(classifier_timeout_s=10))
    monkeypatch.setattr(llm_mod, "time", _Clock([0.0, 8.0]))  # 8s > 10s 的一半
    _install_post(monkeypatch, lambda p, t: _ok({}))

    with caplog.at_level(logging.WARNING, logger="travel"):
        client.complete_json(role="classifier", prompt="p", schema={}, context={})

    warn = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warn and "偏慢" in warn[0].getMessage()


def test_fast_success_stays_at_info(monkeypatch, caplog):
    """别把正常调用也吵成 WARNING，否则日志会被淹没。"""
    client = ChatLLM(_settings(classifier_timeout_s=15))
    monkeypatch.setattr(llm_mod, "time", _Clock([0.0, 2.0]))  # 2s，远低于 7.5s 阈值
    _install_post(monkeypatch, lambda p, t: _ok({}))

    with caplog.at_level(logging.WARNING, logger="travel"):
        client.complete_json(role="classifier", prompt="p", schema={}, context={})

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
