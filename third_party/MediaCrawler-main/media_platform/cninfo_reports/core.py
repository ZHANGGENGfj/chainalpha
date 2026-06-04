from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import BrowserContext, BrowserType

from base.base_crawler import AbstractCrawler
from tools import utils

from .client import CninfoReportsClient


class CninfoReportsCrawler(AbstractCrawler):
    def __init__(self) -> None:
        self.keyword_list: list[str] = []
        self.browser_context: BrowserContext | None = None
        self.browser_instance = None
        self.cdp_manager = None

    async def start(self):
        await self.search()

    async def search(self):
        keywords = self.keyword_list or []
        client = CninfoReportsClient()
        try:
            rows = client.fetch_reports(
                keywords=keywords,
                max_pages=2,
                page_size=30,
                download_pdf=True,
                pdf_dir="data/cninfo_reports/pdfs",
            )
        except Exception as exc:  # noqa: BLE001
            utils.logger.warning(f"[CninfoReports] fetch failed: {type(exc).__name__}: {exc}")
            rows = []

        print(f"[CninfoReports] fetched {len(rows)} financial-report announcements")
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
        raise RuntimeError("CninfoReportsCrawler does not use Playwright.")

    def preprocess_report_item(self, item: dict[str, Any]) -> dict[str, Any]:
        title = str(item.get("标题") or "")
        company = str(item.get("公司") or "")
        code = str(item.get("代码") or "")
        content = "\n".join(
            part
            for part in [
                f"公告标题：{title}",
                f"公司：{company} {code}".strip(),
                f"公告日期：{item.get('公告日期', '')}",
                f"公告类型：{item.get('公告类型', '')}",
                f"PDF：{item.get('链接', '')}",
                f"本地PDF：{item.get('pdf_path', '')}",
            ]
            if part
        )
        return {
            "id": f"cninfo_report_{abs(hash(str(item.get('链接', '')) + title))}",
            "source": "巨潮资讯",
            "source_type": "official_announcement",
            "title": title,
            "content": content,
            "url": item.get("链接", ""),
            "company": company,
            "ticker": code,
            "published_at": item.get("公告日期", ""),
            "pdf_path": item.get("pdf_path", ""),
            "report_type": _report_type(title),
            "match_type": item.get("匹配关键词", ""),
            "raw": item,
        }

    async def save_raw_data(self, data: list[dict[str, Any]]):
        day = datetime.now().strftime("%Y%m%d")
        save_dir = f"data/cninfo_reports/raw/{day}"
        os.makedirs(save_dir, exist_ok=True)
        file_path = os.path.join(save_dir, f"cninfo_reports_raw_{day}.json")
        existing = _load_json_list(file_path)
        merged = _merge_daily_items(existing, data)
        Path(file_path).write_text(json.dumps(merged, ensure_ascii=False, indent=4), encoding="utf-8")
        utils.logger.info(
            f">>> [CninfoReports] raw daily file saved to {file_path}; "
            f"old={len(existing)}, new={len(data)}, total={len(merged)}"
        )

    async def save_processed_data(self, data: list[dict[str, Any]]):
        day = datetime.now().strftime("%Y%m%d")
        save_dir = f"data/cninfo_reports/processed/{day}"
        os.makedirs(save_dir, exist_ok=True)
        json_path = os.path.join(save_dir, f"cninfo_reports_processed_{day}.json")
        jsonl_path = os.path.join(save_dir, f"cninfo_reports_processed_{day}.jsonl")
        existing = _load_json_list(json_path)
        merged = _merge_daily_items(existing, data)
        Path(json_path).write_text(json.dumps(merged, ensure_ascii=False, indent=4), encoding="utf-8")
        with open(jsonl_path, "w", encoding="utf-8") as file:
            for item in merged:
                file.write(json.dumps(item, ensure_ascii=False) + "\n")
        utils.logger.info(
            f">>> [CninfoReports] processed daily file saved to {json_path}; "
            f"old={len(existing)}, new={len(data)}, total={len(merged)}"
        )


def _report_type(title: str) -> str:
    if "年度报告" in title or "年报" in title:
        return "annual_report"
    if "半年度报告" in title or "半年报" in title:
        return "semi_annual_report"
    if "季度报告" in title or "一季报" in title or "三季报" in title:
        return "quarterly_report"
    return "financial_report"


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
        key = str(item.get("url") or item.get("链接") or item.get("pdf_path") or item.get("title") or item.get("标题") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged
