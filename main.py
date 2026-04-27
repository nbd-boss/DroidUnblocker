"""
DroidUnblocker — 主入口

两阶段流程：
  Phase 1: Exploration — ReAct Loop，LLM 驱动的调用图树搜索
  Phase 2: Reflection  — 生成测试用例，沙箱执行，动态验证静态结论

用法:
    cd E:/UI_Skill/agent
    python main.py path/to/android/project/src
    python main.py path/to/android/project/src --model gpt-4o --max-entries 5
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from core.types import AnalysisConclusion, VerificationStatus
from core.react_loop import ReActLoop
from tools.registry import ToolRegistry
from tools.soot_analyzer import SootAnalyzerTool, get_index
from tools.call_chain_expander import CallChainExpanderTool
from tools.program_slicer import ProgramSlicerTool
from tools.test_generator import TestCaseGeneratorTool
from tools.sandbox import SandboxExecutorTool
from tools.knowledge_query import KnowledgeQueryTool
from llm.client import LLMClient
from output.report import build_report, save_report, save_history

_CORE_PROMPT_DIR = Path(__file__).parent / "core" / "prompt"
_REFLECT_SYSTEM = (_CORE_PROMPT_DIR / "reflect_system.md").read_text(encoding="utf-8")
_REFLECT_USER_TEMPLATE = (_CORE_PROMPT_DIR / "reflect_user.md").read_text(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 组装 Registry
# ──────────────────────────────────────────────

def _build_base_registry() -> ToolRegistry:
    """构建不依赖 LLM 的工具集（供获取 tools_description 用）。"""
    registry = ToolRegistry()
    registry.register(SootAnalyzerTool())
    registry.register(CallChainExpanderTool())
    registry.register(ProgramSlicerTool())
    registry.register(KnowledgeQueryTool())
    registry.register(SandboxExecutorTool())
    return registry


def _build_full_registry(llm_client: LLMClient) -> ToolRegistry:
    """构建完整工具集（含需要 LLM 的工具）。"""
    registry = ToolRegistry()
    registry.register(SootAnalyzerTool())
    registry.register(CallChainExpanderTool())
    registry.register(ProgramSlicerTool())
    registry.register(KnowledgeQueryTool())
    registry.register(SandboxExecutorTool(llm_client=llm_client))
    registry.register(TestCaseGeneratorTool(llm_client=llm_client))
    return registry


# ──────────────────────────────────────────────
# Phase 1: Exploration
# ──────────────────────────────────────────────

def phase1_exploration(
    project_dir: str,
    registry: ToolRegistry,
    llm_client: LLMClient,
    max_entries: int,
    tools_description: str = "",
    output_dir: str = "result",
) -> Dict[str, List[AnalysisConclusion]]:
    logger.info("=== Phase 1: Exploration ===")

    # Step 1 — 识别 UI 线程入口
    result = registry.execute("SootStaticAnalyzer", {"project_dir": project_dir})
    if not result.success:
        logger.error(f"SootStaticAnalyzer failed: {result.error}")
        return {}

    total = result.data.get("total_methods", 0)
    index = get_index()
    entry_methods = [
        {
            "signature": m.signature,
            "file": m.file,
            "line": m.line,
            "class_name": m.class_name,
            "method_name": m.method_name,
        }
        for m in (index.ui_entry_methods if index else [])
    ]
    logger.info(f"Found {len(entry_methods)} UI thread entries ({total} total methods)")

    if not entry_methods:
        logger.warning("No UI thread entry methods found. Check project_dir.")
        return {}

    # Step 2 — 对每个入口运行 ReAct 循环
    react = ReActLoop(registry=registry, llm_client=llm_client, tools_description=tools_description)
    grouped: Dict[str, List[AnalysisConclusion]] = {}

    for i, entry_info in enumerate(entry_methods[:max_entries]):
        sig = entry_info["signature"]
        logger.info(f"  [{i + 1}/{min(len(entry_methods), max_entries)}] Exploring: {sig}")
        entry_conclusions, memory = react.run(entry_method=sig)
        save_history(sig, memory, entry_conclusions, output_dir)
        grouped[sig] = entry_conclusions
        if entry_conclusions:
            for conclusion in entry_conclusions:
                if conclusion.verdict == "CLEAN":
                    logger.info(f"  → CLEAN (no blocking found)")
                else:
                    logger.info(
                        f"  → [{conclusion.blocking_pattern}] {conclusion.root_cause} "
                        f"({conclusion.confidence.value})"
                    )
        else:
            logger.info(f"  → No conclusion reached (max iterations)")

    return grouped


# ──────────────────────────────────────────────
# Phase 2: Reflection
# ──────────────────────────────────────────────

def phase2_reflection(
    grouped: Dict[str, List[AnalysisConclusion]],
    registry: ToolRegistry,
    llm_client: LLMClient,
) -> Dict[str, List[dict]]:
    logger.info("=== Phase 2: Reflection ===")
    reports: Dict[str, List[dict]] = {}

    for entry_method, conclusions in grouped.items():
        entry_reports: List[dict] = []
        for conclusion in conclusions:
            if conclusion.verdict != "BLOCKED":
                continue
            logger.info(f"Verifying: {conclusion.root_cause} (entry: {entry_method})")

            # 生成测试用例
            gen_result = registry.execute("TestCaseGenerator", {
                "call_chain": conclusion.call_chain,
                "root_cause": conclusion.root_cause,
            })
            if not gen_result.success:
                logger.warning(f"Test generation failed: {gen_result.error}")
                entry_reports.append(build_report(conclusion, VerificationStatus.PENDING))
                continue

            test_code = gen_result.data.get("test_code", "")
            target_method = gen_result.data.get("target_method", "")

            # 沙箱执行
            sandbox_result = registry.execute("SandboxExecutor", {
                "test_code": test_code,
                "target_method": target_method,
            })
            if not sandbox_result.success:
                logger.warning(f"Sandbox execution failed: {sandbox_result.error}")
                entry_reports.append(build_report(conclusion, VerificationStatus.PENDING))
                continue

            sandbox_data = sandbox_result.data
            sandbox_summary = sandbox_data.get("summary", "")
            blocking_time_ms = sandbox_data.get("blocking_time_ms", -1)
            has_violations = sandbox_data.get("has_violations", False)

            # LLM Reflection
            verification_status, blocking_time_ms = _llm_reflect(
                llm_client, conclusion, sandbox_summary, blocking_time_ms, has_violations
            )

            logger.info(f"  Verification: {verification_status.value} ({blocking_time_ms}ms)")
            entry_reports.append(
                build_report(
                    conclusion,
                    verification_status,
                    blocking_time_ms=blocking_time_ms,
                    evidence_dynamic=sandbox_summary,
                )
            )

        if entry_reports:
            reports[entry_method] = entry_reports

    return reports


def _llm_reflect(
    llm_client: LLMClient,
    conclusion: AnalysisConclusion,
    sandbox_summary: str,
    blocking_time_ms: int,
    has_violations: bool,
) -> tuple:
    conclusion_summary = (
        f"Blocking method: {conclusion.call_chain[-1] if conclusion.call_chain else ''}\n"
        f"Root cause: {conclusion.root_cause}\n"
        f"Pattern: {conclusion.blocking_pattern}\n"
        f"Call chain: {' → '.join(conclusion.call_chain)}"
    )
    try:
        user_prompt = _REFLECT_USER_TEMPLATE.replace(
            "{conclusion_summary}", conclusion_summary
        ).replace("{sandbox_summary}", sandbox_summary)
        raw = llm_client.complete(
            system=_REFLECT_SYSTEM,
            user=user_prompt,
            response_format={"type": "json_object"},
        )
        data = json.loads(raw)
        verification = VerificationStatus(data.get("verification", "PENDING"))
        if data.get("blocking_time_ms", -1) > 0:
            blocking_time_ms = data["blocking_time_ms"]
        if data.get("adjusted_root_cause"):
            conclusion.root_cause = data["adjusted_root_cause"]
        return verification, blocking_time_ms
    except Exception as exc:
        logger.warning(f"LLM reflection failed, using heuristic: {exc}")
        # 降级：按 sandbox 数据启发式判断
        if has_violations or blocking_time_ms > 300:
            return VerificationStatus.CONFIRMED, blocking_time_ms
        if blocking_time_ms > 0:
            return VerificationStatus.PARTIAL, blocking_time_ms
        return VerificationStatus.REFUTED, blocking_time_ms


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DroidUnblocker — Android UI Thread Blocking Analyzer"
    )
    parser.add_argument("project_dir", help="Android 项目源码目录路径")
    parser.add_argument("--model", default="qwen3.5-plus", help="OpenAI 模型（默认 qwen3.5-plus）")
    parser.add_argument("--max-entries", type=int, default=10000, help="最多分析的 UI 入口数量（默认 10000）")
    parser.add_argument("--output-dir", default="result", help="结果输出目录（默认 result/）")
    parser.add_argument("--api-key", default=None, help="OpenAI API Key（也可通过 OPENAI_API_KEY 环境变量设置）")
    args = parser.parse_args()

    if not Path(args.project_dir).is_dir():
        print(f"Error: {args.project_dir!r} is not a directory", file=sys.stderr)
        sys.exit(1)

    import llm.config as llm_config
    if args.api_key:
        llm_config.API_KEY = args.api_key
    elif os.environ.get("OPENAI_API_KEY"):
        llm_config.API_KEY = os.environ["OPENAI_API_KEY"]
    if args.model:
        llm_config.MODEL = args.model

    if not llm_config.API_KEY or llm_config.API_KEY == "your-api-key-here":
        logger.warning("API key not configured — edit llm/config.py or set OPENAI_API_KEY.")

    # 1. 构建 base registry（无 LLM）→ 获取 tools_description
    base_registry = _build_base_registry()
    tools_description = base_registry.get_tools_prompt()

    # 2. 创建 LLM 客户端（key / base_url / model 均从 llm/config.py 读取）
    llm_client = LLMClient()

    # 4. 完整 registry（含 TestCaseGeneratorTool）
    registry = _build_full_registry(llm_client)

    output_dir = args.output_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    report_path = str(Path(output_dir) / "root_cause_report.json")

    print(f"\n{'=' * 60}")
    print(f" DroidUnblocker")
    print(f" Project : {args.project_dir}")
    print(f" Model   : {llm_config.MODEL}")
    print(f" Entries : up to {args.max_entries}")
    print(f" Output  : {Path(output_dir).absolute()}")
    print(f"{'=' * 60}\n")

    # Phase 1
    grouped = phase1_exploration(
        project_dir=args.project_dir,
        registry=registry,
        llm_client=llm_client,
        max_entries=args.max_entries,
        tools_description=tools_description,
        output_dir=output_dir,
    )

    all_conclusions = [c for cs in grouped.values() for c in cs]
    blocked = [c for c in all_conclusions if c.verdict == "BLOCKED"]
    clean = [c for c in all_conclusions if c.verdict == "CLEAN"]
    logger.info(f"Phase 1 summary: {len(blocked)} BLOCKED, {len(clean)} CLEAN, "
                f"{len(all_conclusions) - len(blocked) - len(clean)} no conclusion")

    if not blocked:
        print("\nNo blocking patterns found.")
        save_report({}, report_path)
        return

    print(f"\nFound {len(blocked)} potential blocking pattern(s). Verifying...\n")

    # Phase 2 — 只对 BLOCKED 结论执行，按入口分组
    blocked_grouped = {
        sig: [c for c in cs if c.verdict == "BLOCKED"]
        for sig, cs in grouped.items()
        if any(c.verdict == "BLOCKED" for c in cs)
    }
    reports = phase2_reflection(
        grouped=blocked_grouped,
        registry=registry,
        llm_client=llm_client,
    )

    save_report(reports, report_path)

    print(f"\n{'=' * 60}")
    print(f" Token Usage")
    print(f" Prompt     : {llm_client.prompt_tokens:,}")
    print(f" Completion : {llm_client.completion_tokens:,}")
    print(f" Total      : {llm_client.total_tokens:,}")
    print(f"{'=' * 60}")
if __name__ == "__main__":
    main()
