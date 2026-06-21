from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import json
import re
import textwrap
from pathlib import Path
from typing import Any


@dataclass
class ChainVisualItem:
    title: str
    usage: str = "数据缺口：需要补充用途证据"
    bottleneck: str = "数据缺口：需要验证卡点"
    companies: list[str] = field(default_factory=list)
    evidence_level: str = "待验证"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "usage": self.usage,
            "bottleneck": self.bottleneck,
            "companies": self.companies,
            "evidence_level": self.evidence_level,
        }


@dataclass
class ChainVisualStage:
    name: str
    description: str
    items: list[ChainVisualItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass
class ChainVisualData:
    title: str
    subtitle: str
    stages: list[ChainVisualStage]
    process_flow: list[str]
    evidence_note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "stages": [stage.to_dict() for stage in self.stages],
            "process_flow": self.process_flow,
            "evidence_note": self.evidence_note,
        }


def build_chain_visual_data(topic_report: dict[str, Any], agent_report: dict[str, Any] | None = None) -> ChainVisualData:
    """Build a deterministic upstream-to-downstream visual data pack.

    The visual is intentionally conservative: company names and bottlenecks are
    marked as "待验证" unless they can be inferred from the existing structured
    evidence. This keeps the infographic aligned with the report's evidence
    discipline.
    """

    query_expansion = topic_report.get("query_expansion") or {}
    topic = str(topic_report.get("topic") or query_expansion.get("corrected_query") or "产业链")
    subject = _primary_subject(query_expansion, topic)
    product_terms = _unique([str(item) for item in query_expansion.get("product_terms") or []])
    keyword_terms = _unique([str(item) for item in query_expansion.get("keywords") or []])
    doc_text = _evidence_text(topic_report)

    if _is_material_chain(query_expansion):
        stages = _material_chain_stages(subject, product_terms, keyword_terms, doc_text)
        flow = _material_process_flow(subject, product_terms)
    else:
        stages = _generic_chain_stages(topic_report, subject, doc_text)
        flow = _generic_process_flow(stages)

    return ChainVisualData(
        title=f"{subject}产业链流水线图谱",
        subtitle="源头资源 → 上游材料 → 中游制造 → 下游应用 → 终端需求",
        stages=stages,
        process_flow=flow,
        evidence_note="图中公司与卡点为投研线索；未被公告、财报或研报直接验证的内容均应视为待验证。",
    )


def save_chain_visual_outputs(visual_data: ChainVisualData, out_dir: str | Path) -> dict[str, str]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    json_path = out_path / "chain_visual.json"
    svg_path = out_path / "chain_visual.svg"
    json_path.write_text(json.dumps(visual_data.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    svg_path.write_text(render_chain_visual_svg(visual_data), encoding="utf-8")
    return {"json": str(json_path), "svg": str(svg_path)}


def render_chain_visual_markdown(svg_path: str | Path) -> str:
    path = Path(svg_path)
    return f"![产业链流水线图谱]({path.name})\n"


def render_chain_visual_svg(data: ChainVisualData) -> str:
    width = 1440
    stage_width = 330
    gap = 22
    left = 38
    top = 168
    card_width = stage_width - 34
    card_height = 178
    stage_heights = [116 + len(stage.items[:4]) * (card_height + 18) for stage in data.stages]
    chart_height = max(stage_heights or [560])
    flow_top = top + chart_height + 48
    note_top = flow_top + 156
    height = note_top + 72

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<defs>",
        '<linearGradient id="header" x1="0" y1="0" x2="1" y2="0">',
        '<stop offset="0%" stop-color="#0f3d6e"/>',
        '<stop offset="56%" stop-color="#155e75"/>',
        '<stop offset="100%" stop-color="#0f766e"/>',
        "</linearGradient>",
        '<pattern id="grid" width="28" height="28" patternUnits="userSpaceOnUse">',
        '<path d="M 28 0 L 0 0 0 28" fill="none" stroke="#dce6ef" stroke-width="0.7" opacity="0.45"/>',
        "</pattern>",
        '<filter id="shadow" x="-10%" y="-10%" width="120%" height="130%">',
        '<feDropShadow dx="0" dy="10" stdDeviation="12" flood-color="#0f172a" flood-opacity="0.13"/>',
        "</filter>",
        "</defs>",
        '<rect width="100%" height="100%" fill="#f4f7fb"/>',
        '<rect width="100%" height="100%" fill="url(#grid)"/>',
        '<rect x="0" y="0" width="1440" height="124" fill="url(#header)"/>',
        '<rect x="34" y="18" width="1372" height="88" rx="18" fill="#ffffff" opacity="0.08"/>',
        _svg_text(data.title, 46, 76, 42, "#ffffff", weight=800),
        _svg_text(data.subtitle, 48, 112, 20, "#d9f2ef", weight=500),
    ]

    for idx, stage in enumerate(data.stages[:4], 1):
        x = left + (idx - 1) * (stage_width + gap)
        parts.extend(_stage_svg(stage, idx, x, top, stage_width, card_width, card_height))
        if idx < min(len(data.stages), 4):
            arrow_x = x + stage_width + 4
            arrow_y = top + 52
            parts.append(_arrow(arrow_x, arrow_y, arrow_x + gap - 8, arrow_y, "#1f6f9f"))

    parts.extend(_flow_svg(data.process_flow, left, flow_top, width - 2 * left))
    parts.extend(_note_svg(data.evidence_note, left, note_top, width - 2 * left))
    parts.append("</svg>")
    return "\n".join(parts)


def _stage_svg(
    stage: ChainVisualStage,
    idx: int,
    x: int,
    y: int,
    stage_width: int,
    card_width: int,
    card_height: int,
) -> list[str]:
    color = _stage_color(idx)
    stage_height = 92 + len(stage.items[:4]) * (card_height + 18)
    parts: list[str] = [
        f'<rect x="{x}" y="{y}" width="{stage_width}" height="{stage_height}" rx="18" fill="#ffffff" stroke="#d7e0ea" filter="url(#shadow)"/>',
        f'<rect x="{x}" y="{y}" width="{stage_width}" height="7" rx="4" fill="{color}"/>',
        f'<circle cx="{x + 34}" cy="{y + 36}" r="19" fill="{color}"/>',
        _svg_text(str(idx), x + 34, y + 41, 21, "#ffffff", anchor="middle", weight=800),
        _svg_text(stage.name, x + 62, y + 38, 23, color, weight=800),
        _svg_text(stage.description, x + 22, y + 70, 15, "#52616f"),
    ]
    card_y = y + 94
    for item in stage.items[:4]:
        parts.extend(_card_svg(item, x + 17, card_y, card_width, card_height, color))
        card_y += card_height + 18
    return parts


def _card_svg(item: ChainVisualItem, x: int, y: int, width: int, height: int, accent: str) -> list[str]:
    companies = "、".join(item.companies[:5]) if item.companies else "待验证"
    parts = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="12" fill="#fbfdff" stroke="#c7d4e2"/>',
        f'<rect x="{x}" y="{y}" width="6" height="{height}" rx="3" fill="{accent}"/>',
        _svg_text(item.title, x + 16, y + 30, 20, "#102a43", weight=800),
        *_evidence_badge(item.evidence_level, x + width - 90, y + 13),
    ]
    parts.extend(_field_svg("用途", item.usage, x + 16, y + 58))
    parts.extend(_field_svg("卡点", item.bottleneck, x + 16, y + 92))
    parts.extend(_company_chip_svg(companies, x + 16, y + 132, width - 32, accent))
    return parts


def _field_svg(label: str, value: str, x: int, y: int) -> list[str]:
    parts = [_svg_text(f"{label}：", x, y, 15, "#0f766e", weight=700)]
    for offset, text in enumerate(_wrap_text(value, 20)[:2]):
        parts.append(_svg_text(text, x + 50, y + offset * 18, 15, "#243b53"))
    return parts


