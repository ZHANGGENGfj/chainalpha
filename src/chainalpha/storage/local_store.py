from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def sha256_text(text: str | None) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def safe_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "model_dump"):
        return to_jsonable(obj.model_dump())
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return to_jsonable(obj.item())
        except Exception:
            pass
    if not isinstance(obj, (str, bytes)) and hasattr(obj, "isoformat") and callable(obj.isoformat):
        try:
            return obj.isoformat()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return to_jsonable({
            k: v
            for k, v in obj.__dict__.items()
            if not k.startswith("_")
        })
    return obj


class JsonlStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append_many(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0

        with self.path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(to_jsonable(row), ensure_ascii=False) + "\n")

        return len(rows)

    def load_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows


class DedupIndex:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.index = self._load()

    def _load(self) -> dict[str, set[str]]:
        if not self.path.exists():
            return {
                "url_hashes": set(),
                "title_hashes": set(),
                "content_hashes": set(),
                "chunk_hashes": set(),
                "event_hashes": set(),
                "signal_hashes": set(),
            }

        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {k: set(v) for k, v in data.items()}

    def save(self) -> None:
        data = {k: sorted(v) for k, v in self.index.items()}
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def exists(self, bucket: str, value: str) -> bool:
        if not value:
            return False
        return value in self.index.setdefault(bucket, set())

    def add(self, bucket: str, value: str) -> None:
        if value:
            self.index.setdefault(bucket, set()).add(value)


