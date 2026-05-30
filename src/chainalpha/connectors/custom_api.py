from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, parse_datetime, text_matches
from chainalpha.connectors.http_utils import best_effort_json, http_get, http_post


class JSONAPIConnector(BaseConnector):
    """Configurable JSON API connector.

    Use this for company websites, project备案, price APIs, import/export APIs,
    tender APIs, or paid/licensed data. It maps JSON records into SourceDocument.
    """

    connector_type = "json_api"

    def __init__(
        self,
        name: str,
        url: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        records_path: str = "",
        title_field: str = "title",
        content_fields: list[str] | None = None,
        url_field: str | None = None,
        time_field: str | None = None,
        source_type: str = "json_api",
        timeout: int = 20,
        **extra: Any,
    ) -> None:
        super().__init__(name, url=url, method=method, **extra)
        self.url = url
        self.method = method.upper()
        self.params = params or {}
        self.headers = headers or {}
        self.json_body = json_body or {}
        self.records_path = records_path
        self.title_field = title_field
        self.content_fields = content_fields or ["content", "summary", "description", "text"]
        self.url_field = url_field
        self.time_field = time_field
        self.source_type = source_type
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        terms = request.terms or [request.query or ""]
        for term in terms:
            params = self._render(self.params, term)
            body = self._render(self.json_body, term)
            url = self.url.format(query=quote_plus(term), raw_query=term)
            if self.method == "POST":
                resp = http_post(url, timeout=self.timeout, headers=self.headers, json_body=body)
            else:
                resp = http_get(url, timeout=self.timeout, headers=self.headers, params=params)
            data = best_effort_json(resp)
            records = self._extract_records(data)
            for rec in records:
                doc = self._record_to_doc(rec, request)
                if doc:
                    docs.append(doc)
            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)

    def _render(self, obj: Any, term: str) -> Any:
        if isinstance(obj, str):
            return obj.format(query=quote_plus(term), raw_query=term)
        if isinstance(obj, dict):
            return {k: self._render(v, term) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._render(v, term) for v in obj]
        return obj

    def _extract_records(self, data: Any) -> list[dict[str, Any]]:
        node = data
        if self.records_path:
            for part in self.records_path.split("."):
                if not part:
                    continue
                if isinstance(node, dict):
                    node = node.get(part, [])
                else:
                    node = []
        if isinstance(node, list):
            return [x for x in node if isinstance(x, dict)]
        if isinstance(node, dict):
            return [node]
        return []

    def _record_to_doc(self, rec: dict[str, Any], request: FetchRequest) -> SourceDocument | None:
        title = str(rec.get(self.title_field) or rec.get("name") or rec.get("headline") or "JSON API record")
        parts = []
        for f in self.content_fields:
            val = rec.get(f)
            if val:
                parts.append(f"{f}: {val}")
        content = "\n".join(parts) or str(rec)
        published = parse_datetime(rec.get(self.time_field)) if self.time_field else None
        url = str(rec.get(self.url_field)) if self.url_field and rec.get(self.url_field) else None
        if not text_matches(f"{title}\n{content}", request):
            return None
        return build_source_document(
            source=self.name,
            source_type=self.source_type,
            title=title,
            content=content,
            url=url,
            published_at=published,
            metadata={"raw": rec},
        )
