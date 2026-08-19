from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Iterable

from chainalpha.events.adapters import load_existing_candidates, normalize_title
from chainalpha.events.models import (
    DataFreshness,
    EventDashboardResponse,
    EventDistributionItem,
    EventListResponse,
    EventSource,
    EventTimelineItem,
    MarketEvent,
    NormalizedCandidate,
    SectorHeatSnapshot,
)
from chainalpha.events.scoring import calculate_confidence_score, calculate_importance_score


logger = logging.getLogger("chainalpha.events")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_ROOT = Path(
    os.getenv("EVENT_SOURCE_ROOT", PROJECT_ROOT / "third_party" / "MediaCrawler-main" / "data")
).expanduser()
DEFAULT_STORE_PATH = Path(
    os.getenv("EVENT_STORE_PATH", PROJECT_ROOT / "data" / "event_store" / "events.json")
).expanduser()

CATEGORY_TERMS = {
    "policy": ("政策", "通知", "意见", "办法", "监管", "补贴", "规划"),
    "earnings": ("财报", "年报", "季报", "业绩", "营收", "净利润", "盈利预测"),
    "risk": ("风险", "违约", "处罚", "调查", "减持", "暴雷", "停牌核查"),
    "overseas": ("海外", "美国", "欧洲", "美联储", "出口管制", "关税"),
    "company": ("公司", "订单", "合同", "中标", "收购", "回购", "增持", "公告"),
}
POSITIVE_TERMS = ("增长", "预增", "中标", "订单", "上调", "突破", "回购", "增持")
NEGATIVE_TERMS = ("下降", "预亏", "减持", "处罚", "违约", "下调", "风险", "终止")


