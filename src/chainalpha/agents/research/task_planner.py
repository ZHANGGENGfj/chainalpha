"""Task-tree planning for multi-step financial DeepResearch."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from chainalpha.agents.runtime import SkillRegistry
from chainalpha.nlp.llm_client import LLMClient


@dataclass
class ResearchTask:
    id: str
    question: str
    capabilities: list[str]
    keywords: list[str] = field(default_factory=list)
    preferred_source_profiles: list[str] = field(default_factory=list)
    required_evidence_types: list[str] = field(default_factory=list)
    min_evidence: int = 1
    max_attempts: int = 3
    status: str = "pending"
    attempts: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    uncovered_capabilities: list[str] = field(default_factory=list)
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "capabilities": self.capabilities,
            "keywords": self.keywords,
            "preferred_source_profiles": self.preferred_source_profiles,
            "required_evidence_types": self.required_evidence_types,
            "stop_conditions": {
                "min_evidence": self.min_evidence,
                "max_attempts": self.max_attempts,
            },
            "status": self.status,
            "attempts": self.attempts,
            "evidence_ids": self.evidence_ids,
            "uncovered_capabilities": self.uncovered_capabilities,
            "stop_reason": self.stop_reason,
        }


class ResearchTaskPlanner:
    """Plans verifiable sub-questions and validates them against loaded Skills."""

    def __init__(self, client: LLMClient, skill_registry: SkillRegistry) -> None:
        self.client = client
        self.skill_registry = skill_registry

    def plan(
        self,
        *,
        question: str,
        target_name: str,
        target_ticker: str,
        research_type: str,
    ) -> tuple[list[dict[str, Any]], str]:
        fallback = self._fallback_plan(
            question=question,
            target_name=target_name,
            target_ticker=target_ticker,
            research_type=research_type,
        )
        system = """You are a financial DeepResearch planner. Break a question into independent,
verifiable research tasks. Each task must map only to capability names supplied in runtime_skills.
Return JSON only: {\"tasks\":[{\"question\":str,\"capabilities\":[str],\"keywords\":[str],
\"preferred_source_profiles\":[str],\"required_evidence_types\":[str],\"min_evidence\":int,
\"max_attempts\":int}]}. Do not write conclusions. Keep 2-6 tasks."""
        prompt = json.dumps(
            {
                "question": question,
                "target_name": target_name,
                "target_ticker": target_ticker,
                "research_type": research_type,
                "runtime_skills": self.skill_registry.describe_for_model(research_type),
            },
            ensure_ascii=False,
        )
        try:
            raw = self.client.chat_json(system, prompt, timeout=60)
            tasks = self._validate(raw, fallback, research_type)
            if tasks:
                return tasks, "llm"
        except Exception as exc:  # noqa: BLE001
            return fallback, f"fallback:{type(exc).__name__}"
        return fallback, "fallback:invalid_plan"

    def _fallback_plan(
        self,
        *,
        question: str,
        target_name: str,
        target_ticker: str,
        research_type: str,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        active = self.skill_registry.active_skills(research_type)
        for skill in active.values():
            for capability in skill.capabilities.values():
                if not capability.required:
                    continue
                profiles = [
                    profile.name
                    for name in capability.source_profiles
                    if (profile := skill.source_profiles.get(name)) is not None
                ]
                template = capability.query_templates[0] if capability.query_templates else "{question}"
                query = template.format(
                    target=target_name or question,
                    ticker=target_ticker.split(".")[0],
                    question=question,
                )
                tasks.append(
                    ResearchTask(
                        id=f"task_{len(tasks) + 1:02d}",
                        question=query,
                        capabilities=[capability.name],
                        keywords=[part for part in (target_name, target_ticker, capability.name) if part],
                        preferred_source_profiles=profiles,
                        required_evidence_types=list(capability.evidence_hints[:4]),
                        min_evidence=1,
                        max_attempts=max(2, min(4, len(capability.tools) + 1)),
                        uncovered_capabilities=[capability.name],
                    ).to_dict()
                )
        return tasks

    def _validate(
        self,
        raw: Any,
        fallback: list[dict[str, Any]],
        research_type: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
            return []
        active = self.skill_registry.active_skills(research_type)
        known_capabilities = {
            capability_name: skill
            for skill in active.values()
            for capability_name in skill.capabilities
        }
        tasks: list[dict[str, Any]] = []
        for item in raw["tasks"][:6]:
            if not isinstance(item, dict):
                continue
            capabilities = [
                str(value).strip()
                for value in item.get("capabilities") or []
                if str(value).strip() in known_capabilities
            ]
            question = " ".join(str(item.get("question") or "").split()).strip()
            if not question or not capabilities:
                continue
            allowed_profiles = {
                profile.name
                for capability_name in capabilities
                for profile in self._profiles_for_capability(known_capabilities[capability_name], capability_name)
            }
            requested_profiles = [
                str(value).strip()
                for value in item.get("preferred_source_profiles") or []
                if str(value).strip() in allowed_profiles
            ]
            task = ResearchTask(
                id=f"task_{len(tasks) + 1:02d}",
                question=question,
                capabilities=list(dict.fromkeys(capabilities)),
                keywords=_strings(item.get("keywords"), limit=8),
                preferred_source_profiles=requested_profiles or sorted(allowed_profiles),
                required_evidence_types=_strings(item.get("required_evidence_types"), limit=8),
                min_evidence=_bounded_int(item.get("min_evidence"), default=1, low=1, high=4),
                max_attempts=_bounded_int(item.get("max_attempts"), default=3, low=1, high=6),
                uncovered_capabilities=list(dict.fromkeys(capabilities)),
            )
            tasks.append(task.to_dict())
        return tasks or fallback

    @staticmethod
    def _profiles_for_capability(skill: Any, capability_name: str) -> list[Any]:
        capability = skill.capabilities[capability_name]
        return [skill.source_profiles[name] for name in capability.source_profiles if name in skill.source_profiles]


def _strings(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:limit]


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default
