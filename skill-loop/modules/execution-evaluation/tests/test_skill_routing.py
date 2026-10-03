"""按域注入（routed skill injection）的回归测试。

为什么需要它：路由架构的 SKILL 把内容放在 `static/fragments/**`，而 harness 原先只会
"把目录里所有能注入的文件都拼上"。那样测的是"12 个分片全塞进上下文"，
与文件型 agent 的真实行为（按路由器指示只读命中的那几个）不是一回事。

`--skill-route` 用 skill 自带的路由表决定注入哪些分片；这里固定住它的四条契约：
只注入命中的分片、顺序稳定、快照记下命中、路由表坏了要**报错而不是静默全量注入**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution_evaluation import runner  # noqa: E402


@pytest.fixture()
def skill_dir(tmp_path: Path) -> Path:
    root = tmp_path / "skill"
    (root / "static" / "core").mkdir(parents=True)
    (root / "static" / "fragments" / "domain").mkdir(parents=True)
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# Router\nload fragments from disk\n", encoding="utf-8")
    (root / "manifest.yaml").write_text("name: t\naxes:\n  domain:\n    values:\n", encoding="utf-8")
    (root / "static" / "core" / "principles.md").write_text("# core\n", encoding="utf-8")
    for slug in ("alpha", "beta"):
        (root / "static" / "fragments" / "domain" / f"{slug}.md").write_text(
            f"# {slug}\n", encoding="utf-8")
    (root / "references" / "policy.md").write_text("# policy\n", encoding="utf-8")
    (root / "routing.json").write_text(json.dumps({
        "task_field": "scenario_id",
        "values": {"S1": ["static/fragments/domain/alpha.md"],
                   "S2": ["static/fragments/domain/beta.md"]},
    }), encoding="utf-8")
    return root


def test_full_injection_includes_every_fragment(skill_dir: Path) -> None:
    text, snapshot = runner.load_skill(skill_dir)
    paths = [f["path"] for f in snapshot["files"]]
    assert "static/fragments/domain/alpha.md" in paths
    assert "static/fragments/domain/beta.md" in paths
    assert "routing" not in snapshot, "非路由模式不应声称做过路由"
    assert "alpha" in text and "beta" in text


def test_routed_injection_only_loads_matched_fragment(skill_dir: Path) -> None:
    route = runner.load_route(skill_dir / "routing.json")
    text, snapshot = runner.load_skill(skill_dir, route, {"scenario_id": "S1"})
    paths = [f["path"] for f in snapshot["files"]]
    assert "static/fragments/domain/alpha.md" in paths
    assert "static/fragments/domain/beta.md" not in paths, "未命中的分片不得注入"
    assert "alpha" in text and "beta" not in text
    # 常驻层与参考层必须照常注入，否则路由就把 core 一起丢了
    assert "static/core/principles.md" in paths
    assert "references/policy.md" in paths
    assert "manifest.yaml" in paths
    assert snapshot["routing"]["task_value"] == "S1"
    assert snapshot["routing"]["fragments"] == ["static/fragments/domain/alpha.md"]


def test_routed_injection_with_unknown_value_loads_no_fragment(skill_dir: Path) -> None:
    route = runner.load_route(skill_dir / "routing.json")
    _, snapshot = runner.load_skill(skill_dir, route, {"scenario_id": "S9"})
    paths = [f["path"] for f in snapshot["files"]]
    assert not [p for p in paths if p.startswith("static/fragments/")]
    assert snapshot["routing"]["fragments"] == []


def test_route_errors_are_loud(skill_dir: Path) -> None:
    """路由表坏了必须报错：静默退化成全量注入会让两条臂的注入范围不同却看不出来。"""
    with pytest.raises(FileNotFoundError):
        runner.load_route(skill_dir / "nope.json")
    bad = skill_dir / "bad.json"
    bad.write_text(json.dumps({"values": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        runner.load_route(bad)
    broken = skill_dir / "broken.json"
    broken.write_text(json.dumps({"task_field": "scenario_id",
                                  "values": {"S1": ["static/fragments/domain/missing.md"]}}),
                      encoding="utf-8")
    route = runner.load_route(broken)
    with pytest.raises(FileNotFoundError):
        runner.load_skill(skill_dir, route, {"scenario_id": "S1"})
