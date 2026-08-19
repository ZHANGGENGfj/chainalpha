from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class ToolPerformanceStore:
    """Persist lightweight tool outcome statistics for future routing."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def scores(self) -> dict[str, float]:
        payload = self._read()
        data = payload.get("tools") or {}
        result: dict[str, float] = {}
        for name, stats in data.items():
            result[str(name)] = _score(stats)
        for route, stats in (payload.get("routes") or {}).items():
            tool = str(stats.get("tool") or "")
            contextual = _score(stats)
            aggregate = result.get(tool, 0.5)
            result[str(route)] = round(0.35 * aggregate + 0.65 * contextual, 6)
        return result

    def record(
        self,
        tool: str,
        *,
        success: bool,
        documents_found: int,
        new_evidence: int,
        latency_ms: float,
        error: str = "",
        skill: str = "",
        capability: str = "",
    ) -> None:
        with self._lock:
            data = self._read()
            tools = data.setdefault("tools", {})
            _update_stats(
                tools.setdefault(tool, _empty_stats()),
                success=success,
                documents_found=documents_found,
                new_evidence=new_evidence,
                latency_ms=latency_ms,
                error=error,
            )
            if skill and capability:
                route = f"{skill}::{capability}::{tool}"
                routes = data.setdefault("routes", {})
                route_stats = routes.setdefault(route, {**_empty_stats(), "tool": tool})
                _update_stats(
                    route_stats,
                    success=success,
                    documents_found=documents_found,
                    new_evidence=new_evidence,
                    latency_ms=latency_ms,
                    error=error,
                )
            data["schema_version"] = "tool_performance.v2"
            self._write(data)

    def snapshot(self) -> dict[str, Any]:
        data = self._read()
        data["routing_scores"] = self.scores()
        return data

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": "tool_performance.v2", "tools": {}, "routes": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"tools": {}, "routes": {}}
        except (OSError, json.JSONDecodeError):
            return {"schema_version": "tool_performance.v2", "tools": {}, "routes": {}}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)


def _empty_stats() -> dict[str, Any]:
    return {
        "calls": 0,
        "successes": 0,
        "documents_found": 0,
        "new_evidence": 0,
        "latency_ms_total": 0.0,
        "last_error": "",
    }


def _update_stats(
    stats: dict[str, Any],
    *,
    success: bool,
    documents_found: int,
    new_evidence: int,
    latency_ms: float,
    error: str,
) -> None:
    stats["calls"] = int(stats.get("calls") or 0) + 1
    stats["successes"] = int(stats.get("successes") or 0) + int(success)
    stats["documents_found"] = int(stats.get("documents_found") or 0) + max(0, int(documents_found))
    stats["new_evidence"] = int(stats.get("new_evidence") or 0) + max(0, int(new_evidence))
    stats["latency_ms_total"] = float(stats.get("latency_ms_total") or 0.0) + max(0.0, float(latency_ms))
    stats["last_error"] = error[:500]
    stats["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _score(stats: dict[str, Any]) -> float:
    calls = max(0, int(stats.get("calls") or 0))
    successes = max(0, int(stats.get("successes") or 0))
    new_evidence = max(0, int(stats.get("new_evidence") or 0))
    reliability = (successes + 1.0) / (calls + 2.0)
    yield_score = min(1.0, new_evidence / max(1.0, calls * 3.0))
    return round(0.7 * reliability + 0.3 * yield_score, 6)
