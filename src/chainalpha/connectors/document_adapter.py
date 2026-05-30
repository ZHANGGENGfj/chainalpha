from __future__ import annotations

from chainalpha.memory.markdown_parser import chunk_document
from chainalpha.schemas import Chunk, Document, SourceDocument, stable_id
from chainalpha.utils.text import normalize_text


def source_document_to_document(src: SourceDocument) -> Document:
    path = src.display_path()
    return Document(
        id=src.id,
        path=path,
        title=src.title,
        content=normalize_text(src.content),
        source=src.source,
        source_type=src.source_type,
        created_at=src.published_at,
        updated_at=src.fetched_at,
        frontmatter={
            "source": src.source,
            "source_type": src.source_type,
            "url": src.url,
            "path": src.path,
            "published_at": src.published_at.isoformat() if src.published_at else None,
            **(src.metadata or {}),
        },
        tags=[str(t).strip("#") for t in src.metadata.get("tags", [])] if isinstance(src.metadata.get("tags"), list) else [],
        wikilinks=[str(w) for w in src.metadata.get("wikilinks", [])] if isinstance(src.metadata.get("wikilinks"), list) else [],
        headings=[],
    )


def source_documents_to_chunks(docs: list[SourceDocument], max_chars: int = 1200) -> list[Chunk]:
    chunks: list[Chunk] = []
    for src in docs:
        doc = source_document_to_document(src)
        doc_chunks = chunk_document(doc, max_chars=max_chars)
        if not doc_chunks and src.content.strip():
            doc_chunks = [
                Chunk(
                    id=stable_id(src.id, "0"),
                    document_id=src.id,
                    path=src.display_path(),
                    title=src.title,
                    text=src.content[:max_chars],
                    source=src.source,
                    source_type=src.source_type,
                )
            ]
        chunks.extend(doc_chunks)
    return chunks
