from __future__ import annotations

import re
from collections import Counter


CJK_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z0-9_+\-.]+|[\u4e00-\u9fff]")


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """
    Lightweight tokenizer:
    - English/numbers are word tokens.
    - Chinese is represented by single chars + bigrams for fuzzy retrieval.
    This avoids requiring jieba in the MVP.
    """
    text = text.lower()
    raw = WORD_RE.findall(text)
    tokens: list[str] = []
    cjk_buffer: list[str] = []

    def flush_cjk() -> None:
        nonlocal cjk_buffer
        if not cjk_buffer:
            return
        tokens.extend(cjk_buffer)
        tokens.extend(["".join(cjk_buffer[i:i + 2]) for i in range(len(cjk_buffer) - 1)])
        cjk_buffer = []

    for tok in raw:
        if CJK_RE.fullmatch(tok):
            cjk_buffer.append(tok)
        else:
            flush_cjk()
            if len(tok) > 1:
                tokens.append(tok)
    flush_cjk()
    return tokens


def keyword_density(text: str, keywords: list[str]) -> float:
    if not text:
        return 0.0
    t = text.lower()
    hits = sum(t.count(k.lower()) for k in keywords)
    return hits / max(len(text), 1)


def top_terms(text: str, n: int = 20) -> list[str]:
    stop = {"the", "and", "for", "with", "this", "that", "一个", "以及", "可以", "需要"}
    counter = Counter(tok for tok in tokenize(text) if tok not in stop and len(tok) > 1)
    return [w for w, _ in counter.most_common(n)]


def extract_snippet(text: str, keywords: list[str], window: int = 160) -> str:
    low = text.lower()
    pos = -1
    for k in keywords:
        pos = low.find(k.lower())
        if pos >= 0:
            break
    if pos < 0:
        return text[:window].strip()
    start = max(0, pos - window // 2)
    end = min(len(text), pos + window // 2)
    return text[start:end].strip()
