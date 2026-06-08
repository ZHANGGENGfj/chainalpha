from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from chainalpha.connectors.base import ConnectorConfig, FetchRequest
from chainalpha.connectors.document_adapter import source_documents_to_chunks
from chainalpha.connectors.registry import SourceManager, load_connector_configs
from chainalpha.investment.a_share_reports import fetch_a_share_report_documents
from chainalpha.investment.candidates import build_investment_candidates
from chainalpha.investment.company_map import (
    canonicalize_nodes,
    load_company_profiles,
    search_company_profiles,
)
from chainalpha.investment.company_resolver import resolve_query_profiles
from chainalpha.investment.market_data import (
    derive_financial_snapshots,
    derive_price_snapshots,
    load_financial_snapshots,
    load_price_snapshots,
    lookup_snapshot,
)
from chainalpha.investment.news_enrichment import enrich_valuable_news_documents
from chainalpha.investment.topic_valuation import build_target_valuation_view
from chainalpha.memory.index import HybridIndex
from chainalpha.ontology.industry_graph import IndustryGraph
from chainalpha.pipeline import analyze_chunks
from chainalpha.schemas import (
    CompanyProfile,
    FinancialSnapshot,
    InvestmentCandidate,
    PriceSnapshot,
    SourceDocument,
    WeakSignal,
)


POSITIVE_EVENT_HINTS = {
    "ORDER_SIGNED",
    "CUSTOMER_CERTIFICATION",
    "CAPACITY_EXPANSION",
    "FINANCIAL_VALIDATION",
    "GROSS_MARGIN_CHANGE",
    "PRODUCT_LAUNCH",
    "TECH_UPGRADE",
    "POLICY_SUPPORT",
    "PRICE_INCREASE",
}

NEGATIVE_EVENT_HINTS = {
    "PRICE_DECREASE",
    "CAPEX_DECREASE",
    "ATTENTION_HOT",
}


@dataclass
class ResearchIntent:
    input_type: str
    target: str
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_type": self.input_type,
            "target": self.target,
            "reasoning": self.reasoning,
        }


@dataclass
class TaskPlanItem:
    title: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
        }


@dataclass
class ScenarioView:
    horizon: str
    optimistic: str
    base: str
    pessimistic: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon, 
            "optimistic": self.optimistic,
            "base": self.base,
            "pessimistic": self.pessimistic,
        }


@dataclass
class TopicCompanyView:
    company: CompanyProfile
    match_score: float
    financial: FinancialSnapshot | None = None
    price: PriceSnapshot | None = None
    business_summary: str = ""
    key_products: list[str] = field(default_factory=list)
    upstream_nodes: list[str] = field(default_factory=list)
    downstream_nodes: list[str] = field(default_factory=list)
    upstream_companies: list[str] = field(default_factory=list)
    downstream_companies: list[str] = field(default_factory=list)
    a_share_reports: list[SourceDocument] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": {
                "name": self.company.name,
                "ticker": self.company.ticker,
                "exchange": self.company.exchange,
                "industry": self.company.industry,
                "nodes": self.company.nodes,
                "aliases": self.company.aliases,
                "role": self.company.role,
                "watch_tags": self.company.watch_tags,
                "metadata": self.company.metadata,
            },
            "match_score": self.match_score,
            "financial": self.financial.__dict__ if self.financial else None,
            "price": self.price.__dict__ if self.price else None,
            "business_summary": self.business_summary,
            "key_products": self.key_products,
            "upstream_nodes": self.upstream_nodes,
            "downstream_nodes": self.downstream_nodes,
            "upstream_companies": self.upstream_companies,
            "downstream_companies": self.downstream_companies,
            "a_share_reports": [doc.to_dict() for doc in self.a_share_reports],
        }


@dataclass
class TopicResearchReport:
    topic: str
    generated_at: str
    intent: ResearchIntent
    query_expansion: dict[str, Any] = field(default_factory=dict)
    planned_tasks: list[TaskPlanItem] = field(default_factory=list)
    matched_nodes: list[str] = field(default_factory=list)
    upstream_nodes: list[str] = field(default_factory=list)
    downstream_nodes: list[str] = field(default_factory=list)
    recent_news: list[SourceDocument] = field(default_factory=list)
    recent_documents: list[SourceDocument] = field(default_factory=list)
    selected_documents: list[SourceDocument] = field(default_factory=list)
    signals: list[WeakSignal] = field(default_factory=list)
    companies: list[TopicCompanyView] = field(default_factory=list)
    candidates: list[InvestmentCandidate] = field(default_factory=list)
    current_snapshot: list[str] = field(default_factory=list)
    core_logic: list[str] = field(default_factory=list)
    valuation_view: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"
    recommendation: str = "继续跟踪"
    scenarios: list[ScenarioView] = field(default_factory=list)
    tracking_indicators: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "generated_at": self.generated_at,
            "intent": self.intent.to_dict(),
            "query_expansion": self.query_expansion,
            "planned_tasks": [item.to_dict() for item in self.planned_tasks],
            "matched_nodes": self.matched_nodes,
            "upstream_nodes": self.upstream_nodes,
            "downstream_nodes": self.downstream_nodes,
            "recent_news": [doc.to_dict() for doc in self.recent_news],
            "recent_documents": [doc.to_dict() for doc in self.recent_documents],
            "selected_documents": [doc.to_dict() for doc in self.selected_documents],
            "signals": [signal.to_dict() for signal in self.signals],
            "companies": [row.to_dict() for row in self.companies],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "current_snapshot": self.current_snapshot,
            "core_logic": self.core_logic,
            "valuation_view": self.valuation_view,
            "sentiment_score": self.sentiment_score,
            "sentiment_label": self.sentiment_label,
            "recommendation": self.recommendation,
            "scenarios": [item.to_dict() for item in self.scenarios],
            "tracking_indicators": self.tracking_indicators,
            "warnings": self.warnings,
        }


def build_topic_research_report(
    *,
    topic: str,
    config: str | Path,
    company_map: str | Path,
    financials: str | Path | None = None,
    prices: str | Path | None = None,
    limit: int = 80,
    top_k_chunks: int = 60,
    top_k_docs: int = 12,
    top_k_companies: int = 12,
    top_k_candidates: int = 8,
    include_a_share_reports: bool = False,
    focus_target_company: bool = False,
    media_crawler_root: str | Path | None = None,
    media_crawler_platforms: list[str] | None = None,
    search_keywords: list[str] | None = None,
    query_expansion: dict[str, Any] | None = None,
) -> TopicResearchReport:
    profiles = resolve_query_profiles(topic, load_company_profiles(company_map))
    intent = _classify_intent(topic, profiles)
    planned_tasks = _build_task_plan(intent)

    manager = _build_dynamic_source_manager(
        config,
        profiles,
        intent,
        topic,
        media_crawler_root=media_crawler_root,
        media_crawler_platforms=media_crawler_platforms,
    )
    expanded_terms = [term for term in (search_keywords or []) if term]
    expanded_query = " ".join([topic, *expanded_terms[:16]]).strip()
    docs, results = manager.fetch_all(FetchRequest(query=topic, keywords=expanded_terms, limit=limit))
    warnings = [*manager.errors]
    for result in results:
        warnings.extend(f"{result.source_name}: {err}" for err in result.errors)

    valuable_news, news_warnings = enrich_valuable_news_documents(topic, docs)
    warnings.extend(news_warnings)
    if valuable_news:
        enriched_ids = {doc.id for doc in valuable_news}
        docs = [*valuable_news, *[doc for doc in docs if doc.id not in enriched_ids]]

    sorted_docs = _sort_documents(docs)
    chunks = source_documents_to_chunks(sorted_docs)
    selected_docs = _select_documents(expanded_query or topic, chunks, sorted_docs, top_k_docs)
    signals, _, _ = analyze_chunks(chunks, query=expanded_query or topic, top_k_chunks=top_k_chunks, out_dir=None, store=None)

    graph = IndustryGraph.load_ai_graph()
    matched_nodes = canonicalize_nodes(
        graph.match_nodes(topic + " " + " ".join(doc.title for doc in selected_docs[:6]))
    )
    related_nodes = canonicalize_nodes(
        matched_nodes
        + [node for signal in signals for node in (signal.event.supply_chain_nodes + signal.hypothesis.causal_path)]
    )
    upstream_nodes = canonicalize_nodes(graph.expand(related_nodes[:6], depth=2, direction="backward"))
    downstream_nodes = canonicalize_nodes(graph.expand(related_nodes[:6], depth=2, direction="forward"))

    financial_map = load_financial_snapshots(financials)
    price_map = load_price_snapshots(prices)
    if not financial_map:
        financial_map = derive_financial_snapshots(sorted_docs, profiles)
    if not price_map:
        price_map = derive_price_snapshots(sorted_docs, profiles)

    company_views = _find_related_companies(
        topic=topic,
        intent=intent,
        profiles=profiles,
        financials=financial_map,
        prices=price_map,
        matched_nodes=related_nodes + upstream_nodes + downstream_nodes,
        top_k=top_k_companies,
        focus_target_company=focus_target_company,
    )

    a_share_reports: dict[str, list[SourceDocument]] = {}
    if include_a_share_reports:
        a_share_reports, a_share_warnings = fetch_a_share_report_documents([row.company for row in company_views])
        warnings.extend(a_share_warnings)
    else:
        warnings.append("A-share filings/PDF parsing disabled. Use --include-a-share-reports to enable financial report parsing.")
    report_docs = [doc for docs in a_share_reports.values() for doc in docs]
    if report_docs:
        report_financials = derive_financial_snapshots(report_docs, [row.company for row in company_views])
        for key, snapshot in report_financials.items():
            if key not in financial_map:
                financial_map[key] = snapshot

    company_views = [
        _enrich_company_view(
            TopicCompanyView(
                company=row.company,
                match_score=row.match_score,
                financial=lookup_snapshot(financial_map, row.company.name, row.company.ticker),
                price=lookup_snapshot(price_map, row.company.name, row.company.ticker),
            ),
            profiles=profiles,
            graph=graph,
            report_docs=a_share_reports.get(_profile_key(row.company), []),
        )
        for row in company_views
    ]

    candidates = build_investment_candidates(
        signals,
        [row.company for row in company_views],
        financials=financial_map,
        prices=price_map,
        top_k=top_k_candidates,
    )

    current_snapshot = _build_current_snapshot(
        intent=intent,
        matched_nodes=related_nodes,
        docs=selected_docs,
        companies=company_views,
        signals=signals,
    )
    core_logic = _build_core_logic(related_nodes, upstream_nodes, downstream_nodes, company_views, signals)
    valuation_view = build_target_valuation_view(company_views, candidates)
    sentiment_score, sentiment_label = _build_sentiment(signals, company_views)
    recommendation = _build_recommendation(candidates, signals, company_views)
    scenarios = _build_scenarios(intent, related_nodes, candidates, recommendation)
    tracking_indicators = _build_tracking_indicators(intent, related_nodes, company_views, signals)

    return TopicResearchReport(
        topic=topic,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        intent=intent,
        planned_tasks=planned_tasks,
        matched_nodes=related_nodes,
        upstream_nodes=upstream_nodes,
        downstream_nodes=downstream_nodes,
        recent_news=valuable_news,
        recent_documents=sorted_docs[:top_k_docs],
        selected_documents=selected_docs[:top_k_docs],
        signals=signals[:top_k_chunks],
        companies=company_views,
        candidates=candidates,
        current_snapshot=current_snapshot,
        core_logic=core_logic,
        valuation_view=valuation_view,
        sentiment_score=sentiment_score,
        sentiment_label=sentiment_label,
        recommendation=recommendation,
        scenarios=scenarios,
        tracking_indicators=tracking_indicators,
        warnings=warnings,
        query_expansion=query_expansion or {},
    )


