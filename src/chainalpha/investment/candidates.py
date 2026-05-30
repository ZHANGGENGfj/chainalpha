from __future__ import annotations

from collections import defaultdict

from chainalpha.investment.company_map import match_signal_companies
from chainalpha.investment.market_data import lookup_snapshot
from chainalpha.schemas import (
    CompanyProfile,
    FinancialSnapshot,
    InvestmentCandidate,
    PriceSnapshot,
    WeakSignal,
)


POSITIVE_EVENTS = {
    "ORDER_SIGNED",
    "CUSTOMER_CERTIFICATION",
    "CAPACITY_EXPANSION",
    "FINANCIAL_VALIDATION",
    "GROSS_MARGIN_CHANGE",
    "PRODUCT_LAUNCH",
    "TECH_UPGRADE",
    "POLICY_SUPPORT",
}

RISK_EVENTS = {
    "PRICE_DECREASE",
    "CAPEX_DECREASE",
    "ATTENTION_HOT",
}


def build_investment_candidates(
    signals: list[WeakSignal],
    companies: list[CompanyProfile],
    financials: dict[str, FinancialSnapshot] | None = None,
    prices: dict[str, PriceSnapshot] | None = None,
    top_k: int = 20,
) -> list[InvestmentCandidate]:
    financials = financials or {}
    prices = prices or {}
    grouped: dict[str, list[WeakSignal]] = defaultdict(list)
    profile_by_name: dict[str, CompanyProfile] = {}

    for sig in signals:
        text = " ".join([
            sig.title,
            sig.event.description,
            " ".join(sig.event.companies),
            " ".join(sig.event.supply_chain_nodes),
            " ".join(sig.hypothesis.causal_path),
        ])
        matched = match_signal_companies(text, sig.event.supply_chain_nodes + sig.hypothesis.causal_path, companies)
        for profile in matched:
            if not profile.name:
                continue
            grouped[profile.name].append(sig)
            profile_by_name[profile.name] = profile

    candidates = [
        _build_candidate(
            profile=profile_by_name[name],
            signals=company_signals,
            financial=lookup_snapshot(financials, name, profile_by_name[name].ticker),
            price=lookup_snapshot(prices, name, profile_by_name[name].ticker),
        )
        for name, company_signals in grouped.items()
    ]
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:top_k]


def _build_candidate(
    profile: CompanyProfile,
    signals: list[WeakSignal],
    financial: FinancialSnapshot | None,
    price: PriceSnapshot | None,
) -> InvestmentCandidate:
    signals = sorted(signals, key=lambda s: s.weak_signal_score, reverse=True)
    score = _candidate_score(signals, financial, price)
    rating = _rating(score)
    catalysts = _catalysts(signals, financial)
    risks = _risks(signals, financial, price)
    evidence = [
        item
        for sig in signals[:5]
        for item in (sig.event.evidence[:1] or [sig.event.description])
        if item
    ]
    thesis = _thesis(profile, signals, financial, price)

    return InvestmentCandidate(
        company=profile,
        score=score,
        rating=rating,
        thesis=thesis,
        signals=signals[:8],
        financial=financial,
        price=price,
        catalysts=catalysts,
        risks=risks,
        evidence=evidence[:6],
        next_actions=_next_actions(financial, price),
    )


def _candidate_score(
    signals: list[WeakSignal],
    financial: FinancialSnapshot | None,
    price: PriceSnapshot | None,
) -> float:
    if not signals:
        return 0.0

    signal_score = min(100.0, sum(s.weak_signal_score for s in signals[:5]) / min(len(signals), 5))
    evidence_bonus = min(10.0, len(signals) * 1.8)
    financial_bonus = _financial_bonus(financial)
    price_penalty = _price_penalty(price)
    attention_penalty = max((s.attention_score for s in signals), default=0.0) * 0.08
    score = signal_score + evidence_bonus + financial_bonus - price_penalty - attention_penalty
    return round(max(0.0, min(100.0, score)), 2)


def _financial_bonus(financial: FinancialSnapshot | None) -> float:
    if financial is None:
        return 0.0
    bonus = 0.0
    if financial.revenue_growth is not None and financial.revenue_growth > 15:
        bonus += 4.0
    if financial.profit_growth is not None and financial.profit_growth > 20:
        bonus += 5.0
    if financial.gross_margin is not None and financial.gross_margin > 30:
        bonus += 3.0
    if financial.roe is not None and financial.roe > 12:
        bonus += 3.0
    if financial.debt_ratio is not None and financial.debt_ratio > 70:
        bonus -= 4.0
    if financial.pe is not None and financial.pe > 80:
        bonus -= 5.0
    return bonus


def _price_penalty(price: PriceSnapshot | None) -> float:
    if price is None:
        return 0.0
    penalty = 0.0
    if price.change_3m is not None and price.change_3m > 40:
        penalty += 8.0
    if price.change_6m is not None and price.change_6m > 80:
        penalty += 8.0
    if price.change_12m is not None and price.change_12m > 120:
        penalty += 10.0
    return penalty


def _rating(score: float) -> str:
    if score >= 80:
        return "high_conviction"
    if score >= 65:
        return "watch"
    if score >= 50:
        return "early_track"
    return "low_priority"


def _catalysts(signals: list[WeakSignal], financial: FinancialSnapshot | None) -> list[str]:
    out: list[str] = []
    for sig in signals:
        if sig.event.event_type in POSITIVE_EVENTS:
            out.append(f"{sig.event.event_type}: {sig.event.description[:80]}")
    if financial and financial.profit_growth is not None and financial.profit_growth > 20:
        out.append(f"profit_growth={financial.profit_growth:.1f}")
    if financial and financial.revenue_growth is not None and financial.revenue_growth > 15:
        out.append(f"revenue_growth={financial.revenue_growth:.1f}")
    return list(dict.fromkeys(out))[:6]


def _risks(signals: list[WeakSignal], financial: FinancialSnapshot | None, price: PriceSnapshot | None) -> list[str]:
    out: list[str] = []
    for sig in signals:
        if sig.event.event_type in RISK_EVENTS:
            out.append(f"{sig.event.event_type}: {sig.event.description[:80]}")
        out.extend(sig.risks[:1])
    if financial and financial.debt_ratio is not None and financial.debt_ratio > 70:
        out.append(f"high_debt_ratio={financial.debt_ratio:.1f}")
    if financial and financial.pe is not None and financial.pe > 80:
        out.append(f"high_pe={financial.pe:.1f}")
    if price and price.change_3m is not None and price.change_3m > 40:
        out.append(f"crowded_price_move_3m={price.change_3m:.1f}")
    return list(dict.fromkeys(out))[:6]


def _thesis(
    profile: CompanyProfile,
    signals: list[WeakSignal],
    financial: FinancialSnapshot | None,
    price: PriceSnapshot | None,
) -> str:
    nodes = ", ".join(profile.nodes[:4]) or profile.industry or "mapped industry nodes"
    top_event = signals[0].event.event_type if signals else "signal"
    parts = [f"{profile.name} maps to {nodes}; latest strongest signal is {top_event}."]
    if profile.role:
        parts.append(f"Role: {profile.role}.")
    if financial and financial.revenue_growth is not None:
        parts.append(f"Revenue growth: {financial.revenue_growth:.1f}.")
    if price and price.change_3m is not None:
        parts.append(f"3m price move: {price.change_3m:.1f}.")
    return " ".join(parts)


def _next_actions(financial: FinancialSnapshot | None, price: PriceSnapshot | None) -> list[str]:
    actions = ["review original announcements/news", "verify customer/order details"]
    if financial is None:
        actions.append("import latest financial snapshot")
    if price is None:
        actions.append("import recent price performance")
    actions.append("schedule 1m/3m thesis review")
    return actions
