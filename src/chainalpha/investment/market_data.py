from __future__ import annotations

import csv
from pathlib import Path
import re
from typing import Any

from chainalpha.schemas import CompanyProfile, FinancialSnapshot, PriceSnapshot, SourceDocument


def load_financial_snapshots(path: str | Path | None) -> dict[str, FinancialSnapshot]:
    if path is None:
        return {}
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {}
    return {
        _key(snapshot.company, snapshot.ticker): snapshot
        for snapshot in (_financial_from_row(row) for row in _read_csv(p))
    }


def load_price_snapshots(path: str | Path | None) -> dict[str, PriceSnapshot]:
    if path is None:
        return {}
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {}
    return {
        _key(snapshot.company, snapshot.ticker): snapshot
        for snapshot in (_price_from_row(row) for row in _read_csv(p))
    }


def derive_financial_snapshots(
    docs: list[SourceDocument],
    profiles: list[CompanyProfile],
) -> dict[str, FinancialSnapshot]:
    snapshots: dict[str, FinancialSnapshot] = {}
    for profile in profiles:
        related_docs = [doc for doc in docs if _doc_matches_profile(doc, profile)]
        if not related_docs:
            continue
        candidate = _best_financial_snapshot(profile, related_docs)
        if candidate is not None:
            snapshots[_key(profile.name, profile.ticker)] = candidate
    return snapshots


def derive_price_snapshots(
    docs: list[SourceDocument],
    profiles: list[CompanyProfile],
) -> dict[str, PriceSnapshot]:
    snapshots: dict[str, PriceSnapshot] = {}
    for profile in profiles:
        related_docs = [doc for doc in docs if _price_doc_matches_profile(doc, profile)]
        if not related_docs:
            continue
        candidate = _best_price_snapshot(profile, related_docs)
        if candidate is not None:
            snapshots[_key(profile.name, profile.ticker)] = candidate
    return snapshots


def lookup_snapshot(rows: dict[str, Any], company: str, ticker: str | None) -> Any:
    return rows.get(_key(company, ticker)) or rows.get(_key(company, None)) or rows.get(_key("", ticker))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _key(company: str, ticker: str | None) -> str:
    if ticker:
        return f"ticker:{ticker.strip().lower()}"
    return f"company:{company.strip().lower()}"


def _float(row: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None or str(value).strip() == "":
            continue
        raw = str(value).strip().replace("%", "")
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _str(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _financial_from_row(row: dict[str, str]) -> FinancialSnapshot:
    return FinancialSnapshot(
        company=_str(row, "company", "name", "公司") or "",
        ticker=_str(row, "ticker", "symbol", "代码"),
        period=_str(row, "period", "报告期"),
        revenue_growth=_float(row, "revenue_growth", "营收增速"),
        profit_growth=_float(row, "profit_growth", "净利润增速"),
        gross_margin=_float(row, "gross_margin", "毛利率"),
        roe=_float(row, "roe", "ROE"),
        operating_cash_flow=_float(row, "operating_cash_flow", "经营现金流"),
        debt_ratio=_float(row, "debt_ratio", "资产负债率"),
        pe=_float(row, "pe", "PE"),
        pb=_float(row, "pb", "PB"),
        ps=_float(row, "ps", "PS"),
        market_cap=_float(row, "market_cap", "市值"),
        metadata=dict(row),
    )


def _price_from_row(row: dict[str, str]) -> PriceSnapshot:
    return PriceSnapshot(
        company=_str(row, "company", "name", "公司") or "",
        ticker=_str(row, "ticker", "symbol", "代码"),
        date=_str(row, "date", "日期"),
        close=_float(row, "close", "收盘价"),
        change_1m=_float(row, "change_1m", "1月涨跌幅"),
        change_3m=_float(row, "change_3m", "3月涨跌幅"),
        change_6m=_float(row, "change_6m", "6月涨跌幅"),
        change_12m=_float(row, "change_12m", "12月涨跌幅"),
        metadata=dict(row),
    )


def _doc_matches_profile(doc: SourceDocument, profile: CompanyProfile) -> bool:
    text = " ".join([
        doc.title,
        doc.content,
        str(doc.metadata.get("company_name") or ""),
        str(doc.metadata.get("sec_name") or ""),
        str(doc.metadata.get("sec_code") or ""),
    ]).lower()
    terms = [profile.name, profile.ticker or "", *(profile.aliases or [])]
    return any(term and term.lower() in text for term in terms)


def _price_doc_matches_profile(doc: SourceDocument, profile: CompanyProfile) -> bool:
    symbol = str(doc.metadata.get("symbol") or "").lower()
    ticker = (profile.ticker or "").lower()
    if ticker and ticker in symbol:
        return True
    return _doc_matches_profile(doc, profile) or doc.source_type == "price_data"


def _best_financial_snapshot(profile: CompanyProfile, docs: list[SourceDocument]) -> FinancialSnapshot | None:
    best: FinancialSnapshot | None = None
    best_score = -1
    for doc in docs:
        snapshot = _financial_from_doc(profile, doc)
        if snapshot is None:
            continue
        score = _financial_score(snapshot, doc)
        if score > best_score:
            best = snapshot
            best_score = score
    return best


def _best_price_snapshot(profile: CompanyProfile, docs: list[SourceDocument]) -> PriceSnapshot | None:
    best: PriceSnapshot | None = None
    best_score = -1
    for doc in docs:
        snapshot = _price_from_doc(profile, doc)
        if snapshot is None:
            continue
        score = _price_score(snapshot, doc)
        if score > best_score:
            best = snapshot
            best_score = score
    return best


def _financial_from_doc(profile: CompanyProfile, doc: SourceDocument) -> FinancialSnapshot | None:
    text = " ".join([doc.title, doc.content])
    revenue_growth = _extract_pct(text, [
        r"(?:revenue|sales|营收|收入)[^%\n]{0,30}?([+-]?\d+(?:\.\d+)?)\s*%",
        r"([+-]?\d+(?:\.\d+)?)\s*%\s*(?:revenue|sales|营收|收入)",
    ])
    profit_growth = _extract_pct(text, [
        r"(?:net profit|profit growth|净利润|利润)[^%\n]{0,30}?([+-]?\d+(?:\.\d+)?)\s*%",
        r"([+-]?\d+(?:\.\d+)?)\s*%\s*(?:net profit|净利润|利润)",
    ])
    gross_margin = _extract_pct(text, [
        r"(?:gross margin|毛利率)[^%\n]{0,20}?([+-]?\d+(?:\.\d+)?)\s*%",
    ])
    roe = _extract_pct(text, [
        r"(?:\bROE\b)[^%\n]{0,12}?([+-]?\d+(?:\.\d+)?)\s*%",
    ])
    debt_ratio = _extract_pct(text, [
        r"(?:debt ratio|资产负债率)[^%\n]{0,20}?([+-]?\d+(?:\.\d+)?)\s*%",
    ])
    pe = _extract_number(text, [r"(?:\bPE\b|市盈率)[^\d\n]{0,10}(\d+(?:\.\d+)?)"])
    pb = _extract_number(text, [r"(?:\bPB\b|市净率)[^\d\n]{0,10}(\d+(?:\.\d+)?)"])
    ps = _extract_number(text, [r"(?:\bPS\b|市销率)[^\d\n]{0,10}(\d+(?:\.\d+)?)"])
    market_cap = _extract_number(text, [r"(?:market cap|市值)[^\d\n]{0,20}(\d+(?:\.\d+)?)"])
    period = _extract_period(text) or _extract_period(doc.title)

    if not any(value is not None for value in [
        revenue_growth,
        profit_growth,
        gross_margin,
        roe,
        debt_ratio,
        pe,
        pb,
        ps,
        market_cap,
    ]):
        return None

    return FinancialSnapshot(
        company=profile.name,
        ticker=profile.ticker,
        period=period,
        revenue_growth=revenue_growth,
        profit_growth=profit_growth,
        gross_margin=gross_margin,
        roe=roe,
        debt_ratio=debt_ratio,
        pe=pe,
        pb=pb,
        ps=ps,
        market_cap=market_cap,
        metadata={"source_doc_id": doc.id, "source_type": doc.source_type, "title": doc.title},
    )


def _price_from_doc(profile: CompanyProfile, doc: SourceDocument) -> PriceSnapshot | None:
    metadata = doc.metadata or {}
    latest = metadata.get("latest") or {}
    close = _coerce_float(latest.get("Close")) or _extract_number(
        doc.content,
        [r"(?:latest close|close|收盘价)[^\d\n]{0,12}(\d+(?:\.\d+)?)"],
    )
    change_3m = _coerce_float(metadata.get("window_change_pct")) or _extract_pct(
        doc.content,
        [r"(?:60 trading day change|3月涨跌幅|3m)[^%\n]{0,18}?([+-]?\d+(?:\.\d+)?)\s*%"],
    )
    date = str(latest.get("Date") or "") or _extract_date(doc.content)

    if close is None and change_3m is None:
        return None

    return PriceSnapshot(
        company=profile.name,
        ticker=profile.ticker,
        date=date or None,
        close=close,
        change_3m=change_3m,
        metadata={"source_doc_id": doc.id, "source_type": doc.source_type, "title": doc.title, **metadata},
    )


def _financial_score(snapshot: FinancialSnapshot, doc: SourceDocument) -> int:
    fields = [
        snapshot.revenue_growth,
        snapshot.profit_growth,
        snapshot.gross_margin,
        snapshot.roe,
        snapshot.debt_ratio,
        snapshot.pe,
        snapshot.pb,
        snapshot.ps,
        snapshot.market_cap,
    ]
    return sum(value is not None for value in fields) * 10 + (1 if doc.published_at else 0)


def _price_score(snapshot: PriceSnapshot, doc: SourceDocument) -> int:
    fields = [
        snapshot.close,
        snapshot.change_1m,
        snapshot.change_3m,
        snapshot.change_6m,
        snapshot.change_12m,
    ]
    return sum(value is not None for value in fields) * 10 + (1 if doc.published_at else 0)


def _extract_pct(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _coerce_float(match.group(1))
    return None


def _extract_number(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _coerce_float(match.group(1))
    return None


def _extract_period(text: str) -> str | None:
    match = re.search(r"(20\d{2}\s*(?:Q[1-4]|H1|FY|年报|半年报|一季报|三季报))", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _extract_date(text: str) -> str | None:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    return match.group(1) if match else None


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    raw = str(value).strip().replace("%", "").replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None
