"""
程序切片器 — Tool 3

对可疑方法的源码做后向静态切片：
给定切片准则 <target_stmt, target_var>，提取所有影响该变量在目标语句处取值的语句。
算法基于文本近似 PDG 遍历（def-use 链 + 控制依赖）。

优化点：
1. 切片结果携带文件内绝对行号，LLM 可精确定位
2. found 字段区分"方法不在 index"与"切片为空"，与 CallChainExpander 保持一致
3. 1 层跨方法数据流追踪：RHS 为项目内方法时，自动附加其返回值相关行作为补充上下文
"""
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from core.types import ToolResult
from tools.registry import BaseTool
from tools.soot_analyzer import get_index

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r'\b(\$?[a-z_]\w*)\b')
_ASSIGN_RE = re.compile(r'^(\$?\w+)\s*[+\-*/%&|^]?=(?!=)')   # a = ..., a += ...
_TYPE_DECL_RE = re.compile(                                    # String sql = ..., Cursor c = ...
    r'^(?:(?:final|static|volatile|transient|synchronized)\s+)*'
    r'(?:[A-Z]\w*(?:<[^>]*>)?(?:\[\])*'
    r'|int|long|float|double|boolean|byte|char|short)(?:\[\])*'
    r'\s+(\$?[a-z_]\w*)\s*'
    r'[+\-*/%&|^]?=(?!=)'
)
_KT_DECL_RE = re.compile(                                      # val sql = ..., var cursor: Cursor = ...
    r'^(?:val|var)\s+(\$?[a-z_]\w*)(?:\s*:\s*\w[\w.<>?, ]*)?\s*[+\-*/%&|^]?=(?!=)'
)
_BRANCH_RE = re.compile(r'^\s*(if|while|for|switch)\s*\(')
_CALLEE_FROM_ASSIGN_RE = re.compile(r'=\s*(?:\w+\.)*(\w+)\s*\(')
MAX_PASSES = 5

_NOISE_TOKENS = frozenset({
    "if", "while", "for", "switch", "return", "new", "null", "true", "false",
    "this", "super", "class", "void", "int", "long", "float", "double",
    "boolean", "byte", "char", "short", "else", "try", "catch", "finally",
    "throw", "throws", "import", "package", "static", "final", "public",
    "private", "protected", "abstract", "override", "fun", "val", "var",
})


def _extract_vars(line: str) -> Set[str]:
    return {
        m for m in _VAR_RE.findall(line)
        if len(m) > 1 and m not in _NOISE_TOKENS
    }


def _defined_var(line: str) -> Optional[str]:
    stripped = line.strip()
    # 1. Java type declaration: String sql = ..., Cursor c = ..., int value = ...
    m = _TYPE_DECL_RE.match(stripped)
    if m:
        return m.group(1)
    # 2. Kotlin val/var declaration: val sql = ..., var cursor: Cursor = ...
    m = _KT_DECL_RE.match(stripped)
    if m:
        return m.group(1)
    # 3. Simple or compound assignment: sql = ..., sql += ...
    m = _ASSIGN_RE.match(stripped)
    if m:
        candidate = m.group(1)
        if candidate not in _NOISE_TOKENS:
            return candidate
    return None


def backward_slice(
    body: str,
    target_stmt: str,
    target_var: Optional[str] = None,
    method_start_line: int = 1,
) -> Tuple[List[Dict], Set[str]]:
    """
    后向静态切片，返回 (slice_items, use_vars)。

    slice_items: [{"line": int, "code": str}, ...]
      line 为文件内绝对行号（1-based），与源文件行号对齐。
    use_vars: 切片过程中收集的变量集合，供跨方法追踪使用。
    """
    raw_lines = body.split("\n")
    # 保留行号映射，过滤纯括号行
    indexed: List[Tuple[int, str]] = [
        (method_start_line + i, ln)
        for i, ln in enumerate(raw_lines)
        if ln.strip() not in ("{", "}")
    ]

    # 种子行：包含 target_stmt 的所有行
    seeds: Set[int] = {
        i for i, (_, ln) in enumerate(indexed) if target_stmt in ln
    }
    if not seeds:
        # 未找到目标语句，返回方法头部供参考
        return (
            [{"line": lineno, "code": ln}
             for lineno, ln in indexed[:15] if ln.strip()],
            set(),
        )

    use_vars: Set[str] = set()
    if target_var:
        use_vars.update({target_var.lstrip("$"), target_var})
    for idx in seeds:
        use_vars |= _extract_vars(indexed[idx][1])

    slice_idx: Set[int] = set(seeds)

    for _ in range(MAX_PASSES):
        prev = len(slice_idx)
        for i, (_, ln) in enumerate(indexed):
            stripped = ln.strip()
            # 数据依赖：定义了 use_vars 中变量的语句
            def_var = _defined_var(stripped)
            if def_var and (def_var in use_vars or def_var.lstrip("$") in use_vars):
                slice_idx.add(i)
                use_vars |= _extract_vars(stripped)
            # 控制依赖：条件语句中含 use_vars 变量
            if _BRANCH_RE.match(stripped):
                if _extract_vars(stripped) & use_vars:
                    slice_idx.add(i)
        if len(slice_idx) == prev:
            break

    return (
        [{"line": indexed[i][0], "code": indexed[i][1]}
         for i in sorted(slice_idx) if indexed[i][1].strip()],
        use_vars,
    )


