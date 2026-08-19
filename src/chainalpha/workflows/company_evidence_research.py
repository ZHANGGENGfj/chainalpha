"""Workflow that turns a research request into report-ready evidence.

The CLI and API both need this same orchestration: formulate an evidence
question, invoke the research agent, and normalise the returned evidence into
``SourceDocument`` objects.  Keeping it here prevents entry points from
becoming a second implementation of the research workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from chainalpha.agents.research import LangGraphResearchAgent
from chainalpha.config import Settings
from chainalpha.connectors.base import SourceDocument, build_source_document


@dataclass(frozen=True)
class CompanyEvidenceResearchRequest:
    settings: Settings
    question: str
    research_type: str
    target_name: str
    target_ticker: str
    search_keywords: list[str]
    crawl_keywords: list[str]
    media_crawler_root: Path
    enable_web_search: bool
    enable_targeted_crawl: bool
    top_k: int
    max_iterations: int
    skill_root: Path
    state_path: Path
    performance_path: Path
    evidence_memory_path: Path
    max_web_pages: int = 20
    preference_context: str = ""
    progress_callback: Callable[[dict[str, Any]], None] | None = None


@dataclass
class CompanyEvidenceResearchResult:
    tool_research: dict[str, Any]
    documents: list[SourceDocument]
    framework: str
    step_count: int
    task_tree: list[dict[str, Any]]
    coverage: dict[str, Any]
    warning: str = ""


def run_company_evidence_research(
    request: CompanyEvidenceResearchRequest,
) -> CompanyEvidenceResearchResult:
    """Run the LangGraph evidence loop and return builder-ready documents."""

    agent = LangGraphResearchAgent(
        request.settings,
        media_crawler_root=request.media_crawler_root,
        enable_local_search=True,
        enable_web_search=request.enable_web_search,
        enable_targeted_crawl=request.enable_targeted_crawl,
        top_k=request.top_k,
        max_iterations=request.max_iterations,
        max_web_pages=request.max_web_pages,
        skill_root=request.skill_root,
        state_path=request.state_path,
        performance_path=request.performance_path,
        evidence_memory_path=request.evidence_memory_path,
        progress_callback=request.progress_callback,
    )
    result = agent.run(
        build_evidence_question(request),
        target_name=request.target_name,
        target_ticker=request.target_ticker,
        research_type=request.research_type,
    )
    payload = result.to_dict()
    return CompanyEvidenceResearchResult(
        tool_research=payload,
        documents=tool_result_to_documents(payload),
        framework=result.framework,
        step_count=len(result.steps),
        task_tree=result.task_tree,
        coverage=result.coverage,
    )


def build_evidence_question(request: CompanyEvidenceResearchRequest) -> str:
    keywords = ", ".join(request.search_keywords[:12])
    crawl_keywords = ", ".join(request.crawl_keywords[:12])
    if request.research_type == "industry-chain":
        prompt = (
            f"Research question: {request.question}\n"
            "Type: industry-chain.\n"
            f"Search keywords: {keywords}\n"
            f"Crawl keywords: {crawl_keywords}\n"
            "Collect only evidence useful for an upstream-to-downstream industry-chain report: "
            "core materials/components, bottleneck links, capacity constraints, value transfer, "
            "representative companies, policy, price/supply-demand signals, risks, and falsification indicators. "
            "Prefer local MediaCrawler evidence first, then public finance web pages if local evidence is insufficient."
        )
    else:
        prompt = (
            f"Research question: {request.question}\n"
            "Type: company.\n"
            f"Target company: {request.target_name}\n"
            f"Ticker: {request.target_ticker or 'unknown'}\n"
            f"Search keywords: {keywords}\n"
            f"Crawl keywords: {crawl_keywords}\n"
            "Collect only evidence useful for a company research report: recent news, announcements, financial metrics, "
            "stock performance, fund flow, institutional holdings, catalysts, risks, and falsification indicators. "
            "Prefer local MediaCrawler evidence first, then public finance web pages if local evidence is insufficient."
        )
    if request.preference_context:
        prompt += (
            "\n\nUser report preferences (writing/retrieval priorities only; not factual evidence):\n"
            f"{request.preference_context}"
        )
    return prompt


def tool_result_to_documents(tool_result: dict[str, Any]) -> list[SourceDocument]:
    """Normalise agent evidence into the report builder's source contract."""

    documents: list[SourceDocument] = []
    seen: set[str] = set()
    for item in tool_result.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "Tool research evidence").strip()
        content = str(item.get("content_excerpt") or item.get("content") or "").strip()
        url = item.get("url")
        path = item.get("path")
        key = str(url or path or title)
        if not content or key in seen:
            continue
        seen.add(key)
        documents.append(
            build_source_document(
                source=str(item.get("source") or "langgraph_tool_agent"),
                source_type=str(item.get("source_type") or "tool_research"),
                title=title,
                content=content,
                url=str(url) if url else None,
                path=str(path) if path else None,
                metadata={
                    "platform": "langgraph_tool_agent",
                    "record": item,
                    "evidence_store": True,
                },
            )
        )
    return documents
