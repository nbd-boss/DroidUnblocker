import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.types import ToolResult
from tools.registry import BaseTool
from tools.soot_analyzer.src.analysis import call_graph_builder, callback_resolver, ui_entry_detector
from tools.soot_analyzer.src.analysis.class_hierarchy import ClassHierarchy
from tools.soot_analyzer.src.analysis.ui_entry_detector import UIEntryPoint
from tools.soot_analyzer.src.graph.call_graph import CallGraph
from tools.soot_analyzer.src.graph import graph_exporter
from tools.soot_analyzer.src.parser import java_parser, kotlin_parser, project_scanner, xml_parser
from tools.soot_analyzer.src.parser.java_parser import ClassInfo, MethodInfo

logger = logging.getLogger(__name__)

# src/main.py → parent = src/ → parent = soot_analyzer/ → parent = tools/ → parent = agent root
_AGENT_DIR = Path(__file__).parent.parent.parent.parent
_CONFIG_DIR = Path(__file__).parent.parent / "config"


# ── 分析流水线内部模型 ────────────────────────────────────────────────────────

@dataclass
class AnalysisResult:
    method_infos: Dict[str, MethodInfo] = field(default_factory=dict)
    call_graph: Optional[CallGraph] = None
    ui_entry_points: List[UIEntryPoint] = field(default_factory=list)
    class_hierarchy: Optional[ClassHierarchy] = None
    project_dir: str = ""
    timestamp: str = ""


# ── 向后兼容数据模型（供 CallChainExpander / ProgramSlicer 使用）─────────────

@dataclass
class MethodRecord:
    signature: str
    class_name: str
    method_name: str
    file: str
    line: int
    body: str = ""
    callees: List[str] = field(default_factory=list)
    is_ui_entry: bool = False
    detection_method: str = "rule"
    annotations: List[str] = field(default_factory=list)

    def line_range(self) -> Tuple[int, int]:
        return (self.line, self.line + self.body.count("\n") + 1)


class SourceCodeIndex:
    def __init__(self) -> None:
        self.methods: Dict[str, MethodRecord] = {}
        self.ui_entry_methods: List[MethodRecord] = []

    def get_method(self, signature: str) -> Optional[MethodRecord]:
        if signature in self.methods:
            return self.methods[signature]
        for sig, record in self.methods.items():
            if sig.endswith("." + signature) or signature in sig:
                return record
        return None

    def get_direct_callees(self, signature: str) -> List[MethodRecord]:
        record = self.get_method(signature)
        if not record:
            return []
        result = []
        for callee_sig in record.callees:
            callee = self.get_method(callee_sig)
            if callee:
                result.append(callee)
            else:
                parts = callee_sig.rsplit(".", 1)
                result.append(MethodRecord(
                    signature=callee_sig,
                    class_name=parts[0] if len(parts) > 1 else "External",
                    method_name=parts[-1],
                    file="<external>",
                    line=0,
                ))
        return result


# ── 模块级单例 ────────────────────────────────────────────────────────────────

_INDEX: Optional[SourceCodeIndex] = None


def get_index() -> Optional[SourceCodeIndex]:
    return _INDEX


def build_index(project_dir: str, llm_client=None) -> SourceCodeIndex:
    global _INDEX
    skill = AndroidUIAnalysisSkill(llm_client=llm_client)
    result = skill.analyze(project_dir)
    _INDEX = _build_source_code_index(result)
    logger.info(
        f"Index built: {len(_INDEX.methods)} methods, "
        f"{len(_INDEX.ui_entry_methods)} UI entries"
    )
    return _INDEX


def _build_source_code_index(result: AnalysisResult) -> SourceCodeIndex:
    index = SourceCodeIndex()
    ui_sigs = {e.method_signature for e in result.ui_entry_points}
    for sig, method_info in result.method_infos.items():
        callees = [site.callee_name for site in method_info.call_sites]
        record = MethodRecord(
            signature=sig,
            class_name=method_info.class_name,
            method_name=method_info.method_name,
            file=method_info.source_file,
            line=method_info.line,
            body=method_info.body,
            callees=callees,
            is_ui_entry=sig in ui_sigs,
            detection_method="rule",
            annotations=method_info.annotations,
        )
        index.methods[sig] = record
        if sig in ui_sigs:
            index.ui_entry_methods.append(record)
    return index


# ── 分析流水线 ────────────────────────────────────────────────────────────────

