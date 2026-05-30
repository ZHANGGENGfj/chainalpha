from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from chainalpha.connectors.base import (
    BaseConnector,
    FetchRequest,
    SourceDocument,
    apply_limit,
    build_source_document,
    in_date_range,
    parse_datetime,
    text_matches,
)
from chainalpha.connectors.http_utils import http_get, parse_html


def _xml_namespace(root: ET.Element) -> dict[str, str]:
    if root.tag.startswith("{"):
        uri = root.tag.split("}", 1)[0].strip("{")
        return {"sm": uri}
    return {}


def _find_text(el: ET.Element, path: str, ns: dict[str, str]) -> str | None:
    found = el.find(path, ns) if ns else el.find(path.replace("sm:", ""))
    if found is None or found.text is None:
        return None
    return found.text.strip()


@dataclass
class SitemapUrl:
    loc: str
    lastmod: datetime | None = None


class SitemapConnector(BaseConnector):
    """Generic sitemap connector.

    It reads sitemap.xml or sitemap index files, filters URLs by keyword/date,
    and optionally fetches the linked pages to extract article text.

    YAML example
    ------------
    - name: company_sitemap
      type: sitemap
      enabled: true
      sitemap_urls:
        - https://example.com/sitemap.xml
      include_patterns: ["/news/", "/press/"]
      exclude_patterns: ["/tag/", "/author/"]
      fetch_pages: true
      max_pages: 50
    """

    connector_type = "sitemap"

    def __init__(
        self,
        name: str,
        sitemap_urls: list[str],
        source_type: str = "sitemap",
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        fetch_pages: bool = True,
        max_sitemaps: int = 20,
        max_pages: int = 100,
        timeout: int = 20,
        headers: dict[str, str] | None = None,
        **params: Any,
    ) -> None:
        super().__init__(
            name,
            sitemap_urls=sitemap_urls,
            source_type=source_type,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            fetch_pages=fetch_pages,
            max_sitemaps=max_sitemaps,
            max_pages=max_pages,
            timeout=timeout,
            **params,
        )
        self.sitemap_urls = sitemap_urls
        self.source_type = source_type
        self.include_patterns = include_patterns or []
        self.exclude_patterns = exclude_patterns or []
        self.fetch_pages = fetch_pages
        self.max_sitemaps = max_sitemaps
        self.max_pages = max_pages
        self.timeout = timeout
        self.headers = headers or {}

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        urls: list[SitemapUrl] = []
        visited_sitemaps: set[str] = set()
        queue = list(self.sitemap_urls)

        while queue and len(visited_sitemaps) < self.max_sitemaps:
            sitemap_url = queue.pop(0)
            if sitemap_url in visited_sitemaps:
                continue
            visited_sitemaps.add(sitemap_url)
            xml_text = self._download_sitemap(sitemap_url)
            child_sitemaps, page_urls = self._parse_sitemap(xml_text)
            queue.extend([u for u in child_sitemaps if u not in visited_sitemaps])
            urls.extend(page_urls)
            if len(urls) >= self.max_pages:
                break

        docs: list[SourceDocument] = []
        for item in urls:
            if not self._url_allowed(item.loc):
                continue
            if not in_date_range(item.lastmod, request.start_date, request.end_date):
                continue
            if request.terms and not text_matches(item.loc, request) and not self.fetch_pages:
                continue

            if not self.fetch_pages:
                docs.append(
                    build_source_document(
                        source=self.name,
                        source_type=self.source_type,
                        title=item.loc.rsplit("/", 1)[-1] or item.loc,
                        content=item.loc,
                        url=item.loc,
                        published_at=item.lastmod,
                        metadata={"kind": "sitemap_url"},
                    )
                )
            else:
                try:
                    resp = http_get(item.loc, timeout=self.timeout, headers=self.headers)
                    parser = parse_html(resp.text, base_url=item.loc)
                    title = parser.title() or item.loc.rsplit("/", 1)[-1] or item.loc
                    content = parser.text()
                    if not text_matches(f"{title}\n{content}\n{item.loc}", request):
                        continue
                    docs.append(
                        build_source_document(
                            source=self.name,
                            source_type=self.source_type,
                            title=title,
                            content=content,
                            url=item.loc,
                            published_at=item.lastmod,
                            metadata={"kind": "sitemap_page", "status_code": resp.status_code},
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - skip bad pages
                    if not request.terms or text_matches(item.loc, request):
                        docs.append(
                            build_source_document(
                                source=self.name,
                                source_type=self.source_type,
                                title=item.loc.rsplit("/", 1)[-1] or item.loc,
                                content=item.loc,
                                url=item.loc,
                                published_at=item.lastmod,
                                metadata={"kind": "sitemap_url_fetch_failed", "error": str(exc)},
                            )
                        )

            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)

    def _download_sitemap(self, url: str) -> str:
        resp = http_get(url, timeout=self.timeout, headers=self.headers)
        content = resp.content
        if url.endswith(".gz") or resp.headers.get("Content-Type", "").lower().find("gzip") >= 0:
            content = gzip.decompress(content)
        return content.decode(resp.encoding or "utf-8", errors="replace")

    def _parse_sitemap(self, xml_text: str) -> tuple[list[str], list[SitemapUrl]]:
        root = ET.fromstring(xml_text.encode("utf-8"))
        ns = _xml_namespace(root)
        child_sitemaps: list[str] = []
        page_urls: list[SitemapUrl] = []

        sitemap_nodes = root.findall("sm:sitemap", ns) if ns else root.findall("sitemap")
        for node in sitemap_nodes:
            loc = _find_text(node, "sm:loc", ns)
            if loc:
                child_sitemaps.append(loc)

        url_nodes = root.findall("sm:url", ns) if ns else root.findall("url")
        for node in url_nodes:
            loc = _find_text(node, "sm:loc", ns)
            if not loc:
                continue
            lastmod = parse_datetime(_find_text(node, "sm:lastmod", ns))
            page_urls.append(SitemapUrl(loc=loc, lastmod=lastmod))
        return child_sitemaps, page_urls

    def _url_allowed(self, url: str) -> bool:
        if self.include_patterns and not any(p in url for p in self.include_patterns):
            return False
        if self.exclude_patterns and any(p in url for p in self.exclude_patterns):
            return False
        return True
