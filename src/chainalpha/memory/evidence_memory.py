from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from chainalpha.connectors.base import SourceDocument, build_source_document, parse_datetime


class ResearchEvidenceMemory:
    """Point-in-time evidence memory; stores sources, never model conclusions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def remember(self, evidence: list[dict[str, Any]], *, entity: str) -> int:
        written = 0
        now = datetime.now().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            for item in evidence:
                title = str(item.get("title") or "").strip()
                excerpt = str(item.get("content_excerpt") or item.get("content") or "").strip()
                url = str(item.get("url") or "").strip()
                if not title and not excerpt:
                    continue
                memory_id = hashlib.sha256(
                    f"{entity}|{url}|{title}|{excerpt[:300]}".encode("utf-8")
                ).hexdigest()[:32]
                conn.execute(
                    """
                    INSERT INTO evidence (
                        id, entity, title, source, source_type, url, published_at,
                        as_of_date, content_excerpt, metadata_json, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET last_seen_at=excluded.last_seen_at
                    """,
                    (
                        memory_id,
                        entity,
                        title,
                        str(item.get("source") or ""),
                        str(item.get("source_type") or ""),
                        url,
                        str(item.get("published_at") or ""),
                        str(item.get("as_of_date") or item.get("published_at") or ""),
                        excerpt[:4000],
                        json.dumps(item.get("metadata") or {}, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                written += 1
            conn.commit()
        finally:
            conn.close()
        return written

    def recall(self, query: str, *, entity: str = "", limit: int = 8) -> list[SourceDocument]:
        terms = [term for term in query.replace("，", " ").split() if len(term) >= 2][:8]
        clauses = []
        params: list[Any] = []
        if entity:
            clauses.append("entity = ?")
            params.append(entity)
        if terms:
            term_clauses = []
            for term in terms:
                term_clauses.append("(title LIKE ? OR content_excerpt LIKE ?)")
                params.extend([f"%{term}%", f"%{term}%"])
            clauses.append("(" + " OR ".join(term_clauses) + ")")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, int(limit)))
        sql = (
            "SELECT * FROM evidence"
            + where
            + " ORDER BY COALESCE(published_at, last_seen_at) DESC LIMIT ?"
        )
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        docs: list[SourceDocument] = []
        for row in rows:
            docs.append(build_source_document(
                source=f"research_memory:{row['source'] or 'unknown'}",
                source_type=row["source_type"] or "research_memory",
                title=row["title"],
                content=row["content_excerpt"],
                url=row["url"] or None,
                published_at=parse_datetime(row["published_at"]),
                metadata={
                    "memory_id": row["id"],
                    "entity": row["entity"],
                    "as_of_date": row["as_of_date"],
                    "memory_kind": "observed_evidence",
                },
            ))
        return docs

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    entity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT,
                    source_type TEXT,
                    url TEXT,
                    published_at TEXT,
                    as_of_date TEXT,
                    content_excerpt TEXT NOT NULL,
                    metadata_json TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_entity ON evidence(entity)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_published ON evidence(published_at)")
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn
