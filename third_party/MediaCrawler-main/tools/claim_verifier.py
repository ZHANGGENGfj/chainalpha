from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse
import asyncio
import json
import math
import os
import re


SOURCE_WEIGHTS = {
    "巨潮资讯": 1.0,
    "cninfo": 1.0,
    "交易所": 1.0,
    "证券时报": 0.82,
    "中国证券报": 0.82,
    "上海证券报": 0.82,
    "财联社": 0.8,
    "第一财经": 0.76,
    "东方财富": 0.72,
    "同花顺": 0.7,
    "21世纪经济报道": 0.7,
    "每日经济新闻": 0.68,
}

DEFAULT_SITE_TARGETS = {
    "eastmoney": "https://finance.eastmoney.com/",
    "10jqka": "https://news.10jqka.com.cn/",
    "stcn": "https://www.stcn.com/",
    "cs": "https://www.cs.com.cn/",
    "cnstock": "https://www.cnstock.com/",
    "zqrb": "http://www.zqrb.cn/",
    "yicai": "https://www.yicai.com/",
    "21jingji": "https://www.21jingji.com/",
    "nbd": "https://www.nbd.com.cn/",
}


@dataclass
class Claim:
    raw: str
    claim_type: str
    company: str = ""
    metric: str = ""
    direction: str = ""
    threshold_pct: float | None = None
    period: str = ""
    precision: str = "vague"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceItem:
    source_layer: str
    source: str
    title: str
    url: str = ""
    published_at: str = ""
    snippet: str = ""
    score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NumericCheck:
    check_type: str
    status: str
    metric: str = ""
    period: str = ""
    actual_pct: float | None = None
    threshold_pct: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationReport:
    question: str
    claim: Claim
    verdict: str
    confidence: float
    numeric_checks: list[NumericCheck]
    local_evidence: list[EvidenceItem]
    web_evidence: list[EvidenceItem]
    conflicts: list[str]
    missing_evidence: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "claim": self.claim.to_dict(),
            "verdict": self.verdict,
            "confidence": self.confidence,
            "numeric_checks": [item.to_dict() for item in self.numeric_checks],
            "local_evidence": [item.to_dict() for item in self.local_evidence],
            "web_evidence": [item.to_dict() for item in self.web_evidence],
            "conflicts": self.conflicts,
            "missing_evidence": self.missing_evidence,
            "warnings": self.warnings,
        }


def verify_claim(
    question: str,
    *,
    days: int = 30,
    local_dirs: list[str | Path] | None = None,
    web_sites: list[str] | None = None,
    use_web: bool = False,
    top_k: int = 20,
) -> VerificationReport:
    claim = parse_claim(question)
    warnings: list[str] = []
    numeric_checks: list[NumericCheck] = []

    local_evidence = search_local_news(
        question,
        claim=claim,
        days=days,
        local_dirs=local_dirs,
        top_k=top_k,
        warnings=warnings,
    )

    web_evidence: list[EvidenceItem] = []
    if use_web:
        web_evidence = search_web_sites(
            question,
            claim=claim,
            sites=web_sites,
            top_k=top_k,
            warnings=warnings,
        )

    if claim.claim_type == "price_change":
        numeric_checks.append(check_price_change(claim))
    elif claim.claim_type == "financial_growth":
        numeric_checks.append(check_financial_growth(claim))

    verdict, confidence, conflicts, missing = synthesize_verdict(
        claim=claim,
        numeric_checks=numeric_checks,
        local_evidence=local_evidence,
        web_evidence=web_evidence,
    )

    return VerificationReport(
        question=question,
        claim=claim,
        verdict=verdict,
        confidence=confidence,
        numeric_checks=numeric_checks,
        local_evidence=local_evidence,
        web_evidence=web_evidence,
        conflicts=conflicts,
        missing_evidence=missing,
        warnings=warnings,
    )