def _interprocedural_extension(
    slice_items: List[Dict],
    use_vars: Set[str],
    index,
) -> List[Dict]:
    """
    1 层跨方法数据流追踪。

    对切片中每条 "var = method(...)" 形式的赋值语句：
    若被调用方法在项目 index 中存在，提取其方法体内与返回值相关的行
    作为补充上下文（每个 callee 最多 8 行）。
    """
    extra: List[Dict] = []
    seen_callees: Set[str] = set()

    for item in slice_items:
        ln = item["code"].strip()
        def_var = _defined_var(ln)
        if not def_var:
            continue
        if def_var not in use_vars and def_var.lstrip("$") not in use_vars:
            continue

        m = _CALLEE_FROM_ASSIGN_RE.search(ln)
        if not m:
            continue
        callee_name = m.group(1)
        if callee_name in seen_callees or callee_name in _NOISE_TOKENS:
            continue
        seen_callees.add(callee_name)

        callee_record = index.get_method(callee_name)
        if callee_record is None or not callee_record.body:
            continue

        callee_lines = callee_record.body.split("\n")
        related = [
            {
                "line": callee_record.line + i,
                "code": f"  ↳[{callee_record.signature}] {cl}",
                "interprocedural": True,
            }
            for i, cl in enumerate(callee_lines)
            if cl.strip() and (
                re.search(r'\breturn\b', cl)
                or _defined_var(cl.strip()) is not None
            )
        ][:8]
        extra.extend(related)

    return extra


class ProgramSlicerTool(BaseTool):
    @property
    def name(self) -> str:
        return "ProgramSlicer"

    @property
    def skill_metadata(self) -> dict:
        return {
            "name": "ProgramSlicer",
            "description": (
                "基于切片准则（目标语句关键词 + 关注变量），对方法做后向静态切片，"
                "提取影响该变量在目标语句处取值的所有相关语句（数据依赖 + 控制依赖）。"
                "切片结果携带文件绝对行号；若赋值 RHS 为项目内方法，自动追踪 1 层跨方法数据流。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "待切片方法的签名，如 HistoryActivity.onCreate",
                    },
                    "target_stmt": {
                        "type": "string",
                        "description": "目标语句关键词，如 rawQuery、openStream、listFiles",
                    },
                    "target_var": {
                        "type": "string",
                        "description": "关注变量名（可选），如 query、cursor",
                    },
                },
                "required": ["method", "target_stmt"],
            },
            "returns": (
                '{ "method":"...", "found":bool, "criterion_stmt":"...", "criterion_var":"...", '
                '"method_line":N, "slice":[{"line":N,"code":"..."},...], '
                '"interprocedural_context":[{"line":N,"code":"...","interprocedural":true},...], '
                '"slice_size":N }'
            ),
            "usage_hints": [
                "仅在 CallChainExpander 已定位到可疑方法后调用（EXPLORE 或 CONCLUDE 路径）。",
                "target_stmt 设置为阻塞 API 关键词（如 rawQuery、openStream）。",
                "不要对所有方法做切片——只对已确认可疑的节点使用。",
                "切片结果通常 5-15 行，直接喂给 LLM 做因果分析。",
                "found=false 时方法不在 index，不要猜测衍生方法名。",
                "interprocedural_context 提供 1 层跨方法补充，关注 interprocedural=true 的条目。",
            ],
        }

    def execute(self, params: dict) -> ToolResult:
        method = params.get("method", "")
        target_stmt = params.get("target_stmt", "")
        target_var = params.get("target_var", "") or None

        if not method or not target_stmt:
            return ToolResult(success=False, error="method and target_stmt are required")

        index = get_index()
        if index is None:
            return ToolResult(
                success=False,
                error="Source index not initialized. Call SootStaticAnalyzer first.",
            )

        record = index.get_method(method)
        if record is None:
            return ToolResult(
                success=True,
                data={
                    "method": method,
                    "found": False,
                    "criterion_stmt": target_stmt,
                    "criterion_var": target_var or "",
                    "method_line": -1,
                    "slice": [],
                    "interprocedural_context": [],
                    "slice_size": 0,
                },
            )

        slice_items, use_vars = backward_slice(
            record.body,
            target_stmt,
            target_var,
            method_start_line=record.line,
        )
        interproc = _interprocedural_extension(slice_items, use_vars, index)

        return ToolResult(
            success=True,
            data={
                "method": method,
                "found": True,
                "criterion_stmt": target_stmt,
                "criterion_var": target_var or "",
                "method_line": record.line,
                "slice": slice_items,
                "interprocedural_context": interproc,
                "slice_size": len(slice_items),
            },
        )
