"""Tool capability declarations used by agent planning and guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chainalpha.agents.runtime.skill_registry import SkillRegistry, SkillSourceProfile


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    network_access: bool
    default_timeout_seconds: int


class ResearchToolRegistry:
    """Central allow-list for tools available to the financial research Agent."""

    def __init__(
        self,
        *,
        enable_local_search: bool,
        enable_web_search: bool,
        enable_targeted_crawl: bool,
        skill_registry: "SkillRegistry | None" = None,
    ) -> None:
        self._enabled = {
            "local_search": enable_local_search,
            "web_search": enable_web_search,
            "targeted_news_crawl": enable_targeted_crawl,
        }
        self._specs = {
            "local_search": ToolSpec(
                name="local_search",
                description="Search the local MediaCrawler library and point-in-time evidence memory.",
                network_access=False,
                default_timeout_seconds=15,
            ),
            "targeted_news_crawl": ToolSpec(
                name="targeted_news_crawl",
                description="Run a scoped MediaCrawler finance-news search, then reread local results.",
                network_access=True,
                default_timeout_seconds=120,
            ),
            "web_search": ToolSpec(
                name="web_search",
                description="Search approved public financial websites and fetch linked evidence pages.",
                network_access=True,
                default_timeout_seconds=20,
            ),
        }
        self._skill_registry = skill_registry

    def enabled_names(self) -> set[str]:
        return {name for name, enabled in self._enabled.items() if enabled}

    def is_enabled(self, name: str) -> bool:
        return bool(self._enabled.get(name, False))

    def spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def source_profile(
        self,
        *,
        skill_name: str,
        capability_name: str,
        tool_name: str,
        profile_name: str = "",
    ) -> "SkillSourceProfile | None":
        """Resolve a tool source declared by the active runtime Skill."""

        if not self._skill_registry or not self.is_enabled(tool_name):
            return None
        return self._skill_registry.source_profile(
            skill_name,
            capability_name,
            tool_name,
            profile_name,
        )
