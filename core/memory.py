from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class MemoryEntry:
    '''
    记忆类型 ： AI的思考 | AI执行的动作 | 观察后的结果 | 系统信息
    '''
    role: str   # "thought" | "action" | "observation" | "system"
    content: str


class AgentMemory:
    def __init__(self) -> None:
        self.entries: List[MemoryEntry] = []   # 所有的记忆列表
        self.entry_method: str = ""            # 当前 UI 入口方法签名
        self.mocked_methods: set = set()       # 已经mock的方法
        self.explored_methods: set = set()     # P4 去重用，key=method::mode
        self.explored: Set[str] = set()        # FULL_EXPAND 记忆化，key=纯方法签名（body 已被 LLM 看过）
        self.method_cache: Dict[str, dict] = {}  # body 缓存，key=纯方法签名，value={body_excerpt, tags}
        self.call_stack: List[str] = []        # 方法调用栈
        self.current_depth: int = 0            # 当前探索深度（仅 FULL_EXPAND 时递增）
        self.valid_explored_count: int = 0     # 在 index 中找到并成功分析的方法数（P5 门槛）

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

    def mark_explored(self, method: str) -> None:
        """P4 去重：将 method::mode key 加入集合。"""
        self.explored_methods.add(method)

    def add_to_explored(self, sig: str) -> None:
        """记忆化：标记方法 body 已被 LLM 看过。"""
        self.explored.add(sig)

    def is_body_explored(self, sig: str) -> bool:
        return sig in self.explored

    def cache_method(self, sig: str, body_excerpt: str, tags: list) -> None:
        self.method_cache[sig] = {"body_excerpt": body_excerpt, "tags": tags}

    def get_cached(self, sig: str) -> Optional[dict]:
        return self.method_cache.get(sig)

    def push_call(self, method: str) -> None:
        self.call_stack.append(method)

    def pop_call(self) -> Optional[str]:
        return self.call_stack.pop() if self.call_stack else None

    # --- query helpers ---

    def is_mocked(self, method: str) -> bool:
        return method in self.mocked_methods

    def is_explored(self, method: str) -> bool:
        """P4 去重查询，key=method::mode。"""
        return method in self.explored_methods

    def get_call_chain_str(self) -> str:
        return " → ".join(self.call_stack)

    # 获取上下文
    def get_context(self, max_entries: int = 15, conclusions: Optional[List] = None) -> str:
        parts: List[str] = []

        # ── 结构化摘要（始终置顶，不占滑动窗口配额）──
        summary_lines = [
            "=== Session State ===",
            f"Entry method : {self.entry_method}",
            f"Call chain   : {self.get_call_chain_str() or '(none)'}",
            f"Depth        : {self.current_depth}",
        ]

        if self.mocked_methods:
            summary_lines.append(f"Mocked       : {', '.join(sorted(self.mocked_methods))}")

        if self.explored:
            summary_lines.append(f"Body explored: {', '.join(sorted(self.explored))}")

        if conclusions:
            summary_lines.append("Blocking points found so far:")
            for c in conclusions:
                pattern = getattr(c, "blocking_pattern", "?")
                root = getattr(c, "root_cause", "?")
                chain = " → ".join(getattr(c, "call_chain", []))
                summary_lines.append(f"  [{pattern}] {root}  |  chain: {chain}")

        cached_in_chain = [m for m in self.call_stack if m in self.method_cache]
        if cached_in_chain:
            summary_lines.append("Relevant method bodies:")
            for method in cached_in_chain:
                cached = self.method_cache[method]
                summary_lines.append(f"  [{method}]")
                summary_lines.append(f"    tags: {cached.get('tags', [])}")
                summary_lines.append(f"    body: {cached.get('body_excerpt', '')}")

        summary_lines.append("=== Recent History ===")
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
