from __future__ import annotations

import re
import time
from typing import Any

import requests


EASTMONEY_REPORT_API = "https://reportapi.eastmoney.com/report/list"


class BrokerReportsClient:
    """Lightweight broker/institution research-report collector.

    The current implementation targets Eastmoney's public research-report list
    endpoint. It stores metadata and report URLs; many report pages are HTML
    rather than direct PDFs, so PDF download is intentionally handled later by
    the analysis side if a PDF URL is available.
    """

    def __init__(self, *, timeout: int = 20) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://data.eastmoney.com/report/",
            }
        )

    def fetch_reports(self, *, keywords: list[str], page_size: int = 50) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for keyword in keywords or [""]:
            code = _extract_a_share_code(keyword)
            if code:
                rows.extend(self._fetch_by_code(code, page_size=page_size, matched_keyword=keyword))
            rows.extend(self._fetch_by_keyword(keyword, page_size=page_size, matched_keyword=keyword))
        return _dedup_rows(rows)

    def _fetch_by_code(self, code: str, *, page_size: int, matched_keyword: str) -> list[dict[str, Any]]:
        params = {
            "pageNo": 1,
            "pageSize": page_size,
            "code": code,
            "industryCode": "*",
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": "",
            "endTime": "",
            "qType": 0,
            "orgCode": "",
            "_": int(time.time() * 1000),
        }
        return self._fetch(params, matched_keyword=matched_keyword)

    def _fetch_by_keyword(self, keyword: str, *, page_size: int, matched_keyword: str) -> list[dict[str, Any]]:
        params = {
            "pageNo": 1,
            "pageSize": page_size,
            "industryCode": "*",
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": "",
            "endTime": "",
            "qType": 0,
            "orgCode": "",
            "keyword": keyword,
            "_": int(time.time() * 1000),
        }
        return self._fetch(params, matched_keyword=matched_keyword)

    def _fetch(self, params: dict[str, Any], *, matched_keyword: str) -> list[dict[str, Any]]:
        try:
            resp = self.session.get(EASTMONEY_REPORT_API, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = _loads_json_or_jsonp(resp.text)
        except Exception:
            return []
        records = data.get("data") or data.get("Data") or []
        if isinstance(records, dict):
            records = records.get("list") or records.get("List") or []
        out: list[dict[str, Any]] = []
        for record in records if isinstance(records, list) else []:
            item = _record_to_item(record, matched_keyword=matched_keyword)
            if item:
                out.append(item)
        return out


def _record_to_item(record: dict[str, Any], *, matched_keyword: str) -> dict[str, Any] | None:
    title = _first(record, "title", "TITLE", "REPORT_TITLE", "reportTitle", "art_title")
    if not title:
        return None
    code = _first(record, "stockCode", "STOCK_CODE", "SECURITY_CODE", "code")
    company = _first(record, "stockName", "STOCK_NAME", "SECURITY_NAME", "name")
    org = _first(record, "orgSName", "ORG_S_NAME", "ORG_NAME", "orgName")
    author = _first(record, "emRatingName", "AUTHOR", "author", "researcher")
    rating = _first(record, "emRatingValue", "RATING", "rating", "ratingName", "RATE_NAME")
    publish_date = _first(record, "publishDate", "PUBLISH_DATE", "datetime", "date")
    url = _first(record, "encodeUrl", "url", "URL", "attachUrl", "ATTACH_URL")
    if url and not str(url).startswith(("http://", "https://")):
        url = "https://data.eastmoney.com/report/zw_industry.jshtml?encodeUrl=" + str(url)
    return {
        "来源": "东方财富研报",
        "公司": company,
        "代码": code,
        "标题": title,
        "机构": org,
        "作者": author,
        "评级": rating,
        "发布日期": publish_date,
        "链接": url,
        "匹配关键词": matched_keyword,
        "raw": record,
    }


def _loads_json_or_jsonp(text: str) -> dict[str, Any]:
    import json

    raw = text.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start:end + 1])
    return {}


def _first(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _extract_a_share_code(text: str) -> str | None:
    match = re.search(r"\b([036]\d{5})(?:\.(?:SZ|SH))?\b", text.upper())
    return match.group(1) if match else None


def _dedup_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("链接") or row.get("标题") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out
