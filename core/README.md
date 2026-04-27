# core — ReAct 引擎核心

本包包含驱动 Agent 推理循环的三个模块：数据类型定义、工作记忆管理、ReAct 循环引擎。

---

## 文件列表

| 文件 | 职责 |
|------|------|
| [`types.py`](#typespy) | 枚举与数据类定义 |
| [`memory.py`](#memorypy) | Agent 工作记忆管理 |
| [`react_loop.py`](#react_looppy) | Thought → Action → Observation 循环引擎 |

---

## types.py

系统中所有枚举和数据类的统一定义。

### 枚举

| 类型 | 值 | 用途 |
|------|----|------|
| `DecisionLevel` | `CONCLUDE` `EXPLORE` `SHALLOW` `MOCK` | 探索阶段四级节点决策 |
| `Confidence` | `LOW` `MEDIUM` `HIGH` | 静态分析置信度 |
| `VerificationStatus` | `PENDING` `CONFIRMED` `PARTIAL` `REFUTED` | Phase 2 动态验证结果 |

### 关键数据类

**`AnalysisConclusion`** — 探索阶段输出的结论：
```python
@dataclass
class AnalysisConclusion:
    call_chain: List[str]      # 从入口到根因方法的调用链
    root_cause: str            # 根因描述（CLEAN 时为空）
    blocking_pattern: str      # FILE_IO | DATABASE | NETWORK | CPU_INTENSIVE | SYNCHRONIZATION | OTHER | NONE
    confidence: Confidence
    entry_method: str          # 本次探索的 UI 线程入口
    verdict: str = "BLOCKED"   # BLOCKED | CLEAN
    slice_evidence: str = ""
```

**`FullExpandNode`** — FULL_EXPAND 返回的调用树节点：
```python
@dataclass
class FullExpandNode:
    signature: str
    class_name: str
    method_name: str
    tags: List[str]
    body_excerpt: str          # 第二层节点为空（expandable=True）
    callees: List[FullExpandNode]
    expandable: bool = False   # True 表示该节点未展开，Agent 可继续深入
```

**`ToolResult`** — 所有 Tool 的统一返回格式：
```python
@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""
```

---

## memory.py

`AgentMemory` 管理单个 UI 入口的完整分析上下文，生命周期与一次 `react_loop.run()` 调用一致。

### 存储内容

| 属性 | 类型 | 内容 |
|------|------|------|
| `entry_method` | `str` | 当前 UI 入口方法签名 |
| `entries` | `List[MemoryEntry]` | 按时序排列的 thought/action/observation/system 条目 |
| `mocked_methods` | `set` | 已 MOCK 的方法集合 |
| `explored_methods` | `set` | P4 去重集合，key=`method::mode` |
| `explored` | `Set[str]` | FULL_EXPAND 记忆化集合，key=纯方法签名（body 已被 LLM 看过） |
| `method_cache` | `Dict[str, dict]` | body 缓存，`{body_excerpt, tags}`，上下文压缩后可恢复 |
| `call_stack` | `List[str]` | 当前调用链 |
| `current_depth` | `int` | 当前探索深度（FULL_EXPAND 时递增） |
| `valid_explored_count` | `int` | P5 门槛：在 index 中找到并成功分析的方法数 |

### 上下文结构

`get_context(max_entries=15, conclusions=None)` 返回两部分拼接的文本：

**结构化摘要（置顶，不占滑动窗口配额）**
```
=== Session State ===
Entry method : MainActivity.onMatrixClick
Call chain   : MainActivity.onMatrixClick → MatrixProcessor.runComputation
Depth        : 1
Mocked       : MainActivity.d, MainActivity.runComputation
Body explored: MatrixProcessor.runComputation
Blocking points found so far:
  [CPU_INTENSIVE] O(n³) matrix multiplication  |  chain: MainActivity.onMatrixClick → ...
Relevant method bodies:
  [MatrixProcessor.runComputation]
    tags: []
    body: public static void runComputation() { ... }
=== Recent History ===
```

**滑动窗口（最近 15 条 entry）**
```
[System] Starting exploration. Entry: MainActivity.onMatrixClick
[Thought] ...
[Action] TOOL_CALL CallChainExpander(...)
[Observation] CallChainExpander result: ...
```

结构化摘要保证关键状态不因滑动窗口截断而丢失；Relevant method bodies 只输出 `call_stack` 上涉及的方法 body，避免 token 膨胀。

---

## react_loop.py

`ReActLoop` 对单个 UI 线程入口方法运行 ReAct 循环。

### 构造参数

```python
ReActLoop(
    registry,               # ToolRegistry 实例
    llm_client,             # LLMClient 实例
    tools_description="",   # 工具描述文本，注入 system prompt
    max_depth=8,
    max_iterations=40,
)
```

### 运行流程

```python
conclusions, memory = react_loop.run(entry_method="MainActivity.onCreate")
```

内部循环：
```
while iteration < max_iterations and depth < max_depth:
    context = memory.get_context(conclusions=conclusions)
    response = llm_client.complete(system, user)   → JSON
    parsed = _parse_response(response)

    if action.type == "CONCLUDE":   → 记录结论或终止
    elif action.type == "MOCK":     → 标记方法为 MOCK
    elif action.type == "TOOL_CALL":
        P2: 拦截 SootStaticAnalyzer 重复调用
        P4: 拦截重复 SHALLOW / 已 body-explored 的 FULL_EXPAND
        注入 explored + method_cache 到 FULL_EXPAND params
        result = registry.execute(tool_name, params)
        P5: 记录有效探索计数
```

### 护栏机制

| 护栏 | 位置 | 作用 |
|------|------|------|
| P2 | TOOL_CALL 前 | 硬拦截循环内的 SootStaticAnalyzer 调用 |
| P4-SHALLOW | TOOL_CALL 前 | 拦截重复 SHALLOW 查询（`method::SHALLOW` key） |
| P4-FULL | TOOL_CALL 前 | 拦截已展开过 body 的 FULL_EXPAND 请求（检查 `memory.explored`） |
| P5 | CONCLUDE 前 | 要求至少成功分析过 1 个项目内方法才允许 BLOCKED 结案 |

### LLM 响应格式（JSON）

```json
{
  "thought": "分析推理...",
  "action": {
    "type": "TOOL_CALL | CONCLUDE | MOCK",

    // TOOL_CALL
    "tool_name": "CallChainExpander",
    "params": { "method": "...", "mode": "SHALLOW" },

    // CONCLUDE BLOCKED
    "verdict": "BLOCKED",
    "call_chain": ["EntryMethod", "...", "BlockingMethod"],
    "root_cause": "...",
    "blocking_pattern": "FILE_IO",
    "evidence": "...",

    // CONCLUDE CLEAN / ALL_CLEAR
    "verdict": "CLEAN",
    "reason": "All callees are safe."
  }
}
```

### 容错解析

`_parse_response()` 按以下优先级解析 LLM 响应：
1. 直接 `json.loads()`
2. 去除 Markdown 代码围栏后解析
3. 正则提取第一个 `{...}` 块后解析
4. 全部失败：将原文作为 thought，action 设为 `UNKNOWN`
