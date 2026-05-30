# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional
import httpx
from tools import utils
import urllib.parse
from datetime import datetime
import pandas as pd
import os
import re 

class XueQiuClient:
    def __init__(
        self,
        proxy: Optional[str] = None,
        headers: Optional[Dict] = None,
        playwright_page: Any = None,
        cookie_dict: Optional[Dict] = None,
        browser_client=None
    ) -> None:
        self.proxy = proxy
        self.headers = headers or {}
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict or {}

        self.browser_client = browser_client

        # 初始化异步请求客户端
        self.client = httpx.AsyncClient(
            proxy=self.proxy,
            headers=self.headers,
            timeout=10.0
        )

        
    async def update_cookies(self, browser_context: Any):
        """从 Playwright 浏览器上下文中同步最新的 Cookies"""
        cookies = await browser_context.cookies()
        cookie_str, self.cookie_dict = utils.convert_cookies(cookies)
        self.headers["Cookie"] = cookie_str
        self.client.headers.update({"Cookie": cookie_str})

    async def pong(self) -> bool:
        """检查登录状态（通过尝试访问个人设置或首页接口）"""
        try:
            # 访问雪球一个需要权限的接口来验证 Token 是否有效
            res = await self.client.get("https://xueqiu.com/v4/user/show.json")
            return res.status_code == 200
        except Exception:
            return False

    import urllib.parse

    async def get_search_comments(self, keyword: str, page: int = 1):
        """
        通过浏览器环境执行 fetch，复用当前 Playwright 页面和登录态。
        """
        import urllib.parse
        import json

        encoded_keyword = urllib.parse.quote(keyword)
        api_url = (
            f"https://xueqiu.com/query/v1/search/status.json"
            f"?q={encoded_keyword}&count=20&page={page}"
            f"&sort=relevance&source=all"
        )

        utils.logger.info(f"[XueQiuClient] 浏览器 fetch: {keyword} 第 {page} 页")

        try:
            # 优先使用 create_xq_client 传进来的页面
            page_obj = self.playwright_page

            # 如果没有 page，再从 browser_context 兜底取最后一个页面
            if page_obj is None:
                if not self.browser_context or not self.browser_context.pages:
                    utils.logger.error("[XueQiuClient] 没有可用的浏览器页面")
                    return {"list": []}
                page_obj = self.browser_context.pages[-1]

            script = f"""
            async () => {{
                const response = await fetch("{api_url}", {{
                    method: "GET",
                    credentials: "include",
                    headers: {{
                        "accept": "application/json, text/plain, */*",
                        "referer": "https://xueqiu.com/"
                    }}
                }});
                const text = await response.text();
                return {{
                    status: response.status,
                    url: response.url,
                    text: text
                }};
            }}
            """

            result = await page_obj.evaluate(script)
            status = result.get("status")
            content = result.get("text", "")

            utils.logger.info(
                f"[XueQiuClient] fetch status={status}, content前100字={content[:100]}"
            )

            if status != 200:
                utils.logger.error(f"[XueQiuClient] 请求失败 status={status}, 内容={content[:300]}")
                return {"list": []}

            if not content:
                utils.logger.error("[XueQiuClient] 返回内容为空")
                return {"list": []}

            if "aliyun_waf" in content or "captcha" in content.lower():
                utils.logger.error("[XueQiuClient] 触发 WAF/验证码，请手动刷新雪球页面")
                return {"list": []}

            data = json.loads(content)

            # 兼容不同返回结构
            if isinstance(data, dict):
                if "list" in data:
                    return data
                if "data" in data and isinstance(data["data"], dict) and "list" in data["data"]:
                    return {"list": data["data"]["list"]}
                if "statuses" in data:
                    return {"list": data["statuses"]}

            utils.logger.warning(f"[XueQiuClient] 未识别返回结构: {str(data)[:300]}")
            return {"list": []}

        except Exception as e:
            utils.logger.error(f"[XueQiuClient] 浏览器 fetch 请求失败: {e}")
            return {"list": []}

    async def get_comments_by_symbol(self, symbol: str, page: int = 1, count: int = 20) -> Dict:
        """
        获取特定股票的评论列表
        API: /query/v1/symbol/status/list.json
        """
        url = "https://xueqiu.com/query/v1/symbol/status/list.json"
        params = {
            "symbol": symbol,
            "count": count,
            "page": page,
            "source": "all",
            "comment": "0",
            "stype": "2"
        }
        
        utils.logger.info(f"[XueQiuClient.get_comments_by_symbol] 正在请求股票 {symbol} 第 {page} 页")
        response = await self.client.get(url, params=params)
        
        if response.status_code != 200:
            utils.logger.error(f"请求失败: {response.status_code}, 内容: {response.text}")
            return {"list": []}
            
        return response.json()

    async def search_info_by_keyword(self, keyword: str, page: int = 1, count: int = 20) -> Dict:
        """
        通过关键词搜索综合讨论内容
        API: /query/v1/search/status.json
        """
        url = "https://xueqiu.com/query/v1/search/status.json"
        params = {
            "q": keyword,      # 搜索关键词
            "count": count,
            "page": page,
            "sort": "relevance", # 关联度排序，也可以选 "time"
            "source": "all"
        }
        
        utils.logger.info(f"[XueQiuClient.search] 正在搜索关键词: {keyword}, 第 {page} 页")
        response = await self.client.get(url, params=params)
        
        if response.status_code != 200:
            utils.logger.error(f"搜索失败: {response.status_code}, 内容: {response.text}")
            return {"list": []}
            
        return response.json()

    async def batch_save_comments(self, items: list):
        """
        将抓取到的评论列表保存到本地 Excel，按年份分表
        """
        if not items:
            return

        processed_data = []
        for item in items:
            # 解析雪球的时间戳 (毫秒)
            created_at = item.get("created_at", 0)
            dt_object = datetime.fromtimestamp(created_at / 1000.0)
            year = dt_object.strftime('%Y')
            raw_desc = item.get("description", "")
            clean_desc = re.sub(r'<[^>]+>', '', raw_desc) if raw_desc else ""

            
            # 提取核心字段
            data = {
                "ID": item.get("id"),
                "股票代码": item.get("symbol"),
                "用户名": item.get("user", {}).get("screen_name"),
                "评论内容": clean_desc,
                "发布时间": dt_object.strftime('%Y-%m-%d %H:%M:%S'),
                "年份": year,
                "点赞数": item.get("like_count"),
                "回复数": item.get("reply_count"),
                "源链接": f"https://xueqiu.com{item.get('target')}"
            }
            processed_data.append(data)

        # 按照年份对数据进行分组保存
        df = pd.DataFrame(processed_data)
        years = df['年份'].unique()

        save_dir = "data/xueqiu"
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        for year in years:
            year_df = df[df['年份'] == year]
            file_path = os.path.join(save_dir, f"search_comments_{year}.xlsx")
            
            # 如果文件已存在，追加数据；否则新建
            if os.path.exists(file_path):
                with pd.ExcelWriter(file_path, mode='a', engine='openpyxl', if_sheet_exists='overlay') as writer:
                    # 读取已有数据，合并去重后再写入
                    existing_df = pd.read_excel(file_path)
                    final_df = pd.concat([existing_df, year_df]).drop_duplicates(subset=['ID'])
                    final_df.to_excel(writer, index=False, sheet_name='Comments')
            else:
                year_df.to_excel(file_path, index=False, sheet_name='Comments')
                
        utils.logger.info(f"[XueQiuCrawler] 已成功保存 {len(processed_data)} 条评论到 {save_dir}")