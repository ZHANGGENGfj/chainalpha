from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from chainalpha.connectors.http_utils import best_effort_json, http_get, parse_html
from chainalpha.schemas import CompanyProfile, FinancialSnapshot, PriceSnapshot


QUOTE_FIELDS = ",".join(
    [
        "f43",   # latest price, scaled by 100
        "f57",   # code
        "f58",   # name
        "f84",   # total shares
        "f85",   # free float shares
        "f116",  # total market cap, yuan
        "f117",  # float market cap, yuan
        "f162",  # dynamic PE, scaled by 100
        "f164",  # PE TTM, scaled by 100 on many Eastmoney endpoints
        "f167",  # PB, scaled by 100
        "f168",  # PS, scaled by 100 on many Eastmoney endpoints
    ]
)


def fetch_eastmoney_snapshots(
    profiles: list[CompanyProfile],
    *,
    timeout: int = 8,
) -> tuple[dict[str, FinancialSnapshot], dict[str, PriceSnapshot], list[str]]:
    financials: dict[str, FinancialSnapshot] = {}
    prices: dict[str, PriceSnapshot] = {}
    warnings: list[str] = []
    for profile in profiles:
        ticker = _normalize_ticker(profile.ticker)
        if not ticker:
            continue
        try:
            financial, price = _fetch_one(profile, ticker, timeout=timeout)
        except Exception as exc:
            warnings.append(f"Eastmoney snapshot failed for {profile.name}({ticker}): {type(exc).__name__}: {exc}")
            continue
        key = _key(profile.name, ticker)
        if financial:
            financials[key] = financial
        if price:
            prices[key] = price
    return financials, prices, warnings


def merge_financial_snapshot(base: FinancialSnapshot | None, extra: FinancialSnapshot | None) -> FinancialSnapshot | None:
    if base is None:
        return extra
    if extra is None:
        return base
    merged = FinancialSnapshot(**base.__dict__)
    for field in [
        "revenue",
        "net_profit",
        "revenue_growth",
        "profit_growth",
        "gross_margin",
        "net_margin",
        "roe",
        "eps",
        "bps",
        "debt_ratio",
        "pe",
        "pb",
        "ps",
        "market_cap",
        "float_market_cap",
        "total_shares",
        "float_shares",
    ]:
        if getattr(merged, field, None) is None and getattr(extra, field, None) is not None:
            setattr(merged, field, getattr(extra, field))
    merged.metadata = {**(extra.metadata or {}), **(base.metadata or {})}
    return merged


def merge_price_snapshot(base: PriceSnapshot | None, extra: PriceSnapshot | None) -> PriceSnapshot | None:
    if base is None:
        return extra
    if extra is None:
        return base
    merged = PriceSnapshot(**base.__dict__)
    for field in ["date", "close", "change_1m", "change_3m", "change_6m", "change_12m"]:
        if getattr(merged, field, None) is None and getattr(extra, field, None) is not None:
            setattr(merged, field, getattr(extra, field))
    merged.metadata = {**(extra.metadata or {}), **(base.metadata or {})}
    return merged


