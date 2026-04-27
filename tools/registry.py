import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict

from core.types import ToolResult

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def skill_metadata(self) -> dict: ...

    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> ToolResult: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def execute(self, tool_name: str, params: Dict[str, Any]) -> ToolResult:
        if tool_name not in self._tools:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")
        try:
            return self._tools[tool_name].execute(params)

        except Exception as e:
            logger.exception(f"Tool {tool_name} raised an exception")
            return ToolResult(success=False, error=str(e))

    def get_tools_prompt(self) -> str:
        parts = []
        for tool in self._tools.values():
            m = tool.skill_metadata
            hints = "\n  ".join(m.get("usage_hints", []))
            parts.append(
                f"### {m['name']}\n"
                f"Description: {m['description']}\n"
                f"Parameters: {json.dumps(m['parameters'], ensure_ascii=False)}\n"
                f"Returns: {m['returns']}\n"
                f"Usage hints:\n  {hints}"
            )
        return "\n\n".join(parts)
