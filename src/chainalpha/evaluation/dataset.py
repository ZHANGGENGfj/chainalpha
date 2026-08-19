from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class BenchmarkTask:
    task_id: str
    query: str
    reference_answer: Any = None
    category: str = "unknown"
    level: str = "unknown"
    attachments: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def load_tasks(path: str | Path) -> list[BenchmarkTask]:
    source = Path(path)
    rows = _load_rows(source)
    tasks: list[BenchmarkTask] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        task_id = str(_first(row, "task_id", "id", "qid", "index") or index)
        query = str(_first(row, "query", "question", "prompt", "task") or "").strip()
        if not query:
            continue
        attachments = _first(row, "attachments", "files", "file_paths", "resources") or []
        if isinstance(attachments, str):
            attachments = [attachments]
        resolved = []
        for item in attachments:
            value = str(item)
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = source.parent / candidate
            resolved.append(str(candidate))
        tasks.append(BenchmarkTask(
            task_id=task_id,
            query=query,
            reference_answer=_first(row, "reference_answer", "answer", "ground_truth", "gold_answer"),
            category=str(_first(row, "category", "scenario", "task_type", "domain") or "unknown"),
            level=str(_first(row, "level", "difficulty", "tier") or "unknown"),
            attachments=resolved,
            metadata={key: value for key, value in row.items() if key not in {
                "task_id", "id", "qid", "index", "query", "question", "prompt", "task",
                "reference_answer", "answer", "ground_truth", "gold_answer",
                "category", "scenario", "task_type", "domain", "level", "difficulty", "tier",
                "attachments", "files", "file_paths", "resources",
            }},
        ))
    return tasks


def load_predictions(path: str | Path) -> dict[str, str]:
    source = Path(path)
    if source.is_dir():
        predictions: dict[str, str] = {}
        for item in sorted(source.iterdir()):
            if item.suffix.lower() not in {".md", ".txt", ".json"}:
                continue
            if item.suffix.lower() == ".json":
                payload = json.loads(item.read_text(encoding="utf-8"))
                value = _prediction_text(payload)
            else:
                value = item.read_text(encoding="utf-8")
            predictions[item.stem] = value
        return predictions

    rows = _load_rows(source)
    predictions = {}
    for index, row in enumerate(rows, start=1):
        if isinstance(row, dict):
            task_id = str(_first(row, "task_id", "id", "qid", "index") or index)
            predictions[task_id] = _prediction_text(row)
    return predictions


def _load_rows(path: Path) -> list[Any]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("tasks", "items", "data", "predictions", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    raise ValueError(f"Unsupported benchmark data in {path}")


def _prediction_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return str(payload or "")
    value = _first(payload, "prediction", "output", "response", "content", "answer", "final_answer")
    return str(value or "")


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None
