from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class MemoryEntry:
    '''
    记忆类型 ： AI的思考 | AI执行的动作 | 观察后的结果 | 系统信息
    '''
    role: str   # "thought" | "action" | "observation" | "system"
    content: str


@dataclass
class TreeNode:
    node_id: int
    signature: str
    tags: List[str] = field(default_factory=list)
    body_excerpt: str = ""
    expanded: bool = False
    verdict: str = ""             # "" | "BLOCKED" | "CLEAN"
    blocking_pattern: str = ""
    reuse_from: Optional[int] = None  # alias 节点指向原始节点 ID


class AgentMemory:
    def __init__(self) -> None:
        self.entries: List[MemoryEntry] = []
        self.entry_method: str = ""
        self.mocked_methods: set = set()
        self.valid_explored_count: int = 0

        # 树状结构
        self.tree_nodes: Dict[int, TreeNode] = {}          # node_id → TreeNode
        self.parent: Dict[int, Optional[int]] = {}         # node_id → 父节点 node_id
        self.sig_to_ids: Dict[str, List[int]] = {}         # 签名 → node_id 列表
        self.current_focus: int = -1                       # 当前焦点节点 ID
        self._next_id: int = 0

    # --- mutation helpers ---

    def add(self, role: str, content: str) -> None:
        self.entries.append(MemoryEntry(role=role, content=content))

    def add_thought(self, thought: str) -> None:
        self.add("thought", thought)

    def add_action(self, action: str) -> None:
        self.add("action", action)

    def add_observation(self, observation: str) -> None:
        self.add("observation", observation)

    def mark_mocked(self, method: str) -> None:
        self.mocked_methods.add(method)

    def register_node(self, sig: str, tags: List[str], parent_id: Optional[int]) -> int:
        """发现新节点时调用（callee 被返回时）。若签名已有 expanded 节点则标记为 alias。"""
        existing_ids = self.sig_to_ids.get(sig, [])
        reuse_from = None
        for eid in existing_ids:
            if self.tree_nodes[eid].expanded:
                reuse_from = eid
                break

        node = TreeNode(
            node_id=self._next_id,
            signature=sig,
            tags=tags,
            reuse_from=reuse_from,
        )
        self.tree_nodes[self._next_id] = node
        self.parent[self._next_id] = parent_id
        self.sig_to_ids.setdefault(sig, []).append(self._next_id)
        node_id = self._next_id
        self._next_id += 1
        return node_id

    def expand_node(self, node_id: int, body_excerpt: str, tags: List[str]) -> None:
        """EXPAND 成功后调用，填充 body、标记 expanded=True。"""
        node = self.tree_nodes.get(node_id)
        if node is None:
            return
        node.body_excerpt = body_excerpt
        node.tags = tags
        node.expanded = True

    def set_verdict(self, node_id: int, verdict: str, blocking_pattern: str = "") -> None:
        """CONCLUDE 时回写结论到当前焦点节点。"""
        node = self.tree_nodes.get(node_id)
        if node is None:
            return
        node.verdict = verdict
        node.blocking_pattern = blocking_pattern

    def get_path_to(self, node_id: int) -> List[int]:
        """沿 parent 指针回溯，返回从根到 node_id 的 ID 列表。"""
        path, cur = [], node_id
        while cur is not None:
            path.append(cur)
            cur = self.parent.get(cur)
        return list(reversed(path))

    def is_body_explored(self, sig: str) -> bool:
        """签名对应的方法是否已被 EXPAND 过。"""
        for nid in self.sig_to_ids.get(sig, []):
            if self.tree_nodes[nid].expanded:
                return True
        return False

    def is_mocked(self, method: str) -> bool:
        return method in self.mocked_methods

    # 获取上下文
    def get_context(self, max_entries: int = 15, conclusions: Optional[List] = None) -> str:
        parts: List[str] = []

        # ── 结构化摘要（始终置顶）──
        summary_lines = ["=== Session State ==="]
        summary_lines.append(f"Entry method : {self.entry_method}")

        # 主路径
        if self.current_focus >= 0:
            path_ids = self.get_path_to(self.current_focus)
            path_sigs = [self.tree_nodes[i].signature for i in path_ids]
            summary_lines.append(f"Current path : {' → '.join(path_sigs)}")
        else:
            path_ids = []

        if self.mocked_methods:
            summary_lines.append(f"Mocked       : {', '.join(sorted(self.mocked_methods))}")

        if conclusions:
            summary_lines.append("Blocking points found so far:")
            for c in conclusions:
                pattern = getattr(c, "blocking_pattern", "?")
                root = getattr(c, "root_cause", "?")
                chain = " → ".join(getattr(c, "call_chain", []))
                summary_lines.append(f"  [{pattern}] {root}  |  chain: {chain}")

        # 主路径节点：签名 + tags + 完整 body
        path_id_set = set(path_ids)
        if path_ids:
            summary_lines.append("\n► [CURRENT PATH]")
            for nid in reversed(path_ids):
                node = self.tree_nodes[nid]
                label = "[FOCUS]" if nid == self.current_focus else "[PATH]"
                summary_lines.append(f"  {node.signature}  {label}  tags={node.tags}")
                if node.body_excerpt:
                    summary_lines.append(f"    body: {node.body_excerpt}")

        # 旁路节点：已展开但不在主路径
        off_path_explored = [
            n for n in self.tree_nodes.values()
            if n.expanded and n.node_id not in path_id_set and n.reuse_from is None
        ]
        if off_path_explored:
            summary_lines.append("\n✓ [EXPLORED - off path]")
            for node in off_path_explored:
                verdict_str = f"→ {node.verdict}({node.blocking_pattern})" if node.verdict else ""
                summary_lines.append(f"  {node.signature}  tags={node.tags}  {verdict_str}")

        # alias 节点
        alias_nodes = [n for n in self.tree_nodes.values() if n.reuse_from is not None]
        if alias_nodes:
            summary_lines.append("\n↺ [REUSED]")
            for node in alias_nodes:
                original = self.tree_nodes[node.reuse_from]
                verdict_str = f"→ {original.verdict}({original.blocking_pattern})" if original.verdict else ""
                summary_lines.append(
                    f"  {node.signature}  tags={node.tags}  reused from node#{node.reuse_from}  {verdict_str}"
                )

        # 未展开节点
        unexplored = [
            n for n in self.tree_nodes.values()
            if not n.expanded and n.reuse_from is None and n.node_id not in path_id_set
        ]
        if unexplored:
            summary_lines.append("\n○ [EXPANDABLE - not yet explored]")
            for node in unexplored:
                summary_lines.append(f"  {node.signature}  tags={node.tags}")

        summary_lines.append("\n=== Recent History ===")
        parts.append("\n".join(summary_lines))

        # ── 滑动窗口：最近 max_entries 条 entry ──
        recent = self.entries[-max_entries:]
        role_prefix = {
            "thought":     "[Thought]",
            "action":      "[Action]",
            "observation": "[Observation]",
            "system":      "[System]",
        }
        parts.append("\n".join(
            f"{role_prefix.get(e.role, f'[{e.role}]')} {e.content}"
            for e in recent
        ))

        return "\n".join(parts)
