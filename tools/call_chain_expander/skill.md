---
name: CallChainExpander
description: >
  从指定方法出发，展开其直接被调用的方法列表（单层展开，非全量展开）。
  SHALLOW 模式：返回一层 callee 摘要 + 规则化风险标签（I/O / DATABASE / NETWORK /
  THREADING / SYNCHRONIZATION / HANDLER），标签纯字符串规则生成，零 LLM 开销。
  FULL_EXPAND 模式：返回两层 BFS 调用子树，含方法体摘要，供 LLM 做深度分析。
  是 ReAct 探索阶段调用最频繁的工具，四级决策（CONCLUDE/EXPLORE/SHALLOW/MOCK）的核心依据来源。

  【前置依赖】必须在同一 Agent 会话中先调用 SootStaticAnalyzer，才能使用本工具。
  SootStaticAnalyzer 在运行时将分析结果同时写入内存索引（供本工具使用）和磁盘文件：
    - <agent目录>/skill1_output/ui_entry_points.json  — UI 入口列表（method_signature 字段即为 method 参数的来源）
    - <agent目录>/skill1_output/call_graph.json        — 全量调用图（可用于确认签名格式）
  若未先调用 SootStaticAnalyzer，本工具将直接返回错误。
parameters:
  type: object
  properties:
    method:
      type: string
      description: >
        目标方法签名，应直接取自 ui_entry_points.json 中的 method_signature 字段，
        或取自 call_graph.json 中节点的 signature 字段。
        格式示例："MainActivity.onCreate"、"DBHelper.queryUser"。
        支持模糊匹配：若精确签名不存在，会尝试后缀匹配（如 ".queryUser"）。
    mode:
      type: string
      enum:
        - SHALLOW
        - FULL_EXPAND
      description: >
        SHALLOW（默认）— 单层 callee 摘要 + 规则 tag，成本极低，适合初步评估。
        FULL_EXPAND — 两层 BFS 子树，含方法体摘要，仅在确认可疑后使用。
      default: SHALLOW
  required:
    - method
returns: >
  SHALLOW 模式：
  {
    "method": "...",
    "callees": [{ "method": "DBHelper.queryUser", "tags": ["DATABASE"] }, ...],
    "has_io": false,
    "has_threading": false,
    "has_network": false,
    "has_database": true,
    "has_synchronization": false,
    "estimated_complexity": "medium"
  }

  FULL_EXPAND 模式（递归结构，最多两层）：
  {
    "signature": "DataManager.loadUserProfile",
    "class": "DataManager",
    "method": "loadUserProfile",
    "tags": ["DATABASE"],
    "body_excerpt": "{ val db = ...; val cursor = db.rawQuery(...) }",
    "callees": [{ "signature": "DBHelper.queryUser", "tags": ["DATABASE"], "callees": [...] }]
  }
usage_hints:
  - SootStaticAnalyzer 必须在同一会话中先调用完毕，本工具依赖其写入内存的索引。
  - method 参数应直接使用 ui_entry_points.json 中的 method_signature 值，避免手写签名出错。
  - 默认使用 SHALLOW 模式 — 以极低成本获取风险 tag，帮助做 EXPLORE/MOCK 决策。
  - 仅当 SHALLOW 摘要显示 I/O / DATABASE / NETWORK tag，且调用上下文在主线程时，才升级为 FULL_EXPAND。
  - 每次调用只展开一个方法的直接 callee（单层）；通过多次迭代逐层向下探索，保证 LLM 在每层自主决策。
  - FULL_EXPAND 返回后，对每个 callee 重新执行四级决策（CONCLUDE/EXPLORE/SHALLOW/MOCK），再决定是否继续展开。
  - 不要对所有节点都用 FULL_EXPAND — 只对已判定为 EXPLORE 的可疑节点使用。
  - estimated_complexity 由 risk_count = sum([has_io, has_network, has_database, has_threading]) 决定：0→low，1→medium，≥2→high。
---

## Overview

CallChainExpander 是探索阶段最核心的工具，驱动 Agent 在调用图上进行按需树搜索。

### 前置依赖

本工具依赖 SootStaticAnalyzer 在同一 Agent 会话中写入内存的调用图索引（`_INDEX`）。
若索引未初始化（`get_index()` 返回 None），工具将立即返回错误：
```
"Source index not initialized. Call SootStaticAnalyzer first."
```

方法签名应从 SootStaticAnalyzer 的输出文件中获取：
- **UI 入口**：读取 `<agent目录>/skill1_output/ui_entry_points.json` → `entry_points[*].method_signature`
- **任意节点**：读取 `<agent目录>/skill1_output/call_graph.json` → `nodes[*].signature`

### 单层展开原则

每次调用只返回指定方法的**直接被调用者**（距离为 1 的 callee）。Agent 通过反复调用此工具
逐层向下，形成 LLM 驱动的深度优先 / 广度优先混合搜索。这种设计让 LLM 在每一层都能
自主决策是否继续深入，而不是一次性拿到整棵树。

### Tag 生成机制（纯规则，零 LLM）

扫描 callee 方法体字符串，匹配以下关键词模式：

| Tag | 触发关键词（示例） |
|-----|-----------------|
| `I/O` | `java.io.` `FileInputStream` `BufferedReader` `.listFiles(` `.mkdirs(` |
| `DATABASE` | `SQLiteDatabase` `rawQuery` `execSQL` `.query(` `android.database.` |
| `NETWORK` | `java.net.` `okhttp3.` `HttpURLConnection` `URL(` `openConnection` |
| `THREADING` | `java.lang.Thread` `Executors.` `AsyncTask` `kotlinx.coroutines.` `Dispatchers.` |
| `SYNCHRONIZATION` | `synchronized` `.wait()` `ReentrantLock` `CountDownLatch` |
| `HANDLER` | `android.os.Handler` `Handler(` `.post(` `.postDelayed(` |

### SHALLOW → FULL_EXPAND 升级决策示例

```
[Observation] SHALLOW(initImageLoader):
  callees:
    - DiskCache.init()    [I/O, DATABASE]
    - ThreadPool.create() [THREADING]
    - MemoryCache.setup() []
  has_io: true, estimated_complexity: "high"

[Thought] 在 onCreate 主线程上下文中，DiskCache.init() 有磁盘 I/O 风险。
          升级为 FULL_EXPAND。

[Action] CallChainExpander(method="initImageLoader", mode="FULL_EXPAND")
```