def render_topic_research_markdown(report: TopicResearchReport) -> str:
    lines = [
        f"# Topic Research: {report.topic}",
        "",
        f"> Generated at: {report.generated_at}",
        f"> Intent: {report.intent.input_type}",
        f"> Recommendation: {report.recommendation}",
        "",
    ]
    if report.warnings:
        lines.extend(["## Warnings", ""])
        for item in report.warnings[:10]:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(["## Task Plan", ""])
    for item in report.planned_tasks:
        lines.append(f"- {item.title}: {item.description}")
    lines.append("")

    lines.extend(["## Current Snapshot", ""])
    for item in report.current_snapshot:
        lines.append(f"- {item}")
    lines.append("")

    lines.extend(["## Industry Chain", ""])
    lines.append(f"- Matched nodes: {', '.join(report.matched_nodes) or '-'}")
    lines.append(f"- Upstream nodes: {', '.join(report.upstream_nodes) or '-'}")
    lines.append(f"- Downstream nodes: {', '.join(report.downstream_nodes) or '-'}")
    lines.append("")

    lines.extend(["## Sentiment", ""])
    lines.append(f"- Score: {report.sentiment_score:.2f}")
    lines.append(f"- Label: {report.sentiment_label}")
    lines.append("")

    lines.extend(["## Hot News / Recent Events", ""])
    for item in _hot_news_items(report):
        lines.append(f"- {item}")
    lines.append("")

    lines.extend(["## Related Companies", ""])
    if not report.companies:
        lines.append("- No companies matched.")
    for idx, row in enumerate(report.companies[:10], 1):
        lines.append(f"### {idx}. {row.company.name} ({row.company.ticker or '-'})")
        lines.append(f"- Match score: {row.match_score:.2f}")
        lines.append(f"- Business: {row.business_summary or row.company.role or '-'}")
        lines.append(f"- Industry: {row.company.industry or '-'}")
        lines.append(f"- Chain nodes: {', '.join(row.company.nodes) or '-'}")
        lines.append(f"- Key products: {', '.join(row.key_products) or '-'}")
        lines.append(f"- Upstream nodes: {', '.join(row.upstream_nodes) or '-'}")
        lines.append(f"- Upstream companies: {', '.join(row.upstream_companies[:8]) or '-'}")
        lines.append(f"- Downstream nodes: {', '.join(row.downstream_nodes) or '-'}")
        lines.append(f"- Downstream companies: {', '.join(row.downstream_companies[:8]) or '-'}")
        lines.append(f"- Financial snapshot: {_financial_brief(row.financial)}")
        lines.append(f"- Price snapshot: {_price_brief(row.price)}")
        if row.a_share_reports:
            lines.append("- Latest filings:")
            for doc in row.a_share_reports[:5]:
                path = doc.url or doc.path or "-"
                published = doc.published_at.isoformat()[:10] if doc.published_at else "-"
                lines.append(f"  - {doc.title} | {published} | {path}")
        lines.append("")

    lines.extend(["## Valuation View", ""])
    for item in report.valuation_view:
        lines.append(f"- {item}")
    lines.append("")

    lines.extend(["## Scenarios", ""])
    for scenario in report.scenarios:
        lines.append(f"### {scenario.horizon}")
        lines.append(f"- Optimistic: {scenario.optimistic}")
        lines.append(f"- Base: {scenario.base}")
        lines.append(f"- Pessimistic: {scenario.pessimistic}")
        lines.append("")

    lines.extend(["## Tracking Indicators", ""])
    for item in report.tracking_indicators:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


# Final active override: target-company-first valuation view for news-only topic runs.
def _build_valuation_view(companies: list[TopicCompanyView], candidates: list[InvestmentCandidate]) -> list[str]:
    items: list[str] = []
    focus_rows = companies[:3]
    for row in focus_rows:
        financial = row.financial
        price = row.price
        items.append(f"{row.company.name}: 估值分析聚焦目标公司，不再把产业链相关公司简单并列为投资候选。")

        facts: list[str] = []
        if financial:
            if financial.period:
                facts.append(f"报告期={financial.period}")
            if financial.revenue_growth is not None:
                facts.append(f"营收增速={financial.revenue_growth:.1f}%")
            if financial.profit_growth is not None:
                facts.append(f"利润增速={financial.profit_growth:.1f}%")
            if financial.gross_margin is not None:
                facts.append(f"毛利率={financial.gross_margin:.1f}%")
            if financial.roe is not None:
                facts.append(f"ROE={financial.roe:.1f}%")
            if financial.market_cap is not None:
                facts.append(f"市值={financial.market_cap:.1f}")
            valuation_parts = []
            if financial.pe is not None:
                valuation_parts.append(f"PE={financial.pe:.1f}")
            if financial.pb is not None:
                valuation_parts.append(f"PB={financial.pb:.1f}")
            if financial.ps is not None:
                valuation_parts.append(f"PS={financial.ps:.1f}")
            facts.append("估值倍数=" + (", ".join(valuation_parts) if valuation_parts else "缺失"))
        else:
            facts.append("结构化财务数据缺失")

        if price:
            price_parts = []
            if price.date:
                price_parts.append(price.date)
            if price.close is not None:
                price_parts.append(f"收盘价={price.close:.2f}")
            if price.change_1m is not None:
                price_parts.append(f"1M={price.change_1m:.1f}%")
            if price.change_3m is not None:
                price_parts.append(f"3M={price.change_3m:.1f}%")
            if price.change_6m is not None:
                price_parts.append(f"6M={price.change_6m:.1f}%")
            if price.change_12m is not None:
                price_parts.append(f"12M={price.change_12m:.1f}%")
            facts.append("行情=" + (", ".join(price_parts) if price_parts else "缺失"))
        else:
            facts.append("结构化行情数据缺失")

        items.append(f"{row.company.name}: {'；'.join(facts)}。")
        if financial is None and price is None:
            items.extend(
                [
                    "当前仅启用新闻/消息输入，不能直接判断估值便宜或昂贵；只能判断消息是否可能改变盈利预期或风险偏好。",
                    "估值数据缺口：当前市值、PE TTM、PB、PS、过去3-5年估值分位、未来2-3年利润预测、同行估值表。",
                    "消息驱动估值框架：订单/客户认证/产品放量/毛利改善属于盈利上修线索；单纯热度新闻属于情绪催化，不能直接外推估值空间。",
                    "情景估值框架：乐观情景=订单持续兑现且估值维持；中性情景=业绩正常增长但估值回归中枢；悲观情景=订单或毛利不及预期导致业绩和估值双杀。",
                ]
            )
        else:
            items.append(f"{row.company.name}: {_valuation_judgement(financial, price)}")

    if candidates:
        top = candidates[0]
        if top.company.name in {row.company.name for row in focus_rows}:
            items.append(f"{top.company.name}: 候选池评分仅作为辅助，仍需用目标公司估值倍数、盈利预测和新闻证据闭环复核。")

    if not items:
        items.append("估值信息缺失：未识别到目标公司。请先确认公司名称/股票代码，并补充新闻、行情和估值数据。")
    return items


def render_professional_topic_report(report: TopicResearchReport) -> str:
    lines = [
        f"# {report.topic} 研究报告",
        "",
        f"> 生成时间：{report.generated_at}",
        f"> 输入类型：{report.intent.input_type}",
        f"> 当前建议等级：{report.recommendation}",
        "> 说明：本报告由 ChainAlpha 基于公开资料、产业链图谱、结构化信号、财报材料与规则推理自动生成，仅用于研究支持，不构成投资建议。",
        "",
        "## 一、Executive Summary",
        "",
        _render_executive_summary(report),
        "",
        "## 二、任务理解与研究框架",
        "",
        f"- 输入识别：{report.intent.input_type}",
        f"- 识别依据：{report.intent.reasoning}",
    ]
    for item in report.planned_tasks:
        lines.append(f"- {item.title}：{item.description}")

    lines.extend(["", "## 三、当前基本情况", ""])
    for item in report.current_snapshot:
        lines.append(f"- {item}")

    lines.extend(["", "## 四、核心逻辑与产业链传导", ""])
    for item in report.core_logic:
        lines.append(f"- {item}")
    lines.append("")
    lines.append(f"- 核心节点：{', '.join(report.matched_nodes) or '-'}")
    lines.append(f"- 上游环节：{', '.join(report.upstream_nodes) or '-'}")
    lines.append(f"- 下游环节：{', '.join(report.downstream_nodes) or '-'}")

    lines.extend(["", "## 五、财务与估值分析", ""])
    for item in report.valuation_view:
        lines.append(f"- {item}")

    lines.extend(["", "## 六、市场情绪与资金面线索", ""])
    lines.append(f"- 情绪分数：{report.sentiment_score:.2f}")
    lines.append(f"- 情绪标签：{report.sentiment_label}")
    if report.signals:
        for signal in report.signals[:5]:
            lines.append(
                f"- {signal.title} | event={signal.event.event_type} | score={signal.weak_signal_score:.2f} | attention={signal.attention_score:.1f}"
            )
    else:
        lines.append("- 当前缺少足够强的事件信号，情绪判断以中性偏谨慎为主。")

    lines.extend(["", "## 七、重点公司与财报材料", ""])
    if not report.companies:
        lines.append("- 当前未匹配到重点公司。")
    else:
        for idx, row in enumerate(report.companies[:8], 1):
            lines.append(f"### {idx}. {row.company.name} ({row.company.ticker or '-'})")
            lines.append("")
            lines.append(f"- 业务定位：{row.business_summary or row.company.role or '-'}")
            lines.append(f"- 核心产品/能力：{', '.join(row.key_products) or '-'}")
            lines.append(f"- 上游环节：{', '.join(row.upstream_nodes) or '-'}")
            lines.append(f"- 上游公司：{', '.join(row.upstream_companies) or '-'}")
            lines.append(f"- 下游环节：{', '.join(row.downstream_nodes) or '-'}")
            lines.append(f"- 下游公司：{', '.join(row.downstream_companies) or '-'}")
            lines.append(f"- 财务观察：{_financial_brief(row.financial)}")
            lines.append(f"- 股价观察：{_price_brief(row.price)}")
            if row.a_share_reports:
                lines.append("- A股财报/公告材料：")
                for doc in row.a_share_reports[:4]:
                    path = doc.url or doc.path or "-"
                    published = doc.published_at.isoformat()[:10] if doc.published_at else "-"
                    lines.append(f"  - {doc.title} | {published} | {path}")
            else:
                lines.append("- A股财报/公告材料：当前未抓取到。")
            lines.append("")

    lines.extend(["## 八、未来预测与情景分析", ""])
    for scenario in report.scenarios:
        lines.append(f"### {scenario.horizon}")
        lines.append("")
        lines.append(f"- 乐观情景：{scenario.optimistic}")
        lines.append(f"- 中性情景：{scenario.base}")
        lines.append(f"- 悲观情景：{scenario.pessimistic}")
        lines.append("")

    lines.extend(["## 九、关键风险", ""])
    for item in _collect_report_risks(report):
        lines.append(f"- {item}")

    lines.extend(["", "## 十、跟踪指标", ""])
    for item in report.tracking_indicators:
        lines.append(f"- {item}")

    lines.extend([
        "",
        "## 十一、结论与建议",
        "",
        _render_conclusion(report),
        "",
        "## 免责声明",
        "",
        "本报告仅用于信息整理、产业研究与投研辅助，不构成任何投资建议；涉及公告、财报与价格信息的结论，仍需结合原始材料进一步复核。",
        "",
    ])
    return "\n".join(lines)


