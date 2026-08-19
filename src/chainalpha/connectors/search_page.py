from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote_plus

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, text_matches
from chainalpha.connectors.http_utils import http_get, parse_html, unique_links
from chainalpha.utils.text import normalize_text


class SearchPageConnector(BaseConnector):
    """Generic connector for public search/list pages.

    It fetches configured search/list pages, extracts visible text, and
    optionally follows same-domain article links. This is the safe fallback for
    sources without stable public APIs. It does not execute JavaScript, bypass
    login walls, or scrape paywalled content.

    Parameters
    ----------
    search_url_templates:
        URL templates containing `{query}`. Example:
        `https://www.prnewswire.com/search/news/?keyword={query}`
    urls:
        Fixed list pages to fetch.
    allowed_domains:
        Domains that linked pages may be fetched from.
    fetch_linked_pages:
        Whether to fetch extracted article/detail links.
    max_linked_pages_per_query:
        Safety cap for followed links.
    """

    connector_type = "search_pages"

    def __init__(
        self,
        name: str,
        search_url_templates: list[str] | None = None,
        urls: list[str] | None = None,
        allowed_domains: list[str] | None = None,
        source_type: str = "web_search_page",
        fetch_linked_pages: bool = False,
        include_search_pages: bool = True,
        max_linked_pages_per_query: int = 5,
        timeout: int = 20,
        sleep_seconds: float = 0.0,
        max_workers: int = 4,
        max_page_fetches: int | None = None,
        headers: dict[str, str] | None = None,
        **params: Any,
    ) -> None:
        super().__init__(
            name,
            search_url_templates=search_url_templates,
            urls=urls,
            allowed_domains=allowed_domains,
            source_type=source_type,
            fetch_linked_pages=fetch_linked_pages,
            include_search_pages=include_search_pages,
            max_linked_pages_per_query=max_linked_pages_per_query,
            timeout=timeout,
            **params,
        )
        self.search_url_templates = search_url_templates or []
        self.urls = urls or []
        self.allowed_domains = allowed_domains or []
        self.source_type = source_type
        self.fetch_linked_pages = fetch_linked_pages
        self.include_search_pages = include_search_pages
        self.max_linked_pages_per_query = max_linked_pages_per_query
        self.timeout = timeout
        self.sleep_seconds = sleep_seconds
        self.max_workers = max(1, min(int(max_workers), 8))
        self.max_page_fetches = max(0, int(max_page_fetches or 0))
        self.last_page_fetches = 0
        self.headers = headers or {}

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        terms = request.terms or [request.query or ""]
        target_urls: list[tuple[str, str | None]] = [(u, None) for u in self.urls]
        for term in terms:
            q = quote_plus(term)
            for tpl in self.search_url_templates:
                target_urls.append((tpl.format(query=q, raw_query=term), term))
        if self.max_page_fetches:
            target_urls = target_urls[:self.max_page_fetches]
        self.last_page_fetches = len(target_urls)

        # Search engines and financial publishers are independent hosts. Fetch
        # them concurrently, but keep the worker cap small enough to avoid
        # triggering rate limits. One unavailable site must not cancel results
        # from the remaining sites.
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(target_urls)))) as executor:
            pages = list(executor.map(lambda item: self._fetch_search_page(item, request), target_urls))

        link_jobs: list[tuple[str, str, str]] = []
        for page_docs, links in pages:
            docs.extend(page_docs)
            link_jobs.extend(links)

        if self.fetch_linked_pages and link_jobs:
            deduped_jobs: list[tuple[str, str, str]] = []
            seen_links: set[str] = set()
            for job in link_jobs:
                if job[0] not in seen_links:
                    seen_links.add(job[0])
                    deduped_jobs.append(job)
            # Do not spend an unbounded amount of time reading article bodies.
            article_budget = request.limit or self.max_linked_pages_per_query * len(target_urls)
            if self.max_page_fetches:
                article_budget = min(article_budget, max(0, self.max_page_fetches - len(target_urls)))
            self.last_page_fetches += min(len(deduped_jobs), article_budget)
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(deduped_jobs))) as executor:
                for item in executor.map(lambda job: self._fetch_link(job, request), deduped_jobs[:article_budget]):
                    if item is not None:
                        docs.append(item)
        return apply_limit(docs, request.limit)

    def _fetch_search_page(
        self,
        item: tuple[str, str | None],
        request: FetchRequest,
    ) -> tuple[list[SourceDocument], list[tuple[str, str, str]]]:
        url, term = item
        try:
            resp = http_get(url, timeout=self.timeout, headers=self.headers, sleep_seconds=self.sleep_seconds)
            parser = parse_html(resp.text, base_url=url)
        except Exception:
            return [], []
        title = parser.title() or f"{self.name} search page"
        content = parser.text()
        docs: list[SourceDocument] = []
        if self.include_search_pages and text_matches(f"{title}\n{content}", request):
            docs.append(
                build_source_document(
                    source=self.name,
                    source_type=self.source_type,
                    title=title,
                    content=content,
                    url=url,
                    metadata={"kind": "search_or_list_page", "query": term, "status_code": resp.status_code},
                )
            )
        links = [
            (link, label, url)
            for link, label in _rank_links_for_request(unique_links(parser.links, self.allowed_domains), request)[: self.max_linked_pages_per_query]
        ] if self.fetch_linked_pages else []
        return docs, links

    def _fetch_link(self, job: tuple[str, str, str], request: FetchRequest) -> SourceDocument | None:
        link, label, parent_url = job
        try:
            resp = http_get(link, timeout=self.timeout, headers=self.headers, sleep_seconds=self.sleep_seconds)
            parser = parse_html(resp.text, base_url=link)
            title = parser.title() or normalize_text(label) or link
            content = parser.text()
            if not text_matches(f"{title}\n{content}", request):
                return None
            return build_source_document(
                source=self.name,
                source_type=self.source_type,
                title=title,
                content=content,
                url=link,
                metadata={"kind": "linked_page", "parent_url": parent_url, "status_code": resp.status_code},
            )
        except Exception:
            return None


def _rank_links_for_request(
    links: list[tuple[str, str]],
    request: FetchRequest,
) -> list[tuple[str, str]]:
    """Prefer result links whose anchor text carries the issuer name or code."""
    query = " ".join(request.terms or [request.query or ""])
    tokens = [token.lower() for token in re.split(r"[\s,，;；|]+", query) if len(token) >= 2]

    def score(item: tuple[str, str]) -> tuple[int, int]:
        url, label = item
        haystack = f"{label} {url}".lower()
        matches = sum(1 for token in tokens if token in haystack)
        # Detail URLs tend to be more useful than root/list/search URLs.
        is_landing = int(any(marker in url.lower() for marker in ("search", "searchresult", "searchlist", "?q=", "?keyword=")))
        return matches, -is_landing

    return sorted(links, key=score, reverse=True)


PREDEFINED_NEWS_SOURCES: dict[str, dict[str, Any]] = {
    "cls": {
        "source_type": "mainstream_media",
        "search_url_templates": ["https://www.cls.cn/searchPage?keyword={query}"],
        "allowed_domains": ["cls.cn", "www.cls.cn"],
    },
    "stcn": {
        "source_type": "mainstream_media",
        "search_url_templates": ["https://search.stcn.com/?keyword={query}"],
        "allowed_domains": ["stcn.com", "www.stcn.com"],
    },
    "36kr": {
        "source_type": "mainstream_media",
        "search_url_templates": ["https://36kr.com/search/articles/{query}"],
        "allowed_domains": ["36kr.com", "www.36kr.com"],
    },
    "21jingji": {
        "source_type": "mainstream_media",
        "search_url_templates": ["https://www.21jingji.com/search?keyword={query}"],
        "allowed_domains": ["21jingji.com", "www.21jingji.com"],
    },
    "reuters": {
        "source_type": "mainstream_media",
        "search_url_templates": ["https://www.reuters.com/site-search/?query={query}"],
        "allowed_domains": ["reuters.com", "www.reuters.com"],
    },
    "bloomberg": {
        "source_type": "mainstream_media",
        "search_url_templates": ["https://www.bloomberg.com/search?query={query}"],
        "allowed_domains": ["bloomberg.com", "www.bloomberg.com"],
    },
    "prnewswire": {
        "source_type": "press_release",
        "search_url_templates": ["https://www.prnewswire.com/search/news/?keyword={query}"],
        "urls": ["https://www.prnewswire.com/rss/news-releases-list.rss"],
        "allowed_domains": ["prnewswire.com", "www.prnewswire.com"],
    },
}


class PredefinedNewsConnector(SearchPageConnector):
    """Connector for known news/press-release sources.

    Supported `source_key`: cls, stcn, 36kr, 21jingji, reuters, bloomberg,
    prnewswire. These use public search/list pages as a first version. If a
    source blocks automated access or requires a license, put exported data in
    LocalFilesConnector or configure an official API connector instead.
    """

    connector_type = "news_source"

    def __init__(self, name: str, source_key: str, **params: Any) -> None:
        if source_key not in PREDEFINED_NEWS_SOURCES:
            known = ", ".join(sorted(PREDEFINED_NEWS_SOURCES))
            raise ValueError(f"Unknown news source_key={source_key}. Known: {known}")
        base = dict(PREDEFINED_NEWS_SOURCES[source_key])
        base.update(params)
        super().__init__(name=name, **base)
        self.source_key = source_key
