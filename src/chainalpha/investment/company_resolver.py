from __future__ import annotations

from typing import Any

from chainalpha.schemas import CompanyProfile


def resolve_query_profiles(query: str, seed_profiles: list[CompanyProfile] | None = None, top_k: int = 8) -> list[CompanyProfile]:
    seed_profiles = seed_profiles or []
    resolved = [profile for profile in seed_profiles if _profile_visible_in_query(query, profile)]
    seen = {_profile_key(profile) for profile in resolved}

    for profile in _resolve_with_akshare(query, top_k=top_k):
        key = _profile_key(profile)
        if key in seen:
            continue
        resolved.append(profile)
        seen.add(key)
    if not resolved:
        fallback = _fallback_profile_for_code(query)
        if fallback is not None:
            resolved.append(fallback)
    return resolved


def _profile_visible_in_query(query: str, profile: CompanyProfile) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return False
    terms = [profile.name, profile.ticker or "", (profile.ticker or "").split(".")[0], *(profile.aliases or [])]
    normalized = [str(term).strip().lower() for term in terms if str(term).strip()]
    normalized.sort(key=len, reverse=True)
    return any(len(term) >= 2 and term in q for term in normalized)


def _resolve_with_akshare(query: str, top_k: int) -> list[CompanyProfile]:
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        return []

    try:
        df = ak.stock_info_a_code_name()
    except Exception:
        return []

    text = query.strip()
    if not text:
        return []

    code = _normalize_ticker_query(text)
    matched = df
    if code:
        code_series = df["code"].astype(str).str.zfill(6)
        matched = df[code_series == code]
    else:
        name_series = df["name"].astype(str)
        matched = df[name_series.str.contains(text, case=False, na=False)]

    if getattr(matched, "empty", True):
        return []

    rows = matched.head(top_k).to_dict(orient="records")
    return [_profile_from_akshare_row(row) for row in rows]


def _profile_from_akshare_row(row: dict[str, Any]) -> CompanyProfile:
    code = str(row.get("code") or "").zfill(6)
    name = str(row.get("name") or "").strip() or code
    ticker = _to_ticker(code)
    exchange = "SSE" if ticker.endswith(".SH") else "SZSE"
    return CompanyProfile(
        name=name,
        ticker=ticker,
        exchange=exchange,
        aliases=[],
        industry=None,
        nodes=[],
        role="Resolved from AKShare A-share code-name directory",
        watch_tags=[],
        metadata={"resolver": "akshare.stock_info_a_code_name"},
    )


def _normalize_ticker_query(text: str) -> str | None:
    raw = text.strip().lower().replace(".", "")
    if raw.startswith(("sh", "sz", "bj")):
        raw = raw[2:]
    if raw.isdigit() and len(raw) == 6:
        return raw
    return None


def _to_ticker(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _profile_key(profile: CompanyProfile) -> str:
    return (profile.ticker or profile.name).strip().lower()


def _fallback_profile_for_code(query: str) -> CompanyProfile | None:
    code = _normalize_ticker_query(query)
    if not code:
        return None
    ticker = _to_ticker(code)
    exchange = "SSE" if ticker.endswith(".SH") else "SZSE"
    return CompanyProfile(
        name=code,
        ticker=ticker,
        exchange=exchange,
        aliases=[],
        industry=None,
        nodes=[],
        role="Resolved from ticker pattern without external directory",
        watch_tags=[],
        metadata={"resolver": "ticker_fallback"},
    )
