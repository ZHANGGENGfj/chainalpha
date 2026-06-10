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

SOURCE_HINTS = ["http://", "https://", "来源", "公告", "财报", "研报", "证券报", "交易所", "巨潮", "东方财富", "同花顺"]


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

    @property
    def needs_supplement(self) -> bool:
        return bool(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "needs_supplement": self.needs_supplement,
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
    """Check a final report and append supplemental evidence when gaps are found.

    This is intentionally conservative: the agent does not rewrite conclusions
    or invent numbers. It only surfaces missing-evidence issues and appends
    public web/local search evidence that a human or later LLM pass can verify.
    """

    audit = ReportQualityAudit(topic=topic)
    audit.issues = _detect_quality_issues(markdown)
    audit.supplement_queries = _build_supplement_queries(topic, audit.issues, report_data)[:max_queries]

    if enable_web_search and audit.supplement_queries:
        docs, warnings = _fetch_supplemental_documents(audit.supplement_queries, limit=max_docs)
        audit.warnings.extend(warnings)
        audit.supplemental_documents = [_doc_to_dict(doc) for doc in docs[:max_docs]]

    if not audit.issues:
        return markdown, audit
    return _append_quality_agent_addendum(markdown, audit), audit


def _detect_quality_issues(markdown: str) -> list[ReportQualityIssue]:
    issues: list[ReportQualityIssue] = []
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    missing_lines = [line for line in lines if any(hint in line for hint in MISSING_HINTS)]
    source_count = sum(1 for line in lines if any(hint in line for hint in SOURCE_HINTS))

    if missing_lines:
        issues.append(
            ReportQualityIssue(
                category="data_gap",
                severity="high",
                message=f"报告中出现 {len(missing_lines)} 处数据缺口/待核验表述。",
                query_hint="财务数据、客户路径、产能约束、研报观点、公告来源",
            )
        )
    if source_count < 5:
        issues.append(
            ReportQualityIssue(
                category="source_gap",
                severity="high",
                message=f"报告中的来源线索偏少，仅识别到 {source_count} 处来源/链接/公告/研报提示。",
                query_hint="原始公告、公司年报、券商研报、财经媒体报道",
            )
        )
    if "瓶颈" in markdown and not any(term in markdown for term in ["客户认证", "前五大客户", "供应商", "产能", "良率"]):
        issues.append(
            ReportQualityIssue(
                category="bottleneck_evidence_gap",
                severity="medium",
                message="报告讨论瓶颈权力，但客户路径、供应链、产能或良率证据不足。",
                query_hint="客户认证、前五大客户、供应商、产能、良率",
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
        return docs, []
    except Exception as exc:  # noqa: BLE001
        return [], [f"supplemental web search failed: {type(exc).__name__}: {exc}"]


def _append_quality_agent_addendum(markdown: str, audit: ReportQualityAudit) -> str:
    lines = [markdown.rstrip(), "", "---", "", "## 报告检查 Agent：补充证据与缺口提示", ""]
    lines.append("### 发现的问题")
    for issue in audit.issues:
        lines.append(f"- [{issue.severity}] {issue.category}: {issue.message}")

    lines.extend(["", "### 补充检索词"])
    for query in audit.supplement_queries:
        lines.append(f"- {query}")

    lines.extend(["", "### 补充网页证据"])
    if audit.supplemental_documents:
        for idx, doc in enumerate(audit.supplemental_documents[:10], 1):
            title = doc.get("title") or "-"
            source = doc.get("source") or "-"
            url = doc.get("url") or "-"
            excerpt = str(doc.get("content") or "").replace("\n", " ")[:180]
            lines.append(f"{idx}. {title} | {source} | {url}")
            if excerpt:
                lines.append(f"   - 摘要：{excerpt}")
    else:
        lines.append("- 未抓取到补充网页证据；请检查网络/代理或手动提供公告、财报、研报材料。")

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
    return topic.strip()


def _doc_to_dict(doc: SourceDocument) -> dict[str, Any]:
    data = doc.to_dict()
    data["content"] = str(data.get("content") or "")[:1200]
    return data


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
