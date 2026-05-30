from __future__ import annotations

import argparse
import time
from pathlib import Path

from chainalpha.investment.daily_market_scan_enhanced import build_daily_market_scan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect daily market information and store it locally.")
    parser.add_argument("--topic", default="A股市场", help="Collection query/topic.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols, e.g. 300308,300502.")
    parser.add_argument("--limit", type=int, default=120, help="Maximum news/source documents to collect.")
    parser.add_argument("--top-k", type=int, default=50, help="Maximum technical/sector candidates.")
    parser.add_argument("--store-dir", type=Path, default=Path("./data/daily_market"), help="Date-partitioned storage directory.")
    parser.add_argument("--no-financials", action="store_true", help="Skip financial snapshot collection.")
    parser.add_argument("--no-announcements", action="store_true", help="Skip announcement collection.")
    parser.add_argument("--no-research", action="store_true", help="Skip research report collection.")
    parser.add_argument("--loop", action="store_true", help="Keep collecting on an interval.")
    parser.add_argument("--interval-minutes", type=float, default=60.0, help="Collection interval when --loop is enabled.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]

    while True:
        report = collect_once(args, symbols)
        print("Collection finished.", flush=True)
        print(f"Generated at: {report.generated_at}", flush=True)
        print(f"Raw documents: {len(report.raw_documents)}", flush=True)
        print(f"Technical candidates: {len(report.technical_candidates)}", flush=True)
        print(f"Sector candidates: {len(report.sector_candidates)}", flush=True)
        if report.storage:
            print(f"Stored folder: {report.storage.get('day_dir')}", flush=True)
            print(f"Summary JSON: {report.storage.get('summary_json')}", flush=True)
            print(f"Summary Markdown: {report.storage.get('summary_md')}", flush=True)
        if report.warnings:
            print("Warnings:", flush=True)
            for warning in report.warnings[:30]:
                print(f"- {warning}", flush=True)

        if not args.loop:
            break

        sleep_seconds = max(args.interval_minutes, 0.1) * 60
        print(f"Sleeping {args.interval_minutes:g} minutes before next collection...", flush=True)
        time.sleep(sleep_seconds)


def collect_once(args: argparse.Namespace, symbols: list[str]):
    print(
        f"Collecting market data: topic={args.topic}, limit={args.limit}, top_k={args.top_k}, store={args.store_dir}",
        flush=True,
    )
    if symbols:
        print(f"Symbols: {', '.join(symbols)}", flush=True)

    return build_daily_market_scan(
        query=args.topic,
        symbols=symbols,
        limit=args.limit,
        top_k=args.top_k,
        include_financials=not args.no_financials,
        include_announcements=not args.no_announcements,
        include_research=not args.no_research,
        persist_dir=args.store_dir,
        progress=lambda message: print(f"[collect] {message}", flush=True),
    )


if __name__ == "__main__":
    main()
