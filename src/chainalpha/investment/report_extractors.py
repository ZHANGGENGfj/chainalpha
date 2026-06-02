from __future__ import annotations

from pathlib import Path
import re
from typing import Any


FINANCIAL_KEYWORD_GROUPS: dict[str, list[str]] = {
    "financial_highlights": ["主要会计数据", "主要财务指标", "营业收入", "归属于上市公司股东的净利润", "扣除非经常性损益"],
    "business_model": ["主营业务", "主要业务", "主要产品", "产品结构", "业务概要"],
    "revenue_segments": ["分行业", "分产品", "分地区", "收入构成", "营业收入构成"],
    "management_discussion": ["管理层讨论与分析", "经营情况讨论与分析", "报告期内公司从事的主要业务"],
    "customers_suppliers": ["前五名客户", "前五名供应商", "客户集中度", "供应商集中度"],
    "cash_flow_quality": ["经营活动产生的现金流量净额", "应收账款", "存货", "在建工程", "资本开支"],
    "risks": ["风险因素", "可能面对的风险", "重大风险提示"],
}

BROKER_KEYWORD_GROUPS: dict[str, list[str]] = {
    "core_view": ["核心观点", "投资要点", "投资逻辑", "核心结论"],
    "rating": ["投资评级", "评级", "买入", "增持", "目标价"],
    "forecast": ["盈利预测", "业绩预测", "EPS", "归母净利润", "营业收入"],
    "valuation": ["估值", "PE", "PB", "PS", "DCF", "目标市值"],
    "industry_space": ["市场空间", "行业空间", "需求测算", "渗透率"],
    "risks": ["风险提示", "风险因素"],
}


def summarize_financial_report_document(doc: dict[str, Any], *, max_snippets_per_group: int = 3) -> dict[str, Any]:
    text = _doc_text(doc)
    return {
        "kind": "financial_report",
        "title": doc.get("title"),
        "source": doc.get("source"),
        "url": doc.get("url"),
        "path": doc.get("path") or (doc.get("metadata") or {}).get("pdf_cached_path"),
        "published_at": doc.get("published_at"),
        "page_count": (doc.get("metadata") or {}).get("pdf_page_count"),
        "extraction_note": "这是从公告/PDF文本中按关键词抽取的压缩摘要；页码和表格字段仍需用原文复核。",
        "numeric_facts": _extract_numeric_facts(text),
        "sections": _extract_keyword_sections(text, FINANCIAL_KEYWORD_GROUPS, max_snippets_per_group=max_snippets_per_group),
    }


def summarize_broker_report_text(
    *,
    title: str,
    text: str,
    source: str = "local_broker_report",
    path: str | None = None,
    max_snippets_per_group: int = 3,
) -> dict[str, Any]:
    return {
        "kind": "broker_report",
        "title": title,
        "source": source,
        "path": path,
        "extraction_note": "这是从券商研报/机构材料中按关键词抽取的观点摘要；评级、目标价、盈利预测需保留来源并交叉验证。",
        "rating_terms": _extract_rating_terms(text),
        "numeric_facts": _extract_numeric_facts(text),
        "sections": _extract_keyword_sections(text, BROKER_KEYWORD_GROUPS, max_snippets_per_group=max_snippets_per_group),
    }


def load_local_report_summaries(
    *,
    topic: str,
    financial_report_dir: str | Path | None = None,
    broker_report_dir: str | Path | None = None,
    max_files: int = 8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    financial_summaries: list[dict[str, Any]] = []
    broker_summaries: list[dict[str, Any]] = []
    terms = [term for term in re.split(r"[,，;；\s]+", topic) if term]

    for path in _iter_report_files(financial_report_dir, max_files=max_files):
        try:
            text = extract_text_from_file(path)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"financial report parse failed {path}: {type(exc).__name__}: {exc}")
            continue
        if terms and not _matches_terms(path.name + "\n" + text[:5000], terms):
            continue
        financial_summaries.append(
            summarize_financial_report_document(
                {
                    "title": path.stem,
                    "source": "local_financial_report",
                    "path": str(path),
                    "content": text,
                    "metadata": {"local_file": str(path)},
                }
            )
        )

    for path in _iter_report_files(broker_report_dir, max_files=max_files):
        try:
            text = extract_text_from_file(path)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"broker report parse failed {path}: {type(exc).__name__}: {exc}")
            continue
        if terms and not _matches_terms(path.name + "\n" + text[:5000], terms):
            continue
        broker_summaries.append(summarize_broker_report_text(title=path.stem, text=text, path=str(path)))

    return financial_summaries, broker_summaries, warnings


