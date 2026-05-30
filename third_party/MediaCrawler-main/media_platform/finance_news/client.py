from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List

from playwright.async_api import BrowserContext
from tools import utils


class FinanceNewsClient:
    """Generic finance news client for mainstream Chinese finance media."""

    SOURCES: list[dict[str, Any]] = [
        {"name": "东方财富资讯", "url": "https://finance.eastmoney.com/", "selectors": ["a[href*='/a/']", "a[href*='eastmoney.com/a/']"]},
        {"name": "同花顺财经", "url": "https://news.10jqka.com.cn/", "selectors": ["a[href*='10jqka.com.cn']", ".list-con a", ".arc-title a"]},
        {"name": "证券时报", "url": "https://www.stcn.com/", "selectors": ["a[href*='stcn.com']", ".news a", ".list a"]},
        {"name": "中国证券报", "url": "https://www.cs.com.cn/", "selectors": ["a[href*='cs.com.cn']", ".list a", ".news a"]},
        {"name": "上海证券报", "url": "https://www.cnstock.com/", "selectors": ["a[href*='cnstock.com']", ".list a", ".news a"]},
        {"name": "证券日报", "url": "http://www.zqrb.cn/", "selectors": ["a[href*='zqrb.cn']", ".list a", ".news a"]},
        {"name": "第一财经", "url": "https://www.yicai.com/", "selectors": ["a[href*='yicai.com']", ".m-list a", ".news a"]},
        {"name": "21世纪经济报道", "url": "https://www.21jingji.com/", "selectors": ["a[href*='21jingji.com']", ".list a", ".news a"]},
        {"name": "每日经济新闻", "url": "https://www.nbd.com.cn/", "selectors": ["a[href*='nbd.com.cn']", ".news_item a", ".list a"]},
    ]

    def __init__(self, browser_context: BrowserContext):
        self.browser_context = browser_context

    async def fetch_all_news(self, *, limit_per_source: int = 80) -> List[Dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for source in self.SOURCES:
            try:
                rows = await self.fetch_source_news(source, limit=limit_per_source)
                utils.logger.info(f"[FinanceNews] {source['name']} fetched {len(rows)} items")
                results.extend(rows)
            except Exception as exc:
                utils.logger.warning(f"[FinanceNews] {source['name']} fetch failed: {type(exc).__name__}: {exc}")
            await asyncio.sleep(1.5)
        return results

    async def fetch_source_news(self, source: dict[str, Any], *, limit: int) -> List[Dict[str, Any]]:
        page = await self.browser_context.new_page()
        try:
            await page.goto(source["url"], wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2500)
            await self._auto_scroll(page, rounds=2)

            items: list[dict[str, Any]] = []
            seen: set[str] = set()
            for selector in source.get("selectors", []):
                links = await page.query_selector_all(selector)
                for link in links:
                    if len(items) >= limit:
                        break
                    text = self._clean(await link.inner_text())
                    href = await link.get_attribute("href")
                    url = self._absolute_url(source["url"], href)
                    if not self._is_valid_title(text) or not url:
                        continue
                    key = f"{source['name']}|{text}|{url}"
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({"来源": source["name"], "标题": text, "链接": url})
                if len(items) >= limit:
                    break

            if not items:
                body_text = self._clean(await page.locator("body").inner_text(timeout=5000))
                items = self._fallback_split_body(source, body_text, limit=limit)
            return items[:limit]
        finally:
            await page.close()

    async def _auto_scroll(self, page, *, rounds: int) -> None:
        for _ in range(rounds):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(800)

    def _fallback_split_body(self, source: dict[str, Any], text: str, *, limit: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in re.split(r"[\r\n]+", text):
            title = self._clean(line)
            if self._is_valid_title(title):
                rows.append({"来源": source["name"], "标题": title, "链接": source["url"]})
            if len(rows) >= limit:
                break
        return rows

    def _absolute_url(self, base_url: str, href: str | None) -> str:
        if not href:
            return ""
        href = href.strip()
        if href.startswith("//"):
            return "https:" + href
        if href.startswith(("http://", "https://")):
            return href
        if href.startswith("/"):
            match = re.match(r"^(https?://[^/]+)", base_url)
            return (match.group(1) if match else base_url.rstrip("/")) + href
        return base_url.rstrip("/") + "/" + href

    def _is_valid_title(self, text: str) -> bool:
        if not text or len(text) < 8 or len(text) > 180:
            return False
        bad_words = ["登录", "注册", "广告", "版权", "更多", "首页", "客户端", "app下载", "APP下载"]
        if any(word.lower() in text.lower() for word in bad_words):
            return False
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def _clean(self, text: str | None) -> str:
        if not text:
            return ""
        return re.sub(r"\s+", " ", str(text)).strip()

