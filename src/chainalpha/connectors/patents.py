from __future__ import annotations

from typing import Any

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, parse_datetime, text_matches
from chainalpha.connectors.http_utils import best_effort_json, http_post


class PatentsViewConnector(BaseConnector):
    """PatentsView connector for technology / company patent weak signals.

    The PatentsView API shape may evolve; endpoint and fields are configurable.
    """

    connector_type = "patentsview"

    def __init__(
        self,
        name: str,
        endpoint: str = "https://api.patentsview.org/patents/query",
        fields: list[str] | None = None,
        timeout: int = 20,
        **params: Any,
    ) -> None:
        super().__init__(name, endpoint=endpoint, fields=fields, **params)
        self.endpoint = endpoint
        self.fields = fields or ["patent_id", "patent_title", "patent_abstract", "patent_date", "assignees.assignee_organization"]
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        terms = request.terms or [""]
        per_page = min(request.limit or 50, 100)
        for term in terms:
            query = {"_text_any": {"patent_title": term, "patent_abstract": term}} if term else {"_gte": {"patent_date": "2000-01-01"}}
            body = {"q": query, "f": self.fields, "o": {"per_page": per_page}}
            resp = http_post(self.endpoint, json_body=body, timeout=self.timeout)
            data = best_effort_json(resp)
            for p in data.get("patents", []) or []:
                doc = self._patent_to_doc(p, request)
                if doc:
                    docs.append(doc)
            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)

    def _patent_to_doc(self, p: dict[str, Any], request: FetchRequest) -> SourceDocument | None:
        patent_id = str(p.get("patent_id") or "")
        title = str(p.get("patent_title") or f"Patent {patent_id}")
        abstract = str(p.get("patent_abstract") or "")
        assignees = p.get("assignees") or []
        orgs = [str(a.get("assignee_organization")) for a in assignees if isinstance(a, dict) and a.get("assignee_organization")]
        content = "\n".join([f"Title: {title}", f"Abstract: {abstract}", f"Assignee: {', '.join(orgs)}"])
        if not text_matches(f"{title}\n{content}", request):
            return None
        published = parse_datetime(p.get("patent_date"))
        url = f"https://patents.google.com/patent/US{patent_id}" if patent_id else None
        return build_source_document(
            source=self.name,
            source_type="patent",
            title=title,
            content=content,
            url=url,
            published_at=published,
            metadata={"raw": p, "patent_id": patent_id, "assignees": orgs},
        )
