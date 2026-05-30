from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from chainalpha.connectors.base import (
    BaseConnector,
    FetchRequest,
    SourceDocument,
    apply_limit,
    build_source_document,
    in_date_range,
    parse_datetime,
    resolve_path,
    text_matches,
)
from chainalpha.memory.markdown_parser import parse_frontmatter
from chainalpha.utils.text import normalize_text


TITLE_KEYS = ["title", "标题", "name", "subject", "summary", "desc", "description"]
CONTENT_KEYS = [
    "content",
    "正文",
    "text",
    "note_content",
    "comment_content",
    "description",
    "desc",
    "summary",
    "raw_content",
]
URL_KEYS = ["url", "link", "href", "source_url"]
TIME_KEYS = ["published_at", "publish_time", "created_at", "create_time", "time", "date", "timestamp"]


class LocalFilesConnector(BaseConnector):
    """Read local text/markdown/json/jsonl/csv files.

    This connector is intentionally generic so users can drop exports from
    news crawlers, announcements, research notes, or manually collected files
    into a folder and scan them without writing a new connector.
    """

    connector_type = "local_files"

    def __init__(
        self,
        name: str,
        path: str | Path,
        patterns: list[str] | None = None,
        source_type: str = "local_file",
        recursive: bool = True,
        **params: Any,
    ) -> None:
        super().__init__(name, path=str(path), patterns=patterns, source_type=source_type, recursive=recursive, **params)
        self.root = resolve_path(path)
        self.patterns = patterns or ["*.md", "*.txt", "*.json", "*.jsonl", "*.csv"]
        self.source_type = source_type
        self.recursive = recursive

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        if not self.root.exists():
            return []

        docs: list[SourceDocument] = []
        for file_path in self._iter_files():
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            if not in_date_range(mtime, request.start_date, request.end_date):
                continue
            docs.extend(self._read_file(file_path, request, mtime))
            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)

    def _iter_files(self) -> Iterable[Path]:
        for pattern in self.patterns:
            iterator = self.root.rglob(pattern) if self.recursive else self.root.glob(pattern)
            for path in iterator:
                if path.is_file() and not any(part.startswith(".") for part in path.relative_to(self.root).parts):
                    yield path

    def _read_file(self, path: Path, request: FetchRequest, mtime: datetime) -> list[SourceDocument]:
        suffix = path.suffix.lower()
        try:
            if suffix in {".md", ".txt"}:
                return self._read_text_file(path, request, mtime)
            if suffix == ".json":
                return self._read_json_file(path, request, mtime)
            if suffix == ".jsonl":
                return self._read_jsonl_file(path, request, mtime)
            if suffix == ".csv":
                return self._read_csv_file(path, request, mtime)
        except Exception as exc:  # noqa: BLE001
            return [
                build_source_document(
                    source=self.name,
                    source_type="connector_error",
                    title=f"Failed to parse {path.name}",
                    content=str(exc),
                    path=str(path),
                    metadata={"error": True},
                )
            ]
        return []

    def _read_text_file(self, path: Path, request: FetchRequest, mtime: datetime) -> list[SourceDocument]:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        frontmatter, body = parse_frontmatter(raw) if path.suffix.lower() == ".md" else ({}, raw)
        body = normalize_text(body)
        title = str(frontmatter.get("title") or path.stem)
        source_type = str(frontmatter.get("source_type") or self.source_type)
        published = parse_datetime(frontmatter.get("published_at") or frontmatter.get("date")) or mtime
        full = f"{title}\n{body}"
        if not text_matches(full, request):
            return []
        return [
            build_source_document(
                source=self.name,
                source_type=source_type,
                title=title,
                content=body,
                path=str(path),
                published_at=published,
                metadata={"frontmatter": frontmatter, "file_mtime": mtime.isoformat()},
            )
        ]

    def _read_json_file(self, path: Path, request: FetchRequest, mtime: datetime) -> list[SourceDocument]:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        records = self._records_from_json(data)
        return self._records_to_docs(records, path, request, mtime)

    def _read_jsonl_file(self, path: Path, request: FetchRequest, mtime: datetime) -> list[SourceDocument]:
        records: list[Any] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"content": line})
        return self._records_to_docs(records, path, request, mtime)

    def _read_csv_file(self, path: Path, request: FetchRequest, mtime: datetime) -> list[SourceDocument]:
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(dict(row))
        return self._records_to_docs(records, path, request, mtime)

    def _records_from_json(self, data: Any) -> list[Any]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ["data", "items", "list", "records", "results", "comments", "notes"]:
                value = data.get(key)
                if isinstance(value, list):
                    return value
            return [data]
        return [{"content": str(data)}]

    def _records_to_docs(self, records: list[Any], path: Path, request: FetchRequest, mtime: datetime) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        for idx, record in enumerate(records):
            if not isinstance(record, dict):
                record = {"content": str(record)}
            title = self._first_value(record, TITLE_KEYS) or path.stem
            content = self._first_value(record, CONTENT_KEYS) or json.dumps(record, ensure_ascii=False)
            url = self._first_value(record, URL_KEYS)
            published = parse_datetime(self._first_value(record, TIME_KEYS)) or mtime
            full = f"{title}\n{content}\n{json.dumps(record, ensure_ascii=False)}"
            if not in_date_range(published, request.start_date, request.end_date):
                continue
            if not text_matches(full, request):
                continue
            docs.append(
                build_source_document(
                    source=self.name,
                    source_type=str(record.get("source_type") or self.source_type),
                    title=str(title),
                    content=normalize_text(str(content)),
                    url=str(url) if url else None,
                    path=f"{path}#{idx}",
                    published_at=published,
                    metadata={"record": record, "file": str(path), "file_mtime": mtime.isoformat()},
                )
            )
        return docs

    def _first_value(self, record: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return value
        return None
