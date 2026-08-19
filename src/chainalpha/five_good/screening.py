from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import pstdev
from typing import Any, Iterable

from chainalpha.events.service import model_dump
from chainalpha.five_good.models import (
    EntryStatus,
    FiveGoodCategory,
    FiveGoodEntry,
    FiveGoodHistory,
    HistoryAction,
)


FACTOR_LABELS = {
    "roe": "ROE",
    "gross_margin": "行业内毛利率",
    "net_margin": "行业内净利率",
    "revenue_growth": "营收增长",
    "profit_growth": "利润增长",
    "cashflow": "现金流质量",
    "leverage": "杠杆安全",
    "pe": "行业内PE",
    "pb": "行业内PB",
    "ps": "行业内PS",
    "peg": "增长估值匹配",
    "dividend": "股息率",
    "trend": "中期趋势",
    "momentum_20d": "20日相对强弱",
    "momentum_60d": "60日相对强弱",
    "volume": "量能确认",
    "fund_flow": "资金流",
    "volatility": "波动控制",
    "story": "事件催化",
    "industry_momentum": "行业景气",
    "liquidity": "流动性",
    "quality": "基本面质量",
}


@dataclass(slots=True)
class StockFeatures:
    stock_code: str
    stock_name: str
    market: str
    industry: str = ""
    sub_industry: str = ""
    sector: str = ""
    list_date: str = ""
    report_period: str = ""
    announcement_date: str = ""
    as_of_date: str = ""
    roe: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    revenue_growth: float | None = None
    profit_growth: float | None = None
    debt_ratio: float | None = None
    cashflow_to_revenue: float | None = None
    current_ratio: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    ps_ttm: float | None = None
    dividend_yield: float | None = None
    market_cap: float | None = None
    close: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    price_vs_ma20: float | None = None
    ma20_vs_ma60: float | None = None
    volatility_20d: float | None = None
    fund_flow_5d: float | None = None
    industry_momentum: float | None = None
    story_score: float | None = None
    story_event_id: str = ""
    story_title: str = ""
    story_status: str = ""
    story_direction: str = ""
    story_confidence: float | None = None
    story_occurred_at: str = ""
    story_source: str = ""
    sources: list[str] | None = None

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "StockFeatures":
        allowed = {field.name for field in fields(cls)}
        payload = {key: value for key, value in row.items() if key in allowed}
        for field in fields(cls):
            if field.type in {float | None, "float | None"} and payload.get(field.name) is not None:
                payload[field.name] = _number(payload[field.name])
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ScreeningConfig:
    pool_size: int = 80
    min_score: float = 60.0
    min_completeness: float = 0.60
    min_listing_days: int = 365
    story_max_age_days: int = 180


@dataclass(slots=True)
class ScreeningResult:
    entries: list[FiveGoodEntry]
    universe_count: int
    eligible_count: int
    category_counts: dict[str, int]
    generated_at: datetime
    warnings: list[str]


class CrossSectionRanker:
    def __init__(self, rows: list[StockFeatures]) -> None:
        self.rows = rows
        self.global_values: dict[str, list[float]] = {}
        self.industry_values: dict[tuple[str, str], list[float]] = {}

    def percentile(
        self,
        row: StockFeatures,
        field: str,
        *,
        higher_is_better: bool = True,
        industry_neutral: bool = False,
    ) -> float | None:
        value = _number(getattr(row, field, None))
        if value is None:
            return None
        values = self._values(field, row.industry if industry_neutral else "")
        if not values:
            return None
        rank = 50.0 if len(values) == 1 else (bisect_right(values, value) - 1) / (len(values) - 1) * 100
        return round(rank if higher_is_better else 100 - rank, 2)

    def _values(self, field: str, industry: str) -> list[float]:
        if industry:
            key = (field, industry)
            if key not in self.industry_values:
                values = sorted(
                    value
                    for item in self.rows
                    if item.industry == industry
                    for value in [_number(getattr(item, field, None))]
                    if value is not None
                )
                # Very small peer groups produce unstable ranks; use the market instead.
                self.industry_values[key] = values if len(values) >= 8 else self._values(field, "")
            return self.industry_values[key]
        if field not in self.global_values:
            self.global_values[field] = sorted(
                value
                for item in self.rows
                for value in [_number(getattr(item, field, None))]
                if value is not None
            )
        return self.global_values[field]