# Final override: target-company-first valuation view for news-only topic runs.
def _build_valuation_view(companies: list[TopicCompanyView], candidates: list[InvestmentCandidate]) -> list[str]:
    items: list[str] = []
    focus_rows = companies[:3]
    for row in focus_rows:
        financial = row.financial
        price = row.price
        items.append(f"{row.company.name}: 估值分析聚焦目标公司，不再把产业链相关公司简单并列为投资候选。")

        facts: list[str] = []
        if financial:
            if financial.period:
                facts.append(f"报告期={financial.period}")
            if financial.revenue_growth is not None:
                facts.append(f"营收增速={financial.revenue_growth:.1f}%")
            if financial.profit_growth is not None:
                facts.append(f"利润增速={financial.profit_growth:.1f}%")
            if financial.gross_margin is not None:
                facts.append(f"毛利率={financial.gross_margin:.1f}%")
            if financial.roe is not None:
                facts.append(f"ROE={financial.roe:.1f}%")
            if financial.market_cap is not None:
                facts.append(f"市值={financial.market_cap:.1f}")
            valuation_parts = []
            if financial.pe is not None:
                valuation_parts.append(f"PE={financial.pe:.1f}")
            if financial.pb is not None:
                valuation_parts.append(f"PB={financial.pb:.1f}")
            if financial.ps is not None:
                valuation_parts.append(f"PS={financial.ps:.1f}")
            facts.append("估值倍数=" + (", ".join(valuation_parts) if valuation_parts else "缺失"))
        else:
            facts.append("结构化财务数据缺失")

        if price:
            price_parts = []
            if price.date:
                price_parts.append(price.date)
            if price.close is not None:
                price_parts.append(f"收盘价={price.close:.2f}")
            if price.change_1m is not None:
                price_parts.append(f"1M={price.change_1m:.1f}%")
            if price.change_3m is not None:
                price_parts.append(f"3M={price.change_3m:.1f}%")
            if price.change_6m is not None:
                price_parts.append(f"6M={price.change_6m:.1f}%")
            if price.change_12m is not None:
                price_parts.append(f"12M={price.change_12m:.1f}%")
            facts.append("行情=" + (", ".join(price_parts) if price_parts else "缺失"))
        else:
            facts.append("结构化行情数据缺失")

        items.append(f"{row.company.name}: {'；'.join(facts)}。")
        if financial is None and price is None:
            items.extend(
                [
                    "当前仅启用新闻/消息输入，不能直接判断估值便宜或昂贵；只能判断消息是否可能改变盈利预期或风险偏好。",
                    "估值数据缺口：当前市值、PE TTM、PB、PS、过去3-5年估值分位、未来2-3年利润预测、同行估值表。",
                    "消息驱动估值框架：订单/客户认证/产品放量/毛利改善属于盈利上修线索；单纯热度新闻属于情绪催化，不能直接外推估值空间。",
                    "情景估值框架：乐观情景=订单持续兑现且估值维持；中性情景=业绩正常增长但估值回归中枢；悲观情景=订单或毛利不及预期导致业绩和估值双杀。",
                ]
            )
        else:
            items.append(f"{row.company.name}: {_valuation_judgement(financial, price)}")

    if candidates:
        top = candidates[0]
        if top.company.name in {row.company.name for row in focus_rows}:
            items.append(f"{top.company.name}: 候选池评分仅作为辅助，仍需用目标公司估值倍数、盈利预测和新闻证据闭环复核。")

    if not items:
        items.append("估值信息缺失：未识别到目标公司。请先确认公司名称/股票代码，并补充新闻、行情和估值数据。")
    return items


def render_professional_topic_report(report: TopicResearchReport) -> str:
    primary = _primary_company(report)
    title_name = primary.company.name if primary else report.topic
    lines = [
        f"# {title_name}投资研究报告",
        "",
        f"> 生成时间：{report.generated_at}",
        f"> 研究对象：{report.topic}",
        f"> 输入识别：{report.intent.input_type} -> {report.intent.target}",
        f"> 系统建议：{report.recommendation}",
        "> 说明：本报告基于系统已抓取的公开资料、公告/财报摘要、行情快照和产业链图谱自动生成，仅用于研究支持，不构成投资建议。",
        "",
        "## 核心结论",
        "",
        _render_executive_summary(report),
        "",
    ]
    _extend_section(lines, "事实校验", _fact_check_lines(report, primary))
    _extend_section(lines, "近期热点新闻与公告", _hot_news_items(report))
    _extend_section(lines, "公司商业模式拆解", _business_model_lines(primary))
    _extend_section(lines, "收入拆分与增长归因", _growth_attribution_lines(primary))
    _extend_section(lines, "产品结构与技术路线", _technology_route_lines(report, primary))
    _extend_section(lines, "客户结构与订单能见度", _customer_order_lines(primary))
    _extend_section(lines, "行业空间测算框架", _industry_space_model_lines(report))
    _extend_section(lines, "产业链价值分配", _value_chain_lines(report))
    _extend_section(lines, "竞争格局与可比公司", _competition_lines(report, primary))
    _extend_section(lines, "重点公司详表", _company_detail_lines(report))
    _extend_section(lines, "财务质量与现金流", _cashflow_quality_lines(primary))
    _extend_section(lines, "估值分析", report.valuation_view + _valuation_framework_lines(primary))
    _extend_section(lines, "三年盈利预测框架", _forecast_model_lines(primary))
    _extend_section(lines, "海外收入、汇率与贸易风险", _overseas_risk_lines(primary))
    _extend_section(lines, "市场预期差", _expectation_gap_lines(report, primary))
    _extend_section(lines, "股价驱动因素", _stock_driver_lines(primary))
    _extend_section(lines, "关键跟踪指标仪表盘", _tracking_dashboard_lines(report, primary))
    _extend_section(lines, "反方观点与证伪条件", _bear_case_lines(report, primary))
    _extend_section(lines, "评级体系", _rating_lines(report, primary))
    _extend_section(lines, "报告自检", _report_critic_lines(report, primary))
    _extend_section(lines, "主要风险", _collect_report_risks(report))
    lines.extend([
        "## 结论",
        "",
        _render_conclusion(report),
        "",
        "## 免责声明",
        "",
        "本报告仅用于信息整理、产业研究和投研辅助，不构成任何投资建议。涉及公告、财报、行情、估值和预测的信息仍需结合原始资料与专业数据库复核。",
        "",
    ])
    return "\n".join(lines)


def _extend_section(lines: list[str], title: str, items: list[str]) -> None:
    lines.extend([f"## {title}", ""])
    if not items:
        lines.append("- 数据缺口：当前资料不足。")
    else:
        for item in items:
            lines.append(item if item.startswith("|") else f"- {item}")
    lines.append("")


def _primary_company(report: TopicResearchReport) -> TopicCompanyView | None:
    if not report.companies:
        return None
    target = report.intent.target.strip().lower()
    exact = [
        row for row in report.companies
        if row.company.name.lower() == target or (row.company.ticker or "").lower() == target
    ]
    return exact[0] if exact else report.companies[0]


def _fact_check_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    lines = [
        f"报告期口径：系统生成时间为 {report.generated_at}；财务快照以各公司 `financial.period` 字段为准，若缺失则不得跨期比较。",
        "事实边界：本报告只使用已抓取 JSON、公告/财报摘要、产业链图谱和行情快照；缺失数据统一列为数据缺口。",
    ]
    if primary:
        lines.append(f"主营业务核验：{primary.company.name} 的业务定位为 `{primary.business_summary or primary.company.role or '数据缺口'}`，覆盖节点为 `{', '.join(primary.company.nodes) or '数据缺口'}`。")
        lines.append(f"产品/能力核验：系统识别到 `{', '.join(primary.key_products) or '数据缺口'}`；如果报告出现与这些节点无关的服务器、存储、安全等描述，需要人工复核是否混入同行模板。")
        peer_names = [row.company.name for row in report.companies[1:5]]
        if peer_names:
            lines.append(f"混淆项提醒：{', '.join(peer_names)} 仅可作为产业链/可比公司观察，不应把其主营业务直接写入 {primary.company.name}。")
    else:
        lines.append("主营业务核验：数据缺口，当前没有匹配到主公司。")
    return lines


def _business_model_lines(primary: TopicCompanyView | None) -> list[str]:
    if primary is None:
        return ["数据缺口：未识别到主公司，无法拆解商业模式。"]
    return [
        f"卖什么：{', '.join(primary.key_products) or ', '.join(primary.company.nodes) or '数据缺口'}。",
        f"卖给谁：下游节点包括 {', '.join(primary.downstream_nodes) or '数据缺口'}；具体客户名称、前五大客户占比和海外/国内客户结构仍是数据缺口。",
        f"如何赚钱：核心取决于产品结构、良率、交付节奏、上游成本、规模效应和客户认证；当前系统已识别业务定位为 `{primary.business_summary or primary.company.role or '数据缺口'}`。",
        f"议价能力：需要结合客户集中度、速率产品占比、毛利率和同行价格竞争验证；当前只可做定性跟踪，不能下强结论。",
    ]


def _growth_attribution_lines(primary: TopicCompanyView | None) -> list[str]:
    lines = [
        "| 指标 | 当前数据 | 可能驱动 | 数据缺口 |",
        "| --- | --- | --- | --- |",
    ]
    if primary and primary.financial:
        f = primary.financial
        lines.extend([
            f"| 营收增长 | { _fmt_pct(f.revenue_growth) } | 销量增长、产品结构升级、价格变化、汇率、并表影响 | 需要分产品/分地区收入 |",
            f"| 利润增长 | { _fmt_pct(f.profit_growth) } | 收入增长、毛利率、费用率、投资收益、税率 | 需要利润桥和费用率拆解 |",
            f"| 毛利率 | { _fmt_pct(f.gross_margin) } | 高速率产品占比、良率、价格和上游芯片成本 | 需要分产品毛利率 |",
            f"| 现金流质量 | { _fmt_num(f.operating_cash_flow) } | 回款、存货、应收、资本开支 | 需要经营现金流/净利润、应收、存货 |",
        ])
    else:
        lines.append("| 财务指标 | 数据缺口 | 无法做增长归因 | 需要年报/季报结构化财务数据 |")
    lines.append("归因公式：营收增长 = 销量增长 + 产品结构升级 + 单价变化 + 汇率影响 + 并表影响。")
    lines.append("归因公式：利润增长 = 营收增长 + 毛利率变化 + 费用率变化 + 投资收益 + 税率变化。")
    return lines


def _technology_route_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    topic_text = " ".join([report.topic, *(report.matched_nodes or []), *(primary.company.nodes if primary else [])]).lower()
    is_optical = any(term in topic_text for term in ["光", "optical", "cpo", "lpo", "silicon"])
    if not is_optical:
        return ["当前主题未明确识别为光模块技术路线；如属于技术驱动行业，应补充产品代际、路线切换和替代风险。"]
    return [
        "| 产品代际 | 应用场景 | 投资意义 | 当前证据状态 |",
        "| --- | --- | --- | --- |",
        "| 100G/200G | 传统数据中心、电信 | 成熟产品，价格压力较大 | 需要分产品收入验证 |",
        "| 400G | 云数据中心 | 过渡产品，仍有需求 | 需要出货和客户结构验证 |",
        "| 800G | AI 集群主流增量 | 当前增长核心之一 | 需要公告、订单、财报口径验证 |",
        "| 1.6T | 下一代 AI 数据中心 | 决定未来估值空间 | 需要样品/认证/量产进度验证 |",
        "| 硅光/LPO/CPO | 中长期技术方向 | 可能改变成本结构和价值分配 | 需要技术路线和客户采用证据 |",
        "关键判断：光模块公司估值取决于能否持续跟上速率升级、客户认证、良率和规模交付。当前若缺少 800G/1.6T 量产证据，应列为核心数据缺口。",
    ]


def _customer_order_lines(primary: TopicCompanyView | None) -> list[str]:
    if primary is None:
        return ["数据缺口：未识别主公司，无法分析客户结构。"]
    return [
        "| 维度 | 分析重点 | 当前状态 |",
        "| --- | --- | --- |",
        f"| 客户类型 | 海外云厂商、国内云厂商、通信设备商、设备厂商等 | 下游节点：{', '.join(primary.downstream_nodes) or '数据缺口'} |",
        "| 客户集中度 | 前五大客户收入占比、单一客户依赖度 | 数据缺口 |",
        "| 订单特征 | 认证周期、订单周期、交付节奏、能见度 | 数据缺口 |",
        "| 观察指标 | 云厂商 CapEx、GPU 出货、AI 集群建设、交换机端口速率升级 | 待持续跟踪 |",
        "传导链：AI 模型需求 -> GPU/AI 加速卡需求 -> AI 服务器出货 -> 数据中心网络升级 -> 交换机端口速率提升 -> 高速光模块需求 -> 公司收入与利润。",
    ]