def _fetch_one(profile: CompanyProfile, ticker: str, *, timeout: int) -> tuple[FinancialSnapshot | None, PriceSnapshot | None]:
    quote = _fetch_quote(ticker, timeout=timeout)
    page_metrics = _fetch_stockdata_page(ticker, timeout=timeout)
    metrics = {**quote, **{k: v for k, v in page_metrics.items() if v is not None}}
    if not metrics:
        return None, None
    company = str(metrics.get("name") or profile.name)
    financial = FinancialSnapshot(
        company=profile.name or company,
        ticker=ticker,
        period=str(metrics.get("period") or "") or None,
        revenue=_num(metrics.get("revenue")),
        net_profit=_num(metrics.get("net_profit")),
        revenue_growth=_num(metrics.get("revenue_growth")),
        profit_growth=_num(metrics.get("profit_growth")),
        gross_margin=_num(metrics.get("gross_margin")),
        net_margin=_num(metrics.get("net_margin")),
        roe=_num(metrics.get("roe")),
        eps=_num(metrics.get("eps")),
        bps=_num(metrics.get("bps")),
        debt_ratio=_num(metrics.get("debt_ratio")),
        pe=_num(metrics.get("pe")),
        pb=_num(metrics.get("pb")),
        ps=_num(metrics.get("ps")),
        market_cap=_num(metrics.get("market_cap")),
        float_market_cap=_num(metrics.get("float_market_cap")),
        total_shares=_num(metrics.get("total_shares")),
        float_shares=_num(metrics.get("float_shares")),
        metadata={
            "source": "eastmoney",
            "source_type": "quote_financial_snapshot",
            "quote_url": f"https://quote.eastmoney.com/{_market_prefix(ticker)}{ticker}.html",
            "stockdata_url": f"https://data.eastmoney.com/stockdata/{ticker}.html",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    price = PriceSnapshot(
        company=profile.name or company,
        ticker=ticker,
        date=datetime.now().date().isoformat(),
        close=_num(metrics.get("close")),
        metadata={
            "source": "eastmoney",
            "source_type": "quote_price_snapshot",
            "quote_url": f"https://quote.eastmoney.com/{_market_prefix(ticker)}{ticker}.html",
        },
    )
    metric_fields = [
        "revenue",
        "net_profit",
        "revenue_growth",
        "profit_growth",
        "gross_margin",
        "net_margin",
        "roe",
        "eps",
        "bps",
        "debt_ratio",
        "pe",
        "pb",
        "ps",
        "market_cap",
        "float_market_cap",
        "total_shares",
        "float_shares",
    ]
    if not any(getattr(financial, field, None) is not None for field in metric_fields):
        financial = None
    if price.close is None:
        price = None
    return financial, price


def _fetch_quote(ticker: str, *, timeout: int) -> dict[str, Any]:
    secid = _secid(ticker)
    resp = http_get(
        "https://push2.eastmoney.com/api/qt/stock/get",
        params={"secid": secid, "fields": QUOTE_FIELDS},
        timeout=timeout,
    )
    data = (best_effort_json(resp) or {}).get("data") or {}
    return {
        "name": data.get("f58"),
        "close": _scaled(data.get("f43")),
        "market_cap": _yuan_to_yi(data.get("f116")),
        "float_market_cap": _yuan_to_yi(data.get("f117")),
        "total_shares": _shares_to_yi(data.get("f84")),
        "float_shares": _shares_to_yi(data.get("f85")),
        "pe": _scaled(data.get("f162")),
        "pe_ttm": _scaled(data.get("f164")),
        "pb": _scaled(data.get("f167")),
        "ps": _scaled(data.get("f168")),
    }


def _fetch_stockdata_page(ticker: str, *, timeout: int) -> dict[str, Any]:
    urls = [
        f"https://data.eastmoney.com/stockdata/{ticker}.html",
        f"https://quote.eastmoney.com/{_market_prefix(ticker)}{ticker}.html",
    ]
    merged: dict[str, Any] = {}
    for url in urls:
        try:
            resp = http_get(url, timeout=timeout)
        except Exception:
            continue
        text = parse_html(resp.text, base_url=url).text()
        if not text:
            continue
        merged.update({k: v for k, v in _parse_core_text(text).items() if v is not None})
    return merged


def _parse_core_text(text: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", " ", text or "")
    revenue, revenue_growth = _metric_with_yoy(compact, "总营收")
    net_profit, profit_growth = _metric_with_yoy(compact, "净利润")
    return {
        "eps": _after_label(compact, "收益\\(一\\)|每股收益"),
        "pe": _after_label(compact, "PE\\(动\\)|市盈率"),
        "bps": _after_label(compact, "每股净资产"),
        "pb": _after_label(compact, "市净率"),
        "revenue": revenue,
        "revenue_growth": revenue_growth,
        "net_profit": net_profit,
        "profit_growth": profit_growth,
        "gross_margin": _after_label(compact, "毛利率"),
        "net_margin": _after_label(compact, "净利率"),
        "roe": _after_label(compact, "ROE"),
        "debt_ratio": _after_label(compact, "负债率"),
        "total_shares": _after_label(compact, "总股本"),
        "market_cap": _after_label(compact, "总值"),
        "float_shares": _after_label(compact, "流通股"),
        "float_market_cap": _after_label(compact, "流值"),
    }


def _metric_with_yoy(text: str, label: str) -> tuple[float | None, float | None]:
    value = _after_label(text, label)
    pattern = rf"{label}[^0-9+\-]*[+\-]?\d+(?:\.\d+)?\s*(?:亿|万|元)?[^%]{{0,30}}同比[^0-9+\-]*([+\-]?\d+(?:\.\d+)?)\s*%"
    match = re.search(pattern, text)
    return value, _num(match.group(1)) if match else None


def _after_label(text: str, label_pattern: str) -> float | None:
    match = re.search(rf"(?:{label_pattern})\s*[:：]?\s*([+\-]?\d+(?:\.\d+)?)\s*(亿|万|元|%)?", text)
    if not match:
        return None
    value = _num(match.group(1))
    unit = match.group(2) or ""
    if value is None:
        return None
    if unit == "万":
        return value / 10000
    return value


def _normalize_ticker(value: str | None) -> str:
    if not value:
        return ""
    match = re.search(r"(\d{6})", str(value))
    return match.group(1) if match else ""


def _secid(ticker: str) -> str:
    market = "1" if ticker.startswith(("5", "6", "9")) else "0"
    return f"{market}.{ticker}"


def _market_prefix(ticker: str) -> str:
    return "sh" if ticker.startswith(("5", "6", "9")) else "sz"


def _key(company: str, ticker: str | None) -> str:
    if ticker:
        return f"ticker:{ticker.strip().lower()}"
    return f"company:{company.strip().lower()}"


def _scaled(value: Any) -> float | None:
    num = _num(value)
    if num is None:
        return None
    if abs(num) >= 1000:
        return num / 100
    return num


def _yuan_to_yi(value: Any) -> float | None:
    num = _num(value)
    return None if num is None else num / 100000000


def _shares_to_yi(value: Any) -> float | None:
    num = _num(value)
    return None if num is None else num / 100000000


def _num(value: Any) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    raw = str(value).strip().replace(",", "").replace("%", "")
    try:
        return float(raw)
    except ValueError:
        return None
