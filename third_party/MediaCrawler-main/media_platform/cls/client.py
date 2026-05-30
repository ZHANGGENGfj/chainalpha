# media_platform/cls/client.py
import asyncio
from typing import List, Dict
from tools import utils
class CLSClient:
    def __init__(self, browser_context):
        self.browser_context = browser_context
        # 财联社电报实时流
        self.base_url = "https://www.cls.cn/telegraph"

    async def fetch_cls_news(self) -> List[Dict]:
        utils.logger.info(f"[财联社] 正在打开电报实时流...")
        page = await self.browser_context.new_page()
        
        try:
            # 1. 访问页面并等待网络空闲
            await page.goto(self.base_url, wait_until="networkidle", timeout=60000)
            
            # --- 新增：滚动加载逻辑，直到探测到 100 条以上的数据 ---
            target_count = 100
            retry_count = 0
            max_retries = 30  # 防止无限死循环

            while retry_count < max_retries:
                # 统计当前页面有多少个新闻节点
                current_items = await page.query_selector_all("div[class*='telegraph-item']")
                count = len(current_items)
                utils.logger.info(f"[财联社] 当前页面已加载: {count} 条")

                if count >= target_count:
                    utils.logger.info(f"[财联社] 已达到目标数量 ({target_count}条)，停止加载。")
                    break

                # 模拟滚动到页面底部触发 Ajax 加载
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                
                # 备选方案：尝试寻找并点击“加载更多”按钮（如果滚动不生效）
                load_more_btn = await page.query_selector("text='加载更多'")
                if load_more_btn:
                    await load_more_btn.click()

                # 等待新数据渲染的网络间隔
                await asyncio.sleep(2) 
                retry_count += 1
            
            # 重新获取加载完成后的所有节点
            items = await page.query_selector_all("div[class*='telegraph-item']")
            if not items:
                items = await page.query_selector_all(".telegraph-content-box")

            news_data = []
            utils.logger.info(f"[财联社] 开始解析，探测到总节点数量: {len(items)}")

            # 解析逻辑保持不变，但增加一个 limit 确保只取前 300 条
            # 修改 client.py 中的解析部分
            for item in items:
                try:
                    # 尝试更宽泛的选择器
                    content_el = await item.query_selector("div[class*='content'], span[class*='content'], .telegraph-content")
                    # 如果还是找不到，尝试获取 item 下所有文本内容
                    if not content_el:
                        content_text = await item.inner_text()
                    else:
                        content_text = await content_el.inner_text()

                    content_text = content_text.strip().replace('\n', ' ')
                    
                    if len(content_text) > 10:  # 过滤掉太短的无意义字符
                        news_data.append({
                            "来源": "财联社",
                            "时间": "实时", # 时间提取容易出错，先保内容
                            "内容": content_text
                        })
                except:
                    continue

            return news_data
            
        except Exception as e:
            utils.logger.error(f"[财联社] 页面解析异常: {e}")
            return []
        finally:
            await page.close()