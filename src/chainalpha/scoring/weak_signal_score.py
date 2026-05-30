from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from chainalpha.schemas import IndustryEvent, CausalHypothesis, WeakSignal, stable_id


DEFAULT_SOURCE_RELIABILITY = {
    "official_announcement": 1.0,
    "financial_report": 0.95,
    "investor_relation": 0.9,
    "company_website": 0.85,
    "industry_research": 0.8,
    "mainstream_media": 0.7,
    "obsidian_note": 0.75,
    "local_file": 0.65,
    "social_media": 0.35,
    "connector_error": 0.0,
    "unknown": 0.55,
}

HOT_KEYWORDS = ["大涨", "暴涨", "涨停", "火爆", "热议", "刷屏", "炒作", "风口"]


@dataclass
class ScoreBreakdown:
    source_reliability: float
    event_novelty: float
    causal_strength: float
    supply_chain_importance: float
    financial_elasticity: float
    evidence_consistency: float
    low_attention_bonus: float
    pre_price_move_bonus: float
    risk_adjustment: float


class WeakSignalScorer:
    def __init__(self, source_reliability: dict[str, float] | None = None) -> None:
        self.source_reliability = source_reliability or DEFAULT_SOURCE_RELIABILITY

    @classmethod
    def from_yaml(cls, path: str | Path) -> "WeakSignalScorer":
        p = Path(path)
        if not p.exists():
            return cls()
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls(source_reliability=data.get("source_reliability") or DEFAULT_SOURCE_RELIABILITY)

    def classify_stage(self, event: IndustryEvent, text: str) -> str:
        if event.event_type == "ATTENTION_HOT" or any(k in text for k in HOT_KEYWORDS):
            return "S4"
        if event.event_type in {"FINANCIAL_VALIDATION", "GROSS_MARGIN_CHANGE", "INVENTORY_CHANGE"}:
            return "S3"
        if event.event_type in {"ORDER_SIGNED", "CUSTOMER_CERTIFICATION", "CAPACITY_EXPANSION", "SUPPLY_SHORTAGE"}:
            return "S2"
        if event.event_type in {"CAPEX_INCREASE", "TECH_UPGRADE", "PRODUCT_LAUNCH", "POLICY_SUPPORT", "STANDARD_RELEASE"}:
            return "S1"
        return "S0"

    def attention_score(self, event: IndustryEvent) -> float:
        text = " ".join([event.title, event.description, " ".join(event.evidence)])
        hot_hits = sum(1 for k in HOT_KEYWORDS if k in text)
        if event.event_type == "ATTENTION_HOT":
            hot_hits += 2
        return min(100.0, hot_hits * 28.0)

    def score(self, event: IndustryEvent, hypothesis: CausalHypothesis, source_type: str | None = None) -> WeakSignal:
        text = " ".join([event.title, event.description, " ".join(event.evidence)])
        stage = self.classify_stage(event, text)
        attention = self.attention_score(event)

        effective_source_type = source_type or event.source_type or "unknown"
        source_rel = self.source_reliability.get(effective_source_type, self.source_reliability.get("unknown", 0.55))
        event_novelty = 0.72 if stage in {"S0", "S1", "S2"} else 0.45
        causal_strength = min(1.0, 0.35 + 0.12 * len(hypothesis.causal_path) + 0.25 * hypothesis.confidence)
        supply_importance = min(1.0, 0.45 + 0.08 * len(hypothesis.bottleneck_nodes))
        financial_elasticity = 0.80 if any(n in text for n in ["光模块", "HBM", "CoWoS", "液冷", "电力", "硅光", "AI服务器"]) else 0.58
        evidence_consistency = min(1.0, 0.45 + 0.12 * len(event.evidence))
        low_attention_bonus = max(0.2, 1.0 - attention / 140.0)
        pre_price_move_bonus = 0.78 if stage in {"S0", "S1", "S2"} else 0.48
        risk_adjustment = 0.90 if stage != "S4" else 0.45

        raw = (
            source_rel * 0.14
            + event_novelty * 0.12
            + causal_strength * 0.18
            + supply_importance * 0.14
            + financial_elasticity * 0.12
            + evidence_consistency * 0.10
            + low_attention_bonus * 0.12
            + pre_price_move_bonus * 0.10
        ) * risk_adjustment
        score = round(max(0.0, min(100.0, raw * 100)), 2)

        reasons = [
            f"来源可信度={source_rel:.2f}",
            f"阶段={stage}",
            f"因果链长度={len(hypothesis.causal_path)}",
            f"关注度={attention:.1f}",
            f"瓶颈节点={', '.join(hypothesis.bottleneck_nodes[:4]) or '暂无'}",
        ]
        risks = []
        if attention > 50:
            risks.append("市场关注度偏高，可能已经进入交易拥挤阶段。")
        if event.confidence < 0.55:
            risks.append("事件抽取置信度较低，需要人工核验原文。")
        if stage == "S0":
            risks.append("仍处技术/概念萌芽阶段，商业兑现不确定。")
        if stage == "S3":
            risks.append("财务已经部分兑现，早期预期差可能收窄。")

        return WeakSignal(
            id=stable_id(event.id, hypothesis.id, stage),
            title=event.title,
            stage=stage,
            weak_signal_score=score,
            attention_score=round(attention, 2),
            event=event,
            hypothesis=hypothesis,
            reasons=reasons,
            risks=risks,
        )
