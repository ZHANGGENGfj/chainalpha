from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from chainalpha.reports.html_renderer import render_markdown_html


DEFAULT_MEDIACRAWLER_ROOT = Path(__file__).resolve().parent / "third_party" / "MediaCrawler-main"


@dataclass
class AgentDecision:
    question: str
    keywords: list[str]
    local_evidence_count: int
    should_collect: bool
    reasons: list[str] = field(default_factory=list)
    task_id: str = ""
    task_dir: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ChainAlpha agent entry for claim verification with MediaCrawler on-demand collection."
    )
    parser.add_argument("--question", required=True, help="User claim/question, e.g. 中际旭创上涨30%%了吗")
    parser.add_argument("--mediacrawler-root", type=Path, default=DEFAULT_MEDIACRAWLER_ROOT, help="MediaCrawler-main root directory.")
    parser.add_argument("--mediacrawler-python", default=sys.executable, help="Python executable used to run MediaCrawler scripts.")
    parser.add_argument("--days", type=int, default=30, help="Lookback days used by MediaCrawler verifier.")
    parser.add_argument("--top-k", type=int, default=10, help="Evidence count passed to verifier.")
    parser.add_argument("--min-local-evidence", type=int, default=3, help="Trigger targeted crawl when local evidence is below this count.")
    parser.add_argument("--force-collect", action="store_true", help="Always trigger MediaCrawler targeted collection.")
    parser.add_argument("--no-collect", action="store_true", help="Never trigger targeted collection.")
    parser.add_argument("--web", action="store_true", help="Let MediaCrawler verifier also inspect configured finance pages.")
    parser.add_argument("--sources", default="", help="Comma-separated MediaCrawler source keys for targeted collection.")
    parser.add_argument("--limit-per-source", type=int, default=80, help="Targeted collection limit per source.")
    parser.add_argument("--out-dir", type=Path, default=Path("./data/agent_verify_runs"), help="ChainAlpha output directory.")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    run_dir = args.out_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[agent] question: {args.question}")
    keywords = extract_keywords(args.question)
    print(f"[agent] extracted keywords: {', '.join(keywords)}")

    local_evidence = search_mediacrawler_local(args.mediacrawler_root, keywords)
    print(f"[agent] local evidence before collection: {len(local_evidence)}")

    decision = decide_collection(
        question=args.question,
        keywords=keywords,
        local_evidence_count=len(local_evidence),
        min_local_evidence=args.min_local_evidence,
        force_collect=args.force_collect,
        no_collect=args.no_collect,
    )

    task_processed_dir: Path | None = None
    collect_result: dict[str, Any] = {}
    if decision.should_collect:
        print(f"[agent] triggering targeted MediaCrawler collection: {', '.join(keywords)}")
        collect_result = run_mediacrawler_collect(args, decision, run_dir)
        task_dir_text = collect_result.get("Task dir") or collect_result.get("task_dir") or ""
        if task_dir_text:
            task_dir = Path(task_dir_text)
            if not task_dir.is_absolute():
                task_dir = args.mediacrawler_root / task_dir
            decision.task_dir = str(task_dir)
            task_processed_dir = task_dir
        print(f"[agent] targeted collection finished, processed={collect_result.get('Processed items', '-')}")
    else:
        print("[agent] skip targeted collection")

    print("[agent] running MediaCrawler claim verifier...")
    verify_result = run_mediacrawler_verify(args, task_processed_dir, run_dir)
    print(
        "[agent] verifier finished: "
        f"verdict={verify_result.get('Verdict', '-')}, "
        f"local={verify_result.get('Local evidence', '-')}, "
        f"web={verify_result.get('Web evidence', '-')}"
    )

    copied = copy_verify_outputs(verify_result, run_dir, args.mediacrawler_root)

    orchestration = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision": asdict(decision),
        "local_evidence_preview": local_evidence[:20],
        "collect_result": collect_result,
        "verify_result": verify_result,
        "copied_outputs": copied,
    }
    orchestration_json = run_dir / "agent_orchestration.json"
    orchestration_md = run_dir / "agent_orchestration.md"
    orchestration_html = run_dir / "agent_orchestration.html"
    orchestration_json.write_text(json.dumps(orchestration, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = render_orchestration_markdown(orchestration)
    orchestration_md.write_text(markdown, encoding="utf-8")
    orchestration_html.write_text(render_markdown_html(markdown, title=f"Agent Verify - {args.question}"), encoding="utf-8")

    print(f"ChainAlpha run dir: {run_dir}")
    if copied:
        print("Copied verifier outputs:")
        for key, value in copied.items():
            print(f"- {key}: {value}")
    print(f"Orchestration JSON: {orchestration_json}")
    print(f"Orchestration Markdown: {orchestration_md}")
    print(f"Orchestration HTML: {orchestration_html}")


def decide_collection(
    *,
    question: str,
    keywords: list[str],
    local_evidence_count: int,
    min_local_evidence: int,
    force_collect: bool,
    no_collect: bool,
) -> AgentDecision:
    reasons: list[str] = []
    time_sensitive = any(word in question for word in ["最新", "今天", "刚刚", "传闻", "上涨", "下跌", "订单", "业绩"])
    if local_evidence_count < min_local_evidence:
        reasons.append(f"本地证据不足：{local_evidence_count} < {min_local_evidence}")
    if time_sensitive:
        reasons.append("问题具有时效性或需要定向核验")
    should = (local_evidence_count < min_local_evidence or time_sensitive or force_collect) and not no_collect
    if force_collect:
        reasons.append("用户指定 force_collect")
    if no_collect:
        reasons.append("用户指定 no_collect")
    return AgentDecision(
        question=question,
        keywords=keywords,
        local_evidence_count=local_evidence_count,
        should_collect=should,
        reasons=reasons,
        task_id=f"claim_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )


def run_mediacrawler_collect(args: argparse.Namespace, decision: AgentDecision, run_dir: Path) -> dict[str, Any]:
    script = args.mediacrawler_root / "run_claim_collect.py"
    if not script.exists():
        raise SystemExit(f"MediaCrawler collect script not found: {script}")
    command = [
        args.mediacrawler_python,
        str(script),
        "--keywords",
        ",".join(decision.keywords),
        "--task-id",
        decision.task_id,
        "--limit-per-source",
        str(args.limit_per_source),
        "--out-dir",
        str((run_dir / "mediacrawler_claim_tasks").resolve()),
    ]
    if args.sources:
        command.extend(["--sources", args.sources])
    result = subprocess.run(
        command,
        cwd=args.mediacrawler_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env(),
    )
    output = result.stdout + "\n" + result.stderr
    if result.returncode != 0:
        raise SystemExit(f"MediaCrawler targeted collection failed:\n{output}")
    parsed = parse_key_value_output(output)
    parsed["returncode"] = result.returncode
    parsed["stdout"] = result.stdout
    parsed["stderr"] = result.stderr
    parsed["task_id"] = decision.task_id
    return parsed


def run_mediacrawler_verify(args: argparse.Namespace, task_dir: Path | None, run_dir: Path) -> dict[str, Any]:
    script = args.mediacrawler_root / "run_claim_verify.py"
    if not script.exists():
        raise SystemExit(f"MediaCrawler verify script not found: {script}")
    command = [
        args.mediacrawler_python,
        str(script),
        "--question",
        args.question,
        "--days",
        str(args.days),
        "--top-k",
        str(args.top_k),
        "--out-dir",
        str((run_dir / "mediacrawler_claim_runs").resolve()),
    ]
    if args.web:
        command.append("--web")

    finance_processed = args.mediacrawler_root / "data" / "finance_news" / "processed"
    if finance_processed.exists():
        command.extend(["--local-dir", str(finance_processed)])
    if task_dir is not None and task_dir.exists():
        command.extend(["--local-dir", str(task_dir)])

    result = subprocess.run(
        command,
        cwd=args.mediacrawler_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env(),
    )
    output = result.stdout + "\n" + result.stderr
    if result.returncode != 0:
        raise SystemExit(f"MediaCrawler claim verification failed:\n{output}")
    parsed = parse_key_value_output(output)
    parsed["returncode"] = result.returncode
    parsed["stdout"] = result.stdout
    parsed["stderr"] = result.stderr
    return parsed


def copy_verify_outputs(verify_result: dict[str, Any], run_dir: Path, mediacrawler_root: Path) -> dict[str, str]:
    copied: dict[str, str] = {}
    for key in ["JSON", "Markdown", "HTML"]:
        path_text = verify_result.get(key)
        if not path_text:
            continue
        src = Path(path_text)
        if not src.is_absolute():
            src = mediacrawler_root / src
        if not src.exists():
            continue
        dst = run_dir / f"claim_verification{src.suffix}"
        shutil.copy2(src, dst)
        copied[key.lower()] = str(dst)
    return copied


def search_mediacrawler_local(root: Path, keywords: list[str]) -> list[dict[str, Any]]:
    data_roots = [
        root / "data" / "finance_news" / "processed",
        root / "data" / "claim_tasks",
    ]
    rows: list[dict[str, Any]] = []
    for data_root in data_roots:
        if not data_root.exists():
            continue
        for path in data_root.rglob("*.json*"):
            rows.extend(load_records(path))
    out: list[dict[str, Any]] = []
    for row in rows:
        text = " ".join(str(row.get(key) or "") for key in ["title", "标题", "content", "内容", "summary", "source", "来源", "url", "链接"])
        matched = [keyword for keyword in keywords if keyword and keyword in text]
        if not matched:
            continue
        item = dict(row)
        item["_matched_keywords"] = matched
        out.append(item)
    return out


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        if path.suffix.lower() == ".jsonl":
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    if line.strip():
                        value = json.loads(line)
                        if isinstance(value, dict):
                            value["_path"] = str(path)
                            records.append(value)
            return records
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        for value in data:
            if isinstance(value, dict):
                value["_path"] = str(path)
                records.append(value)
    return records


def extract_keywords(question: str) -> list[str]:
    terms = re.findall(r"[\u4e00-\u9fffA-Za-z0-9.]{2,}", question)
    stop = {"了吗", "是否", "真的", "有没有", "多少", "为什么"}
    terms = [term for term in terms if term not in stop]
    company = extract_company(question)
    if company and company not in terms:
        terms.insert(0, company)
    code = extract_code(question)
    if code and code not in terms:
        terms.append(code)
    if "上涨" in question:
        terms.extend(["股价", "上涨", "涨幅"])
    if "下跌" in question:
        terms.extend(["股价", "下跌", "跌幅"])
    if "业绩" in question or "净利润" in question:
        terms.extend(["业绩", "净利润"])
    return list(dict.fromkeys([term for term in terms if term]))


def extract_company(question: str) -> str:
    match = re.search(r"(?:sh|sz|bj)?(\d{6})", question, flags=re.I)
    if match:
        return match.group(1)
    cleaned = question
    for word in ["上涨", "下跌", "股价", "股票", "业绩", "净利润", "大增", "了吗", "是否", "真的"]:
        cleaned = cleaned.replace(word, " ")
    cleaned = re.sub(r"[,，。！？、?%百分之\d\s]+", " ", cleaned)
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z]{2,12}", cleaned)
    return candidates[0] if candidates else ""


def extract_code(question: str) -> str:
    match = re.search(r"(?:sh|sz|bj)?(\d{6})", question, flags=re.I)
    return match.group(1) if match else ""


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
    return env


def render_orchestration_markdown(data: dict[str, Any]) -> str:
    decision = data["decision"]
    lines = [
        "# ChainAlpha Agent Verify",
        "",
        f"- Question: {decision['question']}",
        f"- Keywords: {', '.join(decision['keywords'])}",
        f"- Local evidence before collection: {decision['local_evidence_count']}",
        f"- Triggered targeted collection: {decision['should_collect']}",
        f"- Task ID: {decision.get('task_id') or '-'}",
        f"- Task dir: {decision.get('task_dir') or '-'}",
        "",
        "## Reasons",
        "",
    ]
    for reason in decision.get("reasons") or []:
        lines.append(f"- {reason}")
    lines.extend(["", "## Outputs", ""])
    for key, value in (data.get("copied_outputs") or {}).items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## MediaCrawler Verify Summary", ""])
    verify = data.get("verify_result") or {}
    for key in ["Question", "Verdict", "Confidence", "Claim type", "Local evidence", "Web evidence", "JSON", "Markdown", "HTML"]:
        if key in verify:
            lines.append(f"- {key}: {verify[key]}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
