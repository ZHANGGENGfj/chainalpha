import json
from pathlib import Path
from datetime import datetime

from chainalpha.storage.models import StoredDocument
from chainalpha.storage.dedup import (
    build_url_hash,
    build_title_hash,
    build_content_hash,
)


class RawDocumentStore:
    """
    企业级 raw document storage.
    """

    def __init__(
        self,
        base_dir: str = "data/raw",
    ):
        self.base_dir = Path(base_dir)

        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_documents(
        self,
        documents: list,
        source: str,
    ) -> dict:
        """
        保存 documents.
        """

        now = datetime.utcnow()

        date_str = now.strftime("%Y-%m-%d")

        source_dir = self.base_dir / date_str / source

        source_dir.mkdir(parents=True, exist_ok=True)

        jsonl_path = source_dir / "documents.jsonl"

        dedup_index_path = source_dir / "dedup_index.json"

        dedup_index = self._load_dedup_index(
            dedup_index_path
        )

        saved_count = 0
        skipped_count = 0

        with open(
            jsonl_path,
            "a",
            encoding="utf-8",
        ) as f:

            for doc in documents:

                stored_doc = self._build_stored_document(
                    doc
                )

                is_duplicate = self._is_duplicate(
                    stored_doc,
                    dedup_index,
                )

                if is_duplicate:
                    skipped_count += 1
                    continue

                f.write(
                    json.dumps(
                        stored_doc.__dict__,
                        ensure_ascii=False,
                    )
                    + "\n"
                )

                self._update_dedup_index(
                    stored_doc,
                    dedup_index,
                )

                saved_count += 1

        self._save_dedup_index(
            dedup_index_path,
            dedup_index,
        )

        return {
            "saved": saved_count,
            "skipped": skipped_count,
            "path": str(jsonl_path),
        }

    def load_documents(
        self,
        path: str,
    ) -> list[dict]:

        documents = []

        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:

            for line in f:
                line = line.strip()

                if not line:
                    continue

                documents.append(json.loads(line))

        return documents

    def _build_stored_document(
        self,
        doc,
    ) -> StoredDocument:

        title = getattr(doc, "title", "") or ""

        content = getattr(doc, "content", "") or ""

        url = getattr(doc, "url", None)

        source = getattr(doc, "source", "unknown")

        source_type = getattr(
            doc,
            "source_type",
            "unknown",
        )

        metadata = getattr(doc, "metadata", {})

        published_at = getattr(
            doc,
            "published_at",
            None,
        )

        url_hash = build_url_hash(url)

        title_hash = build_title_hash(title)

        content_hash = build_content_hash(content)

        doc_id = content_hash[:16]

        return StoredDocument(
            doc_id=doc_id,
            title=title,
            content=content,
            url=url,
            source=source,
            source_type=source_type,
            metadata=metadata,
            published_at=published_at,
            url_hash=url_hash,
            title_hash=title_hash,
            content_hash=content_hash,
        )

    def _is_duplicate(
        self,
        doc: StoredDocument,
        dedup_index: dict,
    ) -> bool:

        if doc.url_hash:
            if doc.url_hash in dedup_index["url_hashes"]:
                return True

        if doc.title_hash in dedup_index["title_hashes"]:
            return True

        if (
            doc.content_hash
            in dedup_index["content_hashes"]
        ):
            return True

        return False

    def _update_dedup_index(
        self,
        doc: StoredDocument,
        dedup_index: dict,
    ):

        if doc.url_hash:
            dedup_index["url_hashes"].append(
                doc.url_hash
            )

        dedup_index["title_hashes"].append(
            doc.title_hash
        )

        dedup_index["content_hashes"].append(
            doc.content_hash
        )

    def _load_dedup_index(
        self,
        path: Path,
    ) -> dict:

        if not path.exists():

            return {
                "url_hashes": [],
                "title_hashes": [],
                "content_hashes": [],
            }

        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:

            return json.load(f)

    def _save_dedup_index(
        self,
        path: Path,
        index: dict,
    ):

        with open(
            path,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                index,
                f,
                ensure_ascii=False,
                indent=2,
            )