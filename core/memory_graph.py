"""
AXIS Memory Graph — directed weighted graph for agent knowledge storage.

Nodes are typed memory units (facts, events, concepts, agent state).
Edges are labelled relationships with decay weights.
"""

import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class MemoryNode:
    content: str
    node_type: str                                   # fact | event | concept | state | goal
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    weight: float = 1.0
    access_count: int = 0
    pinned: bool = False


@dataclass
class MemoryEdge:
    source_id: str
    target_id: str
    relationship: str                                # related_to | caused_by | part_of | led_to | contradicts
    weight: float = 1.0
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MemoryGraph:
    """
    Directed multigraph for agent memory. Supports BFS traversal,
    typed queries, weight decay, and JSON persistence.
    """

    def __init__(self):
        self._nodes: dict[str, MemoryNode] = {}
        self._out: dict[str, list[MemoryEdge]] = defaultdict(list)   # forward edges
        self._in: dict[str, list[MemoryEdge]] = defaultdict(list)    # reverse index

    # ------------------------------------------------------------------ nodes

    def add_node(
        self,
        content: str,
        node_type: str,
        metadata: dict = None,
        tags: list = None,
        weight: float = 1.0,
        pinned: bool = False,
    ) -> MemoryNode:
        node = MemoryNode(
            content=content,
            node_type=node_type,
            metadata=metadata or {},
            tags=tags or [],
            weight=weight,
            pinned=pinned,
        )
        self._nodes[node.id] = node
        return node

    def get_node(self, node_id: str) -> Optional[MemoryNode]:
        node = self._nodes.get(node_id)
        if node:
            node.access_count += 1
        return node

    def update_node(self, node_id: str, **kwargs) -> Optional[MemoryNode]:
        node = self._nodes.get(node_id)
        if not node:
            return None
        for k, v in kwargs.items():
            if hasattr(node, k):
                setattr(node, k, v)
        return node

    def remove_node(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        del self._nodes[node_id]
        out_edges = self._out.pop(node_id, [])
        for e in out_edges:
            self._in[e.target_id] = [x for x in self._in[e.target_id] if x.source_id != node_id]
        in_edges = self._in.pop(node_id, [])
        for e in in_edges:
            self._out[e.source_id] = [x for x in self._out[e.source_id] if x.target_id != node_id]
        return True

    # ------------------------------------------------------------------ edges

    def link(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        weight: float = 1.0,
        metadata: dict = None,
    ) -> Optional[MemoryEdge]:
        if source_id not in self._nodes or target_id not in self._nodes:
            return None
        edge = MemoryEdge(
            source_id=source_id,
            target_id=target_id,
            relationship=relationship,
            weight=weight,
            metadata=metadata or {},
        )
        self._out[source_id].append(edge)
        self._in[target_id].append(edge)
        return edge

    def get_edges(self, source_id: str, relationship: str = None) -> list[MemoryEdge]:
        edges = self._out.get(source_id, [])
        if relationship:
            edges = [e for e in edges if e.relationship == relationship]
        return edges

    def get_neighbors(self, node_id: str, relationship: str = None) -> list[MemoryNode]:
        return [
            self._nodes[e.target_id]
            for e in self.get_edges(node_id, relationship)
            if e.target_id in self._nodes
        ]

    def get_predecessors(self, node_id: str, relationship: str = None) -> list[MemoryNode]:
        edges = self._in.get(node_id, [])
        if relationship:
            edges = [e for e in edges if e.relationship == relationship]
        return [self._nodes[e.source_id] for e in edges if e.source_id in self._nodes]

    # ---------------------------------------------------------------- traversal

    def bfs(self, start_id: str, max_depth: int = 3, relationship: str = None) -> list[MemoryNode]:
        if start_id not in self._nodes:
            return []
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])
        result: list[MemoryNode] = []
        while queue:
            nid, depth = queue.popleft()
            if nid in visited or depth > max_depth:
                continue
            visited.add(nid)
            result.append(self._nodes[nid])
            for edge in self._out.get(nid, []):
                if relationship and edge.relationship != relationship:
                    continue
                if edge.target_id not in visited:
                    queue.append((edge.target_id, depth + 1))
        return result

    def dfs(self, start_id: str, max_depth: int = 3) -> list[MemoryNode]:
        if start_id not in self._nodes:
            return []
        visited: set[str] = set()
        result: list[MemoryNode] = []

        def _recurse(nid: str, depth: int):
            if nid in visited or depth > max_depth:
                return
            visited.add(nid)
            result.append(self._nodes[nid])
            for edge in self._out.get(nid, []):
                _recurse(edge.target_id, depth + 1)

        _recurse(start_id, 0)
        return result

    def shortest_path(self, start_id: str, end_id: str) -> list[MemoryNode]:
        if start_id not in self._nodes or end_id not in self._nodes:
            return []
        parent: dict[str, Optional[str]] = {start_id: None}
        queue: deque[str] = deque([start_id])
        while queue:
            nid = queue.popleft()
            if nid == end_id:
                path = []
                cur = end_id
                while cur is not None:
                    path.append(self._nodes[cur])
                    cur = parent[cur]
                return list(reversed(path))
            for edge in self._out.get(nid, []):
                if edge.target_id not in parent:
                    parent[edge.target_id] = nid
                    queue.append(edge.target_id)
        return []

    # ------------------------------------------------------------------ query

    def query(
        self,
        node_type: str = None,
        tags: list = None,
        content_contains: str = None,
        min_weight: float = None,
        limit: int = None,
    ) -> list[MemoryNode]:
        results = list(self._nodes.values())
        if node_type:
            results = [n for n in results if n.node_type == node_type]
        if tags:
            results = [n for n in results if any(t in n.tags for t in tags)]
        if content_contains:
            needle = content_contains.lower()
            results = [n for n in results if needle in n.content.lower()]
        if min_weight is not None:
            results = [n for n in results if n.weight >= min_weight]
        results.sort(key=lambda n: n.weight * (1 + n.access_count), reverse=True)
        return results[:limit] if limit else results

    def find_hubs(self, top_k: int = 5) -> list[tuple[MemoryNode, int]]:
        degree = {nid: len(edges) for nid, edges in self._out.items()}
        for nid in self._nodes:
            degree.setdefault(nid, 0)
        ranked = sorted(degree.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [(self._nodes[nid], deg) for nid, deg in ranked if nid in self._nodes]

    # ------------------------------------------------------------------- decay

    def decay(self, factor: float = 0.995):
        """Apply multiplicative weight decay to all unpinned nodes."""
        for node in self._nodes.values():
            if not node.pinned:
                node.weight *= factor

    def reinforce(self, node_id: str, amount: float = 0.1):
        node = self._nodes.get(node_id)
        if node:
            node.weight = min(node.weight + amount, 2.0)

    def prune(self, min_weight: float = 0.05) -> int:
        """Remove unpinned nodes below min_weight. Returns count removed."""
        to_remove = [
            nid for nid, n in self._nodes.items()
            if not n.pinned and n.weight < min_weight
        ]
        for nid in to_remove:
            self.remove_node(nid)
        return len(to_remove)

    # ----------------------------------------------------------- persistence

    def to_dict(self) -> dict:
        return {
            "nodes": {nid: asdict(n) for nid, n in self._nodes.items()},
            "edges": {
                nid: [asdict(e) for e in edges]
                for nid, edges in self._out.items()
                if edges
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryGraph":
        g = cls()
        for nid, nd in data.get("nodes", {}).items():
            node = MemoryNode(**nd)
            g._nodes[nid] = node
        for nid, edges in data.get("edges", {}).items():
            for ed in edges:
                edge = MemoryEdge(**ed)
                g._out[nid].append(edge)
                g._in[ed["target_id"]].append(edge)
        return g

    def save(self, path: str):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "MemoryGraph":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    # ------------------------------------------------------------- introspect

    def stats(self) -> dict:
        edge_count = sum(len(v) for v in self._out.values())
        type_counts: dict[str, int] = defaultdict(int)
        for n in self._nodes.values():
            type_counts[n.node_type] += 1
        return {
            "node_count": len(self._nodes),
            "edge_count": edge_count,
            "types": dict(type_counts),
            "pinned": sum(1 for n in self._nodes.values() if n.pinned),
            "avg_weight": (
                sum(n.weight for n in self._nodes.values()) / len(self._nodes)
                if self._nodes else 0.0
            ),
        }

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes
