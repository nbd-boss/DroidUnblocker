"""
依赖内联器 — DependencyInliner

对目标方法做向内展开：递归地将其调用的项目内方法体内联进来，
直到所有依赖都落在 Android SDK / Java 标准库边界，或触及最大展开深度。
"""
import logging
from dataclasses import dataclass, field
from typing import List, Set

logger = logging.getLogger(__name__)


@dataclass
class InlinedBlock:
    target_method: str
    inlined_code: str
    mocked_callees: List[str] = field(default_factory=list)
    sdk_dependencies: List[str] = field(default_factory=list)


class DependencyInliner:
    def __init__(self, index, max_depth: int = 3) -> None:
        self._index = index
        self._max_depth = max_depth

    def inline(self, target_method: str) -> InlinedBlock:
        mocked: List[str] = []
        sdk_deps: List[str] = []
        seen: Set[str] = set()
        code = self._inline_recursive(target_method, self._max_depth, mocked, sdk_deps, seen)
        return InlinedBlock(
            target_method=target_method,
            inlined_code=code,
            mocked_callees=list(dict.fromkeys(mocked)),
            sdk_dependencies=list(dict.fromkeys(sdk_deps)),
        )

    def _inline_recursive(
        self,
        method_sig: str,
        depth: int,
        mocked: List[str],
        sdk_deps: List[str],
        seen: Set[str],
    ) -> str:
        seen.add(method_sig)

        record = self._index.get_method(method_sig)
        if record is None or not record.body:
            sdk_deps.append(method_sig)
            return ""

        if depth == 0:
            mocked.append(method_sig)
            return f"// [MOCK: {method_sig}] — depth limit reached"

        parts = [f"// ↳ [{method_sig}] inlined", record.body.rstrip()]

        # Recurse into project-internal callees
        for callee in self._index.get_direct_callees(method_sig):
            if callee.file == "<external>":
                if callee.signature not in sdk_deps:
                    sdk_deps.append(callee.signature)
            elif callee.signature not in seen:
                nested = self._inline_recursive(
                    callee.signature, depth - 1, mocked, sdk_deps, seen
                )
                if nested:
                    parts.append(nested)

        return "\n".join(parts)
