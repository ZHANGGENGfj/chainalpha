from __future__ import annotations

from pathlib import Path
from typing import Any

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, in_date_range, resolve_path, text_matches
from chainalpha.memory.obsidian_loader import ObsidianLoader


class ObsidianConnector(BaseConnector):
    """Fetch notes from an Obsidian-compatible Markdown vault."""

    connector_type = "obsidian"

    def __init__(self, name: str, path: str | Path, **params: Any) -> None:
        super().__init__(name, path=str(path), **params)
        self.vault_path = resolve_path(path)

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        loader = ObsidianLoader(self.vault_path)
        docs: list[SourceDocument] = []
        for doc in loader.load_documents():
            published = doc.updated_at or doc.created_at
            if not in_date_range(published, request.start_date, request.end_date):
                continue
            full = "\n".join([doc.title, doc.content, " ".join(doc.tags), " ".join(doc.wikilinks)])
            if not text_matches(full, request):
                continue
            docs.append(
                build_source_document(
                    source=doc.source or self.name,
                    source_type=doc.source_type or "obsidian_note",
                    title=doc.title,
                    content=doc.content,
                    path=doc.path,
                    published_at=published,
                    metadata={
                        "frontmatter": doc.frontmatter,
                        "tags": doc.tags,
                        "wikilinks": doc.wikilinks,
                        "headings": doc.headings,
                        "document_id": doc.id,
                    },
                )
            )
        return apply_limit(docs, request.limit)
