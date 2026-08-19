from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FiveGoodCategory(str, Enum):
    GOOD_COMPANY = "good_company"
    GOOD_TIMING = "good_timing"
    GOOD_PRICE = "good_price"
    GOOD_STORY = "good_story"
    GOOD_STOCK = "good_stock"


class EntryStatus(str, Enum):
    ACTIVE = "active"
    WATCHING = "watching"
    EXPIRED = "expired"
    REMOVED = "removed"


class HistoryAction(str, Enum):
    ADDED = "added"
    UPDATED = "updated"
    REMOVED = "removed"
    EXPIRED = "expired"
    REACTIVATED = "reactivated"


class FiveGoodEntry(BaseModel):
    id: str
    stock_code: str
    stock_name: str
    market: str
    category: FiveGoodCategory
    industry: str
    sub_industry: str = ""
    sector: str = ""
    score: float = Field(ge=0, le=100)
    status: EntryStatus = EntryStatus.ACTIVE
    selection_reason: str
    risk_summary: str
    tags: list[str] = Field(default_factory=list)
    valid_from: datetime
    valid_until: datetime | None = None
    as_of_date: date
    created_at: datetime
    updated_at: datetime
    category_data: dict[str, Any] = Field(default_factory=dict)


class FiveGoodHistory(BaseModel):
    id: str
    entry_id: str
    stock_code: str
    category: FiveGoodCategory
    action: HistoryAction
    previous_score: float | None = None
    new_score: float | None = None
    reason: str
    as_of_date: date
    created_at: datetime


class CategoryOverview(BaseModel):
    category: FiveGoodCategory
    count: int
    entries: list[FiveGoodEntry]


class FiveGoodOverview(BaseModel):
    coverage_count: int
    pool_count: int
    screened_universe_count: int = 0
    eligible_count: int = 0
    category_counts: dict[str, int]
    categories: list[CategoryOverview]
    updated_at: datetime
    data_mode: str = "api"


class FiveGoodListResponse(BaseModel):
    items: list[FiveGoodEntry]
    next_cursor: str | None = None
    has_more: bool
    total_estimate: int
    industries: list[str]
    sectors: list[str]
    generated_at: datetime
    data_mode: str = "api"


class FiveGoodStockDetail(BaseModel):
    stock_code: str
    stock_name: str
    market: str
    industry: str
    sub_industry: str = ""
    tags: list[str]
    entries: list[FiveGoodEntry]
    history: list[FiveGoodHistory]
    data_mode: str = "api"
