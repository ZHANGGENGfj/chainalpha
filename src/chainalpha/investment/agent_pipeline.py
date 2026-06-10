from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from chainalpha.investment.web_financial_facts import flatten_web_financial_facts


@dataclass
class AgentSection:
    name: str
    objective: str
    findings: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "objective": self.objective,
            "findings": self.findings,
            "evidence": self.evidence,
            "data_gaps": self.data_gaps,
            "next_actions": self.next_actions,
        }


@dataclass
class FiveAgentResearchReport:
    topic: str
    target_company: dict[str, Any] = field(default_factory=dict)
    framework: str = "local"
    graph_steps: list[str] = field(default_factory=list)
    agents: list[AgentSection] = field(default_factory=list)
    final_conclusion: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "target_company": self.target_company,
            "framework": self.framework,
            "graph_steps": self.graph_steps,
            "agents": [agent.to_dict() for agent in self.agents],
            "final_conclusion": self.final_conclusion,
        }


def build_five_agent_research_report(
    topic_report: dict[str, Any],
    chain_report: dict[str, Any] | None = None,
) -> FiveAgentResearchReport:
    chain_report = chain_report or {}
    target_company = _target_company(topic_report, chain_report)
    agents, framework, graph_steps = _run_agent_graph(topic_report, chain_report, target_company)
    return FiveAgentResearchReport(
        topic=str(topic_report.get("topic") or chain_report.get("topic") or ""),
        target_company=target_company,
        framework=framework,
        graph_steps=graph_steps,
        agents=agents,
        final_conclusion=_final_conclusion(agents, target_company),
    )


def render_five_agent_markdown(report: FiveAgentResearchReport) -> str:
    target = report.target_company.get("name") or "目标公司"
    lines = [
        f"# {report.topic} 五Agent研投流水线报告",
        "",
        f"> 目标公司：{target}",
        f"> Agent框架：{report.framework}",
        "",
        "## 总体结论",
        "",
    ]
    for item in report.final_conclusion:
        lines.append(f"- {item}")

    for idx, agent in enumerate(report.agents, 1):
        lines.extend(["", f"## Agent {idx}：{agent.name}", ""])
        lines.append(f"**目标**：{agent.objective}")
        _append_items(lines, "核心发现", agent.findings)
        _append_items(lines, "证据", agent.evidence)
        _append_items(lines, "数据缺口", agent.data_gaps)
        _append_items(lines, "下一步动作", agent.next_actions)

    lines.extend(
        [
            "",
            "## 使用说明",
            "",
            "- 这份报告是多 Agent 中间层输出，用于把信息收集、产业链拆解、财务验证、投资映射和正式报告生成分开。",
            "- 若某个 Agent 的数据缺口较多，最终 LLM 报告应降低结论强度，避免把概念热度写成确定性投资结论。",
            "- 本报告用于投研辅助，不构成投资建议。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


class AgentGraphState(TypedDict, total=False):
    topic_report: dict[str, Any]
    chain_report: dict[str, Any]
    target_company: dict[str, Any]
    agents: list[AgentSection]
    graph_steps: list[str]


def _run_agent_graph(
    topic_report: dict[str, Any],
    chain_report: dict[str, Any],
    target_company: dict[str, Any],
) -> tuple[list[AgentSection], str, list[str]]:
    langgraph_result = _run_langgraph_agent_graph(topic_report, chain_report, target_company)
    if langgraph_result is not None:
        return langgraph_result
    agents = [
        _information_collection_agent(topic_report),
        _industry_chain_agent(topic_report, chain_report),
        _financial_validation_agent(topic_report),
        _investment_mapping_agent(topic_report, chain_report, target_company),
        _report_generation_agent(topic_report, chain_report, target_company),
    ]
    return agents, "local_fallback", [agent.name for agent in agents]


def _run_langgraph_agent_graph(
    topic_report: dict[str, Any],
    chain_report: dict[str, Any],
    target_company: dict[str, Any],
) -> tuple[list[AgentSection], str, list[str]] | None:
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except ImportError:
        return None

    def append_agent(state: AgentGraphState, agent: AgentSection) -> AgentGraphState:
        agents = [*(state.get("agents") or []), agent]
        steps = [*(state.get("graph_steps") or []), agent.name]
        return {"agents": agents, "graph_steps": steps}

    def info_node(state: AgentGraphState) -> AgentGraphState:
        return append_agent(state, _information_collection_agent(state["topic_report"]))

    def chain_node(state: AgentGraphState) -> AgentGraphState:
        return append_agent(state, _industry_chain_agent(state["topic_report"], state["chain_report"]))

    def finance_node(state: AgentGraphState) -> AgentGraphState:
        return append_agent(state, _financial_validation_agent(state["topic_report"]))

    def mapping_node(state: AgentGraphState) -> AgentGraphState:
        return append_agent(
            state,
            _investment_mapping_agent(state["topic_report"], state["chain_report"], state["target_company"]),
        )

    def report_node(state: AgentGraphState) -> AgentGraphState:
        return append_agent(
            state,
            _report_generation_agent(state["topic_report"], state["chain_report"], state["target_company"]),
        )

    graph = StateGraph(AgentGraphState)
    graph.add_node("information_collection", info_node)
    graph.add_node("industry_chain_decomposition", chain_node)
    graph.add_node("financial_validation", finance_node)
    graph.add_node("investment_mapping", mapping_node)
    graph.add_node("report_generation", report_node)
    graph.set_entry_point("information_collection")
    graph.add_edge("information_collection", "industry_chain_decomposition")
    graph.add_edge("industry_chain_decomposition", "financial_validation")
    graph.add_edge("financial_validation", "investment_mapping")
    graph.add_edge("investment_mapping", "report_generation")
    graph.add_edge("report_generation", END)

    app = graph.compile()
    result = app.invoke(
        {
            "topic_report": topic_report,
            "chain_report": chain_report,
            "target_company": target_company,
            "agents": [],
            "graph_steps": [],
        }
    )
    return result.get("agents") or [], "langgraph", result.get("graph_steps") or []


def _information_collection_agent(topic_report: dict[str, Any]) -> AgentSection:
    recent_news = topic_report.get("recent_news") or []
    recent_docs = topic_report.get("recent_documents") or []
    selected_docs = topic_report.get("selected_documents") or []
    signals = topic_report.get("signals") or []
    warnings = topic_report.get("warnings") or []

    findings = [
        f"已读取新闻/公告/研报类文档 {len(recent_docs)} 条，精选文档 {len(selected_docs)} 条，结构化信号 {len(signals)} 条。",
        f"高价值新闻条目 {len(recent_news)} 条，可用于近期事件和催化剂判断。",
    ]
    evidence = [_doc_line(doc) for doc in [*recent_news, *selected_docs][:8]]
    data_gaps = []
    if not recent_docs:
        data_gaps.append("缺少本地或实时新闻/公告/研报材料。")
    if not recent_news:
        data_gaps.append("缺少经过标题价值判断和正文抽取后的高价值新闻。")
    if any("A-share filings/PDF parsing disabled" in str(item) for item in warnings):
        data_gaps.append("A股财报 PDF 解析未启用，财报证据不足。")
    return AgentSection(
        name="信息收集 Agent",
        objective="抓取并整理新闻、公告、财报、研报、政策、海外映射、价格和产业链数据。",
        findings=findings,
        evidence=evidence,
        data_gaps=data_gaps,
        next_actions=[
            "继续接入交易所公告、巨潮资讯、券商研报和公司年报/季报。",
            "把海外龙头财报、大客户 CapEx、行业价格数据单独结构化入库。",
            "对新闻、财报、研报做按日去重、跨源去重和来源可信度评分。",
        ],
    )


def _industry_chain_agent(topic_report: dict[str, Any], chain_report: dict[str, Any]) -> AgentSection:
    nodes = chain_report.get("nodes") or []
    matched_nodes = topic_report.get("matched_nodes") or []
    upstream_nodes = topic_report.get("upstream_nodes") or []
    downstream_nodes = topic_report.get("downstream_nodes") or []

    top_nodes = [str(node.get("node")) for node in nodes[:6] if node.get("node")]
    findings = [
        f"当前产业链候选瓶颈环节：{', '.join(top_nodes) or '数据缺口'}。",
        f"主题匹配节点：{', '.join(matched_nodes[:8]) or '数据缺口'}。",
        f"上游节点：{', '.join(upstream_nodes[:8]) or '数据缺口'}；下游节点：{', '.join(downstream_nodes[:8]) or '数据缺口'}。",
    ]
    evidence = []
    for node in nodes[:5]:
        dims = node.get("dimension_scores") or []
        score = node.get("bottleneck_score")
        evidence.append(f"{node.get('node')}: bottleneck_score={score}, dimensions={_dimension_summary(dims)}")
    data_gaps = list(chain_report.get("data_gaps") or [])[:8]
    if not nodes:
        data_gaps.append("缺少产业链瓶颈节点排序。")
    return AgentSection(
        name="产业链拆解 Agent",
        objective="拆解上游、中游、下游、技术路线、核心零部件、价值量和供应商关系。",
        findings=findings,
        evidence=evidence,
        data_gaps=data_gaps,
        next_actions=[
            "建立光模块专用节点本体：光模块、光芯片、电芯片、DSP、硅光、CPO/LPO、封装测试、光器件。",
            "为每个节点补充价值量、供应商数量、认证周期和替代难度。",
            "区分需求驱动节点、系统节点和目标公司的真实业务节点。",
        ],
    )


def _financial_validation_agent(topic_report: dict[str, Any]) -> AgentSection:
    companies = topic_report.get("companies") or []
    primary = companies[0] if companies else {}
    company = primary.get("company") or {}
    financial = primary.get("financial") or {}
    price = primary.get("price") or {}
    web_facts_by_category = topic_report.get("web_financial_facts") or {}
    web_facts = flatten_web_financial_facts(web_facts_by_category)

    covered_categories = [category for category, facts in web_facts_by_category.items() if facts]
    findings = [
        f"财务验证目标：{company.get('name') or '目标公司'}。",
        f"网页财务事实覆盖 {len(covered_categories)} 个类别：{', '.join(covered_categories[:8]) or '暂无'}。",
    ]
    evidence = []
    if financial:
        for key in [
            "period",
            "revenue_growth",
            "profit_growth",
            "gross_margin",
            "roe",
            "operating_cash_flow",
            "debt_ratio",
            "pe",
            "pb",
            "ps",
        ]:
            if financial.get(key) is not None:
                evidence.append(f"{key}: {financial.get(key)}")
    if price:
        for key in ["date", "close", "change_1m", "change_3m", "change_6m", "change_12m"]:
            if price.get(key) is not None:
                evidence.append(f"{key}: {price.get(key)}")
    for fact in web_facts[:12]:
        evidence.append(
            f"{fact.get('category')} | {fact.get('confidence')} | {fact.get('title')} | "
            f"{fact.get('source')} | {fact.get('excerpt')}"
        )

    data_gaps = []
    if not financial and not web_facts:
        data_gaps.append("缺少结构化财务快照和网页财务事实，不能验证收入、利润、毛利率、现金流、存货和应收。")
    elif not financial:
        data_gaps.append("缺少结构化财务快照；当前仅能使用网页财务事实作为待核验证据。")
    else:
        for label, key in [
            ("收入增长", "revenue_growth"),
            ("利润增长", "profit_growth"),
            ("毛利率", "gross_margin"),
            ("经营现金流", "operating_cash_flow"),
            ("资产负债率", "debt_ratio"),
        ]:
            if financial.get(key) is None:
                data_gaps.append(f"缺少{label}数据。")
    if not price:
        data_gaps.append("缺少行情快照，不能验证股价表现、估值消化和市场预期。")
    for category in [
        "主要会计数据",
        "分产品收入",
        "分地区收入",
        "毛利率",
        "现金流量表",
        "资产负债表",
        "存货",
        "应收账款",
        "在建工程",
        "研发费用",
        "前五大客户/供应商",
        "管理层讨论与分析",
        "风险提示",
    ]:
        if not web_facts_by_category.get(category):
            data_gaps.append(f"网页检索暂未覆盖：{category}。")

    return AgentSection(
        name="财务验证 Agent",
        objective="验证收入、利润、毛利率、现金流、存货、应收和资本开支，防止纯概念误判。",
        findings=findings,
        evidence=evidence[:12],
        data_gaps=data_gaps,
        next_actions=[
            "优先补充最近三年年报和最近四个季度财务摘要。",
            "从财报 PDF 中抽取营业收入、归母净利润、毛利率、经营现金流、存货、应收账款、资本开支。",
            "建立利润质量评分：经营现金流/净利润、存货增速/收入增速、应收增速/收入增速。",
        ],
    )


def _investment_mapping_agent(
    topic_report: dict[str, Any],
    chain_report: dict[str, Any],
    target_company: dict[str, Any],
) -> AgentSection:
    companies = topic_report.get("companies") or []
    candidates = topic_report.get("candidates") or []
    chain_nodes = chain_report.get("nodes") or []
    target_name = target_company.get("name")

    core_assets = []
    elastic_assets = []
    theme_assets = []
    risk_assets = []
    for row in companies:
        profile = row.get("company") or {}
        name = profile.get("name")
        if not name:
            continue
        if target_name and name == target_name:
            core_assets.append(name)
        elif row.get("match_score", 0) >= 0.8:
            elastic_assets.append(name)
        else:
            theme_assets.append(name)
    for candidate in candidates:
        if candidate.get("rating") in {"回避", "谨慎"}:
            risk_assets.append((candidate.get("company") or {}).get("name") or "")

    findings = [
        f"核心资产：{', '.join(_unique(core_assets)) or '数据缺口'}。",
        f"弹性标的：{', '.join(_unique(elastic_assets)) or '数据缺口'}。",
        f"主题标的：{', '.join(_unique(theme_assets[:8])) or '数据缺口'}。",
        f"风险标的：{', '.join(_unique([item for item in risk_assets if item])) or '数据缺口'}。",
    ]
    evidence = [
        f"{node.get('node')}: score={node.get('bottleneck_score')}, companies={', '.join(node.get('companies') or [])}"
        for node in chain_nodes[:6]
    ]
    return AgentSection(
        name="投资映射 Agent",
        objective="把产业链环节映射到股票池，区分核心资产、弹性标的、主题标的和风险标的。",
        findings=findings,
        evidence=evidence,
        data_gaps=[
            "缺少完整同业公司池和海外可比公司池。",
            "缺少估值分位、盈利预测、资金流向和机构持仓数据，股票池分层仍偏初步。",
        ],
        next_actions=[
            "为每个产业链节点建立 A股/港股/美股映射表。",
            "引入估值、盈利增速、流动性、资金流和新闻催化剂作为二次排序因子。",
            "区分直接受益公司和间接受益公司，避免概念扩散。",
        ],
    )


def _report_generation_agent(
    topic_report: dict[str, Any],
    chain_report: dict[str, Any],
    target_company: dict[str, Any],
) -> AgentSection:
    target = target_company.get("name") or "目标公司"
    recommendation = topic_report.get("recommendation") or "继续跟踪"
    sentiment = topic_report.get("sentiment_label") or "neutral"
    conclusion = chain_report.get("conclusion") or []
    findings = [
        f"正式报告应锁定目标公司：{target}。",
        f"当前系统建议：{recommendation}；市场情绪：{sentiment}。",
        "最终报告应先写证据强度，再写投资结论，避免先有结论后找材料。",
    ]
    evidence = [str(item) for item in conclusion[:5]]
    return AgentSection(
        name="报告生成 Agent",
        objective="把前四个 Agent 的结果整理为核心结论、产业链图谱、市场空间、公司分析、催化剂、风险和跟踪指标。",
        findings=findings,
        evidence=evidence,
        data_gaps=[
            "正式报告需要更强的财务、估值、市场空间和同行比较数据支撑。",
            "若没有客户路径和订单证据，结论必须写为待验证。",
        ],
        next_actions=[
            "将五个 Agent 的结构化结果作为 LLM professional report 的唯一输入源。",
            "报告生成后增加 FactCheck/Critic，检查目标公司是否跑偏、是否混入同行业务、是否编造数据。",
            "HTML 报告中单独展示 Agent 证据链和数据缺口。",
        ],
    )


def _final_conclusion(agents: list[AgentSection], target_company: dict[str, Any]) -> list[str]:
    target = target_company.get("name") or "目标公司"
    gap_count = sum(len(agent.data_gaps) for agent in agents)
    return [
        f"当前五 Agent 流水线已将研究对象锁定为 {target}。",
        "当前更适合输出“待验证的产业链瓶颈假设”，不宜直接输出强投资结论。",
        f"全流程仍有 {gap_count} 个主要数据缺口，优先补充财报、研报、客户路径、产能约束和估值数据。",
    ]


def _target_company(topic_report: dict[str, Any], chain_report: dict[str, Any]) -> dict[str, Any]:
    if chain_report.get("target_company"):
        return chain_report["target_company"]
    companies = topic_report.get("companies") or []
    if not companies:
        return {}
    return companies[0].get("company") or {}


def _dimension_summary(dims: list[dict[str, Any]]) -> str:
    parts = []
    for dim in dims:
        parts.append(f"{dim.get('label')}={dim.get('score')}/{dim.get('weight')}")
    return "; ".join(parts)


def _doc_line(doc: dict[str, Any]) -> str:
    title = doc.get("title") or "-"
    source = doc.get("source") or "-"
    published = doc.get("published_at") or "-"
    url = doc.get("url") or doc.get("path") or "-"
    return f"{title} | {source} | {published} | {url}"


def _append_items(lines: list[str], title: str, items: list[str]) -> None:
    lines.extend(["", f"### {title}", ""])
    if not items:
        lines.append("- 暂无。")
        return
    for item in items:
        lines.append(f"- {item}")


def _unique(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
