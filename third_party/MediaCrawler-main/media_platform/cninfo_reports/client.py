from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re
from typing import Any

import requests


CNINFO_ENDPOINT = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_BASE = "http://static.cninfo.com.cn/"

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


class CninfoReportsClient:
    """Small CNINFO financial-report collector for MediaCrawler.

    It queries CNINFO's public announcement list endpoint and downloads report
    PDFs into the local MediaCrawler data directory.
    """

    def __init__(self, *, timeout: int = 20) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
        )

    def fetch_reports(
        self,
        *,
        keywords: list[str],
        max_pages: int = 2,
        page_size: int = 30,
        download_pdf: bool = True,
        pdf_dir: str | Path = "data/cninfo_reports/pdfs",
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        terms = keywords or [""]
        for term in terms:
            for category in REPORT_CATEGORIES:
                for page_num in range(1, max_pages + 1):
                    payload = {
                        "pageNum": page_num,
                        "pageSize": page_size,
                        "column": "szse",
                        "tabName": "fulltext",
                        "plate": "",
                        "stock": "",
                        "searchkey": term,
                        "secid": "",
                        "category": category,
                        "trade": "",
                        "seDate": "",
                        "sortName": "",
                        "sortType": "",
                        "isHLtitle": "true",
                    }
                    data = self._post_json(payload)
                    announcements = data.get("announcements") or []
                    if not announcements:
                        break
                    for ann in announcements:
                        item = self._announcement_item(ann, matched_keyword=term)
                        if not item or not _looks_like_report(item):
                            continue
                        if download_pdf and item.get("链接"):
                            item["pdf_path"] = self.download_pdf(item["链接"], pdf_dir=pdf_dir)
                        rows.append(item)
        return _dedup_rows(rows)

    def download_pdf(self, url: str, *, pdf_dir: str | Path) -> str:
        path_root = Path(pdf_dir)
        path_root.mkdir(parents=True, exist_ok=True)
        target = path_root / f"{_hash(url)}.pdf"
        if target.exists() and target.stat().st_size > 0:
            return str(target)
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        target.write_bytes(resp.content)
        return str(target)

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(CNINFO_ENDPOINT, data=payload, timeout=self.timeout)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {}

    def _announcement_item(self, ann: dict[str, Any], *, matched_keyword: str) -> dict[str, Any] | None:
        title = _strip_html(str(ann.get("announcementTitle") or ann.get("title") or "")).strip()
        if not title:
            return None
        adjunct = ann.get("adjunctUrl") or ann.get("adjurl") or ""
        url = CNINFO_BASE + adjunct if adjunct and not str(adjunct).startswith("http") else adjunct
        published_at = _parse_cninfo_time(ann.get("announcementTime"))
        return {
            "来源": "巨潮资讯",
            "公司": str(ann.get("secName") or ""),
            "代码": str(ann.get("secCode") or ""),
            "标题": title,
            "公告日期": published_at,
            "公告类型": str(ann.get("categoryName") or ""),
            "链接": url,
            "匹配关键词": matched_keyword,
            "raw": ann,
        }


def _looks_like_report(item: dict[str, Any]) -> bool:
    title = str(item.get("标题") or "")
    return any(keyword in title for keyword in REPORT_KEYWORDS) and "摘要" not in title and "英文版" not in title


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


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _parse_cninfo_time(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        raw = float(value)
        if raw > 10_000_000_000:
            raw = raw / 1000
        return datetime.fromtimestamp(raw).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