def extract_text_from_file(path: str | Path, *, max_chars: int = 180_000) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(p, max_chars=max_chars)
    if suffix in {".txt", ".md", ".json", ".jsonl", ".csv"}:
        return p.read_text(encoding="utf-8", errors="replace")[:max_chars]
    raise ValueError(f"unsupported report file type: {p.suffix}")


def extract_pdf_text(path: str | Path, *, max_chars: int = 180_000) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pypdf is required for PDF parsing.") from exc

    reader = PdfReader(str(path))
    parts: list[str] = []
    total = 0
    for page_no, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        clean = _normalize_text(text)
        if not clean:
            continue
        chunk = f"\n[page {page_no}]\n{clean}"
        parts.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    return "\n".join(parts)[:max_chars]


def _doc_text(doc: dict[str, Any]) -> str:
    content = str(doc.get("content") or "")
    if "=== PDF TEXT ===" in content:
        return content.split("=== PDF TEXT ===", 1)[1]
    return content


def _extract_keyword_sections(
    text: str,
    groups: dict[str, list[str]],
    *,
    max_snippets_per_group: int,
    window: int = 420,
) -> dict[str, list[dict[str, Any]]]:
    compact = _normalize_text(text)
    sections: dict[str, list[dict[str, Any]]] = {}
    for group, keywords in groups.items():
        snippets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for keyword in keywords:
            for match in re.finditer(re.escape(keyword), compact, flags=re.IGNORECASE):
                start = max(0, match.start() - window // 3)
                end = min(len(compact), match.end() + window)
                snippet = compact[start:end].strip()
                key = snippet[:80]
                if key in seen:
                    continue
                seen.add(key)
                snippets.append({"keyword": keyword, "text": snippet})
                if len(snippets) >= max_snippets_per_group:
                    break
            if len(snippets) >= max_snippets_per_group:
                break
        sections[group] = snippets
    return sections


def _extract_numeric_facts(text: str, max_items: int = 24) -> list[str]:
    compact = _normalize_text(text)
    patterns = [
        r"(营业收入[^。；\n]{0,80}(?:\d[\d,.]*\s*(?:亿元|万元|%)))",
        r"(归母净利润[^。；\n]{0,80}(?:\d[\d,.]*\s*(?:亿元|万元|%)))",
        r"(净利润[^。；\n]{0,80}(?:\d[\d,.]*\s*(?:亿元|万元|%)))",
        r"(毛利率[^。；\n]{0,80}\d+(?:\.\d+)?\s*%)",
        r"(资产负债率[^。；\n]{0,80}\d+(?:\.\d+)?\s*%)",
        r"(经营活动产生的现金流量净额[^。；\n]{0,80}(?:\d[\d,.]*\s*(?:亿元|万元|%)))",
        r"(EPS[^。；\n]{0,80}\d+(?:\.\d+)?)",
        r"(PE[^。；\n]{0,80}\d+(?:\.\d+)?)",
    ]
    facts: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, compact, flags=re.IGNORECASE):
            value = match.group(1).strip()
            if value in seen:
                continue
            seen.add(value)
            facts.append(value)
            if len(facts) >= max_items:
                return facts
    return facts


def _extract_rating_terms(text: str) -> list[str]:
    compact = _normalize_text(text)
    terms = []
    for pattern in [
        r"(投资评级[^。；\n]{0,40})",
        r"(评级[^。；\n]{0,30}(?:买入|增持|推荐|中性|持有|卖出))",
        r"(目标价[^。；\n]{0,40})",
    ]:
        for match in re.finditer(pattern, compact, flags=re.IGNORECASE):
            terms.append(match.group(1).strip())
            if len(terms) >= 8:
                return list(dict.fromkeys(terms))
    return list(dict.fromkeys(terms))


def _iter_report_files(root: str | Path | None, *, max_files: int) -> list[Path]:
    if root is None:
        return []
    p = Path(root).expanduser()
    if not p.exists():
        return []
    if p.is_file():
        return [p]
    files = [
        item
        for item in p.rglob("*")
        if item.is_file() and item.suffix.lower() in {".pdf", ".txt", ".md", ".json", ".jsonl", ".csv"}
    ]
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return files[:max_files]


def _matches_terms(text: str, terms: list[str]) -> bool:
    return any(term and term.lower() in text.lower() for term in terms)


def _normalize_text(text: str) -> str:
    lines = [line.strip() for line in str(text).splitlines()]
    return "\n".join(line for line in lines if line).strip()
