from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    openai_api_key: str | None
    openai_base_url: str
    openai_model: str
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    project_root: Path

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def llm_api_key(self) -> str | None:
        if self.llm_provider == "deepseek":
            return self.deepseek_api_key or self.openai_api_key
        return self.openai_api_key

    @property
    def llm_base_url(self) -> str:
        if self.llm_provider == "deepseek":
            return self.deepseek_base_url
        return self.openai_base_url

    @property
    def llm_model(self) -> str:
        if self.llm_provider == "deepseek":
            return self.deepseek_model
        return self.openai_model


def load_settings() -> Settings:
    load_dotenv()
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "deepseek" if os.getenv("DEEPSEEK_API_KEY") else "openai"
    return Settings(
        llm_provider=provider,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_base_url=_normalize_base_url(os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
        deepseek_base_url=_normalize_base_url(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        project_root=Path.cwd(),
    )


def _normalize_base_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/v1/chat/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    return url
