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

    @property
    def skill_metadata(self) -> dict:
        return {
            "name": "KnowledgeQuery",
            "description": (
                "查询 UI 线程阻塞模式知识库。当你对某个方法的阻塞性质无法肯定时使用。\n"
                "两步交互：\n"
                "  1. action=list  → 获取知识库目录，了解有哪些已知阻塞模式\n"
                "  2. action=get   → 获取指定模式的完整描述（特征、典型 API、检测启发式、StrictMode 可否检测）\n"
                "先 list 浏览目录，再根据当前分析场景选择最相关的条目 get。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get"],
                        "description": "list=获取目录，get=获取具体条目",
                    },
                    "id": {
                        "type": "string",
                        "description": "条目 ID，action=get 时必填，如 CPU_INTENSIVE",
                    },
                },
                "required": ["action"],
            },
            "returns": (
                "action=list: { \"entries\": [{\"id\": \"...\", \"summary\": \"...\"},...] }\n"
                "action=get:  完整条目（description, typical_apis, detection_keywords, "
                "severity, strictmode_detectable）"
            ),
            "usage_hints": [
                "当对方法的阻塞性质无法肯定时调用，不必每次分析都查询。",
                "先 list 获取目录，再根据当前代码特征选择最匹配的条目 get。",
                "strictmode_detectable=false 的模式在沙箱阶段只能靠 elapsed > 300ms 判定，"
                "CONCLUDE 时需在 root_cause 中注明。",
            ],
        }

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
