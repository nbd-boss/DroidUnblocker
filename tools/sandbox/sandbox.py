"""
Android 沙箱执行器 — Tool 5

在模拟器/真机上运行 TestCaseGenerator 生成的测试代码，收集运行时性能数据。
流程：编译-修复-重跑闭环（最多 3 轮）→ adb logcat -c → gradlew connectedAndroidTest → 解析 logcat。
为 Reflection 阶段提供"地面真相"。
"""
import logging
import re
import subprocess
from pathlib import Path

from core.types import ToolResult
from tools.registry import BaseTool
from tools.sandbox import compile_fixer

logger = logging.getLogger(__name__)

_ELAPSED_RE = re.compile(r'DroidUnblocker: elapsed=(\d+)ms')
_STRICTMODE_RE = re.compile(
    r'StrictMode policy violation[^\n]*(DiskRead|NetworkOnMain|DiskWrite|'
    r'DiskReadViolation|DiskWriteViolation)[^\n]*',
    re.IGNORECASE,
)

# 默认 Android 测试项目路径，可通过环境变量覆盖
_TEST_PROJECT_DIR = Path("E:/UI_Skill/agent/test-project")
_TEST_FILE_REL = "app/src/androidTest/java/com/droidunblocker/test/UIBlockingTest.kt"
_GRADLEW = "gradlew.bat"
_ADB = "adb"
_MAX_COMPILE_ATTEMPTS = 3


class SandboxExecutorTool(BaseTool):
    def __init__(self, llm_client=None) -> None:
        self._llm = llm_client

    @property
    def name(self) -> str:
        return "SandboxExecutor"

    def execute(self, params: dict) -> ToolResult:
        test_code: str = params.get("test_code", "")
        target_method: str = params.get("target_method", "")
        if not test_code:
            return ToolResult(success=False, error="test_code is required")

        if "Could not generate test body" in test_code:
            return ToolResult(
                success=True,
                data=self._empty_result("Skipped: test body generation failed"),
            )

        try:
            from tools.soot_analyzer import get_index
            return self._run(test_code, index=get_index(), target_method=target_method)
        except FileNotFoundError as e:
            logger.warning(f"ADB or Gradle not found: {e}")
            return ToolResult(
                success=True,
                data=self._empty_result(f"Sandbox unavailable: {e}"),
            )
        except Exception as e:
            logger.error(f"SandboxExecutor error: {e}")
            return ToolResult(
                success=True,
                data=self._empty_result(f"Sandbox execution failed: {e}"),
            )

    # ── 执行流程 ───────────────────────────────

    def _run(self, test_code: str, index, target_method: str = "") -> ToolResult:
        test_file = _TEST_PROJECT_DIR / _TEST_FILE_REL
        test_file.parent.mkdir(parents=True, exist_ok=True)

        # 编译-修复-重跑闭环
        current_code = test_code
        for attempt in range(_MAX_COMPILE_ATTEMPTS):
            test_file.write_text(current_code, encoding="utf-8")
            compile_proc = subprocess.run(
                [str(_TEST_PROJECT_DIR / _GRADLEW), "assembleDebugAndroidTest"],
                cwd=str(_TEST_PROJECT_DIR),
                capture_output=True,
                text=True,
                timeout=180,
            )
            if compile_proc.returncode == 0:
                logger.info(f"Compilation succeeded on attempt {attempt + 1}")
                break
            error_output = compile_proc.stdout + compile_proc.stderr
            logger.info(f"Compile attempt {attempt + 1} failed, invoking LLM repair...")
            logger.info(f"Errors: {error_output[-1000:]}")
            fixed = compile_fixer.fix(current_code, error_output, index, self._llm)
            if fixed == current_code:
                logger.warning("CompileFixer made no changes, stopping repair loop")
                break
            current_code = fixed
        else:
            logger.warning("Max compile attempts reached, proceeding with last code")
            test_file.write_text(current_code, encoding="utf-8")

        # 将修复后的代码回写到 testcase 目录
        if target_method:
            self._save_testcase(current_code, target_method)

        # 清空 logcat 缓冲
        subprocess.run([_ADB, "logcat", "-c"], capture_output=True, timeout=15)

        # 运行 Gradle instrumented test
        gradle_proc = subprocess.run(
            [str(_TEST_PROJECT_DIR / _GRADLEW), "connectedAndroidTest"],
            cwd=str(_TEST_PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=300,
        )
        logger.info(f"Gradle exit code: {gradle_proc.returncode}")
        if gradle_proc.returncode != 0:
            logger.info(f"Gradle stdout: {gradle_proc.stdout[-2000:]}")
            logger.info(f"Gradle stderr: {gradle_proc.stderr[-2000:]}")

        # 抓取 logcat
        logcat_proc = subprocess.run(
            [_ADB, "logcat", "-d", "-v", "threadtime"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return ToolResult(success=True, data=self._parse(logcat_proc.stdout))

    # ── testcase 保存 ──────────────────────────

    @staticmethod
    def _save_testcase(test_code: str, target_method: str) -> None:
        import re
        testcase_dir = Path(__file__).parent.parent.parent / "testcase"
        testcase_dir.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r'[\\/:*?"<>|]', "_", target_method) + ".kt"
        path = testcase_dir / filename
        path.write_text(test_code, encoding="utf-8")
        logger.info(f"Testcase updated with repaired code: {path}")

    # ── logcat 解析 ────────────────────────────

    @staticmethod
    def _parse(logcat: str) -> dict:
        # blocking_time_ms
        elapsed_m = _ELAPSED_RE.search(logcat)
        blocking_time_ms = int(elapsed_m.group(1)) if elapsed_m else -1

        # StrictMode violations
        violations = [m.group()[:200] for m in _STRICTMODE_RE.finditer(logcat)]
        has_violations = bool(violations)
        systrace = logcat[:2000]

        if has_violations:
            types = ", ".join(sorted({m.group(1) for m in _STRICTMODE_RE.finditer(logcat)}))
            summary = f"StrictMode violations: {types} | Blocking time: {blocking_time_ms}ms"
        elif blocking_time_ms > 0:
            summary = f"Blocking time: {blocking_time_ms}ms (no StrictMode violation)"
        else:
            summary = "No blocking detected"

        return {
            "strict_mode_violations": violations,
            "has_violations": has_violations,
            "blocking_time_ms": blocking_time_ms,
            "systrace": systrace,
            "summary": summary,
        }

    @staticmethod
    def _empty_result(reason: str) -> dict:
        return {
            "strict_mode_violations": [],
            "has_violations": False,
            "blocking_time_ms": -1,
            "systrace": "",
            "summary": reason,
        }