class EventService:
    def __init__(
        self,
        *,
        source_root: Path = DEFAULT_SOURCE_ROOT,
        store_path: Path = DEFAULT_STORE_PATH,
        cache_ttl_seconds: int = 20,
        similarity_threshold: float | None = None,
        cluster_window_hours: int | None = None,
    ) -> None:
        self.source_root = source_root
        self.store_path = store_path
        self.cache_ttl_seconds = cache_ttl_seconds
        self.similarity_threshold = similarity_threshold or float(os.getenv("EVENT_SIMILARITY_THRESHOLD", "0.84"))
        self.cluster_window_hours = cluster_window_hours or int(os.getenv("EVENT_CLUSTER_WINDOW_HOURS", "72"))
        self._events: list[MarketEvent] = []
        self._freshness = DataFreshness()
        self._loaded_at = 0.0
        self._fingerprint = ""
        self._lock = threading.RLock()

    def list_events(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        impact_direction: str | None = None,
        time_range: str = "24h",
        keyword: str = "",
        sector: str = "",
        company: str = "",
        cursor: str | None = None,
        limit: int = 20,
        sort: str = "updated_desc",
    ) -> EventListResponse:
        events = self._filtered_events(
            category=category,
            status=status,
            impact_direction=impact_direction,
            time_range=time_range,
            keyword=keyword,
            sector=sector,
            company=company,
        )
        if sort == "importance_desc":
            events.sort(key=lambda event: (event.importance_score, event.updated_at), reverse=True)
        else:
            events.sort(key=lambda event: event.updated_at, reverse=True)
        offset = decode_cursor(cursor)
        page = events[offset : offset + limit]
        next_offset = offset + len(page)
        return EventListResponse(
            items=[without_nested_details(event) for event in page],
            next_cursor=encode_cursor(next_offset) if next_offset < len(events) else None,
            has_more=next_offset < len(events),
            total_estimate=len(events),
            generated_at=utc_now(),
        )

    def dashboard(
        self,
        *,
        category: str | None = None,
        time_range: str = "24h",
        keyword: str = "",
        limit: int = 20,
    ) -> EventDashboardResponse:
        events = self._filtered_events(category=category, time_range=time_range, keyword=keyword)
        event_feed = sorted(events, key=lambda event: event.updated_at, reverse=True)[:limit]
        top_events = sorted(events, key=lambda event: event.importance_score, reverse=True)[:5]
        tracking = sorted(
            (event for event in events if event.status == "tracking"),
            key=lambda event: (event.updated_at, event.update_count, event.importance_score),
            reverse=True,
        )[:5]
        return EventDashboardResponse(
            top_events=[without_nested_details(event) for event in top_events],
            event_feed=[without_nested_details(event) for event in event_feed],
            hot_sectors=self.hot_sectors(time_range=time_range, category=category, limit=5),
            event_distribution=self.distribution(time_range=time_range),
            tracking_events=[without_nested_details(event) for event in tracking],
            generated_at=utc_now(),
            data_freshness=self._freshness,
            data_mode="api",
        )

    def get_event(self, event_id: str) -> MarketEvent | None:
        self._ensure_loaded()
        return next((event for event in self._events if event.id == event_id), None)

    def timeline(self, event_id: str) -> list[EventTimelineItem] | None:
        event = self.get_event(event_id)
        return event.timeline if event else None

    def hot_sectors(
        self,
        *,
        time_range: str = "24h",
        category: str | None = None,
        limit: int = 10,
    ) -> list[SectorHeatSnapshot]:
        events = self._filtered_events(category=category, time_range=time_range)
        previous_events = self._previous_window_events(time_range, category)
        current_scores = _sector_scores(events)
        previous_scores = _sector_scores(previous_events)
        snapshots: list[SectorHeatSnapshot] = []
        for sector, payload in current_scores.items():
            previous = previous_scores.get(sector)
            snapshots.append(
                SectorHeatSnapshot(
                    id=hashlib.sha1(sector.encode("utf-8")).hexdigest()[:12],
                    name=sector,
                    heat_score=payload["score"],
                    heat_change=round(payload["score"] - previous["score"], 2) if previous else None,
                    event_count=payload["event_count"],
                    positive_event_count=payload["positive"],
                    negative_event_count=payload["negative"],
                    primary_catalyst=payload["primary_catalyst"],
                    updated_at=payload["updated_at"],
                )
            )
        return sorted(snapshots, key=lambda item: item.heat_score, reverse=True)[:limit]

    def distribution(self, *, time_range: str = "24h") -> list[EventDistributionItem]:
        events = self._filtered_events(time_range=time_range)
        total = len(events)
        categories = ("policy", "industry", "earnings", "company", "overseas", "risk")
        return [
            EventDistributionItem(
                category=category,
                count=sum(event.category == category for event in events),
                percentage=round(
                    sum(event.category == category for event in events) / total * 100, 2
                ) if total else 0,
            )
            for category in categories
        ]

    def tracking(self, *, time_range: str = "7d", limit: int = 5) -> list[MarketEvent]:
        events = self._filtered_events(status="tracking", time_range=time_range)
        return [
            without_nested_details(event)
            for event in sorted(
                events,
                key=lambda event: (event.updated_at, event.update_count, event.importance_score),
                reverse=True,
            )[:limit]
        ]

    def refresh_if_changed(self, *, force: bool = False) -> tuple[bool, list[MarketEvent]]:
        with self._lock:
            expired = time.monotonic() - self._loaded_at >= self.cache_ttl_seconds
            # TTL controls how often the filesystem fingerprint is checked. It
            # must not force an expensive reload when source files are unchanged.
            if not force and self._events and not expired:
                return False, self._events
            fingerprint = self.source_fingerprint()
            if not force and self._events and fingerprint == self._fingerprint:
                self._loaded_at = time.monotonic()
                return False, self._events
            changed = fingerprint != self._fingerprint
            candidates, freshness = load_existing_candidates(self.source_root)
            started = time.perf_counter()
            events = cluster_candidates(
                candidates,
                similarity_threshold=self.similarity_threshold,
                cluster_window_hours=self.cluster_window_hours,
            )
            self._events = events
            self._freshness = DataFreshness(**freshness)
            self._fingerprint = fingerprint
            self._loaded_at = time.monotonic()
            self._persist_snapshot()
            logger.info(
                "event_snapshot_refreshed",
                extra={
                    "processing_stage": "cluster",
                    "status": "ok",
                    "candidate_count": len(candidates),
                    "event_count": len(events),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return changed, events

    def replace_events(self, events: list[MarketEvent], *, persist: bool = True) -> None:
        """Install enriched events without rebuilding source clusters."""
        with self._lock:
            self._events = list(events)
            self._loaded_at = time.monotonic()
            if persist:
                self._persist_snapshot()

    def source_fingerprint(self) -> str:
        parts: list[str] = []
        for platform in ("finance_news", "cninfo_reports", "broker_reports"):
            base = self.source_root / platform
            if not base.exists():
                continue
            for path in base.glob("processed/**/*"):
                if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}:
                    stat = path.stat()
                    parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()

    def _filtered_events(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        impact_direction: str | None = None,
        time_range: str = "24h",
        keyword: str = "",
        sector: str = "",
        company: str = "",
    ) -> list[MarketEvent]:
        self._ensure_loaded()
        threshold = range_threshold(time_range)
        query = keyword.strip().lower()[:120]
        output: list[MarketEvent] = []
        for event in self._events:
            event_time = event.occurred_at or event.updated_at
            if threshold and event_time < threshold:
                continue
            if category and category != "all" and event.category != category:
                continue
            if status and event.status != status:
                continue
            if impact_direction and event.impact_direction != impact_direction:
                continue
            if sector and sector not in event.sectors:
                continue
            if company and company not in event.companies:
                continue
            if query:
                searchable = " ".join(
                    [
                        event.title,
                        event.fact_summary,
                        event.importance_reason,
                        *event.sectors,
                        *event.concepts,
                        *event.companies,
                    ]
                ).lower()
                if query not in searchable:
                    continue
            output.append(event)
        return output

    def _previous_window_events(self, time_range: str, category: str | None) -> list[MarketEvent]:
        self._ensure_loaded()
        duration = range_duration(time_range)
        if duration is None:
            return []
        end = utc_now() - duration
        start = end - duration
        return [
            event
            for event in self._events
            if start <= (event.occurred_at or event.updated_at) < end
            and (not category or category == "all" or event.category == category)
        ]

    def _ensure_loaded(self) -> None:
        self.refresh_if_changed()

    def _persist_snapshot(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": utc_now().isoformat(),
            "source_fingerprint": self._fingerprint,
            "events": [model_dump(event, mode="json") for event in self._events],
            "data_freshness": model_dump(self._freshness, mode="json"),
        }
        temporary = self.store_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.store_path)


