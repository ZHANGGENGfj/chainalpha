from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chainalpha.connectors.base import FetchRequest, SourceDocument
from chainalpha.connectors.search_page import SearchPageConnector


MISSING_HINTS = [
    "数据缺口",
    "缺少",
    "暂无",
    "未找到",
    "无法判断",
    "无法验证",
    "待补充",
    "待核验",
    "证据不足",
    "来源缺失",
]

SOURCE_HINTS = [
    "http://",
    "https://",
    "来源",
    "公告",
    "财报",
    "研报",
    "证券报",
    "交易所",
    "巨潮",
    "东方财富",
    "同花顺",
]

STRONG_CONCLUSION_HINTS = [
    "强烈推荐",
    "买入评级",
    "推荐评级",
    "明确受益",
    "显著受益",
    "确定性",
    "必然",
    "目标价",
    "上调评级",
    "核心标的",
    "最受益",
]

TRUSTED_SOURCE_TOKENS = [
    "announcement",
    "filing",
    "exchange",
    "annual_report",
    "财报",
    "公告",
    "交易所",
    "巨潮",
]

MEDIUM_SOURCE_TOKENS = [
    "research",
    "broker",
    "mainstream_media",
    "media_crawler",
    "finance_news",
    "研报",
    "券商",
    "证券报",
    "媒体",
    "新闻",
]


@dataclass
class EvidenceItem:
    claim: str
    source: str
    confidence: str
    reason: str
    source_type: str = ""
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "source": self.source,
            "source_type": self.source_type,
            "url": self.url,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass
class EvidenceSummary:
    total: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    sourced: int = 0

    @property
    def usable(self) -> int:
        return self.high + self.medium

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "high": self.high,
            "medium": self.medium,
            "low": self.low,
            "sourced": self.sourced,
            "usable": self.usable,
        }


@dataclass
class ReportQualityIssue:
    category: str
    severity: str
    message: str
    query_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "query_hint": self.query_hint,
        }


@dataclass
class ReportQualityAudit:
    topic: str
    issues: list[ReportQualityIssue] = field(default_factory=list)
    supplement_queries: list[str] = field(default_factory=list)
    supplemental_documents: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    evidence_summary: EvidenceSummary = field(default_factory=EvidenceSummary)
    gate_status: str = "pass"
    conclusion_policy: str = "允许输出有证据支撑的谨慎结论。"

    @property
    def needs_supplement(self) -> bool:
        return bool(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "needs_supplement": self.needs_supplement,
            "gate": self.gate_status,
            "gate_status": self.gate_status,
            "conclusion_policy": self.conclusion_policy,
            "evidence_stats": self.evidence_summary.to_dict(),
            "evidence_summary": self.evidence_summary.to_dict(),
            "evidence_items": [item.to_dict() for item in self.evidence_items],
            "issues": [item.to_dict() for item in self.issues],
            "supplement_queries": self.supplement_queries,
            "supplemental_documents": self.supplemental_documents,
            "warnings": self.warnings,
        }


def run_report_quality_agent(
    *,
    topic: str,
    markdown: str,
    report_data: dict[str, Any] | None = None,
    enable_web_search: bool = True,
    max_queries: int = 8,
    max_docs: int = 10,
) -> tuple[str, ReportQualityAudit]:
    """Check a final report and append evidence strength / conclusion-boundary notes."""

    audit = ReportQualityAudit(topic=topic)
    report_kind = str((report_data or {}).get("_report_kind") or "")
    audit.evidence_items = _extract_evidence_items(report_data or {})
    audit.evidence_summary = _summarize_evidence(audit.evidence_items)
    audit.issues = _detect_quality_issues(markdown, audit.evidence_summary, report_kind=report_kind)
    audit.gate_status, audit.conclusion_policy = _evaluate_quality_gate(
        markdown,
        audit.evidence_summary,
        report_kind=report_kind,
    )
    audit.issues.extend(_gate_issues(markdown, audit.gate_status, audit.evidence_summary))
    audit.issues = _dedup_issues(audit.issues)
    audit.supplement_queries = _build_supplement_queries(topic, audit.issues, report_data)[:max_queries]

    if report_kind == "chain_explainer":
        enable_web_search = False

    if enable_web_search and audit.supplement_queries:
        docs, warnings = _fetch_supplemental_documents(audit.supplement_queries, limit=max_docs)
        audit.warnings.extend(warnings)
        audit.supplemental_documents = [_doc_to_dict(doc) for doc in docs[:max_docs]]

    checked = _apply_conclusion_gate(markdown, audit)
    return _append_quality_agent_addendum(checked, audit), audit


def _detect_quality_issues(markdown: str, evidence: EvidenceSummary, *, report_kind: str = "") -> list[ReportQualityIssue]:
    issues: list[ReportQualityIssue] = []
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    missing_lines = [line for line in lines if any(hint in line for hint in MISSING_HINTS)]
    source_count = sum(1 for line in lines if any(hint in line for hint in SOURCE_HINTS))

    if missing_lines:
        issues.append(
            ReportQualityIssue(
                category="data_gap",
                severity="high",
                message=f"报告中出现 {len(missing_lines)} 处数据缺口或待核验表述。",
                query_hint="财务数据 客户路径 产能约束 研报观点 公告来源",
            )
        )
    if report_kind != "chain_explainer" and source_count < 5 and evidence.sourced < 5:
        issues.append(
            ReportQualityIssue(
                category="source_gap",
                severity="high",
                message=f"报告正文和结构化证据中的来源线索偏少，正文识别到 {source_count} 处来源提示，证据台账中有来源的证据 {evidence.sourced} 条。",
                query_hint="原始公告 公司年报 券商研报 财经媒体报道",
            )
        )
    if "瓶颈" in markdown and not any(term in markdown for term in ["客户认证", "前五大客户", "供应商", "产能", "良率"]):
        issues.append(
            ReportQualityIssue(
                category="bottleneck_evidence_gap",
                severity="medium",
                message="报告讨论瓶颈权力，但客户路径、供应链、产能或良率证据不足。",
                query_hint="客户认证 前五大客户 供应商 产能 良率",
            )
        )
    if "估值" in markdown and not any(term in markdown for term in ["PE", "PB", "PS", "PEG", "市值"]):
        issues.append(
            ReportQualityIssue(
                category="valuation_gap",
                severity="medium",
                message="报告包含估值讨论，但缺少 PE/PB/PS/PEG/市值等可核验估值口径。",
                query_hint="估值 PE PB PS PEG 市值",
            )
        )
    return _dedup_issues(issues)


def _gate_issues(markdown: str, gate_status: str, evidence: EvidenceSummary) -> list[ReportQualityIssue]:
    if gate_status == "pass":
        return []
    has_strong_conclusion = _has_strong_conclusion(markdown)
    if has_strong_conclusion:
        return [
            ReportQualityIssue(
                category="strong_conclusion_low_evidence",
                severity="high",
                message=(
                    "报告存在强结论或评级式表述，但当前高/中置信度证据不足；"
                    f"证据总数 {evidence.total}，可用证据 {evidence.usable}。"
                ),
                query_hint="公告 财报 交易所问询 券商研报 产业链验证",
            )
        ]
    return [
        ReportQualityIssue(
            category="conclusion_boundary",
            severity="medium",
            message=f"质量闸门状态为 {gate_status}，报告结论需降级为待验证假设或观察线索。",
            query_hint="补充来源和交叉验证材料",
        )
    ]


