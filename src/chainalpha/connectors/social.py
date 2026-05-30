from __future__ import annotations

from typing import Any

from chainalpha.connectors.base import BaseConnector, FetchRequest, SourceDocument, apply_limit, build_source_document, parse_datetime, text_matches
from chainalpha.connectors.http_utils import best_effort_json, http_get


class RedditConnector(BaseConnector):
    """Public Reddit JSON connector for market attention monitoring."""

    connector_type = "reddit"

    def __init__(self, name: str, subreddit: str | None = None, sort: str = "new", time: str = "month", timeout: int = 20, **params: Any) -> None:
        super().__init__(name, subreddit=subreddit, sort=sort, time=time, **params)
        self.subreddit = subreddit
        self.sort = sort
        self.time = time
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        terms = request.terms or [request.query or "AI"]
        for term in terms:
            base = f"https://www.reddit.com/r/{self.subreddit}/search.json" if self.subreddit else "https://www.reddit.com/search.json"
            params = {"q": term, "sort": self.sort, "t": self.time, "limit": min(request.limit or 50, 100), "restrict_sr": "1" if self.subreddit else "0"}
            data = best_effort_json(http_get(base, params=params, timeout=self.timeout, headers={"User-Agent": "ChainAlpha/0.1 research"}, sleep_seconds=0.5))
            for child in data.get("data", {}).get("children", []) or []:
                item = child.get("data", {})
                doc = self._post_to_doc(item, request)
                if doc:
                    docs.append(doc)
            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)

    def _post_to_doc(self, item: dict[str, Any], request: FetchRequest) -> SourceDocument | None:
        title = str(item.get("title") or "Reddit post")
        body = str(item.get("selftext") or "")
        content = "\n".join([
            f"Subreddit: {item.get('subreddit', '')}",
            f"Author: {item.get('author', '')}",
            f"Score: {item.get('score', '')}",
            f"Comments: {item.get('num_comments', '')}",
            body,
        ])
        if not text_matches(f"{title}\n{content}", request):
            return None
        permalink = item.get("permalink")
        url = "https://www.reddit.com" + permalink if permalink else item.get("url")
        return build_source_document(
            source=self.name,
            source_type="social_media",
            title=title,
            content=content,
            url=url,
            published_at=parse_datetime(item.get("created_utc")),
            metadata={"raw": item, "platform": "reddit"},
        )


class XRecentSearchConnector(BaseConnector):
    """X/Twitter recent search via official API v2.

    Requires `bearer_token`. This connector is disabled by default and should be
    used only with proper API access.
    """

    connector_type = "x_recent_search"

    def __init__(self, name: str, bearer_token: str, max_results: int = 10, timeout: int = 20, **params: Any) -> None:
        super().__init__(name, bearer_token="***", max_results=max_results, **params)
        self.bearer_token = bearer_token
        self.max_results = max(10, min(max_results, 100))
        self.timeout = timeout

    def fetch(self, request: FetchRequest) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        terms = request.terms or [request.query or "AI"]
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        for term in terms:
            params = {"query": term, "max_results": self.max_results, "tweet.fields": "created_at,author_id,public_metrics,lang"}
            data = best_effort_json(http_get("https://api.twitter.com/2/tweets/search/recent", headers=headers, params=params, timeout=self.timeout))
            for tw in data.get("data", []) or []:
                text = str(tw.get("text") or "")
                title = text[:80] or "X post"
                if not text_matches(text, request):
                    continue
                docs.append(
                    build_source_document(
                        source=self.name,
                        source_type="social_media",
                        title=title,
                        content=text,
                        url=f"https://twitter.com/i/web/status/{tw.get('id')}",
                        published_at=parse_datetime(tw.get("created_at")),
                        metadata={"raw": tw, "platform": "x"},
                    )
                )
            if request.limit is not None and len(docs) >= request.limit:
                break
        return apply_limit(docs, request.limit)
