from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from chainalpha.config import load_settings
from chainalpha.investment.company_resolver import resolve_query_profiles
from chainalpha.investment.company_map import load_company_profiles
from chainalpha.nlp.llm_client import LLMClient


MATERIAL_CHAIN_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "电子布": {
        "aliases": ["电子级玻璃纤维布", "电子玻纤布", "E-glass fabric"],
        "upstream": ["电子级玻璃纤维纱", "玻璃纤维", "石英砂", "叶腊石", "浸润剂", "铂铑合金漏板"],
        "midstream": ["电子布", "开纤", "织造", "表面处理", "薄布", "超薄布", "极薄布"],
        "downstream": ["覆铜板", "CCL", "PCB", "高频高速覆铜板", "服务器PCB", "AI服务器", "汽车电子"],
        "companies": [
            "电子布 上市公司",
            "电子布 相关公司",
            "玻纤电子布 公司",
            "覆铜板 电子布 公司",
            "中国巨石",
            "宏和科技",
            "中材科技",
            "生益科技",
            "南亚新材",
            "金安国纪",
        ],
    }
}


@dataclass
class QueryExpansion:
    original_query: str
    corrected_query: str
    intent: str = "unknown"
    topic_class: str = "unknown"
    keywords: list[str] = field(default_factory=list)
    company_names: list[str] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    industry_terms: list[str] = field(default_factory=list)
    product_terms: list[str] = field(default_factory=list)
    event_terms: list[str] = field(default_factory=list)
    research_focus_terms: list[str] = field(default_factory=list)
    research_queries: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    confidence: float = 0.5
    method: str = "rules"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "corrected_query": self.corrected_query,
            "intent": self.intent,
            "topic_class": self.topic_class,
            "keywords": self.keywords,
            "company_names": self.company_names,
            "tickers": self.tickers,
            "aliases": self.aliases,
            "industry_terms": self.industry_terms,
            "product_terms": self.product_terms,
            "event_terms": self.event_terms,
            "research_focus_terms": self.research_focus_terms,
            "research_queries": self.research_queries,
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
        subject = _primary_query_subject(self)
        scoped_keywords = _scope_terms(subject, _crawl_keyword_dimensions(self))
        scoped_products = _scope_terms(subject, self.product_terms[:6])
        scoped_focus = _scope_terms(subject, self.research_focus_terms[:8])
        scoped_events = _scope_terms(subject, self.event_terms[:8])
        terms = [
            subject,
            *company_names,
            *tickers,
            *aliases,
            *scoped_keywords,
            *scoped_products,
            *scoped_focus,
            *scoped_events,
        ]
        if self.topic_class == "raw_material_chain":
            terms = [subject, *_material_chain_crawl_terms(subject, self), *terms]
        return _unique_terms(terms, limit=24)

    @property
    def search_keywords(self) -> list[str]:
        subject = _primary_query_subject(self)
        terms = [
            self.corrected_query,
            subject,
            *self.keywords,
            *self.company_names,
            *self.tickers,
            *self.aliases,
            *self.industry_terms,
            *_scope_terms(subject, self.product_terms),
            *_scope_terms(subject, self.research_focus_terms),
            *_scope_terms(subject, self.event_terms),
        ]
        if self.topic_class == "raw_material_chain":
            terms = [subject, *_material_chain_crawl_terms(subject, self), *terms]
        return _unique_terms(terms, limit=48)

    @property
    def collection_queries(self) -> list[str]:
        """Exactly four focused queries for the bounded public-information pass."""
        queries = _unique_terms(self.research_queries, limit=4)
        return queries if len(queries) == 4 else _default_research_queries(self)


