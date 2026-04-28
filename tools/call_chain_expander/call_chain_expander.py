"""
调用链展开器 — Tool 2

统一 EXPAND 模式：返回目标方法的完整 body 摘要（≤600字符）+
所有 direct callee 的签名和风险标签。
对可疑 callee 再次发起 EXPAND 可获取其完整 body 及下一层 callee 信息。
已展开过的方法由 react_loop 自动拦截，无需重复调用。
"""
import logging
from typing import List, Optional

from core.types import ToolResult
from tools.registry import BaseTool
from tools.soot_analyzer import MethodRecord, get_index

logger = logging.getLogger(__name__)

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


def _expand(method_sig: str) -> dict:
    index = get_index()
    record: Optional[MethodRecord] = index.get_method(method_sig) if index else None

    if record is None:
        return {
            "method": method_sig,
            "found": False,
            "tags": [],
            "body": "",
            "callees": [],
        }

    body_excerpt = record.body[:600] if record.body else ""
    self_tags = _generate_tags(record.body) if record.body else []

    callees = []
    for callee in index.get_direct_callees(method_sig):
        callee_tags = _generate_tags(callee.body) if callee.body else []
        callee_in_project = index.get_method(callee.signature) is not None
        callees.append({
            "signature": callee.signature,
            "tags": callee_tags,
            "expandable": callee_in_project,
        })

    return {
        "method": method_sig,
        "found": True,
        "tags": self_tags,
        "body": body_excerpt,
        "callees": callees,
    }


class CallChainExpanderTool(BaseTool):
    @property
    def name(self) -> str:
        return "CallChainExpander"

    def execute(self, params: dict) -> ToolResult:
        method = params.get("method", "")

        if not method:
            return ToolResult(success=False, error="method is required")

        index = get_index()
        if index is None:
            return ToolResult(
                success=False,
                error="Source index not initialized. Call SootStaticAnalyzer first.",
            )

        return ToolResult(success=True, data=_expand(method))
