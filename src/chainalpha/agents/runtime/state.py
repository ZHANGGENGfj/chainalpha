from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class AgentStateStore:
    """Atomic JSON snapshots that let a research run resume after interruption."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, *, question: str, target_name: str = "") -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        state = payload.get("state") if isinstance(payload, dict) else None
        if not isinstance(state, dict):
            return None
        if str(state.get("question") or "") != question:
            return None
        if target_name and str(state.get("target_name") or "") != target_name:
            return None
        return state

    def save(self, state: dict[str, Any], *, node: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "agent_state.v1",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "last_node": node,
            "state": _jsonable(state),
        }
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)
