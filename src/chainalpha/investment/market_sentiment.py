from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
import json
import re

from chainalpha.connectors.http_utils import http_get, parse_html
from chainalpha.storage.local_store import JsonlStore, sha256_text


@dataclass
class HotSearchItem:
    source: str
    keyword: str
    rank: int | None = None
    heat: float | None = None
    url: str | None = None
    tags: list[str] = field(default_factory=list)
    score_impact: float = 0.0
    reason: str = ""


@dataclass
class MarketSentimentReport:
    generated_at: str
    score: float
    label: str
    summary: str
    hot_items: list[HotSearchItem]
    source_counts: dict[str, int]
    warning_flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    storage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "score": self.score,
            "label": self.label,
            "summary": self.summary,
            "hot_items": [asdict(item) for item in self.hot_items],
            "source_counts": self.source_counts,
            "warning_flags": self.warning_flags,
            "warnings": self.warnings,
            "storage": self.storage,
        }


def build_market_sentiment_report(
    *,
    limit_per_source: int = 50,
    persist_dir: str | Path | None = "./data/market_sentiment",
) -> MarketSentimentReport:
    warnings: list[str] = []
    hot_items = [
        *_fetch_baidu_hot(limit=limit_per_source, warnings=warnings),
        *_fetch_weibo_hot(limit=limit_per_source, warnings=warnings),
    ]
    hot_items = _dedup_items(hot_items)
    scored_items = [_score_item(item) for item in hot_items]
    score, flags = _aggregate_score(scored_items)
    label = _sentiment_label(score)
    summary = _sentiment_summary(score, label, scored_items, flags)
    report = MarketSentimentReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        score=score,
        label=label,
        summary=summary,
        hot_items=scored_items,
        source_counts=_source_counts(scored_items),
        warning_flags=flags,
        warnings=warnings,
    )
    if persist_dir:
        report.storage = persist_market_sentiment(report, persist_dir)
    return report


def persist_market_sentiment(report: MarketSentimentReport, base_dir: str | Path = "./data/market_sentiment") -> dict[str, Any]:
    run_date = report.generated_at[:10]
    day_dir = Path(base_dir) / run_date
    day_dir.mkdir(parents=True, exist_ok=True)
    hot_items_jsonl = day_dir / "hot_items.jsonl"
    report_json = day_dir / "market_sentiment.json"
    report_md = day_dir / "market_sentiment.md"
    JsonlStore(hot_items_jsonl).append_many([asdict(item) for item in report.hot_items])
    report_json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(render_market_sentiment_markdown(report), encoding="utf-8")
    return {
        "date": run_date,
        "day_dir": str(day_dir),
        "hot_items_jsonl": str(hot_items_jsonl),
        "report_json": str(report_json),
        "report_md": str(report_md),
    }


def render_market_sentiment_markdown(report: MarketSentimentReport) -> str:
    lines = [
        "# Market Sentiment",
        "",
        f"> Generated at: {report.generated_at}",
        "",
        "## Summary",
        "",
        f"- Score: {report.score:.1f}",
        f"- Label: {report.label}",
        f"- Summary: {report.summary}",
        f"- Sources: {', '.join(f'{k}={v}' for k, v in report.source_counts.items()) or '-'}",
        "",
    ]
    if report.warning_flags:
        lines.extend(["## Warning Flags", ""])
        for item in report.warning_flags:
            lines.append(f"- {item}")
        lines.append("")
    if report.warnings:
        lines.extend(["## Fetch Warnings", ""])
        for item in report.warnings[:10]:
            lines.append(f"- {item}")
        lines.append("")
    lines.extend([
        "## Hot Search Items",
        "",
        "| Rank | Source | Keyword | Impact | Tags | Reason |",
        "| ---: | --- | --- | ---: | --- | --- |",
    ])
    for item in report.hot_items[:60]:
        lines.append(
            f"| {item.rank or '-'} | {item.source} | {item.keyword} | {item.score_impact:.1f} | "
            f"{', '.join(item.tags) or '-'} | {item.reason or '-'} |"
        )
    lines.extend([
        "",
        "## Scoring Logic",
        "",
        "- Baseline score is 50.",
        "- Bullish/hype market terms raise the score.",
        "- Panic/risk terms lower the score.",
        "- Finance-related hot searches have larger weight than entertainment-only searches.",
        "- Labels: panic < 30, cautious < 45, neutral < 60, hot < 75, overheated >= 75.",
        "",
    ])
    return "\n".join(lines)


def _fetch_baidu_hot(*, limit: int, warnings: list[str]) -> list[HotSearchItem]:
    url = "https://top.baidu.com/board?tab=realtime"
    try:
        resp = http_get(url, timeout=15, headers={"Referer": "https://top.baidu.com/"})
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"baidu hot fetch failed: {type(exc).__name__}: {exc}")
        return []
    text = resp.text
    items: list[HotSearchItem] = []
    # Baidu frequently embeds JSON-ish fields in HTML. Keep parser permissive.
    titles = []
    titles.extend(re.findall(r'"word"\s*:\s*"([^"]+)"', text))
    titles.extend(re.findall(r'"query"\s*:\s*"([^"]+)"', text))
    if not titles:
        parser = parse_html(text, base_url=url)
        titles = [part.strip() for part in re.split(r"\s{2,}|\n", parser.text()) if 2 <= len(part.strip()) <= 40]
    seen: set[str] = set()
    for title in titles:
        keyword = _clean_keyword(title)
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        rank = len(items) + 1
        items.append(
            HotSearchItem(
                source="baidu_hot",
                keyword=keyword,
                rank=rank,
                url=f"https://www.baidu.com/s?wd={quote_plus(keyword)}",
            )
        )
        if len(items) >= limit:
            break
    return items


def _fetch_weibo_hot(*, limit: int, warnings: list[str]) -> list[HotSearchItem]:
    urls = [
        "https://weibo.com/ajax/side/hotSearch",
        "https://s.weibo.com/top/summary?cate=realtimehot",
    ]
    for url in urls:
        try:
            resp = http_get(
                url,
                timeout=15,
                headers={
                    "Referer": "https://weibo.com/",
                    "Accept": "application/json,text/html,*/*",
                },
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"weibo hot fetch failed {url}: {type(exc).__name__}: {exc}")
            continue
        items = _parse_weibo_response(resp.text, url, limit)
        if items:
            return items
    return []


def _parse_weibo_response(text: str, url: str, limit: int) -> list[HotSearchItem]:
    items: list[HotSearchItem] = []
    try:
        data = json.loads(text)
        realtime = ((data.get("data") or {}).get("realtime") or []) if isinstance(data, dict) else []
        for row in realtime:
            keyword = _clean_keyword(str(row.get("word") or row.get("note") or ""))
            if not keyword:
                continue
            rank = len(items) + 1
            items.append(
                HotSearchItem(
                    source="weibo_hot",
                    keyword=keyword,
                    rank=rank,
                    heat=_to_float(row.get("num")),
                    url=row.get("scheme") or f"https://s.weibo.com/weibo?q={quote_plus(keyword)}",
                )
            )
            if len(items) >= limit:
                return items
    except Exception:
        pass
    keywords = re.findall(r'td-02">\s*<a[^>]*>(.*?)</a>', text, flags=re.S)
    if not keywords:
        parser = parse_html(text, base_url=url)
        keywords = [part.strip() for part in re.split(r"\s{2,}|\n", parser.text()) if 2 <= len(part.strip()) <= 40]
    seen: set[str] = set()
    for raw in keywords:
        keyword = _clean_keyword(re.sub(r"<[^>]+>", "", raw))
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        rank = len(items) + 1
        items.append(
            HotSearchItem(
                source="weibo_hot",
                keyword=keyword,
                rank=rank,
                url=f"https://s.weibo.com/weibo?q={quote_plus(keyword)}",
            )
        )
        if len(items) >= limit:
            break
    return items


BULLISH_TERMS = {
    "牛市": 12,
    "大涨": 9,
    "暴涨": 11,
    "涨停": 10,
    "创新高": 8,
    "突破": 6,
    "火爆": 5,
    "抢购": 5,
    "ai": 4,
    "算力": 5,
    "芯片": 4,
    "机器人": 4,
}

PANIC_TERMS = {
    "暴跌": -14,
    "大跌": -10,
    "跳水": -9,
    "崩盘": -16,
    "恐慌": -12,
    "清仓": -9,
    "爆雷": -12,
    "退市": -10,
    "裁员": -6,
    "制裁": -7,
    "关税": -6,
    "战争": -8,
    "危机": -8,
}

MARKET_TERMS = {
    "a股",
    "股市",
    "股票",
    "基金",
    "券商",
    "证券",
    "上证",
    "创业板",
    "港股",
    "美股",
    "纳斯达克",
    "人民币",
    "美元",
    "利率",
    "降息",
    "降准",
    "经济",
    "房地产",
    "黄金",
    "原油",
    "ai",
    "芯片",
    "算力",
}


def _score_item(item: HotSearchItem) -> HotSearchItem:
    text = item.keyword.lower()
    impact = 0.0
    tags: list[str] = []
    reasons: list[str] = []
    rank_weight = 1.4 if item.rank and item.rank <= 10 else 1.0
    market_related = any(term.lower() in text for term in MARKET_TERMS)
    if market_related:
        tags.append("market_related")
        impact += 1.5
    for term, score in BULLISH_TERMS.items():
        if term.lower() in text:
            tags.append("bullish")
            impact += score * rank_weight * (1.4 if market_related else 0.65)
            reasons.append(term)
    for term, score in PANIC_TERMS.items():
        if term.lower() in text:
            tags.append("panic")
            impact += score * rank_weight * (1.4 if market_related else 0.65)
            reasons.append(term)
    item.tags = list(dict.fromkeys(tags))
    item.score_impact = round(impact, 2)
    item.reason = ", ".join(reasons)
    return item


def _aggregate_score(items: list[HotSearchItem]) -> tuple[float, list[str]]:
    score = 50.0 + sum(item.score_impact for item in items)
    finance_count = sum(1 for item in items if "market_related" in item.tags)
    panic_count = sum(1 for item in items if "panic" in item.tags)
    bullish_count = sum(1 for item in items if "bullish" in item.tags)
    flags: list[str] = []
    if finance_count >= 12:
        flags.append("财经/市场相关热词密集")
        score += 4
    if bullish_count >= 8:
        flags.append("亢奋词条偏多")
        score += 5
    if panic_count >= 5:
        flags.append("风险/恐慌词条偏多")
        score -= 8
    return round(max(0.0, min(100.0, score)), 1), flags


def _sentiment_label(score: float) -> str:
    if score < 30:
        return "panic"
    if score < 45:
        return "cautious"
    if score < 60:
        return "neutral"
    if score < 75:
        return "hot"
    return "overheated"


def _sentiment_summary(score: float, label: str, items: list[HotSearchItem], flags: list[str]) -> str:
    top_positive = [item.keyword for item in items if item.score_impact > 0][:3]
    top_negative = [item.keyword for item in items if item.score_impact < 0][:3]
    pieces = [f"市场情绪标签为 {label}，得分 {score:.1f}。"]
    if top_positive:
        pieces.append("正向/亢奋热词：" + "、".join(top_positive) + "。")
    if top_negative:
        pieces.append("风险/恐慌热词：" + "、".join(top_negative) + "。")
    if flags:
        pieces.append("触发提示：" + "、".join(flags) + "。")
    return "".join(pieces)


def _dedup_items(items: list[HotSearchItem]) -> list[HotSearchItem]:
    seen: set[str] = set()
    out: list[HotSearchItem] = []
    for item in items:
        key = sha256_text(item.keyword)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _source_counts(items: list[HotSearchItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.source] = counts.get(item.source, 0) + 1
    return counts


def _clean_keyword(value: str) -> str:
    text = value.replace("\\/", "/")
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("#[]【】")
    if text in {"更多", "热搜", "实时热点", "换一换"}:
        return ""
    return text[:80]


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None
