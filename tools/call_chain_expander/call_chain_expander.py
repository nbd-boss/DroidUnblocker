"""
调用链展开器 — Tool 2

支持两种模式：
  SHALLOW      — 单层 callee 摘要 + 规则 tag，零 LLM 开销
  FULL_EXPAND  — 两层 BFS 子树 + 方法体片段
"""
import logging
from typing import Dict, List, Optional

from core.types import CalleeInfo, FullExpandNode, ShallowSummary, ToolResult
from tools.registry import BaseTool
from tools.soot_analyzer import MethodRecord, get_index

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Tag 生成规则（纯字符串匹配，零 LLM）
# ──────────────────────────────────────────────
_TAG_RULES: List[tuple] = [
    ("I/O", [
        "java.io.", "java.nio.", "FileInputStream", "FileOutputStream",
        "BufferedReader", "BufferedWriter", "FileReader", "FileWriter",
        "File(", ".listFiles(", ".mkdirs(", "openFileOutput", "openFileInput",
    ]),
    ("DATABASE", [
        "android.database.", "SQLiteDatabase", "SQLiteOpenHelper",
        "rawQuery", "execSQL", ".query(", ".insert(", ".update(", ".delete(",
        "net.sqlcipher.", "getContentResolver", "ContentResolver",
        "DiskLruCache", "DiskCache",
    ]),
    ("NETWORK", [
        "java.net.", "okhttp3.", "retrofit2.", "HttpURLConnection",
        "OkHttpClient", "Retrofit", "URL(", "openConnection", "openStream",
        "HttpClient", "VolleyRequest",
    ]),
    ("THREADING", [
        "java.lang.Thread", "java.util.concurrent.", "Executors.",
        "ExecutorService", "Thread(", "AsyncTask", "HandlerThread",
        "kotlinx.coroutines.", "launch(", "async(", "withContext", "Dispatchers.",
    ]),
    ("SYNCHRONIZATION", [
        "synchronized", ".wait()", ".notify(", ".notifyAll(",
        "java.util.concurrent.locks.", "ReentrantLock", "CountDownLatch", "Semaphore",
    ]),
    ("HANDLER", [
        "android.os.Handler", "Handler(", ".post(", ".postDelayed(",
    ]),
]


def _generate_tags(body: str) -> List[str]:
    return [tag for tag, patterns in _TAG_RULES if any(p in body for p in patterns)]


def _complexity(has_io: bool, has_network: bool, has_database: bool, has_threading: bool, has_sync: bool = False) -> str:
    count = sum([has_io, has_network, has_database, has_threading, has_sync])
    if count == 0:
        return "low"
    if count == 1:
        return "medium"
    return "high"


# ──────────────────────────────────────────────
# SHALLOW 构建
# ──────────────────────────────────────────────
def _build_shallow(method_sig: str) -> Optional[ShallowSummary]:
    index = get_index()
    if index is None:
        return None

    direct = index.get_direct_callees(method_sig)
    callee_infos: List[CalleeInfo] = []
    all_tags: List[str] = []

    for callee in direct:
        tags = _generate_tags(callee.body)
        callee_infos.append(CalleeInfo(method=callee.signature, tags=tags))
        all_tags.extend(tags)

    has_io = "I/O" in all_tags
    has_threading = "THREADING" in all_tags
    has_network = "NETWORK" in all_tags
    has_database = "DATABASE" in all_tags
    has_sync = "SYNCHRONIZATION" in all_tags

    return ShallowSummary(
        method=method_sig,
        callees=callee_infos,
        has_io=has_io,
        has_threading=has_threading,
        has_network=has_network,
        has_database=has_database,
        has_synchronization=has_sync,
        estimated_complexity=_complexity(has_io, has_network, has_database, has_threading, has_sync),
    )


