from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chainalpha.config import load_settings
from chainalpha.reports.evidence_pack import build_professional_evidence_pack
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
    payload = _topic_reply_payload(report)
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
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_json(SYSTEM_PROMPT, user, timeout=90)


def _topic_reply_payload(report: dict[str, Any], max_docs: int = 8, max_signals: int = 8) -> dict[str, Any]:
    companies = report.get("companies") or []
    primary = companies[0] if companies else {}
    return {
        "topic": report.get("topic"),
        "generated_at": report.get("generated_at"),
        "intent": report.get("intent"),
        "target_company": primary.get("company") or {},
        "business_summary": primary.get("business_summary"),
        "key_products": primary.get("key_products") or [],
        "upstream_nodes": primary.get("upstream_nodes") or [],
        "downstream_nodes": primary.get("downstream_nodes") or [],
        "financial": _trim_for_prompt(primary.get("financial")),
        "price": _trim_for_prompt(primary.get("price")),
        "valuation_view": _trim_for_prompt(report.get("valuation_view") or []),
        "recent_documents": [_doc_dict_brief(item, max_content=360) for item in (report.get("recent_documents") or [])[:max_docs]],
        "selected_documents": [_doc_dict_brief(item, max_content=360) for item in (report.get("selected_documents") or [])[:max_docs]],
        "signals": [_signal_dict_brief(item) for item in (report.get("signals") or [])[:max_signals]],
        "sentiment": {"score": report.get("sentiment_score"), "label": report.get("sentiment_label")},
        "scenarios": _trim_for_prompt(report.get("scenarios") or []),
        "tracking_indicators": _trim_for_prompt(report.get("tracking_indicators") or []),
        "warnings": _trim_for_prompt(report.get("warnings") or []),
    }


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
            if not isinstance(item, dict):
                lines.extend([f"### {idx}. {str(item)}", ""])
                continue
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


def _doc_dict_brief(doc: dict[str, Any], max_content: int = 800) -> dict[str, Any]:
    return {
        "title": doc.get("title"),
        "source": doc.get("source"),
        "source_type": doc.get("source_type"),
        "url": doc.get("url"),
        "path": doc.get("path"),
        "published_at": doc.get("published_at"),
        "content": str(doc.get("content") or "")[:max_content],
    }


def _trim_for_prompt(value: Any, *, max_string: int = 500, max_list: int = 8) -> Any:
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, list):
        return [_trim_for_prompt(item, max_string=max_string, max_list=max_list) for item in value[:max_list]]
    if isinstance(value, dict):
        return {key: _trim_for_prompt(item, max_string=max_string, max_list=max_list) for key, item in value.items()}
    return value


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
    """Final active professional-report prompt.

    Keep this definition at the end of the module: earlier versions are legacy
    prompt attempts and Python will use the last definition.
    """
    settings = load_settings()
    client = LLMClient(settings)
    payload = _professional_report_payload(report)
    system = """你是严谨的中文卖方投研分析师。你必须只使用输入 JSON 里的证据写报告。
禁止输出 <think>、思考过程、JSON 或代码。禁止编造事实、客户、订单金额、估值倍数、预测数值、市场份额、关键时间点。
如果字段标记为 low confidence，只能写成“待核验线索”，不能当成确定事实。
如果缺少硬数据，写“数据缺口”，但仍要用已有新闻和信号解释商业逻辑、验证路径和证伪条件。
禁止在报告正文出现输入 JSON 的程序字段名，例如 news_evidence、signals、financial_snapshot、broker_report_summaries、data_quality_warnings。
没有证据时，禁止写具体客户名称、具体百分比阈值、具体年份/季度节点、具体市场份额或具体订单节奏。
目标是写成有逻辑的投研备忘录，不是机械模板填空。"""
    user = f"""
请基于输入 JSON 写一份专业中文 Markdown 投资研究报告。

必须严格使用以下 12 个一级章节，标题、编号、顺序不能改变：
## 1. 核心结论
## 2. 公司概况与商业模式
## 3. 近期事件与市场表现
## 4. 财务深度分析
## 5. 行业与产业链分析
## 6. 竞争格局
## 7. 估值分析
## 8. 盈利预测
## 9. 预期差与催化剂
## 10. 风险与证伪条件
## 11. 跟踪指标仪表盘
## 12. 附录

写作要求：
1. 每个章节都要有内容；缺少硬数据时，不要只写一句“数据缺口”，要说明“当前已有证据能支持什么、不能支持什么、下一步需要补什么”。
2. 优先使用 target_company、business_model、financial_snapshot、price_snapshot、valuation_view、news_evidence、signals。
3. financial_snapshot / price_snapshot 如果 confidence=low，只能作为待核验线索，不能写成最新财报或真实行情结论。
4. 不要把 peer_or_chain_companies 的业务写成 target_company 的业务。
5. 对每个关键判断尽量写清楚：事实依据、投研含义、后续验证点、证伪条件。
6. 估值和盈利预测没有硬数据时，写“分析框架 + 数据缺口”，不要编造 PE、EPS、净利润预测。
7. 附录必须列出使用到的数据来源和数据质量提示。

输入 JSON：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=180)


def generate_professional_topic_markdown(report: dict[str, Any]) -> str:
    """Final active professional-report prompt.

    Keep this definition at the end of the module: earlier versions are legacy
    prompt attempts and Python will use the last definition.
    """
    settings = load_settings()
    client = LLMClient(settings)
    payload = _professional_report_payload(report)
    system = """你是严谨的中文卖方投研分析师。你必须只使用输入 JSON 里的证据写报告。
禁止输出 <think>、思考过程、JSON 或代码。禁止编造事实、客户、订单金额、估值倍数、预测数值、市场份额、关键时间点。
如果字段标记为 low confidence，只能写成“待核验线索”，不能当成确定事实。
如果缺少硬数据，写“数据缺口”，但仍要用已有新闻和信号解释商业逻辑、验证路径和证伪条件。
目标是写成有逻辑的投研备忘录，不是机械模板填空。"""
    user = f"""
请基于输入 JSON 写一份专业中文 Markdown 投资研究报告。

必须严格使用以下 12 个一级章节，标题、编号、顺序不能改变：
## 1. 核心结论
## 2. 公司概况与商业模式
## 3. 近期事件与市场表现
## 4. 财务深度分析
## 5. 行业与产业链分析
## 6. 竞争格局
## 7. 估值分析
## 8. 盈利预测
## 9. 预期差与催化剂
## 10. 风险与证伪条件
## 11. 跟踪指标仪表盘
## 12. 附录

写作要求：
1. 每个章节都要有内容；缺少硬数据时，不要只写一句“数据缺口”，要说明“当前已有证据能支持什么、不能支持什么、下一步需要补什么”。
2. 优先使用 target_company、business_model、financial_snapshot、price_snapshot、valuation_view、news_evidence、signals。
3. financial_snapshot / price_snapshot 如果 confidence=low，只能作为待核验线索，不能写成最新财报或真实行情结论。
4. 不要把 peer_or_chain_companies 的业务写成 target_company 的业务。
5. 对每个关键判断尽量写清楚：事实依据、投研含义、后续验证点、证伪条件。
6. 估值和盈利预测没有硬数据时，写“分析框架 + 数据缺口”，不要编造 PE、EPS、净利润预测。
7. 附录必须列出使用到的数据来源和数据质量提示。

输入 JSON：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=180)


def generate_professional_topic_markdown(report: dict[str, Any]) -> str:
    """Generate the strict 12-section professional report from an evidence pack.

    This final definition intentionally overrides the older prompt variants
    above. The model receives a compact, labeled evidence pack instead of the
    entire raw report dict, which reduces hallucination and makes available
    financial/news fields easier to use.
    """
    settings = load_settings()
    client = LLMClient(settings)
    payload = _professional_report_payload(report)
    system = """你是严谨的中文卖方投研分析师。你必须只使用输入 JSON 里的证据写报告。
禁止输出 <think>、思考过程、JSON 或代码。禁止编造事实、客户、订单金额、估值倍数、预测数值、市场份额、关键时间点。
如果字段标记为 low confidence，只能写成“待核验线索”，不能当成确定事实。
如果缺少硬数据，写“数据缺口”，但仍要用已有新闻和信号解释商业逻辑、验证路径和证伪条件。
目标是写成有逻辑的投研备忘录，不是机械模板填空。"""
    user = f"""
请基于输入 JSON 写一份专业中文 Markdown 投资研究报告。

必须严格使用以下 12 个一级章节，标题、编号、顺序不能改变：
## 1. 核心结论
## 2. 公司概况与商业模式
## 3. 近期事件与市场表现
## 4. 财务深度分析
## 5. 行业与产业链分析
## 6. 竞争格局
## 7. 估值分析
## 8. 盈利预测
## 9. 预期差与催化剂
## 10. 风险与证伪条件
## 11. 跟踪指标仪表盘
## 12. 附录

写作要求：
1. 每个章节都要有内容；缺少硬数据时，不要只写一句“数据缺口”，要说明“当前已有证据能支持什么、不能支持什么、下一步需要补什么”。
2. 优先使用 target_company、business_model、financial_snapshot、price_snapshot、valuation_view、news_evidence、signals。
3. financial_snapshot / price_snapshot 如果 confidence=low，只能作为待核验线索，不能写成最新财报或真实行情结论。
4. 不要把 peer_or_chain_companies 的业务写成 target_company 的业务。
5. 对每个关键判断尽量写清楚：事实依据、投研含义、后续验证点、证伪条件。
6. 估值和盈利预测没有硬数据时，写“分析框架 + 数据缺口”，不要编造 PE、EPS、净利润预测。
7. 附录必须列出使用到的数据来源和数据质量提示。

输入 JSON：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=180)


def _professional_report_payload(report: dict[str, Any], max_docs: int = 12, max_signals: int = 10) -> dict[str, Any]:
    companies = report.get("companies") or []
    primary = companies[0] if companies else {}
    company = primary.get("company") or {}
    financial = primary.get("financial")
    price = primary.get("price")
    docs = _dedup_docs([*(report.get("selected_documents") or []), *(report.get("recent_documents") or [])])[:max_docs]
    signals = (report.get("signals") or [])[:max_signals]
    warnings = list(report.get("warnings") or [])
    if financial and _snapshot_confidence(financial) == "low":
        warnings.append("财务快照来自新闻/社交文本规则抽取，不能等同于财报数据库；报告中只能作为待核验线索。")
    if price and _snapshot_confidence(price) == "low":
        warnings.append("行情快照来自新闻文本规则抽取，不能等同于行情数据库；报告中只能作为待核验线索。")

    return {
        "topic": report.get("topic"),
        "generated_at": report.get("generated_at"),
        "target_company": {
            "name": company.get("name"),
            "ticker": company.get("ticker"),
            "exchange": company.get("exchange"),
            "industry": company.get("industry"),
            "role": company.get("role"),
            "nodes": company.get("nodes") or [],
            "aliases": company.get("aliases") or [],
        },
        "business_model": {
            "business_summary": primary.get("business_summary"),
            "key_products": primary.get("key_products") or [],
            "upstream_nodes": primary.get("upstream_nodes") or [],
            "downstream_nodes": primary.get("downstream_nodes") or [],
            "note": "上下游节点来自系统产业链图谱，只能用于分析传导路径，不能自动视为目标公司的客户或供应商事实。",
        },
        "peer_or_chain_companies": {
            "upstream_companies": primary.get("upstream_companies") or [],
            "downstream_companies": primary.get("downstream_companies") or [],
            "note": "这些公司是产业链相关公司，不等于目标公司可比估值表，除非新闻证据明确支持。",
        },
        "financial_snapshot": _snapshot_pack(financial),
        "price_snapshot": _snapshot_pack(price),
        "six_dimension_analysis": report.get("six_dimension_analysis") or [],
        "valuation_view": report.get("valuation_view") or [],
        "news_evidence": [_doc_pack(doc) for doc in docs],
        "signals": [_signal_dict_brief(sig) for sig in signals],
        "sentiment": {
            "score": report.get("sentiment_score"),
            "label": report.get("sentiment_label"),
        },
        "scenarios": report.get("scenarios") or [],
        "tracking_indicators": report.get("tracking_indicators") or [],
        "data_quality_warnings": warnings,
        "mandatory_data_gaps": _mandatory_data_gaps(financial, price),
    }


def _snapshot_pack(snapshot: Any) -> dict[str, Any] | None:
    if not snapshot:
        return None
    if isinstance(snapshot, dict):
        data = dict(snapshot)
    else:
        data = dict(getattr(snapshot, "__dict__", {}))
    return {
        "confidence": _snapshot_confidence(data),
        "usable_as": "待核验线索" if _snapshot_confidence(data) == "low" else "结构化数据",
        "data": data,
    }


def _snapshot_confidence(snapshot: Any) -> str:
    metadata = (snapshot or {}).get("metadata") if isinstance(snapshot, dict) else getattr(snapshot, "metadata", {})
    source_type = str((metadata or {}).get("source_type") or "").lower()
    provider = str((metadata or {}).get("provider") or "").lower()
    trusted_tokens = ("financial", "price_data", "quote", "mootdx", "akshare", "tushare", "announcement", "filing")
    return "high" if any(token in source_type or token in provider for token in trusted_tokens) else "low"


def _dedup_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for doc in docs:
        key = str(doc.get("url") or doc.get("title") or doc.get("id") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out


def _doc_pack(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": doc.get("title"),
        "source": doc.get("source"),
        "source_type": doc.get("source_type"),
        "published_at": doc.get("published_at"),
        "url": doc.get("url"),
        "content_excerpt": str(doc.get("content") or "")[:1000],
        "metadata_hint": {
            "platform": (doc.get("metadata") or {}).get("platform"),
            "file": (doc.get("metadata") or {}).get("file"),
        },
    }


def _mandatory_data_gaps(financial: Any, price: Any) -> list[str]:
    gaps: list[str] = []
    if not financial or _snapshot_confidence(financial) == "low":
        gaps.extend([
            "最新年报/季报的营业收入、归母净利润、扣非净利润、毛利率、净利率、ROE。",
            "经营现金流、存货、应收账款、资产负债率、资本开支等利润质量指标。",
        ])
    else:
        data = financial if isinstance(financial, dict) else getattr(financial, "__dict__", {})
        for label, key in [
            ("报告期", "period"),
            ("经营现金流", "operating_cash_flow"),
            ("资产负债率", "debt_ratio"),
            ("ROE", "roe"),
            ("PE", "pe"),
            ("PS", "ps"),
        ]:
            if data.get(key) is None:
                gaps.append(f"{label}缺失。")
    if not price or _snapshot_confidence(price) == "low":
        gaps.append("真实行情快照、近1/3/6/12个月涨跌幅、资金流向数据缺失。")
    gaps.append("历史估值分位、同行估值表、三年盈利预测和敏感性分析需要结构化数据源补充。")
    return gaps


def generate_evidence_based_professional_topic_markdown(
    report: dict[str, Any],
    *,
    local_financial_summaries: list[dict[str, Any]] | None = None,
    broker_report_summaries: list[dict[str, Any]] | None = None,
    chunked: bool = True,
) -> str:
    """Generate a professional report from a compact evidence pack.

    This uniquely named entry point avoids the legacy duplicate
    ``generate_professional_topic_markdown`` definitions that still exist in
    this module.
    """
    settings = load_settings()
    client = LLMClient(settings)
    payload = build_professional_evidence_pack(
        report,
        local_financial_summaries=local_financial_summaries,
        broker_report_summaries=broker_report_summaries,
    )
    if chunked:
        return generate_chunked_professional_topic_report(payload, client=client)["markdown"]
    system = """你是严谨的中文卖方投研分析师。你必须只使用输入 JSON 里的证据写报告。
