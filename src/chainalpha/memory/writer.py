from __future__ import annotations

from pathlib import Path
from datetime import datetime


class ObsidianWriter:
    def __init__(self, vault_path: str | Path) -> None:
        self.vault_path = Path(vault_path).expanduser().resolve()

    def write_inbox_note(self, title: str, body: str, note_type: str = "ai_draft") -> Path:
        inbox = self.vault_path / "00_Inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in title).strip()
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_title}.md"
        path = inbox / filename
        content = f"""---
type: {note_type}
status: draft
created_by: chainalpha
created_at: {datetime.now().isoformat(timespec="seconds")}
need_review: true
---

# {title}

{body}
"""
        path.write_text(content, encoding="utf-8")
        return path
