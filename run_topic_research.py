from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from chainalpha.investment.daily_market_scan_enhanced import (
    build_daily_market_scan,
    render_daily_market_scan_markdown,
)
from chainalpha.investment.llm_reply import (
    generate_event_signal_markdown,
    generate_chunked_professional_topic_report,
    generate_topic_reply,
    render_topic_reply,
)
from chainalpha.investment.evidence_pack import build_professional_evidence_pack
from chainalpha.investment.query_expansion import QueryExpansion, expand_user_query
from chainalpha.investment.report_extractors import load_local_report_summaries
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
    parser.add_argument("--config", type=Path, default=Path("./configs/sources_mediacrawler.yaml"), help="Data-source connector YAML.")
    parser.add_argument(
        "--media-crawler-root",
        type=Path,
        default=None,
        help="Override MediaCrawler data directory, e.g. D:/lh/gupiao_agents/MediaCrawler-main/data.",
    )
    parser.add_argument(
        "--media-crawler-platforms",
        default="finance_news,cninfo_reports,broker_reports,claim_tasks,cls,xq",
        help="Comma-separated MediaCrawler data folders to read.",
    )
    parser.add_argument(
        "--skip-refresh-news",
        action="store_true",
        help="Do not trigger MediaCrawler targeted collection before topic analysis.",
    )
    parser.add_argument(
        "--require-refresh-news",
        action="store_true",
        help="Fail the run when MediaCrawler targeted collection fails.",
    )
    parser.add_argument(
        "--media-crawler-python",
        default=sys.executable,
        help="Python executable used to run MediaCrawler scripts.",
    )
    parser.add_argument(
        "--media-crawler-timeout",
        type=int,
        default=90,
        help="Seconds to wait for each MediaCrawler platform before continuing to the next one.",
    )
    parser.add_argument(
        "--crawl-sources",
        default="",
        help="Reserved for targeted collector. MediaCrawler main.py finance_news currently uses its built-in source list.",
    )
    parser.add_argument(
        "--crawl-limit-per-source",
        type=int,
        default=80,
        help="Reserved for targeted collector. MediaCrawler main.py finance_news currently uses its internal limit.",
    )
    parser.add_argument("--company-map", type=Path, default=Path("./configs/company_map.yaml"), help="Company mapping YAML.")
    parser.add_argument("--financials", type=Path, default=None, help="Optional financial snapshot CSV. Leave empty to avoid sample data.")
    parser.add_argument("--prices", type=Path, default=None, help="Optional price snapshot CSV. Leave empty to avoid sample data.")
    parser.add_argument("--financial-report-dir", type=Path, default=None, help="Optional local annual/quarterly report PDF/text directory or file.")
    parser.add_argument("--broker-report-dir", type=Path, default=None, help="Optional local broker/institution research report PDF/text directory or file.")
    parser.add_argument("--run-dir", type=Path, default=Path("./data/topic_runs"), help="Output directory.")
    parser.add_argument("--limit", type=int, default=80, help="Maximum source documents to fetch.")
    parser.add_argument("--top-k-chunks", type=int, default=60, help="Top chunks to analyze.")
    parser.add_argument("--top-k-companies", type=int, default=1, help="Maximum related companies to analyze. Defaults to target-company focus.")
    parser.add_argument("--top-k-candidates", type=int, default=8, help="Maximum candidates to output.")
    parser.add_argument("--include-a-share-reports", action="store_true", help="Fetch and parse A-share filings/PDF reports. Disabled by default.")
    parser.add_argument("--no-query-expansion", action="store_true", help="Disable LLM/rule query correction and expansion.")
    parser.add_argument("--llm-summary", action="store_true", help="Generate final LLM summary.")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
    media_data_root = resolve_media_crawler_data_root(args.media_crawler_root)
    query_expansion = expand_user_query(
        args.topic,
        company_map=str(args.company_map),
        enable_llm=not args.no_query_expansion,
    )
    query_expansion_path = out_dir / "query_expansion.json"
    query_expansion_path.write_text(json.dumps(query_expansion.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Query expansion: {query_expansion.corrected_query} | {', '.join(query_expansion.search_keywords[:12])}", flush=True)
    print(f"Query expansion JSON: {query_expansion_path}", flush=True)

    if not args.skip_refresh_news:
        refresh_result = refresh_mediacrawler_news(args, out_dir, media_data_root, query_expansion)
        if refresh_result:
            metadata_path = out_dir / "media_crawler_refresh.json"
            metadata_path.write_text(json.dumps(refresh_result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"MediaCrawler refresh metadata: {metadata_path}", flush=True)

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


def resolve_media_crawler_data_root(path: Path | None) -> Path:
    if path is None:
        return Path("./third_party/MediaCrawler-main/data")
    p = path
    if p.name == "data":
        return p
    return p / "data"


def resolve_media_crawler_app_root(data_root: Path) -> Path:
    return data_root.parent if data_root.name == "data" else data_root


def refresh_mediacrawler_news(
    args: argparse.Namespace,
    out_dir: Path,
    data_root: Path,
    query_expansion: QueryExpansion | None = None,
) -> dict | None:
    app_root = resolve_media_crawler_app_root(data_root).resolve()
    script = (app_root / "main.py").resolve()
    if not script.exists():
        message = f"MediaCrawler main script not found: {script}"
        if args.require_refresh_news:
            raise SystemExit(message)
        print(f"[media-crawler] skip refresh: {message}", flush=True)
        return {"ok": False, "error": message}

    keywords = query_expansion.crawl_keywords if query_expansion else build_crawl_keywords(args.topic)
    if not keywords:
        keywords = build_crawl_keywords(args.topic)
    platforms = ["cninfo_reports", "broker_reports", "finance_news"]
    # main.py / finance_news currently uses the source list inside
    # media_platform.finance_news.client.FinanceNewsClient. --crawl-sources is
    # kept for the older targeted collector and future source filtering.
    _ = args.crawl_sources
    _ = args.crawl_limit_per_source

    results: list[dict] = []
    all_ok = True
    for platform in platforms:
        command = [
            args.media_crawler_python,
            str(script),
            "--platform",
            platform,
            "--type",
            "search",
            "--keywords",
            ",".join(keywords),
            "--save_data_option",
            "jsonl",
            "--headless",
            "true",
        ]
        print(f"[media-crawler] running main.py {platform} for: {', '.join(keywords)}", flush=True)
        try:
            result = subprocess.run(
                command,
                cwd=app_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env(),
                timeout=args.media_crawler_timeout,
            )
            output = result.stdout + "\n" + result.stderr
            returncode = result.returncode
            ok = returncode == 0
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + "\n" + (
                (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            )
            returncode = -1
            ok = False
            output = f"MediaCrawler platform timed out after {args.media_crawler_timeout}s.\n{output}"
        all_ok = all_ok and ok
        if not ok:
            print(f"[media-crawler] {platform} refresh failed, continue with local data: {output.strip()[:800]}", flush=True)
        else:
            print(f"[media-crawler] {platform} refresh done: saved under {data_root / platform}", flush=True)
        results.append(
            {
                "platform": platform,
                "ok": ok,
                "returncode": returncode,
                "keywords": keywords,
                "command": command,
                "data_dir": str(data_root / platform),
                "parsed": parse_key_value_output(output),
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )

    if not all_ok and args.require_refresh_news:
        failed = [item["platform"] for item in results if not item["ok"]]
        raise SystemExit(f"MediaCrawler collection failed for: {', '.join(failed)}")

    return {
        "ok": all_ok,
        "keywords": keywords,
        "data_root": str(data_root),
        "platforms": results,
    }


def build_crawl_keywords(topic: str) -> list[str]:
    terms = [item.strip() for item in re.split(r"[,，;；\s]+", topic) if item.strip()]
    if topic.strip() and topic.strip() not in terms:
        terms.insert(0, topic.strip())
    return list(dict.fromkeys(terms))[:8]


def parse_key_value_output(output: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = "." if not existing else f".{os.pathsep}{existing}"
    env.pop("SSLKEYLOGFILE", None)
    return env


def _write_llm_outputs(args: argparse.Namespace, out_dir: Path, report_data: dict) -> None:
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

    reply_json = out_dir / "llm_topic_reply.json"
    reply_md = out_dir / "llm_topic_reply.md"
    reply_html = out_dir / "llm_topic_reply.html"
    reply_report_md = out_dir / "llm_professional_report.md"
    reply_report_html = out_dir / "llm_professional_report.html"
    reply_report_chunks = out_dir / "llm_professional_chunks.json"
    event_signal_md = out_dir / "llm_event_signal_analysis.md"
    event_signal_html = out_dir / "llm_event_signal_analysis.html"
    try:
        reply = generate_topic_reply(report_data)
        reply_markdown = render_topic_reply(reply)
        reply_json.write_text(json.dumps(reply, ensure_ascii=False, indent=2), encoding="utf-8")
        reply_md.write_text(reply_markdown, encoding="utf-8")
        reply_html.write_text(render_markdown_html(reply_markdown, title=f"{args.topic} LLM Reply"), encoding="utf-8")
    except Exception as exc:
        print(f"LLM summary failed, continue professional report: {type(exc).__name__}: {exc}")
    try:
        event_signal_markdown = generate_event_signal_markdown(report_data)
        event_signal_md.write_text(event_signal_markdown, encoding="utf-8")
        event_signal_html.write_text(
            render_markdown_html(event_signal_markdown, title=f"{args.topic} LLM Event Signal Analysis"),
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
        chunked_result = generate_chunked_professional_topic_report(professional_payload)
        llm_professional_markdown = chunked_result["markdown"]
        reply_report_md.write_text(llm_professional_markdown, encoding="utf-8")
        reply_report_chunks.write_text(json.dumps(chunked_result["chunk_notes"], ensure_ascii=False, indent=2), encoding="utf-8")
        reply_report_html.write_text(
            render_markdown_html(llm_professional_markdown, title=f"{args.topic} LLM Professional Report"),
            encoding="utf-8",
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


if __name__ == "__main__":
    main()
