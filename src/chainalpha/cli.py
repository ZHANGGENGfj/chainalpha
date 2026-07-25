from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from chainalpha.memory.obsidian_loader import ObsidianLoader
from chainalpha.memory.index import HybridIndex
from chainalpha.memory.writer import ObsidianWriter
from chainalpha.pipeline import run_scan, run_scan_from_sources, DEFAULT_QUERY
from chainalpha.connectors.base import FetchRequest
from chainalpha.connectors.registry import SourceManager
from chainalpha.investment.candidates import build_investment_candidates
from chainalpha.investment.company_map import load_company_profiles, search_company_profiles
from chainalpha.investment.llm_announcement import analyze_announcement_with_llm
from chainalpha.investment.llm_reply import (
    generate_professional_topic_markdown,
    generate_topic_reply,
    render_topic_reply,
)
from chainalpha.investment.market_data import load_financial_snapshots, load_price_snapshots
from chainalpha.investment.topic_report import (
    build_topic_research_report,
    render_professional_topic_report,
    render_topic_research_markdown,
)
from chainalpha.investment.watchlist import ReviewItem, WatchItem, WatchlistStore
from chainalpha.reports.html_renderer import render_markdown_html


app = typer.Typer(help="ChainAlpha: industry-chain weak signal intelligence agent.")
console = Console()


def _print_signal_table(title: str, signals: list, show_source: bool = False) -> None:
    table = Table(title=title)
    table.add_column("Rank", justify="right")
    table.add_column("Stage")
    table.add_column("Score", justify="right")
    if show_source:
        table.add_column("Source")
    else:
        table.add_column("Attention", justify="right")
    table.add_column("Signal")
    table.add_column("Path")

    for i, sig in enumerate(signals[:10], 1):
        row = [
            str(i),
            sig.stage,
            f"{sig.weak_signal_score:.2f}",
            sig.event.source_type if show_source else f"{sig.attention_score:.1f}",
            sig.title[:60],
            " → ".join(sig.hypothesis.causal_path)[:80],
        ]
        table.add_row(*row)
    console.print(table)


def _print_candidate_table(candidates: list) -> None:
    table = Table(title="Investment Candidates")
    table.add_column("Rank", justify="right")
    table.add_column("Company")
    table.add_column("Ticker")
    table.add_column("Score", justify="right")
    table.add_column("Rating")
    table.add_column("Signals", justify="right")
    table.add_column("Thesis")

    for i, item in enumerate(candidates, 1):
        table.add_row(
            str(i),
            item.company.name,
            item.company.ticker or "",
            f"{item.score:.2f}",
            item.rating,
            str(len(item.signals)),
            item.thesis[:100],
        )
    console.print(table)


@app.command("init-vault")
def init_vault(path: Path = typer.Option(..., help="Obsidian-compatible vault path to create.")) -> None:
    path.mkdir(parents=True, exist_ok=True)
    dirs = [
        "00_Inbox",
        "01_Projects/ChainAlpha",
        "02_Industries",
        "03_Companies",
        "04_Events",
        "05_Hypotheses",
        "06_Reports",
        "07_Reviews",
        "99_Templates",
    ]
    for d in dirs:
        (path / d).mkdir(parents=True, exist_ok=True)

    sample = path / "02_Industries" / "光模块.md"
    if not sample.exists():
        sample.write_text("""---
type: industry_node
source_type: obsidian_note
industry: AI产业链
node: 光模块
stage: S2
tags: [AI产业链, 光模块, 800G, 数据中心网络]
related: [800G光模块, 1.6T光模块, 硅光, EML激光器, CPO]
---

# 光模块

## 核心逻辑

光模块需求增长的核心原因不是普通通信复苏，而是 AI 集群规模扩大后，数据中心东西向流量快速增长，网络带宽成为瓶颈。

AI CapEx 增加后，GPU集群扩张，交换机和网络带宽需要升级，400G、800G、1.6T 光模块需求提升。

## 关键弱信号

- 云厂商 AI 数据中心资本开支上调。
- 交换芯片从 51.2T 向更高速率演进。
- 800G 光模块客户认证、小批量交付或订单出现。
- EML、CW激光器、InP、硅光、LPO、CPO 供应紧张。
- 光模块公司毛利率改善、在建工程增加、库存变化。

## 相关节点

[[AI产业链]] [[800G光模块]] [[1.6T光模块]] [[硅光]] [[CPO]] [[LPO]]
""", encoding="utf-8")

    sample2 = path / "05_Hypotheses" / "数据中心电力成为新约束.md"
    if not sample2.exists():
        sample2.write_text("""---
type: hypothesis
source_type: obsidian_note
tags: [AI产业链, 数据中心, 电力, 液冷]
status: draft
---

# 数据中心电力成为新约束

## 假设

AI CapEx 上修和 GPU 集群扩张会推动 AI 数据中心建设，但数据中心的电力接入、变压器、UPS、液冷、高功率机柜可能成为新的瓶颈。

## 观察指标

- 数据中心项目备案和电网接入申请。
- 变压器、UPS、PDU、液冷设备订单。
- 云厂商资本开支和长期电力协议。
- 液冷从测试走向批量部署。
""", encoding="utf-8")

    console.print(Panel.fit(f"Vault initialized at [bold]{path.resolve()}[/bold]"))


