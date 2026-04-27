"""
调用场景收集器 — CallerContextCollector

从调用图中反向找到目标方法的所有调用者，
提取每个调用者从方法入口到目标调用点的完整语句序列（pre_call_statements），
而非仅提取参数表达式——完整的前置语句序列才能还原真实的调用状态。
"""
import logging
import re
from dataclasses import dataclass, field
from typing import List, Tuple

logger = logging.getLogger(__name__)

_MAX_CALLERS = 5  # 最多保留 5 个调用者上下文，避免 prompt 过长


@dataclass
class CallerContext:
    caller_method: str
    pre_call_statements: str
    argument_expressions: List[str] = field(default_factory=list)
    is_primary: bool = False  # True 表示该调用者在 call_chain 中，pre_call_statements 作为主要测试输入


class CallerContextCollector:
    def __init__(self, index) -> None:
        self._index = index

    def collect(self, target_method: str, call_chain: List[str] = None) -> List[CallerContext]:
        target_short = target_method.rsplit(".", 1)[-1]
        call_chain_set = set(call_chain or [])
        results: List[CallerContext] = []

        for sig, record in self._index.methods.items():
            if sig == target_method:
                continue
            if not record.body:
                continue

            is_caller = (
                target_method in record.callees
                or any(target_method in c for c in record.callees)
                or re.search(rf'\b{re.escape(target_short)}\s*\(', record.body) is not None
            )
            if not is_caller:
                continue

            pre_stmts, args = self._extract_pre_call(record.body, target_short)
            if not pre_stmts:
                continue

            is_primary = sig in call_chain_set
            results.append(CallerContext(
                caller_method=sig,
                pre_call_statements=pre_stmts,
                argument_expressions=args,
                is_primary=is_primary,
            ))

            if len(results) >= _MAX_CALLERS:
                break

        return results

    def _extract_pre_call(self, body: str, target_method_name: str) -> Tuple[str, List[str]]:
        lines = body.split("\n")
        call_line_idx = -1
        call_pattern = re.compile(rf'\b{re.escape(target_method_name)}\s*\(')

        for i, line in enumerate(lines):
            if call_pattern.search(line):
                call_line_idx = i
                break

        if call_line_idx == -1:
            return ("", [])

        # Include all non-empty lines from method start through the call site
        pre_lines = [ln for ln in lines[:call_line_idx + 1] if ln.strip()]
        pre_stmts = "\n".join(pre_lines)

        args = self._extract_args(lines[call_line_idx], target_method_name)
        return (pre_stmts, args)

    @staticmethod
    def _extract_args(call_line: str, method_name: str) -> List[str]:
        m = re.search(rf'\b{re.escape(method_name)}\s*\(([^)]*)\)', call_line)
        if not m:
            return []
        args_str = m.group(1).strip()
        if not args_str:
            return []
        return [a.strip() for a in args_str.split(",")]
