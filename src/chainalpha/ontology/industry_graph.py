from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from importlib.resources import files
from collections import defaultdict, deque

import yaml


@dataclass
class GraphNode:
    name: str
    type: str
    aliases: list[str]


class IndustryGraph:
    def __init__(self, nodes: dict[str, GraphNode], edges: list[tuple[str, str, str]]) -> None:
        self.nodes = nodes
        self.edges = edges
        self.forward: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.backward: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for src, dst, rel in edges:
            self.forward[src].append((dst, rel))
            self.backward[dst].append((src, rel))

    @classmethod
    def load_ai_graph(cls) -> "IndustryGraph":
        path = files("chainalpha.ontology").joinpath("ai_supply_chain.yaml")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        nodes = {
            name: GraphNode(name=name, type=info.get("type", "unknown"), aliases=info.get("aliases", []))
            for name, info in data["nodes"].items()
        }
        edges = [tuple(e) for e in data["edges"]]
        return cls(nodes, edges)

    def match_nodes(self, text: str) -> list[str]:
        t = text.lower()
        matched = []
        for name, node in self.nodes.items():
            candidates = [name] + node.aliases
            if any(c.lower() in t for c in candidates):
                matched.append(name)
        return sorted(set(matched))

    def expand(self, start_nodes: list[str], depth: int = 2, direction: str = "forward") -> list[str]:
        graph = self.forward if direction == "forward" else self.backward
        seen = set(start_nodes)
        q = deque((n, 0) for n in start_nodes)
        out = []
        while q:
            node, d = q.popleft()
            if d >= depth:
                continue
            for nxt, rel in graph.get(node, []):
                if nxt not in seen:
                    seen.add(nxt)
                    out.append(nxt)
                    q.append((nxt, d + 1))
        return out

    def causal_path_from(self, start_nodes: list[str], max_depth: int = 4) -> list[str]:
        if not start_nodes:
            return []
        # Prefer the longest meaningful path among matched start nodes.
        best: list[str] = []
        for start in start_nodes:
            path = [start]
            current = start
            for _ in range(max_depth):
                nxts = self.forward.get(current, [])
                if not nxts:
                    break
                # prioritize constraint/component propagation
                nxts_sorted = sorted(nxts, key=lambda x: {"constrains": 0, "requires": 1, "upgrades_to": 2, "drives": 3, "enables": 4}.get(x[1], 9))
                current = nxts_sorted[0][0]
                path.append(current)
            if len(path) > len(best):
                best = path
        return best

    def describe_node(self, node: str) -> str:
        n = self.nodes.get(node)
        if not n:
            return node
        return f"{n.name}({n.type})"