def _evaluate_quality_gate(markdown: str, evidence: EvidenceSummary, *, report_kind: str = "") -> tuple[str, str]:
    has_strong_conclusion = _has_strong_conclusion(markdown)
    if report_kind == "chain_explainer" and not has_strong_conclusion:
        if evidence.total == 0:
            return "caution", "解释型回答允许给出概念框架，但公司、价格、订单和投资判断必须标为待验证。"
        return "pass", "解释型回答可输出概念和产业链框架；涉及投资结论时仍需公司级证据。"
    if evidence.total == 0 or evidence.usable == 0:
        return "block_strong_conclusion", "禁止输出强投资结论；所有看多/看空判断均应降级为待验证假设。"
    if evidence.usable < 3 and has_strong_conclusion:
        return "block_strong_conclusion", "证据数量不足以支撑强结论；评级、目标价、确定性判断必须降级。"
    if evidence.usable < 5 or evidence.low > evidence.usable:
        return "caution", "仅允许输出谨慎结论；需要明确写出证据不足和后续验证清单。"
    return "pass", "允许输出有证据支撑的谨慎结论，但仍需保留风险和数据缺口说明。"


def _extract_evidence_items(report_data: dict[str, Any]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []

    docs = [
        *(report_data.get("selected_documents") or []),
        *(report_data.get("recent_documents") or []),
        *(report_data.get("recent_news") or []),
        *_tool_research_evidence_docs(report_data),
    ]
    for doc in docs[:30]:
        if not isinstance(doc, dict):
            continue
        title = _compact(doc.get("title") or doc.get("summary") or doc.get("content") or "文档证据", limit=90)
        source = str(doc.get("source") or doc.get("provider") or doc.get("platform") or "")
        source_type = str(doc.get("source_type") or "")
        url = str(doc.get("url") or doc.get("path") or doc.get("source_path") or "")
        confidence, reason = _confidence_for_source(source=source, source_type=source_type, url=url)
        items.append(
            EvidenceItem(
                claim=title,
                source=source or url or "未标明来源",
                source_type=source_type,
                url=url,
                confidence=confidence,
                reason=reason,
            )
        )

    for signal in (report_data.get("signals") or [])[:20]:
        if not isinstance(signal, dict):
            continue
        event = signal.get("event") if isinstance(signal.get("event"), dict) else signal
        title = _compact(
            signal.get("title")
            or event.get("event_type")
            or event.get("description")
            or event.get("evidence")
            or "结构化信号",
            limit=90,
        )
        source = str(event.get("source") or signal.get("source") or "")
        source_type = str(event.get("source_type") or signal.get("source_type") or "")
        url = str(event.get("source_path") or event.get("url") or signal.get("source_path") or signal.get("url") or "")
        confidence, reason = _confidence_for_source(source=source, source_type=source_type, url=url)
        if _is_low_quality_signal_evidence(signal):
            confidence = "low"
            reason = "规则信号来自F10/财务网页/搜索页等弱来源，不能单独证明订单、政策、扩产或客户认证。"
        items.append(
            EvidenceItem(
                claim=title,
                source=source or url or "未标明来源",
                source_type=source_type,
                url=url,
                confidence=confidence,
                reason=reason,
            )
        )

    for category, facts in (report_data.get("web_financial_facts") or {}).items():
        if not isinstance(facts, list):
            continue
        for fact in facts[:10]:
            if not isinstance(fact, dict):
                continue
            confidence = _normalize_confidence(fact.get("confidence"))
            source_doc = fact.get("source_doc") if isinstance(fact.get("source_doc"), dict) else {}
            source = str(fact.get("source") or source_doc.get("source") or "")
            source_type = str(fact.get("source_type") or source_doc.get("source_type") or category or "")
            url = str(fact.get("url") or source_doc.get("url") or source_doc.get("path") or "")
            items.append(
                EvidenceItem(
                    claim=_compact(fact.get("title") or fact.get("text") or f"{category} 财务事实", limit=90),
                    source=source or url or "未标明来源",
                    source_type=source_type,
                    url=url,
                    confidence=confidence,
                    reason="来自财务事实抽取；沿用抽取模块给出的置信度。",
                )
            )

    for item in (report_data.get("valuation_view") or [])[:12]:
        if not item:
            continue
        items.append(
            EvidenceItem(
                claim=_compact(item, limit=90),
                source="估值视图/结构化行情与财务快照",
                source_type="valuation_view",
                confidence="medium" if any(token in str(item) for token in ["PE", "PB", "PS", "PEG", "市值"]) else "low",
                reason="估值观点来自结构化视图；若缺少估值口径则只能作为低置信度线索。",
            )
        )

    return _dedup_evidence(items)


def _summarize_evidence(items: list[EvidenceItem]) -> EvidenceSummary:
    summary = EvidenceSummary(total=len(items))
    for item in items:
        if item.confidence == "high":
            summary.high += 1
        elif item.confidence == "medium":
            summary.medium += 1
        else:
            summary.low += 1
        if item.source and item.source != "未标明来源":
            summary.sourced += 1
    return summary


def _confidence_for_source(*, source: str, source_type: str, url: str) -> tuple[str, str]:
    text = f"{source} {source_type} {url}".lower()
    if _looks_like_search_landing_page(source=source, source_type=source_type, url=url, title=""):
        return "low", "这是搜索入口页或结果页，只能作为检索线索，不能作为事实证据。"
    if any(token.lower() in text for token in TRUSTED_SOURCE_TOKENS):
        return "high", "来源类型接近公告、财报或交易所文件，可作为高置信度证据。"
    if any(token.lower() in text for token in MEDIUM_SOURCE_TOKENS):
        return "medium", "来源类型接近研报、财经媒体或新闻材料，适合作为中置信度证据。"
    if source or url:
        return "low", "有来源线索但权威性不足，需要交叉验证。"
    return "low", "缺少明确来源，只能作为待核验线索。"


def _normalize_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low"}:
        return text
    try:
        score = float(text)
    except ValueError:
        return "low"
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _apply_conclusion_gate(markdown: str, audit: ReportQualityAudit) -> str:
    if audit.gate_status == "pass":
        return markdown
    if audit.gate_status == "caution" and audit.conclusion_policy.startswith("解释型回答"):
        return markdown
    checked = _demote_strong_phrases(markdown) if audit.gate_status == "block_strong_conclusion" else markdown
    notice = (
        f"> 质量闸门：{audit.gate_status}。{audit.conclusion_policy}"
        " 下文若出现评级、目标价、确定性或显著受益等表述，应按待验证研究假设阅读，不构成投资建议。"
    )
    if "质量闸门：" in checked[:500]:
        return checked
    lines = checked.splitlines()
    if lines and lines[0].startswith("#"):
        return "\n".join([lines[0], "", notice, *lines[1:]])
    return f"{notice}\n\n{checked}"


def _has_strong_conclusion(markdown: str) -> bool:
    negative_terms = ["不", "不得", "不能", "禁止", "避免", "非", "无", "不是", "不应"]
    for hint in STRONG_CONCLUSION_HINTS:
        start = 0
        while True:
            idx = markdown.find(hint, start)
            if idx < 0:
                break
            context = markdown[max(0, idx - 12) : idx]
            if not any(term in context for term in negative_terms):
                return True
            start = idx + len(hint)
    return False


def _demote_strong_phrases(markdown: str) -> str:
    replacements = {
        "强烈推荐": "观察跟踪（质量闸门已降级，原表述为强烈推荐）",
        "买入评级": "观察评级（质量闸门已降级，原表述为买入评级）",
        "推荐评级": "观察评级（质量闸门已降级，原表述为推荐评级）",
        "明确受益": "可能受益但需验证",
        "显著受益": "可能受益但需验证",
        "确定性": "待验证确定性",
        "必然": "可能",
        "目标价": "情景测算价（非目标价）",
        "上调评级": "调整观察结论",
        "核心标的": "重点观察对象",
        "最受益": "可能受益较多但需验证",
    }
    checked = markdown
    for old, new in replacements.items():
        checked = checked.replace(old, new)
    return checked


def _build_supplement_queries(
    topic: str,
    issues: list[ReportQualityIssue],
    report_data: dict[str, Any] | None,
) -> list[str]:
    target = _target_name(topic, report_data)
    base_terms = [
        f"{target} 年报 主要财务数据 毛利率 现金流",
        f"{target} 前五大客户 前五大供应商",
        f"{target} 供应商 客户 产业链",
        f"{target} 产能 良率 扩产",
        f"{target} 券商研报 盈利预测 估值",
        f"{target} 公告 客户认证 订单",
        f"{target} 存货 应收账款 经营现金流",
        f"{target} 产业链瓶颈 核心零部件",
    ]
    issue_terms = [f"{target} {issue.query_hint}" for issue in issues if issue.query_hint]
    return _unique([*issue_terms, *base_terms], limit=12)


def _fetch_supplemental_documents(queries: list[str], *, limit: int) -> tuple[list[SourceDocument], list[str]]:
    connector = SearchPageConnector(
        "report_quality_web_search",
        search_url_templates=[
            "https://so.eastmoney.com/news/s?keyword={query}",
            "https://search.10jqka.com.cn/search?w={query}&tid=info",
            "https://www.cnstock.com/searchResult?word={query}",
            "https://www.cs.com.cn/searchlist.html?keyword={query}",
            "http://search.zqrb.cn/search.php?src=all&q={query}&f=_all&s=newsdate_DESC",
            "https://www.nbd.com.cn/search/article_search/?utf8=%E2%9C%93&q={query}",
        ],
        allowed_domains=[
            "eastmoney.com",
            "so.eastmoney.com",
            "10jqka.com.cn",
            "cnstock.com",
            "cs.com.cn",
            "zqrb.cn",
            "nbd.com.cn",
        ],
        fetch_linked_pages=True,
        max_linked_pages_per_query=2,
        timeout=8,
    )
    try:
        docs = connector.fetch(FetchRequest(query=" ".join(queries[:3]), keywords=queries, limit=limit))
        filtered = _filter_supplemental_documents(docs, limit=limit)
        warnings = []
        if len(filtered) < len(docs):
            warnings.append(f"filtered {len(docs) - len(filtered)} supplemental search landing/duplicate pages")
        return filtered, warnings
    except Exception as exc:  # noqa: BLE001
        return [], [f"supplemental web search failed: {type(exc).__name__}: {exc}"]


def _append_quality_agent_addendum(markdown: str, audit: ReportQualityAudit) -> str:
    lines = [
        markdown.rstrip(),
        "",
        "---",
        "",
        "## 报告质量闸门：证据强度与结论边界",
        "",
        f"- 闸门状态：{audit.gate_status}",
        f"- 结论边界：{audit.conclusion_policy}",
        (
            "- 证据统计："
            f"共 {audit.evidence_summary.total} 条；高置信度 {audit.evidence_summary.high} 条；"
            f"中置信度 {audit.evidence_summary.medium} 条；低置信度 {audit.evidence_summary.low} 条；"
            f"标明来源 {audit.evidence_summary.sourced} 条。"
        ),
        "",
        "### 证据台账",
        "",
        "| 编号 | 证据/事实 | 来源 | 置信度 | 说明 |",
        "|---:|---|---|---|---|",
    ]
    if audit.evidence_items:
        for idx, item in enumerate(audit.evidence_items[:25], 1):
            source = _compact(item.source or item.url or "未标明来源", limit=60)
            if item.url and item.url not in source:
                source = f"{source} ({_compact(item.url, limit=60)})"
            lines.append(
                f"| {idx} | {_md_cell(item.claim)} | {_md_cell(source)} | {item.confidence} | {_md_cell(item.reason)} |"
            )
    else:
        lines.append("| 1 | 未抽取到结构化证据 | 未标明来源 | low | 当前报告只能作为待核验线索。 |")

    lines.extend(["", "### 发现的问题"])
    if audit.issues:
        for issue in audit.issues:
            lines.append(f"- [{issue.severity}] {issue.category}: {issue.message}")
    else:
        lines.append("- 未发现需要阻断结论的质量问题。")

    lines.extend(["", "### 补充检索词"])
    if audit.supplement_queries:
        for query in audit.supplement_queries:
            lines.append(f"- {query}")
    else:
        lines.append("- 暂无。")

    lines.extend(["", "### 补充网页证据"])
    if audit.supplemental_documents:
        for idx, doc in enumerate(audit.supplemental_documents[:10], 1):
            title = doc.get("title") or "-"
            source = doc.get("source") or "-"
            url = doc.get("url") or "-"
            excerpt = str(doc.get("content") or "").replace("\n", " ")[:180]
            lines.append(f"{idx}. {title} | {source} | {url} | confidence=medium")
            if excerpt:
                lines.append(f"   - 摘要：{excerpt}")
    else:
        lines.append("- 未抓取到补充网页证据；请检查网络、代理或手动提供公告、财报、研报材料。")

    if audit.warnings:
        lines.extend(["", "### 检索警告"])
        for item in audit.warnings:
            lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def _target_name(topic: str, report_data: dict[str, Any] | None) -> str:
    companies = (report_data or {}).get("companies") or []
    if companies:
        company = companies[0].get("company") or {}
        if company.get("name"):
            return str(company["name"])
    target = (report_data or {}).get("target_company") or {}
    if isinstance(target, dict) and target.get("name"):
        return str(target["name"])
    return topic.strip()


def _tool_research_evidence_docs(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    tool_research = report_data.get("tool_research") or {}
    docs: list[dict[str, Any]] = []
    for item in tool_research.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        docs.append(
            {
                "title": item.get("title") or "LangGraph工具检索证据",
                "summary": item.get("content_excerpt") or "",
                "content": item.get("content_excerpt") or "",
                "source": item.get("source") or "langgraph_tool_agent",
                "source_type": item.get("source_type") or "tool_research",
                "url": item.get("url"),
                "path": item.get("path"),
            }
        )
    return docs


def _doc_to_dict(doc: SourceDocument) -> dict[str, Any]:
    data = doc.to_dict()
    data["content"] = str(data.get("content") or "")[:1200]
    return data


def _filter_supplemental_documents(docs: list[SourceDocument], *, limit: int) -> list[SourceDocument]:
    out: list[SourceDocument] = []
    seen: set[str] = set()
    for doc in docs:
        data = doc.to_dict()
        title = str(data.get("title") or "")
        url = str(data.get("url") or data.get("path") or "")
        source = str(data.get("source") or "")
        source_type = str(data.get("source_type") or "")
        content = str(data.get("content") or "")
        key = url or title
        if not key or key in seen:
            continue
        if _looks_like_search_landing_page(source=source, source_type=source_type, url=url, title=title):
            continue
        if len(content.strip()) < 80 and not url:
            continue
        seen.add(key)
        out.append(doc)
        if len(out) >= limit:
            break
    return out


def _looks_like_search_landing_page(*, source: str, source_type: str, url: str, title: str) -> bool:
    text = f"{source} {source_type} {url} {title}".lower()
    search_markers = [
        "/search",
        "search?",
        "search.php",
        "searchlist",
        "searchresult",
        "so.eastmoney.com",
        "search.10jqka.com.cn",
        "搜索结果",
        "站内搜索",
    ]
    return any(marker in text for marker in search_markers)


def _is_low_quality_signal_evidence(signal: dict[str, Any]) -> bool:
    event = signal.get("event") if isinstance(signal.get("event"), dict) else signal
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
    return event_type in event_needs_primary_source and any(token in text for token in weak_source_tokens)


def _dedup_issues(issues: list[ReportQualityIssue]) -> list[ReportQualityIssue]:
    seen: set[tuple[str, str]] = set()
    out: list[ReportQualityIssue] = []
    for issue in issues:
        key = (issue.category, issue.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def _dedup_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[tuple[str, str, str]] = set()
    out: list[EvidenceItem] = []
    for item in items:
        key = (item.claim, item.source, item.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _unique(items: list[str], *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = " ".join(str(item).split())
        if not clean or clean.lower() in seen:
            continue
        seen.add(clean.lower())
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def _compact(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _md_cell(value: Any) -> str:
    return str(value or "-").replace("|", "｜").replace("\n", " ")
