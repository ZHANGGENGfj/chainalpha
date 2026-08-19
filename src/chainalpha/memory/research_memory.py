"""User-preference memory for ChainAlpha research workflows.

This deliberately does not store company facts, news, financial data, report
conclusions, or tool output. Mem0 is only used to remember how a user prefers
to ask questions and receive reports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chainalpha.config import Settings


@dataclass(frozen=True)
class ResearchMemoryContext:
    available: bool
    items: list[dict[str, Any]]
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "items": self.items, "warning": self.warning}

    def prompt_context(self, limit: int = 5) -> str:
        lines: list[str] = []
        for item in self.items[:limit]:
            text = str(item.get("memory") or "").strip()
            kind = str((item.get("metadata") or {}).get("memory_kind") or "background")
            if text:
                lines.append(f"- [{kind}] {text}")
        return "\n".join(lines)


class ResearchMemoryService:
    """Small policy wrapper around Mem0 with a safe fallback when unavailable."""

    def __init__(
        self,
        settings: Settings,
        *,
        user_id: str = "local_user",
        agent_id: str = "chainalpha",
        root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.user_id = user_id or "local_user"
        self.agent_id = agent_id or "chainalpha"
        self.root = root or (settings.project_root / "data" / "mem0")
        self._memory: Any | None = None
        self._warning = ""

    def recall(self, query: str, *, top_k: int = 5) -> ResearchMemoryContext:
        memory = self._get_memory()
        if memory is None:
            return ResearchMemoryContext(False, [], self._warning)
        filters: dict[str, Any] = {
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "memory_kind": "user_preference",
        }
        try:
            result = memory.search(query=query, filters=filters, top_k=top_k, threshold=0.35, rerank=False)
            items = [item for item in (result.get("results") or []) if isinstance(item, dict)]
            return ResearchMemoryContext(True, items)
        except Exception as exc:  # noqa: BLE001
            return ResearchMemoryContext(False, [], f"Mem0 recall failed: {type(exc).__name__}: {exc}")

    def save_preferences(self, text: str, *, session_id: str = "") -> dict[str, Any]:
        return self._save_raw(
            text,
            memory_kind="user_preference",
            status="verified",
            session_id=session_id,
        )

    def remember_from_query(self, query: str, *, session_id: str = "") -> dict[str, Any]:
        """Save only explicit report-style preferences, never research content."""
        preference = _extract_preference_statement(query)
        if not preference:
            return {"ok": False, "skipped": True, "reason": "no explicit user preference"}
        return self.save_preferences(preference, session_id=session_id)

    def _save_raw(self, text: str, **metadata: Any) -> dict[str, Any]:
        memory = self._get_memory()
        if memory is None:
            return {"ok": False, "warning": self._warning}
        if not text.strip():
            return {"ok": False, "warning": "empty memory"}
        clean_metadata = {key: value for key, value in metadata.items() if value not in (None, "", [], {})}
        try:
            result = memory.add(
                [{"role": "user", "content": text.strip()}],
                user_id=self.user_id,
                agent_id=self.agent_id,
                metadata=clean_metadata,
                infer=False,
            )
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "warning": f"Mem0 write failed: {type(exc).__name__}: {exc}"}

    def _get_memory(self) -> Any | None:
        if self._memory is not None:
            return self._memory
        if self._warning:
            return None
        try:
            # Mem0 reads this environment switch when its telemetry module imports.
            os.environ.setdefault("MEM0_TELEMETRY", "False")
            from mem0 import Memory

            self.root.mkdir(parents=True, exist_ok=True)
            llm_config: dict[str, Any] = {
                "model": self.settings.llm_model,
                "api_key": self.settings.llm_api_key,
                "temperature": 0.1,
            }
            provider = "deepseek" if self.settings.llm_provider == "deepseek" else "openai"
            if provider == "deepseek":
                llm_config["deepseek_base_url"] = self.settings.llm_base_url
            else:
                llm_config["base_url"] = self.settings.llm_base_url
            self._memory = Memory.from_config(
                {
                    "vector_store": {
                        "provider": "qdrant",
                        "config": {
                            "collection_name": "chainalpha_research_memory",
                            "embedding_model_dims": 384,
                            "path": str(self.root / "qdrant"),
                            "on_disk": True,
                        },
                    },
                    "llm": {
                        "provider": provider,
                        "config": llm_config,
                    },
                    "embedder": {
                        "provider": "huggingface",
                        "config": {"model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"},
                    },
                    "history_db_path": str(self.root / "history.db"),
                }
            )
            return self._memory
        except Exception as exc:  # noqa: BLE001
            self._warning = (
                f"Mem0 unavailable: {type(exc).__name__}: {exc}. "
                "Install with `pip install -e .[memory]`."
            )
            return None
def _extract_preference_statement(query: str) -> str:
    """Conservatively identify instructions about report style or focus."""
    text = " ".join(str(query or "").split()).strip()
    if len(text) < 4 or len(text) > 500:
        return ""
    preference_markers = ("以后", "默认", "不要", "不需要", "优先", "重点", "少写", "请使用", "我偏好", "我希望")
    research_terms = ("报告", "分析", "投研", "结论", "风险", "估值", "财报", "产业链", "数据", "来源", "新闻", "格式", "表格")
    if any(marker in text for marker in preference_markers) and any(term in text for term in research_terms):
        return text
    return ""
