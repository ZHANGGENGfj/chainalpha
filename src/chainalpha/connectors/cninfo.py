from __future__ import annotations

from datetime import datetime
from typing import Any

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, in_date_range, parse_datetime, text_matches
from chainalpha.connectors.http_utils import best_effort_json, http_post, strip_html


CNINFO_ENDPOINT = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_BASE = "http://static.cninfo.com.cn/"


class CninfoAnnouncementConnector(BaseConnector):
    """巨潮资讯公告连接器.

    It queries the public announcement list endpoint and returns normalized
    announcement metadata with PDF URLs. PDF text extraction is intentionally not
    included in this connector; download and parse PDFs in a separate offline
    job if needed.
    """

    connector_type = "cninfo_announcements"

    def __init__(
        self,
        name: str,
        endpoint: str = CNINFO_ENDPOINT,
        column: str = "szse",
        plate: str = "",
        page_size: int = 30,
        max_pages: int = 3,
        categories: list[str] | None = None,
        stocks: list[str] | None = None,
        timeout: int = 20,
        **params: Any,
    ) -> None:
        super().__init__(
            name,
            endpoint=endpoint,
            column=column,
            plate=plate,
            page_size=page_size,
            max_pages=max_pages,
            **params,
        )
        self.endpoint = endpoint
        self.column = column
        self.plate = plate
        self.page_size = page_size
        self.max_pages = max_pages
        self.categories = categories or []
        self.stocks = stocks or []
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        terms = request.terms or [""]
        cats = self.categories or [""]
        stocks = self.stocks or [""]
        for term in terms:
            for category in cats:
                for stock in stocks:
                    for page_num in range(1, self.max_pages + 1):
                        payload = {
                            "pageNum": page_num,
                            "pageSize": self.page_size,
                            "column": self.column,
                            "tabName": "fulltext",
                            "plate": self.plate,
                            "stock": stock,
                            "searchkey": term,
                            "secid": "",
                            "category": category,
                            "trade": "",
                            "seDate": self._date_range(request),
                            "sortName": "",
                            "sortType": "",
                            "isHLtitle": "true",
                        }
                        resp = http_post(
                            self.endpoint,
                            data=payload,
                            timeout=self.timeout,
                            headers={"Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"},
                            sleep_seconds=0.2,
                        )
                        data = best_effort_json(resp)
                        anns = data.get("announcements") or []
                        if not anns:
                            break
                        for ann in anns:
                            doc = self._announcement_to_doc(ann, request)
                            if doc:
                                docs.append(doc)
                        if request.limit is not None and len(docs) >= request.limit:
                            return apply_limit(docs, request.limit)
        return apply_limit(docs, request.limit)

    def _date_range(self, request: FetchRequest) -> str:
        if not request.start_date and not request.end_date:
            return ""
        start = request.start_date.strftime("%Y-%m-%d") if request.start_date else ""
        end = request.end_date.strftime("%Y-%m-%d") if request.end_date else datetime.now().strftime("%Y-%m-%d")
        return f"{start}~{end}"

    def _announcement_to_doc(self, ann: dict[str, Any], request: FetchRequest) -> SourceDocument | None:
        title = strip_html(str(ann.get("announcementTitle") or ann.get("title") or "公告"))
        sec_name = str(ann.get("secName") or "")
        sec_code = str(ann.get("secCode") or "")
        adjunct = ann.get("adjunctUrl") or ann.get("adjurl") or ""
        url = CNINFO_BASE + adjunct if adjunct and not str(adjunct).startswith("http") else adjunct
        published = parse_datetime(ann.get("announcementTime"))
        content = "\n".join(
            part
            for part in [
                f"公告标题：{title}",
                f"公司：{sec_name} {sec_code}".strip(),
                f"公告类型：{ann.get('categoryName', '')}",
                f"PDF：{url}" if url else "",
            ]
            if part
        )
        if not in_date_range(published, request.start_date, request.end_date):
            return None
        if not text_matches(f"{title}\n{content}", request):
            return None
        return build_source_document(
            source=self.name,
            source_type="official_announcement",
            title=title,
            content=content,
            url=url or None,
            published_at=published,
            metadata={"raw": ann, "sec_name": sec_name, "sec_code": sec_code, "source_platform": "cninfo"},
        )
