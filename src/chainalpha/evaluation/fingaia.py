from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from typing import Any

from chainalpha.evaluation.common import write_results
from chainalpha.evaluation.dataset import BenchmarkTask


class FinGAIAEvaluator:
    """Answer scorer for FinGAIA-style tasks.

    Exact/numeric scoring is deterministic. An optional LLM judge can accept a
    semantically equivalent free-form answer, but such results are marked and
    should not be mixed with official manual-review scores.
    """

    def __init__(self, llm_client: Any | None = None, semantic_judge: bool = False, numeric_tolerance: float = 1e-4) -> None:
        self.llm_client = llm_client
        self.semantic_judge = bool(semantic_judge and llm_client)
        self.numeric_tolerance = numeric_tolerance

    def evaluate(
        self,
        tasks: list[BenchmarkTask],
        predictions: dict[str, str],
        output_dir: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        rows = [self.evaluate_one(task, predictions.get(task.task_id, "")) for task in tasks]
        correct = sum(bool(row["correct"]) for row in rows)
        summary = {
            "evaluation_mode": "compatible_reproduction" if self.semantic_judge else "deterministic_answer_match",
            "judge_model": _judge_model(self.llm_client) if self.semantic_judge else None,
            "task_count": len(tasks),
            "scored_count": len(tasks),
            "missing_count": sum(row["status"] == "missing_prediction" for row in rows),
            "accuracy": round(correct / len(tasks), 6) if tasks else 0.0,
            "by_category": _group_accuracy(rows, "category"),
            "by_level": _group_accuracy(rows, "level"),
            "notes": [
                "FinGAIA官方评测包含人工复核；本地默认采用可复现的规范化精确匹配与数值容差匹配。",
                "开启semantic judge后的分数只能作为内部诊断，不应直接与官方排行榜横向比较。",
                "当前ChainAlpha尚不具备覆盖全部多模态、文件处理、代码执行任务的统一Agent，缺失能力应保留为错误而非跳过。",
            ],
        }
        write_results(output_dir, "FinGAIA", summary, rows)
        return summary, rows

    def evaluate_one(self, task: BenchmarkTask, prediction: str) -> dict[str, Any]:
        reference = task.reference_answer
        if not str(prediction or "").strip():
            matched, method = False, "missing"
            status = "missing_prediction"
        else:
            matched, method = answer_matches(prediction, reference, self.numeric_tolerance)
            status = "scored"
            if not matched and self.semantic_judge:
                matched = self._semantic_match(task, prediction)
                method = "llm_semantic" if matched else method
        return {
            "task_id": task.task_id,
            "query": task.query,
            "category": task.category,
            "level": task.level,
            "attachments": task.attachments,
            "prediction": prediction,
            "reference_answer": reference,
            "correct": matched,
            "score": 1 if matched else 0,
            "match_method": method,
            "status": status,
        }

    def _semantic_match(self, task: BenchmarkTask, prediction: str) -> bool:
        system = """你是金融任务答案核验器。判断候选答案与参考答案是否语义等价。
不得因为候选答案看起来合理就判对；关键实体、方向、日期、单位和数值必须一致。
只输出JSON：{\"equivalent\": true或false, \"reason\": \"...\"}。"""
        payload = self.llm_client.chat_json(
            system,
            f"问题：{task.query}\n参考答案：{task.reference_answer}\n候选答案：{prediction}",
            timeout=90,
        )
        return bool(payload.get("equivalent"))


def answer_matches(prediction: Any, reference: Any, tolerance: float = 1e-4) -> tuple[bool, str]:
    references = _reference_values(reference)
    candidate = str(prediction or "").strip()
    for expected in references:
        if normalize_answer(candidate) == normalize_answer(expected):
            return True, "normalized_exact"
        left = _single_number(candidate)
        right = _single_number(expected)
        if left is not None and right is not None and math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance):
            return True, "numeric_tolerance"
    return False, "no_match"


def normalize_answer(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = re.sub(r"[\s\u3000]+", "", text)
    text = text.replace("，", ",").replace("；", ";").replace("：", ":")
    text = re.sub(r"[。.!！?？]+$", "", text)
    return text


def _single_number(value: Any) -> float | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().replace(",", "")
    match = re.fullmatch(r"[^\d+\-.]*([+-]?\d+(?:\.\d+)?)\s*(%|％)?[^\d]*", text)
    if not match:
        return None
    number = float(match.group(1))
    return number / 100.0 if match.group(2) else number


def _reference_values(reference: Any) -> list[str]:
    if isinstance(reference, dict):
        for key in ("accepted_answers", "answers", "answer", "value"):
            if key in reference:
                return _reference_values(reference[key])
    if isinstance(reference, (list, tuple, set)):
        return [str(item) for item in reference]
    return [str(reference or "")]


def _group_accuracy(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    return {
        name: {
            "correct": sum(bool(row["correct"]) for row in members),
            "total": len(members),
            "accuracy": round(sum(bool(row["correct"]) for row in members) / len(members), 6),
        }
        for name, members in sorted(groups.items())
    }


def _judge_model(client: Any | None) -> str | None:
    settings = getattr(client, "settings", None)
    return getattr(settings, "llm_model", None) if settings else None
