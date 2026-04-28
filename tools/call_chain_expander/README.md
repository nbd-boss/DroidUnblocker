# CallChainExpander

ReAct 探索阶段最核心的工具，驱动 Agent 在调用图上进行按需树搜索。每次调用返回指定方法的完整 body 摘要及其所有直接 callee 的签名和风险标签，Agent 通过反复调用逐层向下，形成 LLM 驱动的深度优先探索。

## 前置依赖

必须在同一 Agent 会话中先调用 `SootStaticAnalyzer`，本工具依赖其写入内存的调用图索引。若索引未初始化，工具直接返回错误：

```
Source index not initialized. Call SootStaticAnalyzer first.
```

方法签名来源：
- UI 入口：`skill1_output/ui_entry_points.json` → `entry_points[*].method_signature`
- 任意节点：`skill1_output/call_graph.json` → `nodes[*].signature`

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `method` | string | 是 | 目标方法签名，如 `"MainActivity.onCreate"`、`"DBHelper.queryUser"`。支持后缀模糊匹配。 |

## 返回值

```json
{
  "method": "DataCacheManager.buildCache",
  "found": true,
  "tags": ["I/O"],
  "body": "public static void buildCache(Context context) { ... }",
  "callees": [
    { "signature": "DataCacheManager.loadEntries", "tags": ["I/O"], "expandable": true },
    { "signature": "Context.getFilesDir", "tags": [], "expandable": false }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `found` | `false` 表示方法不在项目索引中，应立即 MOCK |
| `body` | 方法体摘要，最多 600 字符 |
| `tags` | 方法自身的风险标签 |
| `callees[].expandable` | `true` 表示项目内部方法，可继续展开；`false` 表示外部 SDK 方法 |

## 风险标签规则

标签由规则引擎纯静态生成，零 LLM 开销：

| Tag | 触发关键词（示例） |
|-----|-----------------|
| `I/O` | `java.io.`、`FileInputStream`、`BufferedReader`、`.listFiles(` |
| `DATABASE` | `SQLiteDatabase`、`rawQuery`、`execSQL`、`ContentResolver` |
| `NETWORK` | `okhttp3.`、`HttpURLConnection`、`Retrofit`、`openConnection` |
| `THREADING` | `java.lang.Thread`、`Executors.`、`AsyncTask`、`Dispatchers.` |
| `SYNCHRONIZATION` | `synchronized`、`ReentrantLock`、`CountDownLatch` |
| `HANDLER` | `android.os.Handler`、`.post(`、`.postDelayed(` |

## 使用示例

```
[Action] CallChainExpander(method="initImageLoader")

[Observation]
  body: "public void initImageLoader() { DiskCache.init(ctx); ThreadPool.create(); ... }"
  tags: [I/O, THREADING]
  callees:
    - DiskCache.init()    [I/O, DATABASE]  expandable=true
    - ThreadPool.create() [THREADING]      expandable=true
    - MemoryCache.setup() []               expandable=true

[Thought] DiskCache.init() 在主线程上下文中有磁盘 I/O 和数据库风险，继续展开。

[Action] CallChainExpander(method="DiskCache.init")
```

## 注意事项

- 不要对所有 callee 都发起展开，只对可疑节点深入，控制分析范围
- 已展开过 body 的方法会被 react_loop 自动拦截，无需手动追踪
- `found=false` 时立即 MOCK，不要重试其他方法名
- `expandable=false` 的 callee 是外部 SDK 方法，直接根据 tags 和方法名判断风险即可
