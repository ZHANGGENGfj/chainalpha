from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import BrowserContext, BrowserType

from base.base_crawler import AbstractCrawler
from tools import utils

from .client import BrokerReportsClient


class BrokerReportsCrawler(AbstractCrawler):
    def __init__(self) -> None:
        self.keyword_list: list[str] = []
        self.browser_context: BrowserContext | None = None
        self.browser_instance = None
        self.cdp_manager = None

    async def start(self):
        await self.search()

    async def search(self):
        client = BrokerReportsClient()
        try:
            rows = client.fetch_reports(keywords=self.keyword_list or [], page_size=50)
        except Exception as exc:  # noqa: BLE001
            utils.logger.warning(f"[BrokerReports] fetch failed: {type(exc).__name__}: {exc}")
            rows = []

        print(f"[BrokerReports] fetched {len(rows)} broker research reports")
        if not rows:
            return
        await self.save_raw_data(rows)
        processed = [self.preprocess_report_item(row) for row in rows]
        await self.save_processed_data(processed)

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        raise RuntimeError("BrokerReportsCrawler does not use Playwright.")

    def preprocess_report_item(self, item: dict[str, Any]) -> dict[str, Any]:
        title = str(item.get("标题") or "")
        company = str(item.get("公司") or "")
        code = str(item.get("代码") or "")
        content = "\n".join(
            part
            for part in [
                f"研报标题：{title}",
                f"公司：{company} {code}".strip(),
                f"机构：{item.get('机构', '')}",
                f"作者/评级信息：{item.get('作者', '')} {item.get('评级', '')}".strip(),
                f"发布日期：{item.get('发布日期', '')}",
                f"链接：{item.get('链接', '')}",
            ]
            if part
        )
        return {
            "id": f"broker_report_{abs(hash(str(item.get('链接', '')) + title))}",
            "source": item.get("来源", "机构研报"),
            "source_type": "broker_research_report",
            "title": title,
            "content": content,
            "url": item.get("链接", ""),
            "company": company,
            "ticker": code,
            "org": item.get("机构", ""),
            "rating": item.get("评级", ""),
            "published_at": item.get("发布日期", ""),
            "match_type": item.get("匹配关键词", ""),
            "raw": item,
        }

    async def save_raw_data(self, data: list[dict[str, Any]]):
        save_dir = "data/broker_reports/raw"
        os.makedirs(save_dir, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        file_path = os.path.join(save_dir, f"broker_reports_raw_{day}.json")
        merged = _merge_daily_items(_load_json_list(file_path), data)
        Path(file_path).write_text(json.dumps(merged, ensure_ascii=False, indent=4), encoding="utf-8")
        utils.logger.info(f">>> [BrokerReports] raw daily file saved to {file_path}; total={len(merged)}")

    async def save_processed_data(self, data: list[dict[str, Any]]):
        save_dir = "data/broker_reports/processed"
        os.makedirs(save_dir, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        json_path = os.path.join(save_dir, f"broker_reports_processed_{day}.json")
        jsonl_path = os.path.join(save_dir, f"broker_reports_processed_{day}.jsonl")
        merged = _merge_daily_items(_load_json_list(json_path), data)
        Path(json_path).write_text(json.dumps(merged, ensure_ascii=False, indent=4), encoding="utf-8")
        with open(jsonl_path, "w", encoding="utf-8") as file:
            for item in merged:
                file.write(json.dumps(item, ensure_ascii=False) + "\n")
        utils.logger.info(f">>> [BrokerReports] processed daily file saved to {json_path}; total={len(merged)}")


def _load_json_list(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _merge_daily_items(existing: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*existing, *new_items]:
        key = str(item.get("url") or item.get("链接") or item.get("title") or item.get("标题") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged
