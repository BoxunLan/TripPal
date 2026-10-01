"""A 族（有据事实答）与 D2 族（真实问题 × 无关来源）的语料抽取。

素材：
- `research/raw/web/bjfaq-*.txt`（29 份）：**真实外国游客提问 + 北京 12345 官方答复 + 日期**。
  这是最理想的 A 族素材：问题真实、来源权威、页面自带生效日期。
- `research/se-questions.tsv`（566 条）：Stack Exchange 真实问题标题，用来配规则页（A2）或配无关页（D2）。

为什么用确定性抽取而不是让模型生成答案：判据的实质断言必须是**页面正文的精确子串**，
否则又回到"断言可以编"的老问题。这里只做切片与筛选，切片天然逐字。
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[4]
WEB = REPO / "research" / "raw" / "web"
SE_QUESTIONS = REPO / "research" / "se-questions.tsv"

_NAV = re.compile(r"^(Home 12345|12345 hotline|Related Articles|Attachment|Share|Print)", re.I)
_AUTHORITY = re.compile(
    r"(Administration Center|Municipal|Bureau|Committee|Authority|Government|Office of)", re.I
)
_SUBSTANTIVE = re.compile(
    r"(\d|percent|%|¥|CNY|RMB|USD|must|required|not authorized|only|within|no more than|"
    r"days?|hours?|minutes?|VISA|MasterCard|cash)",
    re.I,
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STOP = {
    "the", "a", "an", "is", "are", "do", "does", "i", "my", "in", "of", "to", "for", "and", "or", "on",
    "can", "what", "how", "when", "where", "which", "if", "it", "you", "your", "with", "at", "be", "not",
    "have", "has", "there", "this", "that", "from", "will", "would", "should", "about",
}


def rare_overlap(question: str, text: str) -> float:
    """问题里的"稀有词"有多大比例出现在文本里。用于相关性/无关性判定。"""
    toks = {t for t in re.findall(r"[a-z]{4,}", question.lower()) if t not in _STOP}
    if not toks:
        return 0.0
    low = text.lower()
    return sum(1 for t in toks if t in low) / len(toks)


def _fragment(sentence: str, question: str, want: int = 64, min_overlap: float = 0.2,
              min_words: int = 6) -> str | None:
    """从句子切出一段**逐字**片段：优先围绕数字/规则词，长度约 want 字符。

    踩过的坑，都在这里堵住：
    1. 片段必须**从词首开始**（曾经切出 "ets of Jingshan Park"）；
    2. 片段必须是**单行**（曾经带出换行与 "The"）；
    3. 片段要有足够词量（拒绝 "needing its own visa or scheme." 这种残句）；
    4. 片段与问题的词面关联由调用方给阈值：A1（官方问答）本就是该问题的答复，阈值可低；
       A2（按相似度配页）是推断出来的相关性，阈值要高。
    """
    flat = re.sub(r"\s+", " ", sentence).strip()
    m = _SUBSTANTIVE.search(flat)
    if not m:
        return None
    start = max(0, m.start() - want // 3)
    if start > 0:
        nxt = flat.find(" ", start)
        start = nxt + 1 if nxt != -1 else start
    window = flat[start:start + want]
    if not window.endswith((" ", ".", ",", ";", ")", "%")) and " " in window:
        window = window.rsplit(" ", 1)[0]
    frag = window.strip(" ,;:—-")
    if len(frag) < 24 or frag.endswith(":") or len(frag.split()) < min_words:
        return None
    letters = sum(1 for ch in frag if ch.isalpha())
    if letters < 20 or letters / max(1, len(frag)) < 0.55:
        return None
    if rare_overlap(question, frag) < min_overlap:
        return None
    # 拒绝"标题与正文粘连"的片段（HTML→文本常见：'... your phone What the 240-hour rule actual ...'）。
    # 阈值放到 4 个连续首字母大写词：官方答复里出现机构名（Beijing Municipal Administration Center）是正常的。
    if re.search(r"\b[A-Z][a-z]+\b(?: [A-Z][a-z]+\b){3,}", frag):
        return None
    # 拒绝含引号/反斜杠的片段：模型输出的是 JSON，`"` 会被转义成 `\"`，
    # 于是 contains 断言永远匹配不上 —— 这种卡是不可解的（G9 会抓到）。
    if '"' in frag or "\\" in frag or "\u201c" in frag or "\u201d" in frag:
        return None
    return frag


def _find_question(body: list[str]) -> str | None:
    """12345 的问题常以 "Thank you in advance!" 之类收尾，不能只认行尾的 `?`；
    也允许跨两行拼出的问题。"""
    for i, line in enumerate(body):
        if _NAV.match(line) or _DATE.match(line) or len(line) < 30:
            continue
        if "?" in line and len(line) >= 40:
            return line
        if i + 1 < len(body):
            joined = f"{line} {body[i + 1]}".strip()
            if "?" in joined and 40 <= len(joined) <= 700:
                return joined
    return None


_KEY_ANCHOR = re.compile(
    r"(\d+(?:[.,]\d+)?\s?(?:%|hours?|days?|minutes?|years?|RMB|CNY|USD|\u00a5)?|"
    r"no more than|not authorized|within \d+|at least|must (?:be|have|carry)|"
    r"cannot|is not allowed|are not allowed|free of charge|\u00a5\s?\d+)",
    re.I,
)


_TRIM_LEAD = {
    "of", "than", "and", "the", "a", "an", "in", "at", "on", "by", "from", "that", "which",
    "is", "are", "was", "were", "be", "as", "it", "its", "or", "but", "if", "then", "when",
    "while", "also", "just", "still", "even", "to", "this", "these", "those", "there",
}
_TRIM_TAIL = {
    "of", "the", "a", "an", "and", "to", "in", "at", "on", "by", "with", "for", "from",
    "is", "are", "be", "as", "or", "but", "if", "that", "which", "than",
}


def _key_phrase(sentence: str, max_words: int = 6, min_words: int = 2) -> str | None:
    """从句子切出一段**短而带实质**的逐字短语（围绕数字/规则词，≤6 词）。

    为什么要它：只断言 10 词以上的整段逐字再现，会把"读了但改写"和"根本没读"混为一谈
    （实测 A 族 1/34 通过）。关键短语能区分二者，同时仍是页面里的精确子串。
    """
    flat = re.sub(r"\s+", " ", sentence).strip()
    m = _KEY_ANCHOR.search(flat)
    if not m:
        return None
    words = flat.split(" ")
    pos, word_idx = 0, 0
    for i, w in enumerate(words):
        end = pos + len(w)
        if pos <= m.start() < end or pos <= m.end() <= end:
            word_idx = i
            break
        pos = end + 1

    def window(start: int) -> str:
        return " ".join(words[start:start + max_words]).strip(" ,;:-\u2014")

    phrase = window(max(0, word_idx - 1))
    if not _KEY_ANCHOR.search(phrase):
        phrase = window(word_idx)
    if not _KEY_ANCHOR.search(phrase):
        return None
    # 前后各裁掉纯连接词，让短语读起来是"要点"而不是半个从句
    toks = phrase.split(" ")
    while len(toks) > min_words and toks[0].lower() in _TRIM_LEAD:
        toks.pop(0)
    while len(toks) > min_words and toks[-1].lower() in _TRIM_TAIL:
        toks.pop()
    phrase = " ".join(toks).strip(" ,;:-\u2014")
    if len(toks) < min_words or len(phrase) < 16:
        return None
    if '"' in phrase or "\\" in phrase:
        return None
    if not _KEY_ANCHOR.search(phrase):
        return None
    return phrase


def load_bjfaq() -> list[dict]:
    """解析 29 份 12345 问答：问题 / 官方答复 / 日期 / 逐字事实片段。"""
    out: list[dict] = []
    for path in sorted(WEB.glob("bjfaq-*.txt")):
        raw = path.read_text(encoding="utf-8", errors="replace")
        url = ""
        for line in raw.splitlines():
            if line.startswith("# source:"):
                url = line.split(":", 1)[1].strip()
        body = [
            l.strip()
            for l in raw.splitlines()
            if l.strip() and not l.startswith("# ")
        ]
        question = _find_question(body)
        if not question:
            continue
        date = next((l for l in body if _DATE.match(l)), "")
        idx = body.index(date) if date in body else 0
        answer_lines = [
            l for l in body[idx + 1:]
            if not _NAV.match(l) and not _AUTHORITY.search(l[:70]) and l != question and len(l) > 12
        ]
        answer = " ".join(answer_lines).strip()
        if len(answer) < 120:
            continue
        facts: list[str] = []
        keys: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", answer):
            if not (40 <= len(sentence) <= 400) or not _SUBSTANTIVE.search(sentence):
                continue
            cand = _fragment(sentence, question, min_overlap=0.0, min_words=4)
            if not cand or cand in facts:
                continue
            facts.append(cand)
            keys.append(_key_phrase(cand) or _key_phrase(sentence) or cand)
        order = sorted(range(len(facts)), key=lambda i: -rare_overlap(question, facts[i]))[:3]
        facts = [facts[i] for i in order]
        keys = [keys[i] for i in order]
        if not facts:
            continue
        out.append({"slug": path.stem, "url": url, "question": question, "date": date,
                    "answer": answer, "facts": facts, "keys": keys})
    return out


_OFF_TOPIC = re.compile(
    r"(chinese (citizen|national|passport|resident)|as a chinese|mainland chinese|"
    r"my chinese (wife|husband|girlfriend|boyfriend|friend)|"
    r"hong kong (citizen|resident)|taiwanese? (citizen|passport)|"
    r"do i need a visa to (visit|go to) (the )?(us|uk|schengen|japan|korea|australia))",
    re.I,
)


def is_inbound_question(title: str) -> bool:
    """只保留"入境中国的外国游客"视角的问题。

    语料里混着"中国公民去香港要不要签注"这类问题，对 TripPal 是噪声，
    会被模型答对也无法反映目标场景。
    """
    return not _OFF_TOPIC.search(title)


def load_se_questions(limit: int | None = None) -> list[dict]:
    rows: list[dict] = []
    if not SE_QUESTIONS.is_file():
        return rows
    lines = SE_QUESTIONS.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < len(header):
            continue
        row = dict(zip(header, parts))
        title = (row.get("title") or "").strip()
        if len(title) < 25 or not title.endswith("?"):
            continue
        if not is_inbound_question(title):
            continue
        rows.append({"id": row.get("id", ""), "title": title, "link": row.get("link", ""),
                     "score": int(row.get("score") or 0), "views": int(row.get("views") or 0)})
    rows.sort(key=lambda r: (-r["score"], -r["views"]))
    return rows[:limit] if limit else rows


def pick_se_facts(se_rows: list[dict], page_texts: dict[str, str], need: int,
                  min_overlap: float = 0.6) -> list[dict]:
    """A2：给 SE 真实问题配一个**高度相关**的规则页，并切出逐字事实片段。"""
    picked: list[dict] = []
    used_q: set[str] = set()
    for row in se_rows:
        if len(picked) >= need:
            break
        title = row["title"]
        if title in used_q:
            continue
        best = None
        for key, text in page_texts.items():
            ov = rare_overlap(title, text)
            if best is None or ov > best[1]:
                best = (key, ov)
        if not best or best[1] < min_overlap:
            continue
        page_key, _ = best
        text = page_texts[page_key]
        fact = None
        best = 0.0
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if not (40 <= len(sentence) <= 400) or not _SUBSTANTIVE.search(sentence):
                continue
            if rare_overlap(title, sentence) <= 0.3:
                continue
            cand = _fragment(sentence, title, min_overlap=0.3, min_words=6)
            if not cand:
                continue
            score = rare_overlap(title, cand)
            if score > best:
                best, fact = score, cand
        if not fact:
            continue
        used_q.add(title)
        picked.append({"question": title, "link": row["link"], "page_key": page_key,
                       "fact": fact, "key": _key_phrase(fact) or fact, "score": row["score"]})
    return picked


def pick_se_unrelated(se_rows: list[dict], page_texts: dict[str, str], need: int,
                      max_overlap: float = 0.2) -> list[dict]:
    """D2：真实问题 × **无关**来源页 —— 奖励诚实拒答，惩罚硬编。"""
    picked: list[dict] = []
    keys = list(page_texts)
    for row in se_rows:
        if len(picked) >= need:
            break
        title = row["title"]
        for key in keys:
            if rare_overlap(title, page_texts[key]) <= max_overlap:
                picked.append({"question": title, "link": row["link"], "page_key": key})
                keys = [k for k in keys if k != key]   # 同一页只用一次，避免重复卡
                break
    return picked
