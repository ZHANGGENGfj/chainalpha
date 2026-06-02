from __future__ import annotations

from typing import Any

from chainalpha.investment.report_extractors import summarize_financial_report_document


def build_professional_evidence_pack(
    report: dict[str, Any],
    *,
    local_financial_summaries: list[dict[str, Any]] | None = None,
    broker_report_summaries: list[dict[str, Any]] | None = None,
    max_docs: int = 12,
    max_signals: int = 10,
    max_a_share_reports: int = 3,
) -> dict[str, Any]:
    companies = report.get("companies") or []
    primary = companies[0] if companies else {}
    company = primary.get("company") or {}
    financial = primary.get("financial")
    price = primary.get("price")
    docs = _dedup_docs([*(report.get("selected_documents") or []), *(report.get("recent_documents") or [])])[:max_docs]
    a_share_docs = primary.get("a_share_reports") or []
    a_share_summaries = [
        summarize_financial_report_document(doc)
        for doc in a_share_docs[:max_a_share_reports]
        if isinstance(doc, dict)
    ]
    financial_summaries = [*a_share_summaries, *(local_financial_summaries or [])]
    broker_summaries = broker_report_summaries or []

    warnings = list(report.get("warnings") or [])
    if financial and _snapshot_confidence(financial) == "low":
        warnings.append("财务快照来自新闻/社交文本规则抽取，不能等同于财报数据库；报告中只能作为待核验线索。")
    if price and _snapshot_confidence(price) == "low":
        warnings.append("行情快照来自新闻文本规则抽取，不能等同于行情数据库；报告中只能作为待核验线索。")
    if financial_summaries:
        warnings.append("已启用财报/PDF摘要卡片；表格精确值仍应回到原文页码或结构化财务源复核。")
    if broker_summaries:
        warnings.append("已启用机构研报摘要卡片；机构观点不等于事实，需要与公告、财报和行情交叉验证。")

    return {
        "topic": report.get("topic"),
        "generated_at": report.get("generated_at"),
        "target_company": {
            "name": company.get("name"),
            "ticker": company.get("ticker"),
            "exchange": company.get("exchange"),
            "industry": company.get("industry"),
            "role": company.get("role"),
            "nodes": company.get("nodes") or [],
            "aliases": company.get("aliases") or [],
        },
        "business_model": {
            "business_summary": primary.get("business_summary"),
            "key_products": primary.get("key_products") or [],
            "upstream_nodes": primary.get("upstream_nodes") or [],
            "downstream_nodes": primary.get("downstream_nodes") or [],
            "note": "上下游节点来自系统产业链图谱，只能用于分析传导路径，不能自动视为目标公司的客户或供应商事实。",
        },
        "peer_or_chain_companies": {
            "upstream_companies": primary.get("upstream_companies") or [],
            "downstream_companies": primary.get("downstream_companies") or [],
            "note": "这些公司是产业链相关公司，不等于目标公司可比估值表，除非新闻证据明确支持。",
        },
        "financial_snapshot": _snapshot_pack(financial),
        "price_snapshot": _snapshot_pack(price),
        "financial_report_summaries": financial_summaries[:max_a_share_reports + 8],
        "broker_report_summaries": broker_summaries[:8],
        "valuation_view": report.get("valuation_view") or [],
        "news_evidence": [_doc_pack(doc) for doc in docs],
        "signals": [_signal_pack(sig) for sig in (report.get("signals") or [])[:max_signals]],
        "sentiment": {
            "score": report.get("sentiment_score"),
            "label": report.get("sentiment_label"),
        },
        "scenarios": report.get("scenarios") or [],
        "tracking_indicators": report.get("tracking_indicators") or [],
        "data_quality_warnings": warnings,
        "mandatory_data_gaps": _mandatory_data_gaps(financial, price, financial_summaries, broker_summaries),
    }


def _snapshot_pack(snapshot: Any) -> dict[str, Any] | None:
    if not snapshot:
        return None
    data = dict(snapshot) if isinstance(snapshot, dict) else dict(getattr(snapshot, "__dict__", {}))
    confidence = _snapshot_confidence(data)
    return {
        "confidence": confidence,
        "usable_as": "待核验线索" if confidence == "low" else "结构化数据",
        "data": data,
    }


def _snapshot_confidence(snapshot: Any) -> str:
    metadata = (snapshot or {}).get("metadata") if isinstance(snapshot, dict) else getattr(snapshot, "metadata", {})
    source_type = str((metadata or {}).get("source_type") or "").lower()
    provider = str((metadata or {}).get("provider") or "").lower()
    trusted_tokens = ("financial", "price_data", "quote", "mootdx", "akshare", "tushare", "announcement", "filing")
    return "high" if any(token in source_type or token in provider for token in trusted_tokens) else "low"


def _dedup_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for doc in docs:
        key = str(doc.get("url") or doc.get("title") or doc.get("id") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out


def _doc_pack(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": doc.get("title"),
        "source": doc.get("source"),
        "source_type": doc.get("source_type"),
        "published_at": doc.get("published_at"),
        "url": doc.get("url"),
        "content_excerpt": str(doc.get("content") or "")[:1000],
        "metadata_hint": {
            "platform": (doc.get("metadata") or {}).get("platform"),
            "file": (doc.get("metadata") or {}).get("file"),
        },
    }


def _signal_pack(signal: dict[str, Any]) -> dict[str, Any]:
    event = signal.get("event") or {}
    hypothesis = signal.get("hypothesis") or {}
    return {
        "title": signal.get("title"),
        "stage": signal.get("stage"),
        "weak_signal_score": signal.get("weak_signal_score"),
        "attention_score": signal.get("attention_score"),
        "event_type": event.get("event_type"),
        "source_type": event.get("source_type"),
        "source": event.get("source"),
        "supply_chain_nodes": event.get("supply_chain_nodes") or [],
        "evidence": event.get("evidence") or [],
        "causal_path": hypothesis.get("causal_path") or [],
        "risks": signal.get("risks") or [],
    }


def _mandatory_data_gaps(
    financial: Any,
    price: Any,
    financial_summaries: list[dict[str, Any]],
    broker_summaries: list[dict[str, Any]],
) -> list[str]:
    gaps: list[str] = []
    if not financial_summaries and (not financial or _snapshot_confidence(financial) == "low"):
        gaps.extend([
            "最新年报/季报的营业收入、归母净利润、扣非净利润、毛利率、净利率、ROE。",
            "经营现金流、存货、应收账款、资产负债率、资本开支等利润质量指标。",
        ])
    if financial:
        data = financial if isinstance(financial, dict) else getattr(financial, "__dict__", {})
        for label, key in [
            ("报告期", "period"),
            ("经营现金流", "operating_cash_flow"),
            ("资产负债率", "debt_ratio"),
            ("ROE", "roe"),
            ("PE", "pe"),
            ("PS", "ps"),
        ]:
            if data.get(key) is None:
                gaps.append(f"{label}缺失。")
    if not price or _snapshot_confidence(price) == "low":
        gaps.append("真实行情快照、近1/3/6/12个月涨跌幅、资金流向数据缺失。")
    if not broker_summaries:
        gaps.append("机构研报共识、盈利预测均值/高低值、估值假设缺失。")
    gaps.append("历史估值分位、同行估值表、三年盈利预测和敏感性分析需要结构化数据源补充。")
    return list(dict.fromkeys(gaps))
