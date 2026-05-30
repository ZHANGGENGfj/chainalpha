from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from media_platform.finance_news.client import FinanceNewsClient
from media_platform.finance_news.core import FinanceNewsCrawler, deduplicate_news, merge_daily_news_items


SOURCE_ALIASES = {
    "eastmoney": "东方财富资讯",
    "10jqka": "同花顺财经",
    "stcn": "证券时报",
    "cs": "中国证券报",
    "cnstock": "上海证券报",
    "zqrb": "证券日报",
    "yicai": "第一财经",
    "21jingji": "21世纪经济报道",
    "nbd": "每日经济新闻",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one targeted finance-news collection task for claim verification.")
    parser.add_argument("--keywords", required=True, help="Comma-separated keywords, e.g. 中际旭创,300308,股价,上涨")
    parser.add_argument("--task-id", default="", help="Task id. Defaults to claim_YYYYMMDD_HHMMSS.")
    parser.add_argument("--sources", default="", help="Comma-separated source keys/names. Defaults to all finance news sources.")
    parser.add_argument("--limit-per-source", type=int, default=80, help="Max items per source page.")
    parser.add_argument("--out-dir", type=Path, default=Path("./data/claim_tasks"), help="Task output root directory.")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser in headless mode.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    task_id = args.task_id or f"claim_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    task_dir = args.out_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    keywords = [item.strip() for item in re.split(r"[,，]", args.keywords) if item.strip()]
    source_filters = [item.strip() for item in re.split(r"[,，]", args.sources) if item.strip()]

    task = {
        "task_id": task_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "keywords": keywords,
        "sources": source_filters or "all",
        "limit_per_source": args.limit_per_source,
    }
    (task_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")

    raw_news = asyncio.run(collect_targeted_news(keywords, source_filters, args.limit_per_source, args.headless))
    raw_news = filter_by_keywords(raw_news, keywords)
    raw_news = deduplicate_news(raw_news)

    crawler = FinanceNewsCrawler()
    crawler.keyword_list = keywords
    processed_news = crawler.preprocess_news(raw_news)

    raw_path = task_dir / "raw.json"
    processed_path = task_dir / "processed.json"
    processed_jsonl = task_dir / "processed.jsonl"

    raw_path.write_text(json.dumps(raw_news, ensure_ascii=False, indent=2), encoding="utf-8")
    processed_path.write_text(json.dumps(processed_news, ensure_ascii=False, indent=2), encoding="utf-8")
    with processed_jsonl.open("w", encoding="utf-8") as file:
        for item in processed_news:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    # Also maintain a daily claim task aggregate for easier local retrieval.
    day_dir = args.out_dir / datetime.now().strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    daily_raw = day_dir / "raw.json"
    daily_processed = day_dir / "processed.json"
    daily_raw.write_text(
        json.dumps(merge_daily_news_items(load_json_list(daily_raw), raw_news), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    daily_processed.write_text(
        json.dumps(merge_daily_news_items(load_json_list(daily_processed), processed_news), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Task ID: {task_id}")
    print(f"Keywords: {', '.join(keywords)}")
    print(f"Raw items: {len(raw_news)}")
    print(f"Processed items: {len(processed_news)}")
    print(f"Task dir: {task_dir}")
    print(f"Processed JSON: {processed_path}")


async def collect_targeted_news(
    keywords: list[str],
    source_filters: list[str],
    limit_per_source: int,
    headless: bool,
) -> list[dict[str, Any]]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )
        )
        try:
            client = FinanceNewsClient(context)
            sources = select_sources(source_filters)
            results: list[dict[str, Any]] = []
            for source in sources:
                rows = await client.fetch_source_news(source, limit=limit_per_source)
                results.extend(rows)
            return results
        finally:
            await context.close()
            await browser.close()


def select_sources(source_filters: list[str]) -> list[dict[str, Any]]:
    if not source_filters:
        return list(FinanceNewsClient.SOURCES)
    wanted = {SOURCE_ALIASES.get(item, item) for item in source_filters}
    selected = []
    for source in FinanceNewsClient.SOURCES:
        if source["name"] in wanted or any(item in source["name"] for item in wanted):
            selected.append(source)
    return selected or list(FinanceNewsClient.SOURCES)


def filter_by_keywords(rows: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    if not keywords:
        return rows
    out = []
    for row in rows:
        text = " ".join(str(row.get(key) or "") for key in ["标题", "内容", "来源", "链接"])
        matched = [keyword for keyword in keywords if keyword and keyword in text]
        if not matched:
            continue
        item = dict(row)
        item["匹配关键词"] = ",".join(matched)
        out.append(item)
    return out


def load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


if __name__ == "__main__":
    main()
