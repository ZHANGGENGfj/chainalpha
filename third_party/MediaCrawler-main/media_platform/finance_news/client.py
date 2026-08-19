from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List
from urllib.parse import quote, urlparse

from playwright.async_api import BrowserContext
from tools import utils


class FinanceNewsClient:
    """Generic finance news client for mainstream Chinese finance media."""

    SOURCES: list[dict[str, Any]] = [
        {
            "name": "东方财富资讯",
            "url": "https://finance.eastmoney.com/",
            "search_urls": ["https://so.eastmoney.com/news/s?keyword={keyword}"],
            "selectors": ["a[href*='/a/']", "a[href*='eastmoney.com/a/']", "a[href*='finance.eastmoney.com']"],
        },
        {
            "name": "中国证券报",
            "url": "https://www.cs.com.cn/",
            "search_urls": [
                "https://www.cs.com.cn/searchlist.html?keyword={keyword}",
                "https://www.cs.com.cn/was5/web/search?searchword={keyword}",
            ],
            "selectors": ["a[href*='cs.com.cn']", ".list a", ".news a", ".search a"],
        },
        {
            "name": "上海证券报",
            "url": "https://www.cnstock.com/",
            "search_urls": [
                "https://www.cnstock.com/searchResult?word={keyword}",
                "https://search.cnstock.com/search?keyword={keyword}",
                "https://www.cnstock.com/commonSearch?searchword={keyword}",
            ],
            "selectors": ["a[href*='cnstock.com']", ".list a", ".news a"],
        },
        {
            "name": "证券日报",
            "url": "http://www.zqrb.cn/",
            "search_urls": [
                "http://search.zqrb.cn/search.php?src=all&q={keyword}&f=_all&s=newsdate_DESC",
                "http://search.zqrb.cn/search.php?keyword={keyword}",
            ],
            "selectors": ["a[href*='zqrb.cn']", ".list a", ".news a"],
        },
        {
            "name": "第一财经",
            "url": "https://www.yicai.com/",
            "search_urls": ["https://www.yicai.com/search?keys={keyword}"],
            "selectors": ["a[href*='yicai.com']", ".m-list a", ".news a", ".search-list a"],
        },
        {
            "name": "每日经济新闻",
            "url": "https://www.nbd.com.cn/",
            "search_urls": [
                "https://www.nbd.com.cn/search/article_search/?utf8=%E2%9C%93&q={keyword}",
                "https://www.nbd.com.cn/search/article_search?search_text={keyword}",
            ],
            "selectors": ["a[href*='nbd.com.cn']", ".news_item a", ".list a"],
        },
        {
            "name": "今日头条",
            "url": "https://www.toutiao.com/",
            "search_urls": [
                "https://so.toutiao.com/search?dvpf=pc&source=input&keyword={keyword}&enable_druid_v2=1",
            ],
            "selectors": [
                "a[href*='toutiao.com']",
                "a[href*='toutiao.com/article']",
                "a[href*='toutiao.com/w']",
                ".result-content a",
                ".search-result a",
                "h3 a",
            ],
        },
    ]

    def __init__(self, browser_context: BrowserContext):
        self.browser_context = browser_context

    async def fetch_all_news(self, *, limit_per_source: int = 80, keywords: list[str] | None = None) -> List[Dict[str, Any]]:
        keyword_list = [keyword.strip() for keyword in keywords or [] if keyword and keyword.strip()]
        semaphore = asyncio.Semaphore(3)

        async def fetch_one(source: dict[str, Any]) -> list[dict[str, Any]]:
            async with semaphore:
                try:
                    if keyword_list:
                        rows = await self.search_source_news(
                            source,
                            keywords=keyword_list,
                            limit=limit_per_source,
                        )
                    else:
                        rows = await self.fetch_source_news(source, limit=limit_per_source)
                    utils.logger.info(f"[FinanceNews] {source['name']} fetched {len(rows)} items")
                    return rows
                except Exception as exc:
                    utils.logger.warning(
                        f"[FinanceNews] {source['name']} fetch failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    return []
                finally:
                    await asyncio.sleep(1.5)

        batches = await asyncio.gather(*(fetch_one(source) for source in self.SOURCES))
        results: list[dict[str, Any]] = []
        for rows in batches:
            results.extend(rows)
        return results

    async def search_source_news(self, source: dict[str, Any], *, keywords: list[str], limit: int) -> List[Dict[str, Any]]:
        """Search a source by keyword before extracting links.

        This keeps finance_news targeted. We intentionally do not fall back to
        homepage crawling in keyword mode, because homepage crawling introduces
        broad market news unrelated to the user's topic.
        """
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        per_keyword_limit = max(5, limit // max(len(keywords), 1))

        for keyword in keywords:
            if len(items) >= limit:
                break
            rows: list[dict[str, Any]] = []
            for search_url, external_search in self._render_search_urls(source, keyword):
                if len(rows) >= per_keyword_limit:
                    break
                try:
                    utils.logger.info(
                        f"[FinanceNews] searching {source['name']} keyword={keyword} "
                        f"external={external_search}: {search_url}"
                    )
                    found = await self.fetch_source_news(
                        source,
                        limit=per_keyword_limit - len(rows),
                        url=search_url,
                        keyword=keyword,
                        fallback_to_body=not external_search,
                        enforce_domain=external_search,
                    )
                except Exception as exc:
                    utils.logger.warning(
                        f"[FinanceNews] {source['name']} keyword search failed "
                        f"({keyword}, external={external_search}): {type(exc).__name__}: {exc}"
                    )
                    continue
                rows.extend(found)
                if found and not external_search:
                    break
                await asyncio.sleep(0.5)
            for row in rows:
                title = self._clean(row.get("标题"))
                url = self._clean(row.get("链接"))
                key = f"{source['name']}|{title}|{url}"
                if key in seen:
                    continue
                if not self._matches_keyword(row, keyword):
                    continue
                row["匹配关键词"] = keyword
                seen.add(key)
                items.append(row)
                if len(items) >= limit:
                    break
            await asyncio.sleep(0.8)
        return items[:limit]

    async def fetch_source_news(
        self,
        source: dict[str, Any],
        *,
        limit: int,
        url: str | None = None,
        keyword: str | None = None,
        fallback_to_body: bool = True,
        enforce_domain: bool = False,
    ) -> List[Dict[str, Any]]:
        page = await self.browser_context.new_page()
        page_url = url or source["url"]
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2500)
            await self._auto_scroll(page, rounds=2)

            items: list[dict[str, Any]] = []
            seen: set[str] = set()
            selectors = list(source.get("selectors", []))
            if keyword:
                selectors.extend(["li.b_algo h2 a", "h3 a", "a"])
            for selector in selectors:
                links = await page.query_selector_all(selector)
                for link in links:
                    if len(items) >= limit:
                        break
                    text = self._clean(await link.inner_text())
                    if not text:
                        try:
                            text = self._clean(await link.evaluate("el => el.closest('div,li,article')?.innerText || ''"))
                        except Exception:
                            text = ""
                    href = await link.get_attribute("href")
                    link_url = self._absolute_url(page_url, href)
                    if not self._is_valid_title(text) or not link_url:
                        continue
                    if enforce_domain and not self._is_allowed_domain(link_url, source):
                        continue
                    row = {"来源": source["name"], "标题": text, "链接": link_url}
                    if keyword and not self._matches_keyword(row, keyword):
                        continue
                    key = f"{source['name']}|{text}|{link_url}"
                    if key in seen:
                        continue
                    seen.add(key)
                    if keyword:
                        row["匹配关键词"] = keyword
                    items.append(row)
                if len(items) >= limit:
                    break

            if not items and fallback_to_body:
                body_text = self._clean(await page.locator("body").inner_text(timeout=5000))
                items = self._fallback_split_body(source, body_text, limit=limit, keyword=keyword)
            return items[:limit]
        finally:
            await page.close()

    async def _auto_scroll(self, page, *, rounds: int) -> None:
        for _ in range(rounds):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(800)

    def _fallback_split_body(
        self,
        source: dict[str, Any],
        text: str,
        *,
        limit: int,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in re.split(r"[\r\n]+", text):
            title = self._clean(line)
            row = {"来源": source["name"], "标题": title, "链接": source["url"]}
            if self._is_valid_title(title) and (not keyword or self._matches_keyword(row, keyword)):
                if keyword:
                    row["匹配关键词"] = keyword
                rows.append(row)
            if len(rows) >= limit:
                break
        return rows

    def _render_search_urls(self, source: dict[str, Any], keyword: str) -> list[tuple[str, bool]]:
        encoded = quote(keyword.strip())
        urls: list[tuple[str, bool]] = []
        templates = source.get("search_urls") or ([source["search_url"]] if source.get("search_url") else [])
        for template in templates:
            urls.append((str(template).format(keyword=encoded), False))

        domain = self._primary_domain(source)
        if domain:
            site_query = quote(f"site:{domain} {keyword.strip()}")
            urls.append((f"https://www.bing.com/search?q={site_query}", True))
        return urls

    def _matches_keyword(self, row: dict[str, Any], keyword: str) -> bool:
        keyword = self._clean(keyword)
        if not keyword:
            return True
        text = self._clean(" ".join(str(row.get(key, "")) for key in ("标题", "内容", "summary")))
        return keyword.lower() in text.lower()

    def _is_allowed_domain(self, url: str, source: dict[str, Any]) -> bool:
        host = urlparse(url).netloc.lower()
        domain = self._primary_domain(source)
        return bool(domain and (host == domain or host.endswith("." + domain)))

    def _primary_domain(self, source: dict[str, Any]) -> str:
        host = urlparse(str(source.get("url", ""))).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host

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
