from __future__ import annotations

import os

os.environ.pop("SSLKEYLOGFILE", None)

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import json
import re
import time
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests

from chainalpha.connectors.base import parse_datetime
from chainalpha.utils.text import normalize_text

DEFAULT_USER_AGENT = (
    "ChainAlpha/0.1 (+https://github.com/chainalpha/chainalpha; "
    "public-information research; contact=research@example.com)"
)


class HTMLTextAndLinkParser(HTMLParser):
    """Small stdlib HTML parser used by connectors.

    It intentionally extracts only visible text, page title and links. It does
    not execute JavaScript and does not bypass paywalls or login walls.
    """

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__()
        self.base_url = base_url
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._current_href: str | None = None
        self._current_anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attrs_d = {k.lower(): v for k, v in attrs}
        if t in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
            return
        if t == "title":
            self._in_title = True
        if t == "a":
            href = attrs_d.get("href")
            if href:
                self._current_href = urljoin(self.base_url or "", href)
                self._current_anchor = []
        if t in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if t == "title":
            self._in_title = False
        if t == "a" and self._current_href:
            label = normalize_text(" ".join(self._current_anchor))
            self.links.append((self._current_href, label))
            self._current_href = None
            self._current_anchor = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        if self._current_href:
            self._current_anchor.append(text)
        self.parts.append(text)

    def text(self) -> str:
        return normalize_text(" ".join(self.parts))

    def title(self) -> str | None:
        title = normalize_text(" ".join(self.title_parts))
        return title or None


def parse_html(html: str, base_url: str | None = None) -> HTMLTextAndLinkParser:
    parser = HTMLTextAndLinkParser(base_url=base_url)
    parser.feed(html)
    return parser


def strip_html(html: str) -> str:
    parser = HTMLTextAndLinkParser()
    parser.feed(unescape(html or ""))
    return parser.text()


def http_get(
    url: str,
    *,
    timeout: int = 20,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    sleep_seconds: float = 0.0,
) -> requests.Response:
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    h = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8"}
    if headers:
        h.update(headers)
    resp = requests.get(url, params=params, timeout=timeout, headers=h)
    resp.raise_for_status()
    return resp


def http_post(
    url: str,
    *,
    timeout: int = 20,
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    sleep_seconds: float = 0.0,
) -> requests.Response:
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    h = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json,text/html,*/*"}
    if headers:
        h.update(headers)
    resp = requests.post(url, data=data, json=json_body, timeout=timeout, headers=h)
    resp.raise_for_status()
    return resp


def best_effort_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return json.loads(resp.text)


def parse_http_datetime(value: Any) -> datetime | None:
    dt = parse_datetime(value)
    if dt:
        return dt
    if isinstance(value, str):
        try:
            return parsedate_to_datetime(value).replace(tzinfo=None)
        except Exception:
            return None
    return None


def same_domain_or_allowed(url: str, allowed_domains: Iterable[str] | None) -> bool:
    if not allowed_domains:
        return True
    host = urlparse(url).netloc.lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed_domains)


def unique_links(links: Iterable[tuple[str, str]], allowed_domains: Iterable[str] | None = None) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for url, label in links:
        if not url.startswith(("http://", "https://")):
            continue
        if not same_domain_or_allowed(url, allowed_domains):
            continue
        # Drop obvious static assets / anchors.
        clean = url.split("#", 1)[0]
        low = clean.lower()
        if any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js", ".ico")):
            continue
        if clean in seen:
            continue
        seen.add(clean)
        out.append((clean, label))
    return out
