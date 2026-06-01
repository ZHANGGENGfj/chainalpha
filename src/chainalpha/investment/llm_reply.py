from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chainalpha.config import load_settings
from chainalpha.nlp.llm_client import LLMClient
from chainalpha.schemas import InvestmentCandidate, WeakSignal


SYSTEM_PROMPT = """You are ChainAlpha, an investment research assistant.
Use only the provided signals and candidate data. Do not invent facts.
Write in Chinese. Be concise, decision-oriented, and clear about uncertainty.
This is research support, not investment advice."""


def generate_investment_reply(
    query: str,
    signals: list[WeakSignal],
    candidates: list[InvestmentCandidate],
    max_candidates: int = 8,
    max_signals: int = 12,
) -> dict[str, Any]:
    settings = load_settings()
    client = LLMClient(settings)
    payload = {
        "query": query,
        "signals": [_signal_brief(signal) for signal in signals[:max_signals]],
        "candidates": [_candidate_brief(candidate) for candidate in candidates[:max_candidates]],
    }
    user = f"""
请基于以下 JSON 数据，生成最终投研回复。只允许使用 JSON 中的信息，不要编造外部事实。

输出必须是 JSON，字段如下：
{{
  "executive_summary": "一句话总结",
  "industry_chain_view": ["产业链层面的判断"],
  "top_candidates": [
    {{
      "company": "公司名",
      "ticker": "代码",
      "view": "为什么值得跟踪",
      "key_evidence": ["证据"],
      "risks": ["风险"],
      "next_actions": ["下一步验证"]
    }}
  ],
  "risk_warnings": ["整体风险"],
  "data_gaps": ["当前数据缺口"],
  "final_note": "免责声明和使用建议"
}}

输入数据：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_json(SYSTEM_PROMPT, user, timeout=90)


def render_investment_reply(reply: dict[str, Any]) -> str:
    lines: list[str] = ["# LLM Investment Reply", ""]
    summary = str(reply.get("executive_summary") or "").strip()
    if summary:
        lines.extend(["## 核心结论", "", summary, ""])

    _append_list(lines, "产业链判断", reply.get("industry_chain_view"))

    top_candidates = reply.get("top_candidates") or []
    if top_candidates:
        lines.extend(["## 候选公司", ""])
        for idx, item in enumerate(top_candidates, 1):
            company = item.get("company") or "Unknown"
            ticker = item.get("ticker") or "-"
            lines.extend([f"### {idx}. {company} ({ticker})", ""])
            view = item.get("view")
            if view:
                lines.extend([str(view), ""])
            _append_list(lines, "关键证据", item.get("key_evidence"), heading_level=4)
            _append_list(lines, "风险", item.get("risks"), heading_level=4)
            _append_list(lines, "下一步验证", item.get("next_actions"), heading_level=4)

    _append_list(lines, "整体风险", reply.get("risk_warnings"))
    _append_list(lines, "数据缺口", reply.get("data_gaps"))

    final_note = str(reply.get("final_note") or "").strip()
    if final_note:
        lines.extend(["## 备注", "", final_note, ""])

    return "\n".join(lines).strip() + "\n"


def generate_topic_reply(report: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    client = LLMClient(settings)
    user = f"""
请基于以下专题研究 JSON，生成最终中文投研回复。只允许使用输入中的信息，不要编造外部事实。

输出必须是 JSON，字段如下：
{{
  "executive_summary": "一句话总结",
  "recent_news_summary": ["最近新闻/公告重点"],
  "industry_chain": {{
    "core_nodes": ["核心节点"],
    "upstream": ["上游"],
    "downstream": ["下游"]
  }},
  "business_logic": ["商业逻辑，例如新产品、扩产、订单、价格传导如何影响业绩和股价"],
  "financial_takeaways": ["财务报表观察"],
  "focus_companies": [
    {{
      "company": "公司名",
      "ticker": "代码",
      "view": "看点",
      "evidence": ["证据"],
      "risks": ["风险"]
    }}
  ],
  "key_risks": ["整体风险"],
  "next_steps": ["下一步验证"],
  "final_note": "最后提醒"
}}