def parse_claim(question: str) -> Claim:
    text = question.strip()
    threshold = _extract_pct(text)
    metric = ""
    claim_type = "news_event"
    direction = ""

    if any(word in text for word in ["股价", "股票", "上涨", "涨了", "涨幅", "下跌", "跌了", "跌幅"]):
        claim_type = "price_change"
        metric = "stock_price"
        direction = "down" if any(word in text for word in ["下跌", "跌了", "跌幅"]) else "up"
    if any(word in text for word in ["业绩", "净利润", "利润", "营收", "收入", "扣非"]):
        claim_type = "financial_growth"
        direction = "down" if any(word in text for word in ["下降", "下滑", "减少", "亏损"]) else "up"
        if "扣非" in text:
            metric = "deducted_net_profit"
        elif "营收" in text or "收入" in text:
            metric = "revenue"
        else:
            metric = "net_profit"

    if not direction:
        direction = "up" if any(word in text for word in ["大增", "增长", "上涨", "走强", "利好"]) else "unknown"

    company = _extract_company(text)
    precision = "exact" if threshold is not None else "vague"
    return Claim(
        raw=text,
        claim_type=claim_type,
        company=company,
        metric=metric,
        direction=direction,
        threshold_pct=threshold,
        period=_extract_period_text(text),
        precision=precision,
    )


def search_local_news(
    query: str,
    *,
    claim: Claim,
    days: int,
    local_dirs: list[str | Path] | None,
    top_k: int,
    warnings: list[str],
) -> list[EvidenceItem]:
    roots = [Path(p) for p in (local_dirs or _default_local_dirs())]
    cutoff = datetime.now() - timedelta(days=days)
    rows: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in _iter_json_sources(root):
            if _path_date(path) and _path_date(path) < cutoff:
                continue
            try:
                rows.extend(_load_records(path))
            except Exception as exc:
                warnings.append(f"failed to read local news {path}: {type(exc).__name__}: {exc}")

    terms = _query_terms(query, claim)
    evidence: list[EvidenceItem] = []
    for row in rows:
        title = str(row.get("title") or row.get("标题") or row.get("summary") or "")
        content = str(row.get("content") or row.get("内容") or row.get("snippet") or "")
        source = str(row.get("source") or row.get("来源") or row.get("media") or "")
        haystack = f"{title} {content} {source}".lower()
        matched = [term for term in terms if term and term.lower() in haystack]
        if not matched:
            continue
        evidence.append(
            EvidenceItem(
                source_layer="local_news",
                source=source or "local",
                title=title or content[:80],
                url=str(row.get("url") or row.get("链接") or ""),
                published_at=str(row.get("published_at") or row.get("publish_time") or row.get("date") or ""),
                snippet=content[:240],
                score=_evidence_score(source, matched, title, content),
                matched_terms=matched,
                metadata={"path_source": str(row.get("_path") or "")},
            )
        )
    return sorted(evidence, key=lambda item: item.score, reverse=True)[:top_k]


def search_web_sites(
    query: str,
    *,
    claim: Claim,
    sites: list[str] | None,
    top_k: int,
    warnings: list[str],
) -> list[EvidenceItem]:
    site_keys = sites or ["eastmoney", "10jqka", "stcn", "cs", "cnstock", "zqrb", "yicai", "21jingji", "nbd"]
    terms = _query_terms(query, claim)
    search_queries = _web_search_queries(query, claim)[:4]
    results: list[EvidenceItem] = []
    for site in site_keys:
        target = DEFAULT_SITE_TARGETS.get(site, site)
        if not target:
            warnings.append(f"unknown web site target: {site}")
            continue
        urls = [target.format(query=quote_plus(item)) for item in search_queries] if "{query}" in target else [target]
        for url in urls:
            items = _fetch_links_from_page(url, site=site, warnings=warnings)
            for item in items:
                haystack = f"{item.title} {item.snippet}".lower()
                matched = [term for term in terms if term and term.lower() in haystack]
                if not _keep_web_evidence(item, matched, claim):
                    continue
                item.matched_terms = matched or [claim.company or query]
                item.score = _evidence_score(item.source, item.matched_terms, item.title, item.snippet)
                item.metadata["search_page"] = url
                results.append(item)
    return _dedup_evidence(sorted(results, key=lambda item: item.score, reverse=True))[:top_k]