def _industry_space_model_lines(report: TopicResearchReport) -> list[str]:
    return [
        "测算框架一：光模块市场空间 = 数据中心数量 × 交换机端口数 × 单端口光模块数量 × 单价。",
        "测算框架二：AI 光模块需求 = GPU 数量 × 网络架构 × 光模块配比 × 单价。",
        "| 假设项 | 保守 | 中性 | 乐观 | 数据状态 |",
        "| --- | --- | --- | --- | --- |",
        "| 全球 AI GPU 出货 | 待填 | 待填 | 待填 | 数据缺口 |",
        "| 单 GPU 对应光模块价值量 | 待填 | 待填 | 待填 | 数据缺口 |",
        "| 800G/1.6T 渗透率 | 待填 | 待填 | 待填 | 数据缺口 |",
        "| 对应市场规模 | 待计算 | 待计算 | 待计算 | 需接入行业数据库/券商假设 |",
    ]


def _value_chain_lines(report: TopicResearchReport) -> list[str]:
    names_by_node = {row.company.name: ", ".join(row.company.nodes[:4]) for row in report.companies[:8]}
    company_text = "；".join(f"{name}: {nodes}" for name, nodes in names_by_node.items()) or "数据缺口"
    return [
        "| 环节 | 投研含义 | 代表公司/当前识别 | 对目标公司的影响 |",
        "| --- | --- | --- | --- |",
        "| GPU/AI 加速卡 | 决定 AI 集群建设节奏 | 需外部数据验证 | 上游需求源头 |",
        "| AI 服务器/交换机 | 决定端口速率升级 | 需外部数据验证 | 影响高速光模块需求 |",
        f"| 光模块/光器件 | 直接收入和利润兑现环节 | {company_text} | 决定目标公司业绩弹性 |",
        "| 光芯片/电芯片 | 影响成本、供应和良率 | 需继续映射 | 决定毛利率和交付能力 |",
    ]


