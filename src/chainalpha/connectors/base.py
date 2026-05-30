from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from chainalpha.schemas import SourceDocument, stable_id


@dataclass(frozen=True)
class FetchRequest:
    """A normalized request passed to every connector.

    `query` is used as a broad full-text filter. `keywords` is used as a more
    precise topic filter. Connectors may ignore unsupported fields, but they
    should keep the method deterministic and side-effect free.
    """

    query: str | None = None
    keywords: list[str] = field(default_factory=list)
    start_date: datetime | None = None
    end_date: datetime | None = None
    limit: int | None = 200
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def terms(self) -> list[str]:
        terms: list[str] = []
        if self.query:
            terms.extend([x.strip() for x in self.query.replace("，", " ").split() if x.strip()])
        terms.extend([x.strip() for x in self.keywords if x.strip()])
        # Keep order while deduplicating.
        return list(dict.fromkeys(terms))


@dataclass(frozen=True)
class ConnectorConfig:
    name: str
    type: str
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    source_name: str
    documents: list[SourceDocument]
    errors: list[str] = field(default_factory=list)


class ConnectorError(RuntimeError):
    pass


class BaseConnector(ABC):
    """Base interface for all source connectors.

    Connectors only fetch and normalize raw public information into
    SourceDocument. They should not perform event extraction, scoring, or
    investment reasoning.
    """

    connector_type: str = "base"

    def __init__(self, name: str, **params: Any) -> None:
        self.name = name
        self.params = params

    @abstractmethod
    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        raise NotImplementedError

    def fetch_safe(self, request: FetchRequest) -> FetchResult:
        try:
            return FetchResult(source_name=self.name, documents=self.fetch(request), errors=[])
        except Exception as exc:  # noqa: BLE001 - connector boundary should not crash the pipeline
            return FetchResult(source_name=self.name, documents=[], errors=[f"{type(exc).__name__}: {exc}"])


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Handle seconds and milliseconds timestamps.
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts)
        except Exception:
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        candidates = [raw, raw.replace("Z", "+00:00")]
        for candidate in candidates:
            try:
                return datetime.fromisoformat(candidate)
            except Exception:
                pass
        # Common Chinese / crawler formats.
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%Y年%m月%d日 %H:%M",
            "%Y年%m月%d日",
        ):
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                pass
    return None


def in_date_range(value: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if value is None:
        return True
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def text_matches(text: str, request: FetchRequest) -> bool:
    terms = request.terms
    if not terms:
        return True
    low = text.lower()
    return any(term.lower() in low for term in terms)


def apply_limit(items: Iterable[SourceDocument], limit: int | None) -> list[SourceDocument]:
    results: list[SourceDocument] = []
    for item in items:
        results.append(item)
        if limit is not None and len(results) >= limit:
            break
    return results


def build_source_document(
    *,
    source: str,
    source_type: str,
    title: str,
    content: str,
    url: str | None = None,
    path: str | None = None,
    published_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> SourceDocument:
    metadata = metadata or {}
    doc_id = stable_id(source, source_type, title, url or path or "", content[:500])
    return SourceDocument(
        id=doc_id,
        source=source,
        source_type=source_type,
        title=title.strip() or "Untitled",
        content=content.strip(),
        url=url,
        path=path,
        published_at=published_at,
        fetched_at=datetime.now(),
        metadata=metadata,
    )


def resolve_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()
