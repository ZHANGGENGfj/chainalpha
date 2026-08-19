"""Bounded, query-first public-information collection for report generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from chainalpha.config import Settings
from chainalpha.connectors.base import SourceDocument
from chainalpha.memory.evidence_memory import ResearchEvidenceMemory
from chainalpha.tools import ResearchToolRegistry, ResearchToolset
from chainalpha.tools.research_tools import dedup_source_documents


MAX_COLLECTION_QUERIES = 4
SEARCH_PAGES_PER_QUERY = 2
MAX_COLLECTION_SEARCHES = 10


@dataclass(frozen=True)
class BoundedQueryCollectionResult:
    queries: list[str]
    documents: list[SourceDocument]
    steps: list[dict[str, Any]]
    warnings: list[str]
    searches_used: int

    def to_dict(self) -> dict[str, Any]:
        evidence = [
            {
                "id": document.id,
                "title": document.title,
                "content_excerpt": document.content[:1200],
                "source": document.source,
                "source_type": document.source_type,
                "url": document.url,
                "published_at": document.published_at.isoformat() if document.published_at else None,
                "metadata": document.metadata,
            }
            for document in self.documents
        ]
        return {
            "framework": "bounded_query_collection.v1",
            "queries": self.queries,
            "searches_used": self.searches_used,
            "search_limit": MAX_COLLECTION_SEARCHES,
            "steps": self.steps,
            "evidence": evidence,
            "warnings": self.warnings,
        }


def collect_bounded_query_evidence(
    *,
    settings: Settings,
    media_crawler_root: Path,
    queries: list[str],
    evidence_memory_path: Path,
    top_k_per_query: int = 6,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> BoundedQueryCollectionResult:
    """Run four query expansions with two site-search pages each.

    The collector deliberately does not query local archives, replan based on
    coverage, or persist transient crawler results. Its output is only the
    bounded evidence prompt for the report-generation stage.
    """
    selected = list(dict.fromkeys(str(query).strip() for query in queries if str(query).strip()))[:MAX_COLLECTION_QUERIES]
    registry = ResearchToolRegistry(
        enable_local_search=False,
        enable_web_search=False,
        enable_targeted_crawl=True,
    )
    tools = ResearchToolset(
        media_crawler_root=media_crawler_root,
        evidence_memory=ResearchEvidenceMemory(evidence_memory_path),
        top_k=max(1, top_k_per_query),
        max_external_pages=SEARCH_PAGES_PER_QUERY,
        registry=registry,
    )
    profile = SimpleNamespace(media_crawler_platform="finance_news")
    documents: list[SourceDocument] = []
    warnings: list[str] = []
    steps: list[dict[str, Any]] = []
    searches_used = 0

    for index, query in enumerate(selected, start=1):
        remaining = MAX_COLLECTION_SEARCHES - searches_used
        if remaining <= 0:
            break
        page_budget = min(SEARCH_PAGES_PER_QUERY, remaining)
        stage_label, stage_goal = _describe_collection_stage(query, index)
        _emit(
            progress_callback,
            f"正在核查{stage_label}",
            f"{stage_goal}：{query}",
            {
                "index": index,
                "total": len(selected),
                "status": "started",
                "stage_label": stage_label,
                "stage_goal": stage_goal,
                "query": query,
                "page_budget": page_budget,
            },
        )
        docs, warning = tools.targeted_news_crawl(
            query,
            profile=profile,
            page_budget=page_budget,
            preserve_query=True,
            source_offset=(index - 1) * SEARCH_PAGES_PER_QUERY,
        )
        for document in docs:
            document.metadata = {
                **(document.metadata or {}),
                "collection_query": query,
                "collection_query_index": index,
            }
        used = min(page_budget, max(0, int(tools.last_external_pages_fetched or 0)))
        searches_used += used
        documents.extend(docs)
        if warning:
            warnings.append(f"{query}: {warning}")
        step = {
            "step": index,
            "action": "public_information_collection",
            "stage_label": stage_label,
            "stage_goal": stage_goal,
            "query": query,
            "search_pages": used,
            "documents_found": len(docs),
            "warning": warning,
            "phases": ["search", "extract"],
        }
        steps.append(step)
        _emit(
            progress_callback,
            f"{stage_label}核查完成",
            f"已完成{stage_goal}，获得 {len(docs)} 条候选信息",
            {**step, "status": "completed", "documents": [document.to_dict() for document in docs[:5]]},
        )

    return BoundedQueryCollectionResult(
        queries=selected,
        documents=dedup_source_documents(documents),
        steps=steps,
        warnings=warnings,
        searches_used=searches_used,
    )


def _emit(
    callback: Callable[[dict[str, Any]], None] | None,
    title: str,
    message: str,
    data: dict[str, Any],
) -> None:
    if callback:
        callback({"type": "stage", "title": title, "message": message, **data})


def _describe_collection_stage(query: str, index: int) -> tuple[str, str]:
    """Turn the four bounded queries into user-readable research actions."""
    normalized = str(query or "").lower()
    labels = (
        (("财报", "年报", "季报", "业绩", "营收", "毛利", "现金流", "应收", "存货"), "财务表现与经营质量", "核对业绩、利润率及现金流相关披露"),
        (("产品", "主营", "业务", "客户", "认证", "订单"), "主营业务、产品与客户", "核查产品进展、客户验证与订单线索"),
        (("风险", "竞争", "产业链", "供应链", "替代", "扩产", "产能"), "风险、竞争与产业链", "核查竞争格局、供需约束及风险事件"),
    )
    for terms, label, goal in labels:
        if any(term in normalized for term in terms):
            return label, goal
    fallback = (
        "近期动态与关键事件",
        "行业趋势与外部需求",
        "公司经营与业务进展",
        "风险、竞争与产业链",
    )
    label = fallback[min(max(index - 1, 0), len(fallback) - 1)]
    return label, "查找近期可验证的公开信息"
