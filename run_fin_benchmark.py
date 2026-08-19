from __future__ import annotations

import argparse
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from chainalpha.config import load_settings
from chainalpha.evaluation import FinGAIAEvaluator, FinResearchBenchEvaluator, load_predictions, load_tasks
from chainalpha.nlp.llm_client import LLMClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate ChainAlpha outputs with financial benchmarks.")
    subparsers = parser.add_subparsers(dest="benchmark", required=True)

    research = subparsers.add_parser("finresearchbench", help="Evaluate long-form financial research reports.")
    _common_args(research)
    research.add_argument("--rule-only", action="store_true", help="Skip LLM judge and output four rule metrics only.")

    gaia = subparsers.add_parser("fingaia", help="Evaluate question-answering tasks.")
    _common_args(gaia)
    gaia.add_argument("--semantic-judge", action="store_true", help="Use an LLM fallback for free-form equivalent answers.")
    gaia.add_argument("--numeric-tolerance", type=float, default=1e-4)
    return parser.parse_args()


def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, required=True, help="Official or compatible JSON/JSONL task file.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--predictions", type=Path, help="Prediction JSON/JSONL or a directory of <task_id>.md files.")
    group.add_argument("--single-report", type=Path, help="Evaluate one report; task file must contain one task.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Smoke-test only the first N tasks.")


def main() -> None:
    args = parse_args()
    tasks = load_tasks(args.tasks)
    if args.limit is not None:
        tasks = tasks[:max(0, args.limit)]
    if args.single_report:
        if len(tasks) != 1:
            raise ValueError("--single-report requires exactly one task after applying --limit.")
        predictions = {tasks[0].task_id: args.single_report.read_text(encoding="utf-8")}
    else:
        predictions = load_predictions(args.predictions)

    output_dir = args.output_dir or Path("data/benchmark_runs") / f"{args.benchmark}_{datetime.now():%Y%m%d_%H%M%S}"
    llm_client = None
    needs_llm = (args.benchmark == "finresearchbench" and not args.rule_only) or getattr(args, "semantic_judge", False)
    if needs_llm:
        settings = _benchmark_judge_settings()
        if not settings.llm_enabled:
            if args.benchmark == "finresearchbench":
                print("Judge API key is not configured; falling back to rule-only evaluation.", flush=True)
            else:
                raise RuntimeError("A judge API key is required by --semantic-judge.")
        else:
            llm_client = LLMClient(settings)
            print(f"Judge: {settings.llm_provider} | model={settings.llm_model}", flush=True)

    if args.benchmark == "finresearchbench":
        summary, _rows = FinResearchBenchEvaluator(llm_client).evaluate(tasks, predictions, str(output_dir))
        metric = summary.get("overall_score")
        print(f"FinResearchBench compatible score: {metric if metric is not None else 'rule-only'}", flush=True)
    else:
        summary, _rows = FinGAIAEvaluator(
            llm_client,
            semantic_judge=args.semantic_judge,
            numeric_tolerance=args.numeric_tolerance,
        ).evaluate(tasks, predictions, str(output_dir))
        print(f"FinGAIA accuracy: {summary['accuracy']:.4f}", flush=True)
    print(f"Results: {output_dir}", flush=True)


def _benchmark_judge_settings():
    settings = load_settings()
    provider = (os.getenv("BENCHMARK_JUDGE_PROVIDER") or "").strip().lower()
    model = (os.getenv("BENCHMARK_JUDGE_MODEL") or "").strip()
    base_url = (os.getenv("BENCHMARK_JUDGE_BASE_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("BENCHMARK_JUDGE_API_KEY") or "").strip()
    if not any((provider, model, base_url, api_key)):
        return settings
    provider = provider or settings.llm_provider
    if provider == "deepseek":
        return replace(
            settings,
            llm_provider="deepseek",
            deepseek_api_key=api_key or settings.deepseek_api_key,
            deepseek_base_url=base_url or settings.deepseek_base_url,
            deepseek_model=model or settings.deepseek_model,
        )
    return replace(
        settings,
        llm_provider="openai",
        openai_api_key=api_key or settings.openai_api_key,
        openai_base_url=base_url or settings.openai_base_url,
        openai_model=model or settings.openai_model,
    )


if __name__ == "__main__":
    main()
