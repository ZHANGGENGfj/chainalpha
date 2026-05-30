from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from chainalpha.connectors.base import FetchRequest
from chainalpha.connectors.cninfo import CninfoAnnouncementConnector
from chainalpha.investment.pdf_reports import enrich_pdf_document
from chainalpha.schemas import CompanyProfile, SourceDocument


REPORT_KEYWORDS = (
    "年度报告",
    "年报",
    "半年度报告",
    "半年报",
    "第一季度报告",
    "第三季度报告",
    "季度报告",
)

REPORT_CATEGORIES = (
    "category_ndbg_szsh",
    "category_bndbg_szsh",
    "category_sjdbg_szsh",
)


def fetch_a_share_report_documents(
    profiles: list[CompanyProfile],
    *,
    limit_per_company: int = 4,
    max_pages: int = 2,
    timeout: int = 20,
    parse_pdfs: bool = True,
    cache_dir: str | Path = "./data/cache/cninfo_pdfs",
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> tuple[dict[str, list[SourceDocument]], list[str]]:
    start_date = start_date or (datetime.now() - timedelta(days=550))
    grouped: dict[str, list[SourceDocument]] = {}
    warnings: list[str] = []
    if parse_pdfs:
        Path(cache_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)

    for profile in profiles:
        code = _a_share_code(profile.ticker)
        if not code:
            continue
        connector = CninfoAnnouncementConnector(
            name=f"cninfo_{code}",
            column=_a_share_column(profile),
            plate=_a_share_plate(profile),
            page_size=max(30, limit_per_company * 6),
            max_pages=max_pages,
            categories=list(REPORT_CATEGORIES),
            stocks=[""],
            timeout=timeout,
        )
        query_terms = [profile.name, *(profile.aliases or [])]
        result = connector.fetch_safe(
            FetchRequest(
                query=" ".join(term for term in query_terms if term),
                start_date=start_date,
                end_date=end_date,
                limit=limit_per_company * 8,
            )
        )
        if result.errors:
            warnings.extend(f"{profile.name}: {err}" for err in result.errors)
            continue
        docs = [
            doc
            for doc in result.documents
            if _looks_like_financial_report(doc) and str(doc.metadata.get("sec_code") or "") == code
        ]
        if not docs:
            warnings.append(f"{profile.name}: no CNINFO financial-report announcements found in current window.")
            continue
        if parse_pdfs:
            parsed_docs: list[SourceDocument] = []
            for doc in docs:
                try:
                    parsed_docs.append(enrich_pdf_document(doc, cache_dir=cache_dir, timeout=timeout))
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"{profile.name}: PDF parse failed: {type(exc).__name__}: {exc}")
                    parsed_docs.append(doc)
            docs = parsed_docs
        docs.sort(key=lambda doc: doc.published_at or datetime.min, reverse=True)
        if docs:
            grouped[_profile_key(profile)] = docs[:limit_per_company]
    return grouped, warnings


def _looks_like_financial_report(doc: SourceDocument) -> bool:
    title = doc.title or ""
    if not any(keyword in title for keyword in REPORT_KEYWORDS):
        return False
    return "摘要" not in title and "英文版" not in title


def _a_share_code(ticker: str | None) -> str | None:
    if not ticker:
        return None
    raw = ticker.strip().upper()
    if "." in raw:
        code, suffix = raw.split(".", 1)
        if suffix in {"SZ", "SH"} and code.isdigit():
            return code
    return None


def _a_share_column(profile: CompanyProfile) -> str:
    exchange = (profile.exchange or "").upper()
    ticker = (profile.ticker or "").upper()
    if exchange == "SSE" or ticker.endswith(".SH"):
        return "sse"
    return "szse"


def _a_share_plate(profile: CompanyProfile) -> str:
    exchange = (profile.exchange or "").upper()
    ticker = (profile.ticker or "").upper()
    if exchange == "SSE" or ticker.endswith(".SH"):
        return "sh"
    return "sz"


def _profile_key(profile: CompanyProfile) -> str:
    return (profile.ticker or profile.name).strip().lower()
