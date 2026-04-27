"""
测试体组装器 — TestBodyAssembler

将 DependencyInliner 的内联代码与 CallerContextCollector 收集的调用场景交给 LLM，
翻译为 Kotlin 测试方法体（8 格缩进，无外层框架代码）。
"""
import json
import logging
from pathlib import Path
from typing import List

from tools.test_generator.dependency_inliner import InlinedBlock
from tools.test_generator.caller_context_collector import CallerContext

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompt"
_SYSTEM_PROMPT = (_PROMPT_DIR / "system_prompt.md").read_text(encoding="utf-8").strip()
_USER_PROMPT_TEMPLATE = (_PROMPT_DIR / "user_prompt.md").read_text(encoding="utf-8")


class TestBodyAssembler:
    def __init__(self, llm_client) -> None:
        self._llm = llm_client

    def assemble(
        self,
        inlined: InlinedBlock,
        contexts: List[CallerContext],
        root_cause: str,
    ) -> str:
        if self._llm is None:
            return "        // Could not generate test body: LLM client not configured"

        user_prompt = self._build_user_prompt(inlined, contexts, root_cause)
        try:
            raw = self._llm.complete(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                response_format={"type": "json_object"},
            )
            data = json.loads(raw)
            return data.get("test_body", "        // Could not generate test body")
        except Exception as e:
            logger.error(f"TestBodyAssembler LLM error: {e}")
            return f"        // Could not generate test body: {e}"

    def _build_user_prompt(
        self,
        inlined: InlinedBlock,
        contexts: List[CallerContext],
        root_cause: str,
    ) -> str:
        mocked_callees_section = ""
        if inlined.mocked_callees:
            lines = ["Methods to mock (too deep to inline):"]
            for m in inlined.mocked_callees:
                lines.append(f"- {m}")
            mocked_callees_section = "\n".join(lines) + "\n"

        primary = [ctx for ctx in contexts if ctx.is_primary]
        background = [ctx for ctx in contexts if not ctx.is_primary]

        primary_contexts_section = ""
        if primary:
            lines = [
                "Primary caller contexts — from the current UI entry call chain "
                "(use these as the main basis for test input and pre-call state):"
            ]
            for i, ctx in enumerate(primary, 1):
                lines.append(f"Caller {i}: {ctx.caller_method}")
                lines.append("  pre_call_statements (entry → call site):")
                for line in ctx.pre_call_statements.split("\n"):
                    if line.strip():
                        lines.append(f"    {line.rstrip()}")
                if ctx.argument_expressions:
                    lines.append(f"  arguments: {', '.join(ctx.argument_expressions)}")
                lines.append("")
            primary_contexts_section = "\n".join(lines)

        background_contexts_section = ""
        if background:
            lines = [
                "Background caller contexts — other callers outside the current call chain "
                "(use only for understanding the function's semantic purpose, NOT as test input):"
            ]
            for i, ctx in enumerate(background, 1):
                lines.append(f"Caller {i}: {ctx.caller_method}")
                if ctx.argument_expressions:
                    lines.append(f"  arguments: {', '.join(ctx.argument_expressions)}")
                lines.append("")
            background_contexts_section = "\n".join(lines)

        if not primary and not background:
            primary_contexts_section = "No caller contexts found in project index."

        return _USER_PROMPT_TEMPLATE.format(
            root_cause=root_cause,
            inlined_code=inlined.inlined_code or "// (no body available)",
            mocked_callees_section=mocked_callees_section,
            primary_contexts_section=primary_contexts_section,
            background_contexts_section=background_contexts_section,
        )
