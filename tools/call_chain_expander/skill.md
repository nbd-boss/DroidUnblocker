---
name: CallChainExpander
description: >
  从指定方法出发，返回该方法的完整 body 摘要（≤600字符）及其所有 direct callee
  的签名和风险标签（I/O / DATABASE / NETWORK / THREADING / SYNCHRONIZATION / HANDLER）。
  对可疑的 callee 再次发起 EXPAND 可获取其 body 及下一层 callee 信息，
  逐层按需深入，避免一次性拉取过多无关代码。
  已展开过 body 的方法会被自动跳过（记忆化），无需手动追踪。
  是 ReAct 探索阶段调用最频繁的工具，四级决策（CONCLUDE/EXPLORE/EXPAND/MOCK）的核心依据来源。

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
        或取自 call_graph.json 中节点的 signature 字段，或取自上一次 EXPAND 结果中
        callee 的 signature 字段。格式示例："MainActivity.onCreate"、"DBHelper.queryUser"。
        支持模糊匹配：若精确签名不存在，会尝试后缀匹配（如 ".queryUser"）。
  required:
    - method
returns: >
  {
    "method": "DataCacheManager.buildCache",
    "found": true,
    "tags": ["I/O"],
    "body": "    public static void buildCache(Context context) { ... }",
    "callees": [
      { "signature": "DataCacheManager.loadEntries", "tags": ["I/O"], "expandable": true },
      { "signature": "Context.getFilesDir", "tags": [], "expandable": false }
    ]
  }
  — found=false 表示方法不在项目索引中，body 为空，callees 为空，立即 MOCK。
  — expandable=true 表示该 callee 在项目中存在，可对其再次发起 EXPAND 获取 body。
  — expandable=false 表示外部 SDK 方法，无法继续展开，根据 tags 和方法名判断风险即可。
usage_hints:
  - SootStaticAnalyzer 必须在同一会话中先调用完毕，本工具依赖其写入内存的索引。
  - method 参数应直接使用 ui_entry_points.json 中的 method_signature 值，或上一次 EXPAND 结果中 callee.signature 值，避免手写签名出错。
  - EXPAND 返回当前方法的 body 和 callee 列表；读完 body 后，对可疑的 expandable:true callee 再次发起 EXPAND 以深入分析。
  - 以下信号提示某个 callee 值得继续 EXPAND：① tags 命中（I/O / DATABASE / NETWORK / SYNCHRONIZATION）；② 方法名语义可疑（如 loadXxx、initXxx、queryXxx、parseXxx）；③ 当前方法处于已确认的风险调用链上。
  - 已展开过 body 的方法会被自动拦截，无需手动追踪；被拦截时应转向其 expandable:true 的 callee 继续分析。
  - expandable=false 的 callee 是外部 SDK 方法，直接根据 tags 和方法名判断风险，无需展开。
  - found=false 时方法不在索引中，立即 MOCK，不要重试其他方法名。
  - 不要对所有 callee 都发起 EXPAND，只对判定为可疑的节点展开，控制分析范围。
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

### 按需深入示例

```
[Observation] CallChainExpander(method="initImageLoader"):
  body: "public void initImageLoader() { DiskCache.init(ctx); ThreadPool.create(); ... }"
  tags: [I/O, THREADING]
  callees:
    - DiskCache.init()    [I/O, DATABASE]  expandable=true
    - ThreadPool.create() [THREADING]      expandable=true
    - MemoryCache.setup() []               expandable=true

[Thought] 在 onCreate 主线程上下文中，DiskCache.init() 有磁盘 I/O 和数据库风险，
          需要继续展开。

[Action] CallChainExpander(method="DiskCache.init")
```
