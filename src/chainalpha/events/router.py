from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import os
import threading
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from chainalpha.events.models import (
    EventDashboardResponse,
    EventDistributionItem,
    EventListResponse,
    EventTimelineItem,
    MarketEvent,
    SectorHeatSnapshot,
)
from chainalpha.events.collector import event_collector
from chainalpha.events.service import event_service, model_dump, range_duration, utc_now


logger = logging.getLogger("chainalpha.events.api")
router = APIRouter(prefix="/api", tags=["financial-events"])
_SSE_CONNECTIONS = 0
_SSE_CONNECTION_LOCK = threading.Lock()
_SSE_MAX_CONNECTIONS = max(1, int(os.getenv("EVENT_SSE_MAX_CONNECTIONS", "50")))

Category = Literal["policy", "industry", "earnings", "company", "overseas", "risk"]
Status = Literal["unverified", "confirmed", "tracking", "completed", "corrected"]
Direction = Literal["positive", "negative", "mixed", "neutral", "uncertain"]
SortOrder = Literal["updated_desc", "importance_desc"]
TimeRange = Literal["1h", "6h", "24h", "3d", "7d", "30d", "all"]


@router.get("/events/dashboard", response_model=EventDashboardResponse)
def event_dashboard(
    category: Category | None = None,
    time_range: TimeRange = "24h",
    keyword: str = Query("", max_length=120),
    limit: int = Query(20, ge=1, le=50),
    timezone: str = Query("Asia/Shanghai", max_length=64),
) -> EventDashboardResponse:
    _validate_timezone(timezone)
    return _timed(
        "dashboard",
        lambda: event_service.dashboard(
            category=category,
            time_range=time_range,
            keyword=keyword,
            limit=limit,
        ),
    )