def expand_user_query(
    query: str,
    *,
    company_map: str,
    enable_llm: bool = True,
    use_local_company_map: bool = True,
) -> QueryExpansion:
    base = _rule_expand_query(query, company_map=company_map, use_local_company_map=use_local_company_map)
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
            "如果 rule_guess.company_names 为空，说明规则没有识别出明确上市公司；此时不要猜股票代码，不要把历史上下文里的公司塞进 company_names/tickers。",
            "关键词必须围绕 user_query 的原始主体，不要加入与原问题无关的固定样例词，例如光模块、800G、中际旭创等，除非用户问题明确包含这些词。",
            "用于抓取的关键词应是企业/话题主体或“主体 + 维度”的短语，例如“东财科技 财报”“东财科技 研报”“东财科技 风险”，不要输出单独的“财报”“订单”“风险”这类裸词作为核心关键词。",
            "如果 user_query 是某种材料、原料、零部件或工艺，例如电子布、铜箔、玻纤、光刻胶、覆铜板，请将 topic_class 设为 raw_material_chain，并扩展上游原料、中游制造、下游应用、相关上市公司、价格供需、产能瓶颈、替代难度。",
            "材料产业链主题要优先搜索“主体 上游原料”“主体 下游应用”“主体 产业链”“主体 价格”“主体 供需”“主体 上市公司”“主体 相关公司”，不要只搜索“主体 财报”。",
            "如果是上市公司，尽量保留公司简称、全称、股票代码、常见别名。",
            "如果是行业/板块，扩展上下游、产品、技术路线、事件催化词。",
            "如果用户关注产业链、上下游、瓶颈权力、卡位、核心零部件，请把检索改写为寻找：客户路径、供应链位置、产能约束、替代难度、财务结构、机构滞后六类证据。",
            "research_focus_terms 放入产业链瓶颈研究证据词，例如客户认证、前五大客户、供应商、产能、良率、扩产、替代、毛利率、存货、应收账款、机构覆盖、一致预期、价值量、认证周期。",
            "negative_terms放入可能造成误搜的词，不要放入keywords。",
        ],
        # The report workflow consumes exactly four of these focused queries.
        # Keep the instruction near the schema so every API provider receives it.
        "research_query_constraint": "Return exactly 4 short entity-scoped search queries: recent events, business/products, financial performance, and risks/industry chain.",
        "json_schema": {
            "corrected_query": "str",
            "intent": "company|sector|theme|claim|market|explain|unknown",
            "topic_class": "company|raw_material_chain|component_chain|industry_chain|concept|unknown",
            "keywords": ["str"],
            "company_names": ["str"],
            "tickers": ["str"],
            "aliases": ["str"],
            "industry_terms": ["str"],
            "product_terms": ["str"],
            "event_terms": ["str"],
            "research_focus_terms": ["str"],
            "research_queries": ["exactly 4 short, entity-scoped search queries"],
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


def _rule_expand_query(query: str, *, company_map: str, use_local_company_map: bool = True) -> QueryExpansion:
    clean_query = _clean_query(query)
    profiles: list[Any] = []
    if use_local_company_map:
        try:
            local_profiles = load_company_profiles(company_map)
        except Exception:
            local_profiles = []
    else:
        local_profiles = []
    try:
        external_profiles = resolve_query_profiles(clean_query, None)
    except Exception:
        external_profiles = []

    candidate_profiles = [*local_profiles, *external_profiles]
    seen_profile_keys: set[str] = set()
    candidate_profiles = [
        profile
        for profile in candidate_profiles
        if not (
            (_profile_key := str(getattr(profile, "ticker", "") or getattr(profile, "name", "")).strip().lower())
            in seen_profile_keys
            or seen_profile_keys.add(_profile_key)
        )
    ]
    profiles = []
    direct_profiles = [profile for profile in candidate_profiles if _profile_direct_mentioned(clean_query, profile)]
    exact_profiles = [profile for profile in candidate_profiles if _profile_exact_match(clean_query, profile)]
    if exact_profiles:
        profiles = exact_profiles
    elif direct_profiles:
        profiles = direct_profiles

    expansion = QueryExpansion(
        original_query=query,
        corrected_query=clean_query,
        intent="company" if profiles else _guess_intent(clean_query),
        topic_class=_classify_topic_class(clean_query),
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
    _apply_topic_class_expansion(expansion)
    expansion.research_focus_terms.extend(_bottleneck_research_terms(clean_query, expansion))
    _dedup_expansion(expansion)
    return expansion


def _profile_exact_match(query: str, profile: Any) -> bool:
    q = query.strip().lower()
    terms = [profile.name, profile.ticker or "", *((profile.aliases or []))]
    terms.extend([(profile.ticker or "").split(".")[0]])
    return any(term and str(term).strip().lower() == q for term in terms)


def _profile_direct_mentioned(query: str, profile: Any) -> bool:
    q = query.strip().lower()
    terms = [profile.name, profile.ticker or "", (profile.ticker or "").split(".")[0], *((profile.aliases or []))]
    normalized = [str(term).strip().lower() for term in terms if str(term).strip()]
    normalized.sort(key=len, reverse=True)
    return any(len(term) >= 2 and term in q for term in normalized)


def _profile_mentioned(query: str, profile: Any) -> bool:
    q = query.strip().lower()
    terms = [profile.name, profile.ticker or "", *((profile.aliases or []))]
    terms.extend([(profile.ticker or "").split(".")[0]])
    normalized = [str(term).strip().lower() for term in terms if str(term).strip()]
    normalized.sort(key=len, reverse=True)
    return any(len(term) >= 2 and term in q for term in normalized)


def _term_visible_in_query(term: str, query: str) -> bool:
    raw = str(term or "").strip()
    if not raw:
        return False
    q = str(query or "").lower()
    low = raw.lower()
    variants = {low}
    if "." in low:
        variants.add(low.split(".")[0])
    if low.endswith((".sz", ".sh", ".bj")):
        variants.add(low[:-3])
    return any(len(item) >= 2 and item in q for item in variants)


def _merge_expansion(base: QueryExpansion, result: dict[str, Any]) -> QueryExpansion:
    result_company_names = _list_field(result, "company_names")
    result_tickers = _list_field(result, "tickers")
    merged_intent = "explain" if base.intent == "explain" else str(result.get("intent") or base.intent)
    if base.company_names:
        # The user explicitly mentioned a target company. Keep that identity
        # locked; LLM-added companies should be searched as terms only, not
        # promoted into the target company list.
        result_company_names = [name for name in result_company_names if name in base.company_names]
        result_tickers = [ticker for ticker in result_tickers if ticker in base.tickers]
    else:
        # If rules did not identify a company, do not let the LLM inject an
        # unrelated listed company from prior context. Keep only names/codes
        # that are visibly present in the user's query/corrected query.
        identity_text = f"{base.original_query} {result.get('corrected_query') or base.corrected_query}"
        result_company_names = [name for name in result_company_names if _term_visible_in_query(name, identity_text)]
        result_tickers = [ticker for ticker in result_tickers if _term_visible_in_query(ticker, identity_text)]
    merged = QueryExpansion(
        original_query=base.original_query,
        corrected_query=str(result.get("corrected_query") or base.corrected_query).strip() or base.corrected_query,
        intent=merged_intent,
        topic_class=str(result.get("topic_class") or base.topic_class),
        keywords=[*base.keywords, *_list_field(result, "keywords")],
        company_names=[*base.company_names, *result_company_names],
        tickers=[*base.tickers, *result_tickers],
        aliases=[*base.aliases, *_list_field(result, "aliases")],
        industry_terms=[*base.industry_terms, *_list_field(result, "industry_terms")],
        product_terms=[*base.product_terms, *_list_field(result, "product_terms")],
        event_terms=[*base.event_terms, *_list_field(result, "event_terms")],
        research_focus_terms=[*base.research_focus_terms, *_list_field(result, "research_focus_terms")],
        research_queries=_list_field(result, "research_queries"),
        negative_terms=[*base.negative_terms, *_list_field(result, "negative_terms")],
        confidence=_float_field(result.get("confidence"), default=base.confidence),
        method="llm",
        warnings=base.warnings,
    )
    _dedup_expansion(merged)
    _apply_topic_class_expansion(merged)
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
    expansion.research_focus_terms = _unique_terms(expansion.research_focus_terms, limit=24)
    expansion.research_queries = _unique_terms(expansion.research_queries, limit=4)
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
    subject = _strip_question_suffix(query)
    if subject and subject not in terms:
        terms.insert(1 if terms else 0, subject)
    code_match = re.findall(r"\b[036]\d{5}(?:\.(?:SZ|SH))?\b", query.upper())
    terms.extend(code_match)
    return _unique_terms(terms, limit=12)


def _strip_question_suffix(query: str) -> str:
    text = _clean_query(query)
    text = re.split(r"(是什么东西|是什么|怎么样|如何|怎么看|有前景吗|有没有前景|有前景|前景如何|值得投资吗|能买吗|好不好)", text, maxsplit=1)[0].strip(" ，,。？?！!")
    patterns = [
        r"(怎么样|如何|怎么看|能买吗|值得买吗|值得投资吗|有没有机会|好不好|行不行)[？?。!！]*$",
        r"(是什么|是谁|吗|呢)[？?。!！]*$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text).strip()
    return text if 1 < len(text) < len(query) else ""


def _primary_query_subject(expansion: QueryExpansion) -> str:
    for value in [*expansion.company_names[:1], *expansion.aliases[:1]]:
        clean = _strip_question_suffix(value) or _clean_query(value)
        if _is_generic_query_word(clean):
            continue
        if clean:
            return clean
    stripped = _strip_question_suffix(expansion.corrected_query) or _strip_question_suffix(expansion.original_query)
    if stripped:
        return stripped
    for value in [*expansion.keywords[1:2], expansion.corrected_query]:
        clean = _strip_question_suffix(value) or _clean_query(value)
        if _is_generic_query_word(clean):
            continue
        if clean:
            return clean
    return _clean_query(expansion.corrected_query or expansion.original_query)


def _default_research_queries(expansion: QueryExpansion) -> list[str]:
    """Create four bounded retrieval queries if LLM expansion is unavailable."""
    subject = _primary_query_subject(expansion)
    if expansion.topic_class in {"raw_material_chain", "component_chain", "industry_chain"}:
        dimensions = ["产业链 上游 下游", "供需 价格 产能", "上市公司 技术进展", "政策 风险"]
    else:
        dimensions = ["最新公告 近期动态", "主营业务 产品 客户", "财报 业绩 毛利率", "风险 竞争 产业链"]
    return _unique_terms([f"{subject} {dimension}".strip() for dimension in dimensions], limit=4)


def _crawl_keyword_dimensions(expansion: QueryExpansion) -> list[str]:
    terms: list[str] = []
    for term in expansion.keywords:
        clean = _strip_question_suffix(term) or _clean_query(term)
        if not clean or _is_generic_query_word(clean):
            continue
        terms.append(clean)
    if any(word in expansion.original_query for word in ["是什么", "是什么东西", "怎么样", "如何"]):
        terms.extend(["是什么", "行业", "市场", "产业链", "上市公司"])
    if any(word in expansion.original_query for word in ["前景", "机会", "空间"]):
        terms.extend(["前景", "市场空间", "景气度", "供需", "价格"])
    if any(word in expansion.original_query for word in ["投资", "能买吗", "值得"]):
        terms.extend(["研报", "估值", "风险", "盈利预测"])
    return _unique_terms(terms, limit=10)


def _is_generic_query_word(value: str) -> bool:
    return _clean_query(value) in {
        "怎么样",
        "如何",
        "怎么看",
        "有前景吗",
        "有前景",
        "前景",
        "是什么",
        "是什么东西",
        "吗",
        "呢",
    }


def _scope_terms(subject: str, terms: list[str]) -> list[str]:
    subject = _clean_query(subject)
    if not subject:
        return terms
    scoped: list[str] = []
    for term in terms:
        clean = _clean_query(term)
        if not clean:
            continue
        if clean == subject or subject in clean:
            scoped.append(clean)
        else:
            scoped.append(f"{subject} {clean}")
    return scoped


def _guess_intent(query: str) -> str:
    if _looks_like_definition_question(query):
        return "explain"
    if re.search(r"\b[036]\d{5}(?:\.(?:SZ|SH))?\b", query.upper()):
        return "company"
    if _classify_topic_class(query) in {"raw_material_chain", "component_chain", "industry_chain"}:
        return "theme"
    if _looks_like_bottleneck_research(query):
        return "theme"
    if any(word in query for word in ["板块", "行业", "产业链", "主题"]):
        return "sector"
    if any(word in query for word in ["上涨", "下跌", "业绩", "真实吗", "是否"]):
        return "claim"
    return "unknown"


def _looks_like_definition_question(query: str) -> bool:
    definition_terms = ["是什么", "是什么东西", "什么意思", "介绍一下", "科普", "定义", "概念"]
    investment_terms = ["投资", "机会", "瓶颈", "卡位", "受益", "标的", "公司", "买", "估值", "研报"]
    return any(term in query for term in definition_terms) and not any(term in query for term in investment_terms)


def _classify_topic_class(query: str) -> str:
    subject = _strip_question_suffix(query) or _clean_query(query)
    if _material_template_key(subject):
        return "raw_material_chain"
    if any(word in query for word in ["原材料", "材料", "原料", "化工品", "金属", "玻纤", "电子布", "铜箔", "光刻胶"]):
        return "raw_material_chain"
    if any(word in query for word in ["零部件", "组件", "器件", "模组", "设备", "工艺"]):
        return "component_chain"
    if any(word in query for word in ["产业链", "上下游", "行业链条"]):
        return "industry_chain"
    return "unknown"


def _apply_topic_class_expansion(expansion: QueryExpansion) -> None:
    if expansion.topic_class != "raw_material_chain":
        return
    subject = _primary_query_subject(expansion)
    key = _material_template_key(subject)
    template = MATERIAL_CHAIN_TEMPLATES.get(key or "", {})
    aliases = template.get("aliases") or []
    upstream = template.get("upstream") or []
    midstream = template.get("midstream") or []
    downstream = template.get("downstream") or []
    companies = template.get("companies") or []

    expansion.keywords.extend(
        [
            f"{subject} 是什么",
            f"{subject} 产业链",
            f"{subject} 上游原料",
            f"{subject} 下游应用",
            f"{subject} 价格",
            f"{subject} 供需",
            f"{subject} 市场空间",
            f"{subject} 上市公司",
            *aliases,
            *companies,
        ]
    )
    expansion.industry_terms.extend(["原材料", "电子材料", "产业链", "覆铜板", "PCB"])
    expansion.product_terms.extend([*aliases, *upstream, *midstream, *downstream])
    expansion.research_focus_terms.extend(
        [
            "上游原料",
            "中游制造",
            "下游应用",
            "相关上市公司",
            "供需格局",
            "价格周期",
            "产能扩张",
            "进口替代",
            "技术壁垒",
            "单位价值量",
            "毛利率",
            "库存",
        ]
    )


def _material_chain_crawl_terms(subject: str, expansion: QueryExpansion) -> list[str]:
    key = _material_template_key(subject)
    template = MATERIAL_CHAIN_TEMPLATES.get(key or "", {})
    terms = [
        f"{subject} 是什么",
        f"{subject} 产业链",
        f"{subject} 上游原料",
        f"{subject} 下游应用",
        f"{subject} 相关公司",
        f"{subject} 上市公司",
        f"{subject} 价格",
        f"{subject} 供需",
        f"{subject} 市场空间",
        f"{subject} 产能",
        f"{subject} 研报",
        f"{subject} 风险",
    ]
    for group in ["upstream", "midstream", "downstream", "companies"]:
        for item in (template.get(group) or [])[:5]:
            if _clean_query(item) == _clean_query(subject):
                continue
            terms.append(f"{subject} {item}")
    return _unique_terms(terms, limit=28)


def _material_template_key(subject: str) -> str | None:
    clean = _clean_query(subject)
    for key, data in MATERIAL_CHAIN_TEMPLATES.items():
        aliases = data.get("aliases") or []
        if key in clean or any(alias and alias in clean for alias in aliases):
            return key
    return None


def _default_event_terms(query: str) -> list[str]:
    terms = ["公告", "财报", "研报", "业绩", "订单", "客户认证", "扩产", "产品进展", "风险"]
    if any(word in query for word in ["光模块", "中际旭创", "新易盛"]):
        terms.extend(["800G", "1.6T", "硅光", "CPO", "LPO", "AI数据中心"])
    if any(word in query for word in ["工业富联", "服务器", "算力"]):
        terms.extend(["AI服务器", "算力", "英伟达", "云厂商", "液冷"])
    return terms


def _looks_like_bottleneck_research(query: str) -> bool:
    return any(
        word in query
        for word in [
            "产业链",
            "上下游",
            "瓶颈",
            "卡位",
            "核心零部件",
            "零部件",
            "供应链",
            "价值量",
            "替代难度",
            "谁受益",
            "谁最受益",
            "真正受益",
        ]
    )


def _bottleneck_research_terms(query: str, expansion: QueryExpansion) -> list[str]:
    if not _looks_like_bottleneck_research(query):
        return []

    target_terms = [*expansion.company_names[:1], *expansion.tickers[:1]]
    product_terms = expansion.product_terms[:8]
    dimension_terms = [
        "客户路径",
        "供应链位置",
        "产能约束",
        "替代难度",
        "财务结构",
        "机构滞后",
    ]
    evidence_terms = [
        "上游供应商",
        "下游客户",
        "前五大客户",
        "前五大供应商",
        "客户认证",
        "大客户订单",
        "产能利用率",
        "扩产",
        "良率",
        "交付能力",
        "认证周期",
        "单机价值量",
        "国产替代",
        "毛利率",
        "经营现金流",
        "存货",
        "应收账款",
        "资本开支",
        "机构覆盖",
        "一致预期",
        "盈利预测",
        "研报观点",
    ]
    combined_queries: list[str] = []
    primary = target_terms[0] if target_terms else query
    for item in ["产业链瓶颈", "上下游", "核心零部件", "供应商 客户", "产能 良率", "毛利率 现金流", "研报 一致预期"]:
        combined_queries.append(f"{primary} {item}")
    return _unique_terms([*combined_queries, *dimension_terms, *evidence_terms, *product_terms], limit=32)


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
