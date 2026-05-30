from __future__ import annotations

from pathlib import Path

from chainalpha.memory.markdown_parser import parse_markdown_file, chunk_document
from chainalpha.schemas import Document, Chunk


class ObsidianLoader:
    def __init__(self, vault_path: str | Path, ignore_dirs: set[str] | None = None) -> None:
        self.vault_path = Path(vault_path).expanduser().resolve()
        self.ignore_dirs = ignore_dirs or {".obsidian", ".git", "__pycache__", ".trash"}

    def _is_ignored(self, path: Path) -> bool:
        return any(part in self.ignore_dirs for part in path.parts)

    def load_documents(self) -> list[Document]:
        if not self.vault_path.exists():
            raise FileNotFoundError(f"Vault path does not exist: {self.vault_path}")
        docs: list[Document] = []
        for path in self.vault_path.rglob("*.md"):
            if self._is_ignored(path.relative_to(self.vault_path)):
                continue
            try:
                docs.append(parse_markdown_file(path, root=self.vault_path))
            except Exception as exc:
                print(f"[WARN] Failed to parse {path}: {exc}")
        return docs

    def load_chunks(self, max_chars: int = 1200) -> list[Chunk]:
        chunks: list[Chunk] = []
        for doc in self.load_documents():
            chunks.extend(chunk_document(doc, max_chars=max_chars))
        return chunks
