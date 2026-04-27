"""
ReAct 循环引擎

驱动 Thought → Action → Observation 循环，直到：
  - LLM 输出 CONCLUDE 决策
  - 达到最大探索深度
  - 达到最大迭代次数
"""
import json
import logging
import re
from pathlib import Path
from typing import List, Tuple

from core.memory import AgentMemory
from core.types import AnalysisConclusion, Confidence, ToolResult

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompt"
_EXPLORE_SYSTEM_TEMPLATE = (_PROMPT_DIR / "explore_system.md").read_text(encoding="utf-8")
_EXPLORE_USER_TEMPLATE = (_PROMPT_DIR / "explore_user.md").read_text(encoding="utf-8")


class ReActLoop:
    def __init__(
        self,
        registry,
        llm_client,
        tools_description: str = "",
        max_depth: int = 8,
        max_iterations: int = 40,
    ) -> None:
        self.registry = registry
        self.llm_client = llm_client
        self.max_depth = max_depth
        self.max_iterations = max_iterations
        self._explore_system = _EXPLORE_SYSTEM_TEMPLATE.replace(
            "{tools_description}", tools_description
        )

    def run(self, entry_method: str) -> Tuple[List[AnalysisConclusion], AgentMemory]:
        # AI 的短期记忆 = 走过的调用链 + 思考内容 + 工具调用结果 + 当前深度
        memory = AgentMemory()
        memory.entry_method = entry_method
        memory.push_call(entry_method)
        memory.add("system", f"Starting exploration. Entry: {entry_method}")

        conclusions: List[AnalysisConclusion] = []

        # 最多思考 max_iterations 轮
        for iteration in range(self.max_iterations):
            if memory.current_depth >= self.max_depth:
                logger.info(f"Max depth {self.max_depth} reached, stopping exploration.")
                break

            # ── Thought ───────────────────────────────
            context = memory.get_context(conclusions=conclusions)
            try:
                '''
                 entry_methods = [
                {
                    "signature": m.signature,
                    "file": m.file,
                    "line": m.line,
                    "class_name": m.class_name,
                    "method_name": m.method_name,
                }
                for m in index.ui_entry_methods
                ]
                '''
                user_prompt = _EXPLORE_USER_TEMPLATE.replace(
                    "{entry_method}", entry_method
                ).replace("{context}", context)
                raw_response = self.llm_client.complete(
                    system=self._explore_system,
                    user=user_prompt,
                    response_format={"type": "json_object"},
                )
            except Exception as exc:
                logger.warning(f"LLM explore failed at iteration {iteration}: {exc}")
                break

            parsed = self._parse_response(raw_response)
            thought = parsed.get("thought", "")
            action = parsed.get("action", {})
            if isinstance(action, str):
                action = {"type": action}

            memory.add_thought(thought)
            logger.debug(f"[iter {iteration}] thought: {thought[:120]}")

            action_type = action.get("type", "UNKNOWN")

            # ── Action: CONCLUDE ──────────────────────
            if action_type == "CONCLUDE":
                verdict = action.get("verdict", "BLOCKED").upper()
                call_chain = action.get("call_chain") or [entry_method]

                if verdict == "ALL_CLEAR":
                    memory.add_action(
                        f"CONCLUDE — ALL_CLEAR: {action.get('reason', 'All paths explored')}"
                    )
                    logger.info(f"ALL_CLEAR: all suspicious paths explored, stopping.")
                    return conclusions, memory

                if verdict == "CLEAN":
                    conclusion = AnalysisConclusion(
                        call_chain=call_chain,
                        root_cause="",
                        blocking_pattern="NONE",
                        confidence=Confidence.HIGH,
                        entry_method=entry_method,
                        verdict="CLEAN",
                        slice_evidence="",
                    )
                    memory.add_action(
                        f"CONCLUDE — CLEAN: {action.get('reason', 'No blocking pattern found')}"
                    )
                    logger.info(f"Conclusion reached: CLEAN (no blocking)")
                    conclusions.append(conclusion)
                    return conclusions, memory

                # BLOCKED: P5 — 必须至少成功分析过 1 个项目内方法才允许结案
                if memory.valid_explored_count == 0:
                    memory.add_observation(
                        "CONCLUDE rejected: no project method has been successfully analyzed yet "
                        "(valid_explored_count=0). You must expand at least one method that "
                        "exists in the codebase (found=true) before concluding. "
                        "Continue exploring or explain specifically why no further analysis is possible."
                    )
                    continue

                conclusion = AnalysisConclusion(
                    call_chain=call_chain,
                    root_cause=action.get("root_cause", "Unknown blocking operation"),
                    blocking_pattern=action.get("blocking_pattern", "OTHER"),
                    confidence=Confidence.HIGH,
                    entry_method=entry_method,
                    verdict="BLOCKED",
                    slice_evidence=action.get("evidence", ""),
                )
                memory.add_action(
                    f"CONCLUDE — {conclusion.blocking_pattern}: {conclusion.root_cause}"
                )
                logger.info(
                    f"Conclusion reached: [{conclusion.blocking_pattern}] {conclusion.root_cause}"
                )
                conclusions.append(conclusion)
                memory.add_observation(
                    f"Blocking point recorded: [{conclusion.blocking_pattern}] {conclusion.root_cause}. "
                    "Continue exploring other potential blocking paths in this entry method. "
                    "If no more blocking points exist, CONCLUDE CLEAN."
                )

            # ── Action: MOCK ──────────────────────────
            elif action_type == "MOCK":
                method = action.get("method", "")
                reason = action.get("reason", "")
                memory.mark_mocked(method)
                memory.add_action(f"MOCK {method} — {reason}")

            # ── Action: TOOL_CALL ─────────────────────
            elif action_type == "TOOL_CALL":
                tool_name = action.get("tool_name", "")
                tool_params = action.get("params", {})

                # P2: 硬拦截 SootStaticAnalyzer，禁止在 ReAct 循环内重复调用
                if tool_name == "SootStaticAnalyzer":
                    memory.add_action(f"TOOL_CALL {tool_name} [BLOCKED]")
                    memory.add_observation(
                        "ERROR: SootStaticAnalyzer is forbidden inside the ReAct loop. "
                        "Static analysis already ran in Phase 1. "
                        "Use CallChainExpander or ProgramSlicer to investigate methods."
                    )
                    continue

                # P4: CallChainExpander 去重
                # SHALLOW: key=method::SHALLOW，防止重复查询
                # FULL_EXPAND: 检查 memory.explored（body 是否已被看过），允许 SHALLOW→FULL_EXPAND 升级
                if tool_name == "CallChainExpander":
                    target_method = tool_params.get("method", "")
                    target_mode = tool_params.get("mode", "SHALLOW")
                    if target_method:
                        if target_mode == "SHALLOW" and memory.is_explored(f"{target_method}::SHALLOW"):
                            memory.add_action(f"TOOL_CALL {tool_name}({tool_params}) [SKIPPED - already explored]")
                            memory.add_observation(
                                f"'{target_method}' (SHALLOW) was already queried in this session. "
                                "MOCK this method or move to a different entry point."
                            )
                            continue
                        if target_mode == "FULL_EXPAND" and memory.is_body_explored(target_method):
                            memory.add_action(f"TOOL_CALL {tool_name}({tool_params}) [SKIPPED - body already explored]")
                            memory.add_observation(
                                f"'{target_method}' body was already expanded in this session. "
                                "MOCK this method or move to a different entry point."
                            )
                            continue

                # FULL_EXPAND 注入记忆化上下文
                if tool_name == "CallChainExpander" and tool_params.get("mode") == "FULL_EXPAND":
                    tool_params = dict(tool_params)
                    tool_params["explored"] = memory.explored
                    tool_params["method_cache"] = memory.method_cache

                memory.add_action(f"TOOL_CALL {tool_name}({tool_params})")
                result: ToolResult = self.registry.execute(tool_name, tool_params)

                if result.success:
                    obs = json.dumps(result.data, ensure_ascii=False, indent=2)
                    if len(obs) > 3000:
                        obs = obs[:3000] + "\n... (truncated)"
                    memory.add_observation(f"{tool_name} result:\n{obs}")

                    if tool_name == "CallChainExpander":
                        target_method = tool_params.get("method", "")
                        target_mode = tool_params.get("mode", "SHALLOW")
                        if target_method:
                            # P4 标记（SHALLOW 用 method::mode key）
                            if target_mode == "SHALLOW":
                                memory.mark_explored(f"{target_method}::SHALLOW")
                            # P5: 方法在 index 中存在才算有效探索
                            if result.data.get("found", True):
                                memory.valid_explored_count += 1

                    # FULL_EXPAND 时更新调用深度和调用栈
                    if (tool_name == "CallChainExpander"
                            and tool_params.get("mode") == "FULL_EXPAND"):
                        memory.current_depth += 1
                        memory.push_call(tool_params.get("method", ""))
                else:
                    memory.add_observation(f"{tool_name} error: {result.error}")

            else:
                logger.warning(f"Unknown action type: {action_type!r}")
                memory.add_observation(f"Unrecognised action type: {action_type!r}")

        if not conclusions:
            logger.info(f"No conclusion reached for {entry_method}")
        return conclusions, memory

    # ── JSON 解析（容错）──────────────────────────

    @staticmethod
    def _parse_response(response: str) -> dict:
        text = response.strip()
        # strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        # attempt direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # attempt to extract first {...} block
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse LLM response as JSON, using raw text as thought")
        return {
            "thought": response,
            "action": {"type": "UNKNOWN"},
        }
