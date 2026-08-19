from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

from chainalpha.config import load_settings
from chainalpha.events.models import MarketEvent
from chainalpha.nlp.llm_client import LLMClient


logger = logging.getLogger("chainalpha.events.llm")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_CATEGORIES = {"policy", "industry", "earnings", "company", "overseas", "risk"}
ALLOWED_DIRECTIONS = {"positive", "negative", "mixed", "neutral", "uncertain"}
ALLOWED_HORIZONS = {"intraday", "short_term", "medium_term", "long_term"}


class EventLLMAnalyzer:
    """Cache-backed AI enrichment for clustered events; never runs on page reads."""

    def __init__(self, *, client: LLMClient | None = None, cache_path: str | Path | None = None) -> None:
        self.settings = load_settings()
        self.client = client or LLMClient(self.settings)
        self.enabled = _env_bool("EVENT_LLM_ANALYSIS", True)
        self.batch_size = max(1, min(10, int(os.getenv("EVENT_LLM_BATCH_SIZE", "5"))))
        self.max_events = max(1, int(os.getenv("EVENT_LLM_MAX_EVENTS_PER_RUN", "20")))
        self.min_importance = float(os.getenv("EVENT_LLM_MIN_IMPORTANCE", "35"))
        self.timeout = max(20, int(os.getenv("EVENT_LLM_TIMEOUT_SECONDS", "90")))
        self.cache_path = Path(
            cache_path
            or os.getenv(
                "EVENT_LLM_CACHE_PATH",
                str(PROJECT_ROOT / "data" / "event_store" / "llm_analysis.json"),
            )
        )

    def enrich(self, events: list[MarketEvent]) -> tuple[list[MarketEvent], dict[str, Any]]:
        status: dict[str, Any] = {
            "enabled": self.enabled,
            "provider": self.settings.llm_provider,
            "model": self.settings.llm_model,
            "cached": 0,
            "analyzed": 0,
            "failed": 0,
            "pending": 0,
            "message": "",
        }
        if not self.enabled:
            status["message"] = "AI event analysis is disabled"
            return events, status
        if not self.settings.llm_enabled:
            status["message"] = f"{self.settings.llm_provider.upper()} API key is not configured"
            return events, status

        cache = self._load_cache()
        entries = cache.setdefault("entries", {})
        output = {event.id: event for event in events}
        pending: list[MarketEvent] = []
        for event in events:
            signature = _event_signature(event)
            cached = entries.get(event.id) or {}
            if cached.get("signature") == signature and isinstance(cached.get("analysis"), dict):
                output[event.id] = _apply_analysis(
                    event,
                    cached["analysis"],
                    str(cached.get("provider") or self.settings.llm_provider),
                    str(cached.get("model") or self.settings.llm_model),
                    analyzed_at=cached.get("analyzed_at"),
                )
                status["cached"] += 1
            elif event.importance_score >= self.min_importance:
                pending.append(event)

        pending.sort(key=lambda event: (event.importance_score, event.updated_at), reverse=True)
        selected = pending[: self.max_events]
        status["pending"] = max(0, len(pending) - len(selected))
        for offset in range(0, len(selected), self.batch_size):
            batch = selected[offset : offset + self.batch_size]
            try:
                analyses = self._analyze_batch(batch)
            except Exception as exc:  # noqa: BLE001
                status["failed"] += len(batch)
                status["message"] = f"{type(exc).__name__}: {exc}"
                logger.exception("event_llm_batch_failed", extra={"event_count": len(batch)})
                continue
            for event in batch:
                analysis = analyses.get(event.id)
                if not analysis:
                    status["failed"] += 1
                    continue
                analyzed_at = datetime.now(timezone.utc).isoformat()
                output[event.id] = _apply_analysis(
                    event,
                    analysis,
                    self.settings.llm_provider,
                    self.settings.llm_model,
                    analyzed_at=analyzed_at,
                )
                entries[event.id] = {
                    "signature": _event_signature(event),
                    "analyzed_at": analyzed_at,
                    "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                    "analysis": analysis,
                }
                status["analyzed"] += 1

        cache["version"] = "event-llm-v1"
        cache["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_cache(cache)
        if not status["message"]:
            status["message"] = (
                f"AI analyzed {status['analyzed']} events; "
                f"reused {status['cached']} cached analyses"
            )
        return [output[event.id] for event in events], status

    def _analyze_batch(self, events: list[MarketEvent]) -> dict[str, dict[str, Any]]:
        payload = {"events": [_event_payload(event) for event in events]}
        system = """你是金融实时事件分析器。只能依据输入中的标题、摘要、来源和时间分析，不得补充外部事实，不得编造订单、客户、金额、业绩或政策。
严格区分可核验事实与分析推断。来源不足时降低 analysis_confidence，并把疑点写入 pending_verification。
返回严格 JSON 对象：
{"events":[{"event_id":"","category":"policy|industry|earnings|company|overseas|risk","impact_direction":"positive|negative|mixed|neutral|uncertain","impact_horizon":"intraday|short_term|medium_term|long_term","fact_summary":"仅陈述输入支持的事实","importance_reason":"为什么值得关注","direct_impact":"一阶影响，不确定则明确说明","inferred_impact":"二阶推断并写明成立条件","pending_verification":["最多4项"],"risk_note":"误读和反向风险","companies":["输入中明确出现的公司"],"sectors":["相关行业"],"concepts":["相关概念"],"materiality_score":0,"analysis_confidence":0}]}
materiality_score 和 analysis_confidence 范围为 0-100。必须为每个输入 event_id 返回一项。"""
        response: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self.client.chat_json(
                    system,
                    json.dumps(payload, ensure_ascii=False),
                    timeout=self.timeout,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == 0:
                    time.sleep(2)
        if response is None:
            raise last_error or RuntimeError("empty LLM response")

        rows = response.get("events") or []
        events_by_id = {event.id: event for event in events}
        return {
            str(row.get("event_id")): _sanitize_analysis(row, events_by_id[str(row["event_id"])])
            for row in rows
            if isinstance(row, dict) and str(row.get("event_id")) in events_by_id
        }

    def _load_cache(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"entries": {}}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"entries": {}}

    def _save_cache(self, payload: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.cache_path)


def _event_payload(event: MarketEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "title": event.title,
        "rule_category": event.category,
        "rule_impact_direction": event.impact_direction,
        "companies": event.companies,
        "sectors": event.sectors,
        "sources": [
            {
                "publisher": source.publisher,
                "source_type": source.source_type,
                "title": source.title,
                "summary": source.summary[:500],
                "published_at": source.published_at.isoformat() if source.published_at else None,
                "is_primary_source": source.is_primary_source,
            }
            for source in event.sources[:4]
        ],
    }


def _event_signature(event: MarketEvent) -> str:
    payload = "|".join([event.title, *(source.content_hash for source in event.sources)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sanitize_analysis(row: dict[str, Any], event: MarketEvent) -> dict[str, Any]:
    source_text = " ".join([event.title, *(f"{source.title} {source.summary}" for source in event.sources)])
    companies = [
        str(value).strip()
        for value in row.get("companies", [])
        if str(value).strip() and str(value).strip() in source_text
    ]
    return {
        "category": row.get("category") if row.get("category") in ALLOWED_CATEGORIES else event.category,
        "impact_direction": (
            row.get("impact_direction")
            if row.get("impact_direction") in ALLOWED_DIRECTIONS
            else event.impact_direction
        ),
        "impact_horizon": (
            row.get("impact_horizon")
            if row.get("impact_horizon") in ALLOWED_HORIZONS
            else event.impact_horizon
        ),
        "fact_summary": _text(row.get("fact_summary"), event.fact_summary, 800),
        "importance_reason": _text(row.get("importance_reason"), event.importance_reason, 600),
        "direct_impact": _text(row.get("direct_impact"), event.direct_impact, 600),
        "inferred_impact": _text(row.get("inferred_impact"), event.inferred_impact, 600),
        "pending_verification": [
            _text(item, "", 180)
            for item in (row.get("pending_verification") or [])[:4]
            if _text(item, "", 180)
        ],
        "risk_note": _text(row.get("risk_note"), event.risk_note, 500),
        "companies": list(dict.fromkeys([*event.companies, *companies]))[:12],
        "sectors": _string_list(row.get("sectors"), event.sectors, 8),
        "concepts": _string_list(row.get("concepts"), event.concepts, 8),
        "materiality_score": _score(row.get("materiality_score"), event.importance_score),
        "analysis_confidence": _score(row.get("analysis_confidence"), 50),
    }


def _apply_analysis(
    event: MarketEvent,
    analysis: dict[str, Any],
    provider: str,
    model: str,
    *,
    analyzed_at: str | datetime | None = None,
) -> MarketEvent:
    materiality = _score(analysis.get("materiality_score"), event.importance_score)
    importance = round(event.importance_score * 0.7 + materiality * 0.3, 2)
    if isinstance(analyzed_at, str):
        try:
            analyzed_at = datetime.fromisoformat(analyzed_at.replace("Z", "+00:00"))
        except ValueError:
            analyzed_at = None
    return event.model_copy(
        update={
            "category": analysis.get("category", event.category),
            "impact_direction": analysis.get("impact_direction", event.impact_direction),
            "impact_horizon": analysis.get("impact_horizon", event.impact_horizon),
            "fact_summary": analysis.get("fact_summary", event.fact_summary),
            "importance_reason": analysis.get("importance_reason", event.importance_reason),
            "direct_impact": analysis.get("direct_impact", event.direct_impact),
            "inferred_impact": analysis.get("inferred_impact", event.inferred_impact),
            "pending_verification": analysis.get("pending_verification", event.pending_verification),
            "risk_note": analysis.get("risk_note", event.risk_note),
            "companies": analysis.get("companies", event.companies),
            "sectors": analysis.get("sectors", event.sectors),
            "concepts": analysis.get("concepts", event.concepts),
            "importance_score": importance,
            "analysis_method": "hybrid",
            "analysis_provider": provider,
            "analysis_model": model,
            "analysis_updated_at": analyzed_at or datetime.now(timezone.utc),
            "llm_confidence_score": _score(analysis.get("analysis_confidence"), 50),
        }
    )


def _text(value: Any, fallback: str, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return (text or fallback)[:limit]


def _string_list(value: Any, fallback: list[str], limit: int) -> list[str]:
    if not isinstance(value, list):
        return fallback
    rows = [str(item).strip() for item in value if str(item).strip()]
    return list(dict.fromkeys([*fallback, *rows]))[:limit]


def _score(value: Any, fallback: float) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return max(0.0, min(100.0, float(fallback)))


def _env_bool(name: str, default: bool) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


event_llm_analyzer = EventLLMAnalyzer()
