from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SkillCapability:
    name: str
    description: str
    evidence_hints: tuple[str, ...]
    evidence_groups: tuple[tuple[str, ...], ...]
    query_templates: tuple[str, ...]
    tools: tuple[str, ...]
    source_profiles: tuple[str, ...] = ()
    required: bool = True
    max_age_days: int | None = None


@dataclass(frozen=True)
class SkillSourceProfile:
    """A Skill-owned source configuration for one executable research tool."""

    name: str
    tool: str
    description: str = ""
    search_url_templates: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    source_type: str = "web_search_page"
    media_crawler_platform: str = ""
    local_platforms: tuple[str, ...] = ()
    fetch_linked_pages: bool = True
    max_linked_pages_per_query: int = 2
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class RuntimeSkill:
    name: str
    description: str
    capabilities: dict[str, SkillCapability]
    source_profiles: dict[str, SkillSourceProfile] = field(default_factory=dict)
    research_types: tuple[str, ...] = ("company",)
    source_priority: tuple[str, ...] = ()
    instruction_path: Path | None = None

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def summary(self) -> dict[str, Any]:
        instructions = ""
        if self.instruction_path and self.instruction_path.exists():
            instructions = self.instruction_path.read_text(encoding="utf-8", errors="replace")
            if instructions.startswith("---"):
                parts = instructions.split("---", 2)
                instructions = parts[2] if len(parts) == 3 else instructions
        return {
            "name": self.name,
            "description": self.description,
            "source_priority": list(self.source_priority),
            "operating_instructions": instructions.strip()[:1600],
            "capabilities": [
                {
                    "name": item.name,
                    "description": item.description,
                    "tools": list(item.tools),
                    "source_profiles": list(item.source_profiles),
                }
                for item in self.capabilities.values()
            ],
            "research_types": list(self.research_types),
            "sources": [
                {
                    "name": source.name,
                    "tool": source.tool,
                    "description": source.description,
                    "allowed_domains": list(source.allowed_domains),
                }
                for source in self.source_profiles.values()
            ],
        }


@dataclass(frozen=True)
class SkillPlan:
    skill_name: str
    capability: str
    action: str
    query: str
    reason: str
    source_profile: str = ""


@dataclass
class SkillRegistry:
    skills: dict[str, RuntimeSkill] = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | Path | None = None) -> "SkillRegistry":
        # The runtime implementation lives under ``src/chainalpha/agents``;
        # project-level skills remain in the repository-root ``skills`` folder.
        skill_root = Path(root) if root else Path(__file__).resolve().parents[4] / "skills"
        loaded: dict[str, RuntimeSkill] = {}
        if not skill_root.exists():
            return cls()
        for manifest in sorted(skill_root.glob("*/manifest.yaml")):
            skill = _load_skill_manifest(manifest)
            loaded[skill.name] = skill
        return cls(loaded)

    def describe_for_model(self, research_type: str = "company") -> list[dict[str, Any]]:
        return [skill.summary() for skill in self.active_skills(research_type).values()]

    def active_skills(self, research_type: str = "company") -> dict[str, RuntimeSkill]:
        return {
            name: skill
            for name, skill in self.skills.items()
            if not skill.research_types or research_type in skill.research_types
        }

    def coverage_gaps(self, evidence: list[dict[str, Any]], research_type: str = "company") -> list[str]:
        gaps: list[str] = []
        for skill in self.active_skills(research_type).values():
            for capability in skill.capabilities.values():
                if not capability.required:
                    continue
                relevant = [
                    item for item in evidence
                    if isinstance(item, dict) and _within_age(item.get("published_at"), capability.max_age_days)
                ]
                text = " ".join(
                    " ".join(
                        [
                            str(item.get("title") or ""),
                            str(item.get("content_excerpt") or ""),
                            str(item.get("content") or ""),
                            str(item.get("source_type") or ""),
                        ]
                    ).lower()
                    for item in relevant
                )
                if capability.evidence_groups:
                    covered = all(
                        any(hint.lower() in text for hint in group)
                        for group in capability.evidence_groups
                    )
                else:
                    covered = any(hint.lower() in text for hint in capability.evidence_hints)
                if not covered:
                    gaps.append(capability.name)
        return list(dict.fromkeys(gaps))

    def find_capability(
        self,
        name: str,
        research_type: str = "company",
    ) -> tuple[RuntimeSkill, SkillCapability] | None:
        for skill in self.active_skills(research_type).values():
            capability = skill.capabilities.get(name)
            if capability:
                return skill, capability
        return None

    def next_plan(
        self,
        *,
        gaps: list[str],
        target: str,
        ticker: str,
        question: str,
        steps: list[dict[str, Any]],
        tool_scores: dict[str, float] | None = None,
        enabled_tools: set[str] | None = None,
        research_type: str = "company",
    ) -> SkillPlan | None:
        used = {
            (
                str(step.get("skill") or ""),
                str(step.get("capability") or ""),
                str(step.get("action") or ""),
                _normalize(str(step.get("query") or "")),
                str(step.get("source_profile") or ""),
            )
            for step in steps
            if isinstance(step, dict)
        }
        values = {
            "target": target.strip() or question.strip(),
            "ticker": ticker.split(".")[0].strip(),
            "question": question.strip(),
        }
        scores = tool_scores or {}
        for gap in gaps:
            found = self.find_capability(gap, research_type)
            if not found:
                continue
            skill, capability = found
            available_tools = [
                tool for tool in capability.tools
                if enabled_tools is None or tool in enabled_tools
            ]
            ranked_tools = sorted(
                enumerate(available_tools),
                key=lambda pair: (
                    -scores.get(
                        f"{skill.name}::{capability.name}::{pair[1]}",
                        scores.get(pair[1], 0.5),
                    ),
                    pair[0],
                ),
            )
            for template in capability.query_templates:
                query = " ".join(template.format(**values).split()).strip()
                for _index, tool in ranked_tools:
                    profiles = self.source_profiles_for(skill.name, capability.name, tool)
                    if capability.source_profiles and not profiles:
                        continue
                    profile_names = [profile.name for profile in profiles] or [""]
                    for profile_name in profile_names:
                        key = (skill.name, capability.name, tool, _normalize(query), profile_name)
                        if key in used:
                            continue
                        return SkillPlan(
                            skill_name=skill.name,
                            capability=capability.name,
                            action=tool,
                            query=query,
                            reason=f"补齐 {capability.description}；按历史工具效果选择 {tool}。",
                            source_profile=profile_name,
                        )
        return None

    def source_profiles_for(
        self,
        skill_name: str,
        capability_name: str,
        tool: str,
    ) -> list[SkillSourceProfile]:
        skill = self.skills.get(skill_name)
        if not skill:
            return []
        capability = skill.capabilities.get(capability_name)
        if not capability:
            return []
        names = capability.source_profiles
        profiles = [skill.source_profiles[name] for name in names if name in skill.source_profiles]
        return [profile for profile in profiles if profile.tool == tool]

    def source_profile(
        self,
        skill_name: str,
        capability_name: str,
        tool: str,
        profile_name: str = "",
    ) -> SkillSourceProfile | None:
        candidates = self.source_profiles_for(skill_name, capability_name, tool)
        if not candidates:
            return None
        if profile_name:
            return next((profile for profile in candidates if profile.name == profile_name), None)
        return candidates[0]


def _load_skill_manifest(path: Path) -> RuntimeSkill:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    name = str(data.get("name") or path.parent.name).strip()
    sources: dict[str, SkillSourceProfile] = {}
    for item in data.get("sources") or []:
        if not isinstance(item, dict) or not item.get("name") or not item.get("tool"):
            continue
        profile = SkillSourceProfile(
            name=str(item["name"]),
            tool=str(item["tool"]),
            description=str(item.get("description") or ""),
            search_url_templates=tuple(str(value) for value in item.get("search_url_templates") or []),
            allowed_domains=tuple(str(value) for value in item.get("allowed_domains") or []),
            source_type=str(item.get("source_type") or "web_search_page"),
            media_crawler_platform=str(item.get("media_crawler_platform") or ""),
            local_platforms=tuple(str(value) for value in item.get("local_platforms") or []),
            fetch_linked_pages=bool(item.get("fetch_linked_pages", True)),
            max_linked_pages_per_query=int(item.get("max_linked_pages_per_query") or 2),
            timeout_seconds=int(item["timeout_seconds"]) if item.get("timeout_seconds") is not None else None,
        )
        sources[profile.name] = profile
    capabilities: dict[str, SkillCapability] = {}
    for item in data.get("capabilities") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        capability = SkillCapability(
            name=str(item["name"]),
            description=str(item.get("description") or item["name"]),
            evidence_hints=tuple(str(value) for value in item.get("evidence_hints") or []),
            evidence_groups=tuple(
                tuple(str(value) for value in group)
                for group in item.get("evidence_groups") or []
                if isinstance(group, list)
            ),
            query_templates=tuple(str(value) for value in item.get("query_templates") or ["{question}"]),
            tools=tuple(str(value) for value in item.get("tools") or []),
            source_profiles=tuple(str(value) for value in item.get("source_profiles") or []),
            required=bool(item.get("required", True)),
            max_age_days=int(item["max_age_days"]) if item.get("max_age_days") is not None else None,
        )
        capabilities[capability.name] = capability
    return RuntimeSkill(
        name=name,
        description=str(data.get("description") or ""),
        capabilities=capabilities,
        source_profiles=sources,
        research_types=tuple(str(value) for value in data.get("research_types") or ["company"]),
        source_priority=tuple(str(value) for value in data.get("source_priority") or []),
        instruction_path=path.parent / "SKILL.md",
    )


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _within_age(value: Any, max_age_days: int | None) -> bool:
    if max_age_days is None:
        return True
    if not value:
        return False
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed >= datetime.now(timezone.utc) - timedelta(days=max_age_days)
