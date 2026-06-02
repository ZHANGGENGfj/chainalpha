from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, in_date_range, parse_datetime, resolve_path, text_matches
from chainalpha.utils.text import normalize_text


TITLE_KEYS = ["title", "标题", "note_title", "desc", "description", "display_title", "nickname", "user_name", "keyword"]
CONTENT_KEYS = [
    "content",
    "内容",
    "note_content",
    "comment_content",
    "text",
    "desc",
    "description",
    "raw_text",
    "display_title",
    "article_content",
    "摘要",
    "summary",
]
TIME_KEYS = ["publish_time", "发布时间", "时间", "create_time", "created_at", "time", "timestamp", "last_modify_ts"]
URL_KEYS = ["url", "链接", "note_url", "source_url", "href", "web_url"]
SOURCE_KEYS = ["source", "来源", "platform", "媒体"]


class MediaCrawlerConnector(BaseConnector):
    """Read existing MediaCrawler-main output files.

    It does not launch the crawler. It only normalizes already-crawled JSON / JSONL
    files under directories such as:

    - MediaCrawler-main/data/xq/cache/*.json
    - MediaCrawler-main/data/xq/processed/*.json
    - MediaCrawler-main/data/cls/processed/*.json
    - MediaCrawler-main/data/finance_news/processed/*.json
    - MediaCrawler-main/data/claim_tasks/**/*.jsonl

    This keeps the ChainAlpha pipeline deterministic and avoids browser/session
    side effects inside the data-source layer.
    """

    connector_type = "media_crawler"

    def __init__(
        self,
        name: str,
        root_dir: str | Path,
        platforms: list[str] | None = None,
        modes: list[str] | None = None,
        keywords: list[str] | None = None,
        **params: Any,
    ) -> None:
        super().__init__(name, root_dir=str(root_dir), platforms=platforms, modes=modes, keywords=keywords, **params)
        self.root_dir = resolve_path(root_dir)
        self.platforms = platforms or ["finance_news", "cninfo_reports", "broker_reports", "claim_tasks", "xq", "cls"]
        self.modes = modes or ["processed", "raw", "cache"]
        self.keywords = keywords or []

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        if not self.root_dir.exists():
            return []
        merged_keywords = list(dict.fromkeys([*self.keywords, *request.keywords]))
        local_request = FetchRequest(
            query=request.query,
            keywords=merged_keywords,
            start_date=request.start_date,
            end_date=request.end_date,
            limit=request.limit,
            metadata=request.metadata,
        )
        docs: list[SourceDocument] = []
        for platform, file_path in self._iter_files():
            docs.extend(self._read_file(platform, file_path, local_request))
            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)

    def _iter_files(self) -> Iterable[tuple[str, Path]]:
        for platform in self.platforms:
            base = self.root_dir / platform
            if not base.exists():
                continue
            search_dirs = [base / mode for mode in self.modes if (base / mode).exists()]
            if not search_dirs:
                search_dirs = [base]
            for directory in search_dirs:
                for pattern in ("*.json", "*.jsonl"):
                    for path in sorted(directory.rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
                        if path.is_file():
                            yield platform, path

    def _read_file(self, platform: str, path: Path, request: FetchRequest) -> list[SourceDocument]:
        try:
            records = self._load_records(path)
        except Exception:
            return []
        docs: list[SourceDocument] = []
        source_type = _platform_source_type(platform)
        for idx, record in enumerate(records):
            if not isinstance(record, dict):
                record = {"content": str(record)}
            title = self._first(record, TITLE_KEYS) or self._infer_title_from_filename(path)
            content = self._first(record, CONTENT_KEYS)
            if not content or str(content) == str(title):
                content = self._compose_content(record)
            url = self._first(record, URL_KEYS)
            published = parse_datetime(self._first(record, TIME_KEYS))
            if not in_date_range(published, request.start_date, request.end_date):
                continue
            full = f"{title}\n{content}\n{json.dumps(record, ensure_ascii=False)}\n{path.name}"
            if not text_matches(full, request):
                continue
            docs.append(
                build_source_document(
                    source=f"media_crawler:{self._first(record, SOURCE_KEYS) or platform}",
                    source_type=source_type,
                    title=str(title),
                    content=normalize_text(str(content)),
                    url=str(url) if url else None,
                    path=f"{path}#{idx}",
                    published_at=published,
                    metadata={"platform": platform, "record": record, "file": str(path)},
                )
            )
        return docs

    def _load_records(self, path: Path) -> list[Any]:
        if path.suffix.lower() == ".jsonl":
            records = []
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip():
                    records.append(json.loads(line))
            return records
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ["data", "items", "list", "records", "results", "comments", "notes"]:
                value = data.get(key)
                if isinstance(value, list):
                    return value
            return [data]
        return [{"content": str(data)}]

    def _first(self, record: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return value
        return None

    def _compose_content(self, record: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ["摘要", "summary", "匹配关键词", "tags", "category", "source", "来源", "url", "链接"]:
            value = record.get(key)
            if value not in (None, ""):
                parts.append(f"{key}: {value}")
        if parts:
            return "\n".join(parts)
        return json.dumps(record, ensure_ascii=False)

    def _infer_title_from_filename(self, path: Path) -> str:
        stem = path.stem
        if "_" in stem:
            return stem.split("_", 1)[-1]
        return stem


def _platform_source_type(platform: str) -> str:
    if platform in {"xq", "dy", "ks", "bili", "wb"}:
        return "social_media"
    if platform == "cninfo_reports":
        return "official_announcement"
    if platform == "broker_reports":
        return "broker_research_report"
    return "mainstream_media"
