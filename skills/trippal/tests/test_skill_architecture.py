"""Structural tests for the TripPal skill architecture.

These tests guard the *shape* of the product, not its policy prose:

- the router stays a router (size budget, required protocol sections);
- every path the manifest declares actually exists, and nothing is declared that is missing;
- no orphan fragments (a fragment nobody maps is dead weight);
- the corpus map and the manifest axis agree, so the router can trust both;
- every generated fragment still carries its SEED banner and its required sections;
- re-running the seed generator produces identical output (no drift between sources and skill).

Run:  pytest skills/trippal/tests -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
MANIFEST = SKILL / "manifest.yaml"
ROUTER = SKILL / "SKILL.md"
FRAGMENTS = SKILL / "static" / "fragments" / "domain"
CORPUS_MAP = SKILL / "static" / "corpus-map.json"
GENERATOR = REPO / "skill-loop" / "integration" / "pipeline" / "build_skill_seed.py"

yaml = pytest.importorskip("yaml", reason="PyYAML 未安装，跳过架构测试")

REQUIRED_FRAGMENT_SECTIONS = (
    "## 随包权威来源",
    "## 要核对的检查项",
    "## 答案里必须出现的内容",
    "## 已知失败模式",
)


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST.exists(), "manifest.yaml 缺失：路由表是这套架构的核心"
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_router_is_a_router(manifest: dict) -> None:
    """路由器必须短、必须讲清五步、且不得把 core 内容抄进来。"""
    text = ROUTER.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= 6000, f"SKILL.md 已 {len(text.encode('utf-8'))} B，路由器应 ≤6 KB"
    assert "## Routing protocol" in text
    assert "manifest.yaml" in text
    assert "always_load" in text
    # 路由器不得内联域级内容：它应当指向分片，而不是复制它们
    assert "## 已知失败模式" not in text
    assert "随包权威来源" not in text


def test_every_always_load_path_exists(manifest: dict) -> None:
    files = manifest.get("always_load") or []
    assert len(files) >= 4, "core 层至少要有 principles / tool-policy / workflow / output-and-quality"
    for rel in files:
        path = SKILL / rel
        assert path.is_file(), f"manifest.always_load 指向的文件不存在：{rel}"
        assert path.stat().st_size > 300, f"core 文档太薄：{rel}"


def test_axis_values_are_complete_and_reachable(manifest: dict) -> None:
    axes = manifest.get("axes") or {}
    assert "domain" in axes, "主轴必须是 readiness domain"
    domain = axes["domain"]
    values = domain.get("values") or {}
    assert len(values) >= 12, f"就绪域应有 12 个，实际 {len(values)}"
    assert domain.get("default") in values, "default 必须是 values 之一"
    assert domain.get("multi") is True, "一个 profile 通常跨多个域，multi 必须为 true"
    assert str(domain.get("detect", "")).strip(), "detect 提示缺失：模型无法分类"
    for slug, rel in values.items():
        path = SKILL / rel
        assert path.is_file(), f"域 {slug} 的分片不存在：{rel}"
        assert path.stat().st_size > 600, f"域 {slug} 的分片过薄：{rel}"


def test_no_orphan_fragments(manifest: dict) -> None:
    declared = {Path(p).name for p in (manifest["axes"]["domain"]["values"]).values()}
    on_disk = {p.name for p in FRAGMENTS.glob("*.md")}
    assert on_disk == declared, f"孤儿分片 {sorted(on_disk - declared)}；缺失分片 {sorted(declared - on_disk)}"


def test_references_on_demand_exist(manifest: dict) -> None:
    entries = ((manifest.get("references") or {}).get("on_demand")) or []
    assert len(entries) >= 3, "按需参考至少要有 source-policy / readiness-baseline / web-profile-contract"
    for e in entries:
        assert str(e.get("condition", "")).strip(), f"缺少触发条件：{e}"
        assert (SKILL / e["path"]).exists(), f"按需参考不存在：{e['path']}"


def test_quality_tool_is_declared_and_runnable(manifest: dict) -> None:
    tool = (manifest.get("quality_tools") or {}).get("assessment_check_script")
    assert tool, "manifest 必须声明质量闸脚本"
    path = SKILL / tool
    assert path.is_file(), f"质量闸不存在：{tool}"
    assert "__main__" in path.read_text(encoding="utf-8"), "质量闸必须可独立运行"
    # 必须显式指定 encoding：Windows 上 text=True 会按 GBK 解码，遇到中文输出直接抛 UnicodeDecodeError
    proc = subprocess.run([sys.executable, str(path), "--help"],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, f"质量闸 --help 失败：{proc.stderr[-300:]}"


def test_corpus_map_matches_axis(manifest: dict) -> None:
    corpus = json.loads(CORPUS_MAP.read_text(encoding="utf-8"))
    axis = set((manifest["axes"]["domain"]["values"]).keys())
    assert set(corpus["domains"]) == axis, "corpus-map 的域与 manifest 轴不一致"
    for slug, entry in corpus["domains"].items():
        assert entry["coverage"] in {"bundled", "external-only", "none"}, f"{slug} coverage 非法"
        if entry["coverage"] == "bundled":
            assert entry["bundled_pages"], f"{slug} 标了 bundled 却没有页面"
        for page in entry["bundled_pages"]:
            assert page["url"].startswith("http"), f"{slug} 的页面缺 URL"


def test_fragments_carry_banner_and_sections() -> None:
    for path in sorted(FRAGMENTS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert "SEED DRAFT" in text, f"{path.name} 缺少 SEED 横幅（未评审状态必须显式）"
        for section in REQUIRED_FRAGMENT_SECTIONS:
            assert section in text, f"{path.name} 缺少小节：{section}"


def test_quality_tool_catches_unsourced_claims_and_red_lines(tmp_path: Path) -> None:
    """质量闸必须真的抓得住东西：用它自己声明要抓的三类问题做回归。

    这条测试是被现实逼出来的：第一版红线规则漏掉了中文的「肯定可以」，一份"肯定可以免签入境"
    的载荷竟然通过。所以这里把三类载荷固定下来当回归样本。
    """
    tool = SKILL / "scripts" / "verify_assessment.py"

    def run(payload: dict) -> tuple[int, str]:
        path = tmp_path / "a.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(tool), "--assessment", str(path)],
                              capture_output=True, text=True, encoding="utf-8")
        return proc.returncode, proc.stdout

    good = {
        "submissionId": "s1", "source": "trippal-skill", "generatedAt": "2026-10-04T09:30:00+08:00",
        "language": "zh", "score": 68, "status": "needs_attention",
        "summary": "过境时限已按官方页核实（https://example.gov.cn/x，页面显示 2026-03-24）。",
        "risks": [{"id": "r1", "severity": "critical", "title": "t", "detail": "d",
                   "evidence": "e（https://example.com/y，页面显示 2026-09-14）"}],
        "actions": [{"id": "a1", "timing": "before_departure", "title": "t", "detail": "d"}],
        "officialChecks": [{"topic": "Entry", "result": "ok",
                            "url": "https://example.gov.cn/x", "checkedAt": "2026-03-24"}],
        "assumptions": [],
    }
    code, _ = run(good)
    assert code == 0, "一份合规载荷不应被拦下"

    unsourced = dict(good, summary="免签停留 240 小时以内都没问题。", officialChecks=[])
    code, out = run(unsourced)
    assert code == 1 and "evidence.unsourced" in out, f"未抓无来源政策断言：{out}"

    decision = dict(good, summary="你肯定可以免签入境，护照号 E12345678 已记录。")
    code, out = run(decision)
    assert code == 1, "应拦下"
    assert "redline.decision" in out, f"未抓红线措辞：{out}"
    assert "sensitive.passport" in out, f"未抓敏感数据：{out}"


def test_generated_layer_has_no_drift() -> None:
    """源材料改了但没人重跑生成器时，这条会失败——防止产品内容与调研脱节。"""
    if not GENERATOR.exists():
        pytest.skip("生成器不存在")
    proc = subprocess.run([sys.executable, str(GENERATOR), "--check"],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, f"seed 层与源材料不一致：\n{proc.stdout}\n{proc.stderr[-300:]}"
