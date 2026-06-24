from __future__ import annotations

import json
from typing import Any

from chainalpha.config import load_settings
from chainalpha.nlp.llm_client import LLMClient
from chainalpha.schemas import CompanyProfile


def generate_company_profiles_with_llm(
    topic: str,
    *,
    query_expansion: dict[str, Any] | None = None,
    max_profiles: int = 8,
) -> list[CompanyProfile]:
    """Generate company profiles from the LLM instead of matching local maps."""

    settings = load_settings()
    if not settings.llm_enabled:
        return []

    client = LLMClient(settings)
    payload = {
        "topic": topic,
        "query_expansion": query_expansion or {},
        "max_profiles": max_profiles,
        "rules": [
            "Return only companies that are directly relevant to the user's topic.",
            "If the topic asks what a material/component is, return related listed companies only when they are commonly associated with that industry chain.",
            "Do not invent ticker codes. Leave ticker empty when uncertain.",
            "Use concise Chinese fields.",
            "Mark uncertain items in metadata.verification_status as 待验证.",
        ],
        "schema": {
            "companies": [
                {
                    "name": "company short name",
                    "ticker": "optional, e.g. 600176.SH",
                    "exchange": "optional",
                    "industry": "optional",
                    "nodes": ["supply-chain/product nodes"],
                    "aliases": ["optional aliases"],
                    "role": "one-sentence role in this topic",
                    "watch_tags": ["tracking tags"],
                    "confidence": 0.0,
                    "reason": "why relevant to the topic",
                }
            ]
        },
    }
    data = client.chat_json(
        system=(
            "你是A股产业链公司画像助手。你只负责生成候选公司画像，不写研报。"
            "必须返回JSON，不要输出解释。不要编造股票代码；不确定就留空。"
        ),
        user=json.dumps(payload, ensure_ascii=False),
        timeout=45,
    )
    rows = data.get("companies") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []
    profiles: list[CompanyProfile] = []
    for row in rows[:max_profiles]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        confidence = _float(row.get("confidence"), default=0.5)
        profiles.append(
            CompanyProfile(
                name=name,
                ticker=_clean_optional(row.get("ticker")),
                exchange=_clean_optional(row.get("exchange")),
                industry=_clean_optional(row.get("industry")),
                nodes=_list(row.get("nodes")),
                aliases=_list(row.get("aliases")),
                role=str(row.get("role") or row.get("reason") or "").strip(),
                watch_tags=_list(row.get("watch_tags")),
                metadata={
                    "resolver": "llm_company_profiler",
                    "confidence": confidence,
                    "verification_status": "待验证",
                    "reason": str(row.get("reason") or "").strip(),
                    "source": "LLM generated profile; requires announcement/filing/news verification.",
                },
            )
        )
    return _dedup_profiles(profiles)


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:12]


def _clean_optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedup_profiles(profiles: list[CompanyProfile]) -> list[CompanyProfile]:
    seen: set[str] = set()
    out: list[CompanyProfile] = []
    for profile in profiles:
        key = (profile.ticker or profile.name).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(profile)
    return out
