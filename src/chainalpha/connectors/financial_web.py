from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import requests

from chainalpha.connectors.base import BaseConnector, FetchRequest, apply_limit, build_source_document
from chainalpha.schemas import SourceDocument


class FinancialWebConnector(BaseConnector):
    """Fetch public finance-summary pages for A-share companies.

    This connector intentionally fetches web pages instead of parsing full PDF
    reports. The downstream web_financial_facts extractor classifies snippets
    into revenue, margin, cash-flow, inventory, receivables, capex, R&D, risk,
    and other categories.
    """

    connector_type = "financial_web"

    def __init__(
        self,
        name: str,
        symbols: list[str] | None = None,
        timeout: int = 6,
        max_pages_per_symbol: int = 5,
        **params: Any,
    ) -> None:
        super().__init__(name, **params)
        self.symbols = symbols or []
        self.timeout = timeout
        self.max_pages_per_symbol = max_pages_per_symbol

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        symbols = _unique([*self.symbols, *_symbols_from_request(request)])
        docs: list[SourceDocument] = []
        errors: list[str] = []
        for symbol in symbols:
            api_docs, api_errors = _fetch_akshare_financial_docs(symbol)
            docs.extend(api_docs)
            errors.extend(api_errors)
            symbol_errors: list[str] = []
            for page in _page_specs(symbol)[: self.max_pages_per_symbol]:
                try:
                    text = _fetch_text(page["url"], timeout=self.timeout)
                except Exception as exc:  # noqa: BLE001
                    symbol_errors.append(f"{page['source']} {symbol}: {type(exc).__name__}: {exc}")
                    continue
                if not text or len(text) < 80:
                    continue
                docs.append(
                    build_source_document(
                        source=page["source"],
                        source_type="financial_web",
                        title=f"{symbol} {page['title']}",
                        content=text,
                        url=page["url"],
                        published_at=datetime.now(),
                        metadata={
                            "symbol": symbol,
                            "financial_web_page": page["kind"],
                            "connector_errors": symbol_errors[-5:],
                        },
                    )
                )
            errors.extend(symbol_errors)
        return apply_limit(docs, request.limit)


def _fetch_akshare_financial_docs(symbol: str) -> tuple[list[SourceDocument], list[str]]:
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        return [], ["akshare not installed; skip online financial tables."]

    code = _plain_code(symbol)
    market_symbol = f"{_market_prefix(symbol)}{code}"
    docs: list[SourceDocument] = []
    errors: list[str] = []

    calls = [
        (
            "AkShare财务指标",
            "financial_indicator",
            lambda: ak.stock_financial_analysis_indicator(symbol=code),
        ),
        (
            "新浪财务报表-资产负债表",
            "balance_sheet",
            lambda: ak.stock_financial_report_sina(stock=market_symbol, symbol="资产负债表"),
        ),
        (
            "新浪财务报表-利润表",
            "income_statement",
            lambda: ak.stock_financial_report_sina(stock=market_symbol, symbol="利润表"),
        ),
        (
            "新浪财务报表-现金流量表",
            "cashflow_statement",
            lambda: ak.stock_financial_report_sina(stock=market_symbol, symbol="现金流量表"),
        ),
    ]
    for source, kind, fn in calls:
        try:
            df = fn()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source} {symbol}: {type(exc).__name__}: {exc}")
            continue
        if df is None or getattr(df, "empty", False):
            continue
        content = _dataframe_to_text(df)
        docs.append(
            build_source_document(
                source=source,
                source_type="financial_web",
                title=f"{symbol} {source}",
                content=content,
                url=f"akshare://{kind}/{market_symbol}",
                published_at=datetime.now(),
                metadata={
                    "symbol": symbol,
                    "financial_web_page": kind,
                    "rows": int(getattr(df, "shape", [0])[0]),
                    "columns": [str(col) for col in getattr(df, "columns", [])],
                },
            )
        )
    return docs, errors


def _dataframe_to_text(df: Any, max_rows: int = 24) -> str:
    try:
        slim = df.head(max_rows).copy()
        return slim.to_csv(index=False)
    except Exception:
        return str(df)[:12000]


def _page_specs(symbol: str) -> list[dict[str, str]]:
    code = _plain_code(symbol)
    em_code = _eastmoney_code(symbol)
    market_prefix = _market_prefix(symbol)
    return [
        {
            "source": "东方财富F10",
            "kind": "finance_analysis",
            "title": "财务分析",
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/Index?type=web&code={em_code}",
        },
        {
            "source": "东方财富F10",
            "kind": "business_analysis",
            "title": "经营分析",
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/Index?type=web&code={em_code}",
        },
        {
            "source": "东方财富F10",
            "kind": "capital_structure",
            "title": "资本结构",
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/CapitalStockStructure/Index?type=web&code={em_code}",
        },
        {
            "source": "同花顺F10",
            "kind": "finance",
            "title": "财务概况",
            "url": f"https://basic.10jqka.com.cn/{code}/finance.html",
        },
        {
            "source": "同花顺F10",
            "kind": "operate",
            "title": "经营分析",
            "url": f"https://basic.10jqka.com.cn/{code}/operate.html",
        },
        {
            "source": "新浪财经",
            "kind": "financial_indicator",
            "title": "财务指标",
            "url": f"https://finance.sina.com.cn/realstock/company/{market_prefix}{code}/nc.shtml",
        },
        {
            "source": "新浪财经",
            "kind": "finance_summary",
            "title": "财务摘要",
            "url": f"https://money.finance.sina.com.cn/corp/go.php/vFD_FinancialGuideLine/stockid/{code}/displaytype/4.phtml",
        },
    ]


def _fetch_text(url: str, *, timeout: int) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    session = requests.Session()
    session.trust_env = False
    resp = session.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return _html_to_text(resp.text)


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text[:12000]


def _symbols_from_request(request: FetchRequest) -> list[str]:
    symbols = []
    for term in request.terms:
        symbols.extend(re.findall(r"\b[036]\d{5}(?:\.(?:SZ|SH|BJ))?\b", term.upper()))
    return symbols


def _plain_code(symbol: str) -> str:
    match = re.search(r"([036]\d{5})", str(symbol).upper())
    return match.group(1) if match else str(symbol).strip()


def _market_prefix(symbol: str) -> str:
    raw = str(symbol).upper()
    code = _plain_code(raw)
    if raw.endswith(".SH") or code.startswith("6"):
        return "sh"
    if raw.endswith(".BJ") or code.startswith(("8", "4")):
        return "bj"
    return "sz"


def _eastmoney_code(symbol: str) -> str:
    code = _plain_code(symbol)
    prefix = _market_prefix(symbol).upper()
    return f"{prefix}{code}"


def _unique(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out
