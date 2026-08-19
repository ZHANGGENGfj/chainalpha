from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from chainalpha.evaluation.common import clamp, write_results
from chainalpha.evaluation.dataset import BenchmarkTask


QUALITATIVE_DIMENSIONS = (
    "visual_linguistic_synergy",
    "articulation_professionalism",
    "articulation_coherence",
    "analysis_framework_quality",
    "logic_consistency",
    "viewpoint_clarity",
)
RULE_DIMENSIONS = (
    "analysis_width",
    "analysis_depth",
    "information_density",
    "paragraph_richness",
)


class FinResearchBenchEvaluator:
    """FinResearchBench-compatible report evaluator.

    The public paper describes the ten dimensions and logic-tree judge but does
    not ship the complete official evaluator. Results are therefore explicitly
    marked as a compatible reproduction.
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self.llm_client = llm_client

    def evaluate(
        self,
        tasks: list[BenchmarkTask],
        predictions: dict[str, str],
        output_dir: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        rows = [self.evaluate_one(task, predictions.get(task.task_id, "")) for task in tasks]
        evaluated = [row for row in rows if row.get("status") in {"scored", "rule_only"}]
        fully_scored = [row for row in evaluated if row.get("overall_score") is not None]
        dimension_names = QUALITATIVE_DIMENSIONS + RULE_DIMENSIONS
        dimensions = {
            name: round(sum(float(row["scores"][name]) for row in members) / len(members), 4)
            for name in dimension_names
            if (members := [row for row in evaluated if name in row.get("scores", {})])
        }
        summary = {
            "evaluation_mode": "compatible_reproduction",
            "judge_model": _judge_model(self.llm_client),
            "task_count": len(tasks),
            "scored_count": len(evaluated),
            "missing_count": sum(not bool(predictions.get(task.task_id, "").strip()) for task in tasks),
            "overall_score": round(sum(row["overall_score"] for row in fully_scored) / len(fully_scored), 4) if fully_scored else None,
            "dimensions": dimensions,
            "notes": [
                "十维结构依据FinResearchBench公开论文复现，不宣称为官方排行榜分数。",
                "未配置judge模型时仅计算四项规则指标，overall_score留空。",
                "为减少同模型自评偏差，建议使用独立judge模型，并固定模型版本与评测日期。",
            ],
        }
        write_results(output_dir, "FinResearchBench", summary, rows)
        return summary, rows

    def evaluate_one(self, task: BenchmarkTask, report: str) -> dict[str, Any]:
        if not report.strip():
            return {
                "task_id": task.task_id,
                "query": task.query,
                "category": task.category,
                "status": "missing_prediction",
                "scores": {},
                "overall_score": None,
            }
        judged = self._judge(task, report) if self.llm_client else {}
        logic_tree = judged.get("logic_tree") or _fallback_logic_tree(report)
        rule_scores = score_logic_tree(logic_tree, report)
        qualitative = _coerce_qualitative(judged.get("qualitative_scores"))
        scores = {**qualitative, **rule_scores}
        overall = None
        if len(qualitative) == len(QUALITATIVE_DIMENSIONS):
            overall = round(sum(scores[name] for name in QUALITATIVE_DIMENSIONS + RULE_DIMENSIONS) / 10.0, 4)
        return {
            "task_id": task.task_id,
            "query": task.query,
            "category": task.category,
            "status": "scored" if overall is not None else "rule_only",
            "scores": scores,
            "overall_score": overall,
            "logic_tree": logic_tree,
            "judge_rationale": judged.get("rationales") or {},
        }

    def _judge(self, task: BenchmarkTask, report: str) -> dict[str, Any]:
        system = """你是FinResearchBench兼容评测器。只评价输入报告，不补充外部事实，不因同意观点而加分。
