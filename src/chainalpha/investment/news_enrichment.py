from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from typing import Any

import requests

from chainalpha.config import load_settings
from chainalpha.nlp.llm_client import LLMClient
from chainalpha.schemas import SourceDocument
from chainalpha.utils.text import normalize_text


VALUE_KEYWORDS = {
    "earnings": ["业绩", "预增", "净利润", "营收", "年报", "季报", "中报", "扣非", "毛利", "盈利"],
    "order": ["订单", "合同", "中标", "签约", "采购", "客户", "认证", "供应商", "交付"],
    "technology": ["突破", "研发", "新品", "产品", "量产", "800G", "1.6T", "硅光", "CPO", "LPO", "AI"],
    "capacity": ["扩产", "产能", "投资", "项目", "基地", "募投", "资本开支"],
    "risk": ["问询", "处罚", "减持", "诉讼", "亏损", "下滑", "风险", "解禁", "监管"],
    "market": ["涨停", "跌停", "大涨", "大跌", "股价", "资金", "龙虎榜", "机构"],
}

LOW_VALUE_HINTS = ["行情快报", "早报", "午评", "收评", "指数", "板块普涨", "三大指数", "直播", "滚动"]


def enrich_valuable_news_documents(
    topic: str,
    docs: list[SourceDocument],
    *,
    max_titles: int = 40,
    max_articles: int = 8,
    timeout: int = 12,
) -> tuple[list[SourceDocument], list[str]]:
    """Select valuable MediaCrawler finance news, fetch article body, and summarize.

    The expensive step is article fetching/summarization, so the pipeline first
    judges only processed titles. If the LLM is unavailable, a rule-based score
    keeps the research flow deterministic.
    """

    candidates = [_doc for _doc in docs if _is_mediacrawler_finance_news(_doc)]
    if not candidates:
        return [], []

    title_candidates = candidates[:max_titles]
    warnings: list[str] = []
    judgements = _judge_titles_with_llm(topic, title_candidates, warnings)
    if not judgements:
        judgements = [_judge_title_by_rules(topic, doc) for doc in title_candidates]

    by_id = {doc.id: doc for doc in title_candidates}
    valuable = [
        item
        for item in judgements
        if item.get("valuable") and item.get("doc_id") in by_id
    ]
    valuable.sort(key=lambda item: float(item.get("score") or 0), reverse=True)

    enriched: list[SourceDocument] = []
    for item in valuable[:max_articles]:
        doc = by_id[str(item["doc_id"])]
        body, fetch_warning = _fetch_article_text(doc.url, timeout=timeout)
        if fetch_warning:
            warnings.append(f"{doc.title}: {fetch_warning}")
        summary = _summarize_article_with_llm(topic, doc, body, item, warnings) if body else ""
        if not summary:
            summary = _summarize_article_by_rules(doc, body)
        content = _compose_enriched_content(doc, summary, body)
        metadata = dict(doc.metadata or {})
        metadata["news_value"] = {
            "valuable": True,
            "score": float(item.get("score") or 0),
            "category": item.get("category") or "unknown",
            "reason": item.get("reason") or "",
            "article_fetched": bool(body),
        }
        metadata["article_summary"] = summary
        metadata["article_excerpt"] = body[:1200] if body else ""
        enriched.append(
            replace(
                doc,
                content=content,
                metadata=metadata,
            )
        )

    return enriched, warnings


def _is_mediacrawler_finance_news(doc: SourceDocument) -> bool:
    metadata = doc.metadata or {}
    platform = str(metadata.get("platform") or "")
    return platform == "finance_news" or "media_crawler" in doc.source and doc.source_type == "mainstream_media"


def _judge_titles_with_llm(topic: str, docs: list[SourceDocument], warnings: list[str]) -> list[dict[str, Any]]:
    try:
        settings = load_settings()
        if not settings.llm_enabled:
            return []
        client = LLMClient(settings)
        payload = [
            {
                "doc_id": doc.id,
                "title": doc.title,
                "source": doc.source,
                "url": doc.url,
            }
            for doc in docs
        ]
        result = client.chat_json(
            system=(
                "你是投研新闻筛选助手。只判断新闻标题是否值得进入下一步正文提取。"
                "有价值新闻包括：财报/业绩、订单/客户认证、产品/技术、扩产/资本开支、"
                "监管风险、股价资金异动、重大产业催化。普通大盘收评、泛泛板块热度、"
                "不影响目标公司基本面或预期的新闻应判为无价值。"
            ),
            user=json.dumps({"topic": topic, "items": payload}, ensure_ascii=False),
            timeout=40,
        )
        items = result.get("items")
        return items if isinstance(items, list) else []
    except Exception as exc:  # noqa: BLE001 - LLM is optional in this stage
        warnings.append(f"news title LLM filter failed, fallback to rules: {type(exc).__name__}: {exc}")
        return []


def _judge_title_by_rules(topic: str, doc: SourceDocument) -> dict[str, Any]:
    text = f"{doc.title} {doc.content}".lower()
    topic_terms = [term for term in [topic, *_split_topic_terms(topic)] if term]
    topic_hit = any(term.lower() in text for term in topic_terms)
    category_scores: dict[str, int] = {}
    for category, words in VALUE_KEYWORDS.items():
        category_scores[category] = sum(1 for word in words if word.lower() in text)
    category, keyword_hits = max(category_scores.items(), key=lambda item: item[1])
    low_value = any(hint.lower() in text for hint in LOW_VALUE_HINTS)
    score = keyword_hits * 0.25 + (0.4 if topic_hit else 0.0) - (0.25 if low_value else 0.0)
    return {
        "doc_id": doc.id,
        "valuable": bool(topic_hit and score >= 0.45),
        "score": round(max(0.0, min(1.0, score)), 2),
        "category": category if keyword_hits else "general",
        "reason": "标题命中目标和投研关键词" if topic_hit and keyword_hits else "标题缺少明确投研价值信号",
    }


def _split_topic_terms(topic: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，\s/|]+", topic or "") if part.strip()]


def _fetch_article_text(url: str | None, *, timeout: int) -> tuple[str, str | None]:
    if not url or not url.startswith(("http://", "https://")):
        return "", "missing article url"
    try:
        os.environ.pop("SSLKEYLOGFILE", None)
        session = requests.Session()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            )
        }
        resp = session.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        text = _html_to_text(resp.text)
        return text[:12000], None
    except Exception as exc:  # noqa: BLE001
        return "", f"article fetch failed: {type(exc).__name__}: {exc}"


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<script.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?</style>", " ", html)
    html = re.sub(r"(?is)<nav.*?</nav>", " ", html)
    html = re.sub(r"(?is)<footer.*?</footer>", " ", html)
    html = re.sub(r"(?is)<[^>]+>", "\n", html)
    html = (
        html.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    lines = [line.strip() for line in html.splitlines()]
    lines = [line for line in lines if len(line) >= 12]
    return normalize_text("\n".join(lines))


def _summarize_article_with_llm(
    topic: str,
    doc: SourceDocument,
    body: str,
    judgement: dict[str, Any],
    warnings: list[str],
) -> str:
    try:
        settings = load_settings()
        if not settings.llm_enabled:
            return ""
        client = LLMClient(settings)
        result = client.chat_json(
            system=(
                "你是投研新闻摘要助手。请只基于给定新闻正文，输出JSON。"
                "summary要说明事件、涉及主体、对基本面/估值/风险的可能影响、需要验证的数据。"
                "不要编造正文没有的订单金额、客户名称、财务数字。"
            ),
            user=json.dumps(
                {
                    "topic": topic,
                    "title": doc.title,
                    "url": doc.url,
                    "value_judgement": judgement,
                    "article_text": body[:5000],
                },
                ensure_ascii=False,
            ),
            timeout=50,
        )
        summary = result.get("summary")
        return normalize_text(str(summary))[:900] if summary else ""
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{doc.title}: article LLM summary failed, fallback to extractive summary: {type(exc).__name__}: {exc}")
        return ""


def _summarize_article_by_rules(doc: SourceDocument, body: str) -> str:
    text = body or doc.content or doc.title
    sentences = re.split(r"(?<=[。！？!?])\s*", text)
    useful = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip() and (
            any(word in sentence for words in VALUE_KEYWORDS.values() for word in words)
            or doc.title[:12] in sentence
        )
    ]
    if not useful:
        useful = [sentence.strip() for sentence in sentences if len(sentence.strip()) >= 20]
    return normalize_text(" ".join(useful[:4]))[:800]


def _compose_enriched_content(doc: SourceDocument, summary: str, body: str) -> str:
    parts = [
        f"标题: {doc.title}",
        f"来源: {doc.source}",
        f"链接: {doc.url or '-'}",
    ]
    if summary:
        parts.append(f"投研摘要: {summary}")
    if body:
        parts.append(f"正文摘录: {body[:1600]}")
    else:
        parts.append(f"原始摘要: {doc.content[:800]}")
    return "\n".join(parts)
