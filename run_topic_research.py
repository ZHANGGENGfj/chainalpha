from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from chainalpha.investment.daily_market_scan_enhanced import (
    build_daily_market_scan,
    render_daily_market_scan_markdown,
)
from chainalpha.investment.llm_reply import (
    generate_professional_topic_markdown,
    generate_topic_reply,
    render_topic_reply,
)
from chainalpha.investment.topic_report import (
    build_topic_research_report,
    render_professional_topic_report,
    render_topic_research_markdown,
)
from chainalpha.reports.html_renderer import render_markdown_html


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ChainAlpha topic research or daily market scan.")
    parser.add_argument("--topic", default="A股市场", help="Company, sector, or daily-scan query.")
    parser.add_argument("--daily-scan", action="store_true", help="Run daily market information collection and first-pass screening.")
    parser.add_argument("--scan-symbols", default="", help="Comma-separated symbols for technical screening, e.g. 300308,300502.")
    parser.add_argument("--scan-top-k", type=int, default=30, help="Maximum technical/sector candidates in daily scan.")
    parser.add_argument("--daily-store-dir", type=Path, default=Path("./data/daily_market"), help="Date-partitioned storage directory for daily scan artifacts.")
    parser.add_argument("--config", type=Path, default=Path("./configs/sources_full.yaml"), help="Data-source connector YAML.")
    parser.add_argument("--company-map", type=Path, default=Path("./configs/company_map.yaml"), help="Company mapping YAML.")
    parser.add_argument("--financials", type=Path, default=None, help="Optional financial snapshot CSV. Leave empty to avoid sample data.")
    parser.add_argument("--prices", type=Path, default=None, help="Optional price snapshot CSV. Leave empty to avoid sample data.")
    parser.add_argument("--run-dir", type=Path, default=Path("./data/topic_runs"), help="Output directory.")
    parser.add_argument("--limit", type=int, default=80, help="Maximum source documents to fetch.")
    parser.add_argument("--top-k-chunks", type=int, default=60, help="Top chunks to analyze.")
    parser.add_argument("--top-k-candidates", type=int, default=8, help="Maximum candidates to output.")
    parser.add_argument("--llm-summary", action="store_true", help="Generate final LLM summary.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = args.run_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.daily_scan:
        _run_daily_scan(args, out_dir)
        return

    _run_topic_research(args, out_dir)


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
    report_html.write_text(render_markdown_html(markdown, title=f"{args.topic} Daily Market Scan"), encoding="utf-8")

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
    report = build_topic_research_report(
        topic=args.topic,
        config=args.config,
        company_map=args.company_map,
        financials=args.financials,
        prices=args.prices,
        limit=args.limit,
        top_k_chunks=args.top_k_chunks,
        top_k_candidates=args.top_k_candidates,
    )

    report_json = out_dir / "topic_research.json"
    report_md = out_dir / "topic_research.md"
    report_html = out_dir / "topic_research.html"
    professional_md = out_dir / "topic_professional_report.md"
    professional_html = out_dir / "topic_professional_report.html"
    report_markdown = render_topic_research_markdown(report)
    professional_markdown = render_professional_topic_report(report)
    report_json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(report_markdown, encoding="utf-8")
    professional_md.write_text(professional_markdown, encoding="utf-8")
    report_html.write_text(render_markdown_html(report_markdown, title=f"{args.topic} Topic Research"), encoding="utf-8")
    professional_html.write_text(
        render_markdown_html(professional_markdown, title=f"{args.topic} Professional Report"),
        encoding="utf-8",
    )

    print(f"Topic: {args.topic}")
    print(f"Intent: {report.intent.input_type} -> {report.intent.target}")
    print(f"Report JSON: {report_json}")
    print(f"Report Markdown: {report_md}")
    print(f"Report HTML: {report_html}")
    print(f"Professional Markdown: {professional_md}")
    print(f"Professional HTML: {professional_html}")

    if args.llm_summary:
        _write_llm_outputs(args, out_dir, report.to_dict())


def _write_llm_outputs(args: argparse.Namespace, out_dir: Path, report_data: dict) -> None:
    try:
        reply = generate_topic_reply(report_data)
    except Exception as exc:
        print(f"LLM summary failed: {type(exc).__name__}: {exc}")
        return

    reply_json = out_dir / "llm_topic_reply.json"
    reply_md = out_dir / "llm_topic_reply.md"
    reply_html = out_dir / "llm_topic_reply.html"
    reply_report_md = out_dir / "llm_professional_report.md"
    reply_report_html = out_dir / "llm_professional_report.html"
    reply_markdown = render_topic_reply(reply)
    reply_json.write_text(json.dumps(reply, ensure_ascii=False, indent=2), encoding="utf-8")
    reply_md.write_text(reply_markdown, encoding="utf-8")
    reply_html.write_text(render_markdown_html(reply_markdown, title=f"{args.topic} LLM Reply"), encoding="utf-8")
    try:
        llm_professional_markdown = generate_professional_topic_markdown(report_data)
        reply_report_md.write_text(llm_professional_markdown, encoding="utf-8")
        reply_report_html.write_text(
            render_markdown_html(llm_professional_markdown, title=f"{args.topic} LLM Professional Report"),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"LLM professional report failed: {type(exc).__name__}: {exc}")
    print(f"LLM JSON: {reply_json}")
    print(f"LLM Markdown: {reply_md}")
    print(f"LLM HTML: {reply_html}")
    if reply_report_md.exists():
        print(f"LLM Professional Report: {reply_report_md}")
    if reply_report_html.exists():
        print(f"LLM Professional HTML: {reply_report_html}")


if __name__ == "__main__":
    main()
