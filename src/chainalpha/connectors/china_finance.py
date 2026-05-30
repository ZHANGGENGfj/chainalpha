from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json

from chainalpha.connectors.base import (
    BaseConnector,
    FetchRequest,
    SourceDocument,
    apply_limit,
    build_source_document,
    text_matches,
)
from chainalpha.connectors.http_utils import http_get


def _normalize_symbol(symbol: str) -> str:
    raw = symbol.strip().lower().replace(".", "")
    if raw.startswith(("sh", "sz", "bj")):
        return raw
    if raw.isdigit():
        if raw.startswith(("6", "9")):
            return f"sh{raw}"
        return f"sz{raw}"
    return raw


def _plain_code(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    return normalized[2:] if len(normalized) > 2 else normalized


class TencentQuoteConnector(BaseConnector):
    connector_type = "tencent_quotes"

    def __init__(self, name: str, symbols: list[str], timeout: int = 20, **params: Any) -> None:
        super().__init__(name, symbols=symbols, **params)
        self.symbols = symbols
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        normalized = [_normalize_symbol(symbol) for symbol in self.symbols]
        if not normalized:
            return []
        url = f"https://qt.gtimg.cn/q={','.join(normalized)}"
        resp = http_get(url, timeout=self.timeout, headers={"Referer": "https://gu.qq.com/"})
        docs: list[SourceDocument] = []
        for line in resp.text.split(";"):
            doc = self._line_to_doc(line, request)
            if doc:
                docs.append(doc)
        return apply_limit(docs, request.limit)

    def _line_to_doc(self, line: str, request: FetchRequest) -> SourceDocument | None:
        if "=" not in line:
            return None
        left, right = line.split("=", 1)
        symbol = left.replace("v_", "").strip()
        payload = right.strip().strip('"')
        if not payload or "~" not in payload:
            return None
        parts = payload.split("~")
        if len(parts) < 6:
            return None
        name = parts[1].strip() or symbol
        code = parts[2].strip() or _plain_code(symbol)
        latest = _to_float(parts[3])
        prev_close = _to_float(parts[4])
        open_price = _to_float(parts[5])
        pct = None
        if latest is not None and prev_close not in (None, 0):
            pct = (latest / prev_close - 1) * 100
        title = f"Tencent quote {name} ({code})"
        content = "\n".join(
            [
                f"Name: {name}",
                f"Symbol: {symbol}",
                f"Code: {code}",
                f"Latest close: {latest if latest is not None else '-'}",
                f"Previous close: {prev_close if prev_close is not None else '-'}",
                f"Open: {open_price if open_price is not None else '-'}",
                f"Daily change: {pct:.2f}%" if pct is not None else "Daily change: -",
            ]
        )
        if not text_matches(f"{title}\n{content}", request):
            return None
        return build_source_document(
            source=self.name,
            source_type="price_data",
            title=title,
            content=content,
            url=f"https://gu.qq.com/{symbol}",
            metadata={
                "symbol": symbol,
                "provider": "tencent",
                "latest": {"Date": datetime.now().strftime("%Y-%m-%d"), "Close": latest},
                "previous_close": prev_close,
                "open": open_price,
                "window_change_pct": pct,
            },
        )


class AkshareNewsConnector(BaseConnector):
    connector_type = "akshare_news"

    def __init__(
        self,
        name: str,
        dataset: str,
        symbols: list[str] | None = None,
        timeout: int = 20,
        **params: Any,
    ) -> None:
        super().__init__(name, dataset=dataset, symbols=symbols or [], **params)
        self.dataset = dataset
        self.symbols = symbols or []
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        ak = _import_akshare()
        docs: list[SourceDocument] = []
        if self.dataset == "stock_news_em":
            targets = self.symbols or [_plain_code(term) for term in request.terms if _plain_code(term).isdigit()]
            for symbol in targets:
                docs.extend(self._from_df(ak.stock_news_em(symbol=symbol), request, source_type="stock_news", default_title=f"{symbol} 个股新闻"))
        elif self.dataset == "stock_info_global_em":
            docs.extend(self._from_df(ak.stock_info_global_em(), request, source_type="global_news_em", default_title="东财全球资讯"))
        elif self.dataset == "stock_info_global_cls":
            docs.extend(self._from_df(ak.stock_info_global_cls(), request, source_type="global_news_cls", default_title="财联社全球资讯"))
        else:
            raise ValueError(f"Unsupported akshare dataset: {self.dataset}")
        return apply_limit(docs, request.limit)

    def _from_df(self, df: Any, request: FetchRequest, *, source_type: str, default_title: str) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        records = _df_to_records(df)
        for rec in records:
            title = str(
                rec.get("标题")
                or rec.get("新闻标题")
                or rec.get("title")
                or rec.get("Headline")
                or default_title
            ).strip()
            content_fields = []
            for key in ("内容", "摘要", "新闻内容", "content", "简介", "Title", "detail"):
                value = rec.get(key)
                if value:
                    content_fields.append(f"{key}: {value}")
            if not content_fields:
                content_fields.append(json.dumps(rec, ensure_ascii=False))
            content = "\n".join(content_fields)
            published = _parse_dt(rec.get("发布时间") or rec.get("时间") or rec.get("date"))
            url = str(rec.get("链接") or rec.get("新闻链接") or rec.get("url") or "").strip() or None
            if not text_matches(f"{title}\n{content}", request):
                continue
            docs.append(
                build_source_document(
                    source=self.name,
                    source_type=source_type,
                    title=title,
                    content=content,
                    url=url,
                    published_at=published,
                    metadata={"provider": "akshare", "dataset": self.dataset, "raw": rec},
                )
            )
        return docs


class MooTDXQuoteConnector(BaseConnector):
    connector_type = "mootdx_quotes"

    def __init__(self, name: str, symbols: list[str], offset: int = 60, timeout: int = 15, **params: Any) -> None:
        super().__init__(name, symbols=symbols, offset=offset, timeout=timeout, **params)
        self.symbols = symbols
        self.offset = offset
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        Quotes = _import_mootdx_quotes()
        client = Quotes.factory(market="std", bestip=True, multithread=True, heartbeat=False, timeout=self.timeout)
        docs: list[SourceDocument] = []
        for raw_symbol in self.symbols:
            symbol = _plain_code(raw_symbol)
            bars = client.bars(symbol=symbol, frequency=9, offset=self.offset)
            if bars is None or getattr(bars, "empty", True):
                continue
            latest_row = bars.iloc[-1]
            first_row = bars.iloc[0]
            latest_close = _to_float(latest_row.get("close"))
            first_close = _to_float(first_row.get("close"))
            pct = None
            if latest_close is not None and first_close not in (None, 0):
                pct = (latest_close / first_close - 1) * 100
            title = f"MooTDX quote {symbol}"
            content = "\n".join(
                [
                    f"Symbol: {symbol}",
                    f"Latest date: {latest_row.get('datetime') or latest_row.get('date') or '-'}",
                    f"Latest close: {latest_close if latest_close is not None else '-'}",
                    f"{self.offset} bar change: {pct:.2f}%" if pct is not None else f"{self.offset} bar change: -",
                ]
            )
            if not text_matches(f"{title}\n{content}", request):
                continue
            docs.append(
                build_source_document(
                    source=self.name,
                    source_type="price_data",
                    title=title,
                    content=content,
                    metadata={
                        "provider": "mootdx",
                        "symbol": symbol,
                        "latest": {
                            "Date": str(latest_row.get("datetime") or latest_row.get("date") or datetime.now().strftime("%Y-%m-%d")),
                            "Close": latest_close,
                        },
                        "window_change_pct": pct,
                    },
                )
            )
        return apply_limit(docs, request.limit)


class MooTDXFinancialConnector(BaseConnector):
    connector_type = "mootdx_financials"

    def __init__(
        self,
        name: str,
        symbols: list[str],
        downdir: str = "./data/cache/mootdx_financials",
        filename: str | None = None,
        **params: Any,
    ) -> None:
        super().__init__(name, symbols=symbols, downdir=downdir, filename=filename, **params)
        self.symbols = symbols
        self.downdir = Path(downdir)
        self.filename = filename

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        Affair = _import_mootdx_affair()
        self.downdir.mkdir(parents=True, exist_ok=True)
        target_file = self.filename or self._latest_filename(Affair)
        self._ensure_file(Affair, target_file)
        data = Affair.parse(downdir=str(self.downdir), filename=target_file)
        docs: list[SourceDocument] = []
        for symbol in self.symbols:
            code = _plain_code(symbol)
            content_parts: list[str] = [f"Symbol: {code}", f"File: {target_file}"]
            for table_name, table_df in _iter_table_frames(data):
                row = _find_symbol_row(table_df, code)
                if row is None:
                    continue
                pairs = [f"{col}={row[col]}" for col in list(row.index)[:18]]
                content_parts.append(f"[{table_name}] " + ", ".join(pairs))
            if len(content_parts) <= 2:
                continue
            content = "\n".join(content_parts)
            title = f"MooTDX financial report snapshot {code}"
            if not text_matches(f"{title}\n{content}", request):
                continue
            docs.append(
                build_source_document(
                    source=self.name,
                    source_type="financial_statement",
                    title=title,
                    content=content,
                    path=str(self.downdir / target_file),
                    metadata={"provider": "mootdx", "filename": target_file, "symbol": code},
                )
            )
        return apply_limit(docs, request.limit)

    def _latest_filename(self, Affair: Any) -> str:
        files = Affair.files()
        if not files:
            raise RuntimeError("MooTDX Affair.files() returned no financial files.")
        if isinstance(files, list):
            candidates = [str(item.get("filename") or "") for item in files if isinstance(item, dict)]
        else:
            candidates = []
        candidates = [item for item in candidates if item]
        if not candidates:
            raise RuntimeError("MooTDX Affair.files() did not expose usable filenames.")
        return sorted(candidates)[-1]

    def _ensure_file(self, Affair: Any, filename: str) -> None:
        path = self.downdir / filename
        if path.exists():
            return
        if hasattr(Affair, "fetch"):
            Affair.fetch(downdir=str(self.downdir), filename=filename)
            return
        if hasattr(Affair, "download"):
            Affair.download(downdir=str(self.downdir), filename=filename)
            return
        raise RuntimeError(f"MooTDX financial file missing locally and no download method was found: {filename}")


def _import_akshare() -> Any:
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("akshare is required for AKShare-based connectors. Install it first.") from exc
    return ak


def _import_mootdx_quotes() -> Any:
    try:
        from mootdx.quotes import Quotes  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("mootdx is required for MooTDX quote connector. Install it first.") from exc
    return Quotes


def _import_mootdx_affair() -> Any:
    try:
        from mootdx.affair import Affair  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("mootdx is required for MooTDX financial connector. Install it first.") from exc
    return Affair


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _df_to_records(df: Any) -> list[dict[str, Any]]:
    if df is None:
        return []
    try:
        return df.to_dict(orient="records")
    except Exception:
        return []


def _iter_table_frames(data: Any) -> list[tuple[str, Any]]:
    if isinstance(data, dict):
        return [(str(name), frame) for name, frame in data.items()]
    return [("financial_data", data)]


def _find_symbol_row(df: Any, symbol: str) -> Any | None:
    if df is None or getattr(df, "empty", True):
        return None
    for key in ("code", "symbol", "股票代码", "证券代码"):
        if key in getattr(df, "columns", []):
            matched = df[df[key].astype(str).str.zfill(6) == symbol.zfill(6)]
            if not getattr(matched, "empty", True):
                return matched.iloc[0]
    return None
