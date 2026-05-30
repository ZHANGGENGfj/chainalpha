from __future__ import annotations

from io import BytesIO
from pathlib import Path
import hashlib

from chainalpha.connectors.http_utils import http_get
from chainalpha.schemas import SourceDocument


def enrich_pdf_document(
    doc: SourceDocument,
    *,
    cache_dir: str | Path = "./data/cache/cninfo_pdfs",
    timeout: int = 30,
    max_chars: int = 120_000,
) -> SourceDocument:
    url = (doc.url or "").strip()
    if not url.lower().endswith(".pdf"):
        return doc

    cache_root = Path(cache_dir).expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    pdf_path = cache_root / f"{_doc_hash(url)}.pdf"

    if not pdf_path.exists():
        resp = http_get(
            url,
            timeout=timeout,
            headers={"Referer": "http://www.cninfo.com.cn/"},
        )
        pdf_path.write_bytes(resp.content)

    raw = pdf_path.read_bytes()
    text, page_count = _extract_pdf_text(raw, max_chars=max_chars)
    if not text.strip():
        return doc

    content_parts = [
        doc.content,
        "",
        "=== PDF TEXT ===",
        text,
    ]
    metadata = dict(doc.metadata)
    metadata.update(
        {
            "pdf_cached_path": str(pdf_path),
            "pdf_page_count": page_count,
            "pdf_text_extracted": True,
        }
    )
    return SourceDocument(
        id=doc.id,
        source=doc.source,
        source_type=doc.source_type,
        title=doc.title,
        content="\n".join(part for part in content_parts if part),
        url=doc.url,
        path=str(pdf_path),
        published_at=doc.published_at,
        fetched_at=doc.fetched_at,
        metadata=metadata,
    )


def _extract_pdf_text(raw: bytes, *, max_chars: int) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for A-share PDF parsing.") from exc

    reader = PdfReader(BytesIO(raw))
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        clean = _normalize_pdf_text(text)
        if not clean:
            continue
        parts.append(clean)
        total += len(clean)
        if total >= max_chars:
            break
    joined = "\n\n".join(parts)
    return joined[:max_chars], len(reader.pages)


def _normalize_pdf_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    compact = "\n".join(line for line in lines if line)
    return compact.strip()


def _doc_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
