from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from chainalpha.config import load_settings
from chainalpha.investment.company_resolver import resolve_query_profiles
from chainalpha.investment.company_map import load_company_profiles
from chainalpha.nlp.llm_client import LLMClient


@dataclass
class QueryExpansion:
    original_query: str
    corrected_query: str
    intent: str = "unknown"
    keywords: list[str] = field(default_factory=list)
    company_names: list[str] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    industry_terms: list[str] = field(default_factory=list)
    product_terms: list[str] = field(default_factory=list)
    event_terms: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    confidence: float = 0.5
    method: str = "rules"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "corrected_query": self.corrected_query,
            "intent": self.intent,
            "keywords": self.keywords,
            "company_names": self.company_names,
            "tickers": self.tickers,
            "aliases": self.aliases,
            "industry_terms": self.industry_terms,
            "product_terms": self.product_terms,
            "event_terms": self.event_terms,
            "negative_terms": self.negative_terms,
            "confidence": self.confidence,
            "method": self.method,
            "warnings": self.warnings,
        }

    @property
    def crawl_keywords(self) -> list[str]:
        company_names = self.company_names[:1] if self.intent == "company" else self.company_names
        tickers = self.tickers[:2] if self.intent == "company" else self.tickers
        aliases = self.aliases[:4] if self.intent == "company" else self.aliases
        terms = [
            self.corrected_query,
            *company_names,
            *tickers,
            *aliases,
            *self.product_terms,
            *self.event_terms,
        ]
        return _unique_terms(terms, limit=12)

    @property
    def search_keywords(self) -> list[str]:
        terms = [
            self.corrected_query,
            *self.keywords,
            *self.company_names,
            *self.tickers,
            *self.aliases,
            *self.industry_terms,
            *self.product_terms,
            *self.event_terms,
        ]
        return _unique_terms(terms, limit=24)


def expand_user_query(
    query: str,
    *,
    company_map: str,
    enable_llm: bool = True,
) -> QueryExpansion:
    base = _rule_expand_query(query, company_map=company_map)
    if not enable_llm:
        base.method = "rules_disabled_llm"
        return base

    try:
        settings = load_settings()
        if not settings.llm_enabled:
            base.method = "rules_no_llm"
            base.warnings.append("OPENAI_API_KEY not configured; query expansion used rule fallback.")
            return base
        result = _llm_expand_query(query, base, settings)
        return _merge_expansion(base, result)
    except Exception as exc:  # noqa: BLE001
        base.method = "rules_llm_failed"
        base.warnings.append(f"LLM query expansion failed: {type(exc).__name__}: {exc}")
        return base


def _llm_expand_query(query: str, base: QueryExpansion, settings) -> dict[str, Any]:
    client = LLMClient(settings)
    prompt = {
        "user_query": query,
        "rule_guess": base.to_dict(),
        "requirements": [
            "纠正明显错别字，但不要把不确定的公司强行改成另一家公司。",
            "扩展用于财经新闻、公告、研报搜索的关键词。",
            "如果是上市公司，尽量保留公司简称、全称、股票代码、常见别名。",
            "如果是行业/板块，扩展上下游、产品、技术路线、事件催化词。",
            "negative_terms放入可能造成误搜的词，不要放入keywords。",
        ],
        "json_schema": {
            "corrected_query": "str",
            "intent": "company|sector|theme|claim|market|unknown",
            "keywords": ["str"],
            "company_names": ["str"],
            "tickers": ["str"],
            "aliases": ["str"],
            "industry_terms": ["str"],
            "product_terms": ["str"],
            "event_terms": ["str"],
            "negative_terms": ["str"],
            "confidence": "0-1 float",
        },
    }
    return client.chat_json(
        system=(
            "你是A股投研检索Query泛化助手。你的任务是把用户输入纠错并扩展成搜索关键词。"
            "必须返回JSON，不要输出解释。不要编造不存在的股票代码；不确定就留空。"
        ),
        user=json.dumps(prompt, ensure_ascii=False),
        timeout=35,
    )


