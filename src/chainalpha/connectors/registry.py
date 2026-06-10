from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import os
import re

import yaml

from chainalpha.connectors.base import BaseConnector, ConnectorConfig, FetchRequest, FetchResult
from chainalpha.connectors.local_files import LocalFilesConnector
from chainalpha.connectors.cninfo import CninfoAnnouncementConnector
from chainalpha.connectors.custom_api import JSONAPIConnector
from chainalpha.connectors.china_finance import (
    AkshareNewsConnector,
    MooTDXFinancialConnector,
    MooTDXQuoteConnector,
    TencentQuoteConnector,
)
from chainalpha.connectors.market_data import StooqPriceConnector
from chainalpha.connectors.patents import PatentsViewConnector
from chainalpha.connectors.search_page import PredefinedNewsConnector, SearchPageConnector
from chainalpha.connectors.sec_edgar import SecEdgarConnector
from chainalpha.connectors.social import RedditConnector, XRecentSearchConnector
from chainalpha.connectors.media_crawler_adapter import MediaCrawlerConnector
from chainalpha.connectors.financial_web import FinancialWebConnector
from chainalpha.connectors.obsidian import ObsidianConnector
from chainalpha.connectors.rss import RSSConnector
from chainalpha.connectors.sitemap import SitemapConnector
from chainalpha.connectors.playwright_page import PlaywrightConnector
from chainalpha.connectors.web import WebPageConnector
from chainalpha.schemas import SourceDocument

ConnectorFactory = Callable[..., BaseConnector]


class ConnectorRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ConnectorFactory] = {}

    def register(self, connector_type: str, factory: ConnectorFactory) -> None:
        self._factories[connector_type] = factory

    def create(self, config: ConnectorConfig) -> BaseConnector:
        if config.type not in self._factories:
            known = ", ".join(sorted(self._factories))
            raise ValueError(f"Unknown connector type: {config.type}. Known: {known}")
        return self._factories[config.type](name=config.name, **config.params)

    @classmethod
    def with_defaults(cls) -> "ConnectorRegistry":
        reg = cls()
        reg.register("local_files", LocalFilesConnector)
        reg.register("obsidian", ObsidianConnector)
        reg.register("media_crawler", MediaCrawlerConnector)
        reg.register("financial_web", FinancialWebConnector)
        reg.register("rss", RSSConnector)
        reg.register("sitemap", SitemapConnector)
        reg.register("playwright", PlaywrightConnector)
        reg.register("web_pages", WebPageConnector)
        reg.register("search_pages", SearchPageConnector)
        reg.register("news_source", PredefinedNewsConnector)
        reg.register("cninfo_announcements", CninfoAnnouncementConnector)
        reg.register("sec_edgar", SecEdgarConnector)
        reg.register("json_api", JSONAPIConnector)
        reg.register("tencent_quotes", TencentQuoteConnector)
        reg.register("akshare_news", AkshareNewsConnector)
        reg.register("mootdx_quotes", MooTDXQuoteConnector)
        reg.register("mootdx_financials", MooTDXFinancialConnector)
        reg.register("patentsview", PatentsViewConnector)
        reg.register("reddit", RedditConnector)
        reg.register("x_recent_search", XRecentSearchConnector)
        reg.register("stooq_prices", StooqPriceConnector)
        return reg


@dataclass
class SourceManager:
    connectors: list[BaseConnector]
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_configs(cls, configs: list[ConnectorConfig], registry: ConnectorRegistry | None = None) -> "SourceManager":
        registry = registry or ConnectorRegistry.with_defaults()
        connectors: list[BaseConnector] = []
        errors: list[str] = []
        for cfg in configs:
            if not cfg.enabled:
                continue
            try:
                connectors.append(registry.create(cfg))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{cfg.name}: {type(exc).__name__}: {exc}")
        return cls(connectors=connectors, errors=errors)

    @classmethod
    def from_yaml(cls, path: str | Path, registry: ConnectorRegistry | None = None) -> "SourceManager":
        configs = load_connector_configs(path)
        return cls.from_configs(configs, registry=registry)

    def fetch_all(self, request: FetchRequest) -> tuple[list[SourceDocument], list[FetchResult]]:
        all_docs: list[SourceDocument] = []
        results: list[FetchResult] = []
        for connector in self.connectors:
            result = connector.fetch_safe(request)
            results.append(result)
            all_docs.extend(result.documents)
        return deduplicate_documents(all_docs), results



_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(value: Any) -> Any:
    """Expand ${ENV_VAR} in YAML config values."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, list):
        return [expand_env(x) for x in value]
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    return value


def load_connector_configs(path: str | Path) -> list[ConnectorConfig]:
    p = Path(path).expanduser().resolve()
    data = expand_env(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    raw_sources = data.get("sources", [])
    configs: list[ConnectorConfig] = []

    # Preferred form:
    # sources:
    #   - name: local_news
    #     type: local_files
    #     enabled: true
    #     path: ./data/raw
    if isinstance(raw_sources, list):
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("type") or "source")
            typ = str(item.get("type") or name)
            enabled = bool(item.get("enabled", True))
            params = {k: v for k, v in item.items() if k not in {"name", "type", "enabled"}}
            configs.append(ConnectorConfig(name=name, type=typ, enabled=enabled, params=params))
        return configs

    # Backward-compatible grouped form:
    # sources:
    #   news:
    #     connectors:
    #       - name: rss
    #         type: rss
    if isinstance(raw_sources, dict):
        for group_name, group in raw_sources.items():
            if not isinstance(group, dict) or not group.get("enabled", True):
                continue
            for item in group.get("connectors", []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or f"{group_name}_{item.get('type', 'source')}")
                typ = str(item.get("type") or name)
                enabled = bool(item.get("enabled", True))
                params = {k: v for k, v in item.items() if k not in {"name", "type", "enabled"}}
                configs.append(ConnectorConfig(name=name, type=typ, enabled=enabled, params=params))
    return configs


def deduplicate_documents(docs: list[SourceDocument]) -> list[SourceDocument]:
    seen: set[str] = set()
    unique: list[SourceDocument] = []
    for doc in docs:
        key = doc.url or doc.path or doc.id
        if key in seen:
            continue
        seen.add(key)
        unique.append(doc)
    return unique


def parse_request_from_config(data: dict[str, Any] | None = None) -> FetchRequest:
    data = data or {}

    def dt(value: Any) -> datetime | None:
        from chainalpha.connectors.base import parse_datetime

        return parse_datetime(value)

    return FetchRequest(
        query=data.get("query"),
        keywords=[str(x) for x in data.get("keywords", [])],
        start_date=dt(data.get("start_date")),
        end_date=dt(data.get("end_date")),
        limit=data.get("limit", 200),
        metadata={k: v for k, v in data.items() if k not in {"query", "keywords", "start_date", "end_date", "limit"}},
    )