class LocalResearchStore:
    """
    ChainAlpha 本地研究数据仓库。

    保存结构：

    data/
    ├── raw/YYYY-MM-DD/source/documents.jsonl
    ├── processed/YYYY-MM-DD/chunks.jsonl
    ├── events/YYYY-MM-DD/events.jsonl
    ├── signals/YYYY-MM-DD/signals.jsonl
    └── indexes/dedup.json
    """

    def __init__(self, base_dir: str | Path = "data"):
        self.base_dir = Path(base_dir)
        self.raw_dir = self.base_dir / "raw"
        self.processed_dir = self.base_dir / "processed"
        self.events_dir = self.base_dir / "events"
        self.signals_dir = self.base_dir / "signals"
        self.index_dir = self.base_dir / "indexes"

        for d in [
            self.raw_dir,
            self.processed_dir,
            self.events_dir,
            self.signals_dir,
            self.index_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

        self.dedup = DedupIndex(self.index_dir / "dedup.json")

    def save_raw_documents(
        self,
        documents: list[Any],
        source_name: str = "unknown",
        run_date: str | None = None,
    ) -> tuple[list[Any], dict[str, Any]]:
        """
        保存 raw documents，并返回去重后的新文档。

        去重策略：
        1. url_hash
        2. content_hash
        3. title_hash
        """

        run_date = run_date or datetime.now().strftime("%Y-%m-%d")

        out_path = (
            self.raw_dir
            / run_date
            / source_name
            / "documents.jsonl"
        )

        rows: list[dict[str, Any]] = []
        new_documents: list[Any] = []
        skipped = 0

        for doc in documents:
            row = self._raw_document_to_row(doc, source_name)

            url_hash = row["url_hash"]
            title_hash = row["title_hash"]
            content_hash = row["content_hash"]

            duplicate = False

            if url_hash and self.dedup.exists("url_hashes", url_hash):
                duplicate = True

            if content_hash and self.dedup.exists("content_hashes", content_hash):
                duplicate = True

            if title_hash and self.dedup.exists("title_hashes", title_hash):
                duplicate = True

            if duplicate:
                skipped += 1
                continue

            self.dedup.add("url_hashes", url_hash)
            self.dedup.add("title_hashes", title_hash)
            self.dedup.add("content_hashes", content_hash)

            rows.append(row)
            new_documents.append(doc)

        JsonlStore(out_path).append_many(rows)
        self.dedup.save()

        return new_documents, {
            "source": source_name,
            "saved": len(rows),
            "skipped": skipped,
            "path": str(out_path),
        }

    def save_processed_chunks(
        self,
        chunks: list[Any],
        run_date: str | None = None,
    ) -> tuple[list[Any], dict[str, Any]]:
        run_date = run_date or datetime.now().strftime("%Y-%m-%d")

        out_path = self.processed_dir / run_date / "chunks.jsonl"

        rows: list[dict[str, Any]] = []
        new_chunks: list[Any] = []
        skipped = 0

        for chunk in chunks:
            row = self._chunk_to_row(chunk)
            chunk_hash = row["chunk_hash"]

            if self.dedup.exists("chunk_hashes", chunk_hash):
                skipped += 1
                continue

            self.dedup.add("chunk_hashes", chunk_hash)
            rows.append(row)
            new_chunks.append(chunk)

        JsonlStore(out_path).append_many(rows)
        self.dedup.save()

        return new_chunks, {
            "saved": len(rows),
            "skipped": skipped,
            "path": str(out_path),
        }

    def save_events(
        self,
        events: list[Any],
        run_date: str | None = None,
    ) -> dict[str, Any]:
        run_date = run_date or datetime.now().strftime("%Y-%m-%d")

        out_path = self.events_dir / run_date / "events.jsonl"

        rows: list[dict[str, Any]] = []
        skipped = 0

        for event in events:
            row = to_jsonable(event)
            event_hash = self._event_hash(row)

            if self.dedup.exists("event_hashes", event_hash):
                skipped += 1
                continue

            self.dedup.add("event_hashes", event_hash)
            row["_stored_at"] = utc_now()
            row["_event_hash"] = event_hash
            rows.append(row)

        JsonlStore(out_path).append_many(rows)
        self.dedup.save()

        return {
            "saved": len(rows),
            "skipped": skipped,
            "path": str(out_path),
        }

    def save_signals(
        self,
        signals: list[Any],
        run_date: str | None = None,
    ) -> dict[str, Any]:
        run_date = run_date or datetime.now().strftime("%Y-%m-%d")

        out_path = self.signals_dir / run_date / "signals.jsonl"

        rows: list[dict[str, Any]] = []
        skipped = 0

        for signal in signals:
            row = to_jsonable(signal)
            signal_hash = self._signal_hash(row)

            if self.dedup.exists("signal_hashes", signal_hash):
                skipped += 1
                continue

            self.dedup.add("signal_hashes", signal_hash)
            row["_stored_at"] = utc_now()
            row["_signal_hash"] = signal_hash
            rows.append(row)

        JsonlStore(out_path).append_many(rows)
        self.dedup.save()

        return {
            "saved": len(rows),
            "skipped": skipped,
            "path": str(out_path),
        }

    def _raw_document_to_row(
        self,
        doc: Any,
        source_name: str,
    ) -> dict[str, Any]:
        title = safe_get(doc, "title", "") or ""
        content = safe_get(doc, "content", "") or ""
        url = safe_get(doc, "url", None)
        source = safe_get(doc, "source", source_name) or source_name
        source_type = safe_get(doc, "source_type", "unknown") or "unknown"
        published_at = safe_get(doc, "published_at", None)
        metadata = to_jsonable(safe_get(doc, "metadata", {}) or {})

        url_hash = sha256_text(url) if url else ""
        title_hash = sha256_text(title)
        content_hash = sha256_text(content[:8000])

        doc_id = content_hash[:20]

        return {
            "doc_id": doc_id,
            "title": title,
            "content": content,
            "url": url,
            "source": source,
            "source_name": source_name,
            "source_type": source_type,
            "published_at": str(published_at) if published_at else None,
            "fetched_at": utc_now(),
            "metadata": metadata,
            "url_hash": url_hash,
            "title_hash": title_hash,
            "content_hash": content_hash,
        }

    def _chunk_to_row(self, chunk: Any) -> dict[str, Any]:
        data = to_jsonable(chunk)

        text = (
            data.get("text")
            or data.get("content")
            or data.get("chunk_text")
            or ""
        )

        source_path = (
            data.get("source_path")
            or data.get("source")
            or data.get("url")
            or ""
        )

        chunk_hash = sha256_text(source_path + "\n" + text[:8000])

        data["_stored_at"] = utc_now()
        data["_chunk_hash"] = chunk_hash
        data["chunk_hash"] = chunk_hash

        return data

    def _event_hash(self, event_row: dict[str, Any]) -> str:
        text = json.dumps(to_jsonable(event_row), ensure_ascii=False, sort_keys=True)
        return sha256_text(text[:12000])

    def _signal_hash(self, signal_row: dict[str, Any]) -> str:
        text = json.dumps(to_jsonable(signal_row), ensure_ascii=False, sort_keys=True)
        return sha256_text(text[:12000])
