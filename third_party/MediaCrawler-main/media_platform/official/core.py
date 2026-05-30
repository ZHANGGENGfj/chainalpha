# media_platform/official/core.py
import asyncio
import pandas as pd
import os
import random
from datetime import datetime
from typing import Dict, Optional
from playwright.async_api import BrowserContext, BrowserType, async_playwright

from .client import OfficialClient
from tools import utils
from base.base_crawler import AbstractCrawler

class OfficialCrawler(AbstractCrawler):
    def __init__(self) -> None:
        self.browser_context: Optional[BrowserContext] = None
        self.official_client: Optional[OfficialClient] = None
        self.playwright_instance = None
        self.browser_instance = None

    async def launch_browser(self, chromium: BrowserType, playwright_proxy: Optional[Dict], user_agent: Optional[str], headless: bool = True) -> BrowserContext:
        """
        实现基类要求的 launch_browser 方法
        """
        utils.logger.info("[OfficialCrawler] 正在启动浏览器...")
        # 注意：这里我们使用传入的 chromium 参数
        self.browser_instance = await chromium.launch(headless=headless, proxy=playwright_proxy)
        self.browser_context = await self.browser_instance.new_context(user_agent=user_agent)
        return self.browser_context

    async def search(self):
        """
        实现基类要求的 search 方法：具体的爬取逻辑
        """
        if not self.browser_context:
            utils.logger.error("[OfficialCrawler] 浏览器上下文未初始化，请先调用 start 或 launch_browser")
            return

        self.official_client = OfficialClient(self.browser_context)
        all_results = []
        # 你关心的四个部委
        depts = ["发改委", "工信部", "财政部", "统计局"]
        
        for dept in depts:
            utils.logger.info(f"--- 正在采集: {dept} ---")
            news = await self.official_client.fetch_department_news(dept)
            if news:
                all_results.extend(news)
            # 随机休眠防封
            await asyncio.sleep(random.uniform(2, 5))

        if all_results:
            await self.save_data(all_results)
        else:
            utils.logger.warning("[OfficialCrawler] 未采集到任何有效数据。")

    async def start(self):
        """
        实现基类要求的 start 方法：整个任务的入口
        """
        async with async_playwright() as playwright:
            # 1. 准备配置（模拟框架通常有的配置）
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            
            # 2. 调用我们自己实现的 launch_browser
            await self.launch_browser(playwright.chromium, None, user_agent, headless=False)
            
            try:
                # 3. 执行核心搜索/采集逻辑
                await self.search()
            finally:
                # 4. 采集完后关闭浏览器
                if self.browser_instance:
                    await self.browser_instance.close()

    async def save_data(self, data: list):
        """保存为 Excel"""
        df = pd.DataFrame(data)
        save_dir = "data/official"
        os.makedirs(save_dir, exist_ok=True)
        file_path = f"{save_dir}/gov_news_{datetime.now().strftime('%Y%m%d')}.xlsx"
        df.to_excel(file_path, index=False)
        utils.logger.info(f">>> [OK] 采集任务完成，数据已存入: {file_path}")