from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path

from tools.claim_verifier import render_verification_markdown, verify_claim


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify precise investment/news claims with local and optional web evidence.")
    parser.add_argument("--question", required=True, help="Claim/question to verify, e.g. 中际旭创股价上涨30%%了吗")
    parser.add_argument("--days", type=int, default=30, help="Local news lookback window.")
    parser.add_argument(
        "--local-dir",
        action="append",
        default=[],
        help="Local news directory. Can be passed multiple times. Defaults include MediaCrawler finance_news data.",
    )
    parser.add_argument("--web", action="store_true", help="Also search configured websites.")
    parser.add_argument(
        "--site",
        action="append",
        default=[],
        help="Web site key or URL template containing {query}. Built-in keys: eastmoney, 10jqka, stcn, cs, cnstock, zqrb, yicai, 21jingji, nbd.",
    )
    parser.add_argument("--top-k", type=int, default=20, help="Maximum evidence items per source layer.")
    parser.add_argument("--out-dir", type=Path, default=Path("./data/claim_runs"), help="Output directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.out_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    report = verify_claim(
        args.question,
        days=args.days,
        local_dirs=args.local_dir or None,
        web_sites=args.site or None,
        use_web=args.web,
        top_k=args.top_k,
    )
    markdown = render_verification_markdown(report)

    json_path = run_dir / "claim_verification.json"
    md_path = run_dir / "claim_verification.md"
    html_path = run_dir / "claim_verification.html"

    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(render_markdown_html(markdown, title=f"Claim Verification - {args.question}"), encoding="utf-8")

    print(f"Question: {args.question}")
    print(f"Verdict: {report.verdict}")
    print(f"Confidence: {report.confidence:.2f}")
    print(f"Claim type: {report.claim.claim_type}")
    print(f"Local evidence: {len(report.local_evidence)}")
    print(f"Web evidence: {len(report.web_evidence)}")
    if report.numeric_checks:
        for check in report.numeric_checks:
            print(
                f"Numeric check: {check.check_type} status={check.status} "
                f"actual={check.actual_pct} threshold={check.threshold_pct}"
            )
    if report.warnings:
        print("Warnings:")
        for warning in report.warnings[:20]:
            print(f"- {warning}")
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    print(f"HTML: {html_path}")


def render_markdown_html(markdown: str, *, title: str) -> str:
    body = []
    in_list = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line:
            if in_list:
                body.append("</ul>")
                in_list = False
            continue
        if line.startswith("# "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{inline_markdown(line[2:])}</li>")
        else:
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<p>{inline_markdown(line)}</p>")
    if in_list:
        body.append("</ul>")
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 32px auto; max-width: 1100px; line-height: 1.65; color: #1f2937; }}
h1, h2, h3 {{ color: #111827; }}
code {{ background: #f3f4f6; padding: 2px 5px; border-radius: 4px; }}
li {{ margin: 6px 0; }}
a {{ color: #2563eb; }}
</style>
</head>
<body>
{body}
</body>
</html>""".format(title=html.escape(title), body="\n".join(body))


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"(https?://[^\s<]+)", r"<a href=\"\1\">\1</a>", escaped)
    return escaped


if __name__ == "__main__":
    main()
