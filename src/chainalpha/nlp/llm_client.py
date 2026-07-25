from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

import requests

from chainalpha.config import Settings


_STREAM_CALLBACK: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar("llm_stream_callback", default=None)


@contextmanager
def llm_stream_events(callback: Callable[[dict[str, Any]], None] | None) -> Iterator[None]:
    token = _STREAM_CALLBACK.set(callback)
    try:
        yield
    finally:
        _STREAM_CALLBACK.reset(token)


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

    def _post_stream(self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> requests.Response:
        session = requests.Session()
        host = urlparse(url).hostname or ""
        if host in {"127.0.0.1", "localhost"}:
            session.trust_env = False
        return session.post(url, headers=headers, json=payload, timeout=timeout, stream=True)

    def chat_json(self, system: str, user: str, timeout: int = 60) -> dict[str, Any]:
        if not self.settings.llm_api_key:
            raise RuntimeError(f"{self.settings.llm_provider.upper()} API key is not configured.")

        url = f"{self.settings.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
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
        if not self.settings.llm_api_key:
            raise RuntimeError(f"{self.settings.llm_provider.upper()} API key is not configured.")

        url = f"{self.settings.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        callback = _STREAM_CALLBACK.get()
        if callback:
            try:
                return _strip_thinking(self._chat_text_stream(url, headers, payload, timeout, callback))
            except Exception as exc:  # noqa: BLE001
                callback(
                    {
                        "type": "model_stream_warning",
                        "message": f"stream failed, fallback to non-stream request: {type(exc).__name__}: {exc}",
                    }
                )
        resp = self._post(url, headers, payload, timeout)
        resp.raise_for_status()
        return _strip_thinking(resp.json()["choices"][0]["message"]["content"])

    def _chat_text_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int,
        callback: Callable[[dict[str, Any]], None],
    ) -> str:
        stream_payload = {**payload, "stream": True}
        callback({"type": "model_stream_start", "message": f"{self.settings.llm_provider}:{self.settings.llm_model}"})
        resp = self._post_stream(url, headers, stream_payload, timeout)
        resp.raise_for_status()
        chunks: list[str] = []
        buffer = ""
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = str(raw_line).strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                packet = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = _extract_stream_delta(packet)
            if not delta:
                continue
            chunks.append(delta)
            buffer += delta
            if len(buffer) >= 20 or "\n" in buffer:
                callback({"type": "model_delta", "message": buffer})
                buffer = ""
        if buffer:
            callback({"type": "model_delta", "message": buffer})
        text = "".join(chunks)
        callback({"type": "model_stream_end", "message": text[-800:]})
        return text


def _strip_thinking(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", str(text), flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()


def _extract_stream_delta(packet: dict[str, Any]) -> str:
    choices = packet.get("choices") or []
    if not choices:
        return ""
    choice = choices[0] or {}
    delta = choice.get("delta") or {}
    content = delta.get("content")
    if content is None:
        content = (choice.get("message") or {}).get("content")
    return str(content or "")