def check_price_change(claim: Claim) -> NumericCheck:
    warnings: list[str] = []
    code = _resolve_a_share_code(claim.company)
    if not code:
        return NumericCheck(
            check_type="price_change",
            status="unknown",
            metric="stock_price",
            threshold_pct=claim.threshold_pct,
            warnings=["无法解析公司或股票代码，不能计算股价涨跌幅。"],
        )

    rows: list[dict[str, Any]] = []
    data_source = ""

    try:
        from tools.tushare_provider import pro_bar

        df = pro_bar(ts_code=_to_ts_code(code), limit=260, adj="qfq")
        rows = df.to_dict(orient="records") if df is not None else []
        data_source = "tushare.pro_bar"
    except Exception as exc:
        warnings.append(f"Tushare行情数据获取失败: {type(exc).__name__}: {exc}")

    if not rows:
        try:
            import akshare as ak  # type: ignore

            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
            rows = df.to_dict(orient="records") if df is not None else []
            data_source = "akshare.stock_zh_a_hist"
        except Exception as exc:
            warnings.append(f"AKShare行情数据获取失败: {type(exc).__name__}: {exc}")

    if rows and any("trade_date" in row for row in rows):
        rows = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))

    closes = [_row_float(row, ["close", "Close", "收盘", "收盘价"]) for row in rows]
    closes = [value for value in closes if value is not None]
    if len(closes) < 2:
        return NumericCheck(
            check_type="price_change",
            status="unknown",
            metric="stock_price",
            threshold_pct=claim.threshold_pct,
            details={"symbol": code, "ts_code": _to_ts_code(code), "data_source": data_source},
            warnings=[*warnings, "行情数据不足。"],
        )

    windows = {"1日": 1, "5日": 5, "20日": 20, "60日": 60, "120日": 120}
    changes: dict[str, float] = {}
    for label, n in windows.items():
        if len(closes) > n and closes[-1 - n] != 0:
            changes[label] = (closes[-1] / closes[-1 - n] - 1) * 100
    threshold = claim.threshold_pct
    actual = _best_matching_change(changes, threshold)
    status = _compare_direction(actual, threshold, claim.direction) if threshold is not None else "informational"
    return NumericCheck(
        check_type="price_change",
        status=status,
        metric="stock_price",
        actual_pct=actual,
        threshold_pct=threshold,
        details={
            "symbol": code,
            "ts_code": _to_ts_code(code),
            "data_source": data_source,
            "window_changes_pct": changes,
            "latest_close": closes[-1],
        },
        warnings=warnings,
    )


def check_financial_growth(claim: Claim) -> NumericCheck:
    code = _resolve_a_share_code(claim.company)
    if not code:
        return NumericCheck(
            check_type="financial_growth",
            status="unknown",
            metric=claim.metric,
            threshold_pct=claim.threshold_pct,
            warnings=["无法解析公司或股票代码，不能自动抓取财务指标。"],
        )
    try:
        import akshare as ak  # type: ignore
        df = ak.stock_financial_analysis_indicator(symbol=code)
    except Exception as exc:
        return NumericCheck(
            check_type="financial_growth",
            status="unknown",
            metric=claim.metric,
            threshold_pct=claim.threshold_pct,
            warnings=[f"财务指标获取失败: {type(exc).__name__}: {exc}"],
        )
    rows = df.to_dict(orient="records") if df is not None else []
    if not rows:
        return NumericCheck(check_type="financial_growth", status="unknown", metric=claim.metric, warnings=["财务指标数据为空。"])

    latest = rows[0]
    metric_keys = {
        "revenue": ["营业收入同比增长率", "主营业务收入增长率", "营业总收入同比增长"],
        "net_profit": ["净利润同比增长率", "归属母公司股东的净利润同比增长率", "净利润增长率"],
        "deducted_net_profit": ["扣除非经常性损益后的净利润同比增长率", "扣非净利润同比增长率"],
    }
    actual = _row_float(latest, metric_keys.get(claim.metric, metric_keys["net_profit"]))
    threshold = claim.threshold_pct
    status = _compare_direction(actual, threshold, claim.direction) if threshold is not None else "informational"
    if threshold is None and actual is not None and claim.direction == "up":
        status = "supported" if actual >= 30 else "not_supported"
    return NumericCheck(
        check_type="financial_growth",
        status=status,
        metric=claim.metric,
        actual_pct=actual,
        threshold_pct=threshold,
        period=str(_first_existing(latest, ["报告日期", "日期", "REPORT_DATE"]) or ""),
        details={"symbol": code, "latest_row": latest},
        warnings=[] if actual is not None else ["未能在财务指标中找到匹配口径，需用公告原文进一步核验。"],
    )


