from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, float(value)))


def write_results(output_dir: str | Path, benchmark: str, summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "benchmark": benchmark,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **summary,
    }
    summary_path = target / "summary.json"
    details_path = target / "details.jsonl"
    markdown_path = target / "summary.md"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    details_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    markdown_path.write_text(render_summary_markdown(payload), encoding="utf-8")
    return {"summary": summary_path, "details": details_path, "markdown": markdown_path}


def render_summary_markdown(summary: dict[str, Any]) -> str:
    benchmark = summary.get("benchmark", "benchmark")
    lines = [f"# {benchmark} 评测结果", ""]
    for key in ("evaluation_mode", "judge_model", "task_count", "scored_count", "missing_count", "overall_score", "accuracy"):
        if key in summary and summary[key] is not None:
            lines.append(f"- **{key}**: {summary[key]}")
    dimensions = summary.get("dimensions") or {}
    if dimensions:
        lines.extend(["", "## 分维度结果", "", "| 维度 | 分数 |", "|---|---:|"])
        lines.extend(f"| {key} | {value:.2f} |" for key, value in dimensions.items())
    for section in ("by_category", "by_level"):
        values = summary.get(section) or {}
        if values:
            lines.extend(["", f"## {section}", "", "| 分组 | 正确/总数 | 准确率 |", "|---|---:|---:|"])
            for key, value in values.items():
                lines.append(f"| {key} | {value['correct']}/{value['total']} | {value['accuracy']:.4f} |")
    notes = summary.get("notes") or []
    if notes:
        lines.extend(["", "## 说明", ""])
        lines.extend(f"- {note}" for note in notes)
    return "\n".join(lines) + "\n"
