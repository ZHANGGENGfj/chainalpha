from __future__ import annotations

from datetime import datetime
from pathlib import Path


def file_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
