"""
Java 源文件解析器（基于 javalang AST）

变量类型推断优先级：
  1. 局部变量声明（ClassName var = new ClassName(...)）
  2. 方法参数（TypeName paramName）
  3. 字段声明（TypeName fieldName）

后续扩展点：方法返回类型推断、泛型类型擦除处理。
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import javalang

from tools.soot_analyzer.src.utils.android_constants import NOISE_METHODS, NOISE_RECEIVERS
from tools.soot_analyzer.src.utils.signature import make_sig

logger = logging.getLogger(__name__)


@dataclass
class FieldInfo:
    name: str
    type_name: str


@dataclass
class CallSite:
    callee_name: str
    receiver_type: Optional[str]  # 解析后的类名；None 表示无法解析
    line_number: int


@dataclass
class MethodInfo:
    signature: str
    class_name: str
    method_name: str
    source_file: str
    line: int
    body: str
    call_sites: List[CallSite] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    is_override: bool = False
    parameters: List[Tuple[str, str]] = field(default_factory=list)  # (type, name)


@dataclass
class ClassInfo:
    name: str
    superclass: Optional[str]
    interfaces: List[str]
    fields: List[FieldInfo]
    methods: List[MethodInfo]
    source_file: str


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _type_name(type_node) -> str:
    """从 javalang type 节点提取简单类名。"""
    if type_node is None:
        return ""
    if hasattr(type_node, "name"):
        return type_node.name
    return str(type_node)


def _extract_body(source: str, start_line: int) -> str:
    """从 start_line（1-based）起提取第一个完整 {} 块。"""
    lines = source.splitlines()
    depth = 0
    collecting = False
    result: List[str] = []

    for i, line in enumerate(lines[start_line - 1:], start=start_line):
        for ch in line:
            if ch == "{":
                depth += 1
                collecting = True
            elif ch == "}":
                depth -= 1
        result.append(line)
        if collecting and depth == 0:
            break

    return "\n".join(result)


def _build_var_type_map(
    method_node,
    param_types: Dict[str, str],
    field_types: Dict[str, str],
) -> Dict[str, str]:
    """
    构建方法内 {变量名 → 类名} 映射。
    来源：局部变量声明 > 方法参数 > 字段。
    """
    mapping: Dict[str, str] = {**field_types, **param_types}

    for _, node in method_node.filter(javalang.tree.LocalVariableDeclaration):
        type_name = _type_name(node.type)
        for declarator in node.declarators:
            mapping[declarator.name] = type_name

    return mapping


def _extract_call_sites(
    body: str,
    var_type_map: Dict[str, str],
    class_name: str,
    method_start_line: int = 1,
) -> List[CallSite]:
    """
    从方法体文本中提取调用点，用 var_type_map 将接收者变量名替换为类名。
    line_number = method_start_line + 匹配点前的换行数。
    """
    sites: List[CallSite] = []
    seen = set()

    # 支持多级静态调用：A.B.C() → 取最后两段，前缀作为候选类名
    for m in re.finditer(r'((?:\w+\.)*\w+)\.(\w+)\s*\(', body):
        receiver_chain, method = m.group(1), m.group(2)
        if method in NOISE_METHODS:
            continue

        # receiver_chain 可能是 "Queries.HistoryTable" 或单个 "db"
        # 取最后一段作为直接接收者，先查 var_type_map
        last_segment = receiver_chain.rsplit(".", 1)[-1]
        if last_segment in NOISE_RECEIVERS:
            continue

        if last_segment == "this":
            resolved = class_name
        else:
            # 优先用 var_type_map 解析变量名；若查不到，直接用 last_segment（类名场景）
            resolved = var_type_map.get(last_segment, last_segment)

        callee_sig = make_sig(resolved, method)
        if callee_sig not in seen:
            seen.add(callee_sig)
            line_no = method_start_line + body[:m.start()].count("\n")
            sites.append(CallSite(
                callee_name=callee_sig,
                receiver_type=resolved,
                line_number=line_no,
            ))

    # 裸方法调用（同类内调用，如 initViews()）
    keywords = frozenset({
        "if", "while", "for", "switch", "return", "new", "throw",
        "catch", "try", "else", "super", "this", "assert",
    })
    for m in re.finditer(r'\b(?<!\.)([a-z][a-zA-Z0-9_]*)\s*\(', body):
        method = m.group(1)
        if method in keywords:
            continue
        callee_sig = make_sig(class_name, method)
        if callee_sig not in seen:
            seen.add(callee_sig)
            line_no = method_start_line + body[:m.start()].count("\n")
            sites.append(CallSite(
                callee_name=callee_sig,
                receiver_type=class_name,
                line_number=line_no,
            ))

    return sites


# ── 主解析函数 ────────────────────────────────────────────────────────────────

def parse_java_file(filepath: str) -> List[ClassInfo]:
    source = Path(filepath).read_text(encoding="utf-8", errors="ignore")
    try:
        tree = javalang.parse.parse(source)
    except javalang.parser.JavaSyntaxError as e:
        logger.debug(f"javalang parse error in {filepath}: {e}")
        return []
    except Exception as e:
        logger.debug(f"Unexpected error parsing {filepath}: {e}")
        return []

    classes: List[ClassInfo] = []

    for _, class_decl in tree.filter(javalang.tree.ClassDeclaration):
        superclass = None
        if class_decl.extends:
            superclass = _type_name(class_decl.extends)

        interfaces = [_type_name(i) for i in (class_decl.implements or [])]

        fields: List[FieldInfo] = []
        field_types: Dict[str, str] = {}
        for _, fd in class_decl.filter(javalang.tree.FieldDeclaration):
            type_name = _type_name(fd.type)
            for decl in fd.declarators:
                fields.append(FieldInfo(name=decl.name, type_name=type_name))
                field_types[decl.name] = type_name

        methods: List[MethodInfo] = []
        for _, md in class_decl.filter(javalang.tree.MethodDeclaration):
            annotations = [a.name for a in (md.annotations or [])]
            is_override = "Override" in annotations

            params: List[Tuple[str, str]] = []
            param_types: Dict[str, str] = {}
            for p in (md.parameters or []):
                t = _type_name(p.type)
                params.append((t, p.name))
                param_types[p.name] = t

            start_line = md.position.line if md.position else 1
            body = _extract_body(source, start_line)

            var_type_map = _build_var_type_map(md, param_types, field_types)
            call_sites = _extract_call_sites(body, var_type_map, class_decl.name, start_line)

            methods.append(MethodInfo(
                signature=make_sig(class_decl.name, md.name),
                class_name=class_decl.name,
                method_name=md.name,
                source_file=filepath,
                line=start_line,
                body=body,
                call_sites=call_sites,
                annotations=annotations,
                is_override=is_override,
                parameters=params,
            ))

        classes.append(ClassInfo(
            name=class_decl.name,
            superclass=superclass,
            interfaces=interfaces,
            fields=fields,
            methods=methods,
            source_file=filepath,
        ))

    return classes
