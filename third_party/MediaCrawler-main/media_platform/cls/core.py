# media_platform/cls/core.py
import asyncio
import os
import re
import json
from datetime import datetime
from collections import OrderedDict

from playwright.async_api import async_playwright

from .client import CLSClient
from tools import utils
from base.base_crawler import AbstractCrawler


TOPIC_RULES = {
    "政策监管": ["政策", "发改委", "工信部", "财政部", "监管", "国务院"],
    "业绩基本面": ["业绩", "净利润", "营收", "预增", "亏损", "年报", "季报"],
    "订单合作": ["订单", "合同", "中标", "签约", "合作"],
    "资金情绪": ["涨停", "跌停", "资金流入", "热度", "龙头"],
    "风险事件": ["减持", "问询", "处罚", "诉讼", "风险", "解禁"],
    "行业催化": ["人工智能", "AI", "算力", "液冷", "机器人", "低空经济", "半导体", "芯片"]
}

POSITIVE_WORDS = [
    "利好", "增长", "预增", "订单", "中标", "签约",
    "突破", "涨停", "回购", "超预期", "改善", "积极"
]

NEGATIVE_WORDS = [
    "利空", "亏损", "下滑", "减持", "处罚", "问询",
    "诉讼", "跌停", "风险", "违约", "承压", "不及预期"
]

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
    "低空经济": 2
}


def clean_news_text(text: str) -> str:
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def classify_topic(text: str) -> str:
    for topic, words in TOPIC_RULES.items():
        if any(word in text for word in words):
            return topic
    return "其他"


def score_sentiment(text: str) -> float:
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    raw = pos - neg

    if raw > 0:
        return round(min(1.0, raw / 5), 2)
    if raw < 0:
        return round(max(-1.0, raw / 5), 2)
    return 0.0


def score_importance(text: str, is_hot: int = 0) -> float:
    score = 0.0

    for word, weight in IMPORTANT_WORDS.items():
        if word in text:
            score += weight

    if is_hot:
        score += 2

    if re.search(r"\d", text):
        score += 1

    if 20 <= len(text) <= 300:
        score += 1

    return round(score, 2)


def make_brief_summary_rule(content: str, max_len: int = 80) -> str:
    content = clean_news_text(content)
    return content[:max_len]


def extract_keywords_simple(text: str, limit: int = 6) -> list:
    keywords = []
    for word in IMPORTANT_WORDS.keys():
        if word in text and word not in keywords:
            keywords.append(word)
    return keywords[:limit]


def deduplicate_news(news_list: list) -> list:
    """
    基于内容前80字符去重，适合快讯类新闻
    """
    seen = OrderedDict()

    for item in news_list:
        content = clean_news_text(item.get("内容", ""))
        if len(content) < 10:
            continue

        key = content[:80]
        if key not in seen:
            seen[key] = item

    return list(seen.values())


class CLSCrawler(AbstractCrawler):
    def __init__(self) -> None:
        self.browser_instance = None
        self.browser_context = None

    async def launch_browser(self, chromium, playwright_proxy, user_agent, headless=True):
        self.browser_instance = await chromium.launch(headless=headless)
        self.browser_context = await self.browser_instance.new_context(user_agent=user_agent)
        return self.browser_context

    def preprocess_news_item(self, item: dict) -> dict:
        content = clean_news_text(item.get("内容", ""))
        publish_time = str(item.get("时间", ""))
        source = str(item.get("来源", "财联社"))

        is_hot = 1 if "【" in content and "】" in content else 0
        topic = classify_topic(content)
        sentiment = score_sentiment(content)
        importance_score = score_importance(content, is_hot=is_hot)
        keywords = extract_keywords_simple(content)

        # 第一版先用规则摘要，后续你可以替换成 LLM 摘要
        if importance_score >= 6:
            summary = make_brief_summary_rule(content, max_len=100)
        else:
            summary = make_brief_summary_rule(content, max_len=60)

        processed = {
            "id": f"cls_{abs(hash(content))}",
            "source": source,
            "publish_time": publish_time,
            "content": content,
            "summary": summary,
            "topic": topic,
            "sentiment": sentiment,
            "importance_score": importance_score,
            "keywords": keywords,
            "is_hot": is_hot,
            "match_type": item.get("匹配关键词", "全量实时")
        }
        return processed

    def preprocess_news(self, raw_news: list) -> list:
        deduped_news = deduplicate_news(raw_news)
        processed_news = []

        for item in deduped_news:
            try:
                processed_news.append(self.preprocess_news_item(item))
            except Exception as e:
                utils.logger.warning(f"[财联社] 单条新闻预处理失败: {e}")

        return processed_news

    async def search(self):
        """
        全量抓取财联社电报 + 入库前预处理
        """
        if not self.browser_context:
            await self.start()
            return

        client = CLSClient(self.browser_context)

        # 1. 抓取全量实时电报
        raw_news = await client.fetch_cls_news()
        print(f"[财联社] 成功抓取到 {len(raw_news)} 条原始电报动态。")

        if not raw_news:
            utils.logger.warning("[财联社] 未获取到任何动态，请检查网络或页面结构。")
            return

        # 2. 标记来源
        for item in raw_news:
            item["匹配关键词"] = "全量实时"

        # 3. 保存原始数据
        await self.save_raw_data(raw_news)

        # 4. 去重 + 预处理
        processed_news = self.preprocess_news(raw_news)
        print(f"[财联社] 去重并预处理后剩余 {len(processed_news)} 条。")

        # 5. 保存处理后数据
        await self.save_processed_data(processed_news)

        print(
            f"[财联社] 已完成全量数据持久化："
            f"原始 {len(raw_news)} 条，处理后 {len(processed_news)} 条。"
        )

    async def start(self):
        async with async_playwright() as p:
            ua = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            )
            await self.launch_browser(p.chromium, None, ua, headless=False)
            await self.search()
            if self.browser_instance:
                await self.browser_instance.close()

    async def save_raw_data(self, data: list):
        save_dir = "data/cls/raw"
        os.makedirs(save_dir, exist_ok=True)

        file_path = os.path.join(
            save_dir,
            f"cls_raw_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        )

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        utils.logger.info(f">>> [财联社] 原始数据保存至: {file_path}")

    async def save_processed_data(self, data: list):
        save_dir = "data/cls/processed"
        os.makedirs(save_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')

        json_path = os.path.join(save_dir, f"cls_processed_{timestamp}.json")
        jsonl_path = os.path.join(save_dir, f"cls_processed_{timestamp}.jsonl")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        with open(jsonl_path, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        utils.logger.info(f">>> [财联社] 处理后 JSON 保存至: {json_path}")
        utils.logger.info(f">>> [财联社] 处理后 JSONL 保存至: {jsonl_path}")