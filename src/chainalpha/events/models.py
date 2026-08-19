from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


EventCategory = Literal["policy", "industry", "earnings", "company", "overseas", "risk"]
EventStatus = Literal["unverified", "confirmed", "tracking", "completed", "corrected"]
ImpactDirection = Literal["positive", "negative", "mixed", "neutral", "uncertain"]
ImpactHorizon = Literal["intraday", "short_term", "medium_term", "long_term"]


class EventSource(BaseModel):
    id: str
    event_id: str = ""
    source_type: Literal["news", "announcement", "research", "social", "unknown"]
    publisher: str
    title: str
    url: str | None = None
    summary: str = ""
    published_at: datetime | None = None
    retrieved_at: datetime
    content_hash: str
    is_primary_source: bool = False
    authority_score: float = Field(ge=0, le=100)
    time_missing: bool = False


class EventTimelineItem(BaseModel):
    id: str
    event_id: str
    occurred_at: datetime
    title: str
    summary: str
    status: EventStatus | None = None
    source_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class MarketEvent(BaseModel):
    id: str
    title: str
    category: EventCategory
    status: EventStatus
    impact_direction: ImpactDirection
    impact_horizon: ImpactHorizon
    occurred_at: datetime | None = None
    occurred_at_estimated: bool = False
    first_seen_at: datetime
    updated_at: datetime
    fact_summary: str
    importance_reason: str
    direct_impact: str = "暂时无法判断"
    inferred_impact: str = "暂时无法判断"
    pending_verification: list[str] = Field(default_factory=list)
    risk_note: str = "需以官方公告和后续披露为准。"
    sectors: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    source_count: int = 0
    update_count: int = 0
    importance_score: float = Field(ge=0, le=100)
    confidence_score: float = Field(ge=0, le=100)
    analysis_method: Literal["rule", "hybrid", "llm"] = "rule"
    analysis_provider: str = ""
    analysis_model: str = ""
    analysis_updated_at: datetime | None = None
    llm_confidence_score: float | None = Field(default=None, ge=0, le=100)
    created_at: datetime
    sources: list[EventSource] = Field(default_factory=list)
    timeline: list[EventTimelineItem] = Field(default_factory=list)


class SectorHeatSnapshot(BaseModel):
    id: str
    name: str
    heat_score: float = Field(ge=0, le=100)
    heat_change: float | None = None
    event_count: int
    positive_event_count: int
    negative_event_count: int
    primary_catalyst: str
    updated_at: datetime


class EventDistributionItem(BaseModel):
    category: EventCategory
    count: int
    percentage: float


class DataFreshness(BaseModel):
    news_updated_at: datetime | None = None
    announcements_updated_at: datetime | None = None
    reports_updated_at: datetime | None = None


class EventDashboardResponse(BaseModel):
    top_events: list[MarketEvent]
    event_feed: list[MarketEvent]
    hot_sectors: list[SectorHeatSnapshot]
    event_distribution: list[EventDistributionItem]
    tracking_events: list[MarketEvent]
    generated_at: datetime
    data_freshness: DataFreshness
    data_mode: Literal["api", "mock"] = "api"


class EventListResponse(BaseModel):
    items: list[MarketEvent]
    next_cursor: str | None = None
    has_more: bool
    total_estimate: int
    generated_at: datetime


class NormalizedCandidate(BaseModel):
    source_id: str
    source_type: Literal["news", "announcement", "research", "social", "unknown"]
    title: str
    summary: str
    publisher: str
    published_at: datetime | None = None
    retrieved_at: datetime
    url: str | None = None
    entities: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    raw_metadata: dict = Field(default_factory=dict)
    content_hash: str
    is_primary_source: bool = False
    authority_score: float = Field(ge=0, le=100)