@app.command("ask")
def ask(
    vault: Path = typer.Option(..., help="Obsidian vault path."),
    query: str = typer.Option(..., help="Question or search query."),
    top_k: int = typer.Option(8, help="Number of chunks to show."),
) -> None:
    loader = ObsidianLoader(vault)
    chunks = loader.load_chunks()
    index = HybridIndex(chunks)
    results = index.search(query, top_k=top_k)

    table = Table(title=f"Search results for: {query}")
    table.add_column("Rank", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("File")
    table.add_column("Heading")
    table.add_column("Snippet")

    for i, c in enumerate(results, 1):
        snippet = c.text.replace("\n", " ")[:180]
        table.add_row(str(i), f"{c.score:.3f}", c.path, c.heading or "", snippet)
    console.print(table)


@app.command("scan")
def scan(
    vault: Path = typer.Option(..., help="Obsidian vault path."),
    out: Path = typer.Option(Path("./outputs/reports"), help="Report output directory."),
    query: str = typer.Option(DEFAULT_QUERY, help="Scan query."),
    top_k_chunks: int = typer.Option(80, help="Top chunks to analyze."),
    write_back: bool = typer.Option(False, help="Write generated report into 00_Inbox as draft."),
) -> None:
    signals, markdown, saved = run_scan(vault, query=query, top_k_chunks=top_k_chunks, out_dir=out)

    console.print(Panel.fit(f"Detected [bold]{len(signals)}[/bold] candidate signals."))
    if saved:
        console.print(f"Report saved: [bold]{saved}[/bold]")

    _print_signal_table("Top Weak Signals", signals)

    if write_back:
        writer = ObsidianWriter(vault)
        note_path = writer.write_inbox_note("AI产业链弱信号报告", markdown, note_type="daily_signal_report")
        console.print(f"Draft note written to: [bold]{note_path}[/bold]")


@app.command("fetch-sources")
def fetch_sources(
    config: Path = typer.Option(Path("./configs/sources.yaml"), help="YAML config for data-source connectors."),
    query: str = typer.Option(DEFAULT_QUERY, help="Fetch filter query."),
    limit: int = typer.Option(50, help="Maximum documents to fetch."),
) -> None:
    """Preview documents fetched by configured connectors."""
    manager = SourceManager.from_yaml(config)
    docs, results = manager.fetch_all(FetchRequest(query=query, limit=limit))

    for err in manager.errors:
        console.print(f"[red]Config error:[/red] {err}")
    for result in results:
        for err in result.errors:
            console.print(f"[red]{result.source_name} error:[/red] {err}")

    table = Table(title=f"Fetched source documents: {len(docs)}")
    table.add_column("Rank", justify="right")
    table.add_column("Source")
    table.add_column("Type")
    table.add_column("Title")
    table.add_column("Path/URL")
    table.add_column("Snippet")
    for i, doc in enumerate(docs[:limit], 1):
        table.add_row(
            str(i),
            doc.source,
            doc.source_type,
            doc.title[:50],
            (doc.url or doc.path or "")[:60],
            doc.content.replace("\n", " ")[:120],
        )
    console.print(table)


@app.command("scan-sources")
def scan_sources(
    config: Path = typer.Option(Path("./configs/sources.yaml"), help="YAML config for data-source connectors."),
    out: Path = typer.Option(Path("./outputs/reports"), help="Report output directory."),
    query: str = typer.Option(DEFAULT_QUERY, help="Scan query."),
    top_k_chunks: int = typer.Option(120, help="Top chunks to analyze."),
    limit: int = typer.Option(500, help="Maximum source documents to fetch."),
) -> None:
    """Fetch configured sources and run weak-signal scan."""
    signals, markdown, saved, errors = run_scan_from_sources(
        config,
        query=query,
        top_k_chunks=top_k_chunks,
        out_dir=out,
        limit=limit,
    )
    for err in errors:
        console.print(f"[yellow]Source warning:[/yellow] {err}")

    console.print(Panel.fit(f"Detected [bold]{len(signals)}[/bold] candidate signals from configured sources."))
    if saved:
        console.print(f"Report saved: [bold]{saved}[/bold]")

    _print_signal_table("Top Source Weak Signals", signals, show_source=True)


@app.command("export-json")
def export_json(
    vault: Path = typer.Option(...),
    out: Path = typer.Option(Path("./outputs/signals.json")),
    query: str = typer.Option(DEFAULT_QUERY),
) -> None:
    signals, _, _ = run_scan(vault, query=query)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([s.to_dict() for s in signals], ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"Exported {len(signals)} signals to {out}")


@app.command("candidates")
def candidates(
    vault: Path | None = typer.Option(None, help="Obsidian vault path. Use either --vault or --config."),
    config: Path | None = typer.Option(None, help="YAML config for data-source connectors. Use either --vault or --config."),
    company_map: Path = typer.Option(Path("./configs/company_map.yaml"), help="Company-to-industry-node mapping YAML."),
    financials: Path | None = typer.Option(None, help="Optional financial snapshot CSV."),
    prices: Path | None = typer.Option(None, help="Optional price snapshot CSV."),
    out: Path = typer.Option(Path("./outputs/investment_candidates.json"), help="Candidate JSON output path."),
    query: str = typer.Option(DEFAULT_QUERY, help="Scan query."),
    top_k_chunks: int = typer.Option(120, help="Top chunks to analyze."),
    limit: int = typer.Option(500, help="Maximum source documents to fetch when using --config."),
    top_k: int = typer.Option(20, help="Maximum candidates to output."),
    data_dir: Path = typer.Option(Path("./data"), help="Research store directory when --persist is enabled."),
    persist: bool = typer.Option(False, "--persist/--no-persist", help="Persist intermediate documents/chunks/events/signals."),
) -> None:
    """Build a company-level investment candidate pool."""
    if vault is None and config is None:
        console.print("[red]Provide either --vault or --config.[/red]")
        raise typer.Exit(2)

    if config is not None:
        signals, _, _, errors = run_scan_from_sources(
            config,
            query=query,
            top_k_chunks=top_k_chunks,
            limit=limit,
            data_dir=data_dir,
            persist=persist,
        )
        for err in errors:
            console.print(f"[yellow]Source warning:[/yellow] {err}")
    else:
        signals, _, _ = run_scan(
            vault,
            query=query,
            top_k_chunks=top_k_chunks,
            data_dir=data_dir,
            persist=persist,
        )

    profiles = load_company_profiles(company_map)
    if not profiles:
        console.print(f"[red]No company profiles loaded from {company_map}.[/red]")
        raise typer.Exit(1)

    candidate_rows = build_investment_candidates(
        signals,
        profiles,
        financials=load_financial_snapshots(financials),
        prices=load_price_snapshots(prices),
        top_k=top_k,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([item.to_dict() for item in candidate_rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _print_candidate_table(candidate_rows)
    console.print(f"Candidate pool saved: [bold]{out}[/bold]")


@app.command("company-search")
def company_search(
    query: str = typer.Option(..., help="Company, ticker, industry, or supply-chain node to search."),
    company_map: Path = typer.Option(Path("./configs/company_map.yaml"), help="Company mapping YAML."),
    top_k: int = typer.Option(20, help="Maximum results to show."),
) -> None:
    """Search the local company universe."""
    profiles = load_company_profiles(company_map)
    results = search_company_profiles(profiles, query, top_k=top_k)

    table = Table(title=f"Company search: {query}")
    table.add_column("Rank", justify="right")
    table.add_column("Match", justify="right")
    table.add_column("Company")
    table.add_column("Ticker")
    table.add_column("Industry")
    table.add_column("Nodes")
    table.add_column("Role")

    for i, (score, profile) in enumerate(results, 1):
        table.add_row(
            str(i),
            f"{score:.2f}",
            profile.name,
            profile.ticker or "",
            profile.industry or "",
            ", ".join(profile.nodes[:4]),
            profile.role[:80],
        )
    console.print(table)


@app.command("topic-research")
def topic_research(
    topic: str = typer.Option(..., help="Company or sector topic, e.g. 光模块 or 英伟达."),
    config: Path = typer.Option(Path("./configs/sources_full.yaml"), help="YAML config for data sources."),
    company_map: Path = typer.Option(Path("./configs/company_map.yaml"), help="Company mapping YAML."),
    financials: Path | None = typer.Option(None, help="Optional financial snapshot CSV."),
    prices: Path | None = typer.Option(None, help="Optional price snapshot CSV."),
    out_dir: Path = typer.Option(Path("./data/runs"), help="Output directory."),
    limit: int = typer.Option(80, help="Maximum source documents to fetch."),
    top_k_chunks: int = typer.Option(60, help="Top chunks to analyze."),
    retrieval_backend: str = typer.Option("auto", help="Retrieval backend: auto, hybrid, or llamaindex."),
    llm_summary: bool = typer.Option(False, help="Generate final LLM summary."),
) -> None:
    """Research one company or sector topic."""
    report = build_topic_research_report(
        topic=topic,
        config=config,
        company_map=company_map,
        financials=financials,
        prices=prices,
        limit=limit,
        top_k_chunks=top_k_chunks,
        retrieval_backend=retrieval_backend,
    )
    run_dir = out_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    report_json = run_dir / "topic_research.json"
    report_md = run_dir / "topic_research.md"
    report_html = run_dir / "topic_research.html"
    report_professional_md = run_dir / "topic_professional_report.md"
    report_professional_html = run_dir / "topic_professional_report.html"
    report_markdown = render_topic_research_markdown(report)
    report_professional_markdown = render_professional_topic_report(report)
    report_json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(report_markdown, encoding="utf-8")
    report_html.write_text(render_markdown_html(report_markdown, title=f"{topic} Topic Research"), encoding="utf-8")
    report_professional_md.write_text(report_professional_markdown, encoding="utf-8")
    report_professional_html.write_text(
        render_markdown_html(report_professional_markdown, title=f"{topic} Professional Report"),
        encoding="utf-8",
    )

    console.print(Panel.fit(f"Topic: [bold]{topic}[/bold]"))
    console.print(f"Report JSON: [bold]{report_json}[/bold]")
    console.print(f"Report Markdown: [bold]{report_md}[/bold]")
    console.print(f"Report HTML: [bold]{report_html}[/bold]")
    console.print(f"Professional Markdown: [bold]{report_professional_md}[/bold]")
    console.print(f"Professional HTML: [bold]{report_professional_html}[/bold]")
    _print_candidate_table(report.candidates[:10])

    if llm_summary:
        try:
            reply = generate_topic_reply(report.to_dict())
        except Exception as exc:
            console.print(f"[red]LLM summary failed:[/red] {type(exc).__name__}: {exc}")
            raise typer.Exit(1) from exc
        reply_json = run_dir / "llm_topic_reply.json"
        reply_md = run_dir / "llm_topic_reply.md"
        reply_html = run_dir / "llm_topic_reply.html"
        reply_professional_md = run_dir / "llm_professional_report.md"
        reply_professional_html = run_dir / "llm_professional_report.html"
        reply_markdown = render_topic_reply(reply)
        reply_professional_markdown = generate_professional_topic_markdown(report.to_dict())
        reply_json.write_text(json.dumps(reply, ensure_ascii=False, indent=2), encoding="utf-8")
        reply_md.write_text(reply_markdown, encoding="utf-8")
        reply_html.write_text(render_markdown_html(reply_markdown, title=f"{topic} LLM Reply"), encoding="utf-8")
        reply_professional_md.write_text(reply_professional_markdown, encoding="utf-8")
        reply_professional_html.write_text(
            render_markdown_html(reply_professional_markdown, title=f"{topic} LLM Professional Report"),
            encoding="utf-8",
        )
        console.print(f"LLM JSON: [bold]{reply_json}[/bold]")
        console.print(f"LLM Markdown: [bold]{reply_md}[/bold]")
        console.print(f"LLM HTML: [bold]{reply_html}[/bold]")
        console.print(f"LLM Professional Markdown: [bold]{reply_professional_md}[/bold]")
        console.print(f"LLM Professional HTML: [bold]{reply_professional_html}[/bold]")


@app.command("analyze-announcement")
def analyze_announcement(
    path: Path = typer.Option(..., help="Announcement/news text file to analyze."),
    title: str = typer.Option("", help="Announcement title. Defaults to file stem."),
    company: str = typer.Option("", help="Optional company name hint."),
    out: Path | None = typer.Option(None, help="Optional JSON output path."),
) -> None:
    """Use an OpenAI-compatible LLM to deeply analyze one announcement."""
    content = path.read_text(encoding="utf-8")
    insight = analyze_announcement_with_llm(title or path.stem, content, company or None)
    data = insight.__dict__
    console.print(Panel.fit(f"{insight.company or company or 'Unknown'} | {insight.event_type} | {insight.sentiment}"))
    console.print(insight.summary)
    if insight.catalysts:
        console.print("[bold]Catalysts[/bold]")
        for item in insight.catalysts:
            console.print(f"- {item}")
    if insight.risks:
        console.print("[bold]Risks[/bold]")
        for item in insight.risks:
            console.print(f"- {item}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"Insight saved: [bold]{out}[/bold]")


watch_app = typer.Typer(help="Manage investment watchlist and reviews.")
app.add_typer(watch_app, name="watchlist")


@watch_app.command("add")
def watchlist_add(
    company: str = typer.Option(..., help="Company name."),
    ticker: str = typer.Option("", help="Ticker code."),
    thesis: str = typer.Option("", help="Investment thesis."),
    status: str = typer.Option("watch", help="watch|holding|rejected|closed."),
    score: float | None = typer.Option(None, help="Optional candidate score."),
    tags: str = typer.Option("", help="Comma-separated tags."),
    data_dir: Path = typer.Option(Path("./data/watchlist"), help="Watchlist storage directory."),
) -> None:
    store = WatchlistStore(data_dir)
    item = store.add_or_update(
        WatchItem(
            company=company,
            ticker=ticker or None,
            thesis=thesis,
            status=status,
            score=score,
            tags=[x.strip() for x in tags.split(",") if x.strip()],
        )
    )
    console.print(f"Watchlist updated: [bold]{item.company}[/bold]")


@watch_app.command("list")
def watchlist_list(
    data_dir: Path = typer.Option(Path("./data/watchlist"), help="Watchlist storage directory."),
) -> None:
    store = WatchlistStore(data_dir)
    table = Table(title="Watchlist")
    table.add_column("Company")
    table.add_column("Ticker")
    table.add_column("Status")
    table.add_column("Score", justify="right")
    table.add_column("Thesis")
    for item in store.list_items():
        table.add_row(item.company, item.ticker or "", item.status, "" if item.score is None else f"{item.score:.2f}", item.thesis[:100])
    console.print(table)


@watch_app.command("review")
def watchlist_review(
    company: str = typer.Option(..., help="Company name."),
    outcome: str = typer.Option(..., help="validated|invalidated|pending|mixed."),
    notes: str = typer.Option("", help="Review notes."),
    score_after: float | None = typer.Option(None, help="Optional score after review."),
    date: str = typer.Option("", help="Review date. Defaults to today."),
    data_dir: Path = typer.Option(Path("./data/watchlist"), help="Watchlist storage directory."),
) -> None:
    from datetime import datetime

    store = WatchlistStore(data_dir)
    review = store.add_review(
        ReviewItem(
            company=company,
            date=date or datetime.now().strftime("%Y-%m-%d"),
            outcome=outcome,
            notes=notes,
            score_after=score_after,
        )
    )
    console.print(f"Review recorded: [bold]{review.company}[/bold] {review.date} {review.outcome}")


if __name__ == "__main__":
    app()