输入 JSON：
{json.dumps(report, ensure_ascii=False, indent=2)}
"""
    return client.chat_json(SYSTEM_PROMPT, user, timeout=90)


def generate_event_signal_markdown(report: dict[str, Any]) -> str:
    """Use the configured local/OpenAI-compatible LLM to interpret event signals.

    The deterministic rule layer extracts signals; this function turns those
    signals into investment-language reasoning with evidence strength,
    verification points, and falsification conditions.
    """
    settings = load_settings()
    client = LLMClient(settings)
    payload = _event_signal_payload(report)
    system = """你是一名中文买方/卖方交叉背景的投研分析师。
你的任务不是复述新闻，而是把结构化事件信号解释成投资研究语言。
只能使用输入 JSON 中的信息，不得编造订单金额、客户名称、估值倍数、财务预测或外部事实。
如果证据不足，必须明确写“数据缺口”或“证据强度较弱”。
输出 Markdown。"""
    user = f"""
请基于输入 JSON，为目标公司生成一段更有逻辑的“市场情绪与事件信号 + 情景分析”。

输出必须包含以下结构：

## 六、市场情绪与事件信号

### 1. 情绪判断
- 解释情绪分数来自哪些事件，而不是只写分数。
- 判断当前是基本面驱动、事件催化驱动，还是情绪/题材驱动。

### 2. 核心事件分组
按以下类别分组，不要机械罗列 event code：
- 正向催化
- 基本面验证
- 估值预期差
- 风险与证伪

### 3. 重点事件解读
对每个重要事件写清楚：
- 事件事实
- 投研含义
- 影响路径：收入 / 毛利率 / 费用率 / 现金流 / 估值 / 风险
- 证据强度：强 / 中 / 弱
- 后续验证点
- 证伪条件

## 七、情景分析

必须分别写：
- 未来3个月
- 未来1年
- 未来3年

每个时间维度都要包含：
- 乐观情景
- 中性情景
- 悲观情景
- 需要跟踪的数据

写作要求：
1. 聚焦目标公司，不要泛泛写整个产业链。
2. 不要把“相关公司新闻”直接等同于目标公司订单或客户认证。
3. 对间接新闻要明确说明“间接证据”。
4. 如果缺少订单金额、客户名称、财务数据、估值倍数，必须写成数据缺口。
5. 语言要像专业投研备忘录，不要像新闻摘要。

输入 JSON：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=120)


def render_topic_reply(reply: dict[str, Any]) -> str:
    lines = ["# LLM Topic Research Reply", ""]
    summary = str(reply.get("executive_summary") or "").strip()
    if summary:
        lines.extend(["## 核心结论", "", summary, ""])

    _append_list(lines, "最近新闻", reply.get("recent_news_summary"))

    chain = reply.get("industry_chain") or {}
    if chain:
        lines.extend(["## 产业链条", ""])
        lines.append(f"- Core nodes: {', '.join(chain.get('core_nodes', [])) or '-'}")
        lines.append(f"- Upstream: {', '.join(chain.get('upstream', [])) or '-'}")
        lines.append(f"- Downstream: {', '.join(chain.get('downstream', [])) or '-'}")
        lines.append("")

    _append_list(lines, "商业逻辑", reply.get("business_logic"))
    _append_list(lines, "财务观察", reply.get("financial_takeaways"))

    companies = reply.get("focus_companies") or []
    if companies:
        lines.extend(["## 重点公司", ""])
        for idx, item in enumerate(companies, 1):
            company = item.get("company") or "Unknown"
            ticker = item.get("ticker") or "-"
            lines.extend([f"### {idx}. {company} ({ticker})", ""])
            if item.get("view"):
                lines.extend([str(item["view"]), ""])
            _append_list(lines, "证据", item.get("evidence"), heading_level=4)
            _append_list(lines, "风险", item.get("risks"), heading_level=4)

    _append_list(lines, "整体风险", reply.get("key_risks"))
    _append_list(lines, "下一步验证", reply.get("next_steps"))

    final_note = str(reply.get("final_note") or "").strip()
    if final_note:
        lines.extend(["## 备注", "", final_note, ""])
    return "\n".join(lines).strip() + "\n"


