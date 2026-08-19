from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path

from dotenv import load_dotenv

from chainalpha.events.service import event_service
from chainalpha.five_good.screening import (
    FiveGoodScreener,
    ScreeningConfig,
    attach_market_events,
    persist_screening_result,
)
from chainalpha.five_good.tushare_provider import TushareScreeningProvider


PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screen the full A-share universe into independent Five Good pools.")
    parser.add_argument("--refresh-data", action="store_true", help="Ignore the normalized feature cache and call Tushare.")
    parser.add_argument("--cache", default="data/five_good/features.json")
    parser.add_argument("--store", default=os.getenv("FIVE_GOOD_STORE_PATH", "data/five_good/store.json"))
    parser.add_argument("--pool-size", type=int, default=int(os.getenv("FIVE_GOOD_POOL_SIZE", "80")))
    parser.add_argument("--min-score", type=float, default=float(os.getenv("FIVE_GOOD_MIN_SCORE", "60")))
    parser.add_argument(
        "--min-completeness",
        type=float,
        default=float(os.getenv("FIVE_GOOD_MIN_COMPLETENESS", "0.60")),
    )
    parser.add_argument("--without-events", action="store_true", help="Do not attach local MarketEvent evidence.")
    return parser.parse_args()


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    provider = TushareScreeningProvider(cache_path=args.cache)
    print("[five-good] collecting full A-share features...", flush=True)
    features = provider.collect(refresh=args.refresh_data)
    print(f"[five-good] normalized companies: {len(features)}", flush=True)
    if not args.without_events:
        _, events = event_service.refresh_if_changed()
        features = attach_market_events(features, events)
        print(f"[five-good] local market events: {len(events)}", flush=True)
    screener = FiveGoodScreener(ScreeningConfig(
        pool_size=max(1, args.pool_size),
        min_score=max(0, min(100, args.min_score)),
        min_completeness=max(0, min(1, args.min_completeness)),
    ))
    result = screener.screen(features, now=datetime.now(timezone.utc))
    result.warnings.extend(provider.warnings)
    persist_screening_result(result, args.store)
    print(f"[five-good] universe={result.universe_count} eligible={result.eligible_count}", flush=True)
    for category, count in result.category_counts.items():
        print(f"[five-good] {category}: {count}", flush=True)
    print(f"[five-good] store: {Path(args.store).resolve()}", flush=True)
    for warning in result.warnings:
        print(f"[five-good] warning: {warning}", flush=True)


if __name__ == "__main__":
    main()
