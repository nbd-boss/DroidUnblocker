# core — ReAct 引擎核心

本包包含驱动 Agent 推理循环的三个模块：数据类型定义、工作记忆管理、ReAct 循环引擎。

---

## 文件列表

| 文件 | 职责 |
|------|------|
| [`types.py`](#typespy) | 枚举与数据类定义 |
| [`memory.py`](#memorypy) | Agent 工作记忆管理（树状结构） |
| [`react_loop.py`](#react_looppy) | Thought → Action → Observation 循环引擎 |

---

## types.py

系统中所有枚举和数据类的统一定义。

### 枚举

| 类型 | 值 | 用途 |
|------|----|------|
| `DecisionLevel` | `CONCLUDE` `EXPLORE` `EXPAND` `MOCK` | 探索阶段四级节点决策 |
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

### 数据结构

#### TreeNode

每个调用树节点的信息载体：

```python
@dataclass
class TreeNode:
    node_id: int
    signature: str
    tags: List[str]
    body_excerpt: str = ""        # expanded=True 时才有值
    expanded: bool = False        # 是否已 EXPAND 过（LLM 看过 body）
    verdict: str = ""             # "" | "BLOCKED" | "CLEAN"
    blocking_pattern: str = ""    # verdict=BLOCKED 时填入
    reuse_from: Optional[int] = None  # alias 节点指向原始节点 ID
```

#### AgentMemory 字段

| 属性 | 类型 | 内容 |
|------|------|------|
| `entry_method` | `str` | 当前 UI 入口方法签名 |
| `entries` | `List[MemoryEntry]` | 按时序排列的 thought/action/observation/system 条目 |
| `mocked_methods` | `set` | 已 MOCK 的方法集合 |
| `valid_explored_count` | `int` | P5 门槛：在 index 中找到并成功分析的方法数 |
| `tree_nodes` | `Dict[int, TreeNode]` | 节点 ID → 节点对象 |
| `parent` | `Dict[int, Optional[int]]` | 节点 ID → 父节点 ID（根节点为 None） |
| `sig_to_ids` | `Dict[str, List[int]]` | 方法签名 → 节点 ID 列表（支持菱形依赖） |
| `current_focus` | `int` | 当前焦点节点 ID |

### 树结构说明

节点在 **callee 被发现时**（CallChainExpander 返回时）注册，在 **EXPAND 时**填充 body。父子关系通过 `parent` 字典编码，树拓扑为父指针表示法。

菱形依赖（A→B→D，A→C→D）通过 `sig_to_ids` 支持同一签名对应多个节点：
- D 首次出现时创建原始节点
- D 再次出现（已有 expanded=True 的同签名节点）时，新节点标记 `reuse_from` 指向原始节点，复用其结论，不重复 EXPAND

### 关键方法

| 方法 | 调用时机 |
|------|---------|
| `register_node(sig, tags, parent_id)` | callee 被发现时，返回新节点 ID |
| `expand_node(node_id, body, tags)` | EXPAND 成功后，填充 body、标记 expanded=True |
| `set_verdict(node_id, verdict, blocking_pattern)` | CONCLUDE 时回写结论 |
| `get_path_to(node_id) -> List[int]` | 沿 parent 回溯，重建从根到指定节点的路径 |
| `is_body_explored(sig)` | P4 去重检查，签名是否已有 expanded=True 的节点 |

### 上下文结构

`get_context(max_entries=15, conclusions=None)` 返回两部分拼接的文本：

**结构化摘要（置顶，不占滑动窗口配额）**
```
=== Session State ===
Entry method : MainActivity.onMatrixClick
Current path : MainActivity.onMatrixClick → MatrixProcessor.runComputation
Blocking points found so far:
  [CPU_INTENSIVE] O(n³) matrix multiplication  |  chain: ...

► [CURRENT PATH]
  MatrixProcessor.runComputation  [FOCUS]  tags=['CPU']
    body: public static void runComputation() { ...
  MainActivity.onMatrixClick  [PATH]  tags=[]
    body: protected void onMatrixClick() { ...

✓ [EXPLORED - off path]
  Utils.formatTime  tags=[]  → CLEAN()

↺ [REUSED]
  DatabaseHelper.query  tags=['DATABASE']  reused from node#3  → BLOCKED(DATABASE)

○ [EXPANDABLE - not yet explored]
  NetworkClient.fetch  tags=['NETWORK']

=== Recent History ===
```

**滑动窗口（最近 15 条 entry）**
```
[System] Starting exploration. Entry: MainActivity.onMatrixClick
[Thought] ...
[Action] TOOL_CALL CallChainExpander(...)
[Observation] CallChainExpander result: ...
```

主路径节点给完整 body；旁路已探索节点只给签名 + tags + verdict；alias 节点标注复用来源；未展开节点只给签名 + tags。

---

## react_loop.py

`ReActLoop` 对单个 UI 线程入口方法运行 ReAct 循环。

### 构造参数

```python
ReActLoop(
    registry,               # ToolRegistry 实例
    llm_client,             # LLMClient 实例
    tools_description="",   # 工具描述文本，注入 system prompt
    max_iterations=40,
)
```

### 运行流程

```python
conclusions, memory = react_loop.run(entry_method="MainActivity.onCreate")
```

内部循环：
```
初始化：register_node(entry_method) → current_focus = root_id

while iteration < max_iterations:
    context = memory.get_context(conclusions=conclusions)
    response = llm_client.complete(system, user)   → JSON
    parsed = _parse_response(response)

    if action.type == "CONCLUDE":
        set_verdict(current_focus, verdict)   → 记录结论或终止
    elif action.type == "MOCK":
        mark_mocked(method)
    elif action.type == "TOOL_CALL":
        P2: 拦截 SootStaticAnalyzer 重复调用
        P4: 拦截已 body-explored 的 EXPAND 请求（is_body_explored）
        更新 current_focus 到目标方法节点
        result = registry.execute(tool_name, params)
        若 found=True：expand_node + register callees + valid_explored_count++
```

### 护栏机制

| 护栏 | 位置 | 作用 |
|------|------|------|
| P2 | TOOL_CALL 前 | 硬拦截循环内的 SootStaticAnalyzer 调用 |
| P4 | TOOL_CALL 前 | 拦截已展开过 body 的 EXPAND 请求（查 `is_body_explored`） |
| P5 | CONCLUDE 前 | 要求至少成功分析过 1 个项目内方法才允许 BLOCKED 结案 |

### LLM 响应格式（JSON）

```json
{
  "thought": "分析推理...",
  "action": {
    "type": "TOOL_CALL | CONCLUDE | MOCK",

    // TOOL_CALL
    "tool_name": "CallChainExpander",
    "params": { "method": "..." },

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
