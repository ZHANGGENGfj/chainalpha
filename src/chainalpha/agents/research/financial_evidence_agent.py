from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any, Callable, TypedDict

from chainalpha.config import Settings
from chainalpha.connectors.base import SourceDocument
from chainalpha.memory.evidence_memory import ResearchEvidenceMemory
from chainalpha.nlp.llm_client import LLMClient
from chainalpha.agents.runtime import AgentStateStore, SkillPlan, SkillRegistry, SkillSourceProfile, ToolPerformanceStore
from chainalpha.tools import ResearchToolRegistry, ResearchToolset
from chainalpha.agents.research.task_planner import ResearchTaskPlanner


class ToolAgentState(TypedDict, total=False):
    question: str
    target_name: str
    target_ticker: str
    research_type: str
    tasks: list[dict[str, Any]]
    active_task_id: str
    task_planner: str
    next_action: str
    next_query: str
    next_reason: str
    next_skill: str
    next_capability: str
    next_source_profile: str
    evidence: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    warnings: list[str]
    enough: bool
    final_answer: str
    iteration: int
    stalled_rounds: int
    last_new_evidence: int
    max_iterations: int


@dataclass
class ToolResearchResult:
    question: str
    answer_markdown: str
    evidence: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    task_tree: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    tool_performance: dict[str, Any] = field(default_factory=dict)
    resumed: bool = False
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
            "task_tree": self.task_tree,
            "coverage": self.coverage,
            "warnings": self.warnings,
            "skills_used": self.skills_used,
            "coverage_gaps": self.coverage_gaps,
            "tool_performance": self.tool_performance,
            "resumed": self.resumed,
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
        enable_targeted_crawl: bool = True,
        top_k: int = 6,
        max_iterations: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        skill_root: str | Path | None = None,
        state_path: str | Path | None = None,
        performance_path: str | Path | None = None,
        evidence_memory_path: str | Path | None = None,
    ) -> None:
        self.client = LLMClient(settings)
        self.media_crawler_root = Path(media_crawler_root) if media_crawler_root else Path("./third_party/MediaCrawler-main/data")
        self.enable_local_search = enable_local_search
        self.enable_web_search = enable_web_search
        self.enable_targeted_crawl = enable_targeted_crawl
        self.top_k = top_k
        self.max_iterations = max(1, int(max_iterations or 10))
        self.progress_callback = progress_callback
        project_root = settings.project_root
        self.skill_registry = SkillRegistry.load(skill_root)
        if not self.skill_registry.skills:
            raise RuntimeError(f"No runtime financial skills found under: {skill_root or project_root / 'skills'}")
        self.state_store = AgentStateStore(state_path) if state_path else None
        self.performance_store = ToolPerformanceStore(
            performance_path or (project_root / "data" / "agent" / "tool_performance.json")
        )
        self.evidence_memory = ResearchEvidenceMemory(
            evidence_memory_path or (project_root / "data" / "research_memory" / "evidence.sqlite3")
        )
        self.tool_registry = ResearchToolRegistry(
            enable_local_search=enable_local_search,
            enable_web_search=enable_web_search,
            enable_targeted_crawl=enable_targeted_crawl,
            skill_registry=self.skill_registry,
        )
        self.tools = ResearchToolset(
            media_crawler_root=self.media_crawler_root,
            evidence_memory=self.evidence_memory,
            top_k=self.top_k,
            registry=self.tool_registry,
        )
        self.task_planner = ResearchTaskPlanner(self.client, self.skill_registry)

    def run(
        self,
        question: str,
        *,
        target_name: str = "",
        target_ticker: str = "",
        research_type: str = "company",
        resume: bool = True,
    ) -> ToolResearchResult:
        self._active_target = target_name.strip() or target_ticker.strip()
        app, framework = self._compile_graph()
        persisted = self.state_store.load(question=question, target_name=target_name.strip()) if self.state_store and resume else None
        initial: ToolAgentState = ({**persisted, "research_type": research_type} if persisted else {
            "question": question,
            "target_name": target_name.strip(),
            "target_ticker": target_ticker.strip(),
            "research_type": research_type,
            "tasks": [],
            "active_task_id": "",
            "task_planner": "",
            "evidence": [],
            "steps": [],
            "warnings": [],
            "iteration": 0,
            "stalled_rounds": 0,
            "last_new_evidence": 0,
            "enough": False,
            "max_iterations": self.max_iterations,
        })
        if not initial.get("tasks"):
            tasks, planner_name = self.task_planner.plan(
                question=question,
                target_name=target_name.strip(),
                target_ticker=target_ticker.strip(),
                research_type=research_type,
            )
            initial = {**initial, "tasks": tasks, "task_planner": planner_name}
            self._emit_progress({
                "type": "research_plan",
                "title": "Research task plan",
                "message": f"planned {len(tasks)} verifiable sub-question(s) via {planner_name}",
                "tasks": tasks,
            })
            self._snapshot(initial, "task_plan")
        if initial.get("final_answer"):
            final_state = initial
        else:
            try:
                final_state = app.invoke(
                    initial,
                    config={"recursion_limit": max(30, self.max_iterations * 4 + 6)},
                )
            except TypeError:
                final_state = app.invoke(initial)
        entity = target_name.strip() or target_ticker.strip() or question[:80]
        self.evidence_memory.remember(final_state.get("evidence") or [], entity=entity)
        return ToolResearchResult(
            question=question,
            answer_markdown=final_state.get("final_answer") or "",
            evidence=final_state.get("evidence") or [],
            steps=final_state.get("steps") or [],
            task_tree=final_state.get("tasks") or [],
            coverage=self._coverage_summary(final_state),
            warnings=final_state.get("warnings") or [],
            framework=framework,
            skills_used=list(dict.fromkeys(
                str(step.get("skill") or "")
                for step in (final_state.get("steps") or [])
                if step.get("skill")
            )),
            coverage_gaps=self._coverage_gaps(final_state),
            tool_performance=self.performance_store.snapshot(),
            resumed=bool(persisted),
        )

    def _compile_graph(self) -> tuple[Any, str]:
        try:
            from langgraph.graph import END, StateGraph  # type: ignore
        except ImportError:
            return _FallbackGraph(self), "local_fallback"

        graph = StateGraph(ToolAgentState)
        graph.add_node("plan", self._plan_node)
        graph.add_node("local_search", self._local_search_node)
        graph.add_node("targeted_news_crawl", self._targeted_news_crawl_node)
        graph.add_node("web_search", self._web_search_node)
        graph.add_node("judge", self._judge_node)
        graph.add_node("write", self._write_node)
        graph.set_entry_point("plan")
        graph.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {
                "local_search": "local_search",
                "targeted_news_crawl": "targeted_news_crawl",
                "web_search": "web_search",
                "write": "write",
            },
        )
        graph.add_edge("local_search", "judge")
        graph.add_edge("targeted_news_crawl", "judge")
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
        prepared = self._refresh_task_states(state)
        task = self._next_open_task(prepared)
        gaps = self._task_coverage_gaps(prepared, task) if task else []
        decision = self._decide_next_action(prepared, task=task, gaps=gaps)
        fallback = self._next_skill_plan(prepared, gaps=gaps, task=task)
        plan = self._validated_model_plan(prepared, decision, gaps, task=task)
        if plan is None:
            plan = fallback
        if not task or not gaps:
            plan = None
        if plan is None:
            action, query, skill_name, capability, source_profile = "finish", "", "", "", ""
            reason = "核心证据能力已覆盖，或所有可用Skill均已尝试。"
        else:
            action = plan.action
            query = plan.query
            skill_name = plan.skill_name
            capability = plan.capability
            source_profile = plan.source_profile
            reason = str(decision.get("reason") or plan.reason)

        self._emit_progress(
            {
                "type": "tool_plan",
                "title": "工具检索决策",
                "message": f"{action}: {query}",
                "action": action,
                "query": query,
                "reason": reason,
                "skill": skill_name,
                "capability": capability,
                "source_profile": source_profile,
                "task_id": str(task.get("id") or "") if task else "",
                "task_question": str(task.get("question") or "") if task else "",
            }
        )
        updated = {
            **prepared,
            "next_action": action,
            "next_query": query,
            "next_reason": reason,
            "next_skill": skill_name,
            "next_capability": capability,
            "next_source_profile": source_profile,
            "active_task_id": str(task.get("id") or "") if task else "",
        }
        self._snapshot(updated, "plan")
        return updated

    def _local_search_node(self, state: ToolAgentState) -> ToolAgentState:
        started = time.perf_counter()
        docs, warning = self._run_local_search(state.get("next_query") or state["question"], state)
        return self._append_tool_result(
            state,
            "local_search",
            docs,
            warning,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _web_search_node(self, state: ToolAgentState) -> ToolAgentState:
        started = time.perf_counter()
        docs, warning = self._run_web_search(state.get("next_query") or state["question"], state)
        return self._append_tool_result(
            state,
            "web_search",
            docs,
            warning,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _targeted_news_crawl_node(self, state: ToolAgentState) -> ToolAgentState:
        started = time.perf_counter()
        docs, warning = self._run_targeted_news_crawl(state.get("next_query") or state["question"], state)
        return self._append_tool_result(
            state,
            "targeted_news_crawl",
            docs,
            warning,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _judge_node(self, state: ToolAgentState) -> ToolAgentState:
        iteration = int(state.get("iteration") or 0) + 1
        reviewed = self._refresh_task_states(state, apply_stop_conditions=True)
        active_task = self._task_by_id(reviewed, str(reviewed.get("active_task_id") or ""))
        gaps = self._task_coverage_gaps(reviewed, active_task) if active_task else []
        stalled_rounds = int(reviewed.get("stalled_rounds") or 0)
        stalled_rounds = stalled_rounds + 1 if int(reviewed.get("last_new_evidence") or 0) == 0 else 0
        terminal = all(str(task.get("status") or "") in {"completed", "exhausted"} for task in (reviewed.get("tasks") or []))
        reached_limit = iteration >= int(state.get("max_iterations") or self.max_iterations)
        enough = terminal or reached_limit
        self._emit_progress(
            {
                "type": "tool_judge",
                "title": "证据充分性判断",
                "message": f"task={active_task.get('id') if active_task else 'complete'}, coverage={','.join(gaps) or 'complete'}, new={state.get('last_new_evidence') or 0}",
                "iteration": iteration,
                "enough": enough,
                "evidence_count": len(state.get("evidence") or []),
                "all_tasks_terminal": terminal,
                "reached_limit": reached_limit,
            }
        )
        updated = {
            **reviewed,
            "iteration": iteration,
            "stalled_rounds": stalled_rounds,
            "enough": enough,
        }
        self._snapshot(updated, "judge")
        return updated

    def _write_node(self, state: ToolAgentState) -> ToolAgentState:
        finalized = self._finalize_task_states(state)
        answer = self._write_final_answer(finalized)
        self._emit_progress(
            {
                "type": "model_output",
                "title": "工具 Agent 阶段性结论",
                "message": answer[:800],
                "evidence_count": len(finalized.get("evidence") or []),
            }
        )
        updated = {**finalized, "final_answer": answer, "enough": True}
        self._snapshot(updated, "write")
        return updated

    def _route_after_plan(self, state: ToolAgentState) -> str:
        action = state.get("next_action") or "finish"
        if action == "local_search":
            return "local_search"
        if action == "targeted_news_crawl":
            return "targeted_news_crawl"
        if action == "web_search":
            return "web_search"
        return "write"

    def _route_after_judge(self, state: ToolAgentState) -> str:
        return "write" if state.get("enough") else "plan"

    def _decide_next_action(
        self,
        state: ToolAgentState,
        *,
        task: dict[str, Any] | None,
        gaps: list[str],
    ) -> dict[str, Any]:
        system = """你是 ChainAlpha 的金融Skill调度Agent。你只决定下一步Skill、能力和工具，不直接回答问题。
从输入的runtime_skills中选择一个仍缺失的capability，并选择它声明的action。
输出严格JSON：
{
  "skill": "skill name",
  "capability": "missing capability",
  "action": "local_search|targeted_news_crawl|web_search|finish",
  "source_profile": "a source profile declared by the selected capability",
  "query": "包含目标公司、代码、指标和期间的具体查询",
  "reason": "选择依据"
}
不得选择Skill未声明的能力或工具；不得重复已执行的查询；证据能力全部覆盖或工具耗尽时才能finish。"""
        user = json.dumps(
            {
                "question": state["question"],
                "target_company": state.get("target_name") or "",
                "target_ticker": state.get("target_ticker") or "",
                "research_type": state.get("research_type") or "company",
                "active_task": task or {},
                "coverage_gaps": gaps,
                "runtime_skills": self.skill_registry.describe_for_model(str(state.get("research_type") or "company")),
                "tool_performance": self.performance_store.snapshot(),
                "steps": state.get("steps") or [],
                "evidence": [_short_evidence(item) for item in (state.get("evidence") or [])[:10]],
                "local_search_enabled": self.enable_local_search,
                "targeted_news_crawl_enabled": self.enable_targeted_crawl,
                "web_search_enabled": self.enable_web_search,
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            return self.client.chat_json(system, user, timeout=60)
        except Exception as exc:  # noqa: BLE001
            return {
                "action": "finish",
                "query": "",
                "reason": f"LLM action planning failed: {type(exc).__name__}: {exc}",
            }

    def _coverage_gaps(self, state: ToolAgentState) -> list[str]:
        return self.skill_registry.coverage_gaps(
            state.get("evidence") or [],
            str(state.get("research_type") or "company"),
        )

    def _refresh_task_states(
        self,
        state: ToolAgentState,
        *,
        apply_stop_conditions: bool = False,
    ) -> ToolAgentState:
        """Recompute per-task coverage from evidence linked to each task."""

        refreshed: list[dict[str, Any]] = []
        for raw_task in state.get("tasks") or []:
            if not isinstance(raw_task, dict):
                continue
            task = dict(raw_task)
            task_id = str(task.get("id") or "")
            task_evidence = [
                item for item in (state.get("evidence") or [])
                if task_id and task_id in (item.get("task_ids") or [])
            ]
            required = [str(item) for item in task.get("capabilities") or []]
            all_gaps = self.skill_registry.coverage_gaps(
                task_evidence,
                str(state.get("research_type") or "company"),
            )
            gaps = [capability for capability in required if capability in all_gaps]
            task["evidence_ids"] = [str(item.get("id") or item.get("url") or item.get("title") or "") for item in task_evidence]
            task["uncovered_capabilities"] = gaps
            attempts = sum(1 for step in (state.get("steps") or []) if str(step.get("task_id") or "") == task_id)
            task["attempts"] = attempts
            if not gaps and len(task_evidence) >= int(task.get("stop_conditions", {}).get("min_evidence", task.get("min_evidence", 1))):
                task["status"] = "completed"
                task["stop_reason"] = "required evidence coverage reached"
            elif apply_stop_conditions and attempts >= int(task.get("stop_conditions", {}).get("max_attempts", task.get("max_attempts", 3))):
                task["status"] = "exhausted"
                task["stop_reason"] = "task attempt limit reached before full coverage"
            elif str(task.get("status") or "") not in {"completed", "exhausted"}:
                task["status"] = "in_progress" if attempts else "pending"
            refreshed.append(task)
        return {**state, "tasks": refreshed}

    @staticmethod
    def _task_by_id(state: ToolAgentState, task_id: str) -> dict[str, Any] | None:
        return next((task for task in (state.get("tasks") or []) if str(task.get("id") or "") == task_id), None)

    @staticmethod
    def _next_open_task(state: ToolAgentState) -> dict[str, Any] | None:
        return next(
            (
                task
                for task in (state.get("tasks") or [])
                if str(task.get("status") or "pending") not in {"completed", "exhausted"}
            ),
            None,
        )

    def _task_coverage_gaps(self, state: ToolAgentState, task: dict[str, Any] | None) -> list[str]:
        if not task:
            return []
        return [str(item) for item in task.get("uncovered_capabilities") or []]

    def _apply_task_source_preference(
        self,
        plan: SkillPlan | None,
        task: dict[str, Any] | None,
    ) -> SkillPlan | None:
        if plan is None or not task:
            return plan
        preferred = [str(item) for item in task.get("preferred_source_profiles") or []]
        if not preferred or plan.source_profile in preferred:
            return plan
        for profile_name in preferred:
            profile = self.tool_registry.source_profile(
                skill_name=plan.skill_name,
                capability_name=plan.capability,
                tool_name=plan.action,
                profile_name=profile_name,
            )
            if profile is not None:
                return SkillPlan(
                    skill_name=plan.skill_name,
                    capability=plan.capability,
                    action=plan.action,
                    query=plan.query,
                    reason=plan.reason,
                    source_profile=profile.name,
                )
        return plan

    def _coverage_summary(self, state: ToolAgentState) -> dict[str, Any]:
        tasks = state.get("tasks") or []
        completed = [task for task in tasks if task.get("status") == "completed"]
        exhausted = [task for task in tasks if task.get("status") == "exhausted"]
        total = len(tasks)
        return {
            "tasks_total": total,
            "tasks_completed": len(completed),
            "tasks_exhausted": len(exhausted),
            "coverage_rate": round(len(completed) / total, 4) if total else 0.0,
            "remaining_capabilities": [
                capability
                for task in tasks
                if task.get("status") != "completed"
                for capability in task.get("uncovered_capabilities") or []
            ],
        }

    def _finalize_task_states(self, state: ToolAgentState) -> ToolAgentState:
        finalized = self._refresh_task_states(state, apply_stop_conditions=True)
        reached_limit = int(finalized.get("iteration") or 0) >= int(finalized.get("max_iterations") or self.max_iterations)
        if not reached_limit:
            return finalized
        tasks: list[dict[str, Any]] = []
        for raw_task in finalized.get("tasks") or []:
            task = dict(raw_task)
            if task.get("status") not in {"completed", "exhausted"}:
                task["status"] = "exhausted"
                task["stop_reason"] = "global safety budget reached"
            tasks.append(task)
        return {**finalized, "tasks": tasks}

    def _next_skill_plan(
        self,
        state: ToolAgentState,
        *,
        gaps: list[str] | None = None,
        task: dict[str, Any] | None = None,
    ) -> SkillPlan | None:
        scores = self.performance_store.scores()
        plan = self.skill_registry.next_plan(
            gaps=gaps if gaps is not None else self._coverage_gaps(state),
            target=str(state.get("target_name") or ""),
            ticker=str(state.get("target_ticker") or ""),
            question=state["question"],
            steps=state.get("steps") or [],
            tool_scores=scores,
            enabled_tools=self._enabled_tools(),
            research_type=str(state.get("research_type") or "company"),
        )
        return self._apply_task_source_preference(plan, task)

    def _enabled_tools(self) -> set[str]:
        return self.tool_registry.enabled_names()

    def _validated_model_plan(
        self,
        state: ToolAgentState,
        decision: dict[str, Any],
        gaps: list[str],
        *,
        task: dict[str, Any] | None,
    ) -> SkillPlan | None:
        skill_name = str(decision.get("skill") or "").strip()
        capability_name = str(decision.get("capability") or "").strip()
        action = str(decision.get("action") or "").strip()
        source_profile = str(decision.get("source_profile") or "").strip()
        query = " ".join(str(decision.get("query") or "").split()).strip()
        skill = self.skill_registry.active_skills(str(state.get("research_type") or "company")).get(skill_name)
        if not skill or capability_name not in gaps or not skill.supports(capability_name):
            return None
        if task and capability_name not in (task.get("capabilities") or []):
            return None
        capability = skill.capabilities[capability_name]
        if action not in capability.tools or not self._tool_enabled(action) or not query:
            return None
        source = self.tool_registry.source_profile(
            skill_name=skill_name,
            capability_name=capability_name,
            tool_name=action,
            profile_name=source_profile,
        )
        if capability.source_profiles and source is None:
            return None
        if self._query_was_used(state, action, query, skill_name, capability_name, source.name if source else ""):
            return None
        return self._apply_task_source_preference(SkillPlan(
            skill_name=skill_name,
            capability=capability_name,
            action=action,
            query=query,
            reason=str(decision.get("reason") or capability.description),
            source_profile=source.name if source else "",
        ), task)

    def _tool_enabled(self, action: str) -> bool:
        return action == "finish" or self.tool_registry.is_enabled(action)

    def _query_was_used(
        self,
        state: ToolAgentState,
        action: str,
        query: str,
        skill_name: str = "",
        capability: str = "",
        source_profile: str = "",
    ) -> bool:
        normalized = " ".join(query.lower().split())
        return any(
            str(step.get("action") or "") == action
            and " ".join(str(step.get("query") or "").lower().split()) == normalized
            and (not skill_name or str(step.get("skill") or "") == skill_name)
            and (not capability or str(step.get("capability") or "") == capability)
            and (not source_profile or str(step.get("source_profile") or "") == source_profile)
            for step in (state.get("steps") or [])
            if isinstance(step, dict)
        )


    def _run_local_search(self, query: str, state: ToolAgentState) -> tuple[list[SourceDocument], str]:
        return self.tools.local_search(
            query,
            target=getattr(self, "_active_target", ""),
            profile=self._source_profile_for(state),
        )

    def _run_web_search(self, query: str, state: ToolAgentState) -> tuple[list[SourceDocument], str]:
        return self.tools.web_search(query, profile=self._source_profile_for(state))

    def _run_targeted_news_crawl(self, query: str, state: ToolAgentState) -> tuple[list[SourceDocument], str]:
        return self.tools.targeted_news_crawl(
            query,
            target=getattr(self, "_active_target", ""),
            profile=self._source_profile_for(state),
        )

    def _source_profile_for(self, state: ToolAgentState) -> SkillSourceProfile | None:
        return self.tool_registry.source_profile(
            skill_name=str(state.get("next_skill") or ""),
            capability_name=str(state.get("next_capability") or ""),
            tool_name=str(state.get("next_action") or ""),
            profile_name=str(state.get("next_source_profile") or ""),
        )
    def _append_tool_result(
        self,
        state: ToolAgentState,
        action: str,
        docs: list[SourceDocument],
        warning: str,
        *,
        latency_ms: float = 0.0,
    ) -> ToolAgentState:
        relevant_docs, ignored = self._target_relevant_docs(docs, state)
        packed = [_pack_doc(doc) for doc in relevant_docs[: self.top_k]]
        task_id = str(state.get("active_task_id") or "")
        if task_id:
            packed = [{**item, "task_ids": [task_id]} for item in packed]
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
                "skill": state.get("next_skill") or "",
                "capability": state.get("next_capability") or "",
                "source_profile": state.get("next_source_profile") or "",
                "task_id": task_id,
                "phases": ["search", "read", "extract"],
                "documents_found": len(packed),
                "new_evidence": new_evidence,
                "latency_ms": round(latency_ms, 2),
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
        self._emit_progress(
            {
                "type": "evidence_extract",
                "title": "Evidence read and extraction",
                "message": f"task={task_id or 'unassigned'}, extracted={len(packed)} candidate evidence item(s)",
                "task_id": task_id,
                "capability": state.get("next_capability") or "",
                "source_profile": state.get("next_source_profile") or "",
            }
        )
        self.performance_store.record(
            action,
            success=bool(packed),
            documents_found=len(packed),
            new_evidence=new_evidence,
            latency_ms=latency_ms,
            error=effective_warning,
            skill=str(state.get("next_skill") or ""),
            capability=str(state.get("next_capability") or ""),
        )
        updated = {
            **state,
            "evidence": evidence,
            "warnings": warnings,
            "steps": steps,
            "last_new_evidence": new_evidence,
        }
        self._snapshot(updated, action)
        return updated

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

    def _snapshot(self, state: ToolAgentState, node: str) -> None:
        if not self.state_store:
            return
        try:
            self.state_store.save(dict(state), node=node)
        except Exception as exc:  # noqa: BLE001
            self._emit_progress({
                "type": "state_warning",
                "title": "Agent状态快照失败",
                "message": f"{type(exc).__name__}: {exc}",
            })

    def _write_final_answer(self, state: ToolAgentState) -> str:
        system = """你是严谨的中文投研分析师。
只能使用输入证据回答。不要编造事实、客户、订单、估值、预测或时间点。
如果证据不足，必须写“数据缺口”和下一步检索方向。
输出 Markdown。"""
        user = json.dumps(
            {
                "question": state["question"],
                "research_task_tree": state.get("tasks") or [],
                "coverage": self._coverage_summary(state),
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
        for _ in range(int(current.get("max_iterations") or self.agent.max_iterations)):
            current = self.agent._plan_node(current)
            action = current.get("next_action")
            if action == "finish":
                break
            if action == "local_search":
                current = self.agent._local_search_node(current)
            elif action == "targeted_news_crawl":
                current = self.agent._targeted_news_crawl_node(current)
            elif action == "web_search":
                current = self.agent._web_search_node(current)
            current = self.agent._judge_node(current)
            if current.get("enough"):
                break
        return self.agent._write_node(current)


def _pack_doc(doc: SourceDocument) -> dict[str, Any]:
    metadata = doc.metadata or {}
    return {
        "id": doc.id,
        "title": doc.title,
        "source": doc.source,
        "source_type": doc.source_type,
        "url": doc.url,
        "path": doc.path,
        "published_at": doc.published_at.isoformat() if doc.published_at else None,
        "as_of_date": metadata.get("as_of_date") or metadata.get("report_period") or (
            doc.published_at.date().isoformat() if doc.published_at else None
        ),
        "content_excerpt": doc.content[:1200],
        "metadata": {
            key: metadata.get(key)
            for key in ("as_of_date", "report_period", "kind", "platform", "confidence", "evidence_level")
            if metadata.get(key) not in (None, "")
        },
    }


def _short_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title"),
        "source": item.get("source"),
        "source_type": item.get("source_type"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "as_of_date": item.get("as_of_date"),
        "content_excerpt": str(item.get("content_excerpt") or "")[:300],
    }


def _dedup_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("url") or item.get("path") or item.get("title") or "").strip()
        if not key:
            continue
        prior = seen.get(key)
        if prior is not None:
            prior["task_ids"] = list(dict.fromkeys([
                *(prior.get("task_ids") or []),
                *(item.get("task_ids") or []),
            ]))
            continue
        seen[key] = item
        out.append(item)
    return out
