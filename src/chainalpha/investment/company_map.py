from __future__ import annotations

from pathlib import Path
from difflib import SequenceMatcher
from typing import Any

import yaml

from chainalpha.schemas import CompanyProfile


NODE_ALIASES = {
    "ai capex": "AI CapEx",
    "ai资本开支": "AI CapEx",
    "数据中心": "AI Data Center",
    "ai数据中心": "AI Data Center",
    "智算中心": "AI Data Center",
    "电力": "Power Grid",
    "电网": "Power Grid",
    "ups": "Power Grid",
    "pdu": "Power Grid",
    "液冷": "Liquid Cooling",
    "散热": "Liquid Cooling",
    "cdu": "Liquid Cooling",
    "gpu集群": "GPU Cluster",
    "算力集群": "GPU Cluster",
    "hbm": "HBM",
    "先进封装": "Advanced Packaging",
    "cowos": "Advanced Packaging",
    "ai服务器": "AI Server",
    "服务器": "AI Server",
    "交换机": "Network Switch",
    "交换芯片": "Network Switch",
    "光模块": "Optical Module",
    "800g": "Optical Module",
    "1.6t": "Optical Module",
    "激光器": "Laser",
    "eml": "Laser",
    "硅光": "Silicon Photonics",
    "cpo": "Silicon Photonics",
    "lpo": "Silicon Photonics",
    "模型api": "Model API",
    "企业ai": "Enterprise AI",
    "agent": "Enterprise AI",
    "rag": "Enterprise AI",
    "ai安全": "AI Security",
}


def load_company_profiles(path: str | Path) -> list[CompanyProfile]:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return []

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw_companies = data.get("companies", [])
    profiles: list[CompanyProfile] = []

    if isinstance(raw_companies, dict):
        items = [{"name": name, **(value or {})} for name, value in raw_companies.items()]
    elif isinstance(raw_companies, list):
        items = [item for item in raw_companies if isinstance(item, dict)]
    else:
        items = []

    for item in items:
        profiles.append(_profile_from_mapping(item))

    return profiles


def _profile_from_mapping(item: dict[str, Any]) -> CompanyProfile:
    def list_of_str(key: str) -> list[str]:
        value = item.get(key) or []
        if isinstance(value, str):
            return [value]
        return [str(x) for x in value]

    return CompanyProfile(
        name=str(item.get("name") or item.get("company") or "").strip(),
        ticker=str(item["ticker"]).strip() if item.get("ticker") else None,
        exchange=str(item["exchange"]).strip() if item.get("exchange") else None,
        industry=str(item["industry"]).strip() if item.get("industry") else None,
        nodes=canonicalize_nodes(list_of_str("nodes")),
        aliases=list_of_str("aliases"),
        role=str(item.get("role") or ""),
        watch_tags=list_of_str("watch_tags"),
        metadata={k: v for k, v in item.items() if k not in {
            "name",
            "company",
            "ticker",
            "exchange",
            "industry",
            "nodes",
            "aliases",
            "role",
            "watch_tags",
        }},
    )


def match_signal_companies(signal_text: str, nodes: list[str], profiles: list[CompanyProfile]) -> list[CompanyProfile]:
    matched: list[CompanyProfile] = []
    text = signal_text.lower()
    node_set = set(canonicalize_nodes(nodes))

    for profile in profiles:
        company_terms = [profile.name, *(profile.aliases or []), profile.ticker or ""]
        by_name = any(term and term.lower() in text for term in company_terms)
        by_node = bool(node_set & set(canonicalize_nodes(profile.nodes)))
        if by_name or by_node:
            matched.append(profile)

    return matched


def canonicalize_node(node: str) -> str:
    raw = str(node or "").strip()
    if not raw:
        return ""
    return NODE_ALIASES.get(raw.lower(), raw)


def canonicalize_nodes(nodes: list[str]) -> list[str]:
    return list(dict.fromkeys(canonicalize_node(node) for node in nodes if canonicalize_node(node)))


def search_company_profiles(
    profiles: list[CompanyProfile],
    query: str,
    top_k: int = 20,
) -> list[tuple[float, CompanyProfile]]:
    q = query.strip().lower()
    q_node = canonicalize_node(q)
    scored: list[tuple[float, CompanyProfile]] = []

    for profile in profiles:
        fields = [
            profile.name,
            profile.ticker or "",
            profile.exchange or "",
            profile.industry or "",
            profile.role,
            " ".join(profile.nodes),
            " ".join(profile.aliases),
            " ".join(profile.watch_tags),
        ]
        haystack = " ".join(fields).lower()
        exact = 1.0 if q and q in haystack else 0.0
        node_hit = 1.0 if q_node and q_node in canonicalize_nodes(profile.nodes) else 0.0
        fuzzy = max(SequenceMatcher(None, q, field.lower()).ratio() for field in fields if field)
        score = max(exact, node_hit, fuzzy)
        if score > 0.2:
            scored.append((round(score, 4), profile))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:top_k]
