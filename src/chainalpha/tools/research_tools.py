"""Deterministic implementations for financial-research tool calls."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import re
import time
from typing import Any

from chainalpha.connectors.base import FetchRequest, SourceDocument, build_source_document
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
        max_external_pages: int = 20,
        registry: ResearchToolRegistry,
    ) -> None:
        self.media_crawler_root = Path(media_crawler_root)
        self.evidence_memory = evidence_memory
        self.top_k = top_k
        self.max_external_pages = max(0, int(max_external_pages))
        self.last_external_pages_fetched = 0
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

    def web_search(
        self,
        query: str,
        *,
        profile: SkillSourceProfile | None = None,
        page_budget: int | None = None,
    ) -> tuple[list[SourceDocument], str]:
        self.last_external_pages_fetched = 0
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
            sleep_seconds=0.0,
            max_workers=int(os.getenv("CHAINALPHA_WEB_SEARCH_MAX_WORKERS", "4")),
            max_page_fetches=page_budget if page_budget is not None else self.max_external_pages,
        )
        try:
            docs = connector.fetch(FetchRequest(query=query, keywords=[query], limit=self.top_k))
            self.last_external_pages_fetched = connector.last_page_fetches
            return docs, ""
        except Exception as exc:  # noqa: BLE001
            return [], f"web_search failed: {type(exc).__name__}: {exc}"

    def targeted_news_crawl(
        self,
        query: str,
        *,
        target: str = "",
        profile: SkillSourceProfile | None = None,
        page_budget: int | None = None,
        preserve_query: bool = False,
        source_offset: int = 0,
    ) -> tuple[list[SourceDocument], str]:
        self.last_external_pages_fetched = 0
        if not self.registry.is_enabled("targeted_news_crawl"):
            return [], "targeted_news_crawl disabled"
        if profile is None or not profile.media_crawler_platform:
            return [], "targeted_news_crawl requires a MediaCrawler source profile declared by the selected Skill"
        crawl_keywords = [_crawl_subject_keyword(query)] if preserve_query and query.strip() else _targeted_crawl_keywords(query, target)
        lookup_query = crawl_keywords[0] if crawl_keywords else query
        app_root = self.media_crawler_root.parent if self.media_crawler_root.name.lower() == "data" else self.media_crawler_root
        # ``subprocess.run(..., cwd=app_root)`` resolves a relative script path
        # from that cwd. Always make it absolute so a relative MediaCrawler
        # root does not become ``MediaCrawler-main/third_party/.../main.py``.
        app_root = app_root.resolve()
        script = app_root / "main.py"
        if not script.exists():
            return [], f"MediaCrawler main.py not found: {script}"
        env = os.environ.copy()
        env.pop("SSLKEYLOGFILE", None)
        env["PYTHONPATH"] = str(app_root)
        allowed_pages = self.max_external_pages if page_budget is None else max(0, int(page_budget))
        env["CHAINALPHA_FINANCE_NEWS_MAX_PAGES"] = str(allowed_pages)
        env["CHAINALPHA_FINANCE_NEWS_RETURN_RESULTS"] = "1"
        # On-demand report research consumes the returned evidence directly.
        # It must not mutate the daily library or dedup index as a side effect.
        env["CHAINALPHA_FINANCE_NEWS_PERSIST_RESULTS"] = "0"
        env["CHAINALPHA_FINANCE_NEWS_FAST_MODE"] = "1"
        env["CHAINALPHA_FINANCE_NEWS_SOURCE_OFFSET"] = str(max(0, int(source_offset)))
        command = [
            sys.executable,
            str(script),
            "--platform",
            profile.media_crawler_platform,
            "--type",
            "search",
            "--keywords",
            ",".join(crawl_keywords) if crawl_keywords else query,
            # ChainAlpha runs crawler searches as a background tool. The
            # browser remains a Playwright implementation detail and must not
            # take focus or open a visible desktop window for the user.
            "--headless",
            "true",
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
        docs = _crawler_result_documents(result.stdout, source=f"media_crawler:{profile.media_crawler_platform}")
        local_warning = ""
        if not docs and self.registry.is_enabled("local_search"):
            # Fall back to the persistent archive for older MediaCrawler
            # versions that do not emit the structured result marker.
            for attempt in range(6):
                docs, local_warning = self.local_search(lookup_query, target=target, profile=profile)
                if docs:
                    break
                if attempt < 5:
                    time.sleep(0.5)
        elif not docs:
            local_warning = "no matching public-information result returned for this query"
        crawl_log = f"{result.stdout}\n{result.stderr}"
        self.last_external_pages_fetched = min(allowed_pages, crawl_log.count("[FinanceNews] searching "))
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


def _targeted_crawl_keywords(query: str, target: str) -> list[str]:
    """Keep browser search terms entity-focused instead of passing a full plan."""
    values: list[str] = []
    entity = " ".join(str(target or "").split()).strip()
    if entity:
        values.append(entity)
    for ticker in re.findall(r"(?<!\d)(?:\d{6})(?:\.(?:SZ|SH))?(?!\d)", query, flags=re.IGNORECASE):
        values.append(ticker)
    if not values:
        # A theme query has no resolved company. Keep only its leading subject,
        # not trailing requests such as "财报" or "最新新闻".
        subject = re.split(r"[，,。；;：:\s]+", query, maxsplit=1)[0].strip()
        if subject:
            values.append(subject[:32])
    return list(dict.fromkeys(value for value in values if value))[:2]


def _crawl_subject_keyword(query: str) -> str:
    """Use the entity/topic token that finance-site search actually indexes."""
    text = " ".join(str(query or "").split()).strip()
    if not text:
        return ""
    # Query expansions are written as "subject + research dimension". Finance
    # portals index the subject reliably, while long AND phrases often return
    # an empty result page.
    return re.split(r"[\s,，、;；:：]+", text, maxsplit=1)[0].strip() or text


def _crawler_result_documents(stdout: str, *, source: str) -> list[SourceDocument]:
    marker = "__CHAINALPHA_FINANCE_NEWS_RESULTS__="
    payload = next((line[len(marker):] for line in stdout.splitlines() if line.startswith(marker)), "")
    if not payload:
        return []
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError:
        return []
    documents: list[SourceDocument] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        content = str(row.get("content") or row.get("summary") or title).strip()
        url = str(row.get("url") or "").strip()
        if not title:
            continue
        documents.append(
            build_source_document(
                source=source,
                source_type="targeted_finance_news",
                title=title,
                content=content,
                url=url or None,
                metadata={
                    "topic": row.get("topic"),
                    "importance_score": row.get("importance_score"),
                    "match_type": row.get("match_type"),
                },
            )
        )
    return dedup_source_documents(documents)
