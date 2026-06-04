from __future__ import annotations

import difflib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import config
from playwright.async_api import BrowserContext, BrowserType, async_playwright

from .client import FinanceNewsClient
from base.base_crawler import AbstractCrawler
from tools import utils


TOPIC_RULES = {
    "政策监管": ["政策", "发改委", "工信部", "财政部", "监管", "国务院", "央行", "证监会"],
    "业绩基本面": ["业绩", "净利润", "营收", "预增", "亏损", "年报", "季报", "中报"],
    "订单合作": ["订单", "合同", "中标", "签约", "合作", "采购"],
    "资金情绪": ["涨停", "跌停", "资金流入", "热度", "龙头", "成交额", "北向"],
    "风险事件": ["减持", "问询", "处罚", "诉讼", "风险", "解禁", "退市"],
    "行业催化": ["人工智能", "AI", "算力", "液冷", "机器人", "低空经济", "半导体", "芯片", "新能源"],
}

POSITIVE_WORDS = ["利好", "增长", "预增", "订单", "中标", "签约", "突破", "涨停", "回购", "超预期", "改善", "积极"]
NEGATIVE_WORDS = ["利空", "亏损", "下滑", "减持", "处罚", "问询", "诉讼", "跌停", "风险", "违约", "承压", "不及预期"]
IMPORTANT_WORDS = {
    "涨停": 3,
    "跌停": 3,
    "订单": 2,
    "中标": 2,
    "签约": 2,
    "业绩": 2,
    "预增": 2,
    "亏损": 2,
    "政策": 2,
    "监管": 2,
    "减持": 2,
    "回购": 2,
    "风险": 2,
    "人工智能": 2,
    "AI": 2,
    "算力": 2,
    "液冷": 2,
    "半导体": 2,
    "芯片": 2,
    "机器人": 2,
    "低空经济": 2,
}


def clean_news_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_title(title: str) -> str:
    title = clean_news_text(title).lower()
    title = re.sub(r"[【】\[\]（）()《》\"'“”‘’：:，,。.!！?？、|_\-\s]+", "", title)
    title = re.sub(r"(快讯|独家|最新|重磅|突发|盘前|盘后)$", "", title)
    return title


def title_ngrams(title: str, n: int = 3) -> set[str]:
    text = normalize_title(title)
    if len(text) <= n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def title_similarity(left: str, right: str) -> float:
    left_norm = normalize_title(left)
    right_norm = normalize_title(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    left_grams = title_ngrams(left_norm)
    right_grams = title_ngrams(right_norm)
    jaccard = len(left_grams & right_grams) / max(len(left_grams | right_grams), 1)
    sequence = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
    return max(jaccard, sequence)


def clean_raw_news_item(item: dict[str, Any]) -> dict[str, Any]:
    title = clean_news_text(item.get("标题") or item.get("内容") or "")
    clean_item = dict(item)
    clean_item["标题"] = title
    clean_item.pop("时间", None)
    if clean_news_text(clean_item.get("内容")) == title:
        clean_item.pop("内容", None)
    return clean_item


class FinanceNewsDedupIndex:
    """Local rolling dedup index for frequent finance-news collection.

    Policy:
    - URL exact dedup: long-term retention.
    - normalized title exact dedup: 30 days.
    - fuzzy title dedup: 7 days.
    """

    def __init__(
        self,
        path: str | Path = "data/finance_news/index/dedup_index.json",
        *,
        exact_title_days: int = 30,
        fuzzy_title_days: int = 7,
        fuzzy_threshold: float = 0.86,
    ) -> None:
        self.path = Path(path)
        self.exact_title_days = exact_title_days
        self.fuzzy_title_days = fuzzy_title_days
        self.fuzzy_threshold = fuzzy_threshold
        self.data = self._load()
        self.prune()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"urls": {}, "titles": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"urls": {}, "titles": {}}
        data.setdefault("urls", {})
        data.setdefault("titles", {})
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def prune(self) -> None:
        now = datetime.now()
        exact_cutoff = now - timedelta(days=self.exact_title_days)
        fuzzy_cutoff = now - timedelta(days=max(self.exact_title_days, self.fuzzy_title_days))
        self.data["titles"] = {
            norm: record
            for norm, record in self.data.get("titles", {}).items()
            if self._parse_dt(record.get("last_seen")) >= exact_cutoff
        }
        # Keep URL index long-term, but drop unusable blank URLs if present.
        self.data["urls"] = {url: record for url, record in self.data.get("urls", {}).items() if url}
        # If old versions left very old titles, exact_cutoff handles them. fuzzy_cutoff is kept for clarity.
        _ = fuzzy_cutoff

    def is_duplicate(self, item: dict[str, Any]) -> tuple[bool, str]:
        title = clean_news_text(item.get("标题") or item.get("内容") or "")
        norm = normalize_title(title)
        url = clean_news_text(item.get("链接") or item.get("url") or "")
        if url and url in self.data.get("urls", {}):
            return True, "duplicate_url"
        if norm and norm in self.data.get("titles", {}):
            return True, "duplicate_title_exact_30d"
        if norm and self._has_fuzzy_title_duplicate(title):
            return True, "duplicate_title_fuzzy_7d"
        return False, "new"

    def add(self, item: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        title = clean_news_text(item.get("标题") or item.get("内容") or "")
        norm = normalize_title(title)
        url = clean_news_text(item.get("链接") or item.get("url") or "")
        source = clean_news_text(item.get("来源") or item.get("source") or "")
        if url:
            record = self.data.setdefault("urls", {}).setdefault(url, {"first_seen": now})
            record.update({"last_seen": now, "title": title, "source": source})
        if norm:
            record = self.data.setdefault("titles", {}).setdefault(norm, {"first_seen": now})
            record.update({"last_seen": now, "title": title, "source": source, "url": url})

    def filter_new(self, news_list: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
        kept: list[dict[str, Any]] = []
        stats = {
            "input": len(news_list),
            "kept": 0,
            "duplicate_url": 0,
            "duplicate_title_exact_30d": 0,
            "duplicate_title_fuzzy_7d": 0,
            "invalid": 0,
        }
        batch_titles: list[str] = []
        batch_norms: set[str] = set()

        for raw_item in news_list:
            item = clean_raw_news_item(raw_item)
            title = clean_news_text(item.get("标题") or "")
            norm = normalize_title(title)
            if len(norm) < 6:
                stats["invalid"] += 1
                continue
            if norm in batch_norms or any(title_similarity(title, old_title) >= self.fuzzy_threshold for old_title in batch_titles):
                stats["duplicate_title_fuzzy_7d"] += 1
                continue
            duplicate, reason = self.is_duplicate(item)
            if duplicate:
                stats[reason] = stats.get(reason, 0) + 1
                continue
            batch_norms.add(norm)
            batch_titles.append(title)
            self.add(item)
            kept.append(item)

        stats["kept"] = len(kept)
        return kept, stats

    def _has_fuzzy_title_duplicate(self, title: str) -> bool:
        cutoff = datetime.now() - timedelta(days=self.fuzzy_title_days)
        for record in self.data.get("titles", {}).values():
            if self._parse_dt(record.get("last_seen")) < cutoff:
                continue
            old_title = clean_news_text(record.get("title") or "")
            if old_title and title_similarity(title, old_title) >= self.fuzzy_threshold:
                return True
        return False

    def _parse_dt(self, value: Any) -> datetime:
        if not value:
            return datetime.min
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return datetime.min


def deduplicate_news(news_list: list[dict[str, Any]], threshold: float = 0.86) -> list[dict[str, Any]]:
    """Backward-compatible in-memory dedup helper."""
    index = FinanceNewsDedupIndex(path=Path(os.devnull), fuzzy_threshold=threshold)
    kept, _ = index.filter_new(news_list)
    return kept



def load_json_list(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def merge_daily_news_items(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge one day's file and deduplicate inside the file."""
    kept: list[dict[str, Any]] = []
    kept_titles: list[str] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for raw_item in [*existing, *incoming]:
        item = dict(raw_item)
        title = clean_news_text(item.get("标题") or item.get("title") or item.get("内容") or item.get("content") or "")
        norm = normalize_title(title)
        url = clean_news_text(item.get("链接") or item.get("url") or "")
        if len(norm) < 6:
            continue
        if url and url in seen_urls:
            continue
        if norm in seen_titles:
            continue
        if any(title_similarity(title, old_title) >= 0.86 for old_title in kept_titles):
            continue
        item["标题"] = title if "标题" in item or "title" not in item else item.get("title", title)
        item.pop("时间", None)
        if clean_news_text(item.get("内容")) == title:
            item.pop("内容", None)
        if url:
            seen_urls.add(url)
        seen_titles.add(norm)
        kept_titles.append(title)
        kept.append(item)
    return kept

def classify_topic(text: str) -> str:
    for topic, words in TOPIC_RULES.items():
        if any(word in text for word in words):
            return topic
    return "其他"


def score_sentiment(text: str) -> float:
    pos = sum(1 for word in POSITIVE_WORDS if word in text)
    neg = sum(1 for word in NEGATIVE_WORDS if word in text)
    raw = pos - neg
    if raw > 0:
        return round(min(1.0, raw / 5), 2)
    if raw < 0:
        return round(max(-1.0, raw / 5), 2)
    return 0.0


def score_importance(text: str) -> float:
    score = 0.0
    for word, weight in IMPORTANT_WORDS.items():
        if word in text:
            score += weight
    if re.search(r"\d", text):
        score += 1
    if 20 <= len(text) <= 300:
        score += 1
    return round(score, 2)


def extract_keywords_simple(text: str, limit: int = 8) -> list[str]:
    keywords: list[str] = []
    for word in IMPORTANT_WORDS:
        if word in text and word not in keywords:
            keywords.append(word)
    return keywords[:limit]


class FinanceNewsCrawler(AbstractCrawler):
    def __init__(self) -> None:
        self.browser_context: Optional[BrowserContext] = None
        self.browser_instance = None
        self.keyword_list: list[str] = []
        self.dedup_index = FinanceNewsDedupIndex()

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        self.browser_instance = await chromium.launch(headless=headless, proxy=playwright_proxy)
        self.browser_context = await self.browser_instance.new_context(user_agent=user_agent)
        return self.browser_context

    def preprocess_news_item(self, item: dict[str, Any]) -> dict[str, Any]:
        title = clean_news_text(item.get("标题") or item.get("内容") or "")
        content = clean_news_text(item.get("内容") or "")
        text = f"{title} {content}"
        summary_base = content or title
        return {
            "id": f"finance_news_{abs(hash(str(item.get('来源', '')) + title + str(item.get('链接', ''))))}",
            "source": item.get("来源", "财经媒体"),
            "title": title,
            "content": content,
            "url": item.get("链接", ""),
            "summary": summary_base[:100],
            "topic": classify_topic(text),
            "sentiment": score_sentiment(text),
            "importance_score": score_importance(text),
            "keywords": extract_keywords_simple(text),
            "match_type": item.get("匹配关键词", "全量财经新闻"),
        }

    def preprocess_news(self, raw_news: list[dict[str, Any]]) -> list[dict[str, Any]]:
        processed: list[dict[str, Any]] = []
        for item in raw_news:
            try:
                if self.keyword_list:
                    text = clean_news_text((item.get("标题") or "") + " " + (item.get("内容") or ""))
                    matched = [keyword for keyword in self.keyword_list if keyword and keyword in text]
                    if not matched:
                        continue
                    item["匹配关键词"] = ",".join(matched)
                processed.append(self.preprocess_news_item(item))
            except Exception as exc:
                utils.logger.warning(f"[FinanceNews] single item preprocess failed: {exc}")
        return processed

    async def search(self):
        if not self.browser_context:
            await self.start()
            return

        client = FinanceNewsClient(self.browser_context)
        raw_news = await client.fetch_all_news(limit_per_source=80, keywords=self.keyword_list)
        print(f"[FinanceNews] fetched {len(raw_news)} raw news items before dedup")

        raw_news, dedup_stats = self.dedup_index.filter_new(raw_news)
        self.dedup_index.save()
        print(
            "[FinanceNews] kept {kept} new raw news after historical dedup "
            "(url={duplicate_url}, exact_title={duplicate_title_exact_30d}, "
            "fuzzy_title={duplicate_title_fuzzy_7d}, invalid={invalid})".format(**dedup_stats)
        )

        if not raw_news:
            utils.logger.warning("[FinanceNews] no new news after dedup; skip saving raw/processed files")
            return

        await self.save_raw_data(raw_news)
        processed_news = self.preprocess_news(raw_news)
        print(f"[FinanceNews] processed {len(processed_news)} news items")
        await self.save_processed_data(processed_news)

    async def start(self):
        async with async_playwright() as playwright:
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            await self.launch_browser(playwright.chromium, None, user_agent, headless=config.HEADLESS)
            try:
                await self.search()
            finally:
                if self.browser_instance:
                    await self.browser_instance.close()

    async def save_raw_data(self, data: list[dict[str, Any]]):
        day = datetime.now().strftime("%Y%m%d")
        save_dir = os.path.join("data/finance_news/raw", day)
        os.makedirs(save_dir, exist_ok=True)
        file_path = os.path.join(save_dir, f"finance_news_raw_{day}.json")
        existing = load_json_list(file_path)
        merged = merge_daily_news_items(existing, data)
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(merged, file, ensure_ascii=False, indent=4)
        utils.logger.info(
            f">>> [FinanceNews] raw daily file saved to {file_path}; "
            f"old={len(existing)}, new={len(data)}, total={len(merged)}"
        )

    async def save_processed_data(self, data: list[dict[str, Any]]):
        day = datetime.now().strftime("%Y%m%d")
        save_dir = os.path.join("data/finance_news/processed", day)
        os.makedirs(save_dir, exist_ok=True)
        json_path = os.path.join(save_dir, f"finance_news_processed_{day}.json")
        jsonl_path = os.path.join(save_dir, f"finance_news_processed_{day}.jsonl")
        existing = load_json_list(json_path)
        merged = merge_daily_news_items(existing, data)
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump(merged, file, ensure_ascii=False, indent=4)
        with open(jsonl_path, "w", encoding="utf-8") as file:
            for item in merged:
                file.write(json.dumps(item, ensure_ascii=False) + "\n")
        utils.logger.info(
            f">>> [FinanceNews] processed daily file saved to {json_path}; "
            f"old={len(existing)}, new={len(data)}, total={len(merged)}"
        )

