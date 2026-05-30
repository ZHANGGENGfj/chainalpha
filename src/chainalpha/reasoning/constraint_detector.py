from __future__ import annotations

from chainalpha.schemas import IndustryEvent, CausalHypothesis, stable_id
from chainalpha.ontology.industry_graph import IndustryGraph


EVENT_START_NODE_HINTS = {
    "CAPEX_INCREASE": ["AI CapEx"],
    "CAPEX_DECREASE": ["AI CapEx"],
    "SUPPLY_SHORTAGE": [],
    "TECH_UPGRADE": [],
    "CAPACITY_EXPANSION": [],
    "CUSTOMER_CERTIFICATION": [],
    "ORDER_SIGNED": [],
    "FINANCIAL_VALIDATION": [],
    "HIRING_SURGE": [],
    "POLICY_SUPPORT": ["AI CapEx"],
}


class ConstraintReasoner:
    def __init__(self, graph: IndustryGraph) -> None:
        self.graph = graph

    def build_hypothesis(self, event: IndustryEvent) -> CausalHypothesis:
        start_nodes = EVENT_START_NODE_HINTS.get(event.event_type, []) + event.supply_chain_nodes
        start_nodes = [n for n in start_nodes if n in self.graph.nodes]
        if not start_nodes:
            start_nodes = ["AI CapEx"]

        path = self.graph.causal_path_from(start_nodes, max_depth=5)
        if not path:
            path = start_nodes[:1]

        downstream = self.graph.expand(path[-1:], depth=2, direction="forward")
        bottlenecks = []
        if event.event_type in {"SUPPLY_SHORTAGE", "TECH_UPGRADE", "CAPACITY_EXPANSION"}:
            bottlenecks = list(dict.fromkeys(event.supply_chain_nodes + downstream))[:5]
        else:
            bottlenecks = list(dict.fromkeys(downstream + event.supply_chain_nodes))[:5]

        readable_path = " → ".join(path)
        hypothesis_text = (
            f"事件「{event.title}」可能意味着产业链约束沿着「{readable_path}」传导。"
            f" 当前需要重点验证的瓶颈节点包括：{', '.join(bottlenecks) if bottlenecks else '暂无明确节点'}。"
        )

        confidence = min(0.95, 0.45 + 0.25 * event.confidence + 0.05 * len(path) + 0.03 * len(bottlenecks))
        return CausalHypothesis(
            id=stable_id(event.id, readable_path),
            event_id=event.id,
            hypothesis=hypothesis_text,
            causal_path=path,
            bottleneck_nodes=bottlenecks,
            confidence=round(confidence, 3),
            evidence=event.evidence,
        )