def _competition_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    lines = [
        "| 公司 | 产业链位置 | 核心产品/节点 | 优势线索 | 风险线索 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report.companies[:8]:
        relation = "目标公司" if primary and row.company.name == primary.company.name else "相关/可比观察"
        lines.append(
            f"| {row.company.name} | {relation} | {', '.join(row.company.nodes[:4]) or '-'} | {row.business_summary or row.company.role or '-'} | 需验证客户、价格、毛利率和现金流 |"
        )
    if len(lines) == 2:
        lines.append("| 数据缺口 | 未匹配到可比公司 | - | - | 需要扩充公司自动映射和同行估值数据 |")
    lines.append("可比公司筛选原则：直接可比应优先选择同产业链位置和同产品结构公司；产业链上下游公司只能用于景气传导，不宜直接做估值对比。")
    return lines


def _company_detail_lines(report: TopicResearchReport) -> list[str]:
    lines: list[str] = []
    for idx, row in enumerate(report.companies[:10], 1):
        lines.append(f"### {idx}. {row.company.name} ({row.company.ticker or '-'})")
        lines.append(f"匹配分：{row.match_score:.2f}；行业：{row.company.industry or '-'}；业务定位：{row.business_summary or row.company.role or '-'}。")
        lines.append(f"产品/节点：{', '.join(row.key_products or row.company.nodes) or '-'}。")
        lines.append(f"上游：{', '.join(row.upstream_nodes) or '-'}；上游公司：{', '.join(row.upstream_companies) or '-'}。")
        lines.append(f"下游：{', '.join(row.downstream_nodes) or '-'}；下游公司：{', '.join(row.downstream_companies) or '-'}。")
        lines.append(f"财务快照：{_financial_brief(row.financial)}；行情快照：{_price_brief(row.price)}。")
        if row.a_share_reports:
            filings = "；".join(f"{_doc_date_text(doc)} {doc.title}" for doc in row.a_share_reports[:3])
            lines.append(f"最近公告/财报：{filings}。")
    return lines or ["数据缺口：未识别到重点公司。"]


def _cashflow_quality_lines(primary: TopicCompanyView | None) -> list[str]:
    if primary is None or primary.financial is None:
        return [
            "数据缺口：缺少经营现金流、应收账款、存货、资本开支、在建工程、借款结构等字段，无法判断利润质量。",
            "| 指标 | 投研含义 | 当前状态 |",
            "| --- | --- | --- |",
            "| 存货增长 | 备货或需求不及预期 | 数据缺口 |",
            "| 应收账款增长 | 大客户账期和回款压力 | 数据缺口 |",
            "| 经营现金流/净利润 | 利润质量 | 数据缺口 |",
            "| 在建工程/CapEx | 扩产节奏和折旧压力 | 数据缺口 |",
        ]
    f = primary.financial
    lines = [
        f"负债率：{_fmt_pct(f.debt_ratio)}；经营现金流：{_fmt_num(f.operating_cash_flow)}；当前仍缺少应收、存货、CapEx 和在建工程字段。",
        "| 指标 | 投研含义 | 当前状态 |",
        "| --- | --- | --- |",
        f"| 负债率 | 财务杠杆和扩产资金压力 | {_fmt_pct(f.debt_ratio)} |",
        f"| 经营现金流 | 利润质量和回款 | {_fmt_num(f.operating_cash_flow)} |",
        "| 存货 | 备货或需求波动 | 数据缺口 |",
        "| 应收账款 | 客户账期和回款压力 | 数据缺口 |",
        "| 在建工程/CapEx | 产能扩张节奏 | 数据缺口 |",
    ]
    return lines


def _valuation_framework_lines(primary: TopicCompanyView | None) -> list[str]:
    lines = [
        "估值框架：至少需要历史估值分位、同行 PE/PB/PS、PEG、情景市值四类证据。",
        "| 估值方法 | 需要数据 | 当前状态 |",
        "| --- | --- | --- |",
    ]
    f = primary.financial if primary else None
    pe_pb_ps = "、".join(
        item for item in [
            f"PE={f.pe:.1f}" if f and f.pe is not None else "",
            f"PB={f.pb:.1f}" if f and f.pb is not None else "",
            f"PS={f.ps:.1f}" if f and f.ps is not None else "",
        ]
        if item
    )
    lines.extend([
        f"| 当前倍数 | PE/PB/PS、市值 | {pe_pb_ps or '数据缺口'} |",
        "| 历史分位 | 3年/5年估值分位 | 数据缺口 |",
        "| 同行对比 | 可比公司估值、增速、净利率 | 数据缺口 |",
        "| PEG | PE / 未来利润复合增速 | 数据缺口 |",
        "| 情景估值 | 乐观/中性/悲观净利润和倍数 | 数据缺口 |",
    ])
    return lines


def _forecast_model_lines(primary: TopicCompanyView | None) -> list[str]:
    base_revenue = _fmt_pct(primary.financial.revenue_growth) if primary and primary.financial else "数据缺口"
    base_profit = _fmt_pct(primary.financial.profit_growth) if primary and primary.financial else "数据缺口"
    return [
        "| 指标 | 最新实际/快照 | 2026E | 2027E | 2028E | 关键假设 |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| 收入增速 | {base_revenue} | 待填 | 待填 | 待填 | 产品出货、ASP、客户 CapEx |",
        "| 毛利率 | " + ( _fmt_pct(primary.financial.gross_margin) if primary and primary.financial else "数据缺口" ) + " | 待填 | 待填 | 待填 | 产品结构、良率、价格竞争、上游成本 |",
        f"| 利润增速 | {base_profit} | 待填 | 待填 | 待填 | 收入增长、毛利率、费用率 |",
        "| EPS | 数据缺口 | 待填 | 待填 | 待填 | 需要股本和盈利预测 |",
        "核心假设必须显式写出，并在后续季度用收入、毛利率、订单、现金流验证。当前系统不应在缺少预测数据时给出目标价。",
    ]


def _overseas_risk_lines(primary: TopicCompanyView | None) -> list[str]:
    return [
        "海外收入占比、北美客户占比、汇率敏感性、关税和出口管制均为重要变量；当前如未在 JSON 中出现，应列为数据缺口。",
        "如果目标公司海外客户占比较高，则收入弹性更强，但客户集中、汇率波动和贸易政策不确定性也会放大利润和估值波动。",
        "需要补充：分地区收入、前五大客户、外币收入和汇兑损益、出口限制风险说明。",
    ]


def _expectation_gap_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    topic = report.topic
    return [
        f"当前共识线索：市场容易围绕 `{topic}` 交易成长逻辑，但需要区分已定价部分和超预期部分。",
        "已被定价的部分：行业景气、AI/算力链条、龙头叙事等可能已部分反映在估值和股价中。",
        "可能超预期的部分：新产品放量节奏、毛利率维持能力、大客户订单持续性、现金流改善。",
        "可能低于预期的部分：价格竞争、客户 CapEx 放缓、产品认证延迟、存货/应收恶化、技术路线替代。",
        "数据缺口：缺少一致预期、股价涨跌幅、估值分位和盈利预测修正数据，无法精确判断预期差大小。",
    ]


def _stock_driver_lines(primary: TopicCompanyView | None) -> list[str]:
    return [
        "股价拆解：市值变化 = EPS/利润预期变化 + 估值倍数变化 + 风险偏好变化。",
        "| 驱动因素 | 方向 | 验证方式 |",
        "| --- | --- | --- |",
        "| 下游 CapEx 上修 | 正面 | 云厂商资本开支、订单、出货 |",
        "| 高端产品出货超预期 | 正面 | 公告、财报分产品、客户认证 |",
        "| 毛利率维持高位 | 正面 | 季度毛利率、产品结构 |",
        "| 估值处于高位 | 负面风险 | 历史分位、同行对比 |",
        "| ASP 下滑/价格竞争 | 负面 | 行业报价、毛利率 |",
        "| 技术路线变化 | 中长期不确定 | CPO/硅光/LPO 进展 |",
    ]


def _tracking_dashboard_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    rows = [
        ("海外云厂商 CapEx", "季度", "持续上修", "下修或延后"),
        ("高端产品出货", "季度", "放量加速", "低于预期"),
        ("下一代产品进展", "季度", "通过认证/量产", "延迟"),
        ("毛利率", "季度", "稳定或提升", "连续下降"),
        ("存货", "季度", "与订单匹配", "增速明显高于收入"),
        ("应收账款", "季度", "回款正常", "应收大幅增长"),
        ("同行价格竞争", "月度", "价格稳定", "ASP 快速下滑"),
        ("汇率", "月度", "有利于出口收入", "汇兑损失扩大"),
    ]
    lines = ["| 指标 | 观察频率 | 正面信号 | 负面信号 |", "| --- | --- | --- | --- |"]
    lines.extend(f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows)
    return lines


def _bear_case_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    return [
        "反方观点一：行业景气和龙头逻辑可能已经被市场充分定价，后续需要业绩持续超预期才能推动股价。",
        "反方观点二：高端产品放量后，价格竞争可能导致毛利率下行。",
        "反方观点三：客户集中度较高时，大客户订单波动会放大利润波动。",
        "反方观点四：技术路线变化可能改变传统产品的价值分配。",
        "证伪条件：未来两个季度收入增速明显低于行业需求增速。",
        "证伪条件：毛利率连续下降，且无法由产品结构或一次性因素解释。",
        "证伪条件：存货和应收账款增速明显高于收入，经营现金流与利润背离。",
        "证伪条件：下游 CapEx 或订单能见度下修，新产品认证/量产进度低于预期。",
    ]


def _rating_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    base_score = 60
    if primary and primary.financial and primary.financial.profit_growth is not None and primary.financial.profit_growth > 20:
        base_score += 10
    if primary and primary.financial and primary.financial.debt_ratio is not None and primary.financial.debt_ratio > 65:
        base_score -= 5
    if primary and primary.price and primary.price.change_3m is not None and primary.price.change_3m > 35:
        base_score -= 5
    label = "谨慎乐观" if base_score >= 70 else "中性跟踪" if base_score >= 55 else "谨慎"
    return [
        f"综合评级：{label}；综合评分：{base_score}/100。",
        "| 维度 | 分数 | 说明 |",
        "| --- | --- | --- |",
        f"| 基本面 | {min(base_score + 5, 90)} | 基于产业链位置、公司匹配度和已抓取财报信息 |",
        f"| 成长性 | {base_score} | 需要用订单、产品结构和下游需求继续验证 |",
        "| 估值性价比 | 50 | 缺少历史分位、同行估值和盈利预测，暂不给高分 |",
        "| 现金流质量 | 55 | 缺少应收、存货和经营现金流/净利润完整拆解 |",
        "| 风险水平 | 65 | 客户集中、价格竞争、技术迭代和海外风险需持续跟踪 |",
    ]


def _report_critic_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    checks = [
        ("是否有事实校验", True),
        ("是否解释商业模式", primary is not None),
        ("是否有增长归因", bool(primary and primary.financial)),
        ("是否有估值分析", bool(report.valuation_view)),
        ("是否有同行比较", len(report.companies) > 1),
        ("是否有预测框架", True),
        ("是否有反方观点", True),
        ("是否有证伪条件", True),
        ("是否避免把同行业务写入目标公司", True),
    ]
    return [f"{name}：{'通过' if ok else '数据缺口/待补充'}。" for name, ok in checks]


def _fmt_pct(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "数据缺口"


def _fmt_num(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "数据缺口"


def render_professional_topic_report(report: TopicResearchReport) -> str:
    primary = _primary_company(report)
    title_name = primary.company.name if primary else report.topic
    lines = [
        f"# {title_name}投资研究报告",
        "",
        f"> 生成时间：{report.generated_at}",
        f"> 研究对象：{report.topic}",
        f"> 系统建议：{report.recommendation}",
        "",
    ]
    _extend_section(lines, "1. 核心结论", _core_conclusion_lines(report, primary))
    _extend_section(lines, "2. 公司概况与商业模式", _company_overview_lines(report, primary))
    _extend_section(lines, "3. 近期事件与市场表现", _recent_event_market_lines(report, primary))
    _extend_section(lines, "4. 财务深度分析", _financial_deep_dive_lines(primary))
    _extend_section(lines, "5. 行业与产业链分析", _industry_chain_analysis_lines(report, primary))
    _extend_section(lines, "6. 竞争格局", _competitive_landscape_lines(report, primary))
    _extend_section(lines, "7. 估值分析", _valuation_analysis_lines(report, primary))
    _extend_section(lines, "8. 盈利预测", _earnings_forecast_lines(primary))
    _extend_section(lines, "9. 预期差与催化剂", _expectation_catalyst_lines(report, primary))
    _extend_section(lines, "10. 风险与证伪条件", _risk_disproof_lines(report, primary))
    _extend_section(lines, "11. 跟踪指标仪表盘", _tracking_dashboard_lines(report, primary))
    _extend_section(lines, "12. 附录", _appendix_lines(report, primary))
    return "\n".join(lines)


def _core_conclusion_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    company = primary.company.name if primary else report.topic
    bullish = _core_bullish_logic(report, primary)
    risks = _collect_report_risks(report)
    return [
        f"一句话结论：{_render_executive_summary(report)}",
        f"投资评级：{report.recommendation}；情绪标签：{report.sentiment_label}；情绪分数：{report.sentiment_score:.2f}。",
        f"核心看多逻辑：{bullish}",
        f"核心风险：{risks[0] if risks else '数据缺口：当前风险材料不足。'}",
        f"关键证伪：若 {company} 后续收入、毛利率、现金流、订单能见度或关键产品进度连续弱于预期，需要下修投资判断。",
    ]


def _company_overview_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    if primary is None:
        return ["主营业务：数据缺口。", "产品结构：数据缺口。", "客户结构：数据缺口。", "收入来源：数据缺口。", "产业链位置：数据缺口。"]
    return [
        f"主营业务：{primary.business_summary or primary.company.role or '数据缺口'}。",
        f"产品结构：{', '.join(primary.key_products) or ', '.join(primary.company.nodes) or '数据缺口'}。",
        f"客户结构：下游节点包括 {', '.join(primary.downstream_nodes) or '数据缺口'}；具体客户名称、前五大客户占比、海外/国内客户占比仍是数据缺口。",
        "收入来源：需要按产品、客户、地区、数通/电信等维度拆分；当前结构化数据不足。",
        f"产业链位置：覆盖节点 {', '.join(primary.company.nodes) or '数据缺口'}；上游 {', '.join(primary.upstream_nodes) or '数据缺口'}；下游 {', '.join(primary.downstream_nodes) or '数据缺口'}。",
    ]


def _recent_event_market_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    lines = [
        "财报事件：",
        *_indent_items([item for item in _hot_news_items(report) if "财报" in item or "公告" in item][:5]),
        "订单事件：数据缺口，需抓取订单公告、客户认证、扩产和交付相关材料。",
        "技术事件：数据缺口，需跟踪新产品认证、量产、硅光/LPO/CPO 等技术路线进展。",
        f"股价表现：{_price_brief(primary.price) if primary else '数据缺口'}。",
        "资金流向：数据缺口，尚未接入主力资金、北向资金、成交额和换手率等数据。",
    ]
    if len(lines) == 1:
        lines.append("财报事件：数据缺口。")
    return lines


def _financial_deep_dive_lines(primary: TopicCompanyView | None) -> list[str]:
    lines = [
        "收入增长归因：",
        *_indent_items(_growth_attribution_lines(primary)[:6]),
        f"毛利率与净利率：{_gross_net_margin_text(primary)}",
        "费用率：数据缺口，需补充销售、管理、研发、财务费用率及同比变化。",
        f"现金流：{_cashflow_text(primary)}",
        "存货与应收：数据缺口，需补充存货、应收账款、合同负债与收入增速的匹配关系。",
        "杜邦分析：数据缺口，需补充净利率、资产周转率、权益乘数，拆解 ROE 来源。",
    ]
    return lines


def _industry_chain_analysis_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    return [
        "AI 数据中心需求：需要用云厂商 CapEx、GPU 出货、AI 集群建设进度验证；当前作为关键跟踪假设。",
        "光模块市场空间：",
        *_indent_items(_industry_space_model_lines(report)),
        "速率升级路径：",
        *_indent_items(_technology_route_lines(report, primary)),
        f"上游供应链：{', '.join(primary.upstream_nodes) if primary else '数据缺口'}。",
        f"下游客户需求：{', '.join(primary.downstream_nodes) if primary else '数据缺口'}；具体客户结构和订单能见度仍需验证。",
    ]


def _competitive_landscape_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    lines = [
        "国内同行：",
        *_indent_items([row.company.name for row in report.companies[:8] if not primary or row.company.name != primary.company.name] or ["数据缺口：需要扩充可比公司池。"]),
        "海外同行：数据缺口，需接入海外光模块/光器件可比公司数据。",
        "市占率：数据缺口，需行业数据库或公司披露口径。",
        "技术路线：",
        *_indent_items(_technology_route_lines(report, primary)[:6]),
        "竞争优势与短板：",
        *_indent_items(_competition_lines(report, primary)[2:8]),
    ]
    return lines


def _valuation_analysis_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    return [
        "历史估值分位：数据缺口，需补充过去 3 年/5 年 PE、PB、PS 分位。",
        "同行估值对比：",
        *_indent_items(_valuation_framework_lines(primary)),
        "PEG：数据缺口，需当前 PE 与未来利润复合增速。",
        "情景估值：",
        "| 情景 | 净利润假设 | 估值倍数 | 对应市值 | 隐含空间 |",
        "| --- | --- | --- | --- | --- |",
        "| 乐观 | 数据缺口 | 数据缺口 | 数据缺口 | 数据缺口 |",
        "| 中性 | 数据缺口 | 数据缺口 | 数据缺口 | 数据缺口 |",
        "| 悲观 | 数据缺口 | 数据缺口 | 数据缺口 | 数据缺口 |",
        "当前系统估值观察：",
        *_indent_items(report.valuation_view),
    ]


def _earnings_forecast_lines(primary: TopicCompanyView | None) -> list[str]:
    return [
        "未来三年收入预测：",
        *_indent_items(_forecast_model_lines(primary)[:3]),
        "毛利率假设：需基于产品结构、ASP、良率、上游成本和竞争格局建立；当前为数据缺口。",
        "净利润预测：需基于收入、毛利率、费用率、税率和投资收益拆解；当前为数据缺口。",
        "EPS 预测：需补充股本、摊薄因素和净利润预测；当前为数据缺口。",
        "敏感性分析：建议至少测试收入增速、毛利率、估值倍数三个变量。",
    ]


def _expectation_catalyst_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    return [
        "市场已经定价什么：",
        *_indent_items(_expectation_gap_lines(report, primary)[:2]),
        "可能超预期什么：新产品放量节奏、毛利率维持能力、大客户订单持续性、现金流改善。",
        "未来催化剂：财报披露、订单/认证公告、产品量产进度、下游 CapEx 上修、行业价格变化。",
        "关键时间点：季度财报、年度报告、重要客户资本开支披露、行业展会或技术路线发布。",
    ]


def _risk_disproof_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    return [
        "主要风险：",
        *_indent_items(_collect_report_risks(report)),
        "反方观点：",
        *_indent_items(_bear_case_lines(report, primary)[:4]),
        "证伪指标：",
        *_indent_items(_bear_case_lines(report, primary)[4:]),
        "风险等级：中高。原因是成长股通常同时暴露于估值、订单、技术迭代、客户集中和现金流波动。",
    ]


def _appendix_lines(report: TopicResearchReport, primary: TopicCompanyView | None) -> list[str]:
    sources = [doc.source for doc in report.recent_documents[:8]]
    return [
        f"数据来源：{', '.join(dict.fromkeys(sources)) or '公告/财报、新闻源、产业链图谱、行情快照；当前来源列表不足'}。",
        "关键假设：行业需求、产品升级、客户订单、毛利率、现金流和估值倍数均需持续验证。",
        "计算公式：营收增长 = 销量增长 + 产品结构升级 + 单价变化 + 汇率影响 + 并表影响。",
        "计算公式：利润增长 = 营收增长 + 毛利率变化 + 费用率变化 + 投资收益 + 税率变化。",
        "计算公式：市值变化 = 盈利预期变化 + 估值倍数变化 + 风险偏好变化。",
        "免责声明：本报告仅用于信息整理和投研辅助，不构成任何投资建议；结论需结合原始公告、财报和专业数据库复核。",
    ]


def _core_bullish_logic(report: TopicResearchReport, primary: TopicCompanyView | None) -> str:
    if report.core_logic:
        return report.core_logic[0]
    if primary:
        return f"{primary.company.name} 与 {', '.join(primary.company.nodes[:3]) or report.topic} 相关，需继续验证订单、产品结构和财务兑现。"
    return "数据缺口：当前资料不足以形成核心看多逻辑。"


def _gross_net_margin_text(primary: TopicCompanyView | None) -> str:
    if not primary or not primary.financial:
        return "数据缺口，缺少毛利率、净利率及同比变化。"
    f = primary.financial
    return f"毛利率 {_fmt_pct(f.gross_margin)}；净利率数据缺口；ROE {_fmt_pct(f.roe)}。"


def _cashflow_text(primary: TopicCompanyView | None) -> str:
    if not primary or not primary.financial:
        return "数据缺口，缺少经营现金流、资本开支、在建工程等字段。"
    return f"经营现金流 {_fmt_num(primary.financial.operating_cash_flow)}；负债率 {_fmt_pct(primary.financial.debt_ratio)}；仍需补充经营现金流/净利润。"


def _indent_items(items: list[str]) -> list[str]:
    return [f"  - {item}" for item in items if item]


def _classify_intent(topic: str, profiles: list[CompanyProfile]) -> ResearchIntent:
    query = topic.strip()
    low = query.lower()
    exact_company = next((profile for profile in profiles if _matches_company_name(query, profile)), None)
    compare_markers = ["和", "vs", "对比", "比较", "哪个好"]
    question_markers = ["能不能", "是否", "怎么看", "为什么", "还有没有", "值不值得"]
    sector_markers = ["产业链", "板块", "行业", "设备", "经济"]

    if any(marker in low for marker in compare_markers):
        return ResearchIntent("comparison", query, "输入中包含对比关系词，优先按多对象横向比较处理。")
    if exact_company:
        return ResearchIntent("company", exact_company.name, "输入与公司名或别名精确匹配，按公司深度研究处理。")
    if any(marker in query for marker in sector_markers):
        return ResearchIntent("sector", query, "输入更像行业、板块或产业链主题，按板块研究处理。")
    if any(marker in query for marker in question_markers):
        return ResearchIntent("question", query, "输入为投资问题句式，按问答型研究处理。")
    return ResearchIntent("theme", query, "输入更像泛主题或概念，按主题研究处理。")


def _build_dynamic_source_manager(
    config: str | Path,
    profiles: list[CompanyProfile],
    intent: ResearchIntent,
    topic: str,
    media_crawler_root: str | Path | None = None,
    media_crawler_platforms: list[str] | None = None,
) -> SourceManager:
    configs = load_connector_configs(config)
    if media_crawler_root:
        _enable_or_add_media_crawler(configs, media_crawler_root, media_crawler_platforms)
    auto_profiles = _auto_profiles_for_topic(profiles, intent, topic)
    for cfg in configs:
        if cfg.type == "tencent_quotes":
            _fill_symbols(cfg.params, auto_profiles, mode="tencent")
        elif cfg.type in {"mootdx_quotes", "mootdx_financials", "akshare_news"}:
            _fill_symbols(cfg.params, auto_profiles, mode="plain")
    return SourceManager.from_configs(configs)


def _enable_or_add_media_crawler(
    configs: list[ConnectorConfig],
    root_dir: str | Path,
    platforms: list[str] | None = None,
) -> None:
    params = {
        "root_dir": str(root_dir),
        "platforms": platforms or ["finance_news", "claim_tasks", "cls", "xq"],
        "modes": ["processed", "raw", "cache"],
        "keywords": [],
    }
    for idx, cfg in enumerate(configs):
        if cfg.type == "media_crawler":
            configs[idx] = ConnectorConfig(
                name=cfg.name,
                type=cfg.type,
                enabled=True,
                params={**cfg.params, **params},
            )
            return
    configs.insert(
        0,
        ConnectorConfig(
            name="media_crawler_finance_news",
            type="media_crawler",
            enabled=True,
            params=params,
        ),
    )


def _auto_profiles_for_topic(
    profiles: list[CompanyProfile],
    intent: ResearchIntent,
    topic: str,
    top_k: int = 6,
) -> list[CompanyProfile]:
    selected: list[CompanyProfile] = []
    if intent.input_type == "company":
        selected.extend(profile for profile in profiles if _matches_company_name(intent.target, profile))
    for _, profile in search_company_profiles(profiles, topic, top_k=top_k):
        if all(existing.name != profile.name for existing in selected):
            selected.append(profile)
    return selected[:top_k]


def _fill_symbols(params: dict[str, Any], profiles: list[CompanyProfile], *, mode: str) -> None:
    existing = params.get("symbols")
    if existing:
        return
    symbols: list[str] = []
    for profile in profiles:
        ticker = (profile.ticker or "").strip().upper()
        if not ticker or "." not in ticker:
            continue
        code, suffix = ticker.split(".", 1)
        if not code.isdigit():
            continue
        if mode == "tencent":
            prefix = {"SZ": "sz", "SH": "sh", "BJ": "bj"}.get(suffix, "")
            if prefix:
                symbols.append(f"{prefix}{code}")
        else:
            symbols.append(code)
    if symbols:
        params["symbols"] = list(dict.fromkeys(symbols))


def _build_task_plan(intent: ResearchIntent) -> list[TaskPlanItem]:
    if intent.input_type == "company":
        return [
            TaskPlanItem("公司业务梳理", "识别主营业务、收入结构、上下游位置与核心产品。"),
            TaskPlanItem("财报与公告分析", "抓取财报、季报、公告与关键经营变化。"),
            TaskPlanItem("估值与价格观察", "结合估值、涨跌幅和情绪判断市场定价。"),
            TaskPlanItem("未来情景预测", "输出3个月、1年和3年的乐观/中性/悲观情景。"),
        ]
    if intent.input_type in {"sector", "theme"}:
        return [
            TaskPlanItem("产业链拆解", "识别核心环节、上游约束、下游需求与受益方向。"),
            TaskPlanItem("景气与催化分析", "梳理近期新闻、公告、政策和关键事件。"),
            TaskPlanItem("重点公司映射", "筛选核心公司并比对财务、位置和风险。"),
            TaskPlanItem("情景与建议", "输出未来情景、风险点和跟踪指标。"),
        ]
    return [
        TaskPlanItem("问题拆解", "把模糊问题拆成产业、公司、估值和风险四类子任务。"),
        TaskPlanItem("证据收集", "优先收集最能回答该问题的新闻、公告和财务证据。"),
        TaskPlanItem("结构化判断", "输出核心逻辑、分歧点和建议等级。"),
    ]


def _sort_documents(docs: list[SourceDocument]) -> list[SourceDocument]:
    return sorted(docs, key=lambda doc: doc.published_at or doc.fetched_at or datetime.min, reverse=True)


def _select_documents(topic: str, chunks: list, docs: list[SourceDocument], top_k_docs: int) -> list[SourceDocument]:
    if not chunks:
        return docs[:top_k_docs]
    selected_chunks = HybridIndex(chunks).search(topic, top_k=min(len(chunks), top_k_docs * 3))
    doc_ids = list(dict.fromkeys(chunk.document_id for chunk in selected_chunks))
    doc_by_id = {doc.id: doc for doc in docs}
    selected = [doc_by_id[doc_id] for doc_id in doc_ids if doc_id in doc_by_id]
    return selected or docs[:top_k_docs]


def _find_related_companies(
    *,
    topic: str,
    intent: ResearchIntent,
    profiles: list[CompanyProfile],
    financials: dict[str, FinancialSnapshot],
    prices: dict[str, PriceSnapshot],
    matched_nodes: list[str],
    top_k: int,
    focus_target_company: bool = False,
) -> list[TopicCompanyView]:
    node_set = set(canonicalize_nodes(matched_nodes))
    scored: dict[str, TopicCompanyView] = {}

    if intent.input_type == "company":
        for profile in profiles:
            if _matches_company_name(intent.target, profile):
                scored[profile.name] = TopicCompanyView(
                    company=profile,
                    match_score=1.0,
                    financial=lookup_snapshot(financials, profile.name, profile.ticker),
                    price=lookup_snapshot(prices, profile.name, profile.ticker),
                )
                break

    for score, profile in search_company_profiles(profiles, topic, top_k=top_k):
        current = scored.get(profile.name)
        if current is None or score > current.match_score:
            scored[profile.name] = TopicCompanyView(
                company=profile,
                match_score=score,
                financial=lookup_snapshot(financials, profile.name, profile.ticker),
                price=lookup_snapshot(prices, profile.name, profile.ticker),
            )

    for profile in profiles:
        overlap = node_set & set(canonicalize_nodes(profile.nodes))
        if not overlap:
            continue
        node_score = min(1.0, 0.55 + 0.12 * len(overlap))
        current = scored.get(profile.name)
        if current is None or node_score > current.match_score:
            scored[profile.name] = TopicCompanyView(
                company=profile,
                match_score=round(node_score, 4),
                financial=lookup_snapshot(financials, profile.name, profile.ticker),
                price=lookup_snapshot(prices, profile.name, profile.ticker),
            )

    rows = sorted(scored.values(), key=lambda item: item.match_score, reverse=True)
    if intent.input_type == "company":
        locked = [row for row in rows if row.company.name == intent.target]
        if focus_target_company and locked:
            return locked[:1]
        related = [row for row in rows if row.company.name != intent.target]
        return (locked + related)[:top_k]
    return rows[:top_k]


def _enrich_company_view(
    row: TopicCompanyView,
    *,
    profiles: list[CompanyProfile],
    graph: IndustryGraph,
    report_docs: list[SourceDocument],
) -> TopicCompanyView:
    upstream_nodes = canonicalize_nodes(graph.expand(row.company.nodes, depth=1, direction="backward"))
    downstream_nodes = canonicalize_nodes(graph.expand(row.company.nodes, depth=1, direction="forward"))
    return TopicCompanyView(
        company=row.company,
        match_score=row.match_score,
        financial=row.financial,
        price=row.price,
        business_summary=_business_summary(row.company),
        key_products=_infer_key_products(row.company),
        upstream_nodes=upstream_nodes,
        downstream_nodes=downstream_nodes,
        upstream_companies=_related_companies_for_nodes(profiles, upstream_nodes, exclude=row.company.name),
        downstream_companies=_related_companies_for_nodes(profiles, downstream_nodes, exclude=row.company.name),
        a_share_reports=report_docs[:6],
    )


def _build_current_snapshot(
    *,
    intent: ResearchIntent,
    matched_nodes: list[str],
    docs: list[SourceDocument],
    companies: list[TopicCompanyView],
    signals: list[WeakSignal],
) -> list[str]:
    items = [f"研究对象识别为 {intent.input_type}，当前目标为“{intent.target}”。"]
    if matched_nodes:
        items.append(f"当前已映射到的核心产业链节点包括：{', '.join(matched_nodes[:6])}。")
    if docs:
        items.append(f"近期共筛出 {len(docs)} 篇高相关资料，其中最新材料标题为《{docs[0].title}》。")
    else:
        items.append("当前公开资料较少，主题仍处在资料收集阶段。")
    if companies:
        items.append(f"已识别 {len(companies)} 家相关公司，重点公司包括：{', '.join(row.company.name for row in companies[:3])}。")
    if signals:
        items.append(f"已抽取 {len(signals)} 条结构化信号，最强信号为《{signals[0].title}》。")
    else:
        items.append("当前尚未抽取到足够强的结构化事件信号。")
    return items


def _build_core_logic(
    matched_nodes: list[str],
    upstream_nodes: list[str],
    downstream_nodes: list[str],
    companies: list[TopicCompanyView],
    signals: list[WeakSignal],
) -> list[str]:
    items: list[str] = []
    if matched_nodes:
        items.append(f"主题核心链条主要围绕 {', '.join(matched_nodes[:4])} 展开。")
    if upstream_nodes or downstream_nodes:
        items.append(
            f"上游约束更多来自 {', '.join(upstream_nodes[:3]) or '供给端'}，"
            f"下游兑现更多依赖 {', '.join(downstream_nodes[:3]) or '需求端'}。"
        )
    if companies:
        leader = companies[0]
        items.append(
            f"{leader.company.name} 当前是最贴近主题的公司之一，业务定位为：{leader.business_summary or leader.company.role or '待补充'}。"
        )
    if signals:
        top_signal = signals[0]
        items.append(
            f"最强信号对应事件类型为 {top_signal.event.event_type}，说明当前逻辑更偏向"
            f"{_event_interpretation(top_signal.event.event_type)}。"
        )
    if not items:
        items.append("当前尚缺足够证据支撑核心逻辑，建议先补充新闻、公告与财报材料。")
    return items


def _build_valuation_view(companies: list[TopicCompanyView], candidates: list[InvestmentCandidate]) -> list[str]:
    items: list[str] = []
    for row in companies[:4]:
        pieces = [f"{row.company.name}"]
        if row.financial and row.financial.pe is not None:
            pieces.append(f"PE {row.financial.pe:.1f}")
        if row.financial and row.financial.pb is not None:
            pieces.append(f"PB {row.financial.pb:.1f}")
        if row.price and row.price.change_3m is not None:
            pieces.append(f"近3个月涨跌幅 {row.price.change_3m:.1f}%")
        if len(pieces) > 1:
            items.append("，".join(pieces) + "。")
    if candidates:
        top = candidates[0]
        if top.price and top.price.change_3m is not None and top.price.change_3m > 40:
            items.append(f"{top.company.name} 短期涨幅较大，需警惕拥挤交易与估值压缩风险。")
        elif top.financial and top.financial.profit_growth is not None and top.financial.profit_growth > 20:
            items.append(f"{top.company.name} 若利润增长能够持续兑现，当前定价仍有一定消化空间。")
    if not items:
        items.append("当前估值信息仍不完整，建议补充价格、PE/PB/PS 和历史分位数据。")
    return items


def _build_sentiment(signals: list[WeakSignal], companies: list[TopicCompanyView]) -> tuple[float, str]:
    score = 0.0
    for signal in signals[:10]:
        if signal.event.event_type in POSITIVE_EVENT_HINTS:
            score += 0.12
        if signal.event.event_type in NEGATIVE_EVENT_HINTS:
            score -= 0.12
        score += min(signal.weak_signal_score / 300.0, 0.08)
        score -= min(signal.attention_score / 200.0, 0.06)
    for row in companies[:4]:
        if row.financial and row.financial.profit_growth is not None and row.financial.profit_growth > 20:
            score += 0.08
        if row.price and row.price.change_3m is not None and row.price.change_3m > 35:
            score -= 0.05
    score = max(-1.0, min(1.0, score))
    if score >= 0.25:
        return round(score, 2), "偏乐观"
    if score <= -0.2:
        return round(score, 2), "偏谨慎"
    return round(score, 2), "中性"


def _build_recommendation(
    candidates: list[InvestmentCandidate],
    signals: list[WeakSignal],
    companies: list[TopicCompanyView],
) -> str:
    if candidates and candidates[0].score >= 80:
        return "重点关注"
    if candidates and candidates[0].score >= 65:
        return "谨慎关注"
    if companies and signals:
        return "等待催化"
    if companies:
        return "继续跟踪"
    return "资料不足"


def _build_scenarios(
    intent: ResearchIntent,
    matched_nodes: list[str],
    candidates: list[InvestmentCandidate],
    recommendation: str,
) -> list[ScenarioView]:
    target = candidates[0].company.name if candidates else intent.target
    chain = "、".join(matched_nodes[:3]) or intent.target
    return [
        ScenarioView(
            horizon="未来3个月",
            optimistic=f"{chain} 出现新增订单、政策或业绩催化，{target} 获得短期估值提升。",
            base=f"主题维持当前热度，市场继续围绕已有逻辑交易，建议等级维持“{recommendation}”。",
            pessimistic=f"催化落地慢于预期，短期情绪回落，{target} 或主题板块进入消化期。",
        ),
        ScenarioView(
            horizon="未来1年",
            optimistic=f"{chain} 需求和利润兑现逐步清晰，重点公司财报验证主题成立。",
            base=f"行业平稳推进，龙头公司兑现、跟风公司分化，投资回报更依赖选股。",
            pessimistic=f"供给释放过快或需求不及预期，主题估值回归，基本面弱公司承压。",
        ),
        ScenarioView(
            horizon="未来3年",
            optimistic=f"{chain} 进入持续成长阶段，龙头公司形成份额和盈利双升。",
            base=f"行业成长存在但节奏放缓，投资收益更多来自龙头长期经营能力。",
            pessimistic=f"产业路线变化或竞争格局恶化，当前主题逻辑被削弱。",
        ),
    ]


def _build_tracking_indicators(
    intent: ResearchIntent,
    matched_nodes: list[str],
    companies: list[TopicCompanyView],
    signals: list[WeakSignal],
) -> list[str]:
    items = [
        "后续公告中的订单、扩产、客户验证和价格变化。",
        "季度/年度财报中的营收、利润、毛利率、现金流和资本开支。",
    ]
    if matched_nodes:
        items.append(f"与 {', '.join(matched_nodes[:3])} 相关的行业景气、供需和价格指标。")
    if companies:
        items.append(f"重点跟踪公司：{', '.join(row.company.name for row in companies[:3])}。")
    if signals:
        items.append(f"重点复核事件信号：{signals[0].event.event_type} 是否继续得到后续材料验证。")
    if intent.input_type == "company":
        items.append("关注分业务收入结构、客户集中度、前五大供应商和研发投入变化。")
    return list(dict.fromkeys(items))


def _render_executive_summary(report: TopicResearchReport) -> str:
    parts = []
    if report.current_snapshot:
        parts.append(report.current_snapshot[0])
    if report.core_logic:
        parts.append(report.core_logic[0])
    parts.append(f"当前建议等级为“{report.recommendation}”，市场情绪判断为“{report.sentiment_label}”。")
    return " ".join(parts)


def _collect_report_risks(report: TopicResearchReport) -> list[str]:
    risks = list(
        dict.fromkeys(
            risk
            for candidate in report.candidates[:6]
            for risk in candidate.risks[:2]
            if risk
        )
    )
    if not risks:
        risks = [
            "当前结论更多基于公开资料自动归纳，仍需人工复核原始公告和财报。",
            "若缺少订单、扩产和利润兑现的后续验证，主题可能停留在概念阶段。",
            "部分公司财务或价格数据仍不完整，结论存在信息缺口。",
            "若市场情绪先于基本面过快升温，短期估值压缩风险会上升。",
        ]
    return risks[:6]


def _render_conclusion(report: TopicResearchReport) -> str:
    if report.candidates:
        top = report.candidates[0]
        return (
            f"综合当前资料，{report.topic} 已经具备形成持续跟踪框架的基础。"
            f"在自动候选池中，{top.company.name} 暂时位于前列，但是否值得升级为更高优先级配置，"
            "仍要看后续公告、订单与财报是否继续验证当前逻辑。"
        )
    if report.companies:
        return (
            f"综合当前资料，{report.topic} 已经能够识别出一批相关公司，"
            "但事件和财报证据仍偏弱，当前更适合作为研究清单而不是直接下强结论。"
        )
    return (
        f"综合当前资料，{report.topic} 仍处在初步资料聚合阶段。"
        "下一步应优先补充高质量新闻、公司公告、财务与价格数据，再进行二次研判。"
    )


def _profile_key(profile: CompanyProfile) -> str:
    return (profile.ticker or profile.name).strip().lower()


def _business_summary(profile: CompanyProfile) -> str:
    if profile.metadata.get("business_summary"):
        return str(profile.metadata["business_summary"])
    if profile.role and profile.nodes:
        return f"{profile.role}; 主要覆盖 {', '.join(profile.nodes[:3])} 等环节。"
    if profile.role:
        return profile.role
    if profile.nodes:
        return f"公司与 {', '.join(profile.nodes[:3])} 等产业链环节相关。"
    return ""


def _infer_key_products(profile: CompanyProfile) -> list[str]:
    if isinstance(profile.metadata.get("products"), list):
        return [str(item) for item in profile.metadata["products"] if str(item).strip()]
    mapping = {
        "Optical Module": "高速光模块",
        "Silicon Photonics": "硅光/CPO/LPO",
        "Laser": "激光器/光器件",
        "AI Server": "AI服务器/整机",
        "Network Switch": "交换机/高速PCB",
        "Power Grid": "UPS/PDU/供配电",
        "Liquid Cooling": "液冷/热管理",
        "Advanced Packaging": "先进封装",
        "HBM": "HBM相关封装材料",
        "Enterprise AI": "企业AI应用",
        "Model API": "模型服务/API",
        "AI Security": "AI安全与合规",
    }
    products = [mapping[node] for node in profile.nodes if node in mapping]
    return list(dict.fromkeys(products))[:5]


def _related_companies_for_nodes(
    profiles: list[CompanyProfile],
    nodes: list[str],
    *,
    exclude: str,
    top_k: int = 6,
) -> list[str]:
    if not nodes:
        return []
    node_set = set(canonicalize_nodes(nodes))
    scored: list[tuple[int, str]] = []
    for profile in profiles:
        if profile.name == exclude:
            continue
        overlap = node_set & set(canonicalize_nodes(profile.nodes))
        if overlap:
            scored.append((len(overlap), profile.name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[:top_k]]


def _financial_brief(financial: FinancialSnapshot | None) -> str:
    if financial is None:
        return "暂无结构化财务快照"
    items: list[str] = []
    if financial.period:
        items.append(financial.period)
    if financial.revenue_growth is not None:
        items.append(f"营收同比 {financial.revenue_growth:.1f}%")
    if financial.profit_growth is not None:
        items.append(f"利润同比 {financial.profit_growth:.1f}%")
    if financial.gross_margin is not None:
        items.append(f"毛利率 {financial.gross_margin:.1f}%")
    if financial.roe is not None:
        items.append(f"ROE {financial.roe:.1f}%")
    if financial.pe is not None:
        items.append(f"PE {financial.pe:.1f}")
    return "；".join(items) or "暂无结构化财务快照"


def _price_brief(price: PriceSnapshot | None) -> str:
    if price is None:
        return "暂无价格快照"
    items: list[str] = []
    if price.date:
        items.append(f"日期 {price.date}")
    if price.close is not None:
        items.append(f"收盘价 {price.close:.2f}")
    if price.change_1m is not None:
        items.append(f"1M {price.change_1m:.1f}%")
    if price.change_3m is not None:
        items.append(f"3M {price.change_3m:.1f}%")
    if price.change_6m is not None:
        items.append(f"6M {price.change_6m:.1f}%")
    return "；".join(items) or "暂无价格快照"


def _event_interpretation(event_type: str) -> str:
    if event_type in {"ORDER_SIGNED", "CUSTOMER_CERTIFICATION"}:
        return "订单或客户验证开始兑现"
    if event_type in {"CAPACITY_EXPANSION", "TECH_UPGRADE", "PRODUCT_LAUNCH"}:
        return "供给侧或产品侧变化"
    if event_type in {"SUPPLY_SHORTAGE", "PRICE_INCREASE"}:
        return "供需错配带来的利润弹性"
    if event_type in {"FINANCIAL_VALIDATION", "GROSS_MARGIN_CHANGE"}:
        return "财务报表开始验证前期逻辑"
    return "阶段性产业链变化"


def _matches_company_name(query: str, profile: CompanyProfile) -> bool:
    q = query.strip().lower()
    if not q:
        return False
    terms = [profile.name, *(profile.aliases or []), profile.ticker or ""]
    return any(term and term.lower() == q for term in terms)


def _hot_news_items(report: TopicResearchReport, top_k: int = 12) -> list[str]:
    """Build a compact hot-news list for the report without sending full PDFs."""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(sort_key: str, label: str, title: str, source: str = "", path: str = "") -> None:
        clean_title = " ".join((title or "").split())
        if not clean_title:
            return
        key = f"{label}|{clean_title}|{path}"
        if key in seen:
            return
        seen.add(key)
        suffix_parts = [part for part in [source, path] if part]
        suffix = f" | {' | '.join(suffix_parts)}" if suffix_parts else ""
        items.append((sort_key, f"[{label}] {clean_title}{suffix}"))

    for doc in getattr(report, "recent_news", [])[:top_k]:
        date_text = _doc_date_text(doc)
        value = (doc.metadata or {}).get("news_value") or {}
        category = value.get("category") or "news"
        reason = value.get("reason") or ""
        summary = (doc.metadata or {}).get("article_summary") or doc.content
        summary = " ".join(str(summary or "").split())[:260]
        title = f"{date_text} {doc.title}"
        if summary:
            title = f"{title} | 摘要: {summary}"
        if reason:
            title = f"{title} | 入选原因: {reason}"
        add(date_text, f"最近新闻/{category}", title, doc.source, doc.url or doc.path or "")

    docs = [*report.selected_documents, *report.recent_documents]
    docs = _sort_documents(list({doc.id: doc for doc in docs}.values()))
    for doc in docs[:top_k]:
        label = "公告" if _looks_like_announcement(doc) else "新闻"
        date_text = _doc_date_text(doc)
        add(date_text, label, f"{date_text} {doc.title}", doc.source, doc.url or doc.path or "")

    for row in report.companies:
        for doc in row.a_share_reports[:4]:
            date_text = _doc_date_text(doc)
            add(date_text, "财报/公告", f"{date_text} {row.company.name}: {doc.title}", doc.source, doc.url or doc.path or "")

    for signal in report.signals[:top_k]:
        date_text = signal.event.occurred_at.isoformat()[:10] if signal.event.occurred_at else ""
        add(
            date_text,
            "结构化信号",
            f"{date_text} {signal.title} | event={signal.event.event_type} | score={signal.weak_signal_score:.2f}",
            signal.event.source,
            signal.event.source_path,
        )

    if not items:
        return ["当前没有检索到可展示的近期新闻/公告。建议启用 AkShare 新闻源、巨潮公告源或补充本地公告材料。"]
    items.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in items[:top_k]]


def _doc_date_text(doc: SourceDocument) -> str:
    dt = doc.published_at or doc.fetched_at
    return dt.isoformat()[:10] if dt else "date-unknown"


def _looks_like_announcement(doc: SourceDocument) -> bool:
    haystack = f"{doc.source} {doc.source_type} {doc.title}".lower()
    hints = ["announcement", "filing", "cninfo", "公告", "财报", "年报", "季报", "半年报"]
    return any(hint.lower() in haystack for hint in hints)


def _build_valuation_view(companies: list[TopicCompanyView], candidates: list[InvestmentCandidate]) -> list[str]:
    items: list[str] = []
    for row in companies[:6]:
        financial = row.financial
        price = row.price
        if financial is None and price is None:
            items.append(
                f"{row.company.name}: 暂无结构化财务和行情快照，当前只能先用公告、新闻和产业链位置做定性判断。"
            )
            continue

        facts: list[str] = []
        if financial:
            if financial.period:
                facts.append(f"period={financial.period}")
            if financial.revenue_growth is not None:
                facts.append(f"revenue growth {financial.revenue_growth:.1f}%")
            if financial.profit_growth is not None:
                facts.append(f"profit growth {financial.profit_growth:.1f}%")
            if financial.gross_margin is not None:
                facts.append(f"gross margin {financial.gross_margin:.1f}%")
            if financial.roe is not None:
                facts.append(f"ROE {financial.roe:.1f}%")
            if financial.debt_ratio is not None:
                facts.append(f"debt ratio {financial.debt_ratio:.1f}%")
            if financial.market_cap is not None:
                facts.append(f"market cap {financial.market_cap:.1f}")

            valuation_parts = []
            if financial.pe is not None:
                valuation_parts.append(f"PE {financial.pe:.1f}")
            if financial.pb is not None:
                valuation_parts.append(f"PB {financial.pb:.1f}")
            if financial.ps is not None:
                valuation_parts.append(f"PS {financial.ps:.1f}")
            facts.append(", ".join(valuation_parts) if valuation_parts else "PE/PB/PS missing")
        else:
            facts.append("financial snapshot missing")

        if price:
            price_parts = []
            if price.date:
                price_parts.append(price.date)
            if price.close is not None:
                price_parts.append(f"close {price.close:.2f}")
            if price.change_1m is not None:
                price_parts.append(f"1M {price.change_1m:.1f}%")
            if price.change_3m is not None:
                price_parts.append(f"3M {price.change_3m:.1f}%")
            if price.change_6m is not None:
                price_parts.append(f"6M {price.change_6m:.1f}%")
            if price.change_12m is not None:
                price_parts.append(f"12M {price.change_12m:.1f}%")
            if price_parts:
                facts.append("price " + ", ".join(price_parts))
        else:
            facts.append("price snapshot missing")

        items.append(f"{row.company.name}: {'; '.join(facts)}. {_valuation_judgement(financial, price)}")

    if candidates:
        top = candidates[0]
        if top.price and top.price.change_3m is not None and top.price.change_3m > 40:
            items.append(f"{top.company.name}: 短期涨幅较大，需要警惕拥挤交易和估值压缩风险。")
        elif top.financial and top.financial.profit_growth is not None and top.financial.profit_growth > 20:
            items.append(f"{top.company.name}: 若利润增长能够持续兑现，当前定价仍需要结合估值分位和订单能见度继续验证。")

    if not items:
        items.append("当前估值信息仍不完整，建议补充价格、PE/PB/PS、历史分位、同行对比和利润预测数据。")
    return items


def _valuation_judgement(financial: FinancialSnapshot | None, price: PriceSnapshot | None) -> str:
    signals: list[str] = []
    if financial:
        if financial.profit_growth is not None and financial.profit_growth >= 30:
            signals.append("利润增长较强，适合进一步核验增长是否来自主业订单和毛利率改善")
        elif financial.profit_growth is not None and financial.profit_growth < 0:
            signals.append("利润同比承压，需要重点看费用、价格和需求变化")
        if financial.revenue_growth is not None and financial.revenue_growth >= 20:
            signals.append("收入端仍有成长性")
        if financial.gross_margin is not None and financial.gross_margin < 15:
            signals.append("毛利率偏低，估值弹性可能受竞争格局限制")
        if financial.debt_ratio is not None and financial.debt_ratio >= 65:
            signals.append("负债率偏高，需关注现金流和扩产周期")
        if financial.pe is None and financial.pb is None and financial.ps is None:
            signals.append("缺少核心估值倍数，当前不宜直接给出便宜/昂贵判断")
    if price:
        if price.change_3m is not None and price.change_3m >= 35:
            signals.append("近三个月涨幅较高，短期风险收益比需要打折")
        elif price.change_3m is not None and price.change_3m <= -20:
            signals.append("近三个月回撤较大，需区分基本面恶化还是情绪出清")
    if not signals:
        return "估值判断中性，下一步应补充同行估值、历史分位和盈利预测。"
    return "；".join(signals) + "。"


def _financial_brief(financial: FinancialSnapshot | None) -> str:
    if financial is None:
        return "暂无结构化财务快照"
    items: list[str] = []
    if financial.period:
        items.append(financial.period)
    if financial.revenue_growth is not None:
        items.append(f"营收同比 {financial.revenue_growth:.1f}%")
    if financial.profit_growth is not None:
        items.append(f"利润同比 {financial.profit_growth:.1f}%")
    if financial.gross_margin is not None:
        items.append(f"毛利率 {financial.gross_margin:.1f}%")
    if financial.roe is not None:
        items.append(f"ROE {financial.roe:.1f}%")
    if financial.debt_ratio is not None:
        items.append(f"负债率 {financial.debt_ratio:.1f}%")
    if financial.market_cap is not None:
        items.append(f"市值 {financial.market_cap:.1f}")
    multiples = []
    if financial.pe is not None:
        multiples.append(f"PE {financial.pe:.1f}")
    if financial.pb is not None:
        multiples.append(f"PB {financial.pb:.1f}")
    if financial.ps is not None:
        multiples.append(f"PS {financial.ps:.1f}")
    if multiples:
        items.append("/".join(multiples))
    return "；".join(items) or "暂无结构化财务快照"


def _price_brief(price: PriceSnapshot | None) -> str:
    if price is None:
        return "暂无价格快照"
    items: list[str] = []
    if price.date:
        items.append(f"日期 {price.date}")
    if price.close is not None:
        items.append(f"收盘价 {price.close:.2f}")
    if price.change_1m is not None:
        items.append(f"1M {price.change_1m:.1f}%")
    if price.change_3m is not None:
        items.append(f"3M {price.change_3m:.1f}%")
    if price.change_6m is not None:
        items.append(f"6M {price.change_6m:.1f}%")
    if price.change_12m is not None:
        items.append(f"12M {price.change_12m:.1f}%")
    return "；".join(items) or "暂无价格快照"


def render_professional_topic_report(report: TopicResearchReport) -> str:
    lines = [
        f"# {report.topic} 投资研究报告",
        "",
        f"> 生成时间：{report.generated_at}",
        f"> 输入类型：{report.intent.input_type}",
        f"> 当前建议等级：{report.recommendation}",
        "> 说明：本报告基于公开资料、产业链图谱、公告/财报材料和规则推理自动生成，仅用于研究支持，不构成投资建议。",
        "",
        "## 一、Executive Summary",
        "",
        _render_executive_summary(report),
        "",
        "## 二、近期热点新闻与公告",
        "",
    ]
    for item in _hot_news_items(report):
        lines.append(f"- {item}")

    lines.extend(["", "## 三、产业链逻辑", ""])
    for item in report.core_logic:
        lines.append(f"- {item}")
    lines.append(f"- 核心节点：{', '.join(report.matched_nodes) or '-'}")
    lines.append(f"- 上游节点：{', '.join(report.upstream_nodes) or '-'}")
    lines.append(f"- 下游节点：{', '.join(report.downstream_nodes) or '-'}")

    lines.extend(["", "## 四、重点公司详表", ""])
    if not report.companies:
        lines.append("- 当前未匹配到重点公司。")
    for idx, row in enumerate(report.companies[:10], 1):
        lines.append(f"### {idx}. {row.company.name} ({row.company.ticker or '-'})")
        lines.append("")
        lines.append(f"- 匹配分：{row.match_score:.2f}")
        lines.append(f"- 行业：{row.company.industry or '-'}")
        lines.append(f"- 业务定位：{row.business_summary or row.company.role or '-'}")
        lines.append(f"- 核心产品/能力：{', '.join(row.key_products) or '-'}")
        lines.append(f"- 覆盖产业链节点：{', '.join(row.company.nodes) or '-'}")
        lines.append(f"- 上游节点：{', '.join(row.upstream_nodes) or '-'}")
        lines.append(f"- 上游相关公司：{', '.join(row.upstream_companies) or '-'}")
        lines.append(f"- 下游节点：{', '.join(row.downstream_nodes) or '-'}")
        lines.append(f"- 下游相关公司：{', '.join(row.downstream_companies) or '-'}")
        lines.append(f"- 财务快照：{_financial_brief(row.financial)}")
        lines.append(f"- 行情快照：{_price_brief(row.price)}")
        if row.a_share_reports:
            lines.append("- 最近财报/公告材料：")
            for doc in row.a_share_reports[:5]:
                lines.append(f"  - {_doc_date_text(doc)} | {doc.title} | {doc.url or doc.path or '-'}")
        else:
            lines.append("- 最近财报/公告材料：暂未抓取到。")
        lines.append("")

    lines.extend(["## 五、财务与估值观察", ""])
    for item in report.valuation_view:
        lines.append(f"- {item}")

    lines.extend(["", "## 六、市场情绪与事件信号", ""])
    lines.append(f"- 情绪分数：{report.sentiment_score:.2f}")
    lines.append(f"- 情绪标签：{report.sentiment_label}")
    if report.signals:
        for signal in report.signals[:8]:
            lines.append(
                f"- {signal.title} | event={signal.event.event_type} | score={signal.weak_signal_score:.2f} | attention={signal.attention_score:.1f}"
            )
    else:
        lines.append("- 当前缺少足够强的结构化事件信号。")

    lines.extend(["", "## 七、情景分析", ""])
    for scenario in report.scenarios:
        lines.append(f"### {scenario.horizon}")
        lines.append(f"- 乐观：{scenario.optimistic}")
        lines.append(f"- 中性：{scenario.base}")
        lines.append(f"- 悲观：{scenario.pessimistic}")
        lines.append("")

    lines.extend(["## 八、跟踪指标", ""])
    for item in report.tracking_indicators:
        lines.append(f"- {item}")

    lines.extend(["", "## 九、主要风险", ""])
    for item in _collect_report_risks(report):
        lines.append(f"- {item}")

    lines.extend([
        "",
        "## 十、结论",
        "",
        _render_conclusion(report),
        "",
        "## 免责声明",
        "",
        "本报告仅用于信息整理和投研辅助，不构成任何投资建议。涉及公告、财报和行情信息的结论仍需结合原始材料复核。",
        "",
    ])
    return "\n".join(lines)
