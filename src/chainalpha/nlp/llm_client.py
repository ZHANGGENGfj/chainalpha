from __future__ import annotations

import json
from typing import Any

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
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
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
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