def synthesize_verdict(
    *,
    claim: Claim,
    numeric_checks: list[NumericCheck],
    local_evidence: list[EvidenceItem],
    web_evidence: list[EvidenceItem],
) -> tuple[str, float, list[str], list[str]]:
    evidence = local_evidence + web_evidence
    support_score = sum(item.score for item in evidence[:10])
    confidence = min(0.9, support_score / 8)
    missing: list[str] = []
    conflicts: list[str] = []

    if numeric_checks:
        check = numeric_checks[0]
        if check.status == "supported":
            confidence = max(confidence, 0.78)
            return "成立", round(confidence, 2), conflicts, missing
        if check.status == "not_supported":
            confidence = max(confidence, 0.7)
            if evidence:
                conflicts.append("文本证据存在相关报道，但原始数值计算未支持该精确断言，可能存在口径或时间区间差异。")
            return "不成立或口径不匹配", round(confidence, 2), conflicts, missing
        missing.extend(check.warnings)
        if claim.precision == "exact":
            capped = min(max(confidence, 0.35 if evidence else 0.2), 0.55)
            if evidence:
                missing.append("这是精确数值断言，但原始数值核验未完成；新闻只能作为待比较材料，不能单独证明数值成立。")
                return "文本证据存在，但精确数值待核验", round(capped, 2), conflicts, missing

    official_like = [item for item in evidence if _source_weight(item.source) >= 0.95]
    if official_like and len(evidence) >= 2:
        confidence = max(confidence, 0.72)
        return "证据较强，但仍需核对原始口径", round(confidence, 2), conflicts, missing
    if len(evidence) >= 3:
        confidence = max(confidence, 0.58)
        missing.append("缺少原始数值或公告级证据，只能认为多源报道方向一致。")
        return "部分成立或待公告确认", round(confidence, 2), conflicts, missing
    if evidence:
        confidence = max(confidence, 0.38)
        missing.append("证据数量不足，且缺少权威原始来源。")
        return "证据不足", round(confidence, 2), conflicts, missing
    missing.append("本地新闻库和指定网络来源均未找到有效证据。")
    return "未找到证据", 0.1, conflicts, missing


def render_verification_markdown(report: VerificationReport) -> str:
    lines = [
        f"# 断言核验：{report.question}",
        "",
        f"- 结论：**{report.verdict}**",
        f"- 置信度：{report.confidence:.2f}",
        f"- 断言类型：{report.claim.claim_type}",
        f"- 公司/对象：{report.claim.company or '-'}",
        f"- 指标：{report.claim.metric or '-'}",
        f"- 阈值：{_fmt_pct(report.claim.threshold_pct)}",
        "",
        "## 1. 数值核验",
        "",
    ]
    if not report.numeric_checks:
        lines.append("- 该断言未触发数值核验。")
    for check in report.numeric_checks:
        lines.extend([
            f"- 类型：{check.check_type}",
            f"- 状态：{check.status}",
            f"- 实际值：{_fmt_pct(check.actual_pct)}",
            f"- 用户阈值：{_fmt_pct(check.threshold_pct)}",
            f"- 期间：{check.period or '-'}",
        ])
        if check.details:
            lines.append(f"- 细节：`{json.dumps(_compact_details(check.details), ensure_ascii=False)}`")
        for warning in check.warnings:
            lines.append(f"- 警告：{warning}")
        lines.append("")

    lines.extend(["## 2. 本地新闻库证据", ""])
    _append_evidence(lines, report.local_evidence)
    lines.extend(["", "## 3. 网络/指定网站证据", ""])
    _append_evidence(lines, report.web_evidence)

    lines.extend(["", "## 4. 交叉验证说明", ""])
    if report.conflicts:
        lines.append("### 冲突")
        for item in report.conflicts:
            lines.append(f"- {item}")
    if report.missing_evidence:
        lines.append("### 缺失证据")
        for item in report.missing_evidence:
            lines.append(f"- {item}")
    if report.warnings:
        lines.append("### 运行告警")
        for item in report.warnings[:20]:
            lines.append(f"- {item}")
    lines.append("")
    lines.append("> 本报告用于事实核验和投研辅助，不构成投资建议。")
    return "\n".join(lines)


def _append_evidence(lines: list[str], evidence: list[EvidenceItem]) -> None:
    if not evidence:
        lines.append("- 未找到证据。")
        return
    for idx, item in enumerate(evidence, 1):
        terms = ", ".join(item.matched_terms) or "-"
        lines.append(
            f"{idx}. {item.title} | {item.source} | score={item.score:.2f} | terms={terms} | {item.url or '-'}"
        )


def _default_local_dirs() -> list[Path]:
    return [
        Path("./data/finance_news"),
        Path("./data/daily_market"),
        Path("D:/lh/gupiao_agents/MediaCrawler-main/data/finance_news"),
    ]


def _iter_json_sources(root: Path):
    patterns = ["*.json", "*.jsonl"]
    for pattern in patterns:
        yield from root.rglob(pattern)


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    if isinstance(row, dict):
                        row["_path"] = str(path)
                        records.append(row)
        return records
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                row["_path"] = str(path)
                records.append(row)
    elif isinstance(data, dict):
        for key in ["raw_documents", "local_evidence", "announcements", "research_reports"]:
            value = data.get(key)
            if isinstance(value, list):
                for row in value:
                    if isinstance(row, dict):
                        row["_path"] = str(path)
                        records.append(row)
        classified = data.get("classified_events")
        if isinstance(classified, dict):
            for values in classified.values():
                if isinstance(values, list):
                    for row in values:
                        if isinstance(row, dict):
                            row["_path"] = str(path)
                            records.append(row)
    return records


def _path_date(path: Path) -> datetime | None:
    text = str(path)
    for pattern in [r"(\d{4}-\d{2}-\d{2})", r"(\d{8})"]:
        match = re.search(pattern, text)
        if not match:
            continue
        raw = match.group(1)
        try:
            return datetime.strptime(raw, "%Y-%m-%d" if "-" in raw else "%Y%m%d")
        except Exception:
            pass
    return None





def _fetch_links_from_page(url: str, *, site: str, warnings: list[str]) -> list[EvidenceItem]:
    try:
        os.environ.pop("SSLKEYLOGFILE", None)
        import requests

        resp = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 ChainAlphaClaimVerifier/0.1"},
        )
        if resp.status_code == 404:
            warnings.append(f"web page skipped {site}: 404 for {url}")
            return []
        resp.raise_for_status()
        items = _extract_links_from_html(resp.text, base_url=url, site=site)
        if items:
            return items
        warnings.append(f"web page has no html links {site}: {url}; fallback to Playwright")
    except Exception as exc:
        warnings.append(f"requests page fetch failed {site}: {type(exc).__name__}: {exc}; fallback to Playwright")
    return _fetch_links_with_playwright(url, site=site, warnings=warnings)
def _keep_web_evidence(item: EvidenceItem, matched: list[str], claim: Claim) -> bool:
    title = f"{item.title} {item.snippet}".lower()
    company = (claim.company or "").lower()
    code = _resolve_a_share_code(claim.company)
    if company and company in title:
        return True
    if code and code in title:
        return True
    strong_terms = [term for term in matched if term not in {"30", "30%", claim.metric}]
    if len(set(strong_terms)) >= 2:
        return True
    # Search-result pages often shorten titles. Keep rows from a company-specific
    # query if they are market/news-looking rather than navigation links.
    market_words = ["股价", "上涨", "涨幅", "证券", "财经", "公告", "业绩", "净利润", "中际", "旭创"]
    return bool(company and any(word.lower() in title for word in market_words))
def _fetch_links_with_playwright(url: str, *, site: str, warnings: list[str]) -> list[EvidenceItem]:
    try:
        return asyncio.run(_fetch_links_with_playwright_async(url, site=site))
    except RuntimeError:
        # If this is ever called from an existing event loop, fall back to no web evidence.
        warnings.append(f"playwright web search skipped {site}: existing event loop is running")
        return []
    except Exception as exc:
        warnings.append(f"playwright web search failed {site}: {type(exc).__name__}: {exc}")
        return []


