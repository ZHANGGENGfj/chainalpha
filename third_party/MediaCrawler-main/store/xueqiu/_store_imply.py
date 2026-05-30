# -*- coding: utf-8 -*-
# @Author    : Gemini-Assistant
# @Desc      : XueQiu storage implementation class
import os
from typing import Dict
from base.base_crawler import AbstractStore
from tools import utils
from var import crawler_type_var
from tools.async_file_writer import AsyncFileWriter
from database.mongodb_store_base import MongoDBStoreBase
import config

class XueQiuCsvStoreImplement(AbstractStore):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 初始化异步文件写入器，指定平台为 xueqiu
        self.writer = AsyncFileWriter(platform="xueqiu", crawler_type=crawler_type_var.get())

    async def store_content(self, content_item: Dict):
        """雪球动态/帖子 CSV 存储"""
        await self.writer.write_to_csv(item_type="contents", item=content_item)

    async def store_comment(self, comment_item: Dict):
        """雪球评论 CSV 存储"""
        await self.writer.write_to_csv(item_type="comments", item=comment_item)

    async def store_creator(self, creator: Dict):
        """雪球用户信息 CSV 存储"""
        await self.writer.write_to_csv(item_type="creators", item=creator)


class XueQiuJsonStoreImplement(AbstractStore):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.writer = AsyncFileWriter(platform="xueqiu", crawler_type=crawler_type_var.get())

    async def store_content(self, content_item: Dict):
        """JSON 格式存储动态"""
        await self.writer.write_single_item_to_json(item_type="contents", item=content_item)

    async def store_comment(self, comment_item: Dict):
        """JSON 格式存储评论"""
        await self.writer.write_single_item_to_json(item_type="comments", item=comment_item)

    async def store_creator(self, creator: Dict):
        """JSON 格式存储用户"""
        await self.writer.write_single_item_to_json(item_type="creators", item=creator)


class XueQiuMongoStoreImplement(AbstractStore):
    """雪球 MongoDB 存储实现"""
    def __init__(self):
        self.mongo_store = MongoDBStoreBase(collection_prefix="xueqiu")

    async def store_content(self, content_item: Dict):
        content_id = content_item.get("id") or content_item.get("content_id")
        if not content_id:
            return
        await self.mongo_store.save_or_update(
            collection_suffix="contents",
            query={"id": content_id},
            data=content_item
        )
        utils.logger.info(f"[XueQiuMongoStore] Saved content {content_id} to MongoDB")

    async def store_comment(self, comment_item: Dict):
        comment_id = comment_item.get("id") or comment_item.get("comment_id")
        if not comment_id:
            return
        await self.mongo_store.save_or_update(
            collection_suffix="comments",
            query={"id": comment_id},
            data=comment_item
        )

    async def store_creator(self, creator_item: Dict):
        user_id = creator_item.get("user_id") or creator_item.get("id")
        if not user_id:
            return
        await self.mongo_store.save_or_update(
            collection_suffix="creators",
            query={"user_id": user_id},
            data=creator_item
        )


class XueQiuExcelStoreImplement:
    """雪球 Excel 存储实现 - 全局单例"""
    def __new__(cls, *args, **kwargs):
        from store.excel_store_base import ExcelStoreBase
        # 复用框架自带的 ExcelStoreBase，自动处理单年单文件逻辑
        return ExcelStoreBase.get_instance(
            platform="xueqiu",
            crawler_type=crawler_type_var.get()
        )

# ---------------------------------------------------------
# 工厂函数，用于在入口处快速实例化对应的存储方式
# ---------------------------------------------------------
async def update_xueqiu_note(content_item: Dict):
    """供 Crawler 调用的入口函数：保存动态/帖子"""
    store_type = config.STORE_TYPE
    if store_type == "csv":
        await XueQiuCsvStoreImplement().store_content(content_item)
    elif store_type == "json":
        await XueQiuJsonStoreImplement().store_content(content_item)
    elif store_type == "mongo":
        await XueQiuMongoStoreImplement().store_content(content_item)
    elif store_type == "excel":
        await XueQiuExcelStoreImplement().store_content(content_item)

async def batch_update_xueqiu_comments(comment_items: list):
    """批量保存评论"""
    for item in comment_items:
        store_type = config.STORE_TYPE
        if store_type == "csv":
            await XueQiuCsvStoreImplement().store_comment(item)
        elif store_type == "excel":
            await XueQiuExcelStoreImplement().store_comment(item)
        # ... 可以继续补充其他存储类型