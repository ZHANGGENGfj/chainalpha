from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class StoredDocument:
    doc_id: str

    title: str
    content: str

    url: str | None = None

    source: str = "unknown"
    source_type: str = "unknown"

    published_at: str | None = None

    fetched_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )

    metadata: dict[str, Any] = field(default_factory=dict)

    url_hash: str = ""
    title_hash: str = ""
    content_hash: str = ""