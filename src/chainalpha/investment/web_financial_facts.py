from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


FINANCIAL_FACT_CATEGORIES = {
    "主要会计数据": ["主要会计数据", "营业收入", "营业总收入", "归母净利润", "净利润", "扣非", "每股收益", "ROE", "净资产收益率"],
    "分产品收入": ["分产品", "产品收入", "主营业务分产品", "产品结构", "光模块收入", "高速光模块"],
    "分地区收入": ["分地区", "境外收入", "海外收入", "国内收入", "北美", "地区收入"],
    "毛利率": ["毛利率", "综合毛利率", "产品毛利率", "销售毛利率", "毛利"],
    "现金流量表": ["经营现金流", "经营活动现金流", "经营活动产生的现金流量净额", "现金流量表", "现金流净额"],
    "资产负债表": ["资产负债表", "总资产", "资产负债率", "货币资金", "流动资产", "非流动资产", "负债"],
    "存货": ["存货", "库存", "备货", "存货周转"],
    "应收账款": ["应收账款", "应收款", "回款", "账期"],
    "在建工程": ["在建工程", "扩产", "产能建设", "募投", "资本开支", "CapEx"],
    "研发费用": ["研发费用", "研发投入", "研发人员", "研发占比"],
    "前五大客户/供应商": ["前五大客户", "前五名客户", "前五大供应商", "客户集中度", "供应商集中度"],
    "管理层讨论与分析": ["管理层讨论", "经营情况讨论", "管理层分析", "MD&A", "业务回顾"],
    "风险提示": ["风险提示", "主要风险", "经营风险", "价格竞争", "客户集中", "汇率风险"],
}

FINANCIAL_SOURCE_HINTS = [
    "年报",
    "半年报",
    "季报",
    "财报",
    "财务",
    "业绩",
    "研报",
    "公告",
    "F10",
    "东方财富",
    "同花顺",
    "证券",
]


@dataclass
class WebFinancialFact:
    category: str
    title: str
    source: str = ""
    url: str = ""
    published_at: str = ""
    excerpt: str = ""
    confidence: str = "low"
    matched_terms: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "published_at": self.published_at,
            "excerpt": self.excerpt,
            "confidence": self.confidence,
            "matched_terms": self.matched_terms or [],
        }


def extract_web_financial_facts(
    docs: list[dict[str, Any]],
    *,
    company_name: str = "",
    ticker: str = "",
    top_k_per_category: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    facts_by_category: dict[str, list[WebFinancialFact]] = {key: [] for key in FINANCIAL_FACT_CATEGORIES}
    for doc in docs:
        text = _doc_text(doc)
        if company_name and company_name not in text and not _ticker_matches(ticker, text, doc):
            continue
        if not _looks_financial(text):
            continue
        for category, terms in FINANCIAL_FACT_CATEGORIES.items():
            matched = [term for term in terms if term.lower() in text.lower()]
            if not matched:
                continue
            facts_by_category[category].append(_fact_from_doc(category, matched, doc, text))

    result: dict[str, list[dict[str, Any]]] = {}
    for category, facts in facts_by_category.items():
        ranked = sorted(facts, key=_fact_rank_key, reverse=True)
        result[category] = [fact.to_dict() for fact in _dedup_facts(ranked)[:top_k_per_category]]
    return result


def _ticker_matches(ticker: str, text: str, doc: dict[str, Any]) -> bool:
    if not ticker:
        return False
    code = ticker.split(".")[0]
    metadata = doc.get("metadata") or {}
    return code in text or code == str(metadata.get("symbol") or "").split(".")[0]


def flatten_web_financial_facts(facts_by_category: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for facts in facts_by_category.values():
        rows.extend(facts)
    return rows


def _fact_from_doc(category: str, matched_terms: list[str], doc: dict[str, Any], text: str) -> WebFinancialFact:
    confidence = _confidence(doc, text, matched_terms)
    return WebFinancialFact(
        category=category,
        title=str(doc.get("title") or ""),
        source=str(doc.get("source") or ""),
        url=str(doc.get("url") or doc.get("path") or ""),
        published_at=str(doc.get("published_at") or ""),
        excerpt=_best_excerpt(text, matched_terms),
        confidence=confidence,
        matched_terms=matched_terms,
    )


def _confidence(doc: dict[str, Any], text: str, matched_terms: list[str]) -> str:
    source = str(doc.get("source") or "")
    source_type = str(doc.get("source_type") or "")
    title = str(doc.get("title") or "")
    score = 0
    if any(word in title or word in text[:500] for word in ["年报", "季报", "半年报", "财报", "业绩说明"]):
        score += 2
    if any(word in source for word in ["巨潮", "交易所", "公司公告", "东方财富", "同花顺", "证券"]):
        score += 2
    if any(word in source_type for word in ["announcement", "filing", "research", "mainstream_media", "media_crawler"]):
        score += 1
    if len(set(matched_terms)) >= 2:
        score += 1
    if any(word in text for word in ["营业收入", "净利润", "毛利率", "现金流", "存货", "应收账款"]):
        score += 1
    if score >= 5:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _looks_financial(text: str) -> bool:
    return any(word.lower() in text.lower() for word in FINANCIAL_SOURCE_HINTS + _all_category_terms())


def _all_category_terms() -> list[str]:
    terms: list[str] = []
    for values in FINANCIAL_FACT_CATEGORIES.values():
        terms.extend(values)
    return terms


def _doc_text(doc: dict[str, Any]) -> str:
    return " ".join(
        str(doc.get(key) or "")
        for key in ["title", "content", "summary", "source", "source_type", "published_at", "url", "path"]
    )


def _best_excerpt(text: str, terms: list[str], max_len: int = 260) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    positions = [clean.lower().find(term.lower()) for term in terms if clean.lower().find(term.lower()) >= 0]
    if not positions:
        return clean[:max_len]
    start = max(0, min(positions) - 80)
    return clean[start : start + max_len]


def _fact_rank_key(fact: WebFinancialFact) -> tuple[int, int, str]:
    confidence_score = {"high": 3, "medium": 2, "low": 1}.get(fact.confidence, 0)
    term_score = len(fact.matched_terms or [])
    return confidence_score, term_score, fact.published_at


def _dedup_facts(facts: list[WebFinancialFact]) -> list[WebFinancialFact]:
    out: list[WebFinancialFact] = []
    seen: set[str] = set()
    for fact in facts:
        key = (fact.url or fact.title or fact.excerpt[:80]).strip().lower()
        key = f"{fact.category}|{key}"
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out