def cluster_candidates(
    candidates: list[NormalizedCandidate],
    *,
    similarity_threshold: float = 0.84,
    cluster_window_hours: int = 72,
) -> list[MarketEvent]:
    deduped = dedup_candidates(candidates)
    clusters: list[list[NormalizedCandidate]] = []
    for candidate in sorted(deduped, key=candidate_sort_time):
        matching: list[NormalizedCandidate] | None = None
        for cluster in reversed(clusters[-100:]):
            if candidates_match(candidate, cluster[-1], similarity_threshold, cluster_window_hours):
                matching = cluster
                break
        if matching is None:
            clusters.append([candidate])
        else:
            matching.append(candidate)
    return [build_event(cluster) for cluster in clusters]


def dedup_candidates(candidates: Iterable[NormalizedCandidate]) -> list[NormalizedCandidate]:
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    output: list[NormalizedCandidate] = []
    for candidate in candidates:
        if candidate.url and candidate.url in seen_urls:
            continue
        if candidate.content_hash in seen_hashes:
            continue
        if candidate.url:
            seen_urls.add(candidate.url)
        seen_hashes.add(candidate.content_hash)
        output.append(candidate)
    return output


def candidates_match(
    left: NormalizedCandidate,
    right: NormalizedCandidate,
    threshold: float,
    window_hours: int,
) -> bool:
    left_time = left.published_at or left.retrieved_at
    right_time = right.published_at or right.retrieved_at
    if abs((left_time - right_time).total_seconds()) > window_hours * 3600:
        return False
    title_similarity = SequenceMatcher(None, normalize_title(left.title), normalize_title(right.title)).ratio()
    entity_overlap = bool(set(left.entities) & set(right.entities))
    sector_overlap = bool(set(left.sectors) & set(right.sectors))
    return title_similarity >= threshold or (
        title_similarity >= threshold - 0.12 and (entity_overlap or sector_overlap)
    )


