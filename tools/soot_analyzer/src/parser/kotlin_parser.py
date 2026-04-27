"""
Kotlin 源文件解析器（正则实现）

变量类型推断：暂未实现，callee 签名仍使用接收者变量名。
扩展点：替换为 tree-sitter-kotlin 后在此处实现 _build_var_type_map。
"""
import logging
import re
from pathlib import Path
from typing import List

from tools.soot_analyzer.src.parser.java_parser import CallSite, ClassInfo, FieldInfo, MethodInfo
from tools.soot_analyzer.src.utils.android_constants import NOISE_METHODS, NOISE_RECEIVERS
from tools.soot_analyzer.src.utils.signature import make_sig

logger = logging.getLogger(__name__)

_KT_CLASS_RE = re.compile(
    r'(?:open\s+|abstract\s+|data\s+|sealed\s+)?class\s+(\w+)'
    r'(?:\s*\([^)]*\))?'
    r'(?:\s*:\s*([\w,\s().<>?]+))?'
)
_KT_FUN_RE = re.compile(
    r'(?:override\s+|private\s+|protected\s+|internal\s+|open\s+|suspend\s+)*'
    r'fun\s+(\w+)\s*\([^)]*\)'
    r'(?:\s*:\s*[\w<>?.]+)?\s*\{'
)
_KT_ANNOTATION_RE = re.compile(r'@(\w+)(?:\([^)]*\))?')


def _extract_block(source: str, start: int) -> str:
    depth, i = 0, start
    while i < len(source):
        c = source[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return source[start: i + 1]
        i += 1
    return source[start: min(start + 3000, len(source))]


def _find_class_at(source: str, pos: int) -> str:
    best = "Unknown"
    for m in re.finditer(r'\bclass\s+(\w+)', source[:pos]):
        best = m.group(1)
    return best


def _extract_call_sites(body: str, class_name: str, method_start_line: int = 1) -> List[CallSite]:
    """正则提取调用点，无变量类型推断（扩展点）。"""
    sites: List[CallSite] = []
    seen = set()

    for m in re.finditer(r'(\w+)\.(\w+)\s*\(', body):
        receiver, method = m.group(1), m.group(2)
        if receiver in NOISE_RECEIVERS or method in NOISE_METHODS:
            continue
        resolved = class_name if receiver == "this" else receiver
        sig = make_sig(resolved, method)
        if sig not in seen:
            seen.add(sig)
            line_no = method_start_line + body[:m.start()].count("\n")
            sites.append(CallSite(callee_name=sig, receiver_type=resolved, line_number=line_no))

    keywords = frozenset({
        "if", "while", "for", "switch", "return", "new", "throw",
        "catch", "try", "else", "super", "this", "assert",
    })
    for m in re.finditer(r'\b([a-z][a-zA-Z0-9_]*)\s*\(', body):
        method = m.group(1)
        if method in keywords:
            continue
        sig = make_sig(class_name, method)
        if sig not in seen:
            seen.add(sig)
            line_no = method_start_line + body[:m.start()].count("\n")
            sites.append(CallSite(callee_name=sig, receiver_type=class_name, line_number=line_no))

    return sites


def parse_kotlin_file(filepath: str) -> List[ClassInfo]:
    source = Path(filepath).read_text(encoding="utf-8", errors="ignore")
    class_parents = {}
    class_interfaces = {}

    for m in _KT_CLASS_RE.finditer(source):
        cn = m.group(1)
        supers_raw = m.group(2) or ""
        supers = [
            s.strip().split("(")[0].split(".")[-1].split("<")[0]
            for s in supers_raw.split(",") if s.strip()
        ]
        if supers:
            class_parents[cn] = supers[0]
            class_interfaces[cn] = supers[1:]

    classes_map = {}

    for m in _KT_FUN_RE.finditer(source):
        method_name = m.group(1)
        line_no = source[: m.start()].count("\n") + 1
        body = _extract_block(source, m.end() - 1)
        class_name = _find_class_at(source, m.start())
        annotations = _KT_ANNOTATION_RE.findall(source[max(0, m.start() - 200): m.start()])
        is_override = "override" in source[max(0, m.start() - 10): m.start()]

        call_sites = _extract_call_sites(body, class_name, line_no)

        method_info = MethodInfo(
            signature=make_sig(class_name, method_name),
            class_name=class_name,
            method_name=method_name,
            source_file=filepath,
            line=line_no,
            body=body,
            call_sites=call_sites,
            annotations=annotations,
            is_override=is_override,
        )

        if class_name not in classes_map:
            classes_map[class_name] = ClassInfo(
                name=class_name,
                superclass=class_parents.get(class_name),
                interfaces=class_interfaces.get(class_name, []),
                fields=[],
                methods=[],
                source_file=filepath,
            )
        classes_map[class_name].methods.append(method_info)

    return list(classes_map.values())