@router.get("/events", response_model=EventListResponse)
def list_events(
    category: Category | None = None,
    status: Status | None = None,
    impact_direction: Direction | None = None,
    time_range: TimeRange = "24h",
    keyword: str = Query("", max_length=120),
    sector: str = Query("", max_length=80),
    company: str = Query("", max_length=80),
    cursor: str | None = Query(None, max_length=120),
    limit: int = Query(20, ge=1, le=50),
    sort: SortOrder = "updated_desc",
) -> EventListResponse:
    try:
        return _timed(
            "list",
            lambda: event_service.list_events(
                category=category,
                status=status,
                impact_direction=impact_direction,
                time_range=time_range,
                keyword=keyword,
                sector=sector,
                company=company,
                cursor=cursor,
                limit=limit,
                sort=sort,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/events/distribution", response_model=list[EventDistributionItem])
def event_distribution(time_range: TimeRange = "24h") -> list[EventDistributionItem]:
    return _timed("distribution", lambda: event_service.distribution(time_range=time_range))


@router.get("/events/tracking", response_model=list[MarketEvent])
def tracking_events(
    time_range: TimeRange = "7d",
    limit: int = Query(5, ge=3, le=10),
) -> list[MarketEvent]:
    return _timed("tracking", lambda: event_service.tracking(time_range=time_range, limit=limit))


@router.get("/events/stream")
async def event_stream(request: Request) -> StreamingResponse:
    if not _acquire_sse_connection():
        raise HTTPException(status_code=429, detail="Too many event stream connections")

    async def generate():
        try:
            previous: dict[str, dict[str, str]] = {}
            heartbeat_at = time.monotonic()
            retry_ms = 3000
            yield f"retry: {retry_ms}\n\n"
            while not await request.is_disconnected():
                changed, events = await asyncio.to_thread(event_service.refresh_if_changed)
                current = {
                    event.id: {
                        "updated_at": event.updated_at.isoformat(),
                        "status": event.status,
                        "analysis_updated_at": (
                            event.analysis_updated_at.isoformat()
                            if event.analysis_updated_at
                            else ""
                        ),
                    }
                    for event in events
                }
                state_changed = bool(previous) and current != previous
                if changed or state_changed:
                    for event_id in current.keys() - previous.keys():
                        yield _sse(
                            "event.created",
                            {"event_id": event_id, "updated_at": current[event_id]["updated_at"]},
                        )
                    for event_id in current.keys() & previous.keys():
                        if current[event_id] != previous[event_id]:
                            old_state = previous[event_id]
                            new_state = current[event_id]
                            changed_fields = [
                                field
                                for field in ("updated_at", "status", "analysis_updated_at")
                                if old_state.get(field) != new_state.get(field)
                            ]
                            event_type = "event.updated"
                            if old_state.get("status") != new_state.get("status"):
                                event_type = (
                                    "event.corrected"
                                    if new_state.get("status") == "corrected"
                                    else "event.status_changed"
                                )
                            yield _sse(
                                event_type,
                                {
                                    "event_id": event_id,
                                    "updated_at": new_state["updated_at"],
                                    "changed_fields": changed_fields,
                                },
                            )
                    yield _sse("sector.heat_updated", {"updated_at": utc_now().isoformat()})
                previous = current
                if time.monotonic() - heartbeat_at >= 15:
                    yield _sse("heartbeat", {"timestamp": utc_now().isoformat()})
                    heartbeat_at = time.monotonic()
                await asyncio.sleep(3)
        finally:
            _release_sse_connection()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/events/refresh/status")
def event_refresh_status() -> dict:
    return event_collector.status()


@router.post("/events/refresh", status_code=202)
def request_event_refresh() -> dict:
    accepted, message = event_collector.request_refresh()
    if not accepted:
        status = 409 if event_collector.status().get("running") else 503
        raise HTTPException(status_code=status, detail=message)
    return {"accepted": True, "message": message, "status": event_collector.status()}


@router.get("/events/{event_id}", response_model=MarketEvent)
def event_detail(event_id: str) -> MarketEvent:
    event = event_service.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("/events/{event_id}/timeline", response_model=list[EventTimelineItem])
def event_timeline(event_id: str) -> list[EventTimelineItem]:
    timeline = event_service.timeline(event_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return timeline


@router.get("/sectors/hot", response_model=list[SectorHeatSnapshot])
def hot_sectors(
    time_range: TimeRange = "24h",
    limit: int = Query(5, ge=1, le=20),
    category: Category | None = None,
) -> list[SectorHeatSnapshot]:
    return _timed(
        "hot_sectors",
        lambda: event_service.hot_sectors(time_range=time_range, limit=limit, category=category),
    )


def _timed(stage: str, callback):
    started = time.perf_counter()
    try:
        value = callback()
    except Exception as exc:
        logger.exception(
            "event_api_failed",
            extra={
                "processing_stage": stage,
                "status": "error",
                "error_type": type(exc).__name__,
            },
        )
        raise
    logger.info(
        "event_api_completed",
        extra={
            "processing_stage": stage,
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    return value


def _validate_timezone(value: str) -> None:
    if value not in {"Asia/Shanghai", "UTC"}:
        raise HTTPException(status_code=400, detail="timezone must be Asia/Shanghai or UTC")


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _acquire_sse_connection() -> bool:
    global _SSE_CONNECTIONS
    with _SSE_CONNECTION_LOCK:
        if _SSE_CONNECTIONS >= _SSE_MAX_CONNECTIONS:
            return False
        _SSE_CONNECTIONS += 1
        logger.info("event_sse_connected", extra={"active_connections": _SSE_CONNECTIONS})
        return True


def _release_sse_connection() -> None:
    global _SSE_CONNECTIONS
    with _SSE_CONNECTION_LOCK:
        _SSE_CONNECTIONS = max(0, _SSE_CONNECTIONS - 1)
        logger.info("event_sse_disconnected", extra={"active_connections": _SSE_CONNECTIONS})
