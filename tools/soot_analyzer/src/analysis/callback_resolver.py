"""
回调注册解析器

支持的模式：
  1. 匿名内部类：setXxxListener(new Interface() { void method() {...} })
  2. 具名类实例：setXxxListener(new MyHandler())  → MyHandler 实现了对应接口
  3. this：setXxxListener(this)  → 当前类实现了对应接口

暂不支持：Lambda、方法引用。
"""
import re
from dataclasses import dataclass
from typing import Dict, List

from tools.soot_analyzer.src.parser.java_parser import MethodInfo
from tools.soot_analyzer.src.utils.signature import make_sig

_LISTENER_RE = re.compile(r'\bset\w*Listener\s*\(|add\w*Listener\s*\(')
_ANON_CLASS_RE = re.compile(r'new\s+(\w+)\s*\(\s*\)\s*\{')
_NAMED_CLASS_RE = re.compile(r'new\s+(\w+)\s*\(')


@dataclass
class CallbackEdge:
    caller_sig: str       # 注册所在方法签名
    callee_sig: str       # 回调实现方法签名
    edge_type: str = "CALLBACK_REGISTRATION"


def resolve(method_infos: Dict[str, MethodInfo]) -> List[CallbackEdge]:
    edges: List[CallbackEdge] = []

    for sig, method in method_infos.items():
        body = method.body
        if not _LISTENER_RE.search(body):
            continue

        # 匿名内部类模式：提取 new InterfaceName() { ... } 中的重写方法
        for m in _ANON_CLASS_RE.finditer(body):
            iface = m.group(1)
            block_start = body.find("{", m.end() - 1)
            if block_start == -1:
                continue
            # 在块内查找方法名（简单启发：找 public void/boolean xxxMethod(
            block = _extract_block(body, block_start)
            for callback_m in re.finditer(
                r'(?:public|protected)\s+\w[\w<>\[\]]*\s+(\w+)\s*\(', block
            ):
                callee_sig = make_sig(iface, callback_m.group(1))
                edges.append(CallbackEdge(caller_sig=sig, callee_sig=callee_sig))

    return edges


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
    return source[start: min(start + 2000, len(source))]
