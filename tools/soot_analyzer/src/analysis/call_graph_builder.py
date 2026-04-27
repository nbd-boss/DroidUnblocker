from typing import Dict, List

from tools.soot_analyzer.src.analysis.callback_resolver import CallbackEdge
from tools.soot_analyzer.src.graph.call_graph import CallGraph
from tools.soot_analyzer.src.parser.java_parser import MethodInfo


def build(
    method_infos: Dict[str, MethodInfo],
    callback_edges: List[CallbackEdge],
) -> CallGraph:
    graph = CallGraph()

    # 注册所有已知方法为节点
    for sig, method in method_infos.items():
        graph.add_node(sig, {
            "class_fqn": method.class_name,
            "source_file": method.source_file,
        })

    # 直接调用边（来自 CallSite，receiver_type 已解析为类名）
    for sig, method in method_infos.items():
        for site in method.call_sites:
            callee_sig = site.callee_name
            if callee_sig not in graph._nodes:
                # 外部方法：仍然加节点，标记为 external
                graph.add_node(callee_sig, {
                    "class_fqn": site.receiver_type or "",
                    "source_file": "<external>",
                })
            graph.add_edge(sig, callee_sig, edge_type="DIRECT", line=site.line_number)

    # 回调注册边
    for edge in callback_edges:
        if edge.caller_sig not in graph._nodes:
            graph.add_node(edge.caller_sig, {"source_file": "<unknown>"})
        if edge.callee_sig not in graph._nodes:
            graph.add_node(edge.callee_sig, {"source_file": "<unknown>"})
        graph.add_edge(edge.caller_sig, edge.callee_sig,
                       edge_type=edge.edge_type, line=0)

    return graph
