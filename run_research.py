from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from chainalpha.connectors.base import FetchRequest
from chainalpha.connectors.registry import SourceManager
from chainalpha.investment.candidates import build_investment_candidates
from chainalpha.investment.company_map import load_company_profiles
from chainalpha.investment.llm_reply import generate_investment_reply, render_investment_reply
from chainalpha.investment.market_data import load_financial_snapshots, load_price_snapshots
from chainalpha.pipeline import DEFAULT_QUERY, run_scan_from_sources


DEFAULT_CONFIG = Path("./configs/sources_full.yaml")
DEFAULT_COMPANY_MAP = Path("./configs/company_map.yaml")
DEFAULT_FINANCIALS = None
DEFAULT_PRICES = None
DEFAULT_RUN_DIR = Path("./data/runs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ChainAlpha one-click industry-chain and financial research pipeline.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Data-source connector YAML.")
    parser.add_argument("--company-map", type=Path, default=DEFAULT_COMPANY_MAP, help="Company mapping YAML.")
    parser.add_argument("--financials", type=Path, default=DEFAULT_FINANCIALS, help="Optional financial snapshot CSV. Leave empty to avoid sample data.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES, help="Optional price snapshot CSV. Leave empty to avoid sample data.")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Research query.")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR, help="Output run directory.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum source documents to fetch.")
    parser.add_argument("--preview-limit", type=int, default=10, help="Preview source documents.")
    parser.add_argument("--top-k-chunks", type=int, default=120, help="Top chunks to analyze.")
    parser.add_argument("--top-k-candidates", type=int, default=20, help="Maximum candidates to output.")
    parser.add_argument("--persist", action="store_true", help="Persist intermediate documents/chunks/events/signals.")
    parser.add_argument("--skip-preview", action="store_true", help="Skip source preview.")
    parser.add_argument("--llm-summary", action="store_true", help="Use an OpenAI-compatible LLM to write final reply.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.run_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print("== ChainAlpha research pipeline ==")
    print(f"Run dir: {run_dir}")
    print(f"Query: {args.query}")
    print(f"Sources: {args.config}")
    print(f"Company map: {args.company_map}")
    print(f"Financials: {args.financials or 'None'}")
    print(f"Prices: {args.prices or 'None'}")
    print()

    if not args.skip_preview:
        preview_sources(args.config, args.query, args.preview_limit)

    print("== 1/2 Mine industry-chain weak signals ==")
    signals, markdown, report_path, errors = run_scan_from_sources(
        args.config,
        query=args.query,
        top_k_chunks=args.top_k_chunks,
        out_dir=run_dir,
        limit=args.limit,
        persist=args.persist,
    )
    for err in errors:
        print(f"[source warning] {err}")
    print(f"Signals detected: {len(signals)}")
    if report_path:
        print(f"Signal report: {report_path}")
    (run_dir / "signals.json").write_text(
        json.dumps([signal.to_dict() for signal in signals], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "signal_report.md").write_text(markdown, encoding="utf-8")
    print()

    print("== 2/2 Build company investment candidate pool ==")
    profiles = load_company_profiles(args.company_map)
    financials = load_financial_snapshots(args.financials)
    prices = load_price_snapshots(args.prices)
    candidates = build_investment_candidates(
        signals,
        profiles,
        financials=financials,
        prices=prices,
        top_k=args.top_k_candidates,
    )
    candidates_path = run_dir / "investment_candidates.json"
    candidates_path.write_text(
        json.dumps([candidate.to_dict() for candidate in candidates], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_path = run_dir / "investment_candidates.md"
    summary_path.write_text(render_candidate_markdown(candidates), encoding="utf-8")
    print(f"Candidates generated: {len(candidates)}")
    print(f"Candidate JSON: {candidates_path}")
    print(f"Candidate report: {summary_path}")

    print()
    print("Top candidates:")
    if not candidates:
        print("- No candidates matched. Check company_map nodes and source text encoding.")
    for idx, candidate in enumerate(candidates[:10], 1):
        ticker = candidate.company.ticker or "-"
        print(f"{idx}. {candidate.company.name} ({ticker}) score={candidate.score:.2f} rating={candidate.rating}")

    if args.llm_summary:
        print()
        print("== LLM final investment reply ==")
        try:
            reply = generate_investment_reply(args.query, signals, candidates)
        except Exception as exc:
            print(f"LLM summary failed: {type(exc).__name__}: {exc}")
            print("Check OPENAI_API_KEY, OPENAI_BASE_URL, and OPENAI_MODEL in your .env file.")
            return

        reply_json_path = run_dir / "llm_investment_reply.json"
        reply_md_path = run_dir / "llm_investment_reply.md"
        reply_json_path.write_text(json.dumps(reply, ensure_ascii=False, indent=2), encoding="utf-8")
        reply_md_path.write_text(render_investment_reply(reply), encoding="utf-8")
        print(f"LLM reply JSON: {reply_json_path}")
        print(f"LLM reply Markdown: {reply_md_path}")
        print()
        print(render_investment_reply(reply))


def preview_sources(config: Path, query: str, limit: int) -> None:
    print("== Preview source documents ==")
    manager = SourceManager.from_yaml(config)
    docs, results = manager.fetch_all(FetchRequest(query=query, limit=limit))
    for err in manager.errors:
        print(f"[config error] {err}")
    for result in results:
        for err in result.errors:
            print(f"[{result.source_name} error] {err}")

    print(f"Fetched preview docs: {len(docs)}")
    for idx, doc in enumerate(docs[:limit], 1):
        location = doc.url or doc.path or ""
        snippet = doc.content.replace("\n", " ")[:120]
        print(f"{idx}. [{doc.source_type}] {doc.title[:60]} | {location[:80]} | {snippet}")
    print()


def render_candidate_markdown(candidates: list) -> str:
    lines = [
        "# ChainAlpha Investment Candidates",
        "",
        f"> Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if not candidates:
        lines.extend([
            "No candidates matched.",
            "",
            "Check company mapping nodes, source text encoding, and whether the query selected relevant documents.",
        ])
        return "\n".join(lines)

    lines.extend([
        "| Rank | Company | Ticker | Score | Rating | Signals | Thesis |",
        "|---:|---|---|---:|---|---:|---|",
    ])
    for idx, candidate in enumerate(candidates, 1):
        company = candidate.company.name.replace("|", "/")
        ticker = (candidate.company.ticker or "").replace("|", "/")
        thesis = candidate.thesis.replace("|", "/")
        lines.append(
            f"| {idx} | {company} | {ticker} | {candidate.score:.2f} | "
            f"{candidate.rating} | {len(candidate.signals)} | {thesis} |"
        )

    lines.append("")
    lines.append("## Details")
    lines.append("")
    for idx, candidate in enumerate(candidates, 1):
        lines.append(f"### {idx}. {candidate.company.name}")
        lines.append("")
        lines.append(f"- Ticker: {candidate.company.ticker or '-'}")
        lines.append(f"- Score: {candidate.score:.2f}")
        lines.append(f"- Rating: {candidate.rating}")
        lines.append(f"- Thesis: {candidate.thesis}")
        if candidate.catalysts:
            lines.append("- Catalysts:")
            for item in candidate.catalysts[:5]:
                lines.append(f"  - {item}")
        if candidate.risks:
            lines.append("- Risks:")
            for item in candidate.risks[:5]:
                lines.append(f"  - {item}")
        if candidate.next_actions:
            lines.append("- Next actions:")
            for item in candidate.next_actions[:5]:
                lines.append(f"  - {item}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