# ──────────────────────────────────────────────
# FULL_EXPAND 构建（方案 C：第一层完整，第二层仅签名+tags）
# ──────────────────────────────────────────────
def _build_full_expand(
    method_sig: str,
    remaining_depth: int = 2,
    explored: Optional[set] = None,
    method_cache: Optional[dict] = None,
) -> FullExpandNode:
    if explored is None:
        explored = set()
    if method_cache is None:
        method_cache = {}

    index = get_index()

    record: Optional[MethodRecord] = index.get_method(method_sig) if index else None
    if record is None:
        parts = method_sig.rsplit(".", 1)
        return FullExpandNode(
            signature=method_sig,
            class_name=parts[0] if len(parts) > 1 else "External",
            method_name=parts[-1],
            tags=[],
            body_excerpt="<external or not found>",
            callees=[],
            expandable=False,
        )

    tags = _generate_tags(record.body)

    # 第二层（remaining_depth == 0）：仅返回签名+tags，不读 body，标记 expandable
    if remaining_depth == 0:
        return FullExpandNode(
            signature=record.signature,
            class_name=record.class_name,
            method_name=record.method_name,
            tags=tags,
            body_excerpt="",
            callees=[],
            expandable=True,
        )

    # 第一层及根节点：返回完整 body_excerpt，写入 explored 和 method_cache
    body_excerpt = record.body[:600] if record.body else ""
    explored.add(record.signature)
    method_cache[record.signature] = {"body_excerpt": body_excerpt, "tags": tags}

    callee_nodes: List[FullExpandNode] = []
    for callee in index.get_direct_callees(method_sig):
        if callee.signature in explored:
            continue
        callee_nodes.append(_build_full_expand(callee.signature, remaining_depth - 1, explored, method_cache))

    return FullExpandNode(
        signature=record.signature,
        class_name=record.class_name,
        method_name=record.method_name,
        tags=tags,
        body_excerpt=body_excerpt,
        callees=callee_nodes,
        expandable=False,
    )


def _node_to_dict(node: FullExpandNode) -> dict:
    d = {
        "signature": node.signature,
        "class": node.class_name,
        "method": node.method_name,
        "tags": node.tags,
        "callees": [_node_to_dict(c) for c in node.callees],
    }
    if node.expandable:
        d["expandable"] = True
    else:
        d["body_excerpt"] = node.body_excerpt
    return d


# ──────────────────────────────────────────────
# Tool 实现
# ──────────────────────────────────────────────
class CallChainExpanderTool(BaseTool):
    @property
    def name(self) -> str:
        return "CallChainExpander"

    @property
    def skill_metadata(self) -> dict:
        return {
            "name": "CallChainExpander",
            "description": (
                "从指定方法出发，展开调用链信息。"
                "SHALLOW 模式：单层 callee 摘要 + 规则化风险标签（I/O/DATABASE/NETWORK/THREADING），标签纯规则生成，零 LLM 开销。"
                "FULL_EXPAND 模式：完整两层调用子树，含方法体摘要。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "方法签名，如 MainActivity.onCreate",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["SHALLOW", "FULL_EXPAND"],
                        "description": "SHALLOW（默认，廉价）或 FULL_EXPAND（完整子树）",
                        "default": "SHALLOW",
                    },
                },
                "required": ["method"],
            },
            "returns": (
                "SHALLOW: {method, callees:[{method, tags}], has_io, has_threading, "
                "has_network, has_database, has_synchronization, estimated_complexity} | "
                "FULL_EXPAND: {signature, class, method, tags, body_excerpt, callees:[...]}"
            ),
            "usage_hints": [
                "默认使用 SHALLOW 模式获取风险标签，再决定是否升级。",
                "仅当 SHALLOW 显示 I/O/DATABASE/NETWORK 且调用上下文在主线程时，才升级为 FULL_EXPAND。",
                "每次调用只展开一个方法的直接 callee（单层），通过多次迭代逐层向下。",
                "FULL_EXPAND 返回后对每个 callee 重新执行四级决策。",
            ],
        }

    def execute(self, params: dict) -> ToolResult:
        method = params.get("method", "")
        mode = params.get("mode", "SHALLOW")

        if not method:
            return ToolResult(success=False, error="method is required")

        index = get_index()
        if index is None:
            return ToolResult(
                success=False,
                error="Source index not initialized. Call SootStaticAnalyzer first.",
            )

        # found=True 表示方法在 index 中存在；found=False 表示方法不存在于项目代码库
        record = index.get_method(method)
        found = record is not None

        if mode == "SHALLOW":
            # self_tags：方法自身 body 的风险标签（callee 为空时仍可判断根因）
            self_tags = _generate_tags(record.body) if (record and record.body) else []

            summary = _build_shallow(method)
            if summary is None:
                return ToolResult(success=False, error="Index not available")

            return ToolResult(
                success=True,
                data={
                    "method": summary.method,
                    "found": found,
                    "self_tags": self_tags,
                    "callees": [{"method": c.method, "tags": c.tags} for c in summary.callees],
                    "has_io": summary.has_io,
                    "has_threading": summary.has_threading,
                    "has_network": summary.has_network,
                    "has_database": summary.has_database,
                    "has_synchronization": summary.has_synchronization,
                    "estimated_complexity": summary.estimated_complexity,
                },
            )

        if mode == "FULL_EXPAND":
            explored = params.get("explored", set())
            method_cache = params.get("method_cache", {})
            node = _build_full_expand(method, explored=explored, method_cache=method_cache)
            data = _node_to_dict(node)
            data["found"] = found
            return ToolResult(success=True, data=data)

        return ToolResult(success=False, error=f"Unknown mode: {mode}. Use SHALLOW or FULL_EXPAND.")
