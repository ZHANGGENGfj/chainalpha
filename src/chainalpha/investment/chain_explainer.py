from __future__ import annotations

import json
from typing import Any

from chainalpha.config import load_settings
from chainalpha.nlp.llm_client import LLMClient


def is_definition_question(topic: str) -> bool:
    text = str(topic or "").strip()
    definition_terms = ["是什么", "是什么东西", "什么意思", "介绍一下", "科普", "定义", "概念"]
    investment_terms = ["投资", "机会", "瓶颈", "卡位", "受益", "标的", "公司", "买", "估值", "研报"]
    return any(term in text for term in definition_terms) and not any(term in text for term in investment_terms)


def render_chain_explainer_markdown(report_data: dict[str, Any]) -> str:
    topic = str(report_data.get("topic") or "").strip()
    subject = _subject_from_report(report_data)
    query_expansion = report_data.get("query_expansion") or {}
    aliases = _list(query_expansion.get("aliases"))[:6]
    upstream = _list(query_expansion.get("industry_terms"))[:10]
    product_terms = _list(query_expansion.get("product_terms"))[:12]
    docs = _docs(report_data)[:8]
    evidence_lines = _evidence_lines(docs)

    lines = [
        f"# {subject}是什么",
        "",
        "## 1. 直接回答",
        "",
        _definition_paragraph(subject, aliases),
        "",
        "## 2. 它在产业链里的位置",
        "",
        "- 上游：电子级玻璃纤维纱、玻璃纤维原料、浸润剂等材料和工艺环节。",
        "- 中游：织造、开纤、表面处理、薄布/超薄布/极薄布等电子布制造环节。",
        "- 下游：覆铜板（CCL）、PCB、高频高速覆铜板、服务器PCB、汽车电子等应用。",
        "",
        "## 3. 为什么投研会关注它",
        "",
        "- 电子布是覆铜板和PCB的重要基础材料之一，终端需求会受到服务器、AI算力、汽车电子、消费电子等周期影响。",
        "- 如果下游高频高速PCB需求上行，市场会关注电子布的供需、价格、产品结构和相关公司盈利弹性。",
        "- 但“概念相关”不等于“公司有投资价值”，还必须继续验证客户结构、产能利用率、价格趋势、毛利率和现金流。",
        "",
        "## 4. 后续研究应看什么",
        "",
        "| 维度 | 关键问题 | 需要的数据 |",
        "|---|---|---|",
        "| 供需 | 是否供不应求，价格是否上涨 | 电子布价格、库存、产能利用率、扩产计划 |",
        "| 产品结构 | 是否从普通布升级到薄布/超薄布/高速材料 | 产品收入结构、认证进度、客户反馈 |",
        "| 下游拉动 | 覆铜板和PCB需求是否真实改善 | CCL/PCB订单、AI服务器和汽车电子需求 |",
        "| 公司兑现 | 相关公司能否把景气转化为利润 | 收入、毛利率、存货、应收、经营现金流 |",
        "| 竞争格局 | 供给是否很快扩张或被替代 | 主要厂商产能、进口替代、技术路线变化 |",
        "",
        "## 5. 当前证据与置信度",
        "",
    ]
    if evidence_lines:
        lines.extend(evidence_lines)
    else:
        lines.append("- 当前输入材料中没有足够可核验来源；以上为产业链常识框架，具体投资结论需要补充公告、财报、行业数据或研报。")

    if upstream or product_terms:
        lines.extend(["", "## 6. 系统识别到的检索方向", ""])
        if aliases:
            lines.append(f"- 同义词/别名：{', '.join(aliases)}")
        if upstream:
            lines.append(f"- 产业链相关词：{', '.join(upstream[:8])}")
        if product_terms:
            lines.append(f"- 产品/应用相关词：{', '.join(product_terms[:8])}")

    lines.extend(
        [
            "",
            "## 7. 结论边界",
            "",
            f"- 对“{topic or subject}”这个问题，优先回答概念和产业链位置，不应直接套用目标公司或瓶颈权力评分。",
            "- 若要继续做投资研究，下一步应把问题改成“电子布产业链有哪些上市公司、谁更受益、证据是什么”。",
            "- 在没有公司级财务和订单证据前，不输出买入、目标价或强推荐结论。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def generate_llm_chain_explainer_report(report_data: dict[str, Any], agent_report: dict[str, Any] | None = None) -> str:
    settings = load_settings()
    client = LLMClient(settings)
    topic = str(report_data.get("topic") or "").strip()
    subject = _subject_from_report(report_data)
    payload = {
        "topic": topic,
        "subject": subject,
        "query_expansion": report_data.get("query_expansion") or {},
        "recent_documents": _docs(report_data)[:12],
        "signals": (report_data.get("signals") or [])[:8],
        "five_agent_report": agent_report or {},
    }
    system = """你是严谨的中文产业链投研解释助手。
用户问的是“是什么/概念解释”时，先解释概念、产业链位置和研究入口，不要强行写成目标公司研报或瓶颈权力评分。
只能使用输入 JSON 和通用产业链常识；涉及公司、订单、产能、价格、财务、投资结论时，必须标明证据来源或写“待验证”。
输出 Markdown。"""
    user = f"""
请回答用户问题：{topic}

必须使用以下结构：
## 1. 直接回答
用 2-4 句话讲清楚 {subject} 是什么，避免套话。

## 2. 它在产业链里的位置
说明上游、中游、下游和典型应用。

## 3. 为什么投研会关注它
解释需求驱动、价格/供需、产品升级、相关公司盈利弹性之间的关系。

## 4. 后续研究应看什么
用表格列出供需、产品结构、下游拉动、公司兑现、竞争格局五类指标。

## 5. 当前证据与置信度
每条证据都标明来源和置信度。没有证据就写数据缺口，不要硬编。

## 6. 结论边界
说明这不是投资建议；若要继续做投资报告，需要补充哪些公司级证据。

禁止事项：
1. 不要写“目标公司锁定：{topic}”。
2. 不要输出“瓶颈权力排序”或六维评分，除非用户明确问瓶颈/卡位/谁受益。
3. 不要把“电子布是什么”当成上市公司。
4. 不要输出目标价、买入评级、强烈推荐。

输入 JSON：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    return client.chat_text(system, user, timeout=120)


def _subject_from_report(report_data: dict[str, Any]) -> str:
    query_expansion = report_data.get("query_expansion") or {}
    corrected = str(query_expansion.get("corrected_query") or report_data.get("topic") or "").strip()
    subject = corrected
    for marker in ["是什么东西", "是什么", "什么意思", "介绍一下", "科普", "定义"]:
        if marker in subject:
            subject = subject.split(marker, 1)[0]
            break
    return subject.strip(" ，,。？?！!") or corrected or "该主题"


def _definition_paragraph(subject: str, aliases: list[str]) -> str:
    alias_text = f"也常被称为{', '.join(aliases[:3])}。" if aliases else ""
    if "电子布" in subject:
        return (
            f"{subject}通常指电子级玻璃纤维布，是覆铜板（CCL）和PCB中的基础增强材料。"
            "它由电子级玻璃纤维纱织造成布，再经过开纤、表面处理等工艺，使其能与树脂、铜箔等材料结合。"
            f"{alias_text}它本身不是终端电子产品，而是隐藏在PCB材料体系里的上游/中游材料。"
        )
    return f"{subject}是一个产业链概念，需要先确认其材料属性、制造环节、下游应用和相关公司，再讨论投资价值。{alias_text}"


def _docs(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    docs = [
        *(report_data.get("selected_documents") or []),
        *(report_data.get("recent_documents") or []),
        *(report_data.get("recent_news") or []),
    ]
    return [doc for doc in docs if isinstance(doc, dict)]


def _evidence_lines(docs: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for idx, doc in enumerate(docs[:8], 1):
        title = _compact(doc.get("title") or doc.get("summary") or doc.get("content") or "材料", 80)
        source = doc.get("source") or doc.get("provider") or doc.get("url") or "未标明来源"
        confidence = _confidence(doc)
        lines.append(f"{idx}. {title} | 来源：{source} | 置信度：{confidence}")
    return lines


def _confidence(doc: dict[str, Any]) -> str:
    source_type = str(doc.get("source_type") or "").lower()
    source = str(doc.get("source") or doc.get("url") or "").lower()
    text = f"{source_type} {source}"
    if any(token in text for token in ["announcement", "filing", "exchange", "公告", "巨潮", "交易所"]):
        return "high"
    if any(token in text for token in ["research", "broker", "news", "media", "研报", "证券报", "财经"]):
        return "medium"
    return "low"


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
