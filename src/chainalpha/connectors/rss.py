from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
import re
import xml.etree.ElementTree as ET
from typing import Any

import requests

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, in_date_range, parse_datetime, text_matches
from chainalpha.connectors.http_utils import http_get, parse_html
from chainalpha.utils.text import normalize_text

TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return normalize_text(TAG_RE.sub(" ", unescape(text or "")))


class RSSConnector(BaseConnector):
    """Read RSS/Atom feeds.

    Supports RSS 2.0, Atom and common content namespaces. It can optionally
    follow feed item links to fetch full article pages.
    """

    connector_type = "rss"

    def __init__(
        self,
        name: str,
        urls: list[str],
        timeout: int = 15,
        source_type: str = "mainstream_media",
        fetch_linked_pages: bool = False,
        content_selector: str = "body",  # Reserved for API consistency; stdlib parser extracts all visible text.
        headers: dict[str, str] | None = None,
        **params: Any,
    ) -> None:
        super().__init__(name, urls=urls, timeout=timeout, source_type=source_type, fetch_linked_pages=fetch_linked_pages, **params)
        self.urls = urls
        self.timeout = timeout
        self.source_type = source_type
        self.fetch_linked_pages = fetch_linked_pages
        self.content_selector = content_selector
        self.headers = headers or {}

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        for url in self.urls:
            resp = requests.get(url, timeout=self.timeout, headers={"User-Agent": "ChainAlpha/0.1", **self.headers})
            resp.raise_for_status()
            docs.extend(self._parse_feed(resp.text, feed_url=url, request=request))
            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)

    def _parse_feed(self, xml_text: str, feed_url: str, request: FetchRequest) -> list[SourceDocument]:
        root = ET.fromstring(xml_text.encode("utf-8"))
        docs: list[SourceDocument] = []

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "content": "http://purl.org/rss/1.0/modules/content/",
            "dc": "http://purl.org/dc/elements/1.1/",
        }

        for item in root.findall(".//item"):
            title = self._text(item, "title") or "Untitled RSS item"
            link = self._text(item, "link")
            desc = (
                self._text(item, "{http://purl.org/rss/1.0/modules/content/}encoded")
                or self._text(item, "description")
                or self._text(item, "summary")
                or ""
            )
            pub = self._parse_rss_time(self._text(item, "pubDate") or self._text(item, "date") or self._text(item, "{http://purl.org/dc/elements/1.1/}date"))
            content = strip_html(desc)
            if self.fetch_linked_pages and link:
                full = self._fetch_linked_page(link)
                if len(full) > len(content):
                    content = full
            if not in_date_range(pub, request.start_date, request.end_date):
                continue
            if not text_matches(f"{title}\n{content}", request):
                continue
            docs.append(build_source_document(source=self.name, source_type=self.source_type, title=strip_html(title), content=content, url=link, published_at=pub, metadata={"feed_url": feed_url}))

        for entry in root.findall(".//atom:entry", ns):
            title = self._text(entry, "atom:title", ns) or "Untitled Atom entry"
            link = self._atom_link(entry, ns)
            summary = self._text(entry, "atom:summary", ns) or self._text(entry, "atom:content", ns) or ""
            pub = parse_datetime(self._text(entry, "atom:published", ns) or self._text(entry, "atom:updated", ns))
            content = strip_html(summary)
            if self.fetch_linked_pages and link:
                full = self._fetch_linked_page(link)
                if len(full) > len(content):
                    content = full
            if not in_date_range(pub, request.start_date, request.end_date):
                continue
            if not text_matches(f"{title}\n{content}", request):
                continue
            docs.append(build_source_document(source=self.name, source_type=self.source_type, title=strip_html(title), content=content, url=link, published_at=pub, metadata={"feed_url": feed_url}))
        return docs

    def _text(self, element: ET.Element, path: str, ns: dict[str, str] | None = None) -> str | None:
        found = element.find(path, ns or {})
        if found is None or found.text is None:
            return None
        return found.text.strip()

    def _atom_link(self, entry: ET.Element, ns: dict[str, str]) -> str | None:
        for link_el in entry.findall("atom:link", ns):
            rel = link_el.attrib.get("rel", "alternate")
            href = link_el.attrib.get("href")
            if href and rel == "alternate":
                return href
        link_el = entry.find("atom:link", ns)
        return link_el.attrib.get("href") if link_el is not None else None

    def _parse_rss_time(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return parsedate_to_datetime(value).replace(tzinfo=None)
        except Exception:
            return parse_datetime(value)

    def _fetch_linked_page(self, url: str) -> str:
        try:
            resp = http_get(url, timeout=self.timeout, headers=self.headers)
            return parse_html(resp.text, base_url=url).text()
        except Exception:
            return ""