def _rule_expand_query(query: str, *, company_map: str) -> QueryExpansion:
    clean_query = _clean_query(query)
    profiles = []
    try:
        profiles = resolve_query_profiles(clean_query, load_company_profiles(company_map))
    except Exception:
        profiles = []
    exact_profiles = [profile for profile in profiles if _profile_exact_match(clean_query, profile)]
    if exact_profiles:
        profiles = exact_profiles

    expansion = QueryExpansion(
        original_query=query,
        corrected_query=clean_query,
        intent="company" if profiles else _guess_intent(clean_query),
        keywords=_basic_terms(clean_query),
        method="rules",
    )
    for profile in profiles[:3]:
        expansion.company_names.append(profile.name)
        if profile.ticker:
            expansion.tickers.append(profile.ticker)
            expansion.tickers.append(profile.ticker.split(".")[0])
        expansion.aliases.extend(profile.aliases or [])
        expansion.industry_terms.extend([profile.industry] if profile.industry else [])
        expansion.product_terms.extend(profile.nodes or [])
        expansion.keywords.extend(profile.watch_tags or [])

    expansion.event_terms.extend(_default_event_terms(clean_query))
    _dedup_expansion(expansion)
    return expansion


def _profile_exact_match(query: str, profile: Any) -> bool:
    q = query.strip().lower()
    terms = [profile.name, profile.ticker or "", *((profile.aliases or []))]
    terms.extend([(profile.ticker or "").split(".")[0]])
    return any(term and str(term).strip().lower() == q for term in terms)


def _merge_expansion(base: QueryExpansion, result: dict[str, Any]) -> QueryExpansion:
    merged = QueryExpansion(
        original_query=base.original_query,
        corrected_query=str(result.get("corrected_query") or base.corrected_query).strip() or base.corrected_query,
        intent=str(result.get("intent") or base.intent),
        keywords=[*base.keywords, *_list_field(result, "keywords")],
        company_names=[*base.company_names, *_list_field(result, "company_names")],
        tickers=[*base.tickers, *_list_field(result, "tickers")],
        aliases=[*base.aliases, *_list_field(result, "aliases")],
        industry_terms=[*base.industry_terms, *_list_field(result, "industry_terms")],
        product_terms=[*base.product_terms, *_list_field(result, "product_terms")],
        event_terms=[*base.event_terms, *_list_field(result, "event_terms")],
        negative_terms=[*base.negative_terms, *_list_field(result, "negative_terms")],
        confidence=_float_field(result.get("confidence"), default=base.confidence),
        method="llm",
        warnings=base.warnings,
    )
    _dedup_expansion(merged)
    return merged


def _dedup_expansion(expansion: QueryExpansion) -> None:
    expansion.keywords = _unique_terms(expansion.keywords, limit=24)
    expansion.company_names = _unique_terms(expansion.company_names, limit=8)
    expansion.tickers = _unique_terms(expansion.tickers, limit=8)
    expansion.aliases = _unique_terms(expansion.aliases, limit=12)
    expansion.industry_terms = _unique_terms(expansion.industry_terms, limit=12)
    expansion.product_terms = _unique_terms(expansion.product_terms, limit=16)
    expansion.event_terms = _unique_terms(expansion.event_terms, limit=16)
    expansion.negative_terms = _unique_terms(expansion.negative_terms, limit=12)


def _list_field(result: dict[str, Any], key: str) -> list[str]:
    value = result.get(key)
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，/|\s]+", value) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _float_field(value: Any, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _clean_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "")).strip()


def _basic_terms(query: str) -> list[str]:
    terms = [item.strip() for item in re.split(r"[,，/|\s]+", query) if item.strip()]
    if query and query not in terms:
        terms.insert(0, query)
    code_match = re.findall(r"\b[036]\d{5}(?:\.(?:SZ|SH))?\b", query.upper())
    terms.extend(code_match)
    return _unique_terms(terms, limit=12)


def _guess_intent(query: str) -> str:
    if re.search(r"\b[036]\d{5}(?:\.(?:SZ|SH))?\b", query.upper()):
        return "company"
    if any(word in query for word in ["板块", "行业", "产业链", "主题"]):
        return "sector"
    if any(word in query for word in ["上涨", "下跌", "业绩", "真实吗", "是否"]):
        return "claim"
    return "unknown"


def _default_event_terms(query: str) -> list[str]:
    terms = ["公告", "财报", "研报", "业绩", "订单", "客户认证", "扩产", "产品进展", "风险"]
    if any(word in query for word in ["光模块", "中际旭创", "新易盛"]):
        terms.extend(["800G", "1.6T", "硅光", "CPO", "LPO", "AI数据中心"])
    if any(word in query for word in ["工业富联", "服务器", "算力"]):
        terms.extend(["AI服务器", "算力", "英伟达", "云厂商", "液冷"])
    return terms


def _unique_terms(terms: list[str], *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        clean = _clean_query(str(term))
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
        if len(out) >= limit:
            break
    return out
