# -*- coding: utf-8 -*-
import asyncio
import os
import random
from typing import Dict, List, Optional, Type
import json
import re
from datetime import datetime
from collections import OrderedDict
from playwright.async_api import BrowserContext, BrowserType, Page, async_playwright, Playwright

import config
from base.base_crawler import AbstractCrawler
from media_platform.xueqiu.client import XueQiuClient  # 需确保你有对应的 Client 实现
from media_platform.xueqiu.login import XueQiuLogin    # 需确保你有对应的 Login 实现
from store import xueqiu as xueqiu_store                        # 需确保你有对应的 Store 实现
from tools import utils
from var import crawler_type_var, source_keyword_var
from tools.cdp_browser import CDPBrowserManager

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

def deduplicate_xq_comments(comment_list: list) -> list:
    seen = OrderedDict()

    for item in comment_list:
        text = clean_news_text(
            item.get("text")
            or item.get("content")
            or item.get("description")
            or item.get("title")
            or ""
        )
        if len(text) < 5:
            continue

        key = text[:100]
        if key not in seen:
            seen[key] = item

    return list(seen.values())

class XueQiuCrawler(AbstractCrawler):
    context_page: Page
    xq_client: XueQiuClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        self.index_url = "https://xueqiu.com"
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        self.cdp_manager = None
        self.ip_proxy_pool = None

        # 关键：先初始化，避免属性不存在
        self.browser_context = None
        self.context_page = None
        self.xq_client = None

        # 如果以后 main.py 注入共享浏览器，也不会报错
        self.browser_client = None

    def set_browser_context(self, context):
        self.browser_context = context
    
    def preprocess_comment_item(self, item: dict, keyword: str = "") -> dict:
        """
        将雪球评论统一处理成和 CLS processed 一致的结构
        """
        content = clean_news_text(
            item.get("text")
            or item.get("content")
            or item.get("description")
            or item.get("title")
            or ""
        )

        publish_time = (
            str(item.get("time"))
            or str(item.get("created_at"))
            or str(item.get("publish_time"))
            or ""
        )

        comment_id = str(item.get("id", ""))
        is_hot = 1 if ("热" in content or "大涨" in content or "涨停" in content) else 0

        topic = classify_topic(content)
        sentiment = score_sentiment(content)
        importance_score = score_importance(content, is_hot=is_hot)
        keywords = extract_keywords_simple(content)

        if importance_score >= 6:
            summary = make_brief_summary_rule(content, max_len=100)
        else:
            summary = make_brief_summary_rule(content, max_len=60)

        processed = {
            "id": f"xq_{comment_id}" if comment_id else f"xq_{abs(hash(content))}",
            "source": "xq",
            "publish_time": publish_time,
            "content": content,
            "summary": summary,
            "topic": topic,
            "sentiment": sentiment,
            "importance_score": importance_score,
            "keywords": keywords,
            "is_hot": is_hot,
            "match_type": "关键词搜索",
            "stock_name": keyword,
            "symbol": keyword
        }
        return processed

    def preprocess_comments(self, raw_comments: list, keyword: str = "") -> list:
        deduped_comments = deduplicate_xq_comments(raw_comments)
        processed_comments = []

        for item in deduped_comments:
            try:
                processed_comments.append(self.preprocess_comment_item(item, keyword=keyword))
            except Exception as e:
                utils.logger.warning(f"[XueQiuCrawler] 单条评论预处理失败: {e}")

        return processed_comments

    async def save_raw_data(self, data: list):
        save_dir = "data/xq/raw"
        os.makedirs(save_dir, exist_ok=True)

        file_path = os.path.join(
            save_dir,
            f"xq_raw_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        )

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        utils.logger.info(f">>> [雪球] 原始数据保存至: {file_path}")

    async def save_processed_data(self, data: list, keyword: str = ""):
        save_dir = "data/xq/cache"
        os.makedirs(save_dir, exist_ok=True)

        today = datetime.now().strftime("%Y%m%d")
        safe_kw = re.sub(r'[\\/:*?"<>| ]+', "_", keyword)

        json_path = os.path.join(save_dir, f"{today}_{safe_kw}.json")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        utils.logger.info(f">>> [雪球] 缓存保存至: {json_path}")

    async def start(self) -> None:
        playwright_proxy_format, httpx_proxy_format = None, None

        if config.ENABLE_IP_PROXY:
            from tools.ip_proxy import create_ip_pool

            self.ip_proxy_pool = await create_ip_pool(
                config.IP_PROXY_POOL_COUNT,
                enable_validate_ip=True
            )
            ip_proxy_info = await self.ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

        # =========================================================
        # 方案 A：如果 main.py 注入了 browser_client，则复用它
        # =========================================================
        if getattr(self, "browser_client", None) is not None:
            utils.logger.info("[XueQiuCrawler] 使用 main.py 注入的共享浏览器")

            self.browser_context = self.browser_client.context

            if self.browser_context is None:
                utils.logger.error("[XueQiuCrawler] browser_client.context 为空")
                return

            self.context_page = await self.browser_context.new_page()
            await self.context_page.goto(self.index_url, wait_until="domcontentloaded")

            self.xq_client = await self.create_xq_client(httpx_proxy_format)
            self.xq_client.browser_context = self.browser_context

            crawler_type_var.set(config.CRAWLER_TYPE)

            if config.CRAWLER_TYPE == "search":
                await self.search()
            else:
                utils.logger.warning(f"[XueQiuCrawler] 不支持的爬取类型: {config.CRAWLER_TYPE}")

            utils.logger.info("[XueQiuCrawler] 任务完成 ...")
            return

        # =========================================================
        # 方案 B：没有 browser_client，则使用原始 Playwright 启动方式
        # =========================================================
        async with async_playwright() as playwright:
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[XueQiuCrawler] 使用 CDP 模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    self.user_agent,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[XueQiuCrawler] 使用标准模式启动浏览器")
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    playwright_proxy_format,
                    self.user_agent,
                    headless=config.HEADLESS,
                )

                try:
                    await self.browser_context.add_init_script(path="libs/stealth.min.js")
                except Exception as e:
                    utils.logger.warning(f"[XueQiuCrawler] stealth 脚本注入失败: {e}")

            if self.browser_context is None:
                utils.logger.error("[XueQiuCrawler] browser_context 初始化失败")
                return

            self.context_page = await self.browser_context.new_page()
            await self.context_page.goto(self.index_url, wait_until="domcontentloaded")

            # 初始化雪球 API 客户端
            self.xq_client = await self.create_xq_client(httpx_proxy_format)

            # 把当前浏览器上下文注入 client
            self.xq_client.browser_context = self.browser_context

            crawler_type_var.set(config.CRAWLER_TYPE)

            if config.CRAWLER_TYPE == "search":
                await self.search()
            else:
                utils.logger.warning(f"[XueQiuCrawler] 不支持的爬取类型: {config.CRAWLER_TYPE}")

            utils.logger.info("[XueQiuCrawler] 任务完成 ...")

    # media_platform/xueqiu/core.py
    async def search(self) -> None:
        """
        执行搜索逻辑：
        1. 抓取多页评论
        2. 汇总原始数据
        3. 保存 raw
        4. 预处理并保存 processed
        """
        keywords = getattr(self, "keyword_list", ["SH600519"])

        for keyword in keywords:
            utils.logger.info(f"[XueQiuCrawler.search] 开始抓取关键词: {keyword}")
            all_comments = []

            if self.context_page:
                print(f"当前浏览器页面数量: {len(self.browser_context.pages)}")
                page = self.context_page
                self.xq_client.playwright_page = page

                search_url = f"https://xueqiu.com/k?q={keyword}"
                utils.logger.info(f"[XueQiuCrawler] 正在引导浏览器跳转至: {search_url}")

                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=10000)
                except Exception as e:
                    utils.logger.warning(f"[XueQiuCrawler] 页面跳转超时(预期内): {e}")

                await asyncio.sleep(5)
            else:
                utils.logger.error("[XueQiuCrawler.search] 无有效的浏览器页面可用，无法引导跳转！")
                return

            # 开始翻页抓取
            for page_num in range(1, 5):
                utils.logger.info(f"[XueQiuCrawler.search] 正在抓取 {keyword}, 第 {page_num} 页")

                comments_res = await self.xq_client.get_search_comments(keyword, page_num)
                items = comments_res.get("list", [])

                if not items:
                    utils.logger.info(f"[XueQiuCrawler.search] 关键词 {keyword} 无更多内容")
                    break

                for item in items:
                    item["匹配关键词"] = keyword

                all_comments.extend(items)

                await asyncio.sleep(random.uniform(2, 5))

            # 统一保存
            if all_comments:
                await self.save_raw_data(all_comments)

                processed_comments = self.preprocess_comments(all_comments, keyword=keyword)
                await self.save_processed_data(processed_comments, keyword=keyword)

                utils.logger.info(
                    f"[XueQiuCrawler.search] 关键词 {keyword} 抓取完成："
                    f"原始 {len(all_comments)} 条，处理后 {len(processed_comments)} 条"
                )
            else:
                utils.logger.warning(f"[XueQiuCrawler.search] 关键词 {keyword} 未抓到任何评论")

    async def process_comment_item(self, item: Dict, semaphore: asyncio.Semaphore):
        """处理并保存单条评论数据"""
        async with semaphore:
            # 解析并清洗数据
            comment_id = str(item.get("id"))
            utils.logger.info(f"[XueQiuCrawler] 正在保存评论: {comment_id}")
            
            # 调用 store 模块持久化到数据库/Excel
            await xueqiu_store.save_comment(item)

    async def create_xq_client(self, httpx_proxy: Optional[str]) -> XueQiuClient:
        """创建雪球客户端并注入当前的 Cookies"""
        utils.logger.info("[XueQiuCrawler.create_xq_client] 初始化雪球 API 客户端 ...")
        cookie_str, cookie_dict = utils.convert_cookies(await self.browser_context.cookies())
        
        return XueQiuClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": self.user_agent,
                "Cookie": cookie_str,
                "Referer": "https://xueqiu.com/",
                "Accept": "application/json, text/plain, */*"
            },
            cookie_dict=cookie_dict,
            browser_client=self.browser_client   # ⭐关键
        )

    async def launch_browser(self, chromium: BrowserType, playwright_proxy: Optional[Dict], user_agent: Optional[str], headless: bool = True) -> BrowserContext:
        """逻辑同 XHS，建议提取到基类或工具类中"""
        # ... (此处逻辑与你提供的 XHS 代码中 launch_browser 完全一致)
        if config.SAVE_LOGIN_STATE:
            user_data_dir = os.path.join(os.getcwd(), "browser_data", config.USER_DATA_DIR % "xueqiu")
            return await chromium.launch_persistent_context(
                user_data_dir=user_data_dir, headless=headless, proxy=playwright_proxy, user_agent=user_agent
            )
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy)
            return await browser.new_context(user_agent=user_agent)

    async def launch_browser_with_cdp(self, playwright: Playwright, playwright_proxy: Optional[Dict], user_agent: Optional[str], headless: bool = True) -> BrowserContext:
        """逻辑同 XHS，支持 CDP 模式"""
        self.cdp_manager = CDPBrowserManager()
        return await self.cdp_manager.launch_and_connect(
            playwright=playwright, playwright_proxy=playwright_proxy, user_agent=user_agent, headless=headless
        )