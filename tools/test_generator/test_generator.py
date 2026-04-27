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

    @property
    def skill_metadata(self) -> dict:
        return {
            "name": "TestCaseGenerator",
            "description": (
                "根据探索阶段根因结论，生成 Android Instrumented Test（Kotlin）。"
                "内部由 DependencyInliner（递归内联项目内依赖）、"
                "CallerContextCollector（提取调用者前置语句序列）、"
                "TestBodyAssembler（LLM 翻译为 Kotlin）三个子模块协作完成。"
                "生成的测试在主线程直接调用目标方法，挂载 StrictMode 检测违规，"
                "并记录 wall-clock 耗时（DroidUnblocker timing tag）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "call_chain": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "从 UI 线程入口到阻塞方法的完整调用链（方法签名列表）。"
                            "DependencyInliner 以末端方法为起点向内展开依赖；"
                            "CallerContextCollector 以末端方法为目标向外收集调用者上下文。"
                        ),
                    },
                    "root_cause": {
                        "type": "string",
                        "description": "根因描述文字，引导 LLM 生成有针对性的测试逻辑",
                    },
                },
                "required": ["call_chain", "root_cause"],
            },
            "returns": (
                '{ "test_code": "<完整 Kotlin Instrumented Test 源文件内容>", '
                '"target_method": "<call_chain 最后一个方法签名>" }'
            ),
            "usage_hints": [
                "仅在探索阶段得出 CONCLUDE 决策后调用。",
                "专属于 Phase 2（Reflection 验证阶段），是 SandboxExecutor 的前置步骤。",
                "call_chain 直接取 CONCLUDE action 中的字段，无需手动构造。",
                "DependencyInliner 最大展开深度默认为 3；超出深度的依赖以 mock 替代。",
                "每次验证只调用一次，生成的测试代码会被 SandboxExecutor 覆盖写入同一测试文件。",
            ],
        }

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
