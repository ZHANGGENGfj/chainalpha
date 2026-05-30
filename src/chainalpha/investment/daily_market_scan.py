from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
import json

from chainalpha.connectors.base import SourceDocument, build_source_document
from chainalpha.storage.local_store import JsonlStore, LocalResearchStore, sha256_text


EVENT_CATEGORIES = ("policy", "industry", "company", "international")


@dataclass
class ClassifiedEvent:
    event_id: str
    category: str
    tags: list[str]
    title: str
    source: str
    source_type: str
    published_at: str | None = None
    url: str | None = None
    reason: str = ""
    content_preview: str = ""


@dataclass
class TechnicalSignal:
    symbol: str
    name: str = ""
    score: float = 0.0
    signals: list[str] = field(default_factory=list)
    latest_close: float | None = None
    change_pct: float | None = None
    turnover: float | None = None
    net_inflow: float | None = None
    source: str = "akshare"


@dataclass
class DailyMarketScanReport:
    generated_at: str
    query: str
    classified_events: dict[str, list[ClassifiedEvent]]
    technical_candidates: list[TechnicalSignal]
    sector_candidates: list[TechnicalSignal]
    raw_documents: list[SourceDocument]
    storage: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "query": self.query,
            "classified_events": {
                key: [asdict(item) for item in values]
                for key, values in self.classified_events.items()
            },
            "technical_candidates": [asdict(item) for item in self.technical_candidates],
            "sector_candidates": [asdict(item) for item in self.sector_candidates],
            "raw_documents": [doc.to_dict() for doc in self.raw_documents],
            "storage": self.storage,
            "warnings": self.warnings,
        }


def build_daily_market_scan(
    *,
    query: str = "A股市场",
    symbols: list[str] | None = None,
    limit: int = 80,
    top_k: int = 30,
    persist_dir: str | Path | None = "./data/daily_market",
) -> DailyMarketScanReport:
    warnings: list[str] = []
    docs = _fetch_daily_documents(query=query, limit=limit, warnings=warnings)
    docs = _dedup_documents(docs)
    classified = _classify_documents(docs, per_category=max(5, top_k // 2))

    technical_candidates = _screen_technical_candidates(symbols=symbols or [], top_k=top_k, warnings=warnings)
    sector_candidates = _screen_sector_candidates(top_k=max(10, top_k // 2), warnings=warnings)

    report = DailyMarketScanReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        query=query,
        classified_events=classified,
        technical_candidates=technical_candidates,
        sector_candidates=sector_candidates,
        raw_documents=docs,
        warnings=warnings,
    )
    if persist_dir:
        report.storage = persist_daily_market_scan(report, persist_dir)
    return report


def persist_daily_market_scan(report: DailyMarketScanReport, base_dir: str | Path = "./data/daily_market") -> dict[str, Any]:
    run_date = report.generated_at[:10]
    base = Path(base_dir)
    day_dir = base / run_date
    raw_dir = day_dir / "raw"
    processed_dir = day_dir / "processed"
    reports_dir = day_dir / "reports"
    for path in (raw_dir, processed_dir, reports_dir):
        path.mkdir(parents=True, exist_ok=True)

    raw_store = LocalResearchStore(base_dir=base)
    new_docs, raw_meta = raw_store.save_raw_documents(report.raw_documents, source_name="daily_market_news", run_date=run_date)
    raw_jsonl = raw_dir / "deduped_news.jsonl"
    JsonlStore(raw_jsonl).append_many([_doc_to_row(doc) for doc in new_docs])

    events = [
        asdict(event)
        for category in EVENT_CATEGORIES
        for event in report.classified_events.get(category, [])
    ]
    events_jsonl = processed_dir / "classified_events.jsonl"
    JsonlStore(events_jsonl).append_many(events)

    technical_jsonl = processed_dir / "technical_candidates.jsonl"
    JsonlStore(technical_jsonl).append_many([asdict(item) for item in report.technical_candidates])

    sector_jsonl = processed_dir / "sector_candidates.jsonl"
    JsonlStore(sector_jsonl).append_many([asdict(item) for item in report.sector_candidates])

    summary_json = reports_dir / "daily_market_scan.json"
    summary_md = reports_dir / "daily_market_scan.md"
    summary_json.write_text(json.dumps(report.to_dict() | {"raw_documents": [doc.to_dict() for doc in new_docs]}, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_md.write_text(render_daily_market_scan_markdown(report), encoding="utf-8")

    return {
        "date": run_date,
        "day_dir": str(day_dir),
        "raw_store": raw_meta,
        "deduped_documents": len(new_docs),
        "raw_jsonl": str(raw_jsonl),
        "classified_events_jsonl": str(events_jsonl),
        "technical_candidates_jsonl": str(technical_jsonl),
        "sector_candidates_jsonl": str(sector_jsonl),
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
    }


def render_daily_market_scan_markdown(report: DailyMarketScanReport) -> str:
    lines = [
        f"# Daily Market Scan: {report.query}",
        "",
        f"> Generated at: {report.generated_at}",
        "",
        "## 1. 信息收集概览",
        "",
        f"- Raw documents: {len(report.raw_documents)}",
        f"- Technical candidates: {len(report.technical_candidates)}",
        f"- Sector candidates: {len(report.sector_candidates)}",
        "",
    ]
    if report.warnings:
        lines.extend(["## Warnings", ""])
        for item in report.warnings[:20]:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(["## 2. 新闻事件分类", ""])
    titles = {
        "policy": "政策",
        "industry": "行业",
        "company": "公司",
        "international": "国际",
    }
    for category in EVENT_CATEGORIES:
        lines.append(f"### {titles[category]}")
        events = report.classified_events.get(category, [])
        if not events:
            lines.append("- 数据缺口：当前未抓取到该类事件。")
        for item in events:
            date = item.published_at or "date-unknown"
            path = item.url or "-"
            lines.append(f"- {date} | {item.title} | {item.source} | {item.reason} | {path}")
        lines.append("")

    lines.extend(["## 3. 个股技术初筛", ""])
    if report.technical_candidates:
        lines.extend([
            "| Rank | Symbol | Name | Score | Change | Turnover | Net inflow | Signals |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ])
        for idx, item in enumerate(report.technical_candidates, 1):
            lines.append(
                f"| {idx} | {item.symbol} | {item.name or '-'} | {item.score:.1f} | "
                f"{_fmt_pct(item.change_pct)} | {_fmt_num(item.turnover)} | {_fmt_num(item.net_inflow)} | "
                f"{'; '.join(item.signals) or '-'} |"
            )
    else:
        lines.append("- 数据缺口：当前未筛出个股技术信号。")
    lines.append("")

    lines.extend(["## 4. 板块技术初筛", ""])
    if report.sector_candidates:
        lines.extend([
            "| Rank | Sector | Score | Change | Turnover | Signals |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ])
        for idx, item in enumerate(report.sector_candidates, 1):
            lines.append(
                f"| {idx} | {item.name or item.symbol} | {item.score:.1f} | "
                f"{_fmt_pct(item.change_pct)} | {_fmt_num(item.turnover)} | {'; '.join(item.signals) or '-'} |"
            )
    else:
        lines.append("- 数据缺口：当前未筛出板块技术信号。")
    lines.append("")

    lines.extend([
        "## 5. 初筛规则说明",
        "",
        "- 新闻事件按关键词粗分为政策、行业、公司、国际四类。",
        "- 个股初筛优先看涨跌幅、成交活跃度、主力资金净流入、均线多头、量价突破。",
        "- 板块初筛优先看板块涨幅、成交活跃度和可获取的资金/涨跌数据。",
        "- 该扫描用于生成候选池，不构成投资建议，所有候选都需要进一步结合公告、财报和估值复核。",
        "",
    ])
    return "\n".join(lines)


def _fetch_daily_documents(*, query: str, limit: int, warnings: list[str]) -> list[SourceDocument]:
    docs: list[SourceDocument] = []
    ak = _import_akshare_safe(warnings)
    if ak is None:
        return docs

    datasets = [
        ("eastmoney_global", "global_news_em", "stock_info_global_em", {}),
        ("cls_global", "global_news_cls", "stock_info_global_cls", {}),
    ]
    for source, source_type, func_name, kwargs in datasets:
        df = _call_ak_func(ak, func_name, warnings, **kwargs)
        docs.extend(_records_to_documents(source, source_type, df, query=query))

    docs = _sort_docs(docs)
    return docs[:limit]


def _classify_documents(docs: list[SourceDocument], *, per_category: int) -> dict[str, list[ClassifiedEvent]]:
    buckets = {category: [] for category in EVENT_CATEGORIES}
    for doc in docs:
        text = f"{doc.title}\n{doc.content}"
        category, reason = _classify_text(text)
        tags = _extract_event_tags(text, category)
        if len(buckets[category]) >= per_category:
            continue
        buckets[category].append(
            ClassifiedEvent(
                event_id=_event_id(doc, category),
                category=category,
                tags=tags,
                title=doc.title,
                source=doc.source,
                source_type=doc.source_type,
                published_at=doc.published_at.isoformat()[:10] if doc.published_at else None,
                url=doc.url,
                reason=reason,
                content_preview=doc.content.replace("\n", " ")[:180],
            )
        )
    return buckets


def _dedup_documents(docs: list[SourceDocument]) -> list[SourceDocument]:
    seen: set[str] = set()
    out: list[SourceDocument] = []
    for doc in docs:
        key = _document_dedup_key(doc)
        if key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out


def _document_dedup_key(doc: SourceDocument) -> str:
    if doc.url:
        return "url:" + sha256_text(doc.url)
    title = " ".join((doc.title or "").split())
    content = " ".join((doc.content or "").split())[:1000]
    return "text:" + sha256_text(f"{doc.source}|{title}|{content}")


def _event_id(doc: SourceDocument, category: str) -> str:
    return sha256_text(f"{category}|{doc.source}|{doc.title}|{doc.url or ''}")[:24]


def _extract_event_tags(text: str, category: str) -> list[str]:
    low = text.lower()
    tag_rules = {
        "policy": {
            "货币政策": ["降准", "降息", "利率", "央行", "流动性"],
            "产业政策": ["补贴", "专项", "设备更新", "产业", "规划"],
            "监管政策": ["证监会", "监管", "减持", "并购", "退市"],
        },
        "industry": {
            "景气度": ["景气", "需求", "订单", "涨价", "库存"],
            "技术路线": ["ai", "芯片", "光模块", "800g", "1.6t", "cpo", "lpo", "算力"],
            "供需变化": ["产能", "供给", "供应", "短缺", "价格"],
        },
        "company": {
            "业绩": ["业绩", "营收", "净利润", "利润", "年报", "季报"],
            "订单合同": ["订单", "合同", "中标", "客户", "认证"],
            "资本动作": ["回购", "增持", "减持", "分红", "重组", "定增"],
        },
        "international": {
            "外围市场": ["美股", "纳斯达克", "标普", "欧洲", "日本"],
            "宏观流动性": ["美联储", "美元", "利率", "通胀", "就业"],
            "贸易地缘": ["关税", "制裁", "出口管制", "地缘"],
        },
    }
    tags: list[str] = []
    for tag, keywords in tag_rules.get(category, {}).items():
        if any(keyword.lower() in low for keyword in keywords):
            tags.append(tag)
    return tags or [category]


def _doc_to_row(doc: SourceDocument) -> dict[str, Any]:
    return {
        "id": doc.id,
        "title": doc.title,
        "content": doc.content,
        "url": doc.url,
        "path": doc.path,
        "source": doc.source,
        "source_type": doc.source_type,
        "published_at": doc.published_at.isoformat() if doc.published_at else None,
        "fetched_at": doc.fetched_at.isoformat() if doc.fetched_at else None,
        "metadata": doc.metadata,
        "dedup_key": _document_dedup_key(doc),
    }


def _screen_technical_candidates(
    *,
    symbols: list[str],
    top_k: int,
    warnings: list[str],
) -> list[TechnicalSignal]:
    ak = _import_akshare_safe(warnings)
    if ak is None:
        return []

    candidates: dict[str, TechnicalSignal] = {}
    for item in _fund_flow_candidates(ak, warnings, top_k=max(top_k * 2, 40)):
        candidates[item.symbol] = item

    if symbols:
        for symbol in symbols:
            code = _plain_code(symbol)
            candidates.setdefault(code, TechnicalSignal(symbol=code, name=code))
    elif not candidates:
        for item in _spot_candidates(ak, warnings, top_k=max(top_k * 2, 40)):
            candidates[item.symbol] = item

    for symbol, item in list(candidates.items()):
        _enrich_with_spot(ak, item, warnings)
        _enrich_with_ma_signal(ak, item, warnings)

    ranked = sorted(candidates.values(), key=lambda item: item.score, reverse=True)
    return [item for item in ranked if item.signals][:top_k]


def _screen_sector_candidates(*, top_k: int, warnings: list[str]) -> list[TechnicalSignal]:
    ak = _import_akshare_safe(warnings)
    if ak is None:
        return []
    df = _call_first_available(
        ak,
        warnings,
        [
            ("stock_board_industry_name_em", {}),
            ("stock_board_concept_name_em", {}),
        ],
    )
    rows = _df_to_records(df)
    out: list[TechnicalSignal] = []
    for rec in rows:
        name = _first_str(rec, ["板块名称", "名称", "name", "行业名称"])
        change = _first_float(rec, ["涨跌幅", "涨跌幅%", "最新涨跌幅", "change_pct"])
        turnover = _first_float(rec, ["成交额", "总市值", "turnover"])
        if not name:
            continue
        signals: list[str] = []
        score = 0.0
        if change is not None and change >= 2.0:
            signals.append(f"板块涨幅 {change:.2f}%")
            score += min(change * 6, 35)
        if turnover is not None:
            signals.append("成交活跃")
            score += 10
        if signals:
            out.append(TechnicalSignal(symbol=name, name=name, score=score, signals=signals, change_pct=change, turnover=turnover))
    return sorted(out, key=lambda item: item.score, reverse=True)[:top_k]


def _fund_flow_candidates(ak: Any, warnings: list[str], *, top_k: int) -> list[TechnicalSignal]:
    df = _call_first_available(
        ak,
        warnings,
        [
            ("stock_individual_fund_flow_rank", {"indicator": "今日"}),
            ("stock_individual_fund_flow_rank", {"indicator": "3日"}),
        ],
    )
    rows = _df_to_records(df)
    out: list[TechnicalSignal] = []
    for rec in rows[:top_k]:
        symbol = _plain_code(_first_str(rec, ["代码", "股票代码", "code", "symbol"]))
        if not symbol:
            continue
        name = _first_str(rec, ["名称", "股票简称", "name"])
        net = _first_float(rec, ["主力净流入", "今日主力净流入-净额", "净流入", "net_inflow"])
        change = _first_float(rec, ["涨跌幅", "今日涨跌幅", "change_pct"])
        signals: list[str] = []
        score = 0.0
        if net is not None and net > 0:
            signals.append("主力资金流入")
            score += 25
        if change is not None and change > 0:
            signals.append(f"价格上涨 {change:.2f}%")
            score += min(change * 4, 20)
        out.append(TechnicalSignal(symbol=symbol, name=name, score=score, signals=signals, change_pct=change, net_inflow=net))
    return out


def _spot_candidates(ak: Any, warnings: list[str], *, top_k: int) -> list[TechnicalSignal]:
    df = _call_ak_func(ak, "stock_zh_a_spot_em", warnings)
    rows = _df_to_records(df)
    out: list[TechnicalSignal] = []
    for rec in rows[: top_k * 4]:
        symbol = _plain_code(_first_str(rec, ["代码", "code", "symbol"]))
        name = _first_str(rec, ["名称", "name"])
        change = _first_float(rec, ["涨跌幅", "change_pct"])
        turnover = _first_float(rec, ["成交额", "turnover"])
        if not symbol:
            continue
        signals: list[str] = []
        score = 0.0
        if change is not None and change >= 3:
            signals.append(f"量价候选：涨幅 {change:.2f}%")
            score += min(change * 5, 35)
        if turnover is not None:
            signals.append("成交活跃")
            score += 8
        if signals:
            out.append(TechnicalSignal(symbol=symbol, name=name, score=score, signals=signals, change_pct=change, turnover=turnover))
    return sorted(out, key=lambda item: item.score, reverse=True)[:top_k]


def _enrich_with_spot(ak: Any, item: TechnicalSignal, warnings: list[str]) -> None:
    df = _call_ak_func(ak, "stock_zh_a_spot_em", warnings)
    rows = _df_to_records(df)
    for rec in rows:
        if _plain_code(_first_str(rec, ["代码", "code", "symbol"])) != item.symbol:
            continue
        item.name = item.name or _first_str(rec, ["名称", "name"])
        item.latest_close = _first_float(rec, ["最新价", "收盘", "close", "price"])
        item.change_pct = item.change_pct if item.change_pct is not None else _first_float(rec, ["涨跌幅", "change_pct"])
        item.turnover = item.turnover if item.turnover is not None else _first_float(rec, ["成交额", "turnover"])
        if item.change_pct is not None and item.change_pct >= 3 and "量价突破候选" not in item.signals:
            item.signals.append("量价突破候选")
            item.score += 10
        return


def _enrich_with_ma_signal(ak: Any, item: TechnicalSignal, warnings: list[str]) -> None:
    df = _call_ak_func(ak, "stock_zh_a_hist", warnings, symbol=item.symbol, period="daily", adjust="qfq")
    rows = _df_to_records(df)
    if len(rows) < 20:
        return
    closes = [_first_float(row, ["收盘", "close"]) for row in rows[-30:]]
    volumes = [_first_float(row, ["成交量", "volume"]) for row in rows[-30:]]
    closes = [x for x in closes if x is not None]
    volumes = [x for x in volumes if x is not None]
    if len(closes) < 20:
        return
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    latest = closes[-1]
    if latest > ma5 > ma10 > ma20:
        item.signals.append("均线多头排列")
        item.score += 20
    if len(volumes) >= 20:
        avg_vol = sum(volumes[-20:-1]) / 19
        if avg_vol > 0 and volumes[-1] > avg_vol * 1.5 and latest >= max(closes[-20:-1]):
            item.signals.append("放量突破20日高点")
            item.score += 25


def _classify_text(text: str) -> tuple[str, str]:
    low = text.lower()
    rules = [
        ("policy", ["政策", "国务院", "发改委", "证监会", "央行", "财政部", "监管", "补贴", "税收", "利率", "降准"], "政策关键词"),
        ("international", ["美股", "纳斯达克", "标普", "美元", "美联储", "欧洲", "日本", "海外", "全球", "关税", "制裁"], "国际/外围市场关键词"),
        ("company", ["公告", "业绩", "订单", "合同", "减持", "增持", "回购", "分红", "重组", "龙虎榜"], "公司事件关键词"),
        ("industry", ["行业", "板块", "产业", "景气", "需求", "价格", "库存", "产能", "研报"], "行业关键词"),
    ]
    for category, keywords, reason in rules:
        if any(keyword.lower() in low for keyword in keywords):
            return category, reason
    return "industry", "默认归入行业"


def _records_to_documents(source: str, source_type: str, df: Any, *, query: str) -> list[SourceDocument]:
    docs: list[SourceDocument] = []
    for rec in _df_to_records(df):
        title = _first_str(rec, ["标题", "新闻标题", "title", "Title", "资讯标题"]) or source
        content = "\n".join(f"{key}: {value}" for key, value in rec.items() if value not in (None, ""))
        url = _first_str(rec, ["链接", "新闻链接", "url", "URL"])
        published = _parse_dt(_first_str(rec, ["发布时间", "时间", "date", "日期"]))
        docs.append(
            build_source_document(
                source=source,
                source_type=source_type,
                title=title,
                content=content,
                url=url or None,
                published_at=published,
                metadata={"query": query, "raw": rec},
            )
        )
    return docs


def _call_first_available(ak: Any, warnings: list[str], funcs: list[tuple[str, dict[str, Any]]]) -> Any:
    for name, kwargs in funcs:
        df = _call_ak_func(ak, name, warnings, **kwargs)
        if df is not None and not getattr(df, "empty", False):
            return df
    return None


def _call_ak_func(ak: Any, name: str, warnings: list[str], **kwargs: Any) -> Any:
    func = getattr(ak, name, None)
    if func is None:
        warnings.append(f"akshare missing function: {name}")
        return None
    try:
        return func(**kwargs)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{name} failed: {type(exc).__name__}: {exc}")
        return None


def _import_akshare_safe(warnings: list[str]) -> Any | None:
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        warnings.append("akshare is not installed; daily market data/news scan is limited.")
        return None
    return ak


def _df_to_records(df: Any) -> list[dict[str, Any]]:
    if df is None:
        return []
    try:
        return df.to_dict(orient="records")
    except Exception:
        return []


def _first_str(rec: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = rec.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _first_float(rec: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = rec.get(key)
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _plain_code(symbol: str) -> str:
    raw = str(symbol or "").strip().lower().replace(".", "")
    if raw.startswith(("sh", "sz", "bj")):
        return raw[2:]
    return raw.zfill(6) if raw.isdigit() and len(raw) <= 6 else raw


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _sort_docs(docs: list[SourceDocument]) -> list[SourceDocument]:
    return sorted(docs, key=lambda doc: doc.published_at or doc.fetched_at or datetime.min, reverse=True)


def _fmt_pct(value: float | None) -> str:
    return f"{value:.2f}%" if value is not None else "-"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.2f}万"
    return f"{value:.2f}"