def _load_framework_model() -> dict:
    try:
        import yaml  # type: ignore
        model_file = _CONFIG_DIR / "android_framework_model.yaml"
        if model_file.exists():
            with open(model_file, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


class AndroidUIAnalysisSkill:
    def __init__(self, llm_client=None) -> None:
        self._llm = llm_client

    def analyze(self, project_dir: str) -> AnalysisResult:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = AnalysisResult(project_dir=project_dir, timestamp=timestamp)

        # ── 步骤 1：扫描文件 ──────────────────────────────────────────────────
        proj_index = project_scanner.scan(project_dir)
        logger.info(
            f"Scanned: {len(proj_index.java_files)} Java, "
            f"{len(proj_index.kotlin_files)} Kotlin, "
            f"{len(proj_index.manifests)} manifests"
        )

        # ── 步骤 2：AST 解析 ──────────────────────────────────────────────────
        all_classes: List[ClassInfo] = []

        for filepath in proj_index.java_files:
            try:
                all_classes.extend(java_parser.parse_java_file(filepath))
            except Exception as e:
                logger.debug(f"Skip {filepath}: {e}")

        for filepath in proj_index.kotlin_files:
            try:
                all_classes.extend(kotlin_parser.parse_kotlin_file(filepath))
            except Exception as e:
                logger.debug(f"Skip {filepath}: {e}")

        method_infos: Dict[str, MethodInfo] = {}
        for cls in all_classes:
            for method in cls.methods:
                method_infos.setdefault(method.signature, method)

        result.method_infos = method_infos
        logger.info(f"Parsed: {len(all_classes)} classes, {len(method_infos)} methods")

        # 解析 XML
        xml_bindings = []
        for manifest_path in proj_index.manifests:
            try:
                xml_parser.parse_manifest(manifest_path)
            except Exception as e:
                logger.debug(f"Skip manifest {manifest_path}: {e}")

        for layout_path in proj_index.layout_xmls:
            try:
                xml_bindings.extend(xml_parser.parse_layout(layout_path))
            except Exception as e:
                logger.debug(f"Skip layout {layout_path}: {e}")

        # ── 步骤 3：构建类层次结构 ─────────────────────────────────────────────
        hierarchy = ClassHierarchy()
        hierarchy.build(all_classes, _load_framework_model())
        result.class_hierarchy = hierarchy

        # ── 步骤 4：解析回调注册 ──────────────────────────────────────────────
        cb_edges = callback_resolver.resolve(method_infos)

        # ── 步骤 5：构建调用图 ────────────────────────────────────────────────
        graph = call_graph_builder.build(method_infos, cb_edges)
        result.call_graph = graph
        logger.info(f"Call graph: {graph.node_count()} nodes, {graph.edge_count()} edges")

        # ── 步骤 6：检测 UI 入口 ──────────────────────────────────────────────
        ui_entries = ui_entry_detector.detect(
            method_infos, hierarchy, xml_bindings, self._llm
        )
        result.ui_entry_points = ui_entries
        logger.info(f"UI entry points: {len(ui_entries)}")

        return result

    def export(self, result: AnalysisResult, output_dir: str):
        graph_data = graph_exporter.export_call_graph(
            result.call_graph,
            result.method_infos,
            result.ui_entry_points,
            result.project_dir,
            result.timestamp,
        )
        ui_data = graph_exporter.export_ui_entry_points(
            result.ui_entry_points,
            result.project_dir,
            result.timestamp,
        )
        return graph_exporter.write_outputs(output_dir, graph_data, ui_data)


# ── Tool 实现 ─────────────────────────────────────────────────────────────────

class SootAnalyzerTool(BaseTool):
    def __init__(self, llm_client=None) -> None:
        self._llm = llm_client

    @property
    def name(self) -> str:
        return "SootStaticAnalyzer"

    @property
    def skill_metadata(self) -> dict:
        return {
            "name": "SootStaticAnalyzer",
            "description": (
                "遍历 Android 项目源码目录，使用 AST（javalang）解析 Java 源文件，"
                "构建项目级函数调用图（FCG）并识别所有 UI 线程入口函数。"
                "结果写入 output_dir/ui_entry_points.json 和 output_dir/call_graph.json。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_dir": {
                        "type": "string",
                        "description": "Android 项目源码目录路径",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "分析结果输出目录（可选），默认为 <agent目录>/skill1_output/",
                    },
                },
                "required": ["project_dir"],
            },
            "returns": (
                '{"ui_entry_points_file": "<path>", "call_graph_file": "<path>", '
                '"total_entry_points": N, "total_methods": N, "total_edges": N}'
            ),
            "usage_hints": [
                "必须最先调用，整个 Agent 生命周期内只调用一次。",
                "若 total_entry_points 为 0，检查 project_dir 是否包含继承自已知 Android 基类的源文件。",
            ],
        }

    def execute(self, params: dict) -> ToolResult:
        project_dir = params.get("project_dir", "")
        if not project_dir:
            return ToolResult(success=False, error="project_dir is required")

        output_dir = params.get("output_dir") or str(_AGENT_DIR / "skill1_output")

        try:
            skill = AndroidUIAnalysisSkill(llm_client=self._llm)
            result = skill.analyze(project_dir)

            ui_file, graph_file = skill.export(result, output_dir)

            global _INDEX
            _INDEX = _build_source_code_index(result)

            return ToolResult(
                success=True,
                data={
                    "ui_entry_points_file": ui_file,
                    "call_graph_file": graph_file,
                    "total_entry_points": len(result.ui_entry_points),
                    "total_methods": len(result.method_infos),
                    "total_edges": result.call_graph.edge_count() if result.call_graph else 0,
                },
            )
        except Exception as e:
            logger.exception("SootAnalyzerTool failed")
            return ToolResult(success=False, error=str(e))
