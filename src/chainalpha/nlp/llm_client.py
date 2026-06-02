from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import requests

from chainalpha.config import Settings


class LLMClient:
    """
    Minimal OpenAI-compatible chat client.
    Works with OpenAI, DashScope compatible mode, DeepSeek compatible API,
    local vLLM/Ollama OpenAI servers, etc.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _post(self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> requests.Response:
        session = requests.Session()
        host = urlparse(url).hostname or ""
        if host in {"127.0.0.1", "localhost"}:
            # Local vLLM/Ollama endpoints should not be routed through an
            # inherited corporate/VPN proxy such as 127.0.0.1:7897.
            session.trust_env = False
        return session.post(url, headers=headers, json=payload, timeout=timeout)

    def chat_json(self, system: str, user: str, timeout: int = 60) -> dict[str, Any]:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        url = f"{self.settings.openai_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.openai_model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        resp = self._post(url, headers, payload, timeout)
        resp.raise_for_status()
        content = _strip_thinking(resp.json()["choices"][0]["message"]["content"])
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Some models ignore response_format; try to recover JSON substring.
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                return json.loads(content[start:end + 1])
            raise

    def chat_text(self, system: str, user: str, timeout: int = 60) -> str:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        url = f"{self.settings.openai_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.openai_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        resp = self._post(url, headers, payload, timeout)
        resp.raise_for_status()
        return _strip_thinking(resp.json()["choices"][0]["message"]["content"])


def _strip_thinking(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", str(text), flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()
