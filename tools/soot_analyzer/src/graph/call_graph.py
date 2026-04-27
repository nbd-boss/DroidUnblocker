from typing import Dict, List, Optional, Set, Tuple


class CallGraph:
    def __init__(self) -> None:
        self._nodes: Dict[str, dict] = {}
        self._edges: Dict[str, List[dict]] = {}   # caller → [{callee, type, line}]
        self._reverse: Dict[str, List[str]] = {}  # callee → [callers]

    def add_node(self, sig: str, metadata: dict) -> None:
        self._nodes.setdefault(sig, metadata)

    def add_edge(self, caller: str, callee: str, edge_type: str = "DIRECT", line: int = 0) -> None:
        self._edges.setdefault(caller, [])
        self._reverse.setdefault(callee, [])
        entry = {"callee": callee, "type": edge_type, "line": line}
        if entry not in self._edges[caller]:
            self._edges[caller].append(entry)
            self._reverse[callee].append(caller)

    def get_callees(self, sig: str) -> List[str]:
        return [e["callee"] for e in self._edges.get(sig, [])]

    def get_callers(self, sig: str) -> List[str]:
        return self._reverse.get(sig, [])

    def get_edge_info(self, caller: str) -> List[dict]:
        return self._edges.get(caller, [])

    def get_node_metadata(self, sig: str) -> Optional[dict]:
        return self._nodes.get(sig)

    def has_node(self, sig: str) -> bool:
        return sig in self._nodes

    def has_edge(self, caller: str, callee: str) -> bool:
        return any(e["callee"] == callee for e in self._edges.get(caller, []))

    def get_all_nodes(self) -> List[str]:
        return list(self._nodes.keys())

    def get_all_edges(self) -> List[Tuple[str, str, dict]]:
        result = []
        for caller, edges in self._edges.items():
            for e in edges:
                result.append((caller, e["callee"], e))
        return result

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return sum(len(v) for v in self._edges.values())

    def get_reachable_from(self, sig: str) -> Set[str]:
        visited: Set[str] = set()
        stack = [sig]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            stack.extend(self.get_callees(cur))
        return visited

    def to_dict(self) -> dict:
        nodes = [
            {"signature": sig, "metadata": meta}
            for sig, meta in self._nodes.items()
        ]
        edges = [
            {"caller": caller, "callee": e["callee"],
             "call_site": {"line_number": e["line"], "type": e["type"]}}
            for caller, edge_list in self._edges.items()
            for e in edge_list
        ]
        return {"nodes": nodes, "edges": edges}

    @classmethod
    def from_dict(cls, data: dict) -> "CallGraph":
        g = cls()
        for node in data.get("nodes", []):
            g.add_node(node["signature"], node.get("metadata", {}))
        for edge in data.get("edges", []):
            cs = edge.get("call_site", {})
            g.add_edge(edge["caller"], edge["callee"],
                       cs.get("type", "DIRECT"), cs.get("line_number", 0))
        return g
