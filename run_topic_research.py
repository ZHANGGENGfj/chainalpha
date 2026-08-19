from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from chainalpha.investment.daily_market_scan_enhanced import (
    build_daily_market_scan,
    render_daily_market_scan_markdown,
)
from chainalpha.investment.chain_bottleneck import (
    build_chain_bottleneck_report,
    generate_llm_chain_bottleneck_report,
    render_chain_bottleneck_markdown,
)
from chainalpha.investment.chain_explainer import (
    generate_llm_chain_explainer_report,
    is_definition_question,
    render_chain_explainer_markdown,
)
from chainalpha.investment.llm_reply import (
    generate_event_signal_markdown,
    generate_chunked_professional_topic_report,
    generate_topic_reply,
    render_topic_reply,
)
from chainalpha.reports.evidence_pack import build_professional_evidence_pack
from chainalpha.workflows.five_agent_research import (
    build_five_agent_research_report,
    render_five_agent_markdown,
)
from chainalpha.investment.chain_visual import (
    build_chain_visual_data,
    render_chain_visual_markdown,
    save_chain_visual_outputs,
)
from chainalpha.investment.query_expansion import QueryExpansion, expand_user_query
from chainalpha.quality.report_quality_agent import run_report_quality_agent
from chainalpha.investment.report_extractors import load_local_report_summaries
from chainalpha.reports.company_report import (
    build_topic_research_report,
    render_professional_topic_report,
    render_topic_research_markdown,
)
from chainalpha.workflows.company_evidence_research import (
    CompanyEvidenceResearchRequest,
    run_company_evidence_research,
)
from chainalpha.workflows.bounded_query_collection import collect_bounded_query_evidence
from chainalpha.config import load_settings
from chainalpha.connectors.base import SourceDocument
from chainalpha.memory.research_memory import ResearchMemoryContext, ResearchMemoryService
from chainalpha.nlp.llm_client import llm_stream_events
from chainalpha.reports.html_renderer import render_markdown_html


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ChainAlpha topic research or daily market scan.")
    parser.add_argument("--topic", default="A股市场", help="Company, sector, or daily-scan query.")
    parser.add_argument("--daily-scan", action="store_true", help="Run daily market information collection and first-pass screening.")
    parser.add_argument("--scan-symbols", default="", help="Comma-separated symbols for technical screening, e.g. 300308,300502.")
    parser.add_argument("--scan-top-k", type=int, default=30, help="Maximum technical/sector candidates in daily scan.")
    parser.add_argument("--daily-store-dir", type=Path, default=Path("./data/daily_market"), help="Date-partitioned storage directory for daily scan artifacts.")
    parser.add_argument("--config", type=Path, default=Path("./configs/sources_mediacrawler.yaml"), help="Data-source connector YAML.")
    parser.add_argument(
        "--media-crawler-root",
        type=Path,
        default=None,
        help="Override MediaCrawler data directory, e.g. D:/lh/gupiao_agents/MediaCrawler-main/data.",
    )
    parser.add_argument(
        "--media-crawler-platforms",
        default="finance_news,claim_tasks,cls,xq",
        help="Comma-separated MediaCrawler data folders to read.",
    )
    parser.add_argument(
        "--skip-refresh-news",
        action="store_true",
        help="Disable Runtime Skill on-demand MediaCrawler collection for this run.",
    )
    parser.add_argument("--company-map", type=Path, default=Path("./configs/company_map.yaml"), help="Company mapping YAML.")
    parser.add_argument(
        "--company-profile-source",
        choices=["llm", "local", "hybrid", "resolver"],
        default="llm",
        help="Company profile source. llm avoids local company-map matching; local keeps the old company_map flow.",
    )
    parser.add_argument("--financials", type=Path, default=None, help="Optional financial snapshot CSV. Leave empty to avoid sample data.")
    parser.add_argument("--prices", type=Path, default=None, help="Optional price snapshot CSV. Leave empty to avoid sample data.")
    parser.add_argument("--financial-report-dir", type=Path, default=None, help="Optional local annual/quarterly report PDF/text directory or file.")
    parser.add_argument("--broker-report-dir", type=Path, default=None, help="Optional local broker/institution research report PDF/text directory or file.")
    parser.add_argument("--skip-financial-web", action="store_true", help="Disable web financial metric pages such as Eastmoney/10jqka/Sina F10.")
    parser.add_argument("--run-dir", type=Path, default=Path("./data/topic_runs"), help="Output directory.")
    parser.add_argument("--run-id", default="", help="Optional fixed run folder name under --run-dir.")
    parser.add_argument("--limit", type=int, default=80, help="Maximum source documents to fetch.")
    parser.add_argument("--top-k-chunks", type=int, default=60, help="Top chunks to analyze.")
    parser.add_argument("--top-k-companies", type=int, default=1, help="Maximum related companies to analyze. Defaults to target-company focus.")
    parser.add_argument("--top-k-candidates", type=int, default=8, help="Maximum candidates to output.")
    parser.add_argument(
        "--retrieval-backend",
        choices=["auto", "hybrid", "llamaindex"],
        default="auto",
        help="Document retrieval backend. auto tries LlamaIndex first, then falls back to the built-in HybridIndex.",
    )
    parser.add_argument("--include-a-share-reports", action="store_true", help="Fetch and parse A-share filings/PDF reports. Disabled by default.")
    parser.add_argument(
        "--research-type",
        choices=["auto", "company", "industry-chain"],
        default="company",
        help="Report workflow type. Use industry-chain for materials/components/themes such as 电子布; use company for single-company reports.",
    )
    parser.add_argument("--chain-bottleneck-report", action="store_true", help="Generate industry-chain bottleneck power report.")
    parser.add_argument("--chain-visual", action="store_true", help="Generate an upstream-to-downstream SVG chain visual for industry-chain reports.")
    parser.add_argument("--no-query-expansion", action="store_true", help="Disable LLM/rule query correction and expansion.")
    parser.add_argument("--llm-summary", action="store_true", help="Generate final LLM summary.")
    parser.add_argument(
        "--output-mode",
        choices=["final", "full"],
        default="final",
        help="final writes only final reports and essential JSON; full also writes intermediate Markdown/HTML artifacts.",
    )
    parser.add_argument("--skip-report-check", action="store_true", help="Disable final report quality/check-and-supplement agent.")
    parser.add_argument("--skip-report-check-web", action="store_true", help="Run report quality checks without supplemental web search.")
    parser.add_argument("--report-check-web", dest="skip_report_check_web", action="store_false", help="Allow report quality checks to run supplemental web search.")
    parser.set_defaults(skip_report_check_web=True)
    parser.add_argument("--skip-tool-agent", action="store_true", help="Disable LangGraph tool-use evidence search inside the normal report flow.")
    parser.add_argument("--skip-tool-agent-web", action="store_true", help="Disable public web search inside the LangGraph tool-use evidence agent.")
    parser.add_argument(
        "--tool-agent-iterations",
        type=int,
        default=0,
        help="Safety cap for Skill/tool iterations; 0 uses 10. Search still stops early when evidence is covered or tools are exhausted.",
    )
    parser.add_argument("--tool-agent-top-k", type=int, default=6, help="Maximum documents per LangGraph tool call.")
    parser.add_argument("--tool-agent-max-pages", type=int, default=20, help="Maximum public webpages collected by the LangGraph tool agent per run.")
    parser.add_argument("--runtime-skill-root", type=Path, default=Path(os.getenv("CHAINALPHA_SKILL_ROOT", "./skills")), help="Runtime financial Skill directory.")
    parser.add_argument("--agent-state-dir", type=Path, default=Path(os.getenv("CHAINALPHA_AGENT_STATE_DIR", "./data/agent_states")), help="Persistent Agent state snapshots.")
    parser.add_argument("--tool-performance-path", type=Path, default=Path(os.getenv("CHAINALPHA_TOOL_PERFORMANCE_PATH", "./data/agent/tool_performance.json")), help="Learned tool outcome statistics.")
    parser.add_argument("--research-evidence-memory-path", type=Path, default=Path(os.getenv("CHAINALPHA_EVIDENCE_MEMORY_PATH", "./data/research_memory/evidence.sqlite3")), help="Point-in-time research evidence memory.")
    parser.add_argument("--skip-preference-memory", action="store_true", help="Disable Mem0 user-preference recall and writing for this run.")
    parser.add_argument("--memory-user-id", default="local_user", help="Stable user scope for preference memory. Use an authenticated user ID in production.")
    parser.add_argument("--memory-session-id", default="", help="Optional chat session ID stored only as preference-memory metadata.")
    parser.add_argument("--broker-name", default="", help="Optional broker/institution name shown at the top of generated HTML reports.")
    parser.add_argument("--broker-logo", default="", help="Optional broker/institution logo path or URL shown at the top of generated HTML reports.")
    return parser.parse_args()


def _render_html(args: argparse.Namespace, markdown: str, *, title: str) -> str:
    return render_markdown_html(
        markdown,
        title=title,
        brand_name=(args.broker_name or None),
        brand_logo=_normalize_logo_src(args.broker_logo),
    )


def _normalize_logo_src(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw):
        return raw
    path = Path(raw)
    if path.exists():
        try:
            return path.resolve().as_uri()
        except ValueError:
            return str(path)
    return raw.replace("\\", "/")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    out_name = _safe_run_id(args.run_id) if args.run_id else datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.run_dir / out_name
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.daily_scan:
        _run_daily_scan(args, out_dir)
        return

    _run_topic_research(args, out_dir)


def _safe_run_id(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", value.strip())
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("--run-id must contain at least one safe path character.")
    return cleaned[:80]


def _run_daily_scan(args: argparse.Namespace, out_dir: Path) -> None:
    symbols = [item.strip() for item in args.scan_symbols.split(",") if item.strip()]
    print(f"Starting daily market scan: topic={args.topic}, limit={args.limit}, top_k={args.scan_top_k}", flush=True)
    if symbols:
        print(f"Scan symbols: {', '.join(symbols)}", flush=True)
    report = build_daily_market_scan(
        query=args.topic,
        symbols=symbols,
        limit=args.limit,
        top_k=args.scan_top_k,
        persist_dir=args.daily_store_dir,
        progress=lambda message: print(f"[daily-scan] {message}", flush=True),
    )
    report_json = out_dir / "daily_market_scan.json"
    report_md = out_dir / "daily_market_scan.md"
    report_html = out_dir / "daily_market_scan.html"
    markdown = render_daily_market_scan_markdown(report)
    report_json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(markdown, encoding="utf-8")
    report_html.write_text(_render_html(args, markdown, title=f"{args.topic} Daily Market Scan"), encoding="utf-8")

    print(f"Daily scan query: {args.topic}")
    print(f"Raw documents: {len(report.raw_documents)}")
    print(f"Technical candidates: {len(report.technical_candidates)}")
    print(f"Sector candidates: {len(report.sector_candidates)}")
    if report.storage:
        print(f"Stored daily folder: {report.storage.get('day_dir')}")
        print(f"Deduped documents saved: {report.storage.get('deduped_documents')}")
        print(f"Classified events: {report.storage.get('classified_events_jsonl')}")
        print(f"Technical candidates store: {report.storage.get('technical_candidates_jsonl')}")
    if report.warnings:
        print("Warnings:")
        for item in report.warnings[:20]:
            print(f"- {item}")
    print(f"Daily Scan JSON: {report_json}")
    print(f"Daily Scan Markdown: {report_md}")
    print(f"Daily Scan HTML: {report_html}")
    if report.technical_candidates:
        print("Top technical candidates:")
        for idx, item in enumerate(report.technical_candidates[:10], 1):
            print(f"{idx}. {item.symbol} {item.name or ''} score={item.score:.1f} signals={'; '.join(item.signals)}")


def _run_topic_research(args: argparse.Namespace, out_dir: Path) -> None:
    media_data_root = resolve_media_crawler_data_root(args.media_crawler_root)
    if args.llm_summary:
        settings = load_settings()
        """
        print(
            f"LLM provider: {settings.llm_provider} | "
            f"model={settings.llm_model} | base_url={settings.llm_base_url} | "
            f"api_key={'set' if settings.llm_api_key else 'missing'}",
            flush=True,
        )
        """
    query_expansion = expand_user_query(
        args.topic,
        company_map=str(args.company_map),
        enable_llm=not args.no_query_expansion,
        use_local_company_map=args.company_profile_source != "llm",
    )
    _apply_research_type_override(args, query_expansion)
    query_expansion_path = out_dir / "query_expansion.json"
    query_expansion_path.write_text(json.dumps(query_expansion.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    _emit_run_event(
        out_dir,
        "stage",
        "查询理解完成",
        f"{query_expansion.corrected_query} | {', '.join(query_expansion.search_keywords[:8])}",
        {
            "research_type": _effective_research_type(args, query_expansion),
            "topic_class": query_expansion.topic_class,
            "keywords": query_expansion.search_keywords[:16],
        },
    )
    print(f"Query expansion: {query_expansion.corrected_query} | {', '.join(query_expansion.search_keywords[:12])}", flush=True)
    print(f"Research type: {_effective_research_type(args, query_expansion)} | topic_class={query_expansion.topic_class}", flush=True)
    print(f"Query expansion JSON: {query_expansion_path}", flush=True)

    preference_context = ResearchMemoryContext(False, [], "preference memory disabled")
    preference_write: dict[str, object] = {"ok": False, "skipped": True}
    if not args.skip_preference_memory:
        preference_memory = ResearchMemoryService(load_settings(), user_id=args.memory_user_id)
        preference_write = preference_memory.remember_from_query(args.topic, session_id=args.memory_session_id)
        preference_context = preference_memory.recall(
            f"用户报告偏好与写作要求：{query_expansion.corrected_query or args.topic}"
        )
        if preference_context.available and preference_context.items:
            print(f"Preference memory: recalled {len(preference_context.items)} item(s)", flush=True)
        elif preference_context.warning:
            print(f"Preference memory: {preference_context.warning}", flush=True)

    # On-demand collection belongs to the research loop. The agent checks the
    # local library and evidence memory first, then invokes MediaCrawler only
    # for an uncovered, time-sensitive Skill capability. The independent
    # background market collector can continue writing to the same data store.
    refresh_result = {
        "mode": "skill_delegated" if not args.skip_refresh_news else "disabled",
        "targeted_crawl_enabled": not args.skip_refresh_news,
        "message": (
            "Runtime Skill will invoke MediaCrawler only when local evidence is insufficient."
            if not args.skip_refresh_news
            else "On-demand MediaCrawler collection is disabled for this run."
        ),
    }
    _emit_run_event(
        out_dir,
        "stage",
        "按需信息收集",
        refresh_result["message"],
        {"collection": refresh_result},
    )

    evidence_context = _build_evidence_context(
        args,
        out_dir,
        media_data_root,
        query_expansion,
        refresh_result,
        preference_context=preference_context,
    )

    _emit_run_event(out_dir, "stage", "研究底稿构建", "正在基于统一证据层生成 topic_research.json")
    report = build_topic_research_report(
        topic=args.topic,
        config=args.config,
        company_map=args.company_map,
        financials=args.financials,
        prices=args.prices,
        limit=args.limit,
        top_k_chunks=args.top_k_chunks,
        top_k_companies=args.top_k_companies,
        top_k_candidates=args.top_k_candidates,
        include_a_share_reports=args.include_a_share_reports,
        focus_target_company=True,
        media_crawler_root=media_data_root,
        media_crawler_platforms=[item.strip() for item in args.media_crawler_platforms.split(",") if item.strip()],
        search_keywords=query_expansion.search_keywords,
        query_expansion=query_expansion.to_dict(),
        extra_documents=evidence_context["extra_documents"],
        enable_financial_web=not args.skip_financial_web,
        company_profile_source=args.company_profile_source,
        retrieval_backend=args.retrieval_backend,
    )

    report_data = _attach_evidence_store(report.to_dict(), evidence_context["store"])
    report_data["user_preference_memory"] = {
        **preference_context.to_dict(),
        "write": preference_write,
        "policy": "User preferences are writing instructions, not investment evidence.",
    }
    report_json = out_dir / "topic_research.json"
    report_json.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit_run_event(
        out_dir,
        "artifact",
        "研究底稿完成",
        f"documents={len(report_data.get('selected_documents') or [])}, signals={len(report_data.get('signals') or [])}",
        {"artifact": "topic_research.json"},
    )
    if args.output_mode == "full":
        report_md = out_dir / "topic_research.md"
        report_html = out_dir / "topic_research.html"
        professional_md = out_dir / "topic_professional_report.md"
        professional_html = out_dir / "topic_professional_report.html"
        report_markdown = render_topic_research_markdown(report)
        professional_markdown = render_professional_topic_report(report)
        report_md.write_text(report_markdown, encoding="utf-8")
        professional_md.write_text(professional_markdown, encoding="utf-8")
        report_html.write_text(_render_html(args, report_markdown, title=f"{args.topic} Topic Research"), encoding="utf-8")
        professional_html.write_text(
            _render_html(args, professional_markdown, title=f"{args.topic} Professional Report"),
            encoding="utf-8",
        )

    print(f"Topic: {args.topic}")
    print(f"Intent: {report.intent.input_type} -> {report.intent.target}")
    print(f"Report JSON: {report_json}")
    if args.output_mode == "full":
        print(f"Report Markdown: {report_md}")
        print(f"Report HTML: {report_html}")
        print(f"Professional Markdown: {professional_md}")
        print(f"Professional HTML: {professional_html}")

    chain_report_data: dict | None = None
    if args.chain_bottleneck_report:
        chain_report = build_chain_bottleneck_report(report_data)
        chain_report_data = chain_report.to_dict()
        if args.output_mode == "full":
            chain_json = out_dir / "chain_bottleneck_report.json"
            chain_md = out_dir / "chain_bottleneck_report.md"
            chain_html = out_dir / "chain_bottleneck_report.html"
            chain_markdown = render_chain_bottleneck_markdown(chain_report)
            chain_json.write_text(json.dumps(chain_report_data, ensure_ascii=False, indent=2), encoding="utf-8")
            chain_md.write_text(chain_markdown, encoding="utf-8")
            chain_html.write_text(_render_html(args, chain_markdown, title=f"{args.topic} Chain Bottleneck Report"), encoding="utf-8")
            print(f"Chain Bottleneck JSON: {chain_json}")
            print(f"Chain Bottleneck Markdown: {chain_md}")
            print(f"Chain Bottleneck HTML: {chain_html}")

    _emit_run_event(out_dir, "stage", "研究框架整理", "正在汇总产业链、财务与风险证据，形成报告框架")
    agent_report = build_five_agent_research_report(report_data, chain_report_data)
    _emit_run_event(out_dir, "artifact", "研究框架完成", "产业链、财务和风险证据已完成结构化整理")
    chain_visual_markdown = ""
    if args.chain_visual and _effective_research_type(args, query_expansion) == "industry-chain":
        visual_data = build_chain_visual_data(report_data, agent_report.to_dict())
        visual_paths = save_chain_visual_outputs(visual_data, out_dir)
        chain_visual_markdown = render_chain_visual_markdown(visual_paths["svg"]) + "\n"
        print(f"Chain Visual JSON: {visual_paths['json']}")
        print(f"Chain Visual SVG: {visual_paths['svg']}")
    if args.output_mode == "full":
        agent_report_json = out_dir / "five_agent_report.json"
        agent_report_md = out_dir / "five_agent_report.md"
        agent_report_html = out_dir / "five_agent_report.html"
        agent_markdown = render_five_agent_markdown(agent_report)
        agent_report_json.write_text(json.dumps(agent_report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        agent_report_md.write_text(agent_markdown, encoding="utf-8")
        agent_report_html.write_text(
            _render_html(args, agent_markdown, title=f"{args.topic} Five Agent Research Report"),
            encoding="utf-8",
        )
        print(f"Five-Agent JSON: {agent_report_json}")
        print(f"Five-Agent Markdown: {agent_report_md}")
        print(f"Five-Agent HTML: {agent_report_html}")

    if args.llm_summary:
        _emit_run_event(out_dir, "stage", "LLM 报告生成", "正在把证据包交给大模型生成最终报告")
        if args.chain_bottleneck_report and chain_report_data:
            if is_definition_question(args.topic):
                _write_chain_explainer_llm_outputs(
                    args,
                    out_dir,
                    report_data,
                    agent_report.to_dict(),
                    lead_markdown=chain_visual_markdown,
                )
            else:
                _write_chain_bottleneck_llm_outputs(
                    args,
                    out_dir,
                    chain_report_data,
                    agent_report.to_dict(),
                    source_report_data=report_data,
                    lead_markdown=chain_visual_markdown,
                )
            print(
                "Skipped generic LLM professional report because --chain-bottleneck-report is enabled. "
                "Use llm_chain_bottleneck_report.md/html as the primary LLM output.",
                flush=True,
            )
        else:
            _write_llm_outputs(args, out_dir, report_data, lead_markdown=chain_visual_markdown)
    elif args.output_mode == "final":
        final_md = out_dir / "final_report.md"
        final_html = out_dir / "final_report.html"
        _emit_run_event(out_dir, "stage", "确定性报告生成", "正在生成 final_report.md/html")
        if args.chain_bottleneck_report and chain_report_data and is_definition_question(args.topic):
            final_markdown = render_chain_explainer_markdown(report_data)
            quality_report_data = {**report_data, "_report_kind": "chain_explainer"}
        elif args.chain_bottleneck_report and chain_report_data:
            final_markdown = render_chain_bottleneck_markdown(chain_report)
            quality_report_data = report_data
        elif _effective_research_type(args, query_expansion) == "industry-chain":
            final_markdown = render_five_agent_markdown(agent_report)
            quality_report_data = report_data
        else:
            final_markdown = render_professional_topic_report(report)
            quality_report_data = report_data
        final_markdown = _prepend_lead_markdown(chain_visual_markdown, final_markdown)
        final_markdown = _apply_report_quality_agent(args, out_dir, final_markdown, quality_report_data)
        final_md.write_text(final_markdown, encoding="utf-8")
        final_html.write_text(_render_html(args, final_markdown, title=f"{args.topic} Final Report"), encoding="utf-8")
        _emit_run_event(out_dir, "model_output", "最终报告已生成", final_markdown[:900], {"artifact": "final_report.md"})
        print(f"Final Markdown: {final_md}")
        print(f"Final HTML: {final_html}")


def resolve_media_crawler_data_root(path: Path | None) -> Path:
    if path is None:
        return Path("./third_party/MediaCrawler-main/data")
    p = path
    if p.name == "data":
        return p
    return p / "data"


def _build_evidence_context(
    args: argparse.Namespace,
    out_dir: Path,
    media_data_root: Path,
    query_expansion: QueryExpansion,
    refresh_result: dict | None,
    *,
    preference_context: ResearchMemoryContext | None = None,
) -> dict:
    """Build the evidence layer before deterministic research construction."""

    tool_result = None
    tool_warning = ""
    extra_documents: list[SourceDocument] = []
    # Kept temporarily for CLI compatibility, but the web/report default now
    # uses the bounded four-query collector below instead of a re-planning loop.
    if False and not args.skip_tool_agent:
        try:
            _emit_run_event(out_dir, "stage", "工具检索 Agent", "正在补充本地与财经资讯证据")
            preference_text = preference_context.prompt_context() if preference_context else ""
            workflow_result = run_company_evidence_research(
                CompanyEvidenceResearchRequest(
                    settings=load_settings(),
                    question=query_expansion.corrected_query or args.topic,
                    research_type=_effective_research_type(args, query_expansion),
                    target_name=(query_expansion.company_names or [query_expansion.corrected_query or args.topic])[0],
                    target_ticker=(query_expansion.tickers or [""])[0],
                    search_keywords=list(query_expansion.search_keywords),
                    crawl_keywords=list(query_expansion.crawl_keywords),
                    media_crawler_root=media_data_root,
                    # The interactive product uses MediaCrawler's configured
                    # finance sources only. Generic page scraping is too slow
                    # and unreliable for this path.
                    enable_web_search=False,
                    enable_targeted_crawl=not args.skip_refresh_news,
                    top_k=args.tool_agent_top_k,
                    max_iterations=args.tool_agent_iterations,
                    max_web_pages=args.tool_agent_max_pages,
                    skill_root=args.runtime_skill_root,
                    state_path=args.agent_state_dir / f"{out_dir.name}.json",
                    performance_path=args.tool_performance_path,
                    evidence_memory_path=args.research_evidence_memory_path,
                    preference_context=preference_text,
                    progress_callback=lambda event: _emit_run_event(
                        out_dir,
                        str(event.get("type") or "tool"),
                        str(event.get("title") or "tool research"),
                        str(event.get("message") or ""),
                        {key: value for key, value in event.items() if key not in {"type", "title", "message"}},
                    ),
                )
            )
            tool_result = workflow_result.tool_research
            extra_documents = workflow_result.documents
            tool_path = out_dir / "tool_research_agent.json"
            tool_path.write_text(json.dumps(tool_result, ensure_ascii=False, indent=2), encoding="utf-8")
            task_tree_path = out_dir / "research_task_tree.json"
            task_tree_path.write_text(
                json.dumps(
                    {
                        "question": query_expansion.corrected_query or args.topic,
                        "research_type": _effective_research_type(args, query_expansion),
                        "tasks": workflow_result.task_tree,
                        "coverage": workflow_result.coverage,
                        "tool_trace": tool_result.get("steps") or [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(
                f"LangGraph Tool Agent: framework={workflow_result.framework}, "
                f"steps={workflow_result.step_count}, evidence={len(tool_result.get('evidence') or [])}, "
                f"injected_docs={len(extra_documents)}",
                flush=True,
            )
            print(f"Public collection JSON: {tool_path}", flush=True)
            print(f"Research Task Tree JSON: {task_tree_path}", flush=True)
        except Exception as exc:  # noqa: BLE001
            tool_warning = f"LangGraph tool research failed before research build: {type(exc).__name__}: {exc}"
            print(tool_warning, flush=True)

    if not args.skip_tool_agent and not args.skip_refresh_news:
        try:
            collection = collect_bounded_query_evidence(
                settings=load_settings(),
                media_crawler_root=media_data_root,
                queries=query_expansion.collection_queries,
                evidence_memory_path=args.research_evidence_memory_path,
                top_k_per_query=args.tool_agent_top_k,
                progress_callback=lambda event: _emit_run_event(
                    out_dir,
                    str(event.get("type") or "stage"),
                    str(event.get("title") or "public information collection"),
                    str(event.get("message") or ""),
                    {key: value for key, value in event.items() if key not in {"type", "title", "message"}},
                ),
            )
            tool_result = collection.to_dict()
            extra_documents = collection.documents
            tool_warning = "; ".join(collection.warnings)
            tool_path = out_dir / "tool_research_agent.json"
            tool_path.write_text(json.dumps(tool_result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                f"Bounded query collection: queries={len(collection.queries)}, "
                f"searches={collection.searches_used}, evidence={len(collection.documents)}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            tool_warning = f"Bounded public-information collection failed: {type(exc).__name__}: {exc}"
            print(tool_warning, flush=True)
    elif args.skip_refresh_news:
        tool_warning = "On-demand public-information collection is disabled for this run."

    evidence_store = {
        "schema_version": "evidence_store.v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "topic": args.topic,
        "research_type": _effective_research_type(args, query_expansion),
        "query_expansion": query_expansion.to_dict(),
        "media_crawler": {
            "data_root": str(media_data_root),
            "refresh": refresh_result,
        },
        "tool_research": tool_result or {},
        "tool_research_warning": tool_warning,
        "builder_inputs": {
            "extra_document_count": len(extra_documents),
            "extra_document_ids": [doc.id for doc in extra_documents],
        },
        "policy": {
            "bounded_query_collection_runs_before_research_build": True,
            "collection_query_count": 4,
            "collection_search_limit": 10,
            "report_quality_agent_default_web_search": False,
            "single_source_for_llm_and_quality": True,
        },
    }
    evidence_path = out_dir / "evidence_store.json"
    evidence_path.write_text(json.dumps(evidence_store, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit_run_event(
        out_dir,
        "artifact",
        "统一证据层完成",
        f"extra_documents={len(extra_documents)}, tool_evidence={len((tool_result or {}).get('evidence') or [])}",
        {"artifact": "evidence_store.json"},
    )
    print(f"Evidence Store JSON: {evidence_path}", flush=True)
    return {"store": evidence_store, "extra_documents": extra_documents}


def _attach_evidence_store(report_data: dict, evidence_store: dict) -> dict:
    tool_research = evidence_store.get("tool_research") or {}
    attached = {
        **report_data,
        "research_type": evidence_store.get("research_type") or "company",
        "evidence_store": evidence_store,
        "tool_research": tool_research,
    }
    warning = evidence_store.get("tool_research_warning")
    if warning:
        attached["tool_research_warning"] = warning
    return attached


def _emit_run_event(
    out_dir: Path,
    event_type: str,
    title: str,
    message: str = "",
    data: dict | None = None,
) -> None:
    event = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": event_type,
        "title": title,
        "message": str(message or "")[:2000],
        "data": _trim_event_data(data or {}),
    }
    try:
        with (out_dir / "run_events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _llm_stream_event_writer(out_dir: Path, *, phase: str):
    def write(event: dict) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "model_delta":
            _emit_run_event(
                out_dir,
                "model_delta",
                "模型正在输出",
                str(event.get("message") or ""),
                {"phase": phase},
            )
            return
        if event_type == "model_stream_start":
            _emit_run_event(out_dir, "model_stream", "模型开始输出", str(event.get("message") or ""), {"phase": phase})
            return
        if event_type == "model_stream_end":
            _emit_run_event(out_dir, "model_stream", "模型输出完成", str(event.get("message") or ""), {"phase": phase})
            return
        if event_type == "model_stream_warning":
            _emit_run_event(out_dir, "model_stream_warning", "模型流式输出降级", str(event.get("message") or ""), {"phase": phase})

    return write


def _trim_event_data(value, *, max_text: int = 800):
    if isinstance(value, str):
        return value[:max_text]
    if isinstance(value, list):
        return [_trim_event_data(item, max_text=max_text) for item in value[:10]]
    if isinstance(value, dict):
        return {str(key): _trim_event_data(item, max_text=max_text) for key, item in list(value.items())[:20]}
    return value


def _apply_research_type_override(args: argparse.Namespace, query_expansion: QueryExpansion) -> None:
    if args.research_type == "auto":
        return
    if args.research_type == "industry-chain":
        if query_expansion.topic_class not in {"raw_material_chain", "component_chain", "industry_chain"}:
            query_expansion.topic_class = "industry_chain"
        if query_expansion.intent == "company":
            query_expansion.intent = "theme"
        query_expansion.warnings.append("research_type=industry-chain: final report uses industry-chain pipeline template.")
        return
    if args.research_type == "company":
        query_expansion.topic_class = "company"
        query_expansion.intent = "company"
        query_expansion.warnings.append("research_type=company: final report uses company equity-research template.")


def _effective_research_type(args: argparse.Namespace, query_expansion: QueryExpansion) -> str:
    if args.research_type != "auto":
        return args.research_type
    if query_expansion.topic_class in {"raw_material_chain", "component_chain", "industry_chain"}:
        return "industry-chain"
    return "company"


def _apply_report_quality_agent(
    args: argparse.Namespace,
    out_dir: Path,
    markdown: str,
    report_data: dict | None = None,
) -> str:
    if args.skip_report_check:
        return _strip_excluded_report_sections(markdown)
    checked_markdown, audit = run_report_quality_agent(
        topic=args.topic,
        markdown=markdown,
        report_data=report_data,
        enable_web_search=not args.skip_report_check_web,
    )
    audit_path = out_dir / "report_quality_audit.json"
    audit_path.write_text(json.dumps(audit.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report Quality Audit: {audit_path}")
    if audit.needs_supplement:
        print(f"Report Quality Agent: found {len(audit.issues)} issue(s), supplemental docs={len(audit.supplemental_documents)}")
    return _strip_excluded_report_sections(checked_markdown)


def _strip_excluded_report_sections(markdown: str) -> str:
    """Remove excluded sections and normalize public-report section numbering."""
    kept: list[str] = []
    skipping = False
    for line in (markdown or "").splitlines():
        normalized = line.strip()
        if re.match(r"^##\s*(?:\d+[.、]\s*)?(?:估值分析|盈利预测)\s*$", normalized):
            skipping = True
            continue
        if skipping and re.match(r"^##\s+", line):
            skipping = False
        if not skipping:
            kept.append(line)
    cleaned = _flatten_public_report_subheadings("\n".join(kept))
    cleaned = _remove_missing_data_content(cleaned)
    return _normalize_company_report_section_numbers(cleaned)


def _flatten_public_report_subheadings(markdown: str) -> str:
    """Use compact bold labels instead of level-three headings in final reports."""
    output: list[str] = []
    for line in (markdown or "").splitlines():
        match = re.match(r"^###\s+(.+?)\s*$", line)
        if match:
            output.append(f"**{match.group(1).strip()}**")
            continue
        output.append(line)
    return "\n".join(output)


def _normalize_company_report_section_numbers(markdown: str) -> str:
    """Keep the public company-report template contiguous after excluded sections."""
    section_numbers = {
        "核心结论": 1,
        "公司概况与商业模式": 2,
        "近期事件与市场表现": 3,
        "财务深度分析": 4,
        "行业与产业链分析": 5,
        "竞争格局": 6,
        "预期差与催化剂": 7,
        "风险与证伪条件": 8,
        "跟踪指标仪表盘": 9,
        "跟踪指标": 9,
        "附录": 10,
    }
    output: list[str] = []
    for line in (markdown or "").splitlines():
        match = re.match(r"^(##)\s*(?:\d+[.、]\s*)?(.+?)\s*$", line)
        if match:
            title = match.group(2).strip()
            number = section_numbers.get(title)
            if number is not None:
                output.append(f"## {number}. {title}")
                continue
        output.append(line)
    return "\n".join(output).strip() + "\n"


def _remove_missing_data_content(markdown: str) -> str:
    """Hide unsupported prose while retaining factual report rows and audit JSON."""
    markers = (
        "数据缺口",
        "待补数据",
        "待补证据",
        "暂无数据",
        "无直接证据",
        "暂未获得",
        "投资六维分析表",
        "六维跟踪指标表",
    )
    output: list[str] = []
    lines = (markdown or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("|") and index + 1 < len(lines) and "|" in lines[index + 1]:
            table_lines = [line]
            index += 1
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            output.extend(_remove_gap_columns_from_table(table_lines, markers))
            continue
        if not any(marker in line for marker in markers):
            output.append(line)
        index += 1
    return _remove_empty_markdown_sections(output)


def _remove_empty_markdown_sections(lines: list[str]) -> str:
    """Drop headings left empty after unsupported rows and prose are removed."""
    current = list(lines)
    for level in (3, 2):
        header = re.compile(rf"^#{{{level}}}\s+")
        next_header = re.compile(rf"^#{{1,{level}}}\s+")
        cleaned: list[str] = []
        index = 0
        while index < len(current):
            if not header.match(current[index]):
                cleaned.append(current[index])
                index += 1
                continue
            end = index + 1
            while end < len(current) and not next_header.match(current[end]):
                end += 1
            body = current[index + 1 : end]
            meaningful = any(
                item.strip()
                and item.strip() != "---"
                and not re.fullmatch(r"\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|?", item.strip())
                for item in body
            )
            if meaningful:
                cleaned.extend(current[index:end])
            index = end
        current = cleaned
    return "\n".join(current).strip() + "\n"


def _remove_gap_columns_from_table(table_lines: list[str], markers: tuple[str, ...]) -> list[str]:
    def cells(row: str) -> list[str]:
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    rows = [cells(row) for row in table_lines]
    if len(rows) < 3:
        return table_lines
    table_text = " ".join(" ".join(row) for row in rows)
    if all(token in table_text for token in ("技术", "资金", "基本", "产业", "政策")):
        return []
    retained = [idx for idx, header in enumerate(rows[0]) if not any(marker in header for marker in markers)]
    if not retained:
        return []
    rendered: list[str] = []
    for row_index, row in enumerate(rows):
        selected = [row[idx] if idx < len(row) else "" for idx in retained]
        if row_index >= 2 and any(any(marker in cell for marker in markers) for cell in selected):
            continue
        rendered.append("| " + " | ".join(selected) + " |")
    return rendered if len(rendered) >= 3 else []


def _prepend_lead_markdown(lead_markdown: str, markdown: str) -> str:
    lead = (lead_markdown or "").strip()
    body = (markdown or "").lstrip()
    if not lead:
        return markdown
    if lead in body[:500]:
        return markdown
    return f"{lead}\n\n{body}"


def _write_llm_outputs(
    args: argparse.Namespace,
    out_dir: Path,
    report_data: dict,
    *,
    lead_markdown: str = "",
) -> None:
    financial_summaries, broker_summaries, report_warnings = load_local_report_summaries(
        topic=args.topic,
        financial_report_dir=args.financial_report_dir,
        broker_report_dir=args.broker_report_dir,
    )
    if financial_summaries or broker_summaries or report_warnings:
        report_summary_path = out_dir / "local_report_summaries.json"
        report_summary_path.write_text(
            json.dumps(
                {
                    "financial_report_summaries": financial_summaries,
                    "broker_report_summaries": broker_summaries,
                    "warnings": report_warnings,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Local report summaries: {report_summary_path}")
        if report_warnings:
            print("Local report warnings:")
            for item in report_warnings[:10]:
                print(f"- {item}")
    elif args.financial_report_dir or args.broker_report_dir:
        print(
            "Local report summaries: none. "
            "Check that --financial-report-dir/--broker-report-dir exist and contain files matching the topic keyword.",
            flush=True,
        )

    final_mode = args.output_mode == "final"
    reply_json = out_dir / "llm_topic_reply.json"
    reply_md = out_dir / "llm_topic_reply.md"
    reply_html = out_dir / "llm_topic_reply.html"
    reply_report_md = out_dir / ("final_report.md" if final_mode else "llm_professional_report.md")
    reply_report_html = out_dir / ("final_report.html" if final_mode else "llm_professional_report.html")
    reply_report_chunks = out_dir / "llm_professional_chunks.json"
    event_signal_md = out_dir / "llm_event_signal_analysis.md"
    event_signal_html = out_dir / "llm_event_signal_analysis.html"
    if not final_mode:
        try:
            reply = generate_topic_reply(report_data)
            reply_markdown = render_topic_reply(reply)
            reply_json.write_text(json.dumps(reply, ensure_ascii=False, indent=2), encoding="utf-8")
            reply_md.write_text(reply_markdown, encoding="utf-8")
            reply_html.write_text(_render_html(args, reply_markdown, title=f"{args.topic} LLM Reply"), encoding="utf-8")
        except Exception as exc:
            print(f"LLM summary failed, continue professional report: {type(exc).__name__}: {exc}")
        try:
            event_signal_markdown = generate_event_signal_markdown(report_data)
            event_signal_md.write_text(event_signal_markdown, encoding="utf-8")
            event_signal_html.write_text(
                _render_html(args, event_signal_markdown, title=f"{args.topic} LLM Event Signal Analysis"),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"LLM event signal analysis failed: {type(exc).__name__}: {exc}")
    try:
        professional_payload = build_professional_evidence_pack(
            report_data,
            local_financial_summaries=financial_summaries,
            broker_report_summaries=broker_summaries,
        )
        with llm_stream_events(_llm_stream_event_writer(out_dir, phase="professional_report")):
            chunked_result = generate_chunked_professional_topic_report(professional_payload)
        professional_markdown = _prepend_lead_markdown(lead_markdown, chunked_result["markdown"])
        _emit_run_event(out_dir, "stage", "报告质量检查", "正在检查事实证据、章节结构和公开展示内容")
        llm_professional_markdown = _apply_report_quality_agent(
            args,
            out_dir,
            professional_markdown,
            report_data,
        )
        reply_report_md.write_text(llm_professional_markdown, encoding="utf-8")
        if not final_mode:
            reply_report_chunks.write_text(json.dumps(chunked_result["chunk_notes"], ensure_ascii=False, indent=2), encoding="utf-8")
        reply_report_html.write_text(
            _render_html(args, llm_professional_markdown, title=f"{args.topic} Final Report" if final_mode else f"{args.topic} LLM Professional Report"),
            encoding="utf-8",
        )
        _emit_run_event(
            out_dir,
            "model_output",
            "LLM 最终报告已生成",
            llm_professional_markdown[:900],
            {"artifact": reply_report_md.name, "chunks": len(chunked_result.get("chunk_notes") or [])},
        )
    except Exception as exc:
        print(f"LLM professional report failed: {type(exc).__name__}: {exc}")
    if reply_json.exists():
        print(f"LLM JSON: {reply_json}")
    if reply_md.exists():
        print(f"LLM Markdown: {reply_md}")
    if reply_html.exists():
        print(f"LLM HTML: {reply_html}")
    if reply_report_md.exists():
        print(f"LLM Professional Report: {reply_report_md}")
    if reply_report_html.exists():
        print(f"LLM Professional HTML: {reply_report_html}")
    if reply_report_chunks.exists():
        print(f"LLM Professional Chunks: {reply_report_chunks}")
    if event_signal_md.exists():
        print(f"LLM Event Signal Analysis: {event_signal_md}")
    if event_signal_html.exists():
        print(f"LLM Event Signal HTML: {event_signal_html}")


def _write_chain_bottleneck_llm_outputs(
    args: argparse.Namespace,
    out_dir: Path,
    chain_report_data: dict,
    agent_report_data: dict | None = None,
    *,
    source_report_data: dict | None = None,
    lead_markdown: str = "",
) -> None:
    llm_chain_md = out_dir / "llm_chain_bottleneck_report.md"
    llm_chain_html = out_dir / "llm_chain_bottleneck_report.html"
    try:
        from chainalpha.investment.chain_bottleneck import ChainBottleneckReport, BottleneckNode, BottleneckDimension

        nodes = []
        for node_data in chain_report_data.get("nodes") or []:
            dims = [
                BottleneckDimension(
                    key=dim.get("key", ""),
                    label=dim.get("label", ""),
                    score=float(dim.get("score") or 0),
                    weight=int(dim.get("weight") or 0),
                    evidence=dim.get("evidence") or [],
                    gaps=dim.get("gaps") or [],
                )
                for dim in node_data.get("dimension_scores") or []
            ]
            nodes.append(
                BottleneckNode(
                    node=node_data.get("node", ""),
                    aliases=node_data.get("aliases") or [],
                    companies=node_data.get("companies") or [],
                    dimension_scores=dims,
                    bottleneck_score=float(node_data.get("bottleneck_score") or 0),
                    tier=node_data.get("tier", "待验证"),
                    investability=node_data.get("investability", "数据不足"),
                    evidence=node_data.get("evidence") or [],
                    risks=node_data.get("risks") or [],
                    falsification=node_data.get("falsification") or [],
                )
            )
        chain_report = ChainBottleneckReport(
            topic=chain_report_data.get("topic", args.topic),
            target_company=chain_report_data.get("target_company") or {},
            target_question=chain_report_data.get("target_question", ""),
            nodes=nodes,
            chain_map=chain_report_data.get("chain_map") or [],
            conclusion=chain_report_data.get("conclusion") or [],
            data_gaps=chain_report_data.get("data_gaps") or [],
            warnings=chain_report_data.get("warnings") or [],
        )
        report_data = {
            **(source_report_data or {}),
            "topic": chain_report.topic,
            "companies": [{"company": chain_report.target_company}],
            "chain_bottleneck_report": chain_report_data,
            "five_agent_report": agent_report_data or {},
        }
        with llm_stream_events(_llm_stream_event_writer(out_dir, phase="chain_bottleneck_report")):
            markdown = generate_llm_chain_bottleneck_report(chain_report, agent_report=agent_report_data)
        markdown = _prepend_lead_markdown(lead_markdown, markdown)
        markdown = _apply_report_quality_agent(args, out_dir, markdown, report_data)
        llm_chain_md.write_text(markdown, encoding="utf-8")
        llm_chain_html.write_text(_render_html(args, markdown, title=f"{args.topic} LLM Chain Bottleneck Report"), encoding="utf-8")
        _emit_run_event(out_dir, "model_output", "LLM 产业链报告已生成", markdown[:900], {"artifact": llm_chain_md.name})
        print(f"LLM Chain Bottleneck Report: {llm_chain_md}")
        print(f"LLM Chain Bottleneck HTML: {llm_chain_html}")
    except Exception as exc:
        print(f"LLM chain bottleneck report failed: {type(exc).__name__}: {exc}")


def _write_chain_explainer_llm_outputs(
    args: argparse.Namespace,
    out_dir: Path,
    report_data: dict,
    agent_report_data: dict | None = None,
    *,
    lead_markdown: str = "",
) -> None:
    llm_chain_md = out_dir / "llm_chain_bottleneck_report.md"
    llm_chain_html = out_dir / "llm_chain_bottleneck_report.html"
    try:
        payload = {**report_data, "_report_kind": "chain_explainer"}
        with llm_stream_events(_llm_stream_event_writer(out_dir, phase="chain_explainer_report")):
            markdown = generate_llm_chain_explainer_report(payload, agent_report=agent_report_data)
        markdown = _prepend_lead_markdown(lead_markdown, markdown)
        markdown = _apply_report_quality_agent(args, out_dir, markdown, payload)
        llm_chain_md.write_text(markdown, encoding="utf-8")
        llm_chain_html.write_text(_render_html(args, markdown, title=f"{args.topic} Chain Explainer Report"), encoding="utf-8")
        _emit_run_event(out_dir, "model_output", "LLM 产业链解释报告已生成", markdown[:900], {"artifact": llm_chain_md.name})
        print(f"LLM Chain Explainer Report: {llm_chain_md}")
        print(f"LLM Chain Explainer HTML: {llm_chain_html}")
    except Exception as exc:
        print(f"LLM chain explainer report failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
