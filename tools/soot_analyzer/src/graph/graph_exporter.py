import json
import re
from pathlib import Path
from typing import Dict, List, Set

from tools.soot_analyzer.src.analysis.ui_entry_detector import UIEntryPoint
from tools.soot_analyzer.src.graph.call_graph import CallGraph
from tools.soot_analyzer.src.parser.java_parser import MethodInfo
from tools.soot_analyzer.src.utils.android_constants import TAG_PATTERNS


def _extract_tags(body: str) -> List[str]:
    return [
        tag for tag, patterns in TAG_PATTERNS.items()
        if any(re.search(p, body) for p in patterns)
    ]


def export_call_graph(
    graph: CallGraph,
    method_infos: Dict[str, MethodInfo],
    ui_entries: List[UIEntryPoint],
    project_dir: str,
    timestamp: str,
) -> dict:
    ui_sigs: Set[str] = {e.method_signature for e in ui_entries}

    nodes = []
    for sig in graph.get_all_nodes():
        method = method_infos.get(sig)
        meta = graph.get_node_metadata(sig) or {}
        nodes.append({
            "signature": sig,
            "metadata": {
                "class_fqn": method.class_name if method else meta.get("class_fqn", ""),
                "source_file": method.source_file if method else meta.get("source_file", "<external>"),
                "line_range": list(method_line_range(method)) if method else [0, 0],
                "is_ui_entry": sig in ui_sigs,
                "tags": _extract_tags(method.body) if method else [],
                "annotations": method.annotations if method else [],
            },
        })

    edges = []
    for caller, callee, info in graph.get_all_edges():
        edges.append({
            "caller": caller,
            "callee": callee,
            "call_site": {
                "line_number": info.get("line", 0),
                "type": info.get("type", "DIRECT"),
            },
        })

    classes: Set[str] = {m.class_name for m in method_infos.values()}

    return {
        "project": project_dir,
        "analysis_timestamp": timestamp,
        "algorithm": "CHA",
        "stats": {
            "total_nodes": graph.node_count(),
            "total_edges": graph.edge_count(),
            "total_classes": len(classes),
            "total_ui_entry_points": len(ui_sigs),
        },
        "nodes": nodes,
        "edges": edges,
    }


def export_ui_entry_points(
    ui_entries: List[UIEntryPoint],
    project_dir: str,
    timestamp: str,
) -> dict:
    return {
        "project": project_dir,
        "analysis_timestamp": timestamp,
        "total_entry_points": len(ui_entries),
        "entry_points": [
            {
                "method_signature": e.method_signature,
                "class_fqn": e.class_fqn,
                "category": e.category,
                "confidence": e.confidence,
                "source_file": e.source_file,
                "line_number": e.line_number,
                "details": e.details,
            }
            for e in ui_entries
        ],
    }


def write_outputs(
    output_dir: str,
    graph_data: dict,
    ui_data: dict,
) -> tuple:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    graph_file = out / "call_graph.json"
    ui_file = out / "ui_entry_points.json"
    graph_file.write_text(json.dumps(graph_data, indent=2, ensure_ascii=False), encoding="utf-8")
    ui_file.write_text(json.dumps(ui_data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(ui_file), str(graph_file)


def method_line_range(method: MethodInfo):
    end = method.line + method.body.count("\n") + 1
    return method.line, end
