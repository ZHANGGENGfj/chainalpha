from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from chainalpha.reports.html_renderer import render_markdown_html


EVENT_CATEGORIES = ("policy", "industry", "company", "international")
CATEGORY_NAMES = {
    "policy": "政策",
    "industry": "行业",
    "company": "公司",
    "international": "国际",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze locally collected daily market information.")
    parser.add_argument("--date", default="", help="Date to analyze, e.g. 2026-05-28. Defaults to latest local date.")
    parser.add_argument("--store-dir", type=Path, default=Path("./data/daily_market"), help="Collection storage directory.")
    parser.add_argument("--out-dir", type=Path, default=Path("./data/daily_market_analysis"), help="Analysis output directory.")
    parser.add_argument("--top-events", type=int, default=12, help="Maximum events per category.")
    parser.add_argument("--top-candidates", type=int, default=30, help="Maximum candidates to show.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    day_dir = resolve_day_dir(args.store_dir, args.date)
    report_data = load_collected_report(day_dir)

    run_dir = args.out_dir / day_dir.name / datetime.now().strftime("%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    markdown = render_local_market_analysis(
        report_data,
        day_dir=day_dir,
        top_events=args.top_events,
        top_candidates=args.top_candidates,
    )
    json_path = run_dir / "market_analysis_input.json"
    md_path = run_dir / "market_analysis.md"
    html_path = run_dir / "market_analysis.html"

    json_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(render_markdown_html(markdown, title=f"{day_dir.name} Market Analysis"), encoding="utf-8")

    print(f"Analyzed local collection folder: {day_dir}", flush=True)
    print(f"Analysis JSON: {json_path}", flush=True)
    print(f"Analysis Markdown: {md_path}", flush=True)
    print(f"Analysis HTML: {html_path}", flush=True)


def resolve_day_dir(store_dir: Path, date_text: str) -> Path:
    if date_text:
        day_dir = store_dir / date_text
        if not day_dir.exists():
            raise SystemExit(f"Local collection date not found: {day_dir}")
        return day_dir

    candidates = sorted(
        [
            path
            for path in store_dir.iterdir()
            if path.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name)
        ],
        reverse=True,
    ) if store_dir.exists() else []
    if not candidates:
        raise SystemExit(f"No local collection folders found under {store_dir}")
    return candidates[0]


def load_collected_report(day_dir: Path) -> dict[str, Any]:
    summary_json = day_dir / "reports" / "daily_market_scan.json"
    if summary_json.exists():
        return json.loads(summary_json.read_text(encoding="utf-8"))

    processed_dir = day_dir / "processed"
    raw_dir = day_dir / "raw"
    return {
        "generated_at": day_dir.name,
        "query": "local daily market data",
        "classified_events": group_events(load_jsonl(processed_dir / "classified_events.jsonl")),
        "technical_candidates": load_jsonl(processed_dir / "technical_candidates.jsonl"),
        "sector_candidates": load_jsonl(processed_dir / "sector_candidates.jsonl"),
        "financial_snapshots": load_jsonl(processed_dir / "financial_snapshots.jsonl"),
        "announcements": load_jsonl(processed_dir / "announcements.jsonl"),
        "research_reports": load_jsonl(processed_dir / "research_reports.jsonl"),
        "raw_documents": load_jsonl(raw_dir / "deduped_news.jsonl"),
        "storage": {"day_dir": str(day_dir)},
        "warnings": [],
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def group_events(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {category: [] for category in EVENT_CATEGORIES}
    for row in rows:
        grouped.setdefault(row.get("category") or "company", []).append(row)
    return grouped


def render_local_market_analysis(
    data: dict[str, Any],
    *,
    day_dir: Path,
    top_events: int,
    top_candidates: int,
) -> str:
    classified = data.get("classified_events") or {}
    technical = data.get("technical_candidates") or []
    sectors = data.get("sector_candidates") or []
    financials = data.get("financial_snapshots") or []
    announcements = data.get("announcements") or []
    research_reports = data.get("research_reports") or []
    raw_documents = data.get("raw_documents") or []
    warnings = data.get("warnings") or []

    lines = [
        f"# 每日市场本地分析：{data.get('query') or day_dir.name}",
        "",
        f"> 数据目录：`{day_dir}`",
        f"> 采集时间：{data.get('generated_at') or '-'}",
        "",
        "## 1. 数据概览",
        "",
        f"- 新闻/资讯文档：{len(raw_documents)}",
        f"- 结构化事件：{sum(len(classified.get(category, [])) for category in EVENT_CATEGORIES)}",
        f"- 技术候选：{len(technical)}",
        f"- 板块候选：{len(sectors)}",
        f"- 财务快照：{len(financials)}",
        f"- 公司公告：{len(announcements)}",
        f"- 研报/预测：{len(research_reports)}",
        "",
    ]

    if warnings:
        lines.extend(["## 2. 数据源告警", ""])
        for warning in dedup_strings(warnings)[:40]:
            lines.append(f"- {warning}")
        lines.append("")

    lines.extend(["## 3. 新闻事件分类", ""])
    for category in EVENT_CATEGORIES:
        events = sorted(
            classified.get(category, []),
            key=lambda item: (float_or_zero(item.get("impact_score")), item.get("published_at") or ""),
            reverse=True,
        )
        lines.append(f"### {CATEGORY_NAMES.get(category, category)}")
        if not events:
            lines.append("- 本地数据中暂无该类事件。")
        for item in events[:top_events]:
            tags = ", ".join(item.get("tags") or []) or "-"
            lines.append(
                f"- {item.get('published_at') or 'date-unknown'} | "
                f"impact={float_or_zero(item.get('impact_score')):.1f} | "
                f"{item.get('title') or '-'} | {tags} | {item.get('source') or '-'} | {item.get('url') or '-'}"
            )
        lines.append("")

    lines.extend(["## 4. 技术与资金初筛", ""])
    render_signal_table(lines, technical[:top_candidates], label="个股")
    lines.append("")

    lines.extend(["## 5. 板块强弱初筛", ""])
    render_signal_table(lines, sectors[:top_candidates], label="板块")
    lines.append("")

    lines.extend(["## 6. 财务兑现观察", ""])
    if financials:
        lines.extend([
            "| 代码 | 名称 | 报告期 | 营收 | 营收同比 | 净利润 | 净利同比 | ROE | 经营现金流 |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        for item in financials[:top_candidates]:
            lines.append(
                f"| {item.get('symbol') or '-'} | {item.get('name') or '-'} | {item.get('report_date') or '-'} | "
                f"{fmt_num(item.get('revenue'))} | {fmt_pct(item.get('revenue_yoy'))} | "
                f"{fmt_num(item.get('net_profit'))} | {fmt_pct(item.get('net_profit_yoy'))} | "
                f"{fmt_pct(item.get('roe'))} | {fmt_num(item.get('operating_cashflow'))} |"
            )
    else:
        lines.append("- 本地数据中暂无财务快照。")
    lines.append("")

    lines.extend(["## 7. 公告与研报线索", ""])
    if announcements:
        lines.append("### 公司公告")
        for item in announcements[:top_candidates]:
            tags = ", ".join(item.get("tags") or []) or "-"
            lines.append(
                f"- {item.get('published_at') or 'date-unknown'} | {item.get('symbol') or ''} {item.get('name') or ''} | "
                f"importance={float_or_zero(item.get('importance')):.1f} | {tags} | {item.get('title') or '-'}"
            )
    else:
        lines.append("- 本地数据中暂无公告。")
    lines.append("")

    if research_reports:
        lines.append("### 研报/盈利预测")
        for item in research_reports[:top_candidates]:
            lines.append(
                f"- {item.get('report_date') or 'date-unknown'} | {item.get('symbol') or ''} {item.get('name') or ''} | "
                f"{item.get('institution') or '-'} | {item.get('rating') or '-'} | {item.get('title') or '-'}"
            )
    else:
        lines.append("- 本地数据中暂无研报/盈利预测。")
    lines.append("")

    lines.extend([
        "## 8. 分析结论",
        "",
        "- 这个报告只基于本地已经采集的数据生成，不会实时联网。",
        "- 如果某类数据为空，优先检查采集端对应接口是否成功，而不是分析端。",
        "- 后续可以在这里继续接 LLM，让 LLM 只读取本地 JSON，不直接访问外部数据源。",
        "",
    ])
    return "\n".join(lines)


def render_signal_table(lines: list[str], rows: list[dict[str, Any]], *, label: str) -> None:
    if not rows:
        lines.append(f"- 本地数据中暂无{label}候选。")
        return
    lines.extend([
        "| 排名 | 代码 | 名称 | 分数 | 涨跌幅 | 成交额 | 净流入 | 信号 |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ])
    for idx, item in enumerate(rows, 1):
        signals = "; ".join(item.get("signals") or []) or "-"
        lines.append(
            f"| {idx} | {item.get('symbol') or '-'} | {item.get('name') or '-'} | "
            f"{float_or_zero(item.get('score')):.1f} | {fmt_pct(item.get('change_pct'))} | "
            f"{fmt_num(item.get('turnover'))} | {fmt_num(item.get('net_inflow'))} | {signals} |"
        )


def dedup_strings(items: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in items if item))


def float_or_zero(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def fmt_num(value: Any) -> str:
    try:
        if value in (None, ""):
            return "-"
        number = float(value)
    except Exception:
        return str(value)
    if abs(number) >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿"
    if abs(number) >= 10_000:
        return f"{number / 10_000:.2f}万"
    return f"{number:.2f}"


def fmt_pct(value: Any) -> str:
    try:
        if value in (None, ""):
            return "-"
        return f"{float(value):.2f}%"
    except Exception:
        return str(value)


if __name__ == "__main__":
    main()
