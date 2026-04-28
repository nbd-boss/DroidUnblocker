"""
测试用例生成器 — Tool 4（主入口）

协调 DependencyInliner、CallerContextCollector、TestBodyAssembler 三个子模块，
将探索阶段的根因结论转化为可执行的 Android Instrumented Test（Kotlin）。
"""
import logging
import os
import re
from typing import List

from core.types import ToolResult
from tools.registry import BaseTool
from tools.soot_analyzer import get_index
from tools.test_generator.dependency_inliner import DependencyInliner, InlinedBlock
from tools.test_generator.caller_context_collector import CallerContextCollector
from tools.test_generator.test_body_assembler import TestBodyAssembler

logger = logging.getLogger(__name__)

_TEST_TEMPLATE = """\
package com.droidunblocker.test

import android.os.StrictMode
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class UIBlockingTest {{

    @Test
    fun testForUIThreadBlocking() {{
        StrictMode.setThreadPolicy(
            StrictMode.ThreadPolicy.Builder()
                .detectAll()
                .penaltyLog()
                .build()
        )

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val startTime = System.currentTimeMillis()

{test_body}

        val elapsed = System.currentTimeMillis() - startTime
        println("DroidUnblocker: elapsed=${{elapsed}}ms")
        assert(elapsed < 300) {{ "UI thread blocked for ${{elapsed}}ms" }}
    }}
}}
"""


class TestCaseGeneratorTool(BaseTool):
    def __init__(self, llm_client=None) -> None:
        self._llm = llm_client

    @property
    def name(self) -> str:
        return "TestCaseGenerator"

    def execute(self, params: dict) -> ToolResult:
        call_chain: List[str] = params.get("call_chain", [])
        root_cause: str = params.get("root_cause", "")

        if not call_chain:
            return ToolResult(success=False, error="call_chain is required")

        target_method = call_chain[-1]
        index = get_index()

        if index is not None:
            inlined = DependencyInliner(index).inline(target_method)
            contexts = CallerContextCollector(index).collect(target_method, call_chain=call_chain)
        else:
            logger.warning("SourceCodeIndex not available; falling back to LLM-only generation")
            inlined = InlinedBlock(
                target_method=target_method,
                inlined_code=f"// (index not available — target: {target_method})",
            )
            contexts = []

        test_body = TestBodyAssembler(self._llm).assemble(inlined, contexts, root_cause)
        test_code = _TEST_TEMPLATE.format(test_body=test_body)

        self._save_testcase(target_method, test_code)

        return ToolResult(
            success=True,
            data={"test_code": test_code, "target_method": target_method},
        )

    def _save_testcase(self, target_method: str, test_code: str) -> None:
        testcase_dir = os.path.join(os.path.dirname(__file__), "..", "..", "testcase")
        os.makedirs(testcase_dir, exist_ok=True)
        filename = re.sub(r'[\\/:*?"<>|]', "_", target_method) + ".kt"
        path = os.path.join(testcase_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(test_code)
        logger.info(f"Test case saved: {path}")