class FiveGoodScreener:
    """Auditable cross-sectional screener. LLMs may explain results, never invent factors."""

    def __init__(self, config: ScreeningConfig | None = None) -> None:
        self.config = config or ScreeningConfig()

    def screen(self, rows: Iterable[StockFeatures], *, now: datetime | None = None) -> ScreeningResult:
        now = now or datetime.now(timezone.utc)
        universe = list(rows)
        eligible = [row for row in universe if self._eligible(row, now.date())]
        ranker = CrossSectionRanker(eligible)
        scored: dict[FiveGoodCategory, list[tuple[StockFeatures, float, float, dict[str, float]]]] = {
            category: [] for category in FiveGoodCategory
        }
        company_scores: dict[str, float] = {}
        timing_scores: dict[str, float] = {}

        for row in eligible:
            company = self._company_score(row, ranker)
            if company:
                company_scores[row.stock_code] = company[0]
                scored[FiveGoodCategory.GOOD_COMPANY].append((row, *company))
            price = self._price_score(row, ranker, company[0] if company else None)
            if price:
                scored[FiveGoodCategory.GOOD_PRICE].append((row, *price))
            timing = self._timing_score(row, ranker)
            if timing:
                timing_scores[row.stock_code] = timing[0]
                scored[FiveGoodCategory.GOOD_TIMING].append((row, *timing))
            story = self._story_score(row)
            if story:
                scored[FiveGoodCategory.GOOD_STORY].append((row, *story))

        for row in eligible:
            stock = self._stock_score(row, ranker, company_scores.get(row.stock_code), timing_scores.get(row.stock_code))
            if stock:
                scored[FiveGoodCategory.GOOD_STOCK].append((row, *stock))

        entries: list[FiveGoodEntry] = []
        warnings: list[str] = []
        for category, candidates in scored.items():
            candidates.sort(key=lambda item: (item[1], item[2]), reverse=True)
            selected = [item for item in candidates if item[1] >= self.config.min_score][: self.config.pool_size]
            entries.extend(self._entry(category, *item, now=now) for item in selected)
            if not selected:
                warnings.append(f"{category.value}: no candidate passed score and data-quality gates")

        counts = {category.value: sum(item.category == category for item in entries) for category in FiveGoodCategory}
        return ScreeningResult(
            entries=entries,
            universe_count=len(universe),
            eligible_count=len(eligible),
            category_counts=counts,
            generated_at=now,
            warnings=warnings,
        )

    def _eligible(self, row: StockFeatures, today: date) -> bool:
        name = row.stock_name.upper().replace(" ", "")
        if not row.stock_code or not row.stock_name or "ST" in name or "退" in row.stock_name:
            return False
        listed = _parse_date(row.list_date)
        return listed is None or (today - listed).days >= self.config.min_listing_days

    def _company_score(self, row: StockFeatures, ranker: CrossSectionRanker):
        if row.roe is None or row.roe <= 0 or (row.debt_ratio is not None and row.debt_ratio >= 95):
            return None
        factors = {
            "roe": ranker.percentile(row, "roe", industry_neutral=True),
            "gross_margin": ranker.percentile(row, "gross_margin", industry_neutral=True),
            "net_margin": ranker.percentile(row, "net_margin", industry_neutral=True),
            "revenue_growth": ranker.percentile(row, "revenue_growth"),
            "profit_growth": ranker.percentile(row, "profit_growth"),
            "cashflow": ranker.percentile(row, "cashflow_to_revenue", industry_neutral=True),
            "leverage": ranker.percentile(row, "debt_ratio", higher_is_better=False, industry_neutral=True),
        }
        return _weighted(factors, {"roe": 22, "gross_margin": 15, "net_margin": 10, "revenue_growth": 10,
                                   "profit_growth": 18, "cashflow": 15, "leverage": 10}, self.config.min_completeness)

    def _price_score(self, row: StockFeatures, ranker: CrossSectionRanker, company_score: float | None):
        if company_score is None or company_score < 50 or not row.pe_ttm or row.pe_ttm <= 0 or not row.pb or row.pb <= 0:
            return None
        growth = max(_number(row.profit_growth) or 0, 3.0)
        peg_proxy = row.pe_ttm / growth
        factors = {
            "pe": ranker.percentile(row, "pe_ttm", higher_is_better=False, industry_neutral=True),
            "pb": ranker.percentile(row, "pb", higher_is_better=False, industry_neutral=True),
            "ps": ranker.percentile(row, "ps_ttm", higher_is_better=False, industry_neutral=True),
            "peg": max(0.0, min(100.0, 100 - peg_proxy * 28)),
            "dividend": ranker.percentile(row, "dividend_yield"),
            "quality": company_score,
        }
        return _weighted(factors, {"pe": 30, "pb": 18, "ps": 10, "peg": 22, "dividend": 8, "quality": 12}, 0.62)

    def _timing_score(self, row: StockFeatures, ranker: CrossSectionRanker):
        factors = {
            "trend": _trend_score(row.price_vs_ma20, row.ma20_vs_ma60),
            "momentum_20d": ranker.percentile(row, "return_20d"),
            "momentum_60d": ranker.percentile(row, "return_60d"),
            "volume": _bounded_signal(row.volume_ratio, low=0.7, high=2.0),
            "fund_flow": ranker.percentile(row, "fund_flow_5d"),
            "volatility": ranker.percentile(row, "volatility_20d", higher_is_better=False),
        }
        return _weighted(factors, {"trend": 25, "momentum_20d": 20, "momentum_60d": 18, "volume": 12,
                                   "fund_flow": 15, "volatility": 10}, 0.60)

    def _story_score(self, row: StockFeatures):
        if row.story_score is None or row.story_confidence is None or row.story_confidence < 45:
            return None
        if row.story_direction not in {"positive", "mixed"}:
            return None
        factors = {"story": row.story_score, "quality": row.story_confidence}
        return _weighted(factors, {"story": 70, "quality": 30}, 1.0)

    def _stock_score(
        self,
        row: StockFeatures,
        ranker: CrossSectionRanker,
        company_score: float | None,
        timing_score: float | None,
    ):
        if company_score is None or company_score < 52 or timing_score is None:
            return None
        factors = {
            "quality": company_score,
            "trend": timing_score,
            "industry_momentum": ranker.percentile(row, "industry_momentum"),
            "liquidity": ranker.percentile(row, "turnover_rate"),
            "story": row.story_score,
        }
        return _weighted(factors, {"quality": 35, "trend": 25, "industry_momentum": 18, "liquidity": 10,
                                   "story": 12}, 0.75)

    def _entry(
        self,
        category: FiveGoodCategory,
        row: StockFeatures,
        score: float,
        completeness: float,
        factors: dict[str, float],
        *,
        now: datetime,
    ) -> FiveGoodEntry:
        status = EntryStatus.ACTIVE if score >= 70 and completeness >= 0.75 else EntryStatus.WATCHING
        validity = 7 if category == FiveGoodCategory.GOOD_TIMING else 30 if category == FiveGoodCategory.GOOD_STORY else None
        return FiveGoodEntry(
            id=f"screen-{category.value}-{row.stock_code.lower().replace('.', '-')}",
            stock_code=row.stock_code,
            stock_name=row.stock_name,
            market=row.market,
            category=category,
            industry=row.industry or "未分类",
            sub_industry=row.sub_industry,
            sector=row.sector or row.industry,
            score=round(score, 2),
            status=status,
            selection_reason=_selection_reason(category, factors, completeness),
            risk_summary=_risk_summary(category, row, completeness),
            tags=_tags(category, factors),
            valid_from=now,
            valid_until=now + timedelta(days=validity) if validity else None,
            as_of_date=_parse_date(row.as_of_date) or now.date(),
            created_at=now,
            updated_at=now,
            category_data=_category_data(category, row, factors, completeness),
        )


def attach_market_events(
    rows: Iterable[StockFeatures],
    events: Iterable[Any],
    *,
    now: datetime | None = None,
    max_age_days: int = 180,
) -> list[StockFeatures]:
    now = now or datetime.now(timezone.utc)
    event_list = list(events)
    output = list(rows)
    for row in output:
        matches = []
        for event in event_list:
            companies = {str(company).strip() for company in (getattr(event, "companies", []) or [])}
            # Story selection requires an explicit entity mapping. Title substring
            # matching creates false positives for similar company names.
            if row.stock_name.strip() not in companies:
                continue
            event_time = getattr(event, "occurred_at", None) or getattr(event, "updated_at", None)
            if event_time and event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            age = (now - event_time).days if event_time else max_age_days
            if age > max_age_days:
                continue
            recency = max(0.0, 100 - age / max_age_days * 100)
            importance = _number(getattr(event, "importance_score", None)) or 0
            confidence = _number(getattr(event, "confidence_score", None)) or 0
            source_count = _number(getattr(event, "source_count", None)) or len(getattr(event, "sources", []) or [])
            score = importance * 0.45 + confidence * 0.35 + recency * 0.15 + min(source_count / 3, 1) * 100 * 0.05
            if getattr(event, "status", "") == "unverified":
                score *= 0.85
            if getattr(event, "impact_direction", "") == "mixed":
                score *= 0.9
            matches.append((score, event, event_time))
        if not matches:
            continue
        score, event, event_time = max(matches, key=lambda item: item[0])
        sources = getattr(event, "sources", []) or []
        row.story_score = round(score, 2)
        row.story_event_id = str(getattr(event, "id", ""))
        row.story_title = str(getattr(event, "title", ""))
        row.story_status = str(getattr(event, "status", ""))
        row.story_direction = str(getattr(event, "impact_direction", ""))
        row.story_confidence = _number(getattr(event, "confidence_score", None))
        row.story_occurred_at = event_time.isoformat() if event_time else ""
        row.story_source = "、".join(dict.fromkeys(
            str(getattr(source, "publisher", "")) for source in sources if getattr(source, "publisher", "")
        ))
    return output


def persist_screening_result(result: ScreeningResult, path: str | Path) -> None:
    target = Path(path)
    previous_entries: list[FiveGoodEntry] = []
    previous_history: list[FiveGoodHistory] = []
    if target.exists():
        payload = json.loads(target.read_text(encoding="utf-8"))
        previous_entries = [FiveGoodEntry.model_validate(item) for item in payload.get("entries", [])]
        previous_history = [FiveGoodHistory.model_validate(item) for item in payload.get("history", [])]
    old = {(item.stock_code, item.category): item for item in previous_entries}
    new = {(item.stock_code, item.category): item for item in result.entries}
    history = list(previous_history)
    stamp = result.generated_at.strftime("%Y%m%d%H%M%S")
    for key, entry in new.items():
        before = old.get(key)
        if before is None:
            action = HistoryAction.ADDED
        elif abs(before.score - entry.score) < 0.01 and before.status == entry.status:
            continue
        else:
            action = HistoryAction.UPDATED
        history.append(_history_entry(entry, action, stamp, before.score if before else None))
    for key, before in old.items():
        if key not in new:
            history.append(_history_entry(before, HistoryAction.REMOVED, stamp, before.score))
    payload = {
        "metadata": {
            "screening_version": "five-good-v1",
            "universe_count": result.universe_count,
            "eligible_count": result.eligible_count,
            "category_counts": result.category_counts,
            "generated_at": result.generated_at.isoformat(),
            "warnings": result.warnings,
        },
        "entries": [model_dump(item, mode="json") for item in result.entries],
        "history": [model_dump(item, mode="json") for item in history[-20000:]],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(target)


def load_feature_cache(path: str | Path) -> list[StockFeatures]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("features", payload) if isinstance(payload, dict) else payload
    return [StockFeatures.from_dict(item) for item in rows]


def _weighted(factors: dict[str, float | None], weights: dict[str, float], minimum: float):
    available = {key: value for key, value in factors.items() if value is not None and math.isfinite(value)}
    total_weight = sum(weights.values())
    available_weight = sum(weights[key] for key in available)
    completeness = available_weight / total_weight if total_weight else 0
    if completeness < minimum or not available_weight:
        return None
    score = sum(available[key] * weights[key] for key in available) / available_weight
    return round(score, 2), round(completeness, 4), {key: round(value, 2) for key, value in available.items()}


def _trend_score(price_vs_ma20: float | None, ma20_vs_ma60: float | None) -> float | None:
    if price_vs_ma20 is None or ma20_vs_ma60 is None:
        return None
    # A mild positive trend scores highest; extreme extension is penalized.
    extension = abs(price_vs_ma20)
    score = 55 + ma20_vs_ma60 * 3 + price_vs_ma20 * 1.5
    if extension > 15:
        score -= (extension - 15) * 2
    return max(0.0, min(100.0, score))


def _bounded_signal(value: float | None, *, low: float, high: float) -> float | None:
    if value is None:
        return None
    if value < low:
        return max(0.0, value / low * 45)
    if value <= high:
        return 45 + (value - low) / (high - low) * 55
    return max(20.0, 100 - (value - high) * 20)


def _selection_reason(category: FiveGoodCategory, factors: dict[str, float], completeness: float) -> str:
    leaders = sorted(factors.items(), key=lambda item: item[1], reverse=True)[:3]
    evidence = "、".join(f"{FACTOR_LABELS.get(key, key)}{value:.0f}分" for key, value in leaders)
    return f"经全市场横截面筛选，{evidence}构成主要入选依据；有效因子覆盖率{completeness:.0%}。"


def _risk_summary(category: FiveGoodCategory, row: StockFeatures, completeness: float) -> str:
    risks = []
    if completeness < 0.8:
        risks.append("部分因子缺失，结论置信度受限")
    if row.debt_ratio is not None and row.debt_ratio > 70:
        risks.append("资产负债率偏高")
    if row.profit_growth is not None and row.profit_growth < 0:
        risks.append("利润同比下降")
    if category == FiveGoodCategory.GOOD_TIMING:
        risks.append("技术与资金信号可能快速失效")
    if category == FiveGoodCategory.GOOD_STORY:
        risks.append("事件可能被证伪或已被市场定价")
    return "；".join(risks) or "需持续验证财务、行情与行业数据是否延续。"


def _tags(category: FiveGoodCategory, factors: dict[str, float]) -> list[str]:
    return [FACTOR_LABELS.get(key, key) for key, _ in sorted(factors.items(), key=lambda item: item[1], reverse=True)[:3]]


def _category_data(
    category: FiveGoodCategory,
    row: StockFeatures,
    factors: dict[str, float],
    completeness: float,
) -> dict[str, Any]:
    common = {
        "data_completeness": round(completeness * 100, 2),
        "factor_scores": factors,
        "report_period": row.report_period,
        "announcement_date": row.announcement_date,
        "sources": row.sources or [],
    }
    if category == FiveGoodCategory.GOOD_COMPANY:
        return common | {
            "industry_position": "行业内横截面领先",
            "roe": row.roe,
            "gross_margin": row.gross_margin,
            "cashflow_quality": "较强" if factors.get("cashflow", 0) >= 70 else "一般",
            "cashflow_to_revenue": row.cashflow_to_revenue,
            "dividend_yield": row.dividend_yield,
            "profit_growth": row.profit_growth,
            "debt_ratio": row.debt_ratio,
        }
    if category == FiveGoodCategory.GOOD_PRICE:
        growth = max(_number(row.profit_growth) or 0, 3.0)
        return common | {
            "current_price": row.close,
            "pe": row.pe_ttm,
            "pb": row.pb,
            "ps": row.ps_ttm,
            "peg": round(row.pe_ttm / growth, 2) if row.pe_ttm else None,
            "valuation_status": "按行业内估值与增长匹配度筛选",
        }
    if category == FiveGoodCategory.GOOD_TIMING:
        return common | {
            "current_price": row.close,
            "technical_signal": "趋势与相对强弱共振" if factors.get("trend", 0) >= 60 else "观察",
            "capital_flow_signal": "流入" if (row.fund_flow_5d or 0) > 0 else "未确认",
            "return_20d": row.return_20d,
            "return_60d": row.return_60d,
            "volume_ratio": row.volume_ratio,
        }
    if category == FiveGoodCategory.GOOD_STORY:
        return common | {
            "event_id": row.story_event_id,
            "event_title": row.story_title,
            "event_type": "市场事件",
            "event_score": row.story_score,
            "event_status": row.story_status,
            "impact_direction": row.story_direction,
            "confidence": row.story_confidence,
            "event_occurred_at": row.story_occurred_at or None,
            "event_source": row.story_source,
        }
    return common | {
        "industry_chain": row.sector or row.industry,
        "chain_position": row.sub_industry or row.industry,
        "elasticity_score": factors.get("trend"),
        "theme_relevance_score": factors.get("industry_momentum"),
        "relative_strength": "强" if factors.get("trend", 0) >= 70 else "中等",
        "market_cap_level": _market_cap_level(row.market_cap),
    }


def _history_entry(
    entry: FiveGoodEntry,
    action: HistoryAction,
    stamp: str,
    previous_score: float | None,
) -> FiveGoodHistory:
    digest = hashlib.sha1(f"{stamp}:{entry.id}:{action.value}".encode()).hexdigest()[:10]
    return FiveGoodHistory(
        id=f"history-{digest}",
        entry_id=entry.id,
        stock_code=entry.stock_code,
        category=entry.category,
        action=action,
        previous_score=previous_score,
        new_score=entry.score if action != HistoryAction.REMOVED else None,
        reason=entry.selection_reason if action != HistoryAction.REMOVED else "本期未通过评分或数据质量闸门。",
        as_of_date=entry.as_of_date,
        created_at=entry.updated_at,
    )


def calculate_technical_features(closes: list[float], volumes: list[float]) -> dict[str, float | None]:
    if not closes:
        return {}
    latest = closes[-1]
    ma20 = sum(closes[-20:]) / min(len(closes), 20)
    ma60 = sum(closes[-60:]) / min(len(closes), 60)
    returns = [(closes[index] / closes[index - 1] - 1) * 100 for index in range(max(1, len(closes) - 20), len(closes)) if closes[index - 1]]
    avg_volume = sum(volumes[-20:-1]) / max(len(volumes[-20:-1]), 1) if len(volumes) > 1 else None
    return {
        "return_20d": (latest / closes[-21] - 1) * 100 if len(closes) >= 21 and closes[-21] else None,
        "return_60d": (latest / closes[-61] - 1) * 100 if len(closes) >= 61 and closes[-61] else None,
        "price_vs_ma20": (latest / ma20 - 1) * 100 if ma20 else None,
        "ma20_vs_ma60": (ma20 / ma60 - 1) * 100 if ma60 else None,
        "volatility_20d": pstdev(returns) if len(returns) >= 5 else None,
        "volume_ratio": volumes[-1] / avg_volume if avg_volume else None,
    }


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _parse_date(value: str | date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) < 8:
        return None
    try:
        return datetime.strptime(digits[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _market_cap_level(value: float | None) -> str:
    if value is None:
        return "未知"
    # Tushare total_mv is expressed in CNY 10,000.
    if value >= 10_000_000:
        return "大盘"
    if value >= 2_000_000:
        return "中盘"
    return "小盘"
