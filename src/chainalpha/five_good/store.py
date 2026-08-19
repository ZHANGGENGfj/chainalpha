from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterable

from chainalpha.events.service import event_service, model_dump
from chainalpha.five_good.mock_data import build_demo_entries
from chainalpha.five_good.models import (
    CategoryOverview,
    EntryStatus,
    FiveGoodCategory,
    FiveGoodEntry,
    FiveGoodHistory,
    FiveGoodListResponse,
    FiveGoodOverview,
    FiveGoodStockDetail,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class FiveGoodStore:
    """JSON-backed repository; demo data is explicit and never persisted as production data."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(
            os.getenv("FIVE_GOOD_STORE_PATH", PROJECT_ROOT / "data" / "five_good" / "store.json")
        )
        self.demo_enabled = os.getenv("FIVE_GOOD_DEMO_DATA", "true").lower() in {
            "1", "true", "yes", "on",
        }
        self.live_event_hydration = os.getenv("FIVE_GOOD_LIVE_EVENT_HYDRATION", "false").lower() in {
            "1", "true", "yes", "on",
        }
        self._lock = RLock()
        self._entries: list[FiveGoodEntry] = []
        self._history: list[FiveGoodHistory] = []
        self._mtime_ns = -1
        self._data_mode = "api"
        self._metadata: dict = {}
        self.refresh(force=True)

    @property
    def data_mode(self) -> str:
        return self._data_mode

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            mtime = self.path.stat().st_mtime_ns if self.path.exists() else -1
            if not force and mtime == self._mtime_ns:
                return
            if self.path.exists():
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                self._metadata = dict(payload.get("metadata") or {})
                self._entries = [FiveGoodEntry.model_validate(row) for row in payload.get("entries", [])]
                self._history = [FiveGoodHistory.model_validate(row) for row in payload.get("history", [])]
                self._data_mode = "api"
            elif self.demo_enabled:
                self._entries, self._history = build_demo_entries()
                self._metadata = {}
                self._data_mode = "demo"
            else:
                self._entries, self._history = [], []
                self._metadata = {}
                self._data_mode = "api"
            self._mtime_ns = mtime

    def overview(self, limit: int = 6) -> FiveGoodOverview:
        self.refresh()
        entries = self._active_entries()
        categories = []
        category_counts: dict[str, int] = {}
        for category in FiveGoodCategory:
            rows = sorted(
                (item for item in entries if item.category == category),
                key=lambda item: (item.score, item.updated_at),
                reverse=True,
            )
            category_counts[category.value] = len(rows)
            categories.append(CategoryOverview(category=category, count=len(rows), entries=rows[:limit]))
        updated_at = max((item.updated_at for item in entries), default=datetime.now(timezone.utc))
        return FiveGoodOverview(
            coverage_count=len({item.stock_code for item in entries}),
            pool_count=len(entries),
            screened_universe_count=int(self._metadata.get("universe_count") or 0),
            eligible_count=int(self._metadata.get("eligible_count") or 0),
            category_counts=category_counts,
            categories=categories,
            updated_at=updated_at,
            data_mode=self._data_mode,
        )

    def list_entries(
        self,
        *,
        category: FiveGoodCategory | None = None,
        keyword: str = "",
        market: str = "",
        industry: str = "",
        sector: str = "",
        status: EntryStatus | None = None,
        updated_within: str = "all",
        sort: str = "score_desc",
        cursor: str | None = None,
        limit: int = 30,
    ) -> FiveGoodListResponse:
        self.refresh()
        rows = list(self._entries)
        if category:
            rows = [item for item in rows if item.category == category]
        if keyword:
            term = keyword.strip().lower()
            rows = [item for item in rows if term in item.stock_name.lower() or term in item.stock_code.lower()]
        if market:
            rows = [item for item in rows if item.market == market]
        if industry:
            rows = [item for item in rows if item.industry == industry]
        if sector:
            rows = [item for item in rows if item.sector == sector]
        if status:
            rows = [item for item in rows if item.status == status]
        cutoff = _updated_cutoff(updated_within)
        if cutoff:
            rows = [item for item in rows if item.updated_at >= cutoff]
        reverse = sort != "updated_asc"
        if sort.startswith("updated"):
            rows.sort(key=lambda item: item.updated_at, reverse=reverse)
        else:
            rows.sort(key=lambda item: (item.score, item.updated_at), reverse=True)
        offset = _decode_cursor(cursor)
        page = rows[offset : offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < len(rows)
        return FiveGoodListResponse(
            items=self._hydrate_stories(page),
            next_cursor=_encode_cursor(next_offset) if has_more else None,
            has_more=has_more,
            total_estimate=len(rows),
            industries=sorted({item.industry for item in self._entries if item.industry}),
            sectors=sorted({item.sector for item in self._entries if item.sector}),
            generated_at=datetime.now(timezone.utc),
            data_mode=self._data_mode,
        )

    def stock_detail(self, stock_code: str) -> FiveGoodStockDetail | None:
        self.refresh()
        entries = self._hydrate_stories([item for item in self._entries if item.stock_code == stock_code])
        if not entries:
            return None
        first = entries[0]
        histories = sorted(
            (item for item in self._history if item.stock_code == stock_code),
            key=lambda item: item.created_at,
            reverse=True,
        )
        return FiveGoodStockDetail(
            stock_code=first.stock_code,
            stock_name=first.stock_name,
            market=first.market,
            industry=first.industry,
            sub_industry=first.sub_industry,
            tags=sorted({tag for item in entries for tag in item.tags}),
            entries=entries,
            history=histories,
            data_mode=self._data_mode,
        )

    def stock_history(self, stock_code: str) -> list[FiveGoodHistory]:
        self.refresh()
        return sorted(
            (item for item in self._history if item.stock_code == stock_code),
            key=lambda item: item.created_at,
            reverse=True,
        )

    def save(self) -> None:
        """Persist real/imported entries; callers own validation and authorization."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "metadata": self._metadata,
                "entries": [model_dump(item) for item in self._entries],
                "history": [model_dump(item) for item in self._history],
            }
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            temporary.replace(self.path)
            self._mtime_ns = self.path.stat().st_mtime_ns
            self._data_mode = "api"

    def _active_entries(self) -> list[FiveGoodEntry]:
        return [item for item in self._entries if item.status in {EntryStatus.ACTIVE, EntryStatus.WATCHING}]

    def _hydrate_stories(self, entries: list[FiveGoodEntry]) -> list[FiveGoodEntry]:
        # Event evidence is attached by the screening/background pipeline and
        # persisted in store.json. A read endpoint must not rebuild the event
        # corpus; optional live hydration is retained only for diagnostics.
        if not self.live_event_hydration or not any(entry.category == FiveGoodCategory.GOOD_STORY for entry in entries):
            return entries
        _, events = event_service.refresh_if_changed()
        by_id = {event.id: event for event in events}
        by_company: dict[str, list] = {}
        for event in events:
            for company in event.companies:
                by_company.setdefault(company, []).append(event)
        for company_events in by_company.values():
            company_events.sort(
                key=lambda event: (event.importance_score, event.confidence_score, event.updated_at),
                reverse=True,
            )
        return [self._hydrate_story(entry, by_id, by_company) for entry in entries]

    def _hydrate_story(self, entry: FiveGoodEntry, by_id: dict, by_company: dict[str, list]) -> FiveGoodEntry:
        if entry.category != FiveGoodCategory.GOOD_STORY:
            return entry
        data = dict(entry.category_data)
        event_id = str(data.get("event_id") or "")
        event = by_id.get(event_id) if event_id else None
        if event is None:
            event = next(iter(by_company.get(entry.stock_name, [])), None)
        if event is None:
            return entry
        data.update(
            {
                "event_id": event.id,
                "event_title": event.title,
                "event_type": event.category,
                "event_status": event.status,
                "impact_direction": event.impact_direction,
                "confidence": event.confidence_score,
                "event_occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                "event_updated_at": event.updated_at.isoformat(),
                "related_sectors": event.sectors,
                "event_source": _event_source_name(event),
            }
        )
        return entry.model_copy(update={"category_data": data})


def _event_source_name(event: object) -> str:
    sources = getattr(event, "sources", None) or []
    publishers = [str(getattr(source, "publisher", "")).strip() for source in sources]
    publishers = [publisher for publisher in publishers if publisher]
    if publishers:
        return "、".join(dict.fromkeys(publishers[:3]))
    return str(getattr(event, "publisher", "")).strip()


def _updated_cutoff(value: str) -> datetime | None:
    days = {"today": 1, "3d": 3, "7d": 7}.get(value)
    return datetime.now(timezone.utc) - timedelta(days=days) if days else None


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return max(0, int(base64.urlsafe_b64decode(padded).decode()))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid cursor") from None


five_good_store = FiveGoodStore()
