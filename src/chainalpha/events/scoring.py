from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from chainalpha.events.models import EventSource


@dataclass(frozen=True)
class ScoringConfig:
    official_source_weight: float = 24
    independent_source_weight: float = 18
    authority_weight: float = 18
    entity_weight: float = 10
    sector_weight: float = 8
    update_weight: float = 10
    recency_weight: float = 12


DEFAULT_SCORING_CONFIG = ScoringConfig()


def calculate_importance_score(
    sources: list[EventSource],
    *,
    company_count: int,
    sector_count: int,
    update_count: int,
    updated_at: datetime,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> float:
    if not sources:
        return 0.0
    primary = 1.0 if any(source.is_primary_source for source in sources) else 0.0
    independent = min(len({source.publisher for source in sources}) / 4, 1.0)
    authority = sum(source.authority_score for source in sources) / len(sources) / 100
    entities = min(company_count / 4, 1.0)
    sectors = min(sector_count / 3, 1.0)
    updates = min(update_count / 5, 1.0)
    recency = _recency_factor(updated_at)
    score = (
        primary * config.official_source_weight
        + independent * config.independent_source_weight
        + authority * config.authority_weight
        + entities * config.entity_weight
        + sectors * config.sector_weight
        + updates * config.update_weight
        + recency * config.recency_weight
    )
    return round(min(100.0, max(0.0, score)), 2)


def calculate_confidence_score(
    sources: list[EventSource],
    *,
    required_fields_complete: bool,
    has_conflict: bool = False,
) -> float:
    if not sources:
        return 0.0
    official = 35 if any(source.is_primary_source for source in sources) else 0
    independent = min(len({source.publisher for source in sources}), 4) * 10
    authority = (sum(source.authority_score for source in sources) / len(sources)) * 0.2
    completeness = 15 if required_fields_complete else 5
    conflict_penalty = 25 if has_conflict else 0
    return round(min(100.0, max(0.0, official + independent + authority + completeness - conflict_penalty)), 2)


def _recency_factor(value: datetime) -> float:
    reference = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (datetime.now(timezone.utc) - reference.astimezone(timezone.utc)).total_seconds() / 3600)
    if age_hours <= 6:
        return 1.0
    if age_hours <= 24:
        return 0.8
    if age_hours <= 72:
        return 0.55
    if age_hours <= 168:
        return 0.3
    return 0.1
