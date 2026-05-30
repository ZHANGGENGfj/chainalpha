from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus, urlparse

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, text_matches
from chainalpha.utils.text import normalize_text


class PlaywrightConnector(BaseConnector):
    """Generic dynamic-page connector powered by Playwright.

    This connector is intentionally optional. Install runtime support with:

        python -m pip install playwright
        python -m playwright install chromium

    It is useful for pages that require JavaScript rendering. It does not bypass
    login walls, CAPTCHAs or paywalls. For authenticated sources, pass an
    existing Playwright storage_state file that you own.

    YAML example
    ------------
    - name: dynamic_news
      type: playwright
      enabled: true
      search_url_templates:
        - https://example.com/search?q={query}
      fetch_linked_pages: true
      allowed_domains: [example.com]
      wait_until: networkidle
      wait_selector: article
      content_selector: body
      max_linked_pages_per_query: 5
      headless: true
    """

    connector_type = "playwright"

    def __init__(
        self,
        name: str,
        search_url_templates: list[str] | None = None,
        urls: list[str] | None = None,
        allowed_domains: list[str] | None = None,
        source_type: str = "dynamic_web_page",
        fetch_linked_pages: bool = False,
        max_linked_pages_per_query: int = 5,
        headless: bool = True,
        timeout_ms: int = 30000,
        wait_until: str = "domcontentloaded",
        wait_selector: str | None = None,
        content_selector: str = "body",
        link_selector: str = "a[href]",
        user_agent: str | None = None,
        storage_state: str | None = None,
        **params: Any,
    ) -> None:
        super().__init__(
            name,
            search_url_templates=search_url_templates,
            urls=urls,
            allowed_domains=allowed_domains,
            source_type=source_type,
            fetch_linked_pages=fetch_linked_pages,
            max_linked_pages_per_query=max_linked_pages_per_query,
            headless=headless,
            timeout_ms=timeout_ms,
            wait_until=wait_until,
            wait_selector=wait_selector,
            content_selector=content_selector,
            link_selector=link_selector,
            storage_state=storage_state,
            **params,
        )
        self.search_url_templates = search_url_templates or []
        self.urls = urls or []
        self.allowed_domains = allowed_domains or []
        self.source_type = source_type
        self.fetch_linked_pages = fetch_linked_pages
        self.max_linked_pages_per_query = max_linked_pages_per_query
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.wait_until = wait_until
        self.wait_selector = wait_selector
        self.content_selector = content_selector
        self.link_selector = link_selector
        self.user_agent = user_agent
        self.storage_state = storage_state

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "PlaywrightConnector requires optional dependency: "
                "python -m pip install playwright && python -m playwright install chromium"
            ) from exc

        target_urls: list[tuple[str, str | None]] = [(u, None) for u in self.urls]
        terms = request.terms or [request.query or ""]
        for term in terms:
            encoded = quote_plus(term)
            for tpl in self.search_url_templates:
                target_urls.append((tpl.format(query=encoded, raw_query=term), term))

        docs: list[SourceDocument] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context_kwargs: dict[str, Any] = {}
            if self.user_agent:
                context_kwargs["user_agent"] = self.user_agent
            if self.storage_state:
                context_kwargs["storage_state"] = self.storage_state
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            for url, term in target_urls:
                docs.extend(self._fetch_one(page, url, request, kind="dynamic_page", query=term, parent_url=None))
                if self.fetch_linked_pages:
                    links = self._extract_links(page)[: self.max_linked_pages_per_query]
                    for link in links:
                        docs.extend(self._fetch_one(page, link, request, kind="linked_dynamic_page", query=term, parent_url=url))
                        if request.limit is not None and len(docs) >= request.limit:
                            break
                if request.limit is not None and len(docs) >= request.limit:
                    break

            context.close()
            browser.close()
        return apply_limit(docs, request.limit)

    def _fetch_one(self, page: Any, url: str, request: FetchRequest, *, kind: str, query: str | None, parent_url: str | None) -> list[SourceDocument]:
        try:
            page.goto(url, wait_until=self.wait_until, timeout=self.timeout_ms)
            if self.wait_selector:
                page.wait_for_selector(self.wait_selector, timeout=self.timeout_ms)
            title = normalize_text(page.title()) or url
            content = normalize_text(page.locator(self.content_selector).inner_text(timeout=self.timeout_ms))
            if not text_matches(f"{title}\n{content}\n{url}", request):
                return []
            return [
                build_source_document(
                    source=self.name,
                    source_type=self.source_type,
                    title=title,
                    content=content,
                    url=url,
                    metadata={"kind": kind, "query": query, "parent_url": parent_url},
                )
            ]
        except Exception:
            return []

    def _extract_links(self, page: Any) -> list[str]:
        try:
            hrefs = page.locator(self.link_selector).evaluate_all("els => els.map(a => a.href).filter(Boolean)")
        except Exception:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for href in hrefs:
            if not isinstance(href, str) or not href.startswith(("http://", "https://")):
                continue
            if self.allowed_domains and not self._allowed(href):
                continue
            clean = href.split("#", 1)[0]
            if clean in seen:
                continue
            seen.add(clean)
            out.append(clean)
        return out

    def _allowed(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return any(host == d.lower() or host.endswith("." + d.lower()) for d in self.allowed_domains)
