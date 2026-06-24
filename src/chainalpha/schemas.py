from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
import hashlib


def stable_id(*parts: str) -> str:
    raw = "|".join([p or "" for p in parts])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item") and callable(value.item):
        try:
            return _jsonable(value.item())
        except Exception:
            pass
    if not isinstance(value, (str, bytes)) and hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


@dataclass
class SourceDocument:
    """Normalized document returned by a data-source connector.

    This is deliberately separate from `Document`, which is the internal
    Obsidian/Markdown document representation. SourceDocument is the boundary
    object for external news, crawlers, RSS feeds, announcements, and local files.
    """

    id: str
    source: str
    source_type: str
    title: str
    content: str
    url: str | None = None
    path: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat() if self.published_at else None
        d["fetched_at"] = self.fetched_at.isoformat() if self.fetched_at else None
        return _jsonable(d)

    def display_path(self) -> str:
        return self.url or self.path or f"{self.source}:{self.id}"


@dataclass
class Document:
    id: str
    path: str
    title: str
    content: str
    source: str = "obsidian"
    source_type: str = "obsidian_note"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    frontmatter: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        d["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        return _jsonable(d)


@dataclass
class Chunk:
    id: str
    document_id: str
    path: str
    title: str
    text: str
    heading: str | None = None
    tags: list[str] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)
    source: str = "obsidian"
    source_type: str = "obsidian_note"
    score: float = 0.0


@dataclass
class IndustryEvent:
    id: str
    event_type: str
    title: str
    description: str
    source_doc_id: str
    source_path: str
    source: str = "unknown"
    source_type: str = "unknown"
    occurred_at: datetime | None = None
    companies: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    supply_chain_nodes: list[str] = field(default_factory=list)
    direction: str = "unknown"
    magnitude: str = "medium"
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["occurred_at"] = self.occurred_at.isoformat() if self.occurred_at else None
        return d


@dataclass
class CausalHypothesis:
    id: str
    event_id: str
    hypothesis: str
    causal_path: list[str]
    bottleneck_nodes: list[str]
    confidence: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class WeakSignal:
    id: str
    title: str
    stage: str
    weak_signal_score: float
    attention_score: float
    event: IndustryEvent
    hypothesis: CausalHypothesis
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event"] = self.event.to_dict()
        return d


@dataclass
class CompanyProfile:
    name: str
    ticker: str | None = None
    exchange: str | None = None
    industry: str | None = None
    nodes: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    role: str = ""
    watch_tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FinancialSnapshot:
    company: str
    ticker: str | None = None
    period: str | None = None
    revenue: float | None = None
    net_profit: float | None = None
    revenue_growth: float | None = None
    profit_growth: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    roe: float | None = None
    eps: float | None = None
    bps: float | None = None
    operating_cash_flow: float | None = None
    debt_ratio: float | None = None
    pe: float | None = None
    pb: float | None = None
    ps: float | None = None
    market_cap: float | None = None
    float_market_cap: float | None = None
    total_shares: float | None = None
    float_shares: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PriceSnapshot:
    company: str
    ticker: str | None = None
    date: str | None = None
    close: float | None = None
    change_1m: float | None = None
    change_3m: float | None = None
    change_6m: float | None = None
    change_12m: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InvestmentCandidate:
    company: CompanyProfile
    score: float
    rating: str
    thesis: str
    signals: list[WeakSignal] = field(default_factory=list)
    financial: FinancialSnapshot | None = None
    price: PriceSnapshot | None = None
    catalysts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnnouncementInsight:
    title: str
    company: str | None = None
    summary: str = ""
    event_type: str = "unknown"
    sentiment: str = "neutral"
    catalysts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    confidence: float = 0.5