def _company_chip_svg(companies: str, x: int, y: int, width: int, accent: str) -> list[str]:
    wrapped = _wrap_text(companies, 22)
    chip_height = 26 + max(0, len(wrapped[:2]) - 1) * 16
    parts = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{chip_height}" rx="9" fill="#eef7ff" stroke="#d5e5f3"/>',
        _svg_text("公司", x + 12, y + 18, 13, accent, weight=800),
    ]
    for offset, text in enumerate(wrapped[:2]):
        parts.append(_svg_text(text, x + 50, y + 18 + offset * 16, 13, "#243b53", weight=700))
    return parts


def _evidence_badge(level: str, x: int, y: int) -> list[str]:
    fill, color = _evidence_colors(level)
    return [
        f'<rect x="{x}" y="{y}" width="76" height="24" rx="12" fill="{fill}" stroke="{color}" opacity="0.98"/>',
        _svg_text(level, x + 38, y + 17, 12, color, anchor="middle", weight=800),
    ]


def _flow_svg(flow: list[str], x: int, y: int, width: int) -> list[str]:
    items = flow[:9] or ["源头", "上游", "中游", "下游", "终端"]
    n = len(items)
    inner_x = x + 56
    inner_width = width - 112
    step = inner_width / max(n - 1, 1) if n > 1 else inner_width
    axis_y = y + 76
    parts = [
        f'<rect x="{x}" y="{y}" width="{width}" height="140" rx="18" fill="#ffffff" stroke="#d7e0ea" filter="url(#shadow)"/>',
        f'<rect x="{x}" y="{y}" width="{width}" height="7" rx="4" fill="#155e75"/>',
        _svg_text("工艺/价值传导流水线", x + 24, y + 34, 22, "#0f3d6e", weight=800),
    ]
    if n > 1:
        parts.append(
            f'<line x1="{inner_x}" y1="{axis_y}" x2="{inner_x + inner_width}" y2="{axis_y}" '
            'stroke="#b8c7d6" stroke-width="4" stroke-linecap="round"/>'
        )
    for idx, item in enumerate(items):
        cx = inner_x + step * idx if n > 1 else inner_x + inner_width / 2
        parts.append(f'<circle cx="{cx:.1f}" cy="{axis_y}" r="24" fill="#ffffff" stroke="#1f6f9f" stroke-width="4"/>')
        parts.append(f'<circle cx="{cx:.1f}" cy="{axis_y}" r="17" fill="#eef7ff"/>')
        parts.append(_svg_text(str(idx + 1), cx, axis_y + 7, 18, "#1267b3", anchor="middle", weight=800))
        for offset, text in enumerate(_flow_label_lines(item)):
            parts.append(_svg_text(text, cx, axis_y + 44 + offset * 18, 14, "#102a43", anchor="middle", weight=700))
        if idx < n - 1:
            arrow_start = cx + 30
            arrow_end = inner_x + step * (idx + 1) - 30
            if arrow_end > arrow_start:
                parts.append(_arrow(arrow_start, axis_y, arrow_end, axis_y, "#1f6f9f"))
    return parts


def _flow_label_lines(item: str) -> list[str]:
    text = str(item or "").strip()
    if "/" in text:
        parts = [part for part in text.split("/") if part][:2]
        return parts or [text]
    return _wrap_text(text, 5)[:2]


def _note_svg(note: str, x: int, y: int, width: int) -> list[str]:
    return [
        f'<rect x="{x}" y="{y}" width="{width}" height="48" rx="12" fill="#eef7f6" stroke="#b8d8d3"/>',
        f'<rect x="{x}" y="{y}" width="6" height="48" rx="3" fill="#0f766e"/>',
        _svg_text("证据说明", x + 22, y + 31, 16, "#0f766e", weight=800),
        _svg_text(note, x + 104, y + 31, 15, "#52616f"),
    ]


def _stage_color(idx: int) -> str:
    palette = ["#1f6f9f", "#0f766e", "#7c3aed", "#c2410c"]
    return palette[(idx - 1) % len(palette)]


def _evidence_colors(level: str) -> tuple[str, str]:
    if "公告" in level or "财报" in level:
        return "#e8f5e9", "#15803d"
    if "新闻" in level:
        return "#e0f2fe", "#0369a1"
    if "研报" in level:
        return "#f3e8ff", "#7e22ce"
    if "映射" in level:
        return "#fff7ed", "#c2410c"
    return "#f1f5f9", "#64748b"


def _arrow(x1: float, y1: float, x2: float, y2: float, color: str) -> str:
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{color}" stroke-width="3" stroke-linecap="round"/>'
        f'<polygon points="{x2:.1f},{y2:.1f} {x2 - 9:.1f},{y2 - 6:.1f} {x2 - 9:.1f},{y2 + 6:.1f}" fill="{color}"/>'
    )


def _svg_text(
    text: str,
    x: float,
    y: float,
    size: int,
    color: str,
    *,
    anchor: str = "start",
    weight: int = 400,
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" '
        f'font-family="Microsoft YaHei, PingFang SC, Arial, sans-serif" '
        f'font-weight="{weight}" text-anchor="{anchor}">{escape(str(text))}</text>'
    )


def _material_chain_stages(
    subject: str,
    product_terms: list[str],
    keyword_terms: list[str],
    doc_text: str,
) -> list[ChainVisualStage]:
    upstream_raw = _filter_terms(product_terms, ["砂", "石", "纤维", "纱", "浸润", "漏板", "原料"])
    midstream = _filter_terms(product_terms, [subject, "开纤", "织造", "处理", "薄布", "电子布"])
    downstream = _filter_terms(product_terms, ["覆铜板", "CCL", "PCB", "服务器", "汽车", "通信", "终端"])
    companies = _company_terms(keyword_terms, doc_text, subject=subject)

    return [
        ChainVisualStage(
            name="源头原料",
            description="决定成本、纯度和供给稳定性",
            items=_items_from_terms(
                upstream_raw[:4],
                "基础原料/关键辅材",
                "纯度、价格周期、稳定供应",
                _stage_companies(subject, "源头原料", companies),
                doc_text,
                default_evidence="产业链映射",
                company_resolver=lambda term: _term_companies(subject, "源头原料", term, companies),
            ),
        ),
        ChainVisualStage(
            name="上游材料",
            description="从原料到可加工材料",
            items=_items_from_terms(
                upstream_raw[4:8] or upstream_raw[:2],
                "制备电子布前道材料",
                "细度、均匀性、认证周期",
                _stage_companies(subject, "上游材料", companies),
                doc_text,
                default_evidence="产业链映射",
                company_resolver=lambda term: _term_companies(subject, "上游材料", term, companies),
            ),
        ),
        ChainVisualStage(
            name="中游制造",
            description="制造工艺和良率形成壁垒",
            items=_items_from_terms(
                midstream[:4] or [subject],
                "制造产业链核心中间品",
                "开纤、织造、表面处理、良率",
                _stage_companies(subject, "中游制造", companies),
                doc_text,
                default_evidence="产业链映射",
                company_resolver=lambda term: _term_companies(subject, "中游制造", term, companies),
            ),
        ),
        ChainVisualStage(
            name="下游应用",
            description="需求由终端电子产品和算力硬件传导",
            items=_items_from_terms(
                downstream[:4] or ["覆铜板", "PCB"],
                "下游基材/终端应用",
                "高频高速、低损耗、客户认证",
                _stage_companies(subject, "下游应用", companies),
                doc_text,
                default_evidence="产业链映射",
                company_resolver=lambda term: _term_companies(subject, "下游应用", term, companies),
            ),
        ),
    ]


def _generic_chain_stages(topic_report: dict[str, Any], subject: str, doc_text: str) -> list[ChainVisualStage]:
    upstream = _unique([str(item) for item in topic_report.get("upstream_nodes") or []])
    matched = _unique([str(item) for item in topic_report.get("matched_nodes") or []])
    downstream = _unique([str(item) for item in topic_report.get("downstream_nodes") or []])
    companies = _company_terms([], doc_text, subject=subject)
    return [
        ChainVisualStage("源头/基础层", "基础资源、材料或能力", _items_from_terms(upstream[:4] or ["源头资源"], "基础投入", "供给稳定性", companies, doc_text)),
        ChainVisualStage("上游环节", "关键材料、零部件或设备", _items_from_terms(upstream[4:8] or upstream[:2], "上游输入", "替代难度/认证周期", companies, doc_text)),
        ChainVisualStage("中游核心", "主题核心制造或服务环节", _items_from_terms(matched[:4] or [subject], "核心环节", "规模化、良率、成本控制", companies, doc_text)),
        ChainVisualStage("下游/终端", "直接应用和最终需求", _items_from_terms(downstream[:4] or ["终端应用"], "需求承接", "需求波动/价格传导", companies, doc_text)),
    ]


def _items_from_terms(
    terms: list[str],
    usage: str,
    bottleneck: str,
    companies: list[str],
    doc_text: str,
    *,
    default_evidence: str = "待验证",
    company_resolver: Any | None = None,
) -> list[ChainVisualItem]:
    cleaned = _unique([_clean_term(term) for term in terms if _clean_term(term)])[:4]
    if not cleaned:
        cleaned = ["数据缺口"]
    items: list[ChainVisualItem] = []
    for term in cleaned:
        evidence_level = _item_evidence_level(term, doc_text, default_evidence=default_evidence)
        term_companies = company_resolver(term) if company_resolver else companies
        items.append(
            ChainVisualItem(
                title=term,
                usage=usage,
                bottleneck=bottleneck,
                companies=_unique(term_companies or companies)[:5],
                evidence_level=evidence_level,
            )
        )
    return items


def _item_evidence_level(term: str, doc_text: str, *, default_evidence: str) -> str:
    if term and term in doc_text:
        return "新闻证据"
    return default_evidence


def _material_process_flow(subject: str, product_terms: list[str]) -> list[str]:
    preferred = ["原料", "玻纤纱", "织造", "开纤/处理", subject, "覆铜板", "PCB", "终端应用"]
    if any("玻璃纤维" in item for item in product_terms):
        return preferred
    return ["源头原料", "上游材料", "中游制造", subject, "下游应用", "终端需求"]


def _generic_process_flow(stages: list[ChainVisualStage]) -> list[str]:
    out: list[str] = []
    for stage in stages:
        out.append(stage.name)
        if stage.items:
            out.append(stage.items[0].title)
    return _unique(out)[:8]


def _primary_subject(query_expansion: dict[str, Any], topic: str) -> str:
    keywords = [str(item) for item in query_expansion.get("keywords") or []]
    for item in keywords:
        clean = _strip_question_words(item)
        if clean and len(clean) <= 12 and " " not in clean:
            return clean
    corrected = str(query_expansion.get("corrected_query") or topic)
    return _strip_question_words(corrected) or corrected[:16]


def _is_material_chain(query_expansion: dict[str, Any]) -> bool:
    return str(query_expansion.get("topic_class") or "") in {"raw_material_chain", "component_chain", "industry_chain"}


def _filter_terms(terms: list[str], tokens: list[str]) -> list[str]:
    return _unique([term for term in terms if any(token in term for token in tokens)])


def _stage_companies(subject: str, stage_name: str, fallback: list[str]) -> list[str]:
    subject_clean = _clean_term(subject)
    if "电子布" in subject_clean:
        mapping = {
            "源头原料": ["石英股份", "菲利华", "中国巨石", "中材科技"],
            "上游材料": ["中国巨石", "中材科技", "山东玻纤", "长海股份", "宏和科技"],
            "中游制造": ["宏和科技", "中国巨石", "中材科技", "山东玻纤", "长海股份"],
            "下游应用": ["生益科技", "南亚新材", "金安国纪", "华正新材", "建滔积层板"],
        }
        return mapping.get(stage_name, fallback)[:5]
    return fallback[:5]


def _term_companies(subject: str, stage_name: str, term: str, fallback: list[str]) -> list[str]:
    subject_clean = _clean_term(subject)
    term_clean = _clean_term(term)
    if "电子布" not in subject_clean:
        return _stage_companies(subject, stage_name, fallback)

    rules: list[tuple[list[str], list[str]]] = [
        (["石英砂", "石英"], ["石英股份", "菲利华", "凯盛科技"]),
        (["叶腊石", "原料"], ["中国巨石", "中材科技", "山东玻纤"]),
        (["浸润剂"], ["中国巨石", "中材科技", "宏和科技"]),
        (["漏板", "铂铑"], ["贵研铂业", "有研新材", "中国巨石"]),
        (["玻璃纤维纱", "玻纤纱", "玻璃纤维"], ["中国巨石", "中材科技", "山东玻纤", "长海股份", "宏和科技"]),
        (["电子级玻璃纤维布", "电子玻纤布", "电子布", "薄布", "超薄布", "极薄布"], ["宏和科技", "中国巨石", "中材科技", "山东玻纤", "长海股份"]),
        (["开纤", "织造", "表面处理"], ["宏和科技", "中国巨石", "中材科技", "山东玻纤"]),
        (["覆铜板", "CCL"], ["生益科技", "南亚新材", "金安国纪", "华正新材", "建滔积层板"]),
        (["PCB"], ["深南电路", "沪电股份", "胜宏科技", "景旺电子", "东山精密"]),
        (["服务器", "AI", "汽车", "通信", "终端"], ["工业富联", "沪电股份", "深南电路", "胜宏科技", "东山精密"]),
    ]
    for tokens, companies in rules:
        if any(token in term_clean for token in tokens):
            return companies
    return _stage_companies(subject, stage_name, fallback)


def _company_terms(keyword_terms: list[str], doc_text: str, *, subject: str = "") -> list[str]:
    candidates: list[str] = []
    subject_clean = _clean_term(subject)
    for term in keyword_terms:
        clean = _clean_term(term)
        if not clean or len(clean) > 8:
            continue
        if subject_clean and subject_clean in clean:
            continue
        if any(token in clean for token in ["公司", "上市", "产业链", "价格", "供需", "研报", "风险", "是什么", "有没有", "前景"]):
            continue
        if any(suffix in clean for suffix in ["科技", "股份", "新材", "材料", "巨石", "中材"]):
            candidates.append(clean)
    for match in re.findall(r"[\u4e00-\u9fff]{2,8}(?:科技|股份|新材|材料|巨石|中材)", doc_text):
        clean = _clean_term(match)
        if subject_clean and subject_clean in clean:
            continue
        candidates.append(clean)
    return _unique(candidates)[:6]


def _evidence_text(topic_report: dict[str, Any]) -> str:
    parts: list[str] = []
    for doc in [*(topic_report.get("selected_documents") or []), *(topic_report.get("recent_documents") or [])][:20]:
        parts.append(str(doc.get("title") or ""))
        parts.append(str(doc.get("content") or "")[:500])
    return "\n".join(parts)


def _strip_question_words(text: str) -> str:
    clean = _clean_term(text)
    for token in ["是什么东西", "是什么", "怎么样", "有没有投资机会", "有投资机会吗", "有没有前景", "有前景吗", "前景如何"]:
        clean = clean.replace(token, "")
    clean = re.sub(r"[，,。？?].*$", "", clean)
    return clean.strip()


def _clean_term(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" -_，,。；;：:")


def _wrap_text(text: str, width: int) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return [""]
    chunks: list[str] = []
    for part in re.split(r"([、/])", text):
        if not part:
            continue
        if len(part) <= width:
            chunks.append(part)
        else:
            chunks.extend(textwrap.wrap(part, width=width, break_long_words=True, replace_whitespace=False))
    lines: list[str] = []
    current = ""
    for chunk in chunks:
        if len(current + chunk) <= width:
            current += chunk
        else:
            if current:
                lines.append(current)
            current = chunk
    if current:
        lines.append(current)
    return lines or [text[:width]]


def _unique(values: list[str], limit: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_term(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if limit and len(out) >= limit:
            break
    return out
