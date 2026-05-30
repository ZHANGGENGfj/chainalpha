from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class WatchItem:
    company: str
    ticker: str | None = None
    thesis: str = ""
    status: str = "watch"
    score: float | None = None
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class ReviewItem:
    company: str
    date: str
    outcome: str
    notes: str = ""
    score_after: float | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class WatchlistStore:
    def __init__(self, base_dir: str | Path = "data/watchlist") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.watchlist_path = self.base_dir / "watchlist.json"
        self.reviews_path = self.base_dir / "reviews.jsonl"

    def add_or_update(self, item: WatchItem) -> WatchItem:
        items = self.list_items()
        kept = [old for old in items if _key(old.company, old.ticker) != _key(item.company, item.ticker)]
        item.updated_at = datetime.now().isoformat(timespec="seconds")
        kept.append(item)
        kept.sort(key=lambda row: (row.status, row.company))
        self.watchlist_path.write_text(
            json.dumps([asdict(row) for row in kept], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return item

    def list_items(self) -> list[WatchItem]:
        if not self.watchlist_path.exists():
            return []
        data = json.loads(self.watchlist_path.read_text(encoding="utf-8"))
        return [WatchItem(**row) for row in data]

    def add_review(self, review: ReviewItem) -> ReviewItem:
        with self.reviews_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(review), ensure_ascii=False) + "\n")
        return review

    def list_reviews(self) -> list[ReviewItem]:
        if not self.reviews_path.exists():
            return []
        reviews: list[ReviewItem] = []
        with self.reviews_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    reviews.append(ReviewItem(**json.loads(line)))
        return reviews


def _key(company: str, ticker: str | None) -> str:
    return (ticker or company).strip().lower()
