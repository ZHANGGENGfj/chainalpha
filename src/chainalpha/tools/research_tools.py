"""Deterministic implementations for financial-research tool calls."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from chainalpha.connectors.base import FetchRequest, SourceDocument
from chainalpha.connectors.media_crawler_adapter import MediaCrawlerConnector
from chainalpha.connectors.search_page import SearchPageConnector
from chainalpha.agents.runtime.skill_registry import SkillSourceProfile
from chainalpha.memory.evidence_memory import ResearchEvidenceMemory
from chainalpha.tools.registry import ResearchToolRegistry


class ResearchToolset:
    """Execution layer for local, crawler and approved-web research tools."""

    def __init__(
        self,
        *,
        media_crawler_root: str | Path,
        evidence_memory: ResearchEvidenceMemory,
        top_k: int,
        registry: ResearchToolRegistry,
    ) -> None:
        self.media_crawler_root = Path(media_crawler_root)
        self.evidence_memory = evidence_memory
        self.top_k = top_k
        self.registry = registry

    def local_search(
        self,
        query: str,
        *,
        target: str = "",
        profile: SkillSourceProfile | None = None,
    ) -> tuple[list[SourceDocument], str]:
        if not self.registry.is_enabled("local_search"):
            return [], "local_search disabled"
        memory_docs = self.evidence_memory.recall(query, entity=target, limit=self.top_k)
        crawler_docs: list[SourceDocument] = []
        warnings: list[str] = []
        if self.media_crawler_root.exists():
            connector = MediaCrawlerConnector(
                "agent_local_media_crawler",
                root_dir=self.media_crawler_root,
                platforms=list(profile.local_platforms) if profile and profile.local_platforms else ["finance_news", "cninfo_reports", "broker_reports", "claim_tasks", "cls", "xq"],
                modes=["processed", "raw", "cache"],
            )
            try:
                crawler_docs = connector.fetch(FetchRequest(query=query, keywords=[query], limit=self.top_k))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"MediaCrawler local search failed: {type(exc).__name__}: {exc}")
        else:
            warnings.append(f"MediaCrawler root not found: {self.media_crawler_root}")
        return dedup_source_documents([*crawler_docs, *memory_docs])[: self.top_k], "; ".join(warnings)

    def web_search(self, query: str, *, profile: SkillSourceProfile | None = None) -> tuple[list[SourceDocument], str]:
        if not self.registry.is_enabled("web_search"):
            return [], "web_search disabled"
        if profile is None:
            return [], "web_search requires a source profile declared by the selected Skill"
        if not profile.search_url_templates or not profile.allowed_domains:
            return [], f"web_search source profile '{profile.name}' has no templates or allowed domains"
        connector = SearchPageConnector(
            f"skill_web:{profile.name}",
            search_url_templates=list(profile.search_url_templates),
            allowed_domains=list(profile.allowed_domains),
            source_type=profile.source_type,
            include_search_pages=False,
            fetch_linked_pages=profile.fetch_linked_pages,
            max_linked_pages_per_query=profile.max_linked_pages_per_query,
            timeout=profile.timeout_seconds or self.registry.spec("web_search").default_timeout_seconds,
        )
        try:
            return connector.fetch(FetchRequest(query=query, keywords=[query], limit=self.top_k)), ""
        except Exception as exc:  # noqa: BLE001
            return [], f"web_search failed: {type(exc).__name__}: {exc}"

    def targeted_news_crawl(
        self,
        query: str,
        *,
        target: str = "",
        profile: SkillSourceProfile | None = None,
    ) -> tuple[list[SourceDocument], str]:
        if not self.registry.is_enabled("targeted_news_crawl"):
            return [], "targeted_news_crawl disabled"
        if profile is None or not profile.media_crawler_platform:
            return [], "targeted_news_crawl requires a MediaCrawler source profile declared by the selected Skill"
        app_root = self.media_crawler_root.parent if self.media_crawler_root.name.lower() == "data" else self.media_crawler_root
        script = app_root / "main.py"
        if not script.exists():
            return [], f"MediaCrawler main.py not found: {script}"
        env = os.environ.copy()
        env.pop("SSLKEYLOGFILE", None)
        env["PYTHONPATH"] = str(app_root)
        command = [
            sys.executable,
            str(script),
            "--platform",
            profile.media_crawler_platform,
            "--type",
            "search",
            "--keywords",
            query,
            "--save_data_option",
            "jsonl",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=str(app_root),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.registry.spec("targeted_news_crawl").default_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [], "targeted_news_crawl timed out after 120s"
        except Exception as exc:  # noqa: BLE001
            return [], f"targeted_news_crawl failed: {type(exc).__name__}: {exc}"
        docs, local_warning = self.local_search(query, target=target, profile=profile)
        messages: list[str] = []
        if result.returncode != 0:
            messages.append((result.stderr or result.stdout or f"exit={result.returncode}")[-800:])
        if local_warning:
            messages.append(local_warning)
        return docs, "; ".join(messages)


def dedup_source_documents(items: list[SourceDocument]) -> list[SourceDocument]:
    seen: set[str] = set()
    output: list[SourceDocument] = []
    for item in items:
        key = str(item.url or item.path or item.id or item.title).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output
