from __future__ import annotations

from typing import Any

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, in_date_range, parse_datetime, text_matches
from chainalpha.connectors.http_utils import best_effort_json, http_get


class SecEdgarConnector(BaseConnector):
    """SEC EDGAR connector using official company_tickers + submissions JSON.

    Provide tickers such as NVDA, MSFT, AVGO. SEC asks automated clients to use
    an identifiable User-Agent with contact information; configure `user_agent`.
    """

    connector_type = "sec_edgar"

    def __init__(
        self,
        name: str,
        tickers: list[str] | None = None,
        cik_list: list[str] | None = None,
        forms: list[str] | None = None,
        user_agent: str = "ChainAlpha research contact@example.com",
        fetch_primary_documents: bool = False,
        max_filings_per_company: int = 20,
        timeout: int = 20,
        **params: Any,
    ) -> None:
        super().__init__(name, tickers=tickers, cik_list=cik_list, forms=forms, **params)
        self.tickers = [t.upper() for t in (tickers or [])]
        self.cik_list = [str(c).zfill(10) for c in (cik_list or [])]
        self.forms = forms or ["10-K", "10-Q", "8-K", "6-K", "20-F", "S-1"]
        self.user_agent = user_agent
        self.fetch_primary_documents = fetch_primary_documents
        self.max_filings_per_company = max_filings_per_company
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        ciks = list(dict.fromkeys([*self.cik_list, *self._ticker_to_ciks(self.tickers)]))
        docs: list[SourceDocument] = []
        for cik in ciks:
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            data = best_effort_json(http_get(url, timeout=self.timeout, headers={"User-Agent": self.user_agent}, sleep_seconds=0.15))
            company_name = data.get("name") or cik
            recent = data.get("filings", {}).get("recent", {})
            docs.extend(self._recent_filings_to_docs(cik, company_name, recent, request))
            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)

    def _ticker_to_ciks(self, tickers: list[str]) -> list[str]:
        if not tickers:
            return []
        data = best_effort_json(http_get("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": self.user_agent}))
        lookup = {v.get("ticker", "").upper(): str(v.get("cik_str", "")).zfill(10) for v in data.values()}
        return [lookup[t] for t in tickers if t in lookup]

    def _recent_filings_to_docs(self, cik: str, company_name: str, recent: dict[str, list[Any]], request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        filing_dates = recent.get("filingDate") or []
        report_dates = recent.get("reportDate") or []
        primary_docs = recent.get("primaryDocument") or []
        descriptions = recent.get("primaryDocDescription") or []
        count = min(len(forms), self.max_filings_per_company)
        for i in range(count):
            form = str(forms[i])
            if self.forms and form not in self.forms:
                continue
            filed = parse_datetime(filing_dates[i] if i < len(filing_dates) else None)
            if not in_date_range(filed, request.start_date, request.end_date):
                continue
            accession = str(accessions[i])
            accession_no_dash = accession.replace("-", "")
            primary = str(primary_docs[i]) if i < len(primary_docs) else ""
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dash}/{primary}" if primary else f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dash}/"
            title = f"{company_name} {form} filed {filing_dates[i] if i < len(filing_dates) else ''}".strip()
            desc = str(descriptions[i]) if i < len(descriptions) else ""
            content = "\n".join(
                [
                    f"Company: {company_name}",
                    f"CIK: {cik}",
                    f"Form: {form}",
                    f"Filing date: {filing_dates[i] if i < len(filing_dates) else ''}",
                    f"Report date: {report_dates[i] if i < len(report_dates) else ''}",
                    f"Description: {desc}",
                    f"Primary document: {filing_url}",
                ]
            )
            if not text_matches(f"{title}\n{content}", request):
                continue
            docs.append(
                build_source_document(
                    source=self.name,
                    source_type="financial_report",
                    title=title,
                    content=content,
                    url=filing_url,
                    published_at=filed,
                    metadata={"cik": cik, "form": form, "accession": accession, "company_name": company_name},
                )
            )
        return docs
