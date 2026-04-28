import inspect
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict

import yaml

from core.types import ToolResult

logger = logging.getLogger(__name__)


def _parse_frontmatter(md_path: Path) -> dict:
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"No YAML frontmatter found in {md_path}")
    end = text.index("---", 3)
    return yaml.safe_load(text[3:end])


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def skill_metadata(self) -> dict:
        tool_file = Path(inspect.getfile(type(self)))
        for search_dir in [tool_file.parent, tool_file.parent.parent]:
            skill_md = search_dir / "skill.md"
            if skill_md.exists():
                return _parse_frontmatter(skill_md)
        raise NotImplementedError(f"skill.md not found for {type(self).__name__}")

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
            hints = m.get("usage_hints", [])
            hints_str = "\n  ".join(hints) if hints else ""
            parts.append(
                f"### {m['name']}\n"
                f"Description: {m['description']}\n"
                f"Parameters: {json.dumps(m['parameters'], ensure_ascii=False)}\n"
                f"Returns: {m.get('returns', '')}\n"
                f"Usage hints:\n  {hints_str}"
            )
        return "\n\n".join(parts)
