from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from chainalpha.schemas import Document, stable_id, Chunk
from chainalpha.utils.text import normalize_text
from chainalpha.utils.time import file_mtime


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
TAG_RE = re.compile(r"(?<!\w)#([\w\u4e00-\u9fff/-]+)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    try:
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    return data, text[match.end():]


def extract_title(path: Path, body: str, frontmatter: dict[str, Any]) -> str:
    if isinstance(frontmatter.get("title"), str):
        return frontmatter["title"].strip()
    m = HEADING_RE.search(body)
    if m:
        return m.group(2).strip()
    return path.stem


def parse_markdown_file(path: Path, root: Path | None = None) -> Document:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = parse_frontmatter(raw)
    body = normalize_text(body)
    title = extract_title(path, body, frontmatter)
    tags = sorted(set(TAG_RE.findall(body)))
    fm_tags = frontmatter.get("tags")
    if isinstance(fm_tags, list):
        tags = sorted(set(tags + [str(t).strip("#") for t in fm_tags]))
    elif isinstance(fm_tags, str):
        tags = sorted(set(tags + [t.strip("#") for t in re.split(r"[, ]+", fm_tags) if t]))

    wikilinks = sorted(set(w.strip() for w in WIKILINK_RE.findall(body)))
    headings = [h.strip() for _, h in HEADING_RE.findall(body)]
    rel_path = str(path.relative_to(root)) if root else str(path)
    doc_id = stable_id(rel_path, title, str(path.stat().st_mtime))

    source_type = str(frontmatter.get("source_type") or "obsidian_note")
    source = str(frontmatter.get("source") or "obsidian")
    return Document(
        id=doc_id,
        path=rel_path,
        title=title,
        content=body,
        source=source,
        source_type=source_type,
        updated_at=file_mtime(path),
        frontmatter=frontmatter,
        tags=tags,
        wikilinks=wikilinks,
        headings=headings,
    )


def chunk_document(doc: Document, max_chars: int = 1200, overlap: int = 120) -> list[Chunk]:
    """
    Split by headings first, then by character window.
    """
    sections: list[tuple[str | None, str]] = []
    matches = list(HEADING_RE.finditer(doc.content))
    if not matches:
        sections = [(None, doc.content)]
    else:
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(doc.content)
            heading = m.group(2).strip()
            text = doc.content[start:end].strip()
            if text:
                sections.append((heading, text))

    chunks: list[Chunk] = []
    idx = 0
    for heading, text in sections:
        if len(text) <= max_chars:
            chunks.append(Chunk(
                id=stable_id(doc.id, str(idx), heading or ""),
                document_id=doc.id,
                path=doc.path,
                title=doc.title,
                text=text,
                heading=heading,
                tags=doc.tags,
                wikilinks=doc.wikilinks,
                source=doc.source,
                source_type=doc.source_type,
            ))
            idx += 1
            continue

        start = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            part = text[start:end].strip()
            if part:
                chunks.append(Chunk(
                    id=stable_id(doc.id, str(idx), heading or ""),
                    document_id=doc.id,
                    path=doc.path,
                    title=doc.title,
                    text=part,
                    heading=heading,
                    tags=doc.tags,
                    wikilinks=doc.wikilinks,
                    source=doc.source,
                    source_type=doc.source_type,
                ))
                idx += 1
            if end == len(text):
                break
            start = max(0, end - overlap)
    return chunks