def _event_signal_payload(report: dict[str, Any], max_signals: int = 12, max_docs: int = 12) -> dict[str, Any]:
    companies = report.get("companies") or []
    primary = companies[0] if companies else {}
    primary_company = primary.get("company") or {}
    return {
        "topic": report.get("topic"),
        "generated_at": report.get("generated_at"),
        "target_company": {
            "name": primary_company.get("name"),
            "ticker": primary_company.get("ticker"),
            "industry": primary_company.get("industry"),
            "role": primary_company.get("role"),
            "nodes": primary_company.get("nodes") or [],
        },
        "sentiment": {
            "score": report.get("sentiment_score"),
            "label": report.get("sentiment_label"),
        },
        "valuation_view": report.get("valuation_view") or [],
        "signals": [_signal_dict_brief(item) for item in (report.get("signals") or [])[:max_signals]],
        "selected_documents": [_doc_dict_brief(item) for item in (report.get("selected_documents") or [])[:max_docs]],
        "recent_documents": [_doc_dict_brief(item) for item in (report.get("recent_documents") or [])[:max_docs]],
        "scenarios": report.get("scenarios") or [],
        "tracking_indicators": report.get("tracking_indicators") or [],
        "warnings": report.get("warnings") or [],
    }


def _signal_dict_brief(signal: dict[str, Any]) -> dict[str, Any]:
    event = signal.get("event") or {}
    hypothesis = signal.get("hypothesis") or {}
    return {
        "title": signal.get("title"),
        "stage": signal.get("stage"),
        "weak_signal_score": signal.get("weak_signal_score"),
        "attention_score": signal.get("attention_score"),
        "event_type": event.get("event_type"),
        "source_type": event.get("source_type"),
        "source": event.get("source"),
        "supply_chain_nodes": event.get("supply_chain_nodes") or [],
        "evidence": event.get("evidence") or [],
        "causal_path": hypothesis.get("causal_path") or [],
        "hypothesis": hypothesis,
        "risks": signal.get("risks") or [],
    }


def _doc_dict_brief(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": doc.get("title"),
        "source": doc.get("source"),
        "source_type": doc.get("source_type"),
        "url": doc.get("url"),
        "path": doc.get("path"),
        "published_at": doc.get("published_at"),
        "content": str(doc.get("content") or "")[:800],
    }


def generate_professional_topic_markdown(report: dict[str, Any]) -> str:
    settings = load_settings()
    client = LLMClient(settings)
    system = """You are a senior sell-side equity analyst writing a professional sector/company note in Chinese.
Use only the provided data. Do not invent facts. If evidence is weak or missing, say so clearly.
Write polished Markdown with a formal research-report tone. Keep it concise but substantial."""
    user = f"""
请基于以下 JSON 资料，撰写一份像券商专题简报风格的中文 Markdown 研究报告。

硬性要求：
1. 只能使用输入 JSON 中出现的信息，不要补充外部事实。
2. 语气专业、克制、像正式研报，不要口语化。
3. 明确区分事实、推断、风险和数据缺口。
4. 如果主题相关信号较弱，也要如实写出“当前更适合跟踪，不宜下强结论”。
5. 输出必须是 Markdown，不要输出 JSON。

建议结构：
# 标题
## 核心结论
## 近期信息与事件梳理
## 产业链位置与传导逻辑
## 重点公司比较
## 财务与股价观察
## 投资关注点与风险提示
## 结论
## 免责声明

输入 JSON：
{json.dumps(report, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=120)


def _candidate_brief(candidate: InvestmentCandidate) -> dict[str, Any]:
    return {
        "company": candidate.company.name,
        "ticker": candidate.company.ticker,
        "industry": candidate.company.industry,
        "nodes": candidate.company.nodes,
        "score": candidate.score,
        "rating": candidate.rating,
        "thesis": candidate.thesis,
        "catalysts": candidate.catalysts[:5],
        "risks": candidate.risks[:5],
        "evidence": candidate.evidence[:5],
        "next_actions": candidate.next_actions[:5],
        "financial": candidate.financial.to_dict() if hasattr(candidate.financial, "to_dict") else (
            candidate.financial.__dict__ if candidate.financial else None
        ),
        "price": candidate.price.__dict__ if candidate.price else None,
    }


def _signal_brief(signal: WeakSignal) -> dict[str, Any]:
    return {
        "title": signal.title,
        "stage": signal.stage,
        "score": signal.weak_signal_score,
        "attention": signal.attention_score,
        "event_type": signal.event.event_type,
        "source_type": signal.event.source_type,
        "nodes": signal.event.supply_chain_nodes,
        "causal_path": signal.hypothesis.causal_path,
        "evidence": signal.event.evidence[:3],
        "risks": signal.risks[:3],
    }


def _append_list(lines: list[str], title: str, values: Any, heading_level: int = 2) -> None:
    if not values:
        return
    prefix = "#" * heading_level
    lines.extend([f"{prefix} {title}", ""])
    for value in values:
        lines.append(f"- {value}")
    lines.append("")


def generate_professional_topic_markdown(report: dict[str, Any]) -> str:
    settings = load_settings()
    client = LLMClient(settings)
    system = """You are a senior sell-side equity analyst and report critic writing a professional Chinese equity research note.
