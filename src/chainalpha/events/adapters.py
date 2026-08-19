from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Iterable
from urllib.parse import urlparse

from chainalpha.events.models import NormalizedCandidate


logger = logging.getLogger("chainalpha.events.adapters")
CHINA_TZ = timezone(timedelta(hours=8))
TITLE_KEYS = ("title", "标题", "note_title", "display_title", "announcementTitle")
SUMMARY_KEYS = ("summary", "content", "摘要", "description", "article_content", "announcementContent")
URL_KEYS = ("url", "链接", "source_url", "href", "pdf_url")
PUBLISHED_KEYS = (
    "published_at",
    "publish_time",
    "发布时间",
    "公告日期",
    "create_time",
    "time",
)
PUBLISHER_KEYS = ("source", "来源", "publisher", "platform")
ENTITY_KEYS = ("company", "公司", "ticker", "代码")
INVALID_NEWS_MATCH_TYPES = {"编程副业", "编程兼职"}


class SourceAdapter(ABC):
    source_type = "unknown"
    platform = ""

    def __init__(self, root: Path) -> None:
        self.root = root

    @abstractmethod
    def iter_candidates(self) -> Iterable[NormalizedCandidate]:
        raise NotImplementedError

    def _iter_records(self) -> Iterable[tuple[dict[str, Any], Path, int]]:
        base = self.root / self.platform
        if not base.exists():
            return
        files = [
            *base.glob("processed/**/*.jsonl"),
            *base.glob("processed/**/*.json"),
        ]
        for path in sorted(set(files), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                records = _load_records(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for index, record in enumerate(records):
                if isinstance(record, dict):
                    yield record, path, index

    def _normalize(
        self,
        record: dict[str, Any],
        path: Path,
        index: int,
    ) -> NormalizedCandidate | None:
        title = repair_mojibake(str(_first(record, TITLE_KEYS) or "")).strip()
        summary = repair_mojibake(str(_first(record, SUMMARY_KEYS) or "")).strip()
        if not title:
            return None
        title, title_time = _extract_trailing_datetime(title)
        summary, summary_time = _extract_trailing_datetime(summary)
        if not summary:
            summary = title
        publisher = repair_mojibake(
            str(_first(record, PUBLISHER_KEYS) or self.platform)
        ).strip()
        url = _safe_url(_first(record, URL_KEYS))
        published = _parse_datetime(_first(record, PUBLISHED_KEYS)) or title_time or summary_time
        retrieved = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        entities = _unique(
            repair_mojibake(_strip_html(str(record.get(key) or ""))).strip()
            for key in ENTITY_KEYS
        )
        sectors, concepts = infer_sectors_and_concepts(f"{title} {summary}")
        digest_payload = f"{url or ''}|{normalize_title(title)}|{summary[:1000]}"
        content_hash = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
        source_id = str(record.get("id") or f"{self.platform}-{content_hash[:18]}-{index}")
        return NormalizedCandidate(
            source_id=source_id,
            source_type=self.source_type,
            title=title[:300],
            summary=_clean_text(summary)[:1200],
            publisher=publisher or self.platform,
            published_at=published,
            retrieved_at=retrieved,
            url=url,
            entities=entities[:12],
            sectors=sectors,
            concepts=concepts,
            raw_metadata={
                "platform": self.platform,
                "file": str(path),
                "time_missing": published is None,
                "match_type": repair_mojibake(str(record.get("match_type") or "")),
            },
            content_hash=content_hash,
            is_primary_source=self.source_type == "announcement",
            authority_score=_authority_score(self.source_type, publisher),
        )


class NewsSourceAdapter(SourceAdapter):
    source_type = "news"
    platform = "finance_news"

    def iter_candidates(self) -> Iterable[NormalizedCandidate]:
        for record, path, index in self._iter_records():
            match_type = repair_mojibake(str(record.get("match_type") or "")).strip()
            if match_type in INVALID_NEWS_MATCH_TYPES:
                continue
            candidate = self._normalize(record, path, index)
            if candidate:
                yield candidate


class AnnouncementSourceAdapter(SourceAdapter):
    source_type = "announcement"
    platform = "cninfo_reports"

    def iter_candidates(self) -> Iterable[NormalizedCandidate]:
        for record, path, index in self._iter_records():
            candidate = self._normalize(record, path, index)
            if candidate:
                yield candidate


class ResearchSourceAdapter(SourceAdapter):
    source_type = "research"
    platform = "broker_reports"

    def iter_candidates(self) -> Iterable[NormalizedCandidate]:
        for record, path, index in self._iter_records():
            candidate = self._normalize(record, path, index)
            if candidate:
                yield candidate


def load_existing_candidates(
    root: Path,
) -> tuple[list[NormalizedCandidate], dict[str, datetime | None]]:
    candidates: list[NormalizedCandidate] = []
    freshness: dict[str, datetime | None] = {
        "news_updated_at": None,
        "announcements_updated_at": None,
        "reports_updated_at": None,
    }
    adapters = [
        (NewsSourceAdapter(root), "news_updated_at"),
        (AnnouncementSourceAdapter(root), "announcements_updated_at"),
        (ResearchSourceAdapter(root), "reports_updated_at"),
    ]
    for adapter, freshness_key in adapters:
        started = time.perf_counter()
        try:
            rows = list(adapter.iter_candidates())
        except Exception as exc:
            logger.exception(
                "event_adapter_failed",
                extra={
                    "adapter_name": type(adapter).__name__,
                    "processing_stage": "normalize",
                    "status": "error",
                    "error_type": type(exc).__name__,
                },
            )
            rows = []
        candidates.extend(rows)
        timestamps = [row.retrieved_at for row in rows]
        freshness[freshness_key] = max(timestamps) if timestamps else None
        logger.info(
            "event_adapter_completed",
            extra={
                "adapter_name": type(adapter).__name__,
                "processing_stage": "normalize",
                "status": "ok",
                "candidate_count": len(rows),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
    return candidates, freshness


SECTOR_RULES = {
    "AI算力": ("AI", "算力", "GPU", "数据中心", "服务器"),
    "半导体": ("芯片", "半导体", "封测", "晶圆", "存储"),
    "光通信": ("光模块", "光通信", "CPO", "硅光"),
    "机器人": ("机器人", "减速器", "灵巧手"),
    "新能源电池": ("电池", "锂电", "固态电池", "集流体"),
    "汽车": ("汽车", "新能源车", "智能驾驶"),
    "医药": ("医药", "创新药", "临床", "授权"),
    "房地产": ("房地产", "地产", "住房", "公积金"),
    "消费电子": ("消费电子", "手机", "终端", "可穿戴"),
    "有色金属": ("铜价", "黄金", "铝", "稀土"),
    "金融": ("银行", "证券", "保险", "券商"),
    "能源": ("石油", "天然气", "煤炭", "电力"),
}


def infer_sectors_and_concepts(text: str) -> tuple[list[str], list[str]]:
    lowered = text.lower()
    sectors = [
        sector
        for sector, terms in SECTOR_RULES.items()
        if any(term.lower() in lowered for term in terms)
    ]
    concepts: list[str] = []
    for terms in SECTOR_RULES.values():
        concepts.extend(term for term in terms if term.lower() in lowered)
    return sectors[:6], _unique(concepts)[:8]


def normalize_title(value: str) -> str:
    value = repair_mojibake(value).lower()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def repair_mojibake(value: str) -> str:
    if not value:
        return value
    try:
        repaired = value.encode("gb18030").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if _chinese_quality(repaired) > _chinese_quality(value) else value


def _chinese_quality(value: str) -> int:
    markers = ("娑", "閺", "缁", "閿", "閳", "鍏", "璇", "绗", "涓", "鏃", "檯")
    penalties = sum(value.count(marker) for marker in markers)
    penalties += len(re.findall(r"[\ue000-\uf8ff]", value)) * 2
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    return chinese - penalties * 3


def _load_records(path: Path) -> list[Any]:
    if path.suffix.lower() == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        ]
    payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "records", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return []


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or CHINA_TZ)


def _extract_trailing_datetime(value: str) -> tuple[str, datetime | None]:
    """Extract source timestamps appended to titles, such as NBD search results."""
    text = _clean_text(value)
    match = re.search(
        r"(?:\s+|^)(?P<stamp>20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)\s*$",
        text,
    )
    if not match:
        return text, None
    parsed = _parse_datetime(match.group("stamp"))
    if parsed is None:
        return text, None
    cleaned = text[: match.start()].rstrip(" -|·")
    return cleaned or text, parsed


def _safe_url(value: Any) -> str | None:
    if not value:
        return None
    url = str(value).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _authority_score(source_type: str, publisher: str) -> float:
    if source_type == "announcement":
        return 95
    if source_type == "research":
        return 72
    if any(
        name in publisher
        for name in ("证券时报", "证券日报", "中国证券报", "上海证券报", "第一财经", "每日经济新闻")
    ):
        return 82
    if "东方财富" in publisher:
        return 76
    if "今日头条" in publisher:
        return 58
    return 62


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", _strip_html(value)).strip()


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