输出严格JSON，包含：
1. logic_tree.roots：树节点数组。每个节点必须含id、type(argument或evidence)、text、children。
argument是分析主张，evidence是支撑主张的数据、事实、来源或推导依据。保持报告原有论证关系。
2. qualitative_scores：六项0-100分：visual_linguistic_synergy、articulation_professionalism、articulation_coherence、analysis_framework_quality、logic_consistency、viewpoint_clarity。
3. rationales：对应六项的简短评分依据。
不要输出Markdown。"""
        user = f"研究问题：{task.query}\n任务类别：{task.category}\n\n待评报告：\n{report}"
        return self.llm_client.chat_json(system, user, timeout=180)


def score_logic_tree(logic_tree: dict[str, Any], report: str) -> dict[str, float]:
    roots = logic_tree.get("roots") if isinstance(logic_tree, dict) else []
    roots = roots if isinstance(roots, list) else []
    nodes = list(_walk_nodes(roots))
    argument_nodes = [item for item in nodes if item[0].get("type") != "evidence"]
    evidence_nodes = [item for item in nodes if item[0].get("type") == "evidence"]
    parents = [node for node, _depth in argument_nodes if _children(node)]
    average_children = sum(len(_children(node)) for node in parents) / len(parents) if parents else 1.0
    width = clamp(33.33 * (average_children - 1.0))

    leaf_depths = [depth for node, depth in nodes if not _children(node)] or [1]
    maximum_depth = max(leaf_depths)
    average_leaf_depth = sum(leaf_depths) / len(leaf_depths)
    depth = 0.4 * clamp(25.0 * (maximum_depth - 2.0)) + 0.6 * clamp(40.0 * (average_leaf_depth - 1.5))

    total_nodes = len(nodes)
    node_quantity = clamp(2.0 * (total_nodes - 5.0))
    evidence_ratio = clamp(100.0 * len(evidence_nodes) / max(1, len(argument_nodes)))
    information_density = 0.7 * node_quantity + 0.3 * evidence_ratio

    paragraph_scores = [_paragraph_score(_lexical_units(text)) for text in _report_paragraphs(report)]
    paragraph_richness = sum(paragraph_scores) / len(paragraph_scores) if paragraph_scores else 0.0
    return {
        "analysis_width": round(width, 4),
        "analysis_depth": round(depth, 4),
        "information_density": round(information_density, 4),
        "paragraph_richness": round(paragraph_richness, 4),
    }


def _walk_nodes(nodes: Iterable[Any], depth: int = 1) -> Iterable[tuple[dict[str, Any], int]]:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node, depth
        yield from _walk_nodes(_children(node), depth + 1)


def _children(node: dict[str, Any]) -> list[Any]:
    children = node.get("children") or []
    return children if isinstance(children, list) else []


def _fallback_logic_tree(report: str) -> dict[str, Any]:
    roots: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    counter = 0
    for block in _report_paragraphs(report):
        counter += 1
        clean = re.sub(r"^#{1,6}\s*", "", block).strip()
        is_heading = bool(re.match(r"^#{1,6}\s+", block))
        is_evidence = bool(re.search(r"\d|同比|环比|来源|公告|财报|https?://", clean, re.I))
        node = {"id": f"n{counter}", "type": "evidence" if is_evidence and not is_heading else "argument", "text": clean[:500], "children": []}
        if is_heading or current is None:
            roots.append(node)
            current = node
        else:
            current["children"].append(node)
    return {"roots": roots}


def _report_paragraphs(report: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", report) if block.strip() and block.strip() != "---"]


def _lexical_units(text: str) -> int:
    latin = re.findall(r"[A-Za-z0-9]+(?:[._%/-][A-Za-z0-9]+)*", text)
    cjk = re.findall(r"[\u3400-\u9fff]", text)
    return len(latin) + len(cjk)


def _paragraph_score(word_count: int) -> float:
    # Continuous approximation of the paper's public piecewise rule. The
    # published HTML omits the 100-200 interval, so this is intentionally
    # reported as compatible reproduction rather than official scoring code.
    if word_count <= 0:
        return 0.0
    if word_count < 100:
        return 0.6 * word_count
    if word_count < 500:
        return 60.0 + 0.1 * (word_count - 100)
    if word_count <= 1000:
        return 100.0
    return max(60.0, 100.0 - 0.05 * (word_count - 1000))


def _coerce_qualitative(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for name in QUALITATIVE_DIMENSIONS:
        try:
            result[name] = round(clamp(float(value[name])), 4)
        except (KeyError, TypeError, ValueError):
            return {}
    return result


def _judge_model(client: Any | None) -> str | None:
    settings = getattr(client, "settings", None)
    return getattr(settings, "llm_model", None) if settings else None
