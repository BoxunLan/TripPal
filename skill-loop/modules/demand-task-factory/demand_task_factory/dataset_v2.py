"""dataset v2 生成器 —— 规则应用 / 产物卡 / 工具决策 / 拒答 四族 + 质量门。

设计见 `docs/dataset-v2.md`。与 v1 的 `build.py` 完全独立：v1 的 16 张卡继续可复现，
本模块产出 v2（schema_version="v2"）任务包与门禁报告。

核心原则（为什么这版"有意义"）：
1. 判据的实质断言必须**从证据来**：规则族的 reason 断言用治理条款正则，
   产物族的必需项用研究结论推出的正则，拒答族要求点名"证据不足"。
2. 规则族的期望值由**规则表推导**（`decide()`），不是手写常量；每条规则都挂来源页面。
3. 生成期强制七道门（G1..G7），退化就报错，不产出脏包。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import corpus_facts

HERE = Path(__file__).resolve()
SKILL_LOOP = HERE.parents[3]          # skill-loop/（本文件在 <skill-loop>/modules/<pkg>/<pkg>/）
REPO = HERE.parents[4]                # TripPal/
WEB = REPO / "research" / "raw" / "web"
V2_TASK_SCHEMA = SKILL_LOOP / "contracts" / "v2" / "task.schema.json"

GEO = "GLOBAL"
WINDOW = "2025-09-29/2026-09-29"
PAGES_REL = "fixtures/tool_data_trippal_v2/pages.json"
PAGE_TEXT_LIMIT = 4500                 # 注入正文上限，控制 prompt 预算
PAGE_TEXT_LIMIT_FAQ = 6000             # 12345 问答页放长一点，保证逐字事实落在页面内
MIN_PAGE_CHARS = 200

# ----------------------------------------------------------------- 规则表
@dataclass(frozen=True)
class Rule:
    id: str
    slug: str        # research/raw/web/<slug>.txt
    question: str    # lookup 键（同时是 demand_evidence.query_cluster[0]）
    clause: str      # 治理条款正则：reason 断言的来源
    label: str


RULES: dict[str, Rule] = {r.id: r for r in [
    Rule("R-THIRD", "chinadaily-240h-transit-route-faq",
         "Under the 240-hour visa-free transit policy, must the onward ticket go to a third country different from where I came from?",
         r"(?i)third country|a-b-c|round trip|a-b-a", "过境必须去第三国（A-B-C）"),
    Rule("R-HK", "mcg-china-visa-free-transit-requirements",
         "Do Hong Kong and Macau count as a third country or region for China's 240-hour visa-free transit?",
         r"(?i)hong kong|macau|third (country|region)", "港澳计为第三地区"),
    # 用覆盖全部合格国籍的通用页。旧页标题和开头只谈美国护照，
    # 却被用来判定加拿大/德国旅客，单页证据无法支撑整个结论。
    Rule("R-CLOCK", "mcg-china-visa-free-transit-requirements",
         "When does the 240-hour visa-free transit clock start, and how long is it in practice?",
         r"(?i)240|10 days|midnight|00:00|clock", "240 小时起算与上限"),
    Rule("R-TICKET", "mcg-china-visa-free-transit-requirements",
         "What onward ticket evidence is required for the 240-hour visa-free transit?",
         r"(?i)onward ticket|date and seat|confirmed", "需已确认的续程机票"),
    Rule("R-AREA", "mcg-china-visa-free-transit-requirements",
         "Which areas of China can I visit under the 240-hour visa-free transit?",
         r"(?i)province|permitted area|24 ", "仅限获批省份/区域"),
    Rule("R-TIBET", "mcg-beijing-to-lhasa-travel-guide-requirements",
         "Does China's 240-hour visa-free transit allow travel to Tibet?",
         r"(?i)tibet|permit", "西藏不适用过境免签"),
    Rule("R-HARBIN", "mcg-harbin-ice-festival-three-venues-guide",
         "Which port and permitted area apply to Harbin under the 240-hour visa-free transit?",
         r"(?i)harbin|taiping|city", "哈尔滨仅限市区/单一口岸"),
    Rule("R-HAINAN", "mcg-hainan-visa-free-sanya-haikou-guide",
         "Can US passport holders visit Hainan without a visa, and what are the conditions?",
         r"(?i)hainan|30 days|onward", "海南 30 天免签"),
    Rule("R-UNILATERAL", "mcg-china-visa-free-entry-countries-2026",
         "Which countries are on China's unilateral 30-day visa-free list, and is the United States included?",
         r"(?i)unilateral|30[- ]day|not on|visa", "单方面 30 天免签名单"),
    Rule("R-VISA", "mcg-can-americans-enter-china-without-visa",
         "If I already hold a 10-year China visa, do I need the 240-hour transit instead?",
         r"(?i)visa|no need|enter on", "已有签证则直接用签证"),
    Rule("R-REG", "consulate-240h-faq-cn",
         "What registration must a foreign visitor complete after arriving in China?",
         r"(?i)regist|24 hours|police", "24 小时内住宿登记"),
    Rule("R-OVERSTAY", "mcg-can-americans-travel-china-visa-free",
         "What is the penalty for overstaying a visa-free entry in China?",
         r"(?i)overstay|fine|detention|10000", "超期停留处罚"),
    Rule("R-TRANSIT-IS-NOT-TOURIST", "mcg-china-visa-free-transit-requirements",
         "Is the 240-hour visa-free transit a tourist visa, and who qualifies?",
         r"(?i)transit|third country|not a tourist", "过境不等于旅游签"),
    Rule("R-PAY", "govt-chinadaily-alipay-binding",
         "Can a foreign visitor use Alipay or WeChat Pay with an overseas bank card in China?",
         r"(?i)foreign bank card|alipay|wechat|bind", "外卡绑定移动支付"),
    Rule("R-HOTEL", "mcg-china-hotels-foreigners-check-in-guide",
         "Can every hotel in China check in a guest who holds a foreign passport?",
         r"(?i)foreign|passport|regist|licen", "涉外住宿与登记"),
    Rule("R-TICKETS", "mcg-how-to-buy-forbidden-city-tickets",
         "How and when can I buy timed-entry tickets for popular Chinese attractions as a foreign visitor?",
         r"(?i)ticket|release|advance|passport|sold out", "热门景点预约与放票"),
    Rule("R-TRAIN", "mcg-china-high-speed-rail-guide",
         "What do I need to know about booking and boarding high-speed trains in China as a foreigner?",
         r"(?i)passport|12306|ticket|station|gate", "高铁购票与进站"),
    Rule("R-TAXI", "mcg-how-to-use-didi-china-without-chinese-number",
         "How do I use ride-hailing in China without a Chinese phone number?",
         r"(?i)didi|driver|address|phone", "网约车与地址沟通"),
    Rule("R-NET", "mcg-how-to-get-internet-in-china",
         "How do I get internet access in China as a foreign visitor?",
         r"(?i)e-?sim|sim|roaming|vpn|wi-?fi", "上网方案与可用性"),
]}

ENTRY_RULES = {
    "R-THIRD", "R-HK", "R-CLOCK", "R-TICKET", "R-AREA", "R-TIBET", "R-HARBIN",
    "R-HAINAN", "R-UNILATERAL", "R-VISA", "R-REG", "R-OVERSTAY", "R-TRANSIT-IS-NOT-TOURIST",
}
RULE_DOMAIN = {rule_id: ("entry" if rule_id in ENTRY_RULES else "trip_ops") for rule_id in RULES}

INSUFFICIENT_RX = (
    r"(?i)insufficient|does not (answer|address|cover|mention|explain)|"
    r"not (enough|addressed|covered)|cannot|unable|no (reliable )?information|contains no information"
)

TRANSIT_OK = {"United States", "United Kingdom", "Canada", "Japan", "Singapore", "Germany"}
UNILATERAL_30 = {"United Kingdom", "Canada", "Japan", "Singapore", "Germany"}   # 美国不在名单内
HAINAN_OK = {"United States", "United Kingdom", "Canada", "Japan", "Singapore", "Germany"}


# ----------------------------------------------------------------- 页面
def load_page(slug: str) -> tuple[str, str, str]:
    """返回 (正文, source_url, effective_date)。文件头是 `# source:` / `# title:`。"""
    path = WEB / f"{slug}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"规则页缺失：{path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    url, body_lines = "", []
    for line in text.splitlines():
        if line.startswith("# source:"):
            url = line.split(":", 1)[1].strip()
        elif line.startswith("# "):
            continue
        else:
            body_lines.append(line)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(body_lines)).strip()
    eff = ""
    m = re.search(
        r"(?i)(?:updated|published|dated|accessed|last reviewed)[^\n]{0,40}?"
        r"((?:19|20)\d{2}-\d{2}-\d{2}|"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},?\s+(?:19|20)\d{2}|"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(?:19|20)\d{2})",
        body,
    )
    if m:
        eff = m.group(1)
    return body, url, eff


def build_pages() -> dict:
    """v2 检索夹具：lookup 键 → 页面。正文前置 [source] 行，保证模型可引用。"""
    pages: dict[str, dict] = {}
    for rule in RULES.values():
        if rule.question in pages:
            continue
        body, url, eff = load_page(rule.slug)
        # 压平空白：A2 的逐字片段是在压平后的文本上切的，页面必须保持同一形态，
        # 否则 G8（逐字有据）会正确地把它判成"找不到"。
        flat = re.sub(r"\s+", " ", body).strip()
        pages[rule.question] = {
            "text": f"[source] {url}\n\n{flat[:PAGE_TEXT_LIMIT]}",
            "source_url": url,
            "effective_date": eff,
            "rule_id": rule.id,
        }
    # A 族素材：北京 12345 的真实提问 + 官方答复（页面自带日期）
    for faq in corpus_facts.load_bjfaq():
        if faq["question"] in pages:
            continue
        text = (
            f"[source] {faq['url']}\n\n{faq['date']}\n\n{faq['question']}\n\n{faq['answer']}"
        )[:PAGE_TEXT_LIMIT_FAQ]
        kept = [f for f in faq["facts"] if f in text]
        if not kept:
            continue          # 断言必须能在页面里找到，否则这张卡不成立
        pages[faq["question"]] = {
            "text": text,
            "source_url": faq["url"],
            "effective_date": faq["date"],
            "rule_id": "FAQ-12345",
            "facts": kept,
        }
    return pages


def _rule_texts(pages: dict) -> dict[str, str]:
    """只取规则页（不含 12345 问答页），供 D2 配对使用。

    **必须按 RULES 的插入顺序遍历**：曾经写成 `{r.question for r in ...}`（set），
    set 的迭代顺序依赖 PYTHONHASHSEED，于是 D2 的页面配对在不同进程间漂移、
    pack_sha256 随之变化 —— 数据集不可复现，而"同进程两次构建"的测试完全看不见它。
    """
    out: dict[str, str] = {}
    for rule in RULES.values():
        if rule.question in pages:
            out[rule.question] = pages[rule.question]["text"]
    return out


# ----------------------------------------------------------------- 规则判定
def decide(p: dict) -> tuple[str, str]:
    """由规则表推导期望决策；返回 (decision, rule_id)。唯一真值来源。"""
    if p.get("has_visa"):
        return "use_visa", "R-VISA"
    if p.get("hainan_only"):
        if p["passport"] in HAINAN_OK and p.get("stay_days", 0) <= 30:
            return "eligible", "R-HAINAN"
        return "not_eligible", "R-HAINAN"
    if p.get("purpose") != "transit":
        if p["passport"] in UNILATERAL_30 and p.get("stay_days", 99) <= 30:
            return "eligible", "R-UNILATERAL"
        return "not_eligible", "R-UNILATERAL"
    if p["passport"] not in TRANSIT_OK:
        return "not_eligible", "R-TRANSIT-IS-NOT-TOURIST"
    if p.get("area") == "Tibet":
        return "not_eligible", "R-TIBET"
    if p.get("area") == "Harbin province":
        return "not_eligible", "R-HARBIN"
    if p.get("route") == "A-B-A":
        return "not_eligible", "R-THIRD"
    if p.get("ticket") != "confirmed seat":
        return "not_eligible", "R-TICKET"
    # The cited rule starts the 240-hour clock at 00:00 on the day after entry.
    # An elapsed stay slightly above 240 hours can therefore still fit; more than
    # 264 elapsed hours cannot fit even with the most favorable arrival time.
    if p.get("stay_hours", 0) > 264:
        return "not_eligible", "R-CLOCK"
    if p.get("onward") in ("Hong Kong", "Macau"):
        return "eligible", "R-HK"
    return "eligible", "R-CLOCK"


B_INSTRUCTION = (
    "You are checking one traveller's China entry options. Use ONLY the provided source.\n"
    "Decide whether this traveller can enter mainland China without a visa, then output ONLY one JSON object "
    "(no code fences, no commentary) with exactly these keys:\n"
    '{"decision": "eligible" | "use_visa" | "not_eligible", "reason": "<name the governing rule in one or two '
    'sentences>", "source": "<the source URL you relied on>"}\n'
    "Use \"eligible\" only when a visa-free route applies, \"use_visa\" when they should instead travel on a visa "
    "they already hold, and \"not_eligible\" when no visa-free route applies.\n"
    "The route field defines the itinerary: A-B-C means the arrival origin A and onward destination C are different "
    "countries or regions; A-B-A means return to the arrival origin. Passport nationality does not identify the "
    "arrival origin. Hong Kong and Macau count as separate third regions when the source says so.\n"
    "The stay_hours field is elapsed time from immigration to departure. Apply any next-midnight clock rule in "
    "the source before comparing that elapsed duration with the permitted window.\n"
    "Before returning, verify that decision, reason, and source are all present, including when decision is use_visa.\n"
    "Traveller:\n"
)

# (pattern, 基础参数, 变体数, 期望 decision, 国籍池)
B_PATTERNS: list[tuple[str, dict, int, str, list[str] | None]] = [
    ("transit_valid_abc", dict(purpose="transit", route="A-B-C", onward="Japan", stay_hours=200,
                               ticket="confirmed seat", area="Beijing", has_visa=False, hainan_only=False), 10, "eligible", None),
    ("transit_via_hk", dict(purpose="transit", route="A-B-C", onward="Hong Kong", stay_hours=200,
                            ticket="confirmed seat", area="Shanghai", has_visa=False, hainan_only=False), 9, "eligible", None),
    ("transit_via_macau", dict(purpose="transit", route="A-B-C", onward="Macau", stay_hours=220,
                               ticket="confirmed seat", area="Guangzhou", has_visa=False, hainan_only=False), 6, "eligible", None),
    ("transit_round_trip", dict(purpose="transit", route="A-B-A", onward="Home country", stay_hours=200,
                                ticket="confirmed seat", area="Beijing", has_visa=False, hainan_only=False), 10, "not_eligible", None),
    ("transit_tibet", dict(purpose="transit", route="A-B-C", onward="Japan", stay_hours=200,
                           ticket="confirmed seat", area="Tibet", has_visa=False, hainan_only=False), 8, "not_eligible", None),
    ("transit_harbin_province", dict(purpose="transit", route="A-B-C", onward="Seoul", stay_hours=200,
                                     ticket="confirmed seat", area="Harbin province", has_visa=False, hainan_only=False), 7, "not_eligible", None),
    ("transit_placeholder_ticket", dict(purpose="transit", route="A-B-C", onward="Bangkok", stay_hours=200,
                                        ticket="placeholder booking", area="Chengdu", has_visa=False, hainan_only=False), 9, "not_eligible", None),
    ("transit_over_240h", dict(purpose="transit", route="A-B-C", onward="Japan", stay_hours=265,
                               ticket="confirmed seat", area="Shanghai", has_visa=False, hainan_only=False), 9, "not_eligible", None),
    ("transit_over_240h_extreme", dict(purpose="transit", route="A-B-C", onward="Tokyo", stay_hours=300,
                                       ticket="confirmed seat", area="Beijing", has_visa=False, hainan_only=False), 6, "not_eligible", None),
    ("already_holds_visa", dict(purpose="tourism", route="A-B-A", onward="Home country", stay_days=12,
                                ticket="confirmed seat", area="Beijing", has_visa=True, hainan_only=False), 20, "use_visa", None),
    ("tourism_us_no_unilateral", dict(purpose="tourism", route="A-B-A", onward="Home country", stay_days=12,
                                      ticket="confirmed seat", area="Shanghai", has_visa=False, hainan_only=False), 10, "not_eligible", ["United States"]),
    ("tourism_uk_30day", dict(purpose="tourism", route="A-B-A", onward="Home country", stay_days=20,
                              ticket="confirmed seat", area="Beijing", has_visa=False, hainan_only=False), 9, "eligible", ["United Kingdom"]),
    ("tourism_canada_30day", dict(purpose="tourism", route="A-B-A", onward="Home country", stay_days=28,
                                  ticket="confirmed seat", area="Chengdu", has_visa=False, hainan_only=False), 7, "eligible", ["Canada"]),
    ("hainan_island_only", dict(purpose="tourism", route="A-B-A", onward="Home country", stay_days=25,
                                ticket="not booked", area="Hainan", has_visa=False, hainan_only=True), 10, "eligible", None),
]

_B_CITIES = ["Beijing", "Shanghai", "Chengdu", "Guangzhou", "Xi'an", "Hangzhou", "Kunming", "Xiamen"]
_B_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_B_PORTS = ["PEK", "PVG", "CAN", "CTU", "XIY"]


def _b_variant_params(base: dict, passport: str, i: int) -> dict:
    p = dict(base)
    p["passport"] = passport
    if p.get("hainan_only"):
        # 海南 30 天免签要求从省内开放口岸入境且活动范围留在海南。
        # 旧变体会生成 area=Hainan 但 arrival=Beijing/PEK 的自相矛盾卡。
        hainan_ports = [("Haikou", "HAK"), ("Sanya", "SYX")]
        p["arrival_city"], p["arrival_port"] = hainan_ports[i % len(hainan_ports)]
    else:
        p["arrival_port"] = _B_PORTS[i % len(_B_PORTS)]
        p["arrival_city"] = _B_CITIES[i % len(_B_CITIES)]
    p["travel_month"] = _B_MONTHS[i % len(_B_MONTHS)]
    p.setdefault("stay_days", max(1, round(p.get("stay_hours", 0) / 24)))
    return p


def build_family_b(pages: dict) -> list[dict]:
    cards, seen = [], set()
    for pattern, base, count, want, pool in B_PATTERNS:
        passports = pool or sorted(TRANSIT_OK if not base.get("hainan_only") else HAINAN_OK)
        for i in range(count):
            params = _b_variant_params(base, passports[i % len(passports)], i)
            decision, rule_id = decide(params)
            if decision != want:
                raise AssertionError(f"规则表与模式意图冲突：{pattern} 期望 {want}，规则表给 {decision}（{rule_id}）")
            key = json.dumps(params, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            rule = RULES[rule_id]
            cards.append(_card(
                task_id=f"V2B-{pattern}-{i + 1:02d}",
                scenario_id=_b_scenario(rule_id),
                task_type="conditional_decision",
                split_group=f"B:{rule_id}",
                prompt=B_INSTRUCTION + json.dumps(params, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                initial_state={"tool_data": PAGES_REL, "traveller": params},
                expected={"decision": decision, "rule_id": rule_id, "reason_clause": rule.clause},
                verifier={"kind": "deterministic", "assertions": [
                    {"op": "json_path_equals", "path": "$.decision", "value": decision,
                     "note": f"规则 {rule_id}：{rule.label}"},
                    {"op": "regex", "value": rule.clause, "note": "reason 必须点名治理条款"},
                    {"op": "regex", "value": r"https?://", "note": "必须给出所依据的来源链接"},
                ]},
                query=rule.question,
                source=rule.slug,
                note=f"{pattern}：{rule.label}",
            ))
    return cards


# ----------------------------------------------------------------- F 族：到达后义务 / 票务决策
# 与 B 族的区别：B 回答"能不能入境、要不要签证"（eligibility 空间），
# F 回答"下一步该做什么"（动作空间），覆盖 B 触达不到的场景（票务 TP-S04、铁路 TP-S05），
# 也让场景分布不再全压在 TP-S01。

F_TICKET_ACTIONS = ["book_now", "wait_for_release_window", "use_staffed_counter", "use_official_channel"]
F_RAIL_ACTIONS = ["board_normally", "use_staffed_lane", "fix_name_before_travel", "arrive_earlier"]


def decide_ticket(p: dict) -> str:
    """景区票务（规则来自 R-TICKETS：放票窗口、官方网站与护照预订、可信渠道）。"""
    if not p["official_channel"]:
        return "use_official_channel"
    if p["days_before_visit"] > 7:
        return "wait_for_release_window"
    if not p["has_chinese_number"]:
        # 证据页提供的是故宫英文官网，没有声明可现场人工售票。
        return "use_official_channel"
    return "book_now"


def decide_rail(p: dict) -> str:
    """铁路（规则来自 R-TRAIN：姓名须与护照逐字一致、实体护照过闸、车站辨析、提前到场留缓冲）。"""
    if not p["name_matches_passport"]:
        return "fix_name_before_travel"
    if not p["gate_accepts_passport"]:
        return "use_staffed_lane"
    if p["buffer_minutes"] < 45:
        return "arrive_earlier"
    return "board_normally"


# (模式名, 参数, 张数, 意图, 场景, 说明)
F_PATTERNS: list[tuple[str, dict, int, str, str, str]] = [
    ("ticket_official_ready", dict(official_channel=True, days_before_visit=3, has_chinese_number=True),
     3, "book_now", "TP-S04", "官方渠道 + 有中国手机号 + 已在放票窗口内"),
    ("ticket_third_party", dict(official_channel=False, days_before_visit=3, has_chinese_number=True),
     3, "use_official_channel", "TP-S04", "用第三方平台代订（未获授权）"),
    ("ticket_too_early", dict(official_channel=True, days_before_visit=20, has_chinese_number=True),
     3, "wait_for_release_window", "TP-S04", "离出行还有 20 天，放票窗口未开"),
    ("ticket_no_cn_number", dict(official_channel=True, days_before_visit=2, has_chinese_number=False),
     3, "use_official_channel", "TP-S04", "官方小程序要中国手机号，改用官方英文网站"),
    ("rail_name_mismatch", dict(name_matches_passport=False, gate_accepts_passport=True, buffer_minutes=90),
     3, "fix_name_before_travel", "TP-S05", "订票姓名与护照不一致"),
    ("rail_gate_rejects", dict(name_matches_passport=True, gate_accepts_passport=False, buffer_minutes=60),
     3, "use_staffed_lane", "TP-S05", "闸机读不了护照，需走人工通道"),
    ("rail_tight_buffer", dict(name_matches_passport=True, gate_accepts_passport=True, buffer_minutes=25),
     3, "arrive_earlier", "TP-S05", "只留 25 分钟缓冲"),
    ("rail_all_good", dict(name_matches_passport=True, gate_accepts_passport=True, buffer_minutes=60),
     3, "board_normally", "TP-S05", "姓名一致、闸机可用、缓冲充足"),
]

_F_INSTRUCTION = (
    "You are a China travel readiness assistant. Decide the single next action for the traveller, "
    "using the source page(s) given as tool_data.\n"
    "Answer with JSON only: {\"action\": <one of the allowed actions>, \"why\": \"<cite the governing "
    "clause in the source>\", \"source\": \"<url you relied on>\"}\n"
)
_F_ALLOWED = {"ticket": F_TICKET_ACTIONS, "rail": F_RAIL_ACTIONS}
_F_DECIDE = {"ticket": decide_ticket, "rail": decide_rail}
_F_RULE = {"ticket": "R-TICKETS", "rail": "R-TRAIN"}
# 变体必须给出**真实差异**：只改一个隐藏字段会让 prompt 重复（G2/G3 会正确地拦下来）。
_F_TICKET_CTX = [
    {"attraction": "the Forbidden City", "visit_date": "2026-04-05"},
    {"attraction": "the Forbidden City", "visit_date": "2026-05-12"},
    {"attraction": "the Forbidden City", "visit_date": "2026-09-23"},
]
_F_RAIL_CTX = [
    {"from_station": "Beijing South", "to_station": "Shanghai Hongqiao", "train_no": "G11", "depart_time": "08:05"},
    {"from_station": "Shanghai Hongqiao", "to_station": "Hangzhou East", "train_no": "G7331", "depart_time": "13:40"},
    {"from_station": "Guangzhou South", "to_station": "Shenzhen North", "train_no": "G6001", "depart_time": "17:20"},
]


def build_family_f(pages: dict) -> list[dict]:
    cards = []
    for pattern, base, count, want, scenario, label in F_PATTERNS:
        sub = "ticket" if pattern.startswith("ticket") else "rail"
        rule = RULES[_F_RULE[sub]]
        for i in range(count):
            params = dict(base)
            params.update((_F_TICKET_CTX if sub == "ticket" else _F_RAIL_CTX)[i % 3])
            params["traveller"] = f"traveller-{i + 1:02d}"
            got = _F_DECIDE[sub](params)
            if got != want:
                raise AssertionError(f"F 规则表与模式意图冲突：{pattern} 期望 {want}，规则表给 {got}")
            prompt = (
                _F_INSTRUCTION
                + f"Allowed actions: {', '.join(_F_ALLOWED[sub])}\n"
                + f"Situation: {label}\n"
                + json.dumps({k: v for k, v in params.items() if k != "traveller"},
                             ensure_ascii=False, indent=1, sort_keys=True)
                + "\n"
            )
            cards.append(_card(
                task_id=f"V2F-{pattern}-{i + 1:02d}",
                scenario_id=scenario,
                task_type="conditional_decision",
                split_group=f"F:{_F_RULE[sub]}",
                prompt=prompt,
                initial_state={"tool_data": PAGES_REL, "traveller": params},
                expected={"action": want, "rule_id": _F_RULE[sub], "reason_clause": rule.clause},
                verifier={"kind": "deterministic", "assertions": [
                    {"op": "json_path_equals", "path": "$.action", "value": want,
                     "note": f"{label}（规则 {_F_RULE[sub]}）"},
                    {"op": "regex", "value": rule.clause, "note": "why 必须点名治理条款"},
                    {"op": "regex", "value": r"https?://", "note": "必须给出所依据的来源链接"},
                ]},
                query=rule.question,
                source=rule.slug,
                note=f"F：{label}",
            ))
    return cards


def _b_scenario(rule_id: str) -> str:
    return {
        "R-THIRD": "TP-S01", "R-HK": "TP-S01", "R-CLOCK": "TP-S01", "R-TICKET": "TP-S01",
        "R-AREA": "TP-S01", "R-TIBET": "TP-S01", "R-HARBIN": "TP-S01", "R-HAINAN": "TP-S01",
        "R-UNILATERAL": "TP-S01", "R-VISA": "TP-S01", "R-TRANSIT-IS-NOT-TOURIST": "TP-S01",
        "R-REG": "TP-S07", "R-OVERSTAY": "TP-S11", "R-PAY": "TP-S03", "R-HOTEL": "TP-S07",
    }.get(rule_id, "TP-S01")


# ----------------------------------------------------------------- C 产物卡
C_TEMPLATES: list[dict] = [
    dict(name="predeparture", scenario="TP-S02",
         task="a pre-departure readiness checklist covering connectivity; offline maps and copies; VPN or essential "
              "apps installed before departure; Alipay or WeChat Pay; a backup bank card and RMB cash; the physical "
              "passport; and a written Chinese address card",
         required=[
             (r"(?i)e-?sim|roaming|local sim", "联网方案（eSIM/漫游/本地卡）"),
             (r"(?i)offline[^.\n]{0,30}(map|direction|copy)|download[^.\n]{0,30}map", "离线地图/离线副本"),
             (r"(?i)vpn|install[^.\n]{0,40}before", "行前装好工具（VPN/必要 App）"),
             (r"(?i)alipay|wechat pay", "移动支付开通"),
             (r"(?i)(second|backup|spare)[^.\n]{0,20}card", "备用银行卡"),
             (r"(?i)cash|rmb|yuan", "现金兜底"),
             (r"(?i)original passport|physical passport", "随身携带实体护照"),
             (r"(?i)chinese (address|name)|address in chinese", "中文地址/名称卡"),
         ]),
    dict(name="payment", scenario="TP-S03",
         task="a payment readiness plan covering wallet setup; decline fallbacks using a backup bank card and RMB "
              "cash; fees and limits; offline or low-battery failure; and refund handling",
         required=[
             (r"(?i)alipay|wechat pay", "钱包"),
             (r"(?i)(second|backup|spare)[^.\n]{0,20}card", "备用卡"),
             (r"(?i)cash|rmb|yuan", "现金兜底"),
             (r"(?i)\b3\s?%|fee|limit", "手续费/限额"),
             (r"(?i)offline|no (data|signal)|battery", "离线/断电场景"),
             (r"(?i)refund", "退款规则"),
         ]),
    dict(name="stay", scenario="TP-S07",
         task="an accommodation and registration plan covering foreigner-friendly booking, written confirmation, "
              "registration within 24 hours or at the police station, and the Chinese address",
         required=[
             (r"(?i)foreign(er)?[^.\n]{0,20}(friendly|guest)|accept foreign", "涉外可接待确认"),
             (r"(?i)(written|email|confirmed)[^.\n]{0,30}(confirm|reply)", "书面确认"),
             (r"(?i)regist", "住宿登记"),
             (r"(?i)24 ?hour|police station", "24 小时/派出所"),
             (r"(?i)chinese address|address in chinese", "中文地址"),
         ]),
    dict(name="navigation", scenario="TP-S09",
         task="a navigation plan covering a working local map app, offline fallback, Chinese name and address, a "
              "nearby landmark, and a driver card or screenshot",
         required=[
             (r"(?i)amap|baidu|local map", "本地地图"),
             (r"(?i)offline", "离线可用"),
             (r"(?i)chinese (name|address)", "中文名称/地址"),
             (r"(?i)landmark|nearby", "地标参照"),
             (r"(?i)screenshot|card|show", "可出示的卡片/截图"),
         ]),
    dict(name="food", scenario="TP-S10",
         task="a dining plan covering QR ordering, a Chinese menu card, dietary restrictions and price units",
         required=[
             (r"(?i)qr|mini-?program|scan", "扫码点单"),
             (r"(?i)chinese (menu|card|phrase)", "中文点单卡"),
             (r"(?i)allerg|vegetarian|spicy", "过敏/素食/辣度"),
             (r"(?i)500 ?g|per jin|\bjin\b|weight|price", "计价单位/价格"),
         ]),
    dict(name="emergency", scenario="TP-S11",
         task="an emergency plan covering medical prepayment, insurance, a police report path, embassy or consulate "
              "contact, and overstay handling",
         required=[
             (r"(?i)(pay|payment)[^.\n]{0,25}(before|upfront|deposit)|prepay", "先付费/押金"),
             (r"(?i)insurance", "保险"),
             (r"(?i)police report|report to the police", "报警回执"),
             (r"(?i)overstay|stay permit|extension", "超期/停留许可"),
             (r"(?i)embassy|consulate", "使领馆"),
         ]),
    dict(name="ticketing", scenario="TP-S04",
         task="a timed-attraction booking plan covering the release window, real-name booking with a passport, "
              "the physical passport at the gate, official alternatives when tickets sell out, and third-party risk",
         required=[
             (r"(?i)release|go(es)? on sale|advance|days? (ahead|before)", "放票时点"),
             (r"(?i)passport|real-?name", "护照实名"),
             (r"(?i)sold out|sell(s)? out|unavailable", "售罄情形"),
             (r"(?i)physical passport|original passport|passport[^.\n]{0,30}(gate|entry)", "实体护照入场"),
             (r"(?i)scalper|tout|third-?party|reseller", "黄牛/第三方风险"),
         ]),
    dict(name="train", scenario="TP-S05",
         task="an intercity rail plan covering the booking channel, exact-name matching, boarding with the physical "
              "passport, station ambiguity and an early-arrival buffer",
         required=[
             (r"(?i)12306|trip\.com|booking (site|channel|app)", "购票渠道"),
             (r"(?i)exact(ly)? (the )?(name|spelling)|as (printed )?on (the )?passport|name match|"
              r"passport[^.\n]{0,35}(name|details)|name[^.\n]{0,35}passport", "姓名逐字一致"),
             (r"(?i)physical passport|original passport|e-?gate", "实体护照/闸机"),
             (r"(?i)station|terminal|which station", "车站辨析"),
             (r"(?i)arriv(?:e|ing)[^.\n]{0,30}(early|ahead)|buffer|minutes (early|before)|allow extra time",
              "提前到场缓冲"),
         ]),
    dict(name="citytransport", scenario="TP-S06",
         task="a city transport plan covering ride-hailing setup, a Chinese address card, metro payment and "
              "avoiding unlicensed taxis",
         required=[
             (r"(?i)didi|ride-?hailing|taxi app", "网约车"),
             (r"(?i)chinese (address|name)|address in chinese", "中文地址"),
             (r"(?i)metro|subway|QR|transit card", "地铁/公共交通支付"),
             (r"(?i)unlicensed|black taxi|meter|refuse", "黑车/拒载"),
             (r"(?i)pick-?up|meeting point|landmark", "上车点/地标"),
         ]),
    dict(name="connectivity", scenario="TP-S08",
         task="a connectivity plan covering the data option chosen before departure, which services are unreachable, "
              "offline fallbacks and whether a +86 number is needed",
         required=[
             (r"(?i)e-?sim|roaming|local sim", "连接方案"),
             (r"(?i)before (you )?(arrive|depart|fly)|install[^.\n]{0,30}before|pre-?install", "行前安装"),
             (r"(?i)block(ed)?|firewall|cannot access|unavailable", "哪些服务不可用"),
             (r"(?i)offline|download|cached", "离线兜底"),
             (r"(?i)\+?86|chinese (phone )?number|sms", "+86 号码需求"),
         ]),
]

_C_PROFILES = [
    ("United States", "first-time visitor, 7 days, Beijing and Shanghai, mid-range hotels"),
    ("United Kingdom", "first-time visitor, 12 days, Chengdu and Xi'an, budget guesthouses"),
    ("Canada", "returning visitor, 5 days, Shanghai only, international chain hotel"),
    ("Australia", "first-time visitor, 18 days, multi-city by high-speed rail"),
    ("Germany", "business traveller, 4 days, Guangzhou, tight schedule"),
    ("Singapore", "family with two children, 10 days, Beijing and Hangzhou"),
    ("Japan", "solo traveller, 14 days, second-tier cities"),
    ("United States", "solo traveller on a 240-hour transit entry, 8 days, Shanghai and Hangzhou"),
]

_C_PAGE_RULE = {
    "predeparture": "R-NET",
    "payment": "R-PAY",
    "stay": "R-HOTEL",
    "navigation": "R-TAXI",
    "food": "R-PAY",
    "emergency": "R-OVERSTAY",
    "ticketing": "R-TICKETS",
    "train": "R-TRAIN",
    "citytransport": "R-TAXI",
    "connectivity": "R-NET",
}

_C_DIMS = {
    "predeparture": ["information", "tooling", "payment", "identity_verification"],
    "payment": ["payment", "tooling"],
    "stay": ["identity_verification", "information"],
    "navigation": ["tooling", "language"],
    "food": ["language", "payment"],
    "emergency": ["information", "payment"],
    "ticketing": ["identity_verification", "information"],
    "train": ["identity_verification", "tooling"],
    "citytransport": ["tooling", "language", "payment"],
    "connectivity": ["tooling", "information"],
}


def build_family_c(pages: dict) -> list[dict]:
    cards = []
    for tmpl in C_TEMPLATES:
        rule = RULES[_C_PAGE_RULE[tmpl["name"]]]
        for i, (nat, profile) in enumerate(_C_PROFILES):
            prompt = (
                f"Produce {tmpl['task']}.\n"
                f"Traveller: {nat} passport, {profile}.\n"
                "Write a 300-500 word practical checklist in English. Cover every listed requirement once with a "
                "specific action. Skip introductions, readiness scores, repeated advice, and unrelated visa or entry "
                "analysis."
            )
            assertions = [{"op": "regex", "value": rx, "note": label} for rx, label in tmpl["required"]]
            assertions.append({"op": "regex", "value": r"(?s).{400,}", "note": "清单需有实质篇幅"})
            cards.append(_card(
                task_id=f"V2C-{tmpl['name']}-{i + 1:02d}",
                scenario_id=tmpl["scenario"],
                task_type="artifact_card",
                split_group=f"C:{tmpl['name']}",
                prompt=prompt,
                initial_state={"tool_data": PAGES_REL, "profile": {"nationality": nat, "summary": profile}},
                expected={"required_items": [label for _, label in tmpl["required"]]},
                verifier={"kind": "deterministic", "assertions": assertions},
                query=rule.question,
                source=rule.slug,
                note=f"{tmpl['name']} 产物卡",
                artifact={"dimensions": _C_DIMS[tmpl["name"]], "format": "checklist"},
            ))
    return cards


# ----------------------------------------------------------------- E 工具决策
E_CASES: list[tuple[str, bool, str]] = [
    ("Does a data-only eSIM give me a Chinese phone number?", True, r"(?i)e-?sim|sim|number"),
    ("How many days before departure do Forbidden City tickets go on sale?", True, r"(?i)forbidden|ticket|day"),
    ("Can I enter China visa-free for 10 days if I fly on to Thailand?", True, r"(?i)visa|transit|thailand|third"),
    ("Do small Chinese hotels accept foreign passports?", True, r"(?i)hotel|foreign|passport"),
    ("What should I do if my Alipay payment is declined in a shop?", True, r"(?i)alipay|pay|declin"),
    ("Where do I register my stay if I am sleeping at a friend's flat?", True, r"(?i)regist|police|stay"),
    ("Which app works for maps in mainland China?", True, r"(?i)map|app|amap|baidu"),
    ("What is the fine for overstaying a visa-free entry?", True, r"(?i)overstay|fine|penalt"),
    ("Is Google Maps accurate inside China?", True, r"(?i)google map|map|accurate"),
    ("Do I need to carry my passport on travel days?", True, r"(?i)passport|travel day|carry"),
    ("What is the weather in Beijing next Tuesday?", False, ""),
    ("Which restaurant has the best reviews near my hotel?", False, ""),
    ("How do I say thank you in Mandarin?", False, ""),
    ("What is the capital of Australia?", False, ""),
    ("Can you recommend a good science fiction novel?", False, ""),
    ("What time is it in Shanghai right now?", False, ""),
    ("How much does a taxi cost from the airport in general?", False, ""),
    ("What is the best camera to buy for travel?", False, ""),
    ("Who won the football match last night?", False, ""),
    ("Should I book a hotel or an apartment?", False, ""),
]


def build_family_e(pages: dict) -> list[dict]:
    cards = []
    for i, (question, use_tool, qrx) in enumerate(E_CASES, start=1):
        prompt = (
            "You are deciding whether answering the user's request requires the local TripPal source pages.\n"
            f'User request: "{question}"\n'
            "You have exactly one tool: lookup_local(query), which searches the bundled China travel source pages.\n"
            "Output ONLY one JSON object (no code fences) with exactly these keys:\n"
            '{"use_tool": true | false, "query": "<the search query you would run, or an empty string>"}\n'
            "Use true for a specific China travel rule or operational fact that the bundled pages may cover, including "
            "immigration penalties, overstay handling, map availability or accuracy, connectivity, payments, booking, "
            "accommodation and transport. Use false only for live information, personal recommendations, generic "
            "knowledge, or subjects unrelated to the bundled China travel sources."
        )
        assertions = [{"op": "json_path_equals", "path": "$.use_tool", "value": use_tool}]
        if use_tool:
            assertions.append({"op": "regex", "value": qrx, "note": "query 必须落在主题词上"})
        else:
            assertions.append({"op": "regex", "value": r'"query"\s*:\s*(""|null)',
                               "note": "不用工具时 query 必须为空"})
        cards.append(_card(
            task_id=f"V2E-tool-{i:02d}",
            scenario_id="TP-S12",
            task_type="conditional_decision",
            split_group=f"E:{'use' if use_tool else 'no-use'}",
            prompt=prompt,
            initial_state={"tool_data": PAGES_REL, "request": question},
            expected={"use_tool": use_tool},
            verifier={"kind": "deterministic", "assertions": assertions},
            query=RULES["R-CLOCK"].question,
            source=RULES["R-CLOCK"].slug,
            note="工具决策" + ("（该用）" if use_tool else "（不该用）"),
        ))
    return cards


# ----------------------------------------------------------------- A 有据事实答
A_JSON_SHAPE = (
    "Output ONLY one JSON object (no code fences, no commentary) with exactly these keys:\n"
    '{"answered": true | false, "answer": "<the conclusion and key supporting sentence>", '
    '"source": "<the source URL>"}\n'
    "Write the answer in the same language as the question. Quote support when the source uses that language; "
    "otherwise translate the support. "
    "An official navigation path, URL, contact, or explicit catch-all applicant category counts as an answer. "
    "If the provided source does not actually answer the question, set answered to false and explain why."
)

A_DATED_JSON_SHAPE = (
    "Output ONLY one JSON object (no code fences, no commentary) with exactly these keys:\n"
    '{"answered": true | false, "answer": "<the conclusion and all distinct supporting instructions quoted '
    'verbatim>", "source": "<the source URL>", "date": "<the date shown on the page>"}\n'
    "An official navigation path, URL, contact, or explicit catch-all applicant category counts as an answer. "
    "When the page answers the question, set answered to true and preserve every material option, time limit, "
    "contact detail, address, or exception needed to act on it. If the page does not answer the question, set "
    "answered to false and explain why."
)

_A_SCENARIO_HINTS = [
    (r"(?i)ticket|attraction|park|museum|forbidden|reservation", "TP-S04"),
    (r"(?i)hotel|accommodation|regist", "TP-S07"),
    (r"(?i)visa|entry|transit|passport", "TP-S01"),
    (r"(?i)medical|hospital|health|examination|insurance", "TP-S11"),
    (r"(?i)payment|wechat|alipay|card|cash|bank", "TP-S03"),
    (r"(?i)driver|taxi|metro|train|bus|transport", "TP-S06"),
    (r"(?i)phone|sim|internet|wechat app|number", "TP-S08"),
]


def _a_scenario(text: str) -> str:
    for pattern, scenario in _A_SCENARIO_HINTS:
        if re.search(pattern, text):
            return scenario
    return "TP-S12"


def build_family_a(pages: dict, faq_cards: int = 2, se_cards: int = 0) -> list[dict]:
    """A 族：断言的实质部分是**页面正文的逐字片段**，复述问题无法通过。

    A2（SE 问题 × 按相似度配规则页）已停用：实测那批配对抽出的"关键短语"竟是目录行
    （如 "The 5 ways to get online in China, ranked"），而模型 14 张里 13 张答 `answered:false`
    —— 它判断"该来源没回答这个问题"是**对的**，属于任务缺陷而非模型缺陷。
    官方 12345 问答（A1）的问↔答配对由官方本身保证，是可靠来源。
    """
    cards: list[dict] = []

    # A1：12345 真实提问 + 官方答复（自带日期 → 多一条日期断言）
    # 同一份问答里最多取 3 条不同的逐字事实，各成一张卡 —— 素材质量最高，值得吃满。
    faqs = corpus_facts.load_bjfaq()
    seq = 0
    seen: set[tuple[str, str]] = set()          # (问题, 关键短语) 全局去重：两页同问同答只留一张
    for faq in faqs:
        for fact, key in list(zip(faq["facts"], faq["keys"]))[:faq_cards]:
            if (faq["question"], key) in seen:
                continue
            seen.add((faq["question"], key))
            seq += 1
            assertions = [
                {"op": "json_path_equals", "path": "$.answered", "value": True, "note": "该来源确实回答了问题"},
                {"op": "contains", "value": key,
                 "note": "必须给出官方答复里的关键短语（逐字来自页面；整句落在 expected 里供人工复核）"},
                {"op": "regex", "value": r"https?://", "note": "必须给出所依据的来源链接"},
            ]
            if faq["date"]:
                assertions.append({"op": "contains", "value": faq["date"], "note": "必须写出页面上的日期"})
            cards.append(_card(
                task_id=f"V2A-faq-{seq:02d}",
                scenario_id=_a_scenario(faq["question"]),
                task_type="fact_lookup",
                split_group=f"A:faq:{faq['slug']}",
                prompt=(
                    "Answer the traveller's question using ONLY the provided source page.\n"
                    f'Question: "{faq["question"]}"\n'
                    + (A_DATED_JSON_SHAPE if faq["date"] else A_JSON_SHAPE)
                ),
                initial_state={"tool_data": PAGES_REL, "asked_question": faq["question"]},
                expected={"answered": True, "key": key, "fact_full": fact, "url": faq["url"], "date": faq["date"]},
                verifier={"kind": "deterministic", "assertions": assertions},
                query=faq["question"],
                source=faq["slug"],
                note="12345 官方问答（真实提问）",
            ))

    # A2（SE 真实问题 × 高相关规则页）已停用：见函数 docstring 的实测理由
    # （抽出的"关键短语"是目录行，且模型 14 张里 13 张正确地指出"该来源没回答这个问题"）。
    return cards


# ----------------------------------------------------------------- D 拒答/反幻觉
def build_family_d2(pages: dict, limit: int = 20) -> list[dict]:
    """D2：**真实** SE 问题 × 无关规则页（比规则问题更像真实失败场景）。"""
    se_rows = corpus_facts.load_se_questions(limit=400)
    rule_texts = _rule_texts(pages)
    cards = []
    for i, pick in enumerate(corpus_facts.pick_se_unrelated(se_rows, rule_texts, limit), start=1):
        cards.append(_card(
            task_id=f"V2D2-abstain-{i:02d}",
            scenario_id="TP-S12",
            task_type="fact_lookup",
            split_group=f"D2:{pick['page_key'][:60]}",
            prompt=(
                "Answer the traveller's question using ONLY the provided source page.\n"
                f'Question: "{pick["question"]}"\n'
                f"Provided source page: {pick['page_key']}\n" + A_JSON_SHAPE
            ),
            initial_state={"tool_data": PAGES_REL, "asked_question": pick["question"]},
            expected={"answered": False, "page_key": pick["page_key"]},
            verifier={"kind": "deterministic", "assertions": [
                {"op": "json_path_equals", "path": "$.answered", "value": False,
                 "note": "所给来源答不了该问题，必须如实标注"},
                {"op": "regex",
                 "value": INSUFFICIENT_RX,
                 "note": "必须显式说明证据不足"},
                {"op": "regex", "value": r"https?://", "note": "必须点名所给来源"},
            ]},
            query=pick["page_key"],
            source=pick["page_key"],
            note="拒答（真实问题 × 无关来源）",
        ))
    return cards


def _rare_overlap(question: str, page_text: str) -> float:
    return corpus_facts.rare_overlap(question, page_text)


def build_family_d(pages: dict, limit: int = 20) -> list[dict]:
    """给"答不了题"的来源，奖励诚实拒答。

    注入的页面由 `p_rule.question` 决定（该键映射到无关页面），而题面问的是 `q_rule.question`
    —— 这正是真实场景：检索拿回一页，agent 要判断它到底答不答得了用户的问题。
    """
    rules = list(RULES.values())
    candidates = []
    for q_rule, p_rule in itertools.product(rules, repeat=2):
        if q_rule.id == p_rule.id:
            continue
        # 稀有词重叠对跨语言页不可靠：中文领保页与英文问题可得 0 分，
        # 但页面其实直接回答了过境免签问题。拒答卡只在不同业务域之间配页。
        if RULE_DOMAIN[q_rule.id] == RULE_DOMAIN[p_rule.id]:
            continue
        ov = _rare_overlap(q_rule.question, pages[p_rule.question]["text"])
        candidates.append((ov, q_rule, p_rule))
    candidates.sort(key=lambda c: (c[0], c[1].id, c[2].id))
    picked: list[dict] = []
    used: set[tuple[str, str]] = set()
    source_counts: Counter[str] = Counter()
    question_counts: Counter[str] = Counter()
    for ov, q_rule, p_rule in candidates:
        key = (q_rule.id, p_rule.slug)
        if (key in used or source_counts[p_rule.slug] >= 2 or question_counts[q_rule.id] >= 2
                or len(picked) >= limit):
            continue
        used.add(key)
        source_counts[p_rule.slug] += 1
        question_counts[q_rule.id] += 1
        picked.append(_card(
            task_id=f"V2D-abstain-{len(picked) + 1:02d}",
            scenario_id="TP-S12",
            task_type="fact_lookup",
            split_group=f"D:{p_rule.slug}",
            prompt=(
                "Answer the traveller's question using ONLY the provided source page.\n"
                f'Question: "{q_rule.question}"\n'
                f"Provided source page: {p_rule.slug} — {pages[p_rule.question]['source_url']}\n"
                "If the provided source does not actually answer the question, say so explicitly instead of guessing. "
                "Write the answer in English, translating the source when needed. "
                "Output one JSON object (no code fences) with exactly these keys:\n"
                '{"answered": true | false, "answer": "<what the source supports, or why it is insufficient>", '
                '"source": "<the source URL>"}'
            ),
            initial_state={"tool_data": PAGES_REL, "asked_question": q_rule.question},
            expected={"answered": False, "why": f"{p_rule.slug} 不回答该问题（rare-overlap={ov:.2f}）"},
            verifier={"kind": "deterministic", "assertions": [
                {"op": "json_path_equals", "path": "$.answered", "value": False,
                 "note": "所给来源答不了该问题，必须如实标注"},
                {"op": "regex",
                 "value": INSUFFICIENT_RX,
                 "note": "必须显式说明证据不足"},
                {"op": "regex", "value": r"https?://", "note": "必须点名所给来源"},
                {"op": "not_contains",
                 "value": ["applies to your trip", "you qualify for the 240-hour",
                           "you are eligible for the 240-hour"],
                 "note": "不得凭空套用另一条规则下结论（注意：不能禁关键词，被引页面的 URL 里就含 240-hour）"},
            ]},
            query=p_rule.question,
            source=p_rule.slug,
            note=f"拒答：问「{q_rule.label}」但只给 {p_rule.slug}",
        ))
    return picked


# ----------------------------------------------------------------- 标准答案（oracle）
# 目的：证明"题目可解"。按 expected_end_state 机械构造一份**完美答案**，
# 它必须通过这张卡的全部断言；过不了就说明卡本身写坏了（判据自相矛盾/不可满足）。
# 这是"评测有意义"最硬的一条自检 —— 比"小模型答错"更能说明问题在模型而不在题。

ABSTAIN_SENTENCE = (
    "The provided source does not answer this question, so the information is insufficient; "
    "please consult the official page for this topic."
)


def _eval_assertions(assertions: list[dict], text: str, parsed) -> list[bool]:
    """与 B 线 verifier 同语义的最小实现（跨模块不 import，测试里再与真 verifier 对拍）。"""
    import re as _re

    def get(path: str):
        if not path.startswith("$"):
            return None
        cur = parsed
        for token in _re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[['\"]([^'\"]+)['\"]\]", path[1:]):
            key = token[0] or token[1]
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return None
        return cur

    out = []
    for a in assertions:
        op, value = a.get("op"), a.get("value")
        if op == "contains":
            out.append(str(value) in text)
        elif op == "regex":
            out.append(_re.search(str(value), text) is not None)
        elif op == "not_contains":
            banned = value if isinstance(value, list) else [value]
            out.append(not any(str(b) in text for b in banned))
        elif op == "any_of":
            options = value if isinstance(value, list) else [value]
            out.append(any(str(o) in text for o in options))
        elif op == "json_path_equals":
            out.append(get(a.get("path", "")) == value)
        else:
            out.append(False)
    return out


def _corpus_snippet(pattern: str, pages: dict, limit: int = 80) -> str | None:
    """在语料里找一个能匹配该正则的真实片段 —— 既满足断言，又证明断言是可满足的。"""
    compiled = re.compile(pattern)
    for page in pages.values():
        for m in compiled.finditer(page["text"]):
            s = m.group(0).strip()
            if 2 <= len(s) <= limit:
                return s
    return None


_C_LIST_FILLER = (
    " Work through the remaining steps in order and tick each one off. Keep a screenshot of every confirmation, "
    "store the addresses and ticket references where they work without a data connection, and re-check the whole "
    "plan the day before departure so that nothing depends on a single device, a single card or a single app. "
    "If any item cannot be confirmed in writing, treat it as not ready and prepare a fallback that does not rely "
    "on the same channel, then write down the Chinese wording you will show to staff at the counter."
)

# 每个"必需项"标签对应一句能通过它正则的短语 —— C 族的断言要求模型**产出**这些内容，
# 不能靠从语料里摘抄，所以 oracle 用这张表来证明判据可满足。
C_ITEM_PHRASES: dict[str, str] = {
    "联网方案（eSIM/漫游/本地卡）": "Choose an eSIM, roaming or a local SIM before you fly",
    "离线地图/离线副本": "Save an offline map and offline copies of your bookings",
    "行前装好工具（VPN/必要 App）": "Install the VPN and the essential apps before departure",
    "移动支付开通": "Set up Alipay and WeChat Pay",
    "备用银行卡": "Carry a second backup card",
    "现金兜底": "Keep some cash in RMB as a fallback",
    "随身携带实体护照": "Carry your original passport on travel days",
    "中文地址/名称卡": "Write the Chinese address and name on a card",
    "钱包": "Set up a wallet such as Alipay",
    "备用卡": "Add a backup card",
    "手续费/限额": "Know the 3% fee and the per-transaction limit",
    "离线/断电场景": "Plan for offline use and a low battery",
    "退款规则": "Check the refund rules before you pay",
    "涉外可接待确认": "Confirm the hotel accepts foreign guests",
    "书面确认": "Get written confirmation by email",
    "住宿登记": "Complete the accommodation registration",
    "24 小时/派出所": "Register within 24 hours at the police station",
    "中文地址": "Keep the Chinese address handy",
    "本地地图": "Install a local map app such as Amap or Baidu Maps",
    "离线可用": "Make sure the map works offline",
    "中文名称/地址": "Save the Chinese name and address",
    "地标参照": "Note a nearby landmark for the driver",
    "可出示的卡片/截图": "Show the card or a screenshot to the driver",
    "扫码点单": "Expect QR code scanning and mini-program ordering",
    "中文点单卡": "Carry a Chinese menu card",
    "过敏/素食/辣度": "Flag allergies, vegetarian needs and the spicy level",
    "计价单位/价格": "Check the price and the 500 g unit",
    "先付费/押金": "Expect to pay before treatment and a deposit",
    "保险": "Check whether your insurance pays directly",
    "报警回执": "File a police report and keep the receipt",
    "超期/停留许可": "Apply for a stay permit before you overstay",
    "使领馆": "Contact your embassy or consulate",
    "放票时点": "Note the release window days in advance",
    "护照实名": "Book with your passport for real-name entry",
    "售罄情形": "Plan for tickets being sold out",
    "实体护照入场": "Carry the original physical passport for entry at the gate",
    "黄牛/第三方风险": "Avoid scalpers and unauthorized third parties",
    "购票渠道": "Book on 12306 or a trusted booking app",
    "姓名逐字一致": "Use exactly the name printed on your passport",
    "实体护照/闸机": "Use the physical passport at the e-gate",
    "车站辨析": "Check which station your train leaves from",
    "提前到场缓冲": "Arrive early with a buffer before departure",
    "网约车": "Use Didi for ride-hailing",
    "地铁/公共交通支付": "Pay for the metro with a QR code or a transit card",
    "黑车/拒载": "Refuse unlicensed taxis that quote a flat rate",
    "上车点/地标": "Agree on a pick-up point or landmark",
    "连接方案": "Choose an eSIM, roaming or a local SIM",
    "行前安装": "Install it before you arrive",
    "哪些服务不可用": "Some services are blocked or unavailable",
    "离线兜底": "Download what you need for offline use",
    "+86 号码需求": "Decide whether you need a +86 number for SMS",
}


def oracle_answer(card: dict, pages: dict) -> tuple[str, object]:
    """构造一张卡的"完美答案"并返回 (text, parsed_json)。"""
    page = pages.get(card["demand_evidence"]["query_cluster"][0], {})
    url = page.get("source_url", "https://example.invalid/source")
    exp = card["expected_end_state"]

    if card["task_type"] == "artifact_card":
        lines = []
        for a in card["verifier"]["assertions"]:
            if a["op"] != "regex":
                continue
            phrase = C_ITEM_PHRASES.get(a.get("note", ""))
            if not phrase:
                phrase = _corpus_snippet(a["value"], pages) or "item"
            lines.append(f"- {phrase}.")
        return "Readiness checklist:\n" + "\n".join(lines) + _C_LIST_FILLER, None

    if "use_tool" in exp:
        use = exp["use_tool"]
        query = ""
        if use:
            for a in card["verifier"]["assertions"]:
                if a["op"] == "regex":
                    query = _corpus_snippet(a["value"], pages) or "china entry rules"
                    break
        obj = {"use_tool": use, "query": query}
        return json.dumps(obj, ensure_ascii=False), obj

    if "decision" in exp:
        reason = _corpus_snippet(exp["reason_clause"], pages) or "third country"
        obj = {"decision": exp["decision"], "reason": f"The governing rule is: {reason}.", "source": url}
        return json.dumps(obj, ensure_ascii=False), obj

    if "action" in exp:
        why = _corpus_snippet(exp["reason_clause"], pages) or "applies to your situation"
        obj = {"action": exp["action"], "why": f"The governing clause is: {why}.", "source": url}
        return json.dumps(obj, ensure_ascii=False), obj

    if exp.get("answered") is False:
        obj = {"answered": False, "answer": ABSTAIN_SENTENCE, "source": url}
        return json.dumps(obj, ensure_ascii=False), obj

    key = exp.get("key") or exp.get("fact", "")
    full = exp.get("fact_full", "")
    payload = {"answered": True, "answer": f"The source states: {key}. {full}".strip(), "source": url}
    if exp.get("date"):
        payload["date"] = exp["date"]
    return json.dumps(payload, ensure_ascii=False), payload


# ----------------------------------------------------------------- 组装
def assign_splits(cards: list[dict]) -> None:
    """G7：按族内**分组**切分（每 4 组取 1 组进 holdout）。

    分组切而不是按行随机切：同一规则/同一来源页的多个实例必须落在同一边，
    否则 holdout 会被近邻泄漏污染，测出来的不是泛化能力。
    每个族都保证有 holdout，避免某个族在 holdout 里缺席。
    """
    by_family: dict[str, list[str]] = {}
    for c in cards:
        fam = c["task_id"][2]
        by_family.setdefault(fam, [])
        if c["split_group"] not in by_family[fam]:
            by_family[fam].append(c["split_group"])
    holdout = set()
    for groups in by_family.values():
        ordered = sorted(groups)
        k = max(1, round(len(ordered) * 0.2))          # 每族约 20% 分组进 holdout
        step = max(1, len(ordered) // k)
        holdout.update(ordered[::step][:k])
    for c in cards:
        c["split"] = "holdout" if c["split_group"] in holdout else "train"


def _card(*, task_id, scenario_id, task_type, split_group, prompt, initial_state, expected,
          verifier, query, source, note, artifact=None) -> dict:
    card = {
        "schema_version": "v2",
        "task_id": task_id,
        "scenario_id": scenario_id,
        "split": "train",          # 真实切分由 assign_splits() 按族内分组统一分配
        "split_group": split_group,
        "task_type": task_type,
        "demand_evidence": {"source": "research", "query_cluster": [query], "geo": GEO, "time_window": WINDOW},
        "prompt": prompt,
        "initial_state": initial_state,
        "allowed_tools": ["lookup_local"],
        "expected_end_state": expected,
        "verifier": verifier,
        "prohibited_leakage": ["answer_derivation", "skill"],
        "source_page": source,
        "note": note,
    }
    if artifact:
        card["artifact"] = artifact
    return card


# ----------------------------------------------------------------- 质量门
def run_gates(cards: list[dict], pages: dict) -> dict:
    from .contracts import iter_errors  # 三线各有一份副本，这里用 A 线自己的

    fails: list[str] = []
    report: dict = {"gates": {}}

    # G1 schema（启用 v2）
    bad = []
    for c in cards:
        errs = iter_errors(str(V2_TASK_SCHEMA), c)
        if errs:
            bad.append(f"{c['task_id']}: {errs[0]['message'][:100]}")
    report["gates"]["G1_schema_v2"] = {"cards": len(cards), "invalid": len(bad), "examples": bad[:5]}
    if bad:
        fails.append(f"G1 {len(bad)} 张卡不符合 v2 schema")

    # G2 唯一性
    ids = [c["task_id"] for c in cards]
    fps = [hashlib.sha256((c["prompt"] + json.dumps(c["verifier"], sort_keys=True)).encode()).hexdigest() for c in cards]
    dup_id, dup_fp = len(ids) - len(set(ids)), len(fps) - len(set(fps))
    report["gates"]["G2_unique"] = {"dup_task_id": dup_id, "dup_prompt_verifier": dup_fp}
    if dup_id or dup_fp:
        fails.append(f"G2 重复：id={dup_id} prompt+verifier={dup_fp}")

    # G3 非退化
    by_family: dict[str, list[dict]] = {}
    for c in cards:
        by_family.setdefault(c["task_id"][2], []).append(c)
    dist = {}
    for fam, group in by_family.items():
        prompts = {c["prompt"] for c in group}
        ratio = len(prompts) / len(group)
        most_shared = max(Counter(c["prompt"] for c in group).values())
        dist[fam] = {"cards": len(group), "distinct_prompts": len(prompts),
                     "ratio": round(ratio, 3), "max_cards_per_prompt": most_shared}
        # 防的是 v1 那种"1 个 prompt × 8 张卡"的退化，而不是"同一份官方问答有 2 条事实"。
        if ratio < 0.4 or most_shared > 2:
            fails.append(f"G3 族 {fam} prompt 退化：{len(prompts)}/{len(group)}，单 prompt 最多 {most_shared} 张")
    b_dec: dict[str, int] = {}
    for c in by_family.get("B", []):
        d = c["expected_end_state"]["decision"]
        b_dec[d] = b_dec.get(d, 0) + 1
    for d, n in b_dec.items():
        if n / max(1, sum(b_dec.values())) < 0.15:
            fails.append(f"G3 决策类 {d} 占比过低（{n}/{sum(b_dec.values())}）")
    report["gates"]["G3_balance"] = {"per_family": dist, "family_B_decisions": b_dec}

    # G4 实质断言
    weak = []
    for c in cards:
        if c["task_type"] not in ("fact_lookup", "artifact_card"):
            continue
        vals = [str(a.get("value")) for a in c["verifier"]["assertions"]]
        if not any(len(v) > 12 for v in vals):
            weak.append(c["task_id"])
    report["gates"]["G4_substantive"] = {"weak_cards": len(weak), "examples": weak[:5]}
    if weak:
        fails.append(f"G4 {len(weak)} 张卡缺实质断言")

    # G5 证据可溯
    missing = [c["task_id"] for c in cards if c["demand_evidence"]["query_cluster"][0] not in pages]
    short = [k for k, v in pages.items() if len(v["text"]) < MIN_PAGE_CHARS]
    report["gates"]["G5_evidence"] = {
        "missing_page_refs": len(missing), "short_pages": len(short), "pages": len(pages),
        "pages_with_effective_date": sum(1 for v in pages.values() if v["effective_date"]),
    }
    if missing:
        fails.append(f"G5 {len(missing)} 张卡引用了不存在的页面")
    if short:
        fails.append(f"G5 {len(short)} 个页面正文过短")

    # G6 泄漏：期望答案要点不得出现在 prompt / initial_state
    leaked = []
    for c in cards:
        blob = c["prompt"] + json.dumps(c["initial_state"], ensure_ascii=False)
        exp = c["expected_end_state"]
        clause = exp.get("reason_clause")
        if clause and clause in blob:
            leaked.append((c["task_id"], "reason_clause"))
        for item in exp.get("required_items", []) or []:
            if item in blob:
                leaked.append((c["task_id"], f"required_item:{item}"))
        if exp.get("answered") is False and '"answered": false' in blob.lower():
            leaked.append((c["task_id"], "answered"))
    report["gates"]["G6_leakage"] = {"leaked": len(leaked), "examples": leaked[:5]}
    if leaked:
        fails.append(f"G6 {len(leaked)} 处答案泄漏")

    # G7 分组切分
    groups: dict[str, set] = {}
    for c in cards:
        groups.setdefault(c["split_group"], set()).add(c["split"])
    bad_split = [g for g, s in groups.items() if len(s) > 1]
    report["gates"]["G7_group_split"] = {"groups": len(groups), "leaky_groups": len(bad_split),
                                        "examples": bad_split[:5]}
    if bad_split:
        fails.append(f"G7 {len(bad_split)} 个分组跨 train/holdout")

    # G8 逐字有据：实质断言必须能在它所引用的页面里找到。两类分开看 ——
    #   ① `contains`（A 族逐字事实）：页面里必须有；
    #   ② `regex` 治理条款（B/F 决策卡要求"点名条款"，note 里写明）：条款也必须在这张卡给的来源里，
    #      否则模型被要求引用一个它根本拿不到的东西。按 **note 语义**判定而不是按 task_type ——
    #      C 族的"清单必需项"、D 族的拒答措辞、E 族的查询词/输出格式都是对**输出**的要求，
    #      不是对来源的断言（按 task_type 判定会误伤 E 族：它也是 conditional_decision）。
    ungrounded = []
    for c in cards:
        page = pages.get(c["demand_evidence"]["query_cluster"][0])
        if page is None:
            continue
        for a in c["verifier"]["assertions"]:
            value = a.get("value")
            if a["op"] == "contains" and isinstance(value, str) and len(value) > 20:
                if value not in page["text"]:
                    ungrounded.append((c["task_id"], value[:40]))
            elif a["op"] == "regex" and "条款" in (a.get("note") or "") and isinstance(value, str):
                if re.search(value, page["text"]) is None:
                    ungrounded.append((c["task_id"], f"clause:{value[:40]}"))
    report["gates"]["G8_grounded"] = {"ungrounded": len(ungrounded), "examples": ungrounded[:5]}
    if ungrounded:
        fails.append(f"G8 {len(ungrounded)} 条实质断言在页面里找不到（有据性不成立）")

    # G9 题目可解：按 expected 构造的完美答案必须通过全部断言
    unsolvable = []
    for c in cards:
        text, parsed = oracle_answer(c, pages)
        if not all(_eval_assertions(c["verifier"]["assertions"], text, parsed)):
            bad_ops = [
                f"{a['op']}:{str(a.get('value'))[:30]}"
                for a, ok in zip(c["verifier"]["assertions"],
                                 _eval_assertions(c["verifier"]["assertions"], text, parsed))
                if not ok
            ]
            unsolvable.append((c["task_id"], bad_ops[:3]))
    report["gates"]["G9_solvable"] = {"unsolvable": len(unsolvable), "examples": unsolvable[:5]}
    if unsolvable:
        fails.append(f"G9 {len(unsolvable)} 张卡连标准答案都过不了（判据不可满足）")

    report["counts"] = {
        "total": len(cards),
        "by_type": {t: sum(1 for c in cards if c["task_type"] == t)
                    for t in ("fact_lookup", "conditional_decision", "artifact_card")},
        "by_family": {k: len(v) for k, v in sorted(by_family.items())},
        "by_split": {s: sum(1 for c in cards if c["split"] == s) for s in ("train", "holdout")},
        "by_scenario": {s: sum(1 for c in cards if c["scenario_id"] == s)
                        for s in sorted({c["scenario_id"] for c in cards})},
    }
    report["failures"] = fails
    report["ok"] = not fails
    return report


# ----------------------------------------------------------------- 入口
def build_dataset_v2(out_dir: Path, pages_path: Path, write_pages: bool = True) -> dict:
    pages = build_pages()
    if write_pages:
        pages_path.parent.mkdir(parents=True, exist_ok=True)
        pages_path.write_text(json.dumps(pages, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    cards = (
        build_family_b(pages)
        + build_family_f(pages)
        + build_family_c(pages)
        + build_family_e(pages)
        + build_family_a(pages)
        + build_family_d(pages)
        + build_family_d2(pages)
    )
    assign_splits(cards)
    cards.sort(key=lambda c: c["task_id"])

    report = run_gates(cards, pages)
    out_dir.mkdir(parents=True, exist_ok=True)
    pack_path = out_dir / "task_pack.jsonl"
    with pack_path.open("w", encoding="utf-8") as fh:
        for c in cards:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    # provenance 里的 *sha256 一律是**产物文件本身的摘要**（不是规范化重序列化的哈希）：
    # 消费者（如 run_once 的收口核对）会拿它跟文件比对，对不上就等于没有溯源。
    provenance = {
        "generator": "dataset_v2",
        "rule_count": len(RULES),
        "pages": len(pages),
        "pack_sha256": hashlib.sha256(pack_path.read_bytes()).hexdigest(),
        "pages_sha256": hashlib.sha256(pages_path.read_bytes()).hexdigest(),
        "source_corpus": str(WEB.relative_to(REPO)),
    }
    (out_dir / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "dataset_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
