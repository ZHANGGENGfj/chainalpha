# -*- coding: utf-8 -*-
import asyncio
from typing import Optional
from playwright.async_api import BrowserContext, Page
from tools import utils

class XueQiuLogin:
    def __init__(
        self,
        login_type: str, # 'qrcode' 或 'phone' 等
        browser_context: BrowserContext,
        context_page: Page,
        login_phone: Optional[str] = "",
        cookie_str: Optional[str] = ""
    ) -> None:
        self.login_type = login_type
        self.browser_context = browser_context
        self.context_page = context_page
        self.login_phone = login_phone
        self.cookie_str = cookie_str

    async def begin(self):
        """开始登录流程"""
        utils.logger.info(f"[XueQiuLogin.begin] 正在启动雪球登录，类型: {self.login_type}")
        
        # 1. 访问登录页
        await self.context_page.goto("https://xueqiu.com/")
        
        # 2. 点击首页的登录按钮（根据雪球当前 DOM 结构进行选择）
        # 注意：这里需要根据雪球实际的 CSS Selector 调整
        try:
            await self.context_page.click(".nav__login") # 示例选择器
        except:
            pass
        
        # 3. 等待用户手动操作或扫码
        # 雪球登录成功后，Cookie 中会出现 'xq_a_token'
        utils.logger.info("请在弹出的浏览器窗口中完成登录操作...")
        
        while True:
            cookies = await self.browser_context.cookies()
            if any(ck.get('name') == 'xq_a_token' for ck in cookies):
                utils.logger.info("检测到有效 Token，登录成功！")
                break
            await asyncio.sleep(2)