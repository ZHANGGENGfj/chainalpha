from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import json
import re

from chainalpha.connectors.base import SourceDocument, build_source_document
from chainalpha.storage.local_store import JsonlStore, LocalResearchStore, sha256_text, to_jsonable


EVENT_CATEGORIES = ("policy", "industry", "company", "international")
ProgressCallback = Callable[[str], None]
_CURRENT_PROGRESS: ProgressCallback | None = None


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
    confidence: float = 0.0
    impact_score: float = 0.0
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
class FinancialSnapshot:
    symbol: str
    name: str = ""
    report_date: str | None = None
    revenue: float | None = None
    revenue_yoy: float | None = None
    net_profit: float | None = None
    net_profit_yoy: float | None = None
    gross_margin: float | None = None
    roe: float | None = None
    debt_ratio: float | None = None
    operating_cashflow: float | None = None
    source: str = "akshare"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def realized_growth_signal(self) -> str:
        signals: list[str] = []
        if self.revenue_yoy is not None:
            signals.append(f"营收同比 {_fmt_pct(self.revenue_yoy)}")
        if self.net_profit_yoy is not None:
            signals.append(f"净利同比 {_fmt_pct(self.net_profit_yoy)}")
        if self.roe is not None:
            signals.append(f"ROE {_fmt_pct(self.roe)}")
        if self.operating_cashflow is not None:
            signals.append(f"经营现金流 {_fmt_num(self.operating_cashflow)}")
        return "; ".join(signals) or "-"


@dataclass
class CompanyAnnouncement:
    symbol: str = ""
    name: str = ""
    title: str = ""
    announcement_type: str = ""
    published_at: str | None = None
    url: str | None = None
    source: str = "akshare"
    importance: float = 0.0
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchReportItem:
    symbol: str = ""
    name: str = ""
    title: str = ""
    institution: str = ""
    analyst: str = ""
    rating: str = ""
    report_date: str | None = None
    url: str | None = None
    source: str = "akshare"
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DailyMarketScanReport:
    generated_at: str
    query: str
    classified_events: dict[str, list[ClassifiedEvent]]
    technical_candidates: list[TechnicalSignal]
    sector_candidates: list[TechnicalSignal]
    raw_documents: list[SourceDocument]
    financial_snapshots: list[FinancialSnapshot] = field(default_factory=list)
    announcements: list[CompanyAnnouncement] = field(default_factory=list)
    research_reports: list[ResearchReportItem] = field(default_factory=list)
    storage: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable({
            "generated_at": self.generated_at,
            "query": self.query,
            "classified_events": {
                key: [asdict(item) for item in values]
                for key, values in self.classified_events.items()
            },
            "technical_candidates": [asdict(item) for item in self.technical_candidates],
            "sector_candidates": [asdict(item) for item in self.sector_candidates],
            "financial_snapshots": [asdict(item) for item in self.financial_snapshots],
            "announcements": [asdict(item) for item in self.announcements],
            "research_reports": [asdict(item) for item in self.research_reports],
            "raw_documents": [doc.to_dict() for doc in self.raw_documents],
            "storage": self.storage,
            "warnings": self.warnings,
        })


def build_daily_market_scan(
    *,
    query: str = "A股市场",
    symbols: list[str] | None = None,
    limit: int = 160,
    top_k: int = 30,
    include_financials: bool = True,
    include_announcements: bool = True,
    include_research: bool = True,
    max_announcements: int = 80,
    max_research_reports: int = 60,
    persist_dir: str | Path | None = "./data/daily_market",
    progress: ProgressCallback | None = None,
) -> DailyMarketScanReport:
    """Build one daily market scan.

    这个版本解决了原来的 5 个问题：
    1) 新闻源扩展为东方财富、财联社、个股新闻、财经快讯等可用 AkShare 源；
    2) 对候选股票抓财务摘要、利润表、资产负债表、现金流量表；
    3) 抓公司公告/巨潮披露类接口，识别订单、减持、重组、业绩预告等事件；
    4) 抓券商研报/盈利预测/行业研报类接口；
    5) 用规则评分而不是单一关键词命中分类，并返回 confidence/impact_score。

    AkShare 接口版本差异较大，因此所有新增接口都用 safe-call：函数不存在或字段变化时写入 warnings，不中断主流程。
    """
    global _CURRENT_PROGRESS
    previous_progress = _CURRENT_PROGRESS
    _CURRENT_PROGRESS = progress
    try:
        warnings: list[str] = []
        requested_symbols = [_plain_code(x) for x in (symbols or []) if _plain_code(x)]

        _emit_progress(f"Start daily market scan: query={query}, limit={limit}, top_k={top_k}")
        if requested_symbols:
            _emit_progress(f"Requested symbols: {', '.join(requested_symbols)}")

        _emit_progress("Stage 1/8: fetching market news and stock news")
        docs = _fetch_daily_documents(query=query, limit=limit, warnings=warnings, symbols=requested_symbols)
        _emit_progress(f"Stage 1/8 done: fetched {len(docs)} raw documents before dedup")

        _emit_progress("Stage 2/8: deduplicating and classifying news")
        docs = _dedup_documents(docs)
        classified = _classify_documents(docs, per_category=max(8, top_k // 2))
        classified_count = sum(len(v) for v in classified.values())
        _emit_progress(f"Stage 2/8 done: {len(docs)} documents after dedup, {classified_count} classified events")

        _emit_progress("Stage 3/8: screening technical candidates")
        technical_candidates = _screen_technical_candidates(symbols=requested_symbols, top_k=top_k, warnings=warnings)
        _emit_progress(f"Stage 3/8 done: {len(technical_candidates)} technical candidates")

        _emit_progress("Stage 4/8: screening sector candidates")
        sector_candidates = _screen_sector_candidates(top_k=max(10, top_k // 2), warnings=warnings)
        _emit_progress(f"Stage 4/8 done: {len(sector_candidates)} sector candidates")

        scan_symbols = requested_symbols or [item.symbol for item in technical_candidates[: min(20, len(technical_candidates))]]
        _emit_progress(f"Scan symbols for fundamentals/announcements: {', '.join(scan_symbols) if scan_symbols else '-'}")

        financial_snapshots: list[FinancialSnapshot] = []
        if include_financials and scan_symbols:
            _emit_progress("Stage 5/8: fetching financial snapshots")
            financial_snapshots = _fetch_financial_snapshots(symbols=scan_symbols, warnings=warnings)
            _emit_progress(f"Stage 5/8 done: {len(financial_snapshots)} financial snapshots")
        else:
            _emit_progress("Stage 5/8 skipped: no scan symbols or financials disabled")

        announcements: list[CompanyAnnouncement] = []
        if include_announcements:
            _emit_progress("Stage 6/8: fetching company announcements")
            announcements = _fetch_company_announcements(symbols=scan_symbols, max_items=max_announcements, warnings=warnings)
            _emit_progress(f"Stage 6/8 done: {len(announcements)} announcements")
        else:
            _emit_progress("Stage 6/8 skipped: announcements disabled")

        research_reports: list[ResearchReportItem] = []
        if include_research:
            _emit_progress("Stage 7/8: fetching research reports")
            research_reports = _fetch_research_reports(symbols=scan_symbols, max_items=max_research_reports, warnings=warnings)
            _emit_progress(f"Stage 7/8 done: {len(research_reports)} research reports")
        else:
            _emit_progress("Stage 7/8 skipped: research reports disabled")

        report = DailyMarketScanReport(
            generated_at=datetime.now().isoformat(timespec="seconds"),
            query=query,
            classified_events=classified,
            technical_candidates=technical_candidates,
            sector_candidates=sector_candidates,
            financial_snapshots=financial_snapshots,
            announcements=announcements,
            research_reports=research_reports,
            raw_documents=docs,
            warnings=warnings,
        )
        if persist_dir:
            _emit_progress(f"Stage 8/8: persisting scan artifacts to {persist_dir}")
            report.storage = persist_daily_market_scan(report, persist_dir)
            _emit_progress("Stage 8/8 done: scan artifacts persisted")
        else:
            _emit_progress("Stage 8/8 skipped: persistence disabled")
        return report
    finally:
        _CURRENT_PROGRESS = previous_progress


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

    financial_jsonl = processed_dir / "financial_snapshots.jsonl"
    JsonlStore(financial_jsonl).append_many([asdict(item) for item in report.financial_snapshots])

    announcements_jsonl = processed_dir / "announcements.jsonl"
    JsonlStore(announcements_jsonl).append_many([asdict(item) for item in report.announcements])

    research_jsonl = processed_dir / "research_reports.jsonl"
    JsonlStore(research_jsonl).append_many([asdict(item) for item in report.research_reports])

    summary_json = reports_dir / "daily_market_scan.json"
    summary_md = reports_dir / "daily_market_scan.md"
    summary_json.write_text(
        json.dumps(
            to_jsonable(report.to_dict() | {"raw_documents": [doc.to_dict() for doc in new_docs]}),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
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
        "financial_snapshots_jsonl": str(financial_jsonl),
        "announcements_jsonl": str(announcements_jsonl),
        "research_reports_jsonl": str(research_jsonl),
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
        f"- Financial snapshots: {len(report.financial_snapshots)}",
        f"- Announcements: {len(report.announcements)}",
        f"- Research reports: {len(report.research_reports)}",
        "",
    ]
    if report.warnings:
        lines.extend(["## Warnings", ""])
        for item in _dedup_strings(report.warnings)[:40]:
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
            tags = ",".join(item.tags)
            lines.append(
                f"- {date} | {item.title} | {item.source} | confidence={item.confidence:.2f} | "
                f"impact={item.impact_score:.1f} | {tags} | {item.reason} | {path}"
            )
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

    lines.extend(["## 5. 财报兑现初筛", ""])
    if report.financial_snapshots:
        lines.extend([
            "| Symbol | Name | Report date | Revenue | Rev YoY | Net profit | NP YoY | ROE | OCF | Signal |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ])
        for item in report.financial_snapshots:
            lines.append(
                f"| {item.symbol} | {item.name or '-'} | {item.report_date or '-'} | {_fmt_num(item.revenue)} | "
                f"{_fmt_pct(item.revenue_yoy)} | {_fmt_num(item.net_profit)} | {_fmt_pct(item.net_profit_yoy)} | "
                f"{_fmt_pct(item.roe)} | {_fmt_num(item.operating_cashflow)} | {item.realized_growth_signal} |"
            )
    else:
        lines.append("- 数据缺口：当前未抓取到财务快照。")
    lines.append("")

    lines.extend(["## 6. 公司公告/交易所披露", ""])
    if report.announcements:
        for item in report.announcements[:50]:
            date = item.published_at or "date-unknown"
            tags = ",".join(item.tags) or "-"
            lines.append(
                f"- {date} | {item.symbol} {item.name} | {item.announcement_type or '-'} | "
                f"importance={item.importance:.1f} | {tags} | {item.title} | {item.url or '-'}"
            )
    else:
        lines.append("- 数据缺口：当前未抓取到公司公告。")
    lines.append("")

    lines.extend(["## 7. 券商研报/行业报告", ""])
    if report.research_reports:
        for item in report.research_reports[:50]:
            date = item.report_date or "date-unknown"
            tags = ",".join(item.tags) or "-"
            lines.append(
                f"- {date} | {item.symbol} {item.name} | {item.institution or '-'} | "
                f"{item.rating or '-'} | {tags} | {item.title} | {item.url or '-'}"
            )
    else:
        lines.append("- 数据缺口：当前未抓取到研报。")
    lines.append("")

    lines.extend([
        "## 8. 初筛规则说明",
        "",
        "- 新闻源扩展到东方财富、财联社、个股新闻/财经快讯等可用 AkShare 源。",
        "- 公告单独抓取并按订单合同、业绩预告、减持增持、回购分红、并购重组、风险提示等打标签。",
        "- 财报不再只看新闻标题，而是尝试抓利润表、资产负债表、现金流量表、财务指标，用来判断业绩兑现。",
        "- 研报单独抓取，后续可以与公司公告和财报交叉验证。",
        "- 新闻分类由单关键词命中升级为多规则加权评分，并输出 confidence 与 impact_score。",
        "- 该扫描用于生成候选池，不构成投资建议，所有候选都需要进一步结合公告原文、财报附注和估值复核。",
        "",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 信息采集：新闻 + 公告 + 财报 + 研报
# ---------------------------------------------------------------------------


def _fetch_daily_documents(*, query: str, limit: int, warnings: list[str], symbols: list[str] | None = None) -> list[SourceDocument]:
    docs: list[SourceDocument] = []
    ak = _import_akshare_safe(warnings)
    if ak is None:
        return docs

    datasets = [
        # 原有两个源
        ("eastmoney_global", "global_news_em", "stock_info_global_em", {}),
        ("cls_global", "global_news_cls", "stock_info_global_cls", {}),
        # 增补财经快讯/财经新闻。不同 AkShare 版本函数名可能不同，因此都用 safe-call。
        ("eastmoney_news", "finance_news_em", "stock_info_global_em", {}),
        ("caixin_news", "finance_news_cx", "stock_news_main_cx", {}),
        ("ths_news", "finance_news_ths", "stock_info_global_ths", {}),
    ]
    for source, source_type, func_name, kwargs in datasets:
        df = _call_optional_ak_func(ak, func_name, warnings, **kwargs)
        docs.extend(_records_to_documents(source, source_type, df, query=query))

    # 个股新闻：如果传入股票池，额外抓每只股票的新闻。
    for symbol in (symbols or [])[:30]:
        for func_name, kwargs in [
            ("stock_news_em", {"symbol": symbol}),
            ("stock_news_em", {"symbol": _with_market_prefix(symbol)}),
        ]:
            df = _call_optional_ak_func(ak, func_name, warnings, **kwargs)
            docs.extend(_records_to_documents(f"stock_news_{symbol}", "stock_news", df, query=query))
            if df is not None and not getattr(df, "empty", False):
                break

    docs = _sort_docs(docs)
    return docs[:limit]


def _fetch_company_announcements(*, symbols: list[str], max_items: int, warnings: list[str]) -> list[CompanyAnnouncement]:
    ak = _import_akshare_safe(warnings)
    if ak is None:
        return []

    symbol_set = {x for x in (_plain_code(s) for s in symbols) if x}
    records: list[dict[str, Any]] = []

    # 巨潮/交易所公告类接口。AkShare 各版本函数和参数可能有差异，因此多候选安全调用。
    candidate_calls: list[tuple[str, dict[str, Any]]] = [
        ("stock_notice_report", {"symbol": "全部"}),
        ("stock_zh_a_disclosure_report_cninfo", {"symbol": "全部", "market": "沪深京", "keyword": ""}),
        ("stock_zh_a_disclosure_relation_cninfo", {"symbol": "全部", "market": "沪深京"}),
    ]
    for func_name, kwargs in candidate_calls:
        df = _call_optional_ak_func(ak, func_name, warnings, **kwargs)
        rows = _df_to_records(df)
        if rows:
            records.extend(rows)
            break

    # 如果有明确股票池，尝试逐股补公告，避免全市场接口字段或分页限制漏掉目标公司。
    for symbol in list(symbol_set)[:40]:
        for func_name, kwargs in [
            ("stock_notice_report", {"symbol": symbol}),
            ("stock_notice_report", {"symbol": _with_market_prefix(symbol)}),
            ("stock_zh_a_disclosure_report_cninfo", {"symbol": symbol, "market": "沪深京", "keyword": ""}),
        ]:
            df = _call_optional_ak_func(ak, func_name, warnings, **kwargs)
            rows = _df_to_records(df)
            if rows:
                records.extend(rows)
                break

    out: list[CompanyAnnouncement] = []
    for rec in records:
        item = _record_to_announcement(rec)
        if symbol_set and item.symbol and item.symbol not in symbol_set:
            continue
        if not item.title:
            continue
        out.append(item)

    out = _dedup_announcements(out)
    return sorted(out, key=lambda x: (x.published_at or "", x.importance), reverse=True)[:max_items]


def _fetch_financial_snapshots(*, symbols: list[str], warnings: list[str]) -> list[FinancialSnapshot]:
    ak = _import_akshare_safe(warnings)
    if ak is None:
        return []

    out: list[FinancialSnapshot] = []
    for symbol in symbols[:50]:
        pieces: dict[str, list[dict[str, Any]]] = {}
        calls = [
            ("abstract", "stock_financial_abstract", {"symbol": symbol}),
            ("analysis", "stock_financial_analysis_indicator", {"symbol": symbol}),
            ("profit", "stock_profit_sheet_by_report_em", {"symbol": symbol}),
            ("balance", "stock_balance_sheet_by_report_em", {"symbol": symbol}),
            ("cashflow", "stock_cash_flow_sheet_by_report_em", {"symbol": symbol}),
        ]
        for key, func_name, kwargs in calls:
            df = _call_optional_ak_func(ak, func_name, warnings, **kwargs)
            rows = _df_to_records(df)
            if rows:
                pieces[key] = rows

        if not pieces:
            continue
        snapshot = _extract_financial_snapshot(symbol, pieces)
        if snapshot:
            out.append(snapshot)
    return out


def _fetch_research_reports(*, symbols: list[str], max_items: int, warnings: list[str]) -> list[ResearchReportItem]:
    ak = _import_akshare_safe(warnings)
    if ak is None:
        return []

    records: list[dict[str, Any]] = []
    # 行业研报/策略研报：不一定按股票代码。
    for func_name, kwargs in [
        ("stock_report_industry", {}),
        ("stock_research_report_em", {}),
        ("stock_profit_forecast_em", {}),
    ]:
        df = _call_optional_ak_func(ak, func_name, warnings, **kwargs)
        rows = _df_to_records(df)
        if rows:
            records.extend(rows)
            break

    # 个股研报/盈利预测：按候选股票补充。
    for symbol in symbols[:40]:
        for func_name, kwargs in [
            ("stock_research_report_em", {"symbol": symbol}),
            ("stock_profit_forecast_em", {"symbol": symbol}),
        ]:
            df = _call_optional_ak_func(ak, func_name, warnings, **kwargs)
            rows = _df_to_records(df)
            if rows:
                records.extend(rows)
                break

    symbol_set = {x for x in (_plain_code(s) for s in symbols) if x}
    out: list[ResearchReportItem] = []
    for rec in records:
        item = _record_to_research_report(rec)
        if symbol_set and item.symbol and item.symbol not in symbol_set:
            continue
        if item.title:
            out.append(item)
    out = _dedup_research_reports(out)
    return sorted(out, key=lambda x: x.report_date or "", reverse=True)[:max_items]


# ---------------------------------------------------------------------------
# 事件分类：从单关键词升级为加权评分
# ---------------------------------------------------------------------------


CLASSIFICATION_RULES: dict[str, list[tuple[str, list[str], float]]] = {
    "policy": [
        ("货币财政", ["央行", "降准", "降息", "利率", "财政部", "专项债", "流动性"], 3.0),
        ("产业政策", ["国务院", "发改委", "工信部", "产业政策", "补贴", "设备更新", "规划", "行动方案"], 3.0),
        ("监管政策", ["证监会", "交易所", "监管", "减持新规", "并购重组", "退市"], 2.5),
    ],
    "international": [
        ("海外市场", ["美股", "纳斯达克", "标普", "欧洲", "日本", "海外", "全球"], 2.5),
        ("海外宏观", ["美联储", "美元", "通胀", "非农", "就业", "美国国债", "加息", "降息"], 3.0),
        ("贸易地缘", ["关税", "制裁", "出口管制", "地缘", "中东", "俄乌", "台海"], 3.5),
        ("海外科技", ["英伟达", "nvidia", "openai", "微软", "meta", "谷歌", "amd", "台积电", "sk海力士"], 3.5),
    ],
    "company": [
        ("公告披露", ["公告", "披露", "交易所", "巨潮", "董事会", "股东大会"], 3.0),
        ("业绩财报", ["业绩", "营收", "净利润", "利润", "年报", "中报", "季报", "业绩预告", "业绩快报"], 4.0),
        ("订单合同", ["订单", "合同", "中标", "客户", "认证", "供应商", "采购"], 4.0),
        ("资本动作", ["回购", "增持", "减持", "分红", "重组", "定增", "股权激励"], 3.5),
        ("风险事件", ["立案", "问询函", "监管函", "诉讼", "仲裁", "亏损", "商誉减值", "退市风险"], 4.5),
    ],
    "industry": [
        ("行业景气", ["行业", "板块", "景气", "需求", "涨价", "库存", "订单", "开工率"], 2.5),
        ("供需变化", ["产能", "供给", "供应", "短缺", "价格", "扩产", "去库存"], 3.0),
        ("技术路线", ["ai", "芯片", "光模块", "800g", "1.6t", "cpo", "lpo", "算力", "pcb", "液冷", "hbm"], 3.5),
        ("产业链", ["上游", "下游", "产业链", "国产替代", "材料", "设备", "封装"], 2.5),
        ("研报观点", ["研报", "券商", "评级", "目标价", "盈利预测"], 2.0),
    ],
}


def _classify_documents(docs: list[SourceDocument], *, per_category: int) -> dict[str, list[ClassifiedEvent]]:
    buckets = {category: [] for category in EVENT_CATEGORIES}
    for doc in docs:
        text = f"{doc.title}\n{doc.content}"
        category, reason, confidence, impact_score = _classify_text(text)
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
                confidence=confidence,
                impact_score=impact_score,
                content_preview=doc.content.replace("\n", " ")[:220],
            )
        )
    return buckets


def _classify_text(text: str) -> tuple[str, str, float, float]:
    low = text.lower()
    scores: dict[str, float] = {category: 0.0 for category in EVENT_CATEGORIES}
    matched: dict[str, list[str]] = {category: [] for category in EVENT_CATEGORIES}

    for category, rules in CLASSIFICATION_RULES.items():
        for rule_name, keywords, weight in rules:
            hit_count = sum(1 for kw in keywords if kw.lower() in low)
            if hit_count:
                scores[category] += weight * min(hit_count, 3)
                matched[category].append(f"{rule_name}:{hit_count}")

    # 来源类型本身也提供弱标签。
    if any(x in low for x in ["公告", "disclosure", "notice"]):
        scores["company"] += 2.0
    if any(x in low for x in ["report", "研报", "评级", "目标价"]):
        scores["industry"] += 1.0

    category = max(scores, key=scores.get)
    max_score = scores[category]
    total_score = sum(v for v in scores.values() if v > 0)
    if max_score <= 0:
        return "industry", "无明确命中，默认归入行业", 0.20, 0.0

    confidence = max_score / total_score if total_score else 0.0
    reason = "; ".join(matched[category]) or "规则评分命中"
    impact_score = min(max_score * 10, 100.0)
    return category, reason, confidence, impact_score


def _extract_event_tags(text: str, category: str) -> list[str]:
    low = text.lower()
    tag_rules = {
        "policy": {
            "货币政策": ["降准", "降息", "利率", "央行", "流动性"],
            "产业政策": ["补贴", "专项", "设备更新", "产业", "规划", "行动方案"],
            "监管政策": ["证监会", "监管", "减持", "并购", "退市", "交易所"],
        },
        "industry": {
            "景气度": ["景气", "需求", "订单", "涨价", "库存"],
            "技术路线": ["ai", "芯片", "光模块", "800g", "1.6t", "cpo", "lpo", "算力", "pcb", "液冷", "hbm"],
            "供需变化": ["产能", "供给", "供应", "短缺", "价格", "扩产"],
            "产业链": ["上游", "下游", "产业链", "国产替代", "材料", "设备"],
        },
        "company": {
            "业绩": ["业绩", "营收", "净利润", "利润", "年报", "季报", "业绩预告", "业绩快报"],
            "订单合同": ["订单", "合同", "中标", "客户", "认证", "采购"],
            "资本动作": ["回购", "增持", "减持", "分红", "重组", "定增", "股权激励"],
            "风险提示": ["问询函", "监管函", "诉讼", "仲裁", "亏损", "减值", "退市风险"],
        },
        "international": {
            "外围市场": ["美股", "纳斯达克", "标普", "欧洲", "日本"],
            "宏观流动性": ["美联储", "美元", "利率", "通胀", "就业"],
            "贸易地缘": ["关税", "制裁", "出口管制", "地缘"],
            "海外科技": ["英伟达", "nvidia", "openai", "微软", "meta", "谷歌", "amd", "台积电"],
        },
    }
    tags: list[str] = []
    for tag, keywords in tag_rules.get(category, {}).items():
        if any(keyword.lower() in low for keyword in keywords):
            tags.append(tag)
    return tags or [category]


# ---------------------------------------------------------------------------
# 原有技术面/板块筛选逻辑，保留并小幅复用
# ---------------------------------------------------------------------------


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

    for _symbol, item in list(candidates.items()):
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


# ---------------------------------------------------------------------------
# 记录转换与结构化抽取
# ---------------------------------------------------------------------------


def _records_to_documents(source: str, source_type: str, df: Any, *, query: str) -> list[SourceDocument]:
    docs: list[SourceDocument] = []
    for rec in _df_to_records(df):
        title = _first_str(rec, ["标题", "新闻标题", "title", "Title", "资讯标题", "内容", "摘要"]) or source
        content = "\n".join(f"{key}: {value}" for key, value in rec.items() if value not in (None, ""))
        url = _first_str(rec, ["链接", "新闻链接", "url", "URL", "公告链接", "网址"])
        published = _parse_dt(_first_str(rec, ["发布时间", "时间", "date", "日期", "公告日期", "报告日期"])
                              or _first_str(rec, ["datetime", "publish_time"]))
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


def _record_to_announcement(rec: dict[str, Any]) -> CompanyAnnouncement:
    symbol = _plain_code(_first_str(rec, ["代码", "股票代码", "证券代码", "symbol", "code"]))
    name = _first_str(rec, ["名称", "股票简称", "证券简称", "简称", "name"])
    title = _first_str(rec, ["公告标题", "标题", "公告名称", "title", "内容"])
    announcement_type = _first_str(rec, ["公告类型", "类型", "category", "分类"])
    published = _parse_dt(_first_str(rec, ["公告日期", "发布时间", "日期", "时间", "date"]))
    url = _first_str(rec, ["公告链接", "链接", "url", "URL", "网址"])
    tags, importance = _announcement_tags_and_importance(title + "\n" + announcement_type)
    return CompanyAnnouncement(
        symbol=symbol,
        name=name,
        title=title,
        announcement_type=announcement_type,
        published_at=published.isoformat()[:10] if published else None,
        url=url or None,
        importance=importance,
        tags=tags,
        raw=rec,
    )


def _announcement_tags_and_importance(text: str) -> tuple[list[str], float]:
    rules = [
        ("业绩预告", ["业绩预告", "业绩快报", "预增", "预减", "扭亏", "亏损"], 30),
        ("财报", ["年度报告", "半年度报告", "季度报告", "年报", "中报", "季报"], 20),
        ("订单合同", ["重大合同", "订单", "中标", "项目中标", "采购合同", "框架协议"], 35),
        ("增减持", ["减持", "增持", "持股变动"], 25),
        ("回购分红", ["回购", "分红", "权益分派"], 20),
        ("并购重组", ["重组", "收购", "资产购买", "重大资产", "发行股份购买"], 35),
        ("融资定增", ["定增", "非公开发行", "可转债", "配股", "募集资金"], 25),
        ("风险监管", ["问询函", "监管函", "立案", "处罚", "诉讼", "仲裁", "退市风险"], 40),
    ]
    low = text.lower()
    tags: list[str] = []
    score = 0.0
    for tag, keywords, weight in rules:
        if any(kw.lower() in low for kw in keywords):
            tags.append(tag)
            score += weight
    return tags or ["公告"], min(score or 10.0, 100.0)


def _record_to_research_report(rec: dict[str, Any]) -> ResearchReportItem:
    symbol = _plain_code(_first_str(rec, ["股票代码", "代码", "证券代码", "symbol", "code"]))
    name = _first_str(rec, ["股票简称", "名称", "证券简称", "行业名称", "name"])
    title = _first_str(rec, ["报告名称", "报告标题", "标题", "title", "研报标题"])
    institution = _first_str(rec, ["机构", "机构名称", "研究机构", "券商", "org", "institution"])
    analyst = _first_str(rec, ["分析师", "作者", "analyst"])
    rating = _first_str(rec, ["评级", "投资评级", "rating"])
    report_date = _parse_dt(_first_str(rec, ["报告日期", "发布日期", "日期", "date", "时间"]))
    url = _first_str(rec, ["链接", "报告链接", "url", "URL", "网址"])
    text = f"{title}\n{name}\n{rating}"
    tags = _extract_event_tags(text, "industry")
    return ResearchReportItem(
        symbol=symbol,
        name=name,
        title=title,
        institution=institution,
        analyst=analyst,
        rating=rating,
        report_date=report_date.isoformat()[:10] if report_date else None,
        url=url or None,
        tags=tags,
        raw=rec,
    )


def _extract_financial_snapshot(symbol: str, pieces: dict[str, list[dict[str, Any]]]) -> FinancialSnapshot | None:
    # 取每个财务表最新一行，再横向合并。不同接口字段差异很大，所以用多字段别名抽取。
    latest_by_piece: dict[str, dict[str, Any]] = {}
    for key, rows in pieces.items():
        if not rows:
            continue
        sorted_rows = sorted(rows, key=lambda r: _row_date_str(r), reverse=True)
        latest_by_piece[key] = sorted_rows[0]

    if not latest_by_piece:
        return None

    merged: dict[str, Any] = {}
    for key, row in latest_by_piece.items():
        for k, v in row.items():
            merged.setdefault(k, v)
        merged[f"__{key}"] = row

    report_date = _parse_dt(_first_str(merged, ["报告期", "报告日期", "日期", "公告日期", "截止日期", "REPORT_DATE"]))
    name = _first_str(merged, ["股票简称", "名称", "证券简称", "name"])

    return FinancialSnapshot(
        symbol=symbol,
        name=name,
        report_date=report_date.isoformat()[:10] if report_date else _row_date_str(merged) or None,
        revenue=_first_float(merged, ["营业总收入", "营业收入", "主营业务收入", "TOTAL_OPERATE_INCOME", "OPERATE_INCOME"]),
        revenue_yoy=_first_float(merged, ["营业总收入同比增长", "营业收入同比增长", "营收同比", "营业收入-同比增长", "TOTAL_OPERATE_INCOME_YOY"]),
        net_profit=_first_float(merged, ["归母净利润", "净利润", "归属于母公司股东的净利润", "PARENT_NETPROFIT", "NETPROFIT"]),
        net_profit_yoy=_first_float(merged, ["归母净利润同比增长", "净利润同比增长", "净利润同比", "PARENT_NETPROFIT_YOY"]),
        gross_margin=_first_float(merged, ["销售毛利率", "毛利率", "gross_margin", "GROSS_PROFIT_RATIO"]),
        roe=_first_float(merged, ["净资产收益率", "ROE", "roe", "加权净资产收益率"]),
        debt_ratio=_first_float(merged, ["资产负债率", "debt_ratio", "ASSET_LIAB_RATIO"]),
        operating_cashflow=_first_float(merged, ["经营活动产生的现金流量净额", "经营现金流", "NETCASH_OPERATE", "经营活动现金流量净额"]),
        raw=latest_by_piece,
    )


# ---------------------------------------------------------------------------
# 去重、调用、安全工具
# ---------------------------------------------------------------------------


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


def _emit_progress(message: str) -> None:
    if _CURRENT_PROGRESS is not None:
        _CURRENT_PROGRESS(message)


def _document_dedup_key(doc: SourceDocument) -> str:
    if doc.url:
        return "url:" + sha256_text(doc.url)
    title = " ".join((doc.title or "").split())
    content = " ".join((doc.content or "").split())[:1000]
    return "text:" + sha256_text(f"{doc.source}|{title}|{content}")


def _dedup_announcements(items: list[CompanyAnnouncement]) -> list[CompanyAnnouncement]:
    seen: set[str] = set()
    out: list[CompanyAnnouncement] = []
    for item in items:
        key = sha256_text(f"{item.symbol}|{item.title}|{item.published_at}|{item.url or ''}")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedup_research_reports(items: list[ResearchReportItem]) -> list[ResearchReportItem]:
    seen: set[str] = set()
    out: list[ResearchReportItem] = []
    for item in items:
        key = sha256_text(f"{item.symbol}|{item.title}|{item.institution}|{item.report_date}|{item.url or ''}")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _event_id(doc: SourceDocument, category: str) -> str:
    return sha256_text(f"{category}|{doc.source}|{doc.title}|{doc.url or ''}")[:24]


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
        _emit_progress(f"  AkShare call: {name}({kwargs})")
        result = func(**kwargs)
        rows = len(result) if hasattr(result, "__len__") else "unknown"
        _emit_progress(f"  AkShare done: {name}, rows={rows}")
        return result
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{name} failed: {type(exc).__name__}: {exc}")
        _emit_progress(f"  AkShare failed: {name}: {type(exc).__name__}: {exc}")
        return None


def _call_optional_ak_func(ak: Any, name: str, warnings: list[str], **kwargs: Any) -> Any:
    """Call optional AkShare APIs without making missing functions too noisy."""
    func = getattr(ak, name, None)
    if func is None:
        warnings.append(f"optional akshare function unavailable: {name}")
        return None
    try:
        _emit_progress(f"  AkShare call: {name}({kwargs})")
        result = func(**kwargs)
        rows = len(result) if hasattr(result, "__len__") else "unknown"
        _emit_progress(f"  AkShare done: {name}, rows={rows}")
        return result
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"optional {name} failed: {type(exc).__name__}: {exc}")
        _emit_progress(f"  AkShare failed: {name}: {type(exc).__name__}: {exc}")
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


def _with_market_prefix(symbol: str) -> str:
    code = _plain_code(symbol)
    if not re.fullmatch(r"\d{6}", code):
        return code
    if code.startswith(("60", "68", "90")):
        return "sh" + code
    if code.startswith(("00", "30", "20")):
        return "sz" + code
    if code.startswith(("83", "87", "88", "43", "92")):
        return "bj" + code
    return code


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip().replace(",", "").replace("%", "")
        if text in {"-", "--", "nan", "None"}:
            return None
        # AkShare 有些字段带单位，尽量归一到数值。
        multiplier = 1.0
        if text.endswith("万"):
            multiplier = 10_000.0
            text = text[:-1]
        elif text.endswith("亿"):
            multiplier = 100_000_000.0
            text = text[:-1]
        return float(text) * multiplier
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
        "%Y%m%d",
    ):
        try:
            return datetime.strptime(text[: len(datetime.now().strftime(fmt))] if "%H" in fmt else text[:10], fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _row_date_str(row: dict[str, Any]) -> str:
    value = _first_str(row, ["报告期", "报告日期", "公告日期", "日期", "截止日期", "date", "REPORT_DATE"])
    dt = _parse_dt(value)
    if dt:
        return dt.isoformat()[:10]
    return value


def _sort_docs(docs: list[SourceDocument]) -> list[SourceDocument]:
    return sorted(docs, key=lambda doc: doc.published_at or doc.fetched_at or datetime.min, reverse=True)


def _dedup_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


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