Use only the provided JSON data. Do not invent facts, numbers, customers, forecasts, or valuation multiples.
If a required data point is missing, explicitly write "数据缺口" and explain what must be verified.
Before writing conclusions, run an internal fact check:
- Make sure company business descriptions match the target company, not peers.
- Do not mix a peer company's business into the target company.
- Make sure financial data belongs to the same report period when the JSON allows verification.
- Separate fact, inference, risk, and data gap.
Write polished Markdown with a formal research-report tone. The report must be analytical, not a simple announcement summary."""
    user = f"""
请基于以下 JSON 资料，撰写一份专业中文 Markdown 投资研究报告。

硬性要求：
1. 只能使用输入 JSON 中出现的信息，不要补充外部事实、客户名单、预测数值或估值倍数。
2. 如果缺少数据，必须写成“数据缺口”，不要编造。
3. 必须回答：为什么是它、增长来自哪里、增长能持续多久、毛利率能否维持、估值贵不贵、市场预期打到什么程度、哪些数据变坏会推翻结论。
4. 不得把同行或产业链其他公司的业务描述套到目标公司身上。
5. 输出必须是 Markdown，不要输出 JSON。

必须包含以下章节，不能省略：
# 标题
## 核心结论
## 事实校验
## 公司商业模式拆解
## 收入拆分与增长归因
## 产品结构与技术路线
## 客户结构与订单能见度
## 行业空间测算框架
## 产业链价值分配
## 竞争格局与可比公司
## 财务质量与现金流
## 估值分析
## 三年盈利预测框架
## 市场预期差
## 股价驱动因素
## 关键跟踪指标仪表盘
## 反方观点与证伪条件
## 评级体系
## 报告自检
## 风险提示
## 免责声明

写作规则：
- 对每个关键结论给出“依据 / 推断 / 数据缺口”。
- 估值、预测、同行比较如果缺少 PE/PB/PS、历史分位、盈利预测等数据，要明确列为数据缺口。
- 对光模块主题，应重点检查高速光模块、800G/1.6T、硅光、LPO/CPO、海外云厂商需求、毛利率、存货、应收、CapEx 这些维度；但没有 JSON 证据时只能作为“待验证跟踪项”。

