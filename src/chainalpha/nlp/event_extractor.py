from __future__ import annotations

from collections import defaultdict

from chainalpha.schemas import Chunk, IndustryEvent, stable_id
from chainalpha.ontology.industry_graph import IndustryGraph
from chainalpha.utils.text import extract_snippet


EVENT_RULES: dict[str, list[str]] = {
    "CAPEX_INCREASE": ["资本开支", "capex", "投资", "上调", "扩建", "建设", "采购", "数据中心"],
    "TECH_UPGRADE": ["发布", "推出", "升级", "迭代", "量产", "800G", "1.6T", "3.2T", "LPO", "CPO", "硅光", "HBM4"],
    "SUPPLY_SHORTAGE": ["短缺", "紧张", "瓶颈", "交期", "供不应求", "涨价", "产能不足"],
    "CAPACITY_EXPANSION": ["扩产", "产线", "产能", "新增产能", "投产", "厂房", "设备订单"],
    "CUSTOMER_CERTIFICATION": ["认证", "送样", "验证", "导入", "进入供应链", "客户通过", "小批量"],
    "ORDER_SIGNED": ["订单", "中标", "合同", "长协", "采购协议", "框架协议"],
    "FINANCIAL_VALIDATION": ["营收", "收入", "毛利率", "净利润", "库存", "合同负债", "在建工程", "研发费用"],
    "HIRING_SURGE": ["招聘", "岗位", "扩招", "人才", "工程师"],
    "POLICY_SUPPORT": ["政策", "规划", "补贴", "专项", "行动方案", "指导意见"],
    "ATTENTION_HOT": ["大涨", "暴涨", "涨停", "热议", "刷屏", "火爆", "风口"],
}


DIRECTION_MAP = {
    "CAPEX_INCREASE": "positive",
    "TECH_UPGRADE": "positive",
    "SUPPLY_SHORTAGE": "constraint",
    "CAPACITY_EXPANSION": "positive",
    "CUSTOMER_CERTIFICATION": "positive",
    "ORDER_SIGNED": "positive",
    "FINANCIAL_VALIDATION": "validation",
    "HIRING_SURGE": "early_positive",
    "POLICY_SUPPORT": "positive",
    "ATTENTION_HOT": "crowded",
}


class RuleEventExtractor:
    def __init__(self, graph: IndustryGraph) -> None:
        self.graph = graph

    def extract_from_chunk(self, chunk: Chunk) -> list[IndustryEvent]:
        text = "\n".join([chunk.title, chunk.heading or "", chunk.text])
        matched_nodes = self.graph.match_nodes(text)
        events: list[IndustryEvent] = []

        for event_type, keywords in EVENT_RULES.items():
            hits = [k for k in keywords if k.lower() in text.lower()]
            if not hits:
                continue

            snippet = extract_snippet(text, hits)
            confidence = min(0.95, 0.42 + 0.08 * len(hits) + 0.05 * len(matched_nodes))
            if event_type == "ATTENTION_HOT":
                confidence = min(confidence, 0.7)

            title = f"{event_type}: {chunk.title}"
            if chunk.heading:
                title += f" / {chunk.heading}"

            event = IndustryEvent(
                id=stable_id(chunk.id, event_type, snippet[:80]),
                event_type=event_type,
                title=title,
                description=snippet,
                source_doc_id=chunk.document_id,
                source_path=chunk.path,
                source=chunk.source,
                source_type=chunk.source_type,
                supply_chain_nodes=matched_nodes,
                direction=DIRECTION_MAP.get(event_type, "unknown"),
                magnitude="high" if len(hits) >= 3 else "medium",
                evidence=[snippet],
                confidence=round(confidence, 3),
            )
            events.append(event)
        return events

    def extract(self, chunks: list[Chunk]) -> list[IndustryEvent]:
        dedup = {}
        for chunk in chunks:
            for event in self.extract_from_chunk(chunk):
                # Deduplicate weakly by doc/path/type/nodes.
                key = (event.source_path, event.event_type, tuple(event.supply_chain_nodes[:4]), event.description[:80])
                if key not in dedup or dedup[key].confidence < event.confidence:
                    dedup[key] = event
        return list(dedup.values())
