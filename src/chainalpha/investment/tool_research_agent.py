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
    next_action: str
    next_query: str
    next_reason: str
    evidence: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    warnings: list[str]
    enough: bool
    final_answer: str
    iteration: int


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
        max_iterations: int = 4,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.client = LLMClient(settings)
        self.media_crawler_root = Path(media_crawler_root) if media_crawler_root else Path("./third_party/MediaCrawler-main/data")
        self.enable_local_search = enable_local_search
        self.enable_web_search = enable_web_search
        self.top_k = top_k
        self.max_iterations = max_iterations
        self.progress_callback = progress_callback

    def run(self, question: str) -> ToolResearchResult:
        app, framework = self._compile_graph()
        initial: ToolAgentState = {
            "question": question,
            "evidence": [],
            "steps": [],
            "warnings": [],
            "iteration": 0,
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

        self._emit_progress(
            {
                "type": "tool_plan",
                "title": "工具检索决策",
                "message": f"{action}: {decision.get('query') or state['question']}",
                "action": action,
                "query": str(decision.get("query") or state["question"]),
                "reason": str(decision.get("reason") or ""),
            }
        )
        return {
            **state,
            "next_action": action,
            "next_query": str(decision.get("query") or state["question"]),
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
        enough = False
        if iteration >= self.max_iterations:
            enough = True
        elif len(state.get("evidence") or []) >= self.top_k:
            enough = self._judge_evidence(state)
        self._emit_progress(
            {
                "type": "tool_judge",
                "title": "证据充分性判断",
                "message": f"iteration={iteration}, enough={enough}, evidence={len(state.get('evidence') or [])}",
                "iteration": iteration,
                "enough": enough,
                "evidence_count": len(state.get("evidence") or []),
            }
        )
        return {**state, "iteration": iteration, "enough": enough}

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
- query 要具体，包含公司/代码/指标/事件词。"""
        user = json.dumps(
            {
                "question": state["question"],
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
        packed = [_pack_doc(doc) for doc in docs[: self.top_k]]
        evidence = _dedup_evidence([*(state.get("evidence") or []), *packed])
        warnings = [*(state.get("warnings") or [])]
        if warning:
            warnings.append(warning)
        steps = [
            *(state.get("steps") or []),
            {
                "step": len(state.get("steps") or []) + 1,
                "action": action,
                "query": state.get("next_query") or state["question"],
                "reason": state.get("next_reason") or "",
                "documents_found": len(packed),
                "warning": warning,
            },
        ]
        self._emit_progress(
            {
                "type": "tool_result",
                "title": "检索结果",
                "message": f"{action} found {len(packed)} document(s)",
                "action": action,
                "query": state.get("next_query") or state["question"],
                "documents_found": len(packed),
                "warning": warning,
                "documents": [_short_evidence(item) for item in packed[:5]],
            }
        )
        return {**state, "evidence": evidence, "warnings": warnings, "steps": steps}

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
        for _ in range(self.agent.max_iterations):
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
