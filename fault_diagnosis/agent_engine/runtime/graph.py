"""Execution graph utilities for Agent Engine V2 runtime."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from ..contracts import ExecutionPlan


class RuntimeGraphError(ValueError):
    """Raised when a validated plan cannot form an executable graph."""


class RuntimeGraph:
    """Validated DAG view of an ExecutionPlan."""

    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan
        self.nodes = _normalize_nodes(plan.nodes)
        self.parents: dict[str, set[str]] = {node_id: set() for node_id in self.nodes}
        self.children: dict[str, set[str]] = {node_id: set() for node_id in self.nodes}
        self._load_edges(plan.edges)
        self.execution_order = self._topological_order()

    def ordered_nodes(self) -> list[dict[str, Any]]:
        return [self.nodes[node_id] for node_id in self.execution_order]

    def dependencies(self, node_id: str) -> set[str]:
        return set(self.parents.get(node_id, set()))

    def _load_edges(self, edges: list[dict[str, Any]]) -> None:
        if not edges:
            node_ids = list(self.nodes)
            for index in range(len(node_ids) - 1):
                self._add_edge(node_ids[index], node_ids[index + 1])
            return
        for edge in edges:
            source = str(edge.get("from") or edge.get("source") or "").strip()
            target = str(edge.get("to") or edge.get("target") or "").strip()
            self._add_edge(source, target)

    def _add_edge(self, source: str, target: str) -> None:
        if source not in self.nodes or target not in self.nodes:
            raise RuntimeGraphError(f"Plan edge references unknown node: {source}->{target}")
        if source == target:
            raise RuntimeGraphError(f"Plan edge cannot be self-referential: {source}")
        self.children[source].add(target)
        self.parents[target].add(source)

    def _topological_order(self) -> list[str]:
        indegree = {node_id: len(parents) for node_id, parents in self.parents.items()}
        ready = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            node_id = ready.popleft()
            order.append(node_id)
            for child in sorted(self.children[node_id]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if len(order) != len(self.nodes):
            raise RuntimeGraphError("Plan graph contains a cycle.")
        return order


def _normalize_nodes(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    generated_counts: dict[str, int] = defaultdict(int)
    for raw in nodes:
        node = dict(raw)
        node_type = str(node.get("node_type") or node.get("type") or "node").strip() or "node"
        node_id = str(node.get("node_id") or "").strip()
        if not node_id:
            generated_counts[node_type] += 1
            node_id = f"{node_type}_{generated_counts[node_type]}"
            node["node_id"] = node_id
        if node_id in normalized:
            raise RuntimeGraphError(f"Duplicate node_id in plan: {node_id}")
        normalized[node_id] = node
    return normalized