禁止输出 <think>、思考过程、JSON 或代码。禁止编造事实、客户、订单金额、估值倍数、预测数值、市场份额、关键时间点。
如果字段标记为 low confidence，只能写成“待核验线索”，不能当成确定事实。
如果缺少硬数据，写“数据缺口”，但仍要用已有新闻和信号解释商业逻辑、验证路径和证伪条件。
目标是写成有逻辑的投研备忘录，不是机械模板填空。"""
    user = f"""
请基于输入 JSON 写一份专业中文 Markdown 投资研究报告。

必须严格使用以下 12 个一级章节，标题、编号、顺序不能改变：
## 1. 核心结论
## 2. 公司概况与商业模式
## 3. 近期事件与市场表现
## 4. 财务深度分析
## 5. 行业与产业链分析
## 6. 竞争格局
## 7. 估值分析
## 8. 盈利预测
## 9. 预期差与催化剂
## 10. 风险与证伪条件
## 11. 跟踪指标仪表盘
## 12. 附录

写作要求：
1. 每个章节都要有内容；缺少硬数据时，不要只写一句“数据缺口”，要说明“当前已有证据能支持什么、不能支持什么、下一步需要补什么”。
2. 优先使用 target_company、business_model、financial_snapshot、price_snapshot、valuation_view、news_evidence、signals。
3. financial_report_summaries 是从财报/PDF抽出的摘要卡片；broker_report_summaries 是从机构研报抽出的观点卡片。可以引用这些卡片，但精确数值必须说明需要回到原文复核。
4. financial_snapshot / price_snapshot 如果 confidence=low，只能作为待核验线索，不能写成最新财报或真实行情结论。
5. business_model.corrected_chain_logic 优先级高于原始 upstream_nodes/downstream_nodes；尤其光模块公司不能把 GPU 集群写成直接上游，也不能把激光器/光器件机械写成下游客户。
6. 不要把 peer_or_chain_companies 的业务写成 target_company 的业务。
7. 对每个关键判断尽量写清楚：事实依据、投研含义、后续验证点、证伪条件。
8. 估值和盈利预测没有硬数据时，写“分析框架 + 数据缺口”，不要编造 PE、EPS、净利润预测。
9. 如果缺少客户、订单、良率、份额、时间点证据，只能写“需核验客户/订单/良率/份额/时间点”，不能举具体公司名或具体数值例子。
10. 附录必须用自然语言列出数据来源和数据质量提示，不要出现程序字段名。

输入 JSON：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=180)


def generate_chunked_professional_topic_report(
    payload: dict[str, Any],
    *,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    client = client or LLMClient(settings)
    chunk_notes: list[dict[str, str]] = []
    for idx, chunk in enumerate(_professional_payload_chunks(payload), 1):
        chunk_notes.append(
            {
                "chunk": chunk["chunk_name"],
                "markdown": _summarize_professional_chunk(client, chunk, idx=idx),
            }
        )
    markdown = _compose_professional_report_from_chunk_notes(client, payload, chunk_notes)
    return {"markdown": markdown, "chunk_notes": chunk_notes}


def _professional_payload_chunks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    base = {
        "topic": payload.get("topic"),
        "generated_at": payload.get("generated_at"),
        "research_type": payload.get("research_type"),
        "topic_class": payload.get("topic_class"),
        "query_expansion": payload.get("query_expansion"),
        "target_company": payload.get("target_company"),
        "business_model": payload.get("business_model"),
        "peer_or_chain_companies": payload.get("peer_or_chain_companies"),
        "financial_snapshot": payload.get("financial_snapshot"),
        "price_snapshot": payload.get("price_snapshot"),
        "web_financial_facts": payload.get("web_financial_facts") or {},
        "financial_fact_matrix": payload.get("financial_fact_matrix") or [],
        "six_dimension_analysis": payload.get("six_dimension_analysis"),
        "valuation_view": payload.get("valuation_view"),
        "sentiment": payload.get("sentiment"),
        "scenarios": payload.get("scenarios"),
        "tracking_indicators": payload.get("tracking_indicators"),
        "data_quality_warnings": payload.get("data_quality_warnings"),
        "tool_research_summary": payload.get("tool_research_summary"),
        "writing_guardrails": payload.get("writing_guardrails"),
        "mandatory_data_gaps": payload.get("mandatory_data_gaps"),
    }
    chunks: list[dict[str, Any]] = [{"chunk_name": "base_profile_and_guardrails", "payload": base}]
    chunks.extend(_split_list_payload("news_evidence", payload.get("news_evidence") or [], max_items=4))
    chunks.extend(_split_list_payload("signals", payload.get("signals") or [], max_items=5))
    chunks.extend(_split_list_payload("financial_report_summaries", payload.get("financial_report_summaries") or [], max_items=1))
    chunks.extend(_split_list_payload("broker_report_summaries", payload.get("broker_report_summaries") or [], max_items=2))
    chunks.extend(_split_list_payload("financial_fact_matrix", payload.get("financial_fact_matrix") or [], max_items=5))
    return chunks


def _split_list_payload(name: str, values: list[Any], *, max_items: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for start in range(0, len(values), max_items):
        part = values[start:start + max_items]
        if part:
            chunks.append({"chunk_name": f"{name}_{start // max_items + 1}", "payload": {name: _trim_deep(part)}})
    return chunks


def _trim_deep(value: Any, *, max_string: int = 900) -> Any:
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, list):
        return [_trim_deep(item, max_string=max_string) for item in value]
    if isinstance(value, dict):
        return {key: _trim_deep(item, max_string=max_string) for key, item in value.items()}
    return value


def _summarize_professional_chunk(client: LLMClient, chunk: dict[str, Any], *, idx: int) -> str:
    system = """你是投研资料整理员。只基于输入 JSON 生成压缩证据摘要。
禁止输出 <think>、思考过程、JSON 或代码。禁止编造客户、数字、时间点、估值和预测。
如果输入证据不足，写“数据缺口/待核验”。"""
    user = f"""
请把第 {idx} 批投研资料压缩成 Markdown 证据摘要。

要求：
1. 只保留对最终投研报告有用的信息。
2. 分成：事实、低可信线索、投研含义、风险、数据缺口、可引用证据。
3. 不要写完整报告，不要使用 12 章模板。
4. 不要出现程序字段名。

输入 JSON：
{json.dumps(chunk, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=120)


def _compose_professional_report_from_chunk_notes(
    client: LLMClient,
    payload: dict[str, Any],
    chunk_notes: list[dict[str, str]],
) -> str:
    compact_payload = {
        "topic": payload.get("topic"),
        "research_type": payload.get("research_type"),
        "topic_class": payload.get("topic_class"),
        "query_expansion": payload.get("query_expansion"),
        "target_company": payload.get("target_company"),
        "business_model": payload.get("business_model"),
        "financial_snapshot": payload.get("financial_snapshot"),
        "price_snapshot": payload.get("price_snapshot"),
        "web_financial_facts": payload.get("web_financial_facts") or {},
        "financial_fact_matrix": payload.get("financial_fact_matrix") or [],
        "six_dimension_analysis": payload.get("six_dimension_analysis"),
        "valuation_view": payload.get("valuation_view"),
        "sentiment": payload.get("sentiment"),
        "scenarios": payload.get("scenarios"),
        "tracking_indicators": payload.get("tracking_indicators"),
        "filtered_signal_count": payload.get("filtered_signal_count"),
        "data_quality_warnings": payload.get("data_quality_warnings"),
        "tool_research_summary": payload.get("tool_research_summary"),
        "writing_guardrails": payload.get("writing_guardrails"),
        "mandatory_data_gaps": payload.get("mandatory_data_gaps"),
        "chunk_notes": chunk_notes,
    }
    system = """你是严谨的中文卖方投研分析师。根据分批证据摘要合成最终报告。
禁止输出 <think>、思考过程、JSON 或代码。禁止编造事实、客户、订单金额、估值倍数、预测数值、市场份额、关键时间点。
没有证据时，不能写具体客户名、具体百分比阈值、具体年份/季度节点。
禁止在报告正文出现程序字段名。"""
    if _is_industry_chain_payload(payload):
        user = _industry_chain_pipeline_report_prompt(compact_payload)
        return client.chat_text(system, user, timeout=180)
    user = f"""
FINAL PRESENTATION RULES (take precedence over any template text below):
- Use these 11 numbered sections only: 1. 核心结论; 2. 公司概况与商业模式; 3. 近期事件与市场表现; 4. 财务深度分析; 5. 行业与产业链分析; 6. 竞争格局; 7. 盈利预测; 8. 预期差与催化剂; 9. 风险与证伪条件; 10. 跟踪指标; 11. 附录.
- Do not generate valuation analysis, valuation multiples, price targets, PEG, or valuation placeholders.
- Do not generate an investment six-dimension analysis table or any six-dimension data-gap table.
- Only show a section, subsection, table row, or table column when the supplied evidence contains a traceable fact for it.
- Never write placeholder prose such as "数据缺口", "待补", "暂无数据", "无直接证据", or an explanation of what data should be collected. Missing evidence is an internal retrieval task, not report body content.
- In the six-dimension table, retain only dimensions supported by evidence. Use exactly four columns: 维度、当前判断、证据、后续跟踪. Do not include a data-gap column.
- In the financial table, include only financial_fact_matrix rows that contain a fact, a period, and a source. Do not add empty financial categories.

请基于输入的分批证据摘要，合成一份专业中文 Markdown 投资研究报告。

必须严格使用以下 12 个一级章节，标题、编号、顺序不能改变：
## 1. 核心结论
## 2. 公司概况与商业模式
## 3. 近期事件与市场表现
## 4. 财务深度分析
## 5. 行业与产业链分析
## 6. 竞争格局
## 7. 估值分析
## 8. 盈利预测
## 9. 预期差与催化剂
## 10. 风险与证伪条件
## 11. 跟踪指标仪表盘
## 12. 附录

写作要求：
1. 使用分批摘要中的事实和待核验线索，不要回到原始长文本。
2. 每个章节都要有内容；缺少硬数据时，写验证框架和需要补充的数据。不能忽略输入中的 web_financial_facts 或 financial_fact_matrix。
3. 光模块产业链优先使用 corrected_chain_logic，不能把 GPU 集群写成直接上游，不能把激光器/光器件机械写成下游客户。
4. 附录用自然语言说明数据来源和质量，不要出现程序字段名。
5. 必须在“## 1. 核心结论”或“## 11. 跟踪指标仪表盘”中输出一个“投资六维分析表”，列为：维度、当前判断、证据、数据缺口、后续验证路径；六个维度必须包括技术面、资金面、基本面、产业景气度、政策、产业链。若某一维度缺少证据，写“数据缺口 + 后续验证路径”，不能省略。
6. 如果输入里出现 filtered_signal_count 或数据质量提示，必须说明部分规则信号已被剔除；不得把来自F10、财务网页或搜索页的低置信度规则信号写成订单、政策、扩产、客户认证等确定事实。
7. 第 3 节必须先给出“近期事件证据表”：事件、日期/报告期、来源、证据等级、投研含义；没有直接事件时明确写无直接证据。
8. 第 4 节必须优先根据 financial_fact_matrix 输出“财务事实与利润质量表”，列为：项目、已抓取事实、报告期/同比口径、来源、证据等级、投研解读。项目至少覆盖主要会计数据、毛利率、现金流、存货、应收账款、研发费用；输入没有某项目时才标注数据缺口。不得把 F10 网页的“诊断文字”误写成公司公告或审计结论。
9. 第 7 节的估值数据与第 8 节的预测数据必须严格区分：没有 PE/PB/PS 或一致预期时，写估值/预测框架和缺失来源，不要用重复的空泛风险段落代替。
10. 第 12 节必须给出“证据来源台账”，用 Markdown 链接列出实际使用的公告、F10、财务网页或新闻页面，并说明每类来源的证据等级。
11. 不能把搜索结果页、网站首页或产业链图谱节点当作事实证据；它们最多只能写为“检索线索”。公司报告中不得将泛化的 GPU、AI ASIC、模型 API 等节点写成目标公司的真实客户、供应商或业务。
12. 同一指标若混有不同报告期，必须逐项标明期间，不能把年报、季报和网页抓取日混为同一口径。F10 的自动诊断结论必须标注为“网页诊断线索，待原始报表复核”。
13. 避免重复：第 11 节只能列跟踪阈值和频率，不能复述第 1 节六维表；第 7、8 节若数据不足，各用一段简洁的数据缺口和具体补数路径即可。

输入 JSON：
{json.dumps(compact_payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=180)


def _is_industry_chain_payload(payload: dict[str, Any]) -> bool:
    research_type = str(payload.get("research_type") or "").lower()
    topic_class = str(payload.get("topic_class") or "").lower()
    return research_type == "industry-chain" or topic_class in {"raw_material_chain", "component_chain", "industry_chain"}


def _industry_chain_pipeline_report_prompt(compact_payload: dict[str, Any]) -> str:
    return f"""
请基于输入的分批证据摘要，合成一份“产业链流水线研究报告”，而不是公司研报。

硬性规则：
1. 这不是单一公司研究，不得使用“公司概况与商业模式、盈利预测、EPS预测”这类公司模板作为主结构。
2. 必须按照“源头资源/基础原料 → 上游材料/关键辅材 → 中游制造/工艺 → 下游直接应用 → 终端需求场景”的流水线顺序组织。
3. 只能使用输入证据。没有证据的公司、价格、产能、份额、毛利率、市场空间、客户关系必须写成“数据缺口/待验证”。
4. 可以列出“待核验相关公司”，但必须说明它们来自新闻、搜索线索或产业链映射，不能当作已证实供应商、客户或龙头。
5. 每个关键判断都要写清楚：证据来源、投研含义、验证路径、证伪条件。
6. 不要输出 JSON，不要输出程序字段名。
7. 必须在“## 10. 催化剂、预期差与跟踪指标”中输出一个“六维跟踪指标表”，列为：维度、当前判断、证据、数据缺口、验证路径；六个维度必须包括技术面、资金面、基本面、产业景气度、政策、产业链。
8. 如果输入里出现 filtered_signal_count 或数据质量提示，必须说明部分规则信号已被剔除；不得把来自F10、财务网页或搜索页的低置信度规则信号写成订单、政策、扩产、客户认证等确定事实。

报告必须使用以下一级章节，标题和顺序不能改：

# 产业链流水线研究报告：{{主题}}

## 1. 核心结论与研究对象定位
- 一句话结论
- 研究对象属于材料、零部件、工艺、设备、应用中的哪一类
- 当前证据强度
- 主要机会
- 主要风险

## 2. 产业链总览：从源头到终端应用
- 源头资源/基础原料
- 上游材料/关键辅材
- 中游制造/核心工艺
- 下游直接应用
- 终端需求场景
- 价值量和议价权初判

## 3. 源头资源与上游原料
- 关键原料
- 供给约束
- 价格/供需线索
- 代表公司或待核验公司
- 数据缺口

## 4. 中游制造、工艺壁垒与产能约束
- 生产流程
- 技术壁垒
- 良率/认证周期/扩产周期
- 产能约束
- 替代难度

## 5. 下游应用与需求传导
- 直接下游
- 终端应用
- 需求驱动因素
- 下游变化如何传导到本环节
- 需求证伪条件

## 6. 产业链公司图谱
- 上游公司
- 中游公司
- 下游公司
- 直接受益、间接受益、主题相关、需剔除
- 每家公司与本产业链的关系证据强度

## 7. 价格、供需与库存周期
- 价格趋势线索
- 供给变化
- 需求变化
- 库存周期
- 当前周期位置
- 数据缺口

## 8. 瓶颈权力与价值分配
- 哪个环节可能是真瓶颈
- 客户路径
- 供应链位置
- 产能约束
- 替代难度
- 财务验证
- 机构预期滞后

## 9. 投资映射与标的分层
- 核心资产
- 弹性标的
- 主题标的
- 风险标的
- 为什么这样分层

## 10. 催化剂、预期差与跟踪指标
- 产业催化剂
- 公司催化剂
- 市场已经定价什么
- 可能超预期什么
- 六维跟踪指标：技术面、资金面、基本面、产业景气度、政策、产业链
- 跟踪指标仪表盘

## 11. 风险、反方观点与证伪条件
- 供给过剩
- 价格下跌
- 替代材料/技术
- 下游需求不及预期
- 竞争格局恶化
- 如何证伪投资逻辑

## 12. 数据来源、证据强度与免责声明
- 数据来源
- 事实、推断、待验证假设
- 数据缺口
- 免责声明

输入 JSON：
{json.dumps(compact_payload, ensure_ascii=False, indent=2)}
"""


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
