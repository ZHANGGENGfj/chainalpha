from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from chainalpha.config import load_settings
from chainalpha.nlp.llm_client import LLMClient


DIMENSIONS = [
    "customer_path",
    "supply_chain_position",
    "capacity_constraint",
    "substitution_difficulty",
    "financial_structure",
    "institution_lag",
]

DIMENSION_LABELS = {
    "customer_path": "客户路径",
    "supply_chain_position": "供应链位置",
    "capacity_constraint": "产能约束",
    "substitution_difficulty": "替代难度",
    "financial_structure": "财务结构",
    "institution_lag": "机构滞后",
}

DIMENSION_WEIGHTS = {
    "customer_path": 20,
    "supply_chain_position": 20,
    "capacity_constraint": 20,
    "substitution_difficulty": 20,
    "financial_structure": 10,
    "institution_lag": 10,
}

DIMENSION_WORDS = {
    "customer_path": [
        "客户",
        "云厂商",
        "北美",
        "数据中心",
        "AI服务器",
        "交换机",
        "认证",
        "供应商",
        "订单",
        "采购",
        "交付",
        "英伟达",
        "NVIDIA",
        "Microsoft",
        "Meta",
        "Google",
        "Amazon",
    ],
    "supply_chain_position": [
        "上游",
        "下游",
        "产业链",
        "核心环节",
        "零部件",
        "模块",
        "光芯片",
        "电芯片",
        "器件",
        "封装",
        "测试",
        "交换芯片",
        "DSP",
    ],
    "capacity_constraint": [
        "产能",
        "扩产",
        "良率",
        "交付",
        "瓶颈",
        "供给",
        "紧缺",
        "募投",
        "在建工程",
        "产线",
        "爬坡",
        "满产",
    ],
    "substitution_difficulty": [
        "替代",
        "认证周期",
        "壁垒",
        "技术难度",
        "切换成本",
        "专利",
        "国产化",
        "客户认证",
        "可靠性",
        "量产",
        "高速",
        "1.6T",
        "800G",
    ],
    "financial_structure": [
        "营收",
        "收入",
        "净利润",
        "毛利率",
        "现金流",
        "存货",
        "应收",
        "资本开支",
        "费用率",
        "ROE",
        "市值",
        "PE",
        "估值",
    ],
    "institution_lag": [
        "研报",
        "机构",
        "评级",
        "覆盖",
        "一致预期",
        "目标价",
        "预期差",
        "市场关注",
        "低估",
        "上修",
        "盈利预测",
    ],
}

NODE_ALIASES = {
    "Optical Module": ["Optical Module", "光模块", "高速光模块", "800G", "1.6T", "光通信模块"],
    "Silicon Photonics": ["Silicon Photonics", "硅光", "硅光模块", "CPO", "LPO", "LRO"],
    "Laser / Optical Chip": ["Laser", "激光器", "光芯片", "EML", "DFB", "CW激光器", "VCSEL"],
    "DSP / Electrical Chip": ["DSP", "电芯片", "SerDes", "PAM4", "交换芯片"],
    "Optical Components": ["光器件", "无源器件", "有源器件", "连接器", "光引擎", "透镜"],
    "Packaging / Testing": ["封装", "测试", "耦合", "良率", "自动化产线"],
    "Network Switch": ["Network Switch", "交换机", "以太网交换机", "交换设备"],
    "High-speed PCB": ["PCB", "高速PCB", "HDI", "载板"],
    "Liquid Cooling": ["Liquid Cooling", "液冷", "散热"],
}

DEMAND_OR_MACRO_NODES = {
    "AI Data Center",
    "AI CapEx",
    "GPU Cluster",
    "Power Grid",
    "Data Center",
    "Cloud CapEx",
    "AI产业链",
    "optical_communication",
    "AI服务器整机",
    "AI Server",
    "GPU",
}


@dataclass
class BottleneckDimension:
    key: str
    label: str
    score: float
    weight: int
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "score": self.score,
            "weight": self.weight,
            "evidence": self.evidence,
            "gaps": self.gaps,
        }


@dataclass
class BottleneckNode:
    node: str
    aliases: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    dimension_scores: list[BottleneckDimension] = field(default_factory=list)
    bottleneck_score: float = 0.0
    tier: str = "待验证"
    investability: str = "数据不足"
    evidence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    falsification: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "aliases": self.aliases,
            "companies": self.companies,
            "dimension_scores": [item.to_dict() for item in self.dimension_scores],
            "bottleneck_score": self.bottleneck_score,
            "tier": self.tier,
            "investability": self.investability,
            "evidence": self.evidence,
            "risks": self.risks,
            "falsification": self.falsification,
        }


@dataclass
class ChainBottleneckReport:
    topic: str
    target_company: dict[str, Any] = field(default_factory=dict)
    target_question: str = ""
    nodes: list[BottleneckNode] = field(default_factory=list)
    chain_map: list[str] = field(default_factory=list)
    conclusion: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "target_company": self.target_company,
            "target_question": self.target_question,
            "chain_map": self.chain_map,
            "conclusion": self.conclusion,
            "nodes": [item.to_dict() for item in self.nodes],
            "data_gaps": self.data_gaps,
            "warnings": self.warnings,
        }


def build_chain_bottleneck_report(topic_report: dict[str, Any]) -> ChainBottleneckReport:
    topic = str(topic_report.get("topic") or "")
    docs = _collect_docs(topic_report)
    signals = topic_report.get("signals") or []
    companies = topic_report.get("companies") or []
    query_expansion = topic_report.get("query_expansion") or {}
    target_company = _target_company(topic_report)

    candidate_nodes = _candidate_nodes(topic_report, query_expansion)
    node_reports = [
        _build_node_report(node, docs=docs, signals=signals, companies=companies, target_company=target_company)
        for node in candidate_nodes
    ]
    node_reports.sort(key=lambda item: item.bottleneck_score, reverse=True)

    report = ChainBottleneckReport(
        topic=topic,
        target_company=target_company,
        target_question="识别产业链里谁拥有真正的瓶颈权力，而不是判断新闻是否刺激股价。",
        nodes=node_reports[:10],
        chain_map=_chain_map_lines(topic_report, node_reports, target_company),
        conclusion=_conclusion_lines(node_reports, target_company),
        data_gaps=_report_data_gaps(node_reports),
        warnings=[
            "瓶颈权力分是证据驱动的研究排序，不等同于投资建议或股价预测。",
            "客户、订单、产能、良率、价格和财务数据缺少公告/财报/研报交叉验证时，只能视为待验证假设。",
            "需求驱动节点、宏观概念节点和目标公司的真实零部件/环节必须分开，不能把产业链相关性直接当作公司瓶颈权力。",
        ],
    )
    return report


def render_chain_bottleneck_markdown(report: ChainBottleneckReport) -> str:
    target = report.target_company.get("name") or "目标公司"
    lines: list[str] = [
        f"# {report.topic} 产业链瓶颈权力报告",
        "",
        "## 1. 核心结论",
        "",
    ]
    for item in report.conclusion:
        lines.append(f"- {item}")

    lines.extend(["", "## 2. 需求传导链", ""])
    for item in report.chain_map:
        lines.append(f"- {item}")

    lines.extend(["", "## 3. 瓶颈环节排序", ""])
    lines.append("| 排名 | 环节/零部件 | 代表公司 | 瓶颈权力分 | 分层 | 投研含义 |")
    lines.append("| --- | --- | --- | ---: | --- | --- |")
    for idx, node in enumerate(report.nodes, 1):
        lines.append(
            f"| {idx} | {node.node} | {', '.join(node.companies) or '-'} | "
            f"{node.bottleneck_score:.1f} | {node.tier} | {node.investability} |"
        )

    lines.extend(["", "## 4. 六维证据拆解", ""])
    for node in report.nodes:
        lines.extend([f"### {node.node}", ""])
        lines.append(f"- 代表公司：{', '.join(node.companies) or '数据缺口'}")
        if node.aliases:
            lines.append(f"- 同义词：{', '.join(node.aliases[:8])}")
        lines.append(f"- 瓶颈权力分：{node.bottleneck_score:.1f}，分层：{node.tier}")
        lines.append(f"- 投研含义：{node.investability}")
        lines.append("")
        lines.append("| 维度 | 得分 | 证据 | 数据缺口 |")
        lines.append("| --- | ---: | --- | --- |")
        for dim in node.dimension_scores:
            evidence = "<br>".join(dim.evidence[:3]) or "数据缺口"
            gaps = "<br>".join(dim.gaps[:3]) or "-"
            lines.append(f"| {dim.label} | {dim.score:.1f}/{dim.weight} | {evidence} | {gaps} |")
        if node.risks:
            lines.extend(["", "风险："])
            for risk in node.risks[:5]:
                lines.append(f"- {risk}")
        if node.falsification:
            lines.extend(["", "证伪条件："])
            for item in node.falsification[:5]:
                lines.append(f"- {item}")
        lines.append("")

    lines.extend(["## 5. 对目标公司的结论约束", ""])
    lines.extend(
        [
            f"- 本报告的目标公司锁定为：{target}。产业链相关公司只能作为上下游、同行或验证材料，不能替代目标公司。",
            "- 若一个环节只有概念热度，没有客户路径、产能约束、替代难度和财务兑现证据，不应直接判断为真实瓶颈。",
            "- 若目标公司位于瓶颈环节，还需要继续验证其份额、客户认证、交付能力、毛利率和现金流质量。",
        ]
    )

    lines.extend(["", "## 6. 风险与证伪", ""])
    for item in report.data_gaps:
        lines.append(f"- {item}")

    lines.extend(["", "## 7. 跟踪仪表盘", ""])
    lines.extend(
        [
            "- 客户路径：下游大客户资本开支、订单公告、客户认证、供应商名录。",
            "- 供应链位置：零部件单机价值量、供应商数量、国产替代进度、技术路线变化。",
            "- 产能约束：扩产公告、在建工程、良率、交付周期、存货和预付款。",
            "- 替代难度：认证周期、专利/技术指标、客户切换成本、可靠性测试。",
            "- 财务结构：收入增速、毛利率、经营现金流、存货、应收、资本开支。",
            "- 机构滞后：研报覆盖数量、一致预期变化、估值分位、市场认知差。",
        ]
    )

    lines.extend(["", "## 8. 数据来源与免责声明", ""])
    for item in report.warnings:
        lines.append(f"- {item}")
    lines.append("- 本报告用于产业链研究辅助，不构成投资建议。")
    return "\n".join(lines).strip() + "\n"


def generate_llm_chain_bottleneck_report(report: ChainBottleneckReport, agent_report: dict[str, Any] | None = None) -> str:
    settings = load_settings()
    client = LLMClient(settings)
    payload = _trim_for_llm(
        {
            "chain_bottleneck_report": report.to_dict(),
            "five_agent_report": agent_report or {},
        }
    )
    target = report.target_company.get("name") or "目标公司"
    system = f"""你是严谨的中文产业链投研分析师。
你的任务不是判断新闻会不会刺激股价，而是判断产业链里谁拥有真正的瓶颈权力。
目标公司已经锁定为：{target}。不要把同行、上下游或产业链其他公司的业务写成目标公司的业务。
只能使用输入 JSON 中的证据；不得编造客户、订单、产能、良率、市场份额、估值倍数或财务数字。
如果证据不足，必须写“数据缺口/待验证”。
输出 Markdown。"""
    user = f"""
请基于输入 JSON 生成一份专业中文《产业链瓶颈权力报告》。
如果 five_agent_report 存在，必须优先按照五个 Agent 的结果组织论证：
信息收集 Agent -> 产业链拆解 Agent -> 财务验证 Agent -> 投资映射 Agent -> 报告生成 Agent。

必须使用以下结构：
# 标题
## 1. 核心结论
- 目标公司锁定
- 一句话判断：是否拥有瓶颈权力
- 最可能的瓶颈来源
- 当前证据强度
- 最大数据缺口

## 2. 需求传导链
- 终端需求
- 客户路径
- 系统环节
- 核心零部件
- 财务验证路径

## 3. 瓶颈权力排序
用表格列出：环节、代表公司、六维得分、总分、结论。

## 4. 六维证据拆解
按以下六个维度分析：客户路径、供应链位置、产能约束、替代难度、财务结构、机构滞后。

## 5. 目标公司专章
只分析目标公司 {target}：
- 它处于哪个环节
- 它是否控制稀缺环节
- 它的瓶颈权力来自客户认证、量产能力、良率、技术路线、供应链管理还是财务兑现
- 哪些结论只是待验证假设

## 6. 反方观点与证伪条件
列出至少 5 条可证伪条件。

## 7. 跟踪指标仪表盘
给出可持续跟踪的量化/半量化指标。

## 8. 数据缺口与免责声明
明确哪些数据缺口会影响结论。

写作要求：
1. 不要输出普通公司研报的 12 节模板。
2. 不要把中科曙光、工业富联等相关公司写成目标公司。
3. 不要出现 corrected_chain_logic、target_company 等程序字段名。
4. 证据不足时要克制，不要硬写结论。

输入 JSON：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=180)


def _build_node_report(
    node: str,
    *,
    docs: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    companies: list[dict[str, Any]],
    target_company: dict[str, Any],
) -> BottleneckNode:
    aliases = NODE_ALIASES.get(node, [node])
    evidence_pool = _evidence_for_node(node, docs, signals)
    dims = [
        _dimension_score(key, evidence_pool, DIMENSION_WORDS[key])
        for key in DIMENSIONS
    ]
    total = sum(dim.score for dim in dims)
    companies_for_node = _companies_for_node(node, companies, target_company)
    return BottleneckNode(
        node=node,
        aliases=aliases,
        companies=companies_for_node,
        dimension_scores=dims,
        bottleneck_score=round(total, 1),
        tier=_tier(total),
        investability=_investability(total, dims),
        evidence=[item["text"] for item in evidence_pool[:8]],
        risks=_node_risks(node, dims),
        falsification=_node_falsification(),
    )


def _dimension_score(key: str, evidence_pool: list[dict[str, Any]], words: list[str]) -> BottleneckDimension:
    weight = DIMENSION_WEIGHTS[key]
    hits: list[str] = []
    for item in evidence_pool:
        text = item["text"]
        if any(word.lower() in text.lower() for word in words):
            hits.append(text)
    raw = min(1.0, len(hits) / 4)
    score = round(weight * raw, 1)
    gaps = []
    if not hits:
        gaps.append(f"缺少{DIMENSION_LABELS[key]}的直接证据。")
    if key == "financial_structure" and len(hits) < 2:
        gaps.append("需要财报指标验证收入、毛利率、现金流、存货和应收。")
    if key == "institution_lag" and len(hits) < 2:
        gaps.append("需要机构覆盖、研报观点和一致预期变化验证预期差。")
    return BottleneckDimension(
        key=key,
        label=DIMENSION_LABELS[key],
        score=score,
        weight=weight,
        evidence=hits[:5],
        gaps=gaps,
    )


def _candidate_nodes(topic_report: dict[str, Any], query_expansion: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    topic = str(topic_report.get("topic") or "")
    terms.extend(topic_report.get("matched_nodes") or [])
    terms.extend(query_expansion.get("product_terms") or [])
    terms.extend(query_expansion.get("industry_terms") or [])
    for company in topic_report.get("companies") or []:
        profile = company.get("company") or {}
        terms.extend(profile.get("nodes") or [])
        terms.extend(company.get("key_products") or [])
    for signal in topic_report.get("signals") or []:
        event = signal.get("event") or {}
        terms.extend(event.get("supply_chain_nodes") or [])

    normalized = [_normalize_node(term) for term in terms]
    optical_focus = any(word in topic for word in ["光模块", "中际旭创", "800G", "1.6T", "硅光"])
    if not any(node in normalized for node in ["Optical Module", "Silicon Photonics"]) and optical_focus:
        normalized.extend(["Optical Module", "Silicon Photonics", "Laser / Optical Chip", "DSP / Electrical Chip"])
    if optical_focus:
        system_nodes = {"Network Switch", "Liquid Cooling", "High-speed PCB"}
        normalized = [node for node in normalized if node not in system_nodes]
    return _unique([node for node in normalized if _valid_node(node)])[:12]


def _collect_docs(topic_report: dict[str, Any]) -> list[dict[str, Any]]:
    docs = [
        *(topic_report.get("recent_news") or []),
        *(topic_report.get("selected_documents") or []),
        *(topic_report.get("recent_documents") or []),
    ]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for doc in docs:
        key = str(doc.get("url") or doc.get("title") or doc.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out


def _evidence_for_node(node: str, docs: list[dict[str, Any]], signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms = _node_terms(node)
    evidence: list[dict[str, Any]] = []
    for doc in docs:
        text = _doc_text(doc)
        if _matches_any(text, terms):
            evidence.append({"source": doc.get("source"), "text": _compact_evidence(text)})
    for signal in signals:
        text = " ".join(
            [
                str(signal.get("title") or ""),
                json.dumps(signal.get("event") or {}, ensure_ascii=False),
                json.dumps(signal.get("hypothesis") or {}, ensure_ascii=False),
            ]
        )
        if _matches_any(text, terms):
            evidence.append({"source": "signal", "text": _compact_evidence(text)})
    return _dedup_evidence(evidence)[:20]


def _companies_for_node(
    node: str,
    companies: list[dict[str, Any]],
    target_company: dict[str, Any],
) -> list[str]:
    names: list[str] = []
    node_terms = set(_node_terms(node))
    target_name = str(target_company.get("name") or "")
    for row in companies:
        profile = row.get("company") or {}
        name = str(profile.get("name") or "")
        company_nodes = [str(item) for item in profile.get("nodes") or []]
        key_products = [str(item) for item in row.get("key_products") or []]
        haystack = " ".join([*company_nodes, *key_products, str(profile.get("role") or "")])
        if name and any(term.lower() in haystack.lower() for term in node_terms):
            names.append(name)
    if target_name and node in {"Optical Module", "Silicon Photonics", "Laser / Optical Chip", "Optical Components"}:
        names.insert(0, target_name)
    return _unique(names)[:8]


def _chain_map_lines(
    topic_report: dict[str, Any],
    nodes: list[BottleneckNode],
    target_company: dict[str, Any],
) -> list[str]:
    target = target_company.get("name") or "目标公司"
    top_nodes = [node.node for node in nodes[:6]]
    return [
        f"研究对象：{topic_report.get('topic')}；目标公司锁定为 {target}。",
        f"当前识别到的关键环节：{', '.join(top_nodes) or '数据缺口'}。",
        "需求传导应按“AI算力/数据中心需求 -> 云厂商/服务器/交换机 -> 高速光模块 -> 光芯片/电芯片/器件/封装测试 -> 财务兑现”逐层验证。",
        "若缺少客户、订单、产能、良率、价格和财务数据，相关结论只能作为待验证假设。",
    ]


def _conclusion_lines(nodes: list[BottleneckNode], target_company: dict[str, Any]) -> list[str]:
    target = target_company.get("name") or "目标公司"
    if not nodes:
        return [f"当前证据不足以判断 {target} 是否拥有产业链瓶颈权力，需要补充新闻、公告、财报和研报材料。"]
    top = nodes[0]
    lines = [
        f"目标公司锁定为 {target}，不得用相关公司替代目标公司。",
        f"当前证据排序中，{top.node} 的瓶颈权力分最高，为 {top.bottleneck_score:.1f}，分层为“{top.tier}”。",
        "报告从客户路径、供应链位置、产能约束、替代难度、财务结构、机构滞后六个维度判断瓶颈权力。",
    ]
    real = [node.node for node in nodes if node.bottleneck_score >= 70]
    if real:
        lines.append(f"候选真瓶颈环节：{', '.join(real[:5])}。")
    else:
        lines.append("当前尚未形成高置信度真瓶颈结论，重点应继续补证据，而不是直接下投资结论。")
    return lines


def _report_data_gaps(nodes: list[BottleneckNode]) -> list[str]:
    gaps = [
        "需要用公告/财报/研报交叉验证客户路径，避免把概念相关误判为真实客户或供应商关系。",
        "需要补充各零部件的单机价值量、供应商数量、扩产周期、良率和认证周期。",
        "需要补充公司层面的收入结构、毛利率、现金流、存货、应收、资本开支和机构覆盖情况。",
    ]
    for node in nodes[:5]:
        for dim in node.dimension_scores:
            gaps.extend(dim.gaps)
    return _unique(gaps)[:16]


def _node_risks(node: str, dims: list[BottleneckDimension]) -> list[str]:
    risks = [
        "若下游需求放缓或客户资本开支下修，瓶颈环节的收入弹性可能被削弱。",
        "若竞争者扩产或技术路线切换，供给约束和替代难度可能下降。",
    ]
    if any(dim.key == "financial_structure" and dim.score == 0 for dim in dims):
        risks.append("当前缺少财务验证，不能确认瓶颈权力已经转化为收入、利润或现金流。")
    if node in {"Silicon Photonics", "DSP / Electrical Chip"}:
        risks.append("技术路线仍可能变化，需要验证量产、良率、成本和客户认证进度。")
    return risks


def _node_falsification() -> list[str]:
    return [
        "客户认证或订单进展低于预期。",
        "产能扩张后供给约束缓解，价格或毛利率下行。",
        "存货和应收增长明显快于收入，利润质量变弱。",
        "竞争者替代方案成熟，客户切换成本下降。",
        "机构预期已经充分定价，后续缺少新的数据验证。",
    ]


def _target_company(topic_report: dict[str, Any]) -> dict[str, Any]:
    companies = topic_report.get("companies") or []
    if not companies:
        return {}
    profile = companies[0].get("company") or {}
    return {
        "name": profile.get("name"),
        "ticker": profile.get("ticker"),
        "industry": profile.get("industry"),
        "role": profile.get("role"),
        "nodes": profile.get("nodes") or [],
    }


def _tier(score: float) -> str:
    if score >= 75:
        return "真瓶颈候选"
    if score >= 60:
        return "高弹性候选"
    if score >= 40:
        return "主题跟随/待验证"
    return "证据不足/伪相关风险"


def _investability(score: float, dims: list[BottleneckDimension]) -> str:
    weak = [dim.label for dim in dims if dim.score == 0]
    if score >= 75:
        return "优先跟踪，重点验证财务兑现和估值预期。"
    if score >= 60:
        return "具备弹性线索，但需要补充客户、产能或财务证据。"
    if score >= 40:
        return f"产业链相关性存在，但{', '.join(weak[:2]) or '关键证据'}仍不足。"
    return "暂不应作为核心瓶颈标的，需要更多一手证据。"


def _doc_text(doc: dict[str, Any]) -> str:
    return " ".join(
        str(doc.get(key) or "")
        for key in ["title", "content", "summary", "source", "source_type", "published_at", "url"]
    )


def _node_terms(node: str) -> list[str]:
    terms = [node, *NODE_ALIASES.get(node, [])]
    return _unique(terms)


def _normalize_node(term: Any) -> str:
    text = str(term or "").strip()
    if not text:
        return ""
    low = text.lower()
    for canonical, aliases in NODE_ALIASES.items():
        if low == canonical.lower() or any(alias.lower() in low or low in alias.lower() for alias in aliases):
            return canonical
    return text


def _valid_node(term: Any) -> bool:
    text = str(term or "").strip()
    if len(text) < 2:
        return False
    if text in DEMAND_OR_MACRO_NODES:
        return False
    bad = {"公告", "财报", "研报", "业绩", "风险", "AI产业链", "光通信"}
    if text in bad:
        return False
    return text in NODE_ALIASES


def _matches_any(text: str, terms: list[str]) -> bool:
    low = text.lower()
    return any(term and term.lower() in low for term in terms)


def _compact_evidence(text: str, max_len: int = 280) -> str:
    clean = re.sub(r"\s+", " ", str(text)).strip()
    clean = re.sub(r"\{[^{}]{20,}\}", " ", clean)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r'"(?:id|raw|metadata|hypothesis|description)"\s*:\s*', " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:max_len]


def _dedup_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        text = str(item.get("text") or "").strip()
        key = text[:120].lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _unique(values: list[str], limit: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if limit and len(out) >= limit:
            break
    return out


def _trim_for_llm(value: Any, *, max_string: int = 900, max_list: int = 12) -> Any:
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, list):
        return [_trim_for_llm(item, max_string=max_string, max_list=max_list) for item in value[:max_list]]
    if isinstance(value, dict):
        return {key: _trim_for_llm(item, max_string=max_string, max_list=max_list) for key, item in value.items()}
    return value
