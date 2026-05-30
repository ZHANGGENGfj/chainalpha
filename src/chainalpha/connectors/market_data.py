from __future__ import annotations

from datetime import datetime
import csv
from io import StringIO
from typing import Any

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document
from chainalpha.connectors.http_utils import http_get


class StooqPriceConnector(BaseConnector):
    """Simple public EOD price connector via Stooq CSV.

    Use for market attention / price-move filters. For A-share data, prefer a
    licensed local export or configure JSONAPIConnector with your data vendor.
    """

    connector_type = "stooq_prices"

    def __init__(self, name: str, symbols: list[str], timeout: int = 20, **params: Any) -> None:
        super().__init__(name, symbols=symbols, **params)
        self.symbols = symbols
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        for sym in self.symbols:
            url = f"https://stooq.com/q/d/l/?s={sym.lower()}&i=d"
            resp = http_get(url, timeout=self.timeout)
            rows = list(csv.DictReader(StringIO(resp.text)))
            if not rows:
                continue
            recent = rows[-60:]
            last = recent[-1]
            first = recent[0]
            try:
                pct = (float(last["Close"]) / float(first["Close"]) - 1) * 100
            except Exception:
                pct = 0.0
            title = f"Price history {sym}: last close {last.get('Close')}"
            content = f"Symbol: {sym}\nLatest date: {last.get('Date')}\nLatest close: {last.get('Close')}\n60 trading day change: {pct:.2f}%\nRows: {len(rows)}"
            docs.append(
                build_source_document(
                    source=self.name,
                    source_type="price_data",
                    title=title,
                    content=content,
                    url=url,
                    published_at=None,
                    metadata={"symbol": sym, "latest": last, "window_change_pct": pct},
                )
            )
            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)
