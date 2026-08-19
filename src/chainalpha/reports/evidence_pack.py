from __future__ import annotations

import re
from typing import Any

from chainalpha.investment.report_extractors import (
    extract_text_from_file,
    summarize_broker_report_text,
    summarize_financial_report_document,
)


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
    docs = _dedup_docs(
        [
            *(report.get("selected_documents") or []),
            *(report.get("recent_documents") or []),
            *_tool_research_docs(report),
        ]
    )[:max_docs]
    a_share_docs = primary.get("a_share_reports") or []
    a_share_summaries = [
        summarize_financial_report_document(doc)
        for doc in a_share_docs[:max_a_share_reports]
        if isinstance(doc, dict)
    ]
    media_crawler_financial_summaries = _media_crawler_financial_summaries(docs, limit=max_a_share_reports)
    financial_summaries = [*a_share_summaries, *media_crawler_financial_summaries, *(local_financial_summaries or [])]
    broker_summaries = [*_media_crawler_broker_summaries(docs), *(broker_report_summaries or [])]

    warnings = list(report.get("warnings") or [])
    if financial and _snapshot_confidence(financial) == "low":
        warnings.append("财务快照来自新闻/社交文本规则抽取，不能等同于财报数据库；报告中只能作为待核验线索。")
    if price and _snapshot_confidence(price) == "low":
        warnings.append("行情快照来自新闻文本规则抽取，不能等同于行情数据库；报告中只能作为待核验线索。")
    if financial_summaries:
        warnings.append("已启用财报/PDF摘要卡片；表格精确值仍应回到原文页码或结构化财务源复核。")
    if broker_summaries:
        warnings.append("已启用机构研报摘要卡片；机构观点不等于事实，需要与公告、财报和行情交叉验证。")

    query_expansion = report.get("query_expansion") or {}
    topic_class = query_expansion.get("topic_class") or "unknown"
    raw_signals = report.get("signals") or []
    usable_signals = [sig for sig in raw_signals if not _is_low_quality_signal(sig)]
    dropped_signal_count = len(raw_signals) - len(usable_signals)
    if dropped_signal_count > 0:
        warnings.append(
            f"已剔除 {dropped_signal_count} 条低置信度规则信号；这些信号多来自F10/财务网页/搜索页，"
            "不得当作订单、政策、扩产或客户认证事实。"
        )
    research_type = str(report.get("research_type") or "").strip().lower()
    if research_type not in {"company", "industry-chain"}:
        research_type = "industry-chain" if topic_class in {"raw_material_chain", "component_chain", "industry_chain"} else "company"
    web_financial_facts = _financial_web_fact_pack(report.get("web_financial_facts") or {})
    return {
        "topic": report.get("topic"),
        "generated_at": report.get("generated_at"),
        "query_expansion": query_expansion,
        "user_preferences": _user_preference_pack(report.get("user_preference_memory") or {}),
        "topic_class": topic_class,
        "research_type": research_type,
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
            "corrected_chain_logic": _corrected_chain_logic(company, primary),
            "note": "原始上下游节点来自系统产业链图谱，只能用于分析传导路径，不能自动视为目标公司的客户或供应商事实；写报告时优先使用 corrected_chain_logic。",
        },
        "peer_or_chain_companies": {
            "upstream_companies": primary.get("upstream_companies") or [],
            "downstream_companies": primary.get("downstream_companies") or [],
            "note": "这些公司是产业链相关公司，不等于目标公司可比估值表，除非新闻证据明确支持。",
        },
        "financial_snapshot": _snapshot_pack(financial),
        "price_snapshot": _snapshot_pack(price),
        "web_financial_facts": web_financial_facts,
        "financial_fact_matrix": _financial_fact_matrix(web_financial_facts),
        "financial_report_summaries": _trim_deep(financial_summaries[:max_a_share_reports + 8]),
        "broker_report_summaries": _trim_deep(broker_summaries[:8]),
        "six_dimension_analysis": _trim_deep(report.get("six_dimension_analysis") or []),
        "valuation_view": report.get("valuation_view") or [],
        "news_evidence": [_doc_pack(doc) for doc in docs],
        "tool_research_summary": _tool_research_summary(report),
        "signals": [_signal_pack(sig) for sig in usable_signals[:max_signals]],
        "filtered_signal_count": dropped_signal_count,
        "sentiment": {
            "score": report.get("sentiment_score"),
            "label": report.get("sentiment_label"),
        },
        "scenarios": report.get("scenarios") or [],
        "tracking_indicators": report.get("tracking_indicators") or [],
        "data_quality_warnings": _humanize_warnings(warnings),
        "writing_guardrails": _writing_guardrails(),
        "mandatory_data_gaps": _mandatory_data_gaps(financial, price, financial_summaries, broker_summaries),
    }


def _user_preference_pack(raw: Any, limit: int = 5) -> list[str]:
    """Expose recalled preferences as writing constraints, never as evidence."""
    if not isinstance(raw, dict):
        return []
    values: list[str] = []
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("memory") or "").strip()
        if text:
            values.append(text[:300])
    return values[:limit]


def _financial_web_fact_pack(raw: Any, *, max_per_category: int = 2) -> dict[str, list[dict[str, Any]]]:
    """Keep the most useful financial-page snippets visible to the report LLM."""
    if not isinstance(raw, dict):
        return {}
    packed: dict[str, list[dict[str, Any]]] = {}
    for category, facts in raw.items():
        if not isinstance(facts, list):
            continue
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            url = str(fact.get("url") or "").strip()
            excerpt = str(fact.get("excerpt") or "").strip()
            key = url or excerpt[:100]
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "source": fact.get("source") or "公开财务网页",
                    "title": fact.get("title") or category,
                    "url": url,
                    "published_at": fact.get("published_at") or "",
                    "period_or_basis": _financial_period_or_basis(excerpt, fact.get("published_at") or ""),
                    "confidence": fact.get("confidence") or "low",
                    "matched_terms": fact.get("matched_terms") or [],
                    "excerpt": excerpt[:700],
                }
            )
            if len(rows) >= max_per_category:
                break
        if rows:
            packed[str(category)] = rows
    return packed


def _financial_fact_matrix(facts_by_category: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """A compact, report-ready index: one traceable fact per requested category."""
    rows: list[dict[str, Any]] = []
    for category, facts in facts_by_category.items():
        if not facts:
            continue
        fact = facts[0]
        rows.append(
            {
                "category": category,
                "evidence": fact.get("excerpt") or "",
                "period_or_basis": fact.get("period_or_basis") or "口径未识别",
                "source": fact.get("source") or "",
                "url": fact.get("url") or "",
                "confidence": fact.get("confidence") or "low",
            }
        )
    return rows


def _financial_period_or_basis(excerpt: str, published_at: Any) -> str:
    """Expose an explicit reporting period when a financial web page contains one."""
    text = str(excerpt or "")
    patterns = (
        r"20\d{2}\s*年\s*(?:第?[一二三四1-4]季度|中报|半年报|年报)",
        r"20\d{2}\s*[Qq][1-4]",
        r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}",
        r"(?:本期|报告期|同比|环比|TTM)",
    )
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(re.findall(pattern, text))
    if matches:
        return "；".join(dict.fromkeys(matches))[:80]
    return f"网页抓取时间：{published_at}" if published_at else "口径未识别"


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


def _trim_deep(value: Any, *, max_string: int = 900, max_list: int = 12) -> Any:
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, list):
        return [_trim_deep(item, max_string=max_string, max_list=max_list) for item in value[:max_list]]
    if isinstance(value, dict):
        return {key: _trim_deep(item, max_string=max_string, max_list=max_list) for key, item in value.items()}
    return value


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


def _tool_research_docs(report: dict[str, Any]) -> list[dict[str, Any]]:
    tool_research = report.get("tool_research") or {}
    docs: list[dict[str, Any]] = []
    for item in tool_research.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        docs.append(
            {
                "title": item.get("title") or "LangGraph工具检索证据",
                "source": item.get("source") or "langgraph_tool_agent",
                "source_type": item.get("source_type") or "tool_research",
                "published_at": item.get("published_at"),
                "url": item.get("url"),
                "path": item.get("path"),
                "content": item.get("content_excerpt") or item.get("content") or "",
                "metadata": {"platform": "langgraph_tool_agent", "record": item},
            }
        )
    return docs


def _tool_research_summary(report: dict[str, Any]) -> dict[str, Any]:
    tool_research = report.get("tool_research") or {}
    if not tool_research:
        return {}
    return {
        "framework": tool_research.get("framework"),
        "steps": [
            {
                "action": step.get("action"),
                "query": step.get("query"),
                "documents_found": step.get("documents_found"),
                "warning": step.get("warning"),
            }
            for step in (tool_research.get("steps") or [])[:8]
            if isinstance(step, dict)
        ],
        "evidence_count": len(tool_research.get("evidence") or []),
        "warnings": tool_research.get("warnings") or [],
    }


def _media_crawler_financial_summaries(docs: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for doc in docs:
        metadata = doc.get("metadata") or {}
        if metadata.get("platform") != "cninfo_reports":
            continue
        record = metadata.get("record") or {}
        pdf_path = record.get("pdf_path") or record.get("pdf_path".upper())
        resolved_pdf = _resolve_media_crawler_pdf_path(pdf_path, metadata.get("file"))
        if resolved_pdf:
            try:
                text = extract_text_from_file(resolved_pdf)
            except Exception:
                text = ""
            if text:
                doc = {
                    **doc,
                    "content": text,
                    "path": str(resolved_pdf),
                    "metadata": {**metadata, "pdf_cached_path": str(resolved_pdf)},
                }
        summaries.append(summarize_financial_report_document(doc))
        if len(summaries) >= limit:
            break
    return summaries


def _media_crawler_broker_summaries(docs: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for doc in docs:
        metadata = doc.get("metadata") or {}
        if metadata.get("platform") != "broker_reports":
            continue
        record = metadata.get("record") or {}
        title = str(doc.get("title") or record.get("标题") or "机构研报")
        text = "\n".join(
            part
            for part in [
                str(doc.get("content") or ""),
                str(record.get("content") or ""),
                str(record.get("raw") or ""),
            ]
            if part
        )
        summaries.append(
            summarize_broker_report_text(
                title=title,
                text=text,
                source=str(doc.get("source") or "media_crawler:broker_reports"),
                path=str(doc.get("path") or ""),
            )
        )
        if len(summaries) >= limit:
            break
    return summaries


def _resolve_media_crawler_pdf_path(value: Any, source_file: Any) -> str | None:
    if not value:
        return None
    from pathlib import Path

    raw = Path(str(value))
    candidates = [raw]
    if source_file:
        file_path = Path(str(source_file))
        parents = list(file_path.parents)
        if len(parents) >= 4:
            candidates.append(parents[3] / raw)
        if len(parents) >= 3:
            candidates.append(parents[2] / raw)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


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


def _is_low_quality_signal(signal: dict[str, Any]) -> bool:
    event = signal.get("event") or {}
    event_type = str(event.get("event_type") or signal.get("event_type") or "").upper()
    source = str(event.get("source") or signal.get("source") or "")
    source_type = str(event.get("source_type") or signal.get("source_type") or "")
    title = str(signal.get("title") or event.get("description") or "")
    evidence = " ".join(str(item) for item in (event.get("evidence") or []))
    text = f"{source} {source_type} {title} {evidence}".lower()

    weak_source_tokens = [
        "f10",
        "financial_web",
        "akshare://",
        "新浪财务报表",
        "财务报表",
        "财务概况",
        "财务分析",
        "经营分析",
        "资本结构",
        "资产负债表",
        "利润表",
        "现金流量表",
        "search",
        "搜索",
    ]
    event_needs_primary_source = {
        "ORDER_SIGNED",
        "CUSTOMER_CERTIFICATION",
        "CAPACITY_EXPANSION",
        "CAPEX_INCREASE",
        "POLICY_SUPPORT",
        "TECH_UPGRADE",
        "PRODUCT_LAUNCH",
    }
    if event_type in event_needs_primary_source and any(token in text for token in weak_source_tokens):
        return True
    if not title.strip():
        return True
    return False


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


def _corrected_chain_logic(company: dict[str, Any], primary: dict[str, Any]) -> dict[str, Any]:
    nodes = set(company.get("nodes") or [])
    industry = str(company.get("industry") or "").lower()
    if "Optical Module" in nodes or "optical" in industry:
        return {
            "target_position": "高速光模块/光通信收发模块环节。",
            "demand_driver": "AI CapEx、GPU/AI服务器集群扩张、交换机端口速率升级会传导至高速光模块需求。",
            "upstream_supply": ["光芯片/激光器", "电芯片/DSP", "硅光/光器件", "PCB/结构件", "产能与良率"],
            "direct_downstream": ["云厂商/AI数据中心", "交换机与通信设备商", "服务器/数据中心网络集成链条"],
            "not_upstream_or_downstream_fact": [
                "GPU集群是终端需求传导源，不应写成目标公司的直接上游供应商。",
                "激光器/光器件通常更偏光模块上游供应链，不应机械写成下游客户。",
                "产业链相关公司不等于客户、供应商或可比公司，除非新闻、公告或研报明确证明。",
            ],
            "raw_graph_nodes": {
                "upstream_nodes": primary.get("upstream_nodes") or [],
                "downstream_nodes": primary.get("downstream_nodes") or [],
            },
        }
    return {
        "target_position": "以目标公司的公开业务和节点为准。",
        "raw_graph_nodes": {
            "upstream_nodes": primary.get("upstream_nodes") or [],
            "downstream_nodes": primary.get("downstream_nodes") or [],
        },
        "not_upstream_or_downstream_fact": ["产业链图谱节点仅代表传导关系，不代表实际客户或供应商。"],
    }


def _writing_guardrails() -> list[str]:
    return [
        "不要在报告正文出现 news_evidence、signals、financial_snapshot、broker_report_summaries、data_quality_warnings 等程序字段名。",
        "没有证据时，禁止写具体客户名称，例如英伟达、微软、华为、阿里云等。",
        "没有证据时，禁止写具体阈值或时间点，例如800G渗透率30%、良率60%、全球份额15%、2026年Q2放量等。",
        "没有证据时，禁止写具体竞争结论，例如某公司压制目标公司、目标公司份额提升至某比例。",
        "缺少硬数据时，应写验证框架和需要补充的数据，而不是举未经证实的数字例子。",
    ]


def _humanize_warnings(warnings: list[str]) -> list[str]:
    out: list[str] = []
    for warning in warnings:
        text = str(warning)
        if "ProxyError" in text or "ConnectionError" in text or "PermissionError" in text:
            out.append("部分联网数据源访问失败，相关财报、行情或公告需要本地文件/结构化数据源补充。")
        elif "A-share filings/PDF parsing disabled" in text:
            out.append("本次未启用或未成功获取A股公告/PDF解析，财报事实需要用本地财报或巨潮公告补充。")
        else:
            out.append(text)
    return list(dict.fromkeys(out))