def build_event(cluster: list[NormalizedCandidate]) -> MarketEvent:
    primary = max(cluster, key=lambda item: (item.is_primary_source, item.authority_score, len(item.summary)))
    canonical = max(cluster, key=lambda item: (item.authority_score, len(item.title)))
    event_id = "evt_" + hashlib.sha1(
        f"{normalize_title(canonical.title)}|{candidate_sort_time(cluster[0]).date()}".encode("utf-8")
    ).hexdigest()[:18]
    sources = [
        EventSource(
            id=candidate.source_id,
            event_id=event_id,
            source_type=candidate.source_type,
            publisher=candidate.publisher,
            title=candidate.title,
            url=candidate.url,
            summary=candidate.summary[:280],
            published_at=candidate.published_at,
            retrieved_at=candidate.retrieved_at,
            content_hash=candidate.content_hash,
            is_primary_source=candidate.is_primary_source,
            authority_score=candidate.authority_score,
            time_missing=candidate.published_at is None,
        )
        for candidate in cluster
    ]
    timestamps = [candidate_sort_time(candidate) for candidate in cluster]
    published = [candidate.published_at for candidate in cluster if candidate.published_at]
    occurred_at = min(published) if published else min(candidate.retrieved_at for candidate in cluster)
    companies = _unique(value for candidate in cluster for value in candidate.entities if not value.isdigit())
    sectors = _unique(value for candidate in cluster for value in candidate.sectors)
    concepts = _unique(value for candidate in cluster for value in candidate.concepts)
    category = infer_category(f"{canonical.title} {primary.summary}", primary.source_type)
    impact = infer_impact(f"{canonical.title} {primary.summary}")
    status = infer_status(sources, max(timestamps))
    timeline = [
        EventTimelineItem(
            id=f"{event_id}_{index + 1}",
            event_id=event_id,
            occurred_at=candidate.published_at or candidate.retrieved_at,
            title="官方披露" if candidate.is_primary_source else "新增来源",
            summary=f"{candidate.publisher}：{candidate.title}",
            status="confirmed" if candidate.is_primary_source else "unverified",
            source_ids=[candidate.source_id],
            created_at=candidate.retrieved_at,
        )
        for index, candidate in enumerate(sorted(cluster, key=candidate_sort_time))
    ]
    updated = max(timestamps)
    importance = calculate_importance_score(
        sources,
        company_count=len(companies),
        sector_count=len(sectors),
        update_count=max(0, len(cluster) - 1),
        updated_at=updated,
    )
    confidence = calculate_confidence_score(
        sources,
        required_fields_complete=bool(canonical.title and primary.summary and (published or primary.retrieved_at)),
    )
    sector_text = "、".join(sectors[:3]) or "相关行业"
    return MarketEvent(
        id=event_id,
        title=canonical.title,
        category=category,
        status=status,
        impact_direction=impact,
        impact_horizon="short_term",
        occurred_at=occurred_at,
        occurred_at_estimated=not bool(published),
        first_seen_at=min(candidate.retrieved_at for candidate in cluster),
        updated_at=updated,
        fact_summary=primary.summary[:600],
        importance_reason=f"该信息涉及{sector_text}，当前由 {len(sources)} 个独立来源支持；影响仍需结合后续公告和经营数据验证。",
        direct_impact=f"可能直接影响{sector_text}的信息预期，暂不构成确定性经营结论。",
        inferred_impact="暂时无法判断；需要进一步核验订单、价格、产能或财务数据。",
        pending_verification=[
            "核验是否存在公司或监管机构的一手披露。",
            "核验事件对收入、利润或现金流的可量化影响。",
        ],
        risk_note="来源可能存在摘要口径差异；请以原文和官方披露为准。",
        sectors=sectors,
        concepts=concepts,
        companies=companies,
        source_count=len(sources),
        update_count=max(0, len(timeline) - 1),
        importance_score=importance,
        confidence_score=confidence,
        created_at=min(candidate.retrieved_at for candidate in cluster),
        sources=sources,
        timeline=timeline,
    )