输入 JSON：
{json.dumps(report, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=180)


def generate_professional_topic_markdown(report: dict[str, Any]) -> str:
    settings = load_settings()
    client = LLMClient(settings)
    skill_context = _load_topic_equity_research_skill_context()
    system = """You are a senior sell-side equity analyst and report critic writing a professional Chinese equity research note.
Use only the provided JSON data and the provided local skill/template instructions.
Do not invent facts, numbers, customers, forecasts, valuation multiples, or market-share data.
If a required data point is missing, explicitly write "数据缺口".
The output is invalid unless it follows the exact 12-section template from the skill."""
    user = f"""
你必须先读取并遵守以下本地 skill/template 内容：

{skill_context}

请基于以下 JSON 资料，撰写一份专业中文 Markdown 投资研究报告。

硬性规则：
1. 严格使用 skill 里的 12 个一级章节，不能改名、不能换顺序、不能合并、不能省略。
2. 每个一级章节下必须覆盖 skill 模板列出的二级要点。
3. 只能使用输入 JSON 中的信息，不得编造外部事实、客户名称、估值倍数、预测数据或市场份额。
4. 缺少数据时必须写“数据缺口”，并说明后续需要补什么。
5. 不得把同行或产业链其他公司的业务描述套到目标公司身上。
6. 结论必须区分：事实、推断、风险、数据缺口。
7. 输出必须是 Markdown，不要输出 JSON。

输入 JSON：
{json.dumps(report, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=180)


def _load_topic_equity_research_skill_context() -> str:
    skill_dir = _find_topic_equity_research_skill_dir()
    if skill_dir is None:
        return (
            "Skill file not found. Fall back to mandatory template: "
            "use 12 sections from 核心结论 through 附录, and write 数据缺口 for missing data."
        )

    parts: list[str] = []
    for relative_path in [
        "SKILL.md",
        "references/report-template.md",
        "references/report-quality.md",
    ]:
        path = skill_dir / relative_path
        if path.exists():
            parts.append(f"--- {relative_path} ---\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _find_topic_equity_research_skill_dir() -> Path | None:
    candidates: list[Path] = []
    cwd = Path.cwd().resolve()
    candidates.extend(parent / ".codex" / "skills" / "topic-equity-research" for parent in [cwd, *cwd.parents])
    file_path = Path(__file__).resolve()
    candidates.extend(parent / ".codex" / "skills" / "topic-equity-research" for parent in file_path.parents)
    for path in candidates:
        if (path / "SKILL.md").exists():
            return path
    return None


def generate_professional_topic_markdown(report: dict[str, Any]) -> str:
    settings = load_settings()
    client = LLMClient(settings)
    system = """You are a senior sell-side equity analyst and report critic writing a professional Chinese equity research note.
Use only the provided JSON data. Do not invent facts, numbers, customers, forecasts, valuation multiples, or market-share data.
If a required data point is missing, explicitly write "数据缺口".
Check that the target company's business description is not mixed with peer-company descriptions.
Write polished Markdown in the exact requested structure."""
    user = f"""
请基于以下 JSON 资料，撰写一份专业中文 Markdown 投资研究报告。

硬性规则：
1. 只能使用输入 JSON 中的信息，不得编造外部事实、客户名称、估值倍数、预测数据或市场份额。
2. 缺少数据时必须写“数据缺口”，并说明后续需要补什么。
3. 不得把同行或产业链其他公司的业务描述套到目标公司身上。
4. 结论必须区分：事实、推断、风险、数据缺口。
5. 输出必须是 Markdown，不要输出 JSON。

报告必须严格使用以下 12 个一级章节和二级要点：

# 标题

## 1. 核心结论
- 一句话结论
- 投资评级
- 核心看多逻辑
- 核心风险

## 2. 公司概况与商业模式
- 主营业务
- 产品结构
- 客户结构
- 收入来源
- 产业链位置

## 3. 近期事件与市场表现
- 财报事件
- 订单事件
- 技术事件
- 股价表现
- 资金流向

## 4. 财务深度分析
- 收入增长归因
- 毛利率与净利率
- 费用率
- 现金流
- 存货与应收
- 杜邦分析

## 5. 行业与产业链分析
- AI 数据中心需求
- 光模块市场空间
- 速率升级路径
- 上游供应链
- 下游客户需求

## 6. 竞争格局
- 国内同行
- 海外同行
- 市占率
- 技术路线
- 竞争优势与短板

## 7. 估值分析
- 历史估值分位
- 同行估值对比
- PEG
- 情景估值

## 8. 盈利预测
- 未来三年收入预测
- 毛利率假设
- 净利润预测
- EPS 预测
- 敏感性分析

## 9. 预期差与催化剂
- 市场已经定价什么
- 可能超预期什么
- 未来催化剂
- 关键时间点

## 10. 风险与证伪条件
- 主要风险
- 反方观点
- 证伪指标
- 风险等级

## 11. 跟踪指标仪表盘
- 季度财务指标
- 行业指标
- 客户 CapEx
- 产品放量进度
- 技术路线变化

## 12. 附录
- 数据来源
- 关键假设
- 计算公式
- 免责声明

输入 JSON：
{json.dumps(report, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=180)
