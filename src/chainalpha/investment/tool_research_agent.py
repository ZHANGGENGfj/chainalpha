from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict

from chainalpha.config import Settings
from chainalpha.connectors.base import FetchRequest, SourceDocument
from chainalpha.connectors.media_crawler_adapter import MediaCrawlerConnector
from chainalpha.connectors.search_page import SearchPageConnector
from chainalpha.nlp.llm_client import LLMClient


ToolAction = Literal["local_search", "web_search", "finish"]


FINANCE_SEARCH_TEMPLATES = [
    "https://so.eastmoney.com/news/s?keyword={query}",
    "https://search.10jqka.com.cn/search?w={query}&tid=info",
    "https://www.cnstock.com/searchResult?word={query}",
    "https://www.cs.com.cn/searchlist.html?keyword={query}",
    "http://search.zqrb.cn/search.php?src=all&q={query}&f=_all&s=newsdate_DESC",
    "https://www.nbd.com.cn/search/article_search/?utf8=%E2%9C%93&q={query}",
]

FINANCE_ALLOWED_DOMAINS = [
    "eastmoney.com",
    "so.eastmoney.com",
    "10jqka.com.cn",
    "cs.com.cn",
    "cnstock.com",
    "zqrb.cn",
    "nbd.com.cn",
]


class ToolAgentState(TypedDict, total=False):
    question: str
    target_name: str
    target_ticker: str
    next_action: str
    next_query: str
    next_reason: str
    evidence: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    warnings: list[str]
    enough: bool
    final_answer: str
    iteration: int
    stalled_rounds: int
    last_new_evidence: int


@dataclass
class ToolResearchResult:
    question: str
    answer_markdown: str
    evidence: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    framework: str = "langgraph"
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "generated_at": self.generated_at,
            "framework": self.framework,
            "answer_markdown": self.answer_markdown,
            "evidence": self.evidence,
            "steps": self.steps,
            "warnings": self.warnings,
        }


class LangGraphResearchAgent:
    """DeepSeek/OpenAI-compatible tool-use research agent.

    The model decides the next action in JSON. LangGraph controls state and
    routing. Python executes tools such as local MediaCrawler search and public
    finance web search.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        media_crawler_root: str | Path | None = None,
        enable_local_search: bool = True,
        enable_web_search: bool = True,
        top_k: int = 6,
        max_iterations: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.client = LLMClient(settings)
        self.media_crawler_root = Path(media_crawler_root) if media_crawler_root else Path("./third_party/MediaCrawler-main/data")
        self.enable_local_search = enable_local_search
        self.enable_web_search = enable_web_search
        self.top_k = top_k
        # Retained only for backwards-compatible callers. Completion is driven
        # by evidence coverage and search exhaustion, not a fixed iteration count.
        self.max_iterations = max_iterations
        self.progress_callback = progress_callback

    def run(
        self,
        question: str,
        *,
        target_name: str = "",
        target_ticker: str = "",
    ) -> ToolResearchResult:
        app, framework = self._compile_graph()
        initial: ToolAgentState = {
            "question": question,
            "target_name": target_name.strip(),
            "target_ticker": target_ticker.strip(),
            "evidence": [],
            "steps": [],
            "warnings": [],
            "iteration": 0,
            "stalled_rounds": 0,
            "last_new_evidence": 0,
            "enough": False,
        }
        final_state = app.invoke(initial)
        return ToolResearchResult(
            question=question,
            answer_markdown=final_state.get("final_answer") or "",
            evidence=final_state.get("evidence") or [],
            steps=final_state.get("steps") or [],
            warnings=final_state.get("warnings") or [],
            framework=framework,
        )

    def _compile_graph(self) -> tuple[Any, str]:
        try:
            from langgraph.graph import END, StateGraph  # type: ignore
        except ImportError:
            return _FallbackGraph(self), "local_fallback"

        graph = StateGraph(ToolAgentState)
        graph.add_node("plan", self._plan_node)
        graph.add_node("local_search", self._local_search_node)
        graph.add_node("web_search", self._web_search_node)
        graph.add_node("judge", self._judge_node)
        graph.add_node("write", self._write_node)
        graph.set_entry_point("plan")
        graph.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {
                "local_search": "local_search",
                "web_search": "web_search",
                "write": "write",
            },
        )
        graph.add_edge("local_search", "judge")
        graph.add_edge("web_search", "judge")
        graph.add_conditional_edges(
            "judge",
            self._route_after_judge,
            {
                "plan": "plan",
                "write": "write",
            },
        )
        graph.add_edge("write", END)
        return graph.compile(), "langgraph"

    def _plan_node(self, state: ToolAgentState) -> ToolAgentState:
        decision = self._decide_next_action(state)
        action = str(decision.get("action") or "").strip()
        if action not in {"local_search", "web_search", "finish"}:
            action = self._default_action(state)
        if not self.enable_local_search and action == "local_search":
            action = "web_search" if self.enable_web_search else "finish"
        if not self.enable_web_search and action == "web_search":
            action = "local_search" if self.enable_local_search else "finish"
        if action == "finish" and len(state.get("evidence") or []) < 2 and (self.enable_local_search or self.enable_web_search):
            action = self._default_action(state)
        coverage_plan = self._coverage_search_plan(state)
        if coverage_plan is not None:
            action, query, coverage_reason = coverage_plan
            decision = {**decision, "reason": coverage_reason}
        elif self._coverage_gaps(state):
            # Every coverage query was attempted. Do not fall back to another
            # vague query that merely creates more landing-page evidence.
            action, query = "finish", ""
            decision = {**decision, "reason": "核心缺口已逐项检索，未获得新的可引用页面。"}
        else:
            action, query = "finish", ""
            decision = {**decision, "reason": "核心证据类别已覆盖，结束工具检索。"}
        if action != "finish" and self._query_was_used(state, action, query):
            query = self._fallback_query(state, action)

        self._emit_progress(
            {
                "type": "tool_plan",
                "title": "工具检索决策",
                "message": f"{action}: {query}",
                "action": action,
                "query": query,
                "reason": str(decision.get("reason") or ""),
            }
        )
        return {
            **state,
            "next_action": action,
            "next_query": query,
            "next_reason": str(decision.get("reason") or ""),
        }

    def _local_search_node(self, state: ToolAgentState) -> ToolAgentState:
        docs, warning = self._run_local_search(state.get("next_query") or state["question"])
        return self._append_tool_result(state, "local_search", docs, warning)

    def _web_search_node(self, state: ToolAgentState) -> ToolAgentState:
        docs, warning = self._run_web_search(state.get("next_query") or state["question"])
        return self._append_tool_result(state, "web_search", docs, warning)

    def _judge_node(self, state: ToolAgentState) -> ToolAgentState:
        iteration = int(state.get("iteration") or 0) + 1
        gaps = self._coverage_gaps(state)
        stalled_rounds = int(state.get("stalled_rounds") or 0)
        if int(state.get("last_new_evidence") or 0) == 0:
            stalled_rounds += 1
        else:
            stalled_rounds = 0
        enough = not gaps or (self._coverage_search_plan(state) is None and stalled_rounds >= 1)
        self._emit_progress(
            {
                "type": "tool_judge",
                "title": "证据充分性判断",
                "message": f"coverage={','.join(gaps) or 'complete'}, new={state.get('last_new_evidence') or 0}",
                "iteration": iteration,
                "enough": enough,
                "evidence_count": len(state.get("evidence") or []),
            }
        )
        return {
            **state,
            "iteration": iteration,
            "stalled_rounds": stalled_rounds,
            "enough": enough,
        }

    def _write_node(self, state: ToolAgentState) -> ToolAgentState:
        answer = self._write_final_answer(state)
        self._emit_progress(
            {
                "type": "model_output",
                "title": "工具 Agent 阶段性结论",
                "message": answer[:800],
                "evidence_count": len(state.get("evidence") or []),
            }
        )
        return {**state, "final_answer": answer}

    def _route_after_plan(self, state: ToolAgentState) -> str:
        action = state.get("next_action") or "finish"
        if action == "local_search":
            return "local_search"
        if action == "web_search":
            return "web_search"
        return "write"

    def _route_after_judge(self, state: ToolAgentState) -> str:
        return "write" if state.get("enough") else "plan"

    def _decide_next_action(self, state: ToolAgentState) -> dict[str, Any]:
        system = """你是 ChainAlpha 的投研检索调度 Agent。
你不能直接编造答案，只能决定下一步是否调用工具。

可用工具：
1. local_search：检索本地 MediaCrawler 新闻、公告、研报、社媒和历史记忆。
2. web_search：检索公开财经网页并抓取可访问正文。
3. finish：证据足够时结束检索。

输出必须是 JSON：
{
  "action": "local_search|web_search|finish",
  "query": "下一步检索关键词",
  "reason": "为什么需要这一步"
}

规则：
- 涉及最新事实、公告、财务、估值、资金流、政策、产业链时，必须先检索。
- 本地库不够时用 web_search。
- 没有一手来源或交叉验证时不要 finish。
- query 要具体，包含公司/代码/指标/事件词。
- 同一个 action + query 不得重复；财务信息不足时，优先检索营业收入、净利润、毛利率、经营现金流、存货、应收等明确指标。"""
        user = json.dumps(
            {
                "question": state["question"],
                "target_company": state.get("target_name") or "",
                "target_ticker": state.get("target_ticker") or "",
                "coverage_gaps": self._coverage_gaps(state),
                "steps": state.get("steps") or [],
                "evidence": [_short_evidence(item) for item in (state.get("evidence") or [])[:10]],
                "local_search_enabled": self.enable_local_search,
                "web_search_enabled": self.enable_web_search,
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            return self.client.chat_json(system, user, timeout=60)
        except Exception as exc:  # noqa: BLE001
            return {
                "action": self._default_action(state),
                "query": state["question"],
                "reason": f"LLM action planning failed: {type(exc).__name__}: {exc}",
            }

    def _default_action(self, state: ToolAgentState) -> ToolAction:
        used = {str(step.get("action")) for step in (state.get("steps") or [])}
        if self.enable_local_search and "local_search" not in used:
            return "local_search"
        if self.enable_web_search and "web_search" not in used:
            return "web_search"
        return "finish"

    def _coverage_search_plan(self, state: ToolAgentState) -> tuple[ToolAction, str, str] | None:
        """Use a fixed coverage ladder before asking the model to stop research.

        A generic company query often returns landing pages and commentary. The
        ladder keeps every tool call attributable to a report section.
        """
        target = str(state.get("target_name") or "").strip()
        ticker = str(state.get("target_ticker") or "").split(".")[0].strip()
        prefix = " ".join(part for part in [target, ticker] if part).strip()
        if not prefix:
            return None
        steps = state.get("steps") or []
        actions = [str(step.get("action") or "") for step in steps if isinstance(step, dict)]
        if self.enable_local_search and "local_search" not in actions:
            return "local_search", f"{prefix} 年报 季报 公告 业绩", "先核验本地公告、财报和已入库新闻。"
        if not self.enable_web_search:
            return None

        gaps = self._coverage_gaps(state)
        plans = {
            "financial_core": (
                f"{prefix} 2025 年报 营业收入 净利润 扣非净利润 毛利率",
                "补齐收入、利润和毛利率的可追溯财务事实。",
            ),
            "working_capital": (
                f"{prefix} 经营现金流 存货 应收账款 研发费用 在建工程",
                "补齐现金流和营运资本，验证利润质量。",
            ),
            "business_structure": (
                f"{prefix} 主营业务 分产品收入 分地区收入 前五大客户",
                "补齐业务结构、收入来源和客户集中度。",
            ),
            "market_valuation": (
                f"{prefix} PE PB PS 市值 股价 资金流向 两融",
                "补齐估值、行情和资金面；无可靠数据时明确记录缺口。",
            ),
        }
        for gap in gaps:
            query, reason = plans[gap]
            if not self._query_was_used(state, "web_search", query):
                return "web_search", query, reason
        return None

    def _coverage_gaps(self, state: ToolAgentState) -> list[str]:
        text = " ".join(
            " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("content_excerpt") or ""),
                    str(item.get("content") or ""),
                ]
            ).lower()
            for item in (state.get("evidence") or [])
            if isinstance(item, dict)
        )
        requirements = {
            "financial_core": ("营业收入", "营收", "净利润", "归母"),
            "working_capital": ("经营现金流", "现金流", "存货", "应收账款"),
            "business_structure": ("主营", "分产品", "客户", "地区收入"),
            "market_valuation": ("市盈率", "pe", "市净率", "pb", "市值", "股价"),
        }
        return [name for name, hints in requirements.items() if not any(hint.lower() in text for hint in hints)]

    def _query_was_used(self, state: ToolAgentState, action: str, query: str) -> bool:
        normalized = " ".join(query.lower().split())
        return any(
            str(step.get("action") or "") == action
            and " ".join(str(step.get("query") or "").lower().split()) == normalized
            for step in (state.get("steps") or [])
            if isinstance(step, dict)
        )

    def _fallback_query(self, state: ToolAgentState, action: str) -> str:
        target = str(state.get("target_name") or "").strip()
        ticker = str(state.get("target_ticker") or "").split(".")[0].strip()
        prefix = " ".join(part for part in [target, ticker] if part).strip() or state["question"]
        iteration = int(state.get("iteration") or 0)
        if action == "local_search":
            return f"{prefix} 公告 年报 季报 业绩"
        plans = [
            f"{prefix} 财务分析 营业收入 净利润 毛利率 经营现金流",
            f"{prefix} 存货 应收账款 研发费用 在建工程 财务报告",
            f"{prefix} 主营构成 客户供应商 机构研报 估值",
        ]
        return plans[min(iteration, len(plans) - 1)]

    def _run_local_search(self, query: str) -> tuple[list[SourceDocument], str]:
        if not self.enable_local_search:
            return [], "local_search disabled"
        if not self.media_crawler_root.exists():
            return [], f"MediaCrawler root not found: {self.media_crawler_root}"
        connector = MediaCrawlerConnector(
            "agent_local_media_crawler",
            root_dir=self.media_crawler_root,
            platforms=["finance_news", "cninfo_reports", "broker_reports", "claim_tasks", "cls", "xq"],
            modes=["processed", "raw", "cache"],
        )
        try:
            docs = connector.fetch(FetchRequest(query=query, keywords=[query], limit=self.top_k))
        except Exception as exc:  # noqa: BLE001
            return [], f"local_search failed: {type(exc).__name__}: {exc}"
        return docs, ""

    def _run_web_search(self, query: str) -> tuple[list[SourceDocument], str]:
        if not self.enable_web_search:
            return [], "web_search disabled"
        connector = SearchPageConnector(
            "agent_finance_web_search",
            search_url_templates=FINANCE_SEARCH_TEMPLATES,
            allowed_domains=FINANCE_ALLOWED_DOMAINS,
            source_type="mainstream_media",
            include_search_pages=False,
            fetch_linked_pages=True,
            max_linked_pages_per_query=2,
            timeout=10,
        )
        try:
            docs = connector.fetch(FetchRequest(query=query, keywords=[query], limit=self.top_k))
        except Exception as exc:  # noqa: BLE001
            return [], f"web_search failed: {type(exc).__name__}: {exc}"
        return docs, ""

    def _append_tool_result(
        self,
        state: ToolAgentState,
        action: str,
        docs: list[SourceDocument],
        warning: str,
    ) -> ToolAgentState:
        relevant_docs, ignored = self._target_relevant_docs(docs, state)
        packed = [_pack_doc(doc) for doc in relevant_docs[: self.top_k]]
        prior_evidence = state.get("evidence") or []
        evidence = _dedup_evidence([*prior_evidence, *packed])
        new_evidence = max(0, len(evidence) - len(prior_evidence))
        warnings = [*(state.get("warnings") or [])]
        if warning:
            warnings.append(warning)
        if ignored:
            warnings.append(f"{action}: filtered {ignored} non-target document(s) before evidence injection")
        effective_warning = warning or (f"filtered {ignored} non-target document(s)" if ignored else "")
        steps = [
            *(state.get("steps") or []),
            {
                "step": len(state.get("steps") or []) + 1,
                "action": action,
                "query": state.get("next_query") or state["question"],
                "reason": state.get("next_reason") or "",
                "documents_found": len(packed),
                "warning": effective_warning,
            },
        ]
        self._emit_progress(
            {
                "type": "tool_result",
                "title": "检索结果",
                "message": f"{action} found {new_evidence} new document(s)",
                "action": action,
                "query": state.get("next_query") or state["question"],
                "documents_found": len(packed),
                "warning": effective_warning,
                "documents": [_short_evidence(item) for item in packed[:5]],
            }
        )
        return {
            **state,
            "evidence": evidence,
            "warnings": warnings,
            "steps": steps,
            "last_new_evidence": new_evidence,
        }

    def _target_relevant_docs(
        self,
        docs: list[SourceDocument],
        state: ToolAgentState,
    ) -> tuple[list[SourceDocument], int]:
        """Drop broad search pages and articles for another issuer before RAG injection."""
        name = str(state.get("target_name") or "").strip().lower()
        code = str(state.get("target_ticker") or "").split(".")[0].strip()
        if not name and not code:
            return docs, 0
        kept: list[SourceDocument] = []
        ignored = 0
        for doc in docs:
            if str((doc.metadata or {}).get("kind") or "") == "search_or_list_page":
                ignored += 1
                continue
            text = " ".join(
                [doc.title or "", doc.content or "", doc.url or "", json.dumps(doc.metadata or {}, ensure_ascii=False)]
            ).lower()
            if (name and name in text) or (code and code in text):
                kept.append(doc)
            else:
                ignored += 1
        return kept, ignored

    def _emit_progress(self, event: dict[str, Any]) -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback(event)
        except Exception:
            pass

    def _judge_evidence(self, state: ToolAgentState) -> bool:
        system = """你是投研证据质量检查 Agent。只判断证据是否足够回答问题。
输出 JSON：{"sufficient": true|false, "reason": "..."}。
如果缺少财务、行情、公告、来源或交叉验证，返回 false。"""
        user = json.dumps(
            {
                "question": state["question"],
                "evidence": [_short_evidence(item) for item in (state.get("evidence") or [])[:12]],
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            result = self.client.chat_json(system, user, timeout=45)
        except Exception:
            return False
        return bool(result.get("sufficient"))

    def _write_final_answer(self, state: ToolAgentState) -> str:
        system = """你是严谨的中文投研分析师。
只能使用输入证据回答。不要编造事实、客户、订单、估值、预测或时间点。
如果证据不足，必须写“数据缺口”和下一步检索方向。
输出 Markdown。"""
        user = json.dumps(
            {
                "question": state["question"],
                "tool_steps": state.get("steps") or [],
                "evidence": state.get("evidence") or [],
                "warnings": state.get("warnings") or [],
                "required_sections": ["结论", "证据链", "交叉验证情况", "仍然缺什么", "下一步检索计划"],
            },
            ensure_ascii=False,
            indent=2,
        )
        return self.client.chat_text(system, user, timeout=120)


class _FallbackGraph:
    def __init__(self, agent: LangGraphResearchAgent) -> None:
        self.agent = agent

    def invoke(self, state: ToolAgentState) -> ToolAgentState:
        current = state
        # The normal exit is evidence coverage/search exhaustion. This guard
        # only prevents an unexpected connector bug from looping forever.
        for _ in range(16):
            current = self.agent._plan_node(current)
            action = current.get("next_action")
            if action == "finish":
                break
            if action == "local_search":
                current = self.agent._local_search_node(current)
            elif action == "web_search":
                current = self.agent._web_search_node(current)
            current = self.agent._judge_node(current)
            if current.get("enough"):
                break
        return self.agent._write_node(current)


def _pack_doc(doc: SourceDocument) -> dict[str, Any]:
    return {
        "id": doc.id,
        "title": doc.title,
        "source": doc.source,
        "source_type": doc.source_type,
        "url": doc.url,
        "path": doc.path,
        "published_at": doc.published_at.isoformat() if doc.published_at else None,
        "content_excerpt": doc.content[:1200],
    }


def _short_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title"),
        "source": item.get("source"),
        "source_type": item.get("source_type"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "content_excerpt": str(item.get("content_excerpt") or "")[:300],
    }


def _dedup_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("url") or item.get("path") or item.get("title") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
