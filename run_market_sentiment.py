from __future__ import annotations

import argparse
import json
from pathlib import Path

from chainalpha.investment.market_sentiment import (
    build_market_sentiment_report,
    render_market_sentiment_markdown,
)
from chainalpha.reports.html_renderer import render_markdown_html


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Baidu/Weibo hot searches and score market sentiment.")
    parser.add_argument("--limit-per-source", type=int, default=50, help="Maximum hot-search items per source.")
    parser.add_argument("--store-dir", type=Path, default=Path("./data/market_sentiment"), help="Date-partitioned sentiment store.")
    parser.add_argument("--out-dir", type=Path, default=Path("./data/sentiment_runs"), help="Run output directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_market_sentiment_report(
        limit_per_source=args.limit_per_source,
        persist_dir=args.store_dir,
    )

    run_dir = args.out_dir / report.generated_at.replace(":", "").replace("-", "")[:15]
    run_dir.mkdir(parents=True, exist_ok=True)
    report_json = run_dir / "market_sentiment.json"
    report_md = run_dir / "market_sentiment.md"
    report_html = run_dir / "market_sentiment.html"
    markdown = render_market_sentiment_markdown(report)
    report_json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(markdown, encoding="utf-8")
    report_html.write_text(render_markdown_html(markdown, title="Market Sentiment"), encoding="utf-8")

    print(f"Score: {report.score:.1f}")
    print(f"Label: {report.label}")
    print(f"Summary: {report.summary}")
    print(f"Hot items: {len(report.hot_items)}")
    if report.storage:
        print(f"Stored folder: {report.storage.get('day_dir')}")
        print(f"Hot items JSONL: {report.storage.get('hot_items_jsonl')}")
    if report.warnings:
        print("Warnings:")
        for item in report.warnings[:10]:
            print(f"- {item}")
    print(f"Report JSON: {report_json}")
    print(f"Report Markdown: {report_md}")
    print(f"Report HTML: {report_html}")


if __name__ == "__main__":
    main()