def infer_category(text: str, source_type: str) -> str:
    if source_type == "announcement":
        if any(term in text for term in CATEGORY_TERMS["earnings"]):
            return "earnings"
        return "company"
    for category in ("risk", "policy", "earnings", "overseas", "company"):
        if any(term.lower() in text.lower() for term in CATEGORY_TERMS[category]):
            return category
    return "industry"


def infer_impact(text: str) -> str:
    positive = sum(term in text for term in POSITIVE_TERMS)
    negative = sum(term in text for term in NEGATIVE_TERMS)
    if positive and negative:
        return "mixed"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return "uncertain"


def infer_status(sources: list[EventSource], updated_at: datetime) -> str:
    if any(source.is_primary_source for source in sources):
        return "confirmed"
    if len({source.publisher for source in sources}) >= 2 and updated_at >= utc_now() - timedelta(days=7):
        return "tracking"
    return "unverified"


def _sector_scores(events: list[MarketEvent]) -> dict[str, dict]:
    grouped: dict[str, list[MarketEvent]] = defaultdict(list)
    for event in events:
        for sector in set(event.sectors):
            grouped[sector].append(event)
    output: dict[str, dict] = {}
    for sector, rows in grouped.items():
        authority = sum(event.confidence_score for event in rows) / max(len(rows), 1)
        importance = sum(event.importance_score for event in rows) / max(len(rows), 1)
        count_factor = min(len(rows) / 6, 1) * 20
        source_factor = min(sum(event.source_count for event in rows) / 12, 1) * 15
        company_factor = min(len({company for event in rows for company in event.companies}) / 8, 1) * 10
        score = min(100, authority * 0.25 + importance * 0.3 + count_factor + source_factor + company_factor)
        catalyst = max(rows, key=lambda event: event.importance_score)
        output[sector] = {
            "score": round(score, 2),
            "event_count": len(rows),
            "positive": sum(event.impact_direction == "positive" for event in rows),
            "negative": sum(event.impact_direction == "negative" for event in rows),
            "primary_catalyst": catalyst.title,
            "updated_at": max(event.updated_at for event in rows),
        }
    return output


def without_nested_details(event: MarketEvent) -> MarketEvent:
    data = model_dump(event)
    data["sources"] = []
    data["timeline"] = []
    return MarketEvent(**data)


def range_duration(value: str) -> timedelta | None:
    mapping = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "3d": timedelta(days=3),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "all": None,
    }
    if value not in mapping:
        raise ValueError(f"unsupported time_range: {value}")
    return mapping[value]


def range_threshold(value: str) -> datetime | None:
    duration = range_duration(value)
    return utc_now() - duration if duration else None


def candidate_sort_time(candidate: NormalizedCandidate) -> datetime:
    return candidate.published_at or candidate.retrieved_at


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode("ascii")).decode("ascii")


def decode_cursor(value: str | None) -> int:
    if not value:
        return 0
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii")).decode("ascii")
        prefix, number = decoded.split(":", 1)
        offset = int(number)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid cursor") from None
    if prefix != "offset" or offset < 0:
        raise ValueError("invalid cursor")
    return offset


def model_dump(model, **kwargs):
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    kwargs.pop("mode", None)
    return model.dict(**kwargs)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


event_service = EventService()