async def _fetch_links_with_playwright_async(url: str, *, site: str) -> list[EvidenceItem]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2500)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            links = await page.query_selector_all("a")
            items: list[EvidenceItem] = []
            seen: set[str] = set()
            for link in links[:300]:
                title = re.sub(r"\s+", " ", (await link.inner_text() or "")).strip()
                href = await link.get_attribute("href")
                if len(title) < 8 or len(title) > 180 or not re.search(r"[\u4e00-\u9fff]", title):
                    continue
                if any(bad in title.lower() for bad in ["登录", "注册", "广告", "版权", "更多", "首页", "app下载", "app下载"]):
                    continue
                link_url = _absolute_url(url, href or "")
                key = link_url or title
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(
                    EvidenceItem(
                        source_layer="web_search_playwright",
                        source=_host_label(link_url) or site,
                        title=title,
                        url=link_url,
                        snippet=title,
                        metadata={"search_page": url, "extractor": "playwright"},
                    )
                )
            return items
        finally:
            await context.close()
            await browser.close()
def _extract_links_from_html(html: str, *, base_url: str, site: str) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, flags=re.I | re.S):
        href, raw_title = match.groups()
        title = re.sub(r"<[^>]+>", "", raw_title)
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 8 or not re.search(r"[\u4e00-\u9fff]", title):
            continue
        url = _absolute_url(base_url, href)
        source = _host_label(url) or site
        items.append(EvidenceItem(source_layer="web_search", source=source, title=title, url=url, snippet=title))
    return _dedup_evidence(items)


def _absolute_url(base_url: str, href: str) -> str:
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith(("http://", "https://")):
        return href
    parsed = urlparse(base_url)
    if href.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    return f"{parsed.scheme}://{parsed.netloc}/{href}"


def _dedup_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[str] = set()
    out: list[EvidenceItem] = []
    for item in items:
        key = item.url or item.title
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _web_search_queries(query: str, claim: Claim) -> list[str]:
    base_terms: list[str] = []
    if claim.company:
        base_terms.append(claim.company)
    code = _resolve_a_share_code(claim.company)
    if code:
        base_terms.append(code)
        base_terms.append(_to_ts_code(code))
    metric_terms = {
        "stock_price": ["股价", "上涨" if claim.direction != "down" else "下跌", "涨幅" if claim.direction != "down" else "跌幅"],
        "net_profit": ["净利润", "业绩"],
        "deducted_net_profit": ["扣非净利润", "业绩"],
        "revenue": ["营收", "收入"],
    }.get(claim.metric, [])
    pct = f"{claim.threshold_pct:g}%" if claim.threshold_pct is not None else ""
    queries = []
    for base in base_terms or [query]:
        if pct:
            queries.append(" ".join([base, *metric_terms, pct]).strip())
        queries.append(" ".join([base, *metric_terms]).strip())
        queries.append(base)
    queries.append(query)
    return list(dict.fromkeys([item for item in queries if item]))

def _query_terms(query: str, claim: Claim) -> list[str]:
    terms = [
        claim.company,
        claim.metric,
        str(int(claim.threshold_pct)) if claim.threshold_pct else "",
        f"{claim.threshold_pct:g}%" if claim.threshold_pct is not None else "",
        *re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", query),
    ]
    aliases = {
        "net_profit": ["净利润", "归母净利润", "利润", "业绩"],
        "deducted_net_profit": ["扣非", "扣非净利润", "扣非归母净利润"],
        "revenue": ["营收", "收入", "营业收入"],
        "stock_price": ["股价", "上涨", "涨幅", "下跌", "跌幅", "走强"],
    }
    terms.extend(aliases.get(claim.metric, []))
    code = _resolve_a_share_code(claim.company)
    if code:
        terms.extend([code, _to_ts_code(code)])
    return list(dict.fromkeys([term for term in terms if term and term != "None"]))


def _evidence_score(source: str, matched: list[str], title: str, content: str) -> float:
    weight = _source_weight(source)
    density = min(len(set(matched)) / 5, 1)
    title_bonus = 0.25 if any(term in title for term in matched) else 0
    length_bonus = 0.1 if len(content) > 80 else 0
    return round(weight + density + title_bonus + length_bonus, 3)


def _source_weight(source: str) -> float:
    text = source.lower()
    for key, value in SOURCE_WEIGHTS.items():
        if key.lower() in text:
            return value
    return 0.45


def _extract_pct(text: str) -> float | None:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", text)
    if match:
        return float(match.group(1))
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:个点|个百分点)", text)
    if match:
        return float(match.group(1))
    return None


def _extract_company(text: str) -> str:
    match = re.search(r"\b(?:sh|sz|bj)?(\d{6})\b", text, flags=re.I)
    if match:
        return match.group(1)
    stop_words = [
        "业绩", "净利润", "利润", "营收", "收入", "扣非", "股价", "股票", "上涨", "下跌",
        "大增", "增长", "涨了", "跌了", "涨幅", "跌幅", "了吗", "是否", "有没有", "真的",
    ]
    cleaned = text
    for word in stop_words:
        cleaned = cleaned.replace(word, " ")
    cleaned = re.sub(r"[，。！？、,%百分之\d\s]+", " ", cleaned)
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z]{2,12}", cleaned)
    return candidates[0] if candidates else ""


def _extract_period_text(text: str) -> str:
    for pattern in [r"近\d+日", r"近\d+天", r"近\d+月", r"\d{4}年(?:年报|一季报|半年报|三季报)?", r"年初至今"]:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""


def _resolve_a_share_code(company_or_code: str) -> str:
    raw = str(company_or_code or "").strip()
    match = re.search(r"(?:sh|sz|bj)?(\d{6})", raw, flags=re.I)
    if match:
        return match.group(1)

    local_map = {
        "中际旭创": "300308",
        "新易盛": "300502",
        "天孚通信": "300394",
        "光迅科技": "002281",
        "华工科技": "000988",
        "剑桥科技": "603083",
    }
    if raw in local_map:
        return local_map[raw]

    try:
        from tools.tushare_provider import init_tushare_pro

        pro = init_tushare_pro()
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name")
        matched = df[df["name"].astype(str).str.contains(raw, case=False, na=False)]
        if not matched.empty:
            return str(matched.iloc[0]["symbol"]).zfill(6)
    except Exception:
        pass

    try:
        import akshare as ak  # type: ignore

        df = ak.stock_info_a_code_name()
        matched = df[df["name"].astype(str).str.contains(raw, case=False, na=False)]
        if not matched.empty:
            return str(matched.iloc[0]["code"]).zfill(6)
    except Exception:
        return ""
    return ""


def _to_ts_code(code: str) -> str:
    code = str(code or "").strip().upper().replace(".SZ", "").replace(".SH", "")
    if code.endswith("SZ") or code.endswith("SH"):
        code = code[:6]
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _row_float(row: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if value in (None, ""):
            continue
        text = str(value).replace(",", "").replace("%", "").strip()
        try:
            value_float = float(text)
            if not math.isnan(value_float):
                return value_float
        except Exception:
            continue
    return None


def _first_existing(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _best_matching_change(changes: dict[str, float], threshold: float | None) -> float | None:
    if not changes:
        return None
    if threshold is None:
        return changes.get("20日") or list(changes.values())[-1]
    return min(changes.values(), key=lambda value: abs(abs(value) - abs(threshold)))


def _compare_direction(actual: float | None, threshold: float | None, direction: str) -> str:
    if actual is None or threshold is None:
        return "unknown"
    if direction == "down":
        return "supported" if actual <= -abs(threshold) else "not_supported"
    return "supported" if actual >= abs(threshold) else "not_supported"


def _host_label(url: str) -> str:
    host = urlparse(url).netloc
    return host.replace("www.", "")


def _fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}%"


def _compact_details(details: dict[str, Any]) -> dict[str, Any]:
    compact = dict(details)
    if "latest_row" in compact:
        row = compact["latest_row"]
        compact["latest_row_keys"] = list(row)[:20] if isinstance(row, dict) else []
        compact.pop("latest_row", None)
    return compact













