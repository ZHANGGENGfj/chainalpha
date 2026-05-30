from __future__ import annotations

from chainalpha.schemas import Chunk, IndustryEvent, stable_id
from chainalpha.ontology.industry_graph import IndustryGraph
from chainalpha.nlp.llm_client import LLMClient


SYSTEM_PROMPT = """你是产业链研究事件抽取器。
请从文本中抽取结构化产业事件。只输出 JSON。
事件类型只允许：
CAPEX_INCREASE, CAPEX_DECREASE, PRODUCT_LAUNCH, TECH_UPGRADE, CAPACITY_EXPANSION,
CUSTOMER_CERTIFICATION, ORDER_SIGNED, PRICE_INCREASE, PRICE_DECREASE, SUPPLY_SHORTAGE,
INVENTORY_CHANGE, GROSS_MARGIN_CHANGE, POLICY_SUPPORT, STANDARD_RELEASE, PATENT_FILED,
HIRING_SURGE, MANAGEMENT_GUIDANCE, FINANCIAL_VALIDATION, ATTENTION_HOT。
不要编造文本中没有的事实。
"""


class LLMEventExtractor:
    def __init__(self, graph: IndustryGraph, client: LLMClient) -> None:
        self.graph = graph
        self.client = client

    def extract_from_chunk(self, chunk: Chunk) -> list[IndustryEvent]:
        user = f"""
请抽取产业事件，返回格式：
{{
  "events": [
    {{
      "event_type": "...",
      "title": "...",
      "description": "...",
      "companies": [],
      "industries": [],
      "products": [],
      "technologies": [],
      "direction": "positive|negative|constraint|validation|crowded|unknown",
      "magnitude": "low|medium|high",
      "evidence": ["原文证据短句"],
      "confidence": 0.0
    }}
  ]
}}

文档标题：{chunk.title}
章节：{chunk.heading or ""}
文本：
{chunk.text[:3500]}
"""
        data = self.client.chat_json(SYSTEM_PROMPT, user)
        events = []
        for item in data.get("events", []):
            desc = str(item.get("description") or "")[:600]
            evidence = item.get("evidence") or [desc]
            nodes = self.graph.match_nodes(" ".join([
                desc, str(item.get("title", "")), " ".join(item.get("products", [])), " ".join(item.get("technologies", []))
            ]))
            events.append(IndustryEvent(
                id=stable_id(chunk.id, str(item.get("event_type")), desc[:80]),
                event_type=str(item.get("event_type") or "UNKNOWN"),
                title=str(item.get("title") or chunk.title),
                description=desc,
                source_doc_id=chunk.document_id,
                source_path=chunk.path,
                companies=[str(x) for x in item.get("companies", [])],
                industries=[str(x) for x in item.get("industries", [])],
                products=[str(x) for x in item.get("products", [])],
                technologies=[str(x) for x in item.get("technologies", [])],
                supply_chain_nodes=nodes,
                direction=str(item.get("direction") or "unknown"),
                magnitude=str(item.get("magnitude") or "medium"),
                evidence=[str(x) for x in evidence],
                confidence=float(item.get("confidence") or 0.5),
            ))
        return events
