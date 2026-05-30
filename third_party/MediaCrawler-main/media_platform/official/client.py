# media_platform/official/client.py
import asyncio
from typing import List, Dict
from urllib.parse import urljoin
from tools import utils
import datetime


class OfficialClient:
    def __init__(self, browser_context):
        self.browser_context = browser_context
        # 定义目标部委的官方链接
        self.sources = {
            "发改委": "https://www.ndrc.gov.cn/xwdt/tzgg/",
            "工信部": "https://www.miit.gov.cn/zwgk/zcwj/index.html",
            "财政部": "http://www.mof.gov.cn/zhengwuxinxi/caizhengxinwen/",
            "统计局": "http://www.stats.gov.cn/sj/zxfb/"
        }

    # 【关键：确保方法名完全匹配】
    # media_platform/official/client.py

    async def fetch_department_news(self, dept_name: str) -> List[Dict]:
        url = self.sources.get(dept_name)
        if not url: return []
        
        utils.logger.info(f"[{dept_name}] 正在访问: {url}")
        page = await self.browser_context.new_page()
        
        try:
            # 增加等待时间，确保 JS 加载完成
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(3) 
            
            # --- 核心改进：针对性选择器 ---
            dept_selectors = {
                "发改委": "ul.list-res li, .list-body li",
                "工信部": ".pg-main-container .clist_con li, .list-content li",
                "财政部": ".list li, .txt_list li",
                "统计局": ".list-content li, .list_item"
            }
            
            selector = dept_selectors.get(dept_name, "li")
            elements = await page.query_selector_all(selector)
            
            # 如果上面的精准选择器失效，尝试万能兜底方案
            if not elements:
                elements = await page.query_selector_all("a[href*='.html'], a[href*='.htm']")

            news_data = []
            for el in elements[:12]:
                # 在 li 内部寻找 a 标签
                title_el = await el.query_selector("a")
                if not title_el:
                    # 如果 li 本身就是 a 标签
                    title_el = el if (await el.evaluate("el => el.tagName")) == "A" else None
                
                if title_el:
                    title = (await title_el.inner_text()).strip()
                    link = await title_el.get_attribute("href")
                    
                    # 过滤掉没有标题或链接的脏数据
                    if not title or len(title) < 5 or not link:
                        continue
                        
                    if not link.startswith("http"):
                        from urllib.parse import urljoin
                        link = urljoin(url, link)
                    
                    news_data.append({
                        "部门": dept_name,
                        "标题": title,
                        "链接": link,
                    })
            
            utils.logger.info(f"[{dept_name}] 成功提取到 {len(news_data)} 条新闻")
            return news_data

        except Exception as e:
            utils.logger.error(f"[{dept_name}] 提取失败: {e}")
            return []
        finally:
            await page.close()