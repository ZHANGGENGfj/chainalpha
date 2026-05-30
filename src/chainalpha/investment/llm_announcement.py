from __future__ import annotations

from chainalpha.config import load_settings
from chainalpha.nlp.llm_client import LLMClient
from chainalpha.schemas import AnnouncementInsight


SYSTEM_PROMPT = """You are an investment research assistant.
Read a company announcement or company news item and return strict JSON.
Do not invent facts that are not in the text. Focus on catalysts, risks,
financial implications, and questions an analyst should verify."""


def analyze_announcement_with_llm(title: str, content: str, company: str | None = None) -> AnnouncementInsight:
    settings = load_settings()
    client = LLMClient(settings)
    user = f"""
Return this JSON shape:
{{
  "company": "...",
  "summary": "...",
  "event_type": "order|earnings|capacity|product|customer|risk|policy|other",
  "sentiment": "positive|negative|mixed|neutral",
  "catalysts": [],
  "risks": [],
  "evidence": [],
  "questions": [],
  "confidence": 0.0
}}

Company hint: {company or ""}
Title: {title}
Text:
{content[:6000]}
"""
    data = client.chat_json(SYSTEM_PROMPT, user)
    return AnnouncementInsight(
        title=title,
        company=str(data.get("company") or company or ""),
        summary=str(data.get("summary") or ""),
        event_type=str(data.get("event_type") or "other"),
        sentiment=str(data.get("sentiment") or "neutral"),
        catalysts=[str(x) for x in data.get("catalysts", [])],
        risks=[str(x) for x in data.get("risks", [])],
        evidence=[str(x) for x in data.get("evidence", [])],
        questions=[str(x) for x in data.get("questions", [])],
        confidence=float(data.get("confidence") or 0.5),
    )

