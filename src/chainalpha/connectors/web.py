from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import requests

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, build_source_document, text_matches
from chainalpha.utils.text import normalize_text


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = False
        self.title: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip = True
        if tag.lower() in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip = False
        if tag.lower() == "title":
            pass

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return normalize_text(" ".join(self.parts))


class WebPageConnector(BaseConnector):
    """Fetch a fixed list of web pages and extract readable text.

    This connector is for official pages, press releases, or pages you already
    know. It is intentionally not a web crawler.
    """

    connector_type = "web_pages"

    def __init__(self, name: str, urls: list[str], timeout: int = 20, source_type: str = "company_website", **params: Any) -> None:
        super().__init__(name, urls=urls, timeout=timeout, source_type=source_type, **params)
        self.urls = urls
        self.timeout = timeout
        self.source_type = source_type

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        for url in self.urls:
            resp = requests.get(url, timeout=self.timeout, headers={"User-Agent": "ChainAlpha/0.1"})
            resp.raise_for_status()
            parser = _HTMLTextParser()
            parser.feed(resp.text)
            content = parser.text()
            title = self._extract_title(resp.text) or url
            if not text_matches(f"{title}\n{content}", request):
                continue
            docs.append(
                build_source_document(
                    source=self.name,
                    source_type=self.source_type,
                    title=title,
                    content=content,
                    url=url,
                    metadata={"status_code": resp.status_code},
                )
            )
            if request.limit is not None and len(docs) >= request.limit:
                break
        return docs

    def _extract_title(self, html: str) -> str | None:
        low = html.lower()
        start = low.find("<title")
        if start < 0:
            return None
        start = low.find(">", start)
        end = low.find("</title>", start)
        if start < 0 or end < 0:
            return None
        return normalize_text(html[start + 1:end])
