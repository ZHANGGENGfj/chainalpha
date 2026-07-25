from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any

from chainalpha.schemas import Chunk, SourceDocument


@dataclass
class RetrievalSelection:
    documents: list[SourceDocument]
    backend: str
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def select_documents_with_llamaindex(
    *,
    query: str,
    chunks: list[Chunk],
    docs: list[SourceDocument],
    top_k_docs: int,
) -> RetrievalSelection:
    """Select source documents with LlamaIndex when the optional deps exist.

    This adapter intentionally starts with LlamaIndex BM25 so the project can
    adopt LlamaIndex without silently calling a remote embedding API. Vector
    retrieval can be enabled later by swapping this backend for Qdrant/OpenSearch
    + BGE embeddings.
    """

    doc_by_id = {doc.id: doc for doc in docs}
    if not chunks:
        return RetrievalSelection(
            documents=docs[:top_k_docs],
            backend="llamaindex_empty_fallback",
            warnings=["LlamaIndex retrieval skipped because there are no chunks."],
        )

    mode = os.getenv("CHAINALPHA_LLAMA_INDEX_MODE", "bm25").strip().lower() or "bm25"
    if mode not in {"bm25"}:
        return RetrievalSelection(
            documents=[],
            backend="llamaindex_unsupported",
            warnings=[f"Unsupported CHAINALPHA_LLAMA_INDEX_MODE={mode!r}; use bm25 for now."],
        )

    try:
        from llama_index.core.schema import TextNode  # type: ignore
    except ImportError as exc:
        return RetrievalSelection(
            documents=[],
            backend="llamaindex_unavailable",
            warnings=[f"LlamaIndex is not installed: {exc.__class__.__name__}: {exc}"],
        )

    try:
        from llama_index.retrievers.bm25 import BM25Retriever  # type: ignore
    except ImportError as exc:
        return RetrievalSelection(
            documents=[],
            backend="llamaindex_bm25_unavailable",
            warnings=[
                "LlamaIndex is installed, but BM25 retriever is missing. "
                "Install llama-index-retrievers-bm25.",
                f"{exc.__class__.__name__}: {exc}",
            ],
        )

    nodes = [
        TextNode(
            id_=chunk.id,
            text=_chunk_text(chunk),
            metadata={
                "document_id": chunk.document_id,
                "title": chunk.title,
                "path": chunk.path,
                "source": chunk.source,
                "source_type": chunk.source_type,
            },
        )
        for chunk in chunks
        if chunk.text.strip()
    ]
    if not nodes:
        return RetrievalSelection(
            documents=docs[:top_k_docs],
            backend="llamaindex_empty_nodes_fallback",
            warnings=["LlamaIndex retrieval skipped because all chunks are empty."],
        )

    try:
        retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=min(len(nodes), top_k_docs * 4))
        hits = retriever.retrieve(query)
    except Exception as exc:
        return RetrievalSelection(
            documents=[],
            backend="llamaindex_error",
            warnings=[f"LlamaIndex BM25 retrieval failed: {exc.__class__.__name__}: {exc}"],
        )

    selected: list[SourceDocument] = []
    seen: set[str] = set()
    hit_count = 0
    for hit in hits:
        hit_count += 1
        node = getattr(hit, "node", None)
        metadata = getattr(node, "metadata", {}) if node is not None else {}
        doc_id = str(metadata.get("document_id") or "")
        if not doc_id or doc_id in seen:
            continue
        doc = doc_by_id.get(doc_id)
        if not doc:
            continue
        seen.add(doc_id)
        selected.append(doc)
        if len(selected) >= top_k_docs:
            break

    return RetrievalSelection(
        documents=selected,
        backend="llamaindex_bm25",
        warnings=[] if selected else ["LlamaIndex BM25 returned no matching documents."],
        metadata={
            "mode": mode,
            "chunks": len(chunks),
            "nodes": len(nodes),
            "hits": hit_count,
            "selected_documents": len(selected),
        },
    )


def _chunk_text(chunk: Chunk) -> str:
    heading = f"\n{chunk.heading}" if chunk.heading else ""
    tags = f"\nTags: {', '.join(chunk.tags)}" if chunk.tags else ""
    return f"{chunk.title}{heading}\n{chunk.text}{tags}".strip()

