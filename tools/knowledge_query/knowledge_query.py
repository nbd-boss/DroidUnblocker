"""
知识库查询工具 — KnowledgeQueryTool (Tool 6)

两步交互：
  action=list → 返回知识库目录（metadata），让 agent 选择条目
  action=get  → 返回指定条目的完整描述
"""
import logging

from core.types import ToolResult
from tools.registry import BaseTool
from tools.knowledge_query.blocking_patterns import ENTRIES, METADATA

logger = logging.getLogger(__name__)


class KnowledgeQueryTool(BaseTool):

    @property
    def name(self) -> str:
        return "KnowledgeQuery"

    def execute(self, params: dict) -> ToolResult:
        action = params.get("action", "")

        if action == "list":
            return ToolResult(success=True, data={"entries": METADATA})

        if action == "get":
            entry_id = params.get("id", "").upper()
            entry = ENTRIES.get(entry_id)
            if entry is None:
                return ToolResult(
                    success=False,
                    error=(
                        f"Unknown pattern id: '{entry_id}'. "
                        f"Available: {', '.join(ENTRIES.keys())}"
                    ),
                )
            return ToolResult(success=True, data=entry)

        return ToolResult(
            success=False,
            error=f"Unknown action: '{action}'. Use 'list' or 'get'.",
        )
