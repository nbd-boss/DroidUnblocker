# Knowledge-Driven Agent Framework

## 当前问题

`prompts.py` 的 Decision Rules 和 Constraints 中硬编码了阻塞模式的领域知识：

```
When SHALLOW reveals multiple callees tagged I/O / DATABASE / NETWORK ...
Method directly calls rawQuery / execSQL / openStream / openConnection / URL()
```

这导致两个问题：
1. LLM 的 thought 只会复述 prompt 里提到的模式，CPU 密集型等未列出的模式被忽略
2. 新增检测模式必须修改 prompt，耦合度高

---

## 目标架构

将领域知识从 prompt 中剥离，外置为可查询的知识文档。agent 根据当前分析信息**自主判断**是否需要查询——当对某个方法的阻塞性质无法肯定时，主动调用 KnowledgeQuery 获取参考。

```
┌─────────────────────────────────────────────────────┐
│                    ReAct Loop                        │
│                                                      │
│  thought → action → observation → thought ...        │
│                 │                                    │
│     agent 自主决策：当前信息是否足够？                  │
│     不确定 → TOOL_CALL KnowledgeQuery                │
│     确定   → TOOL_CALL / CONCLUDE / MOCK             │
│                 │                                    │
└─────────────────┼────────────────────────────────────┘
                  │
                  ▼
        ┌─────────────────┐
        │  KnowledgeQuery │  ← 新增 Tool
        │  (Tool 6)       │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────────────────┐
        │  knowledge/                 │
        │  ├── blocking_patterns.md   │  ← 阻塞模式知识库
        │  ├── android_apis.md        │  ← 高风险 API 清单
        │  └── cpu_patterns.md        │  ← CPU 密集型识别规则
        └─────────────────────────────┘
```

---

## 知识文档结构

### `knowledge/blocking_patterns.md`

每种阻塞模式以统一格式描述：

```markdown
## FILE_IO
- 特征：访问文件系统，耗时取决于存储速度
- 典型 API：FileInputStream, BufferedReader, getExternalFilesDirs, openFileInput
- 检测关键词：FileInputStream, FileOutputStream, BufferedReader, FileReader, FileWriter
- 危险等级：HIGH（外部存储可能极慢）

## DATABASE
- 特征：执行 SQL 查询或写入，耗时取决于数据量
- 典型 API：rawQuery, execSQL, SQLiteOpenHelper, Room @Query
- 检测关键词：rawQuery, execSQL, SQLiteDatabase, Cursor
- 危险等级：HIGH

## NETWORK
- 特征：网络 I/O，耗时不确定，Android 4.0+ 主线程调用直接抛异常
- 典型 API：HttpURLConnection, OkHttpClient, URL.openConnection
- 检测关键词：HttpURLConnection, OkHttpClient, openConnection, connect
- 危险等级：CRITICAL

## CPU_INTENSIVE
- 特征：纯计算，无 I/O，不触发 StrictMode，但长时间占用主线程
- 典型场景：矩阵运算、排序大数据集、递归、图像处理、JSON 大文件解析
- 检测关键词：嵌套循环（for/for）、递归调用自身、大数组操作
- 危险等级：MEDIUM（取决于数据规模）
- StrictMode 可检测：否，只能通过 elapsed > 300ms 判定

## SYNCHRONIZATION
- 特征：等待锁释放，耗时取决于其他线程持锁时长
- 典型 API：synchronized, ReentrantLock, CountDownLatch, Semaphore
- 检测关键词：synchronized, ReentrantLock, wait(), CountDownLatch
- 危险等级：MEDIUM（单线程环境下不触发）
- StrictMode 可检测：否
```

---

## Prompt 修改方案

### 修改前（硬编码知识）

```
| SHALLOW result shows I/O or DATABASE tags in callees | Upgrade to FULL_EXPAND |
| Method directly calls rawQuery / execSQL / openStream | CONCLUDE immediately |
```

### 修改后（自主查询）

prompt 不再列举具体的阻塞 API 和模式名称，改为告知 agent 知识库的存在和使用时机：

```
## Knowledge Base
A knowledge base is available via KnowledgeQuery. It contains descriptions of all
known UI blocking patterns (FILE_IO, DATABASE, NETWORK, CPU_INTENSIVE, SYNCHRONIZATION),
including detection heuristics, typical APIs, severity, and whether StrictMode can
detect the violation at runtime.

Use KnowledgeQuery when you are uncertain — for example:
- The method body contains patterns you cannot confidently classify
- The callee tags are empty but the code structure looks suspicious
- You need to know whether a pattern is detectable by StrictMode before concluding

You do NOT need to query KnowledgeQuery if the blocking pattern is already obvious
from the code (e.g., a direct rawQuery call). Use your own judgment.

## Decision Rules
| Situation | Action |
|-----------|--------|
| Blocking pattern obvious from code | CONCLUDE directly |
| Pattern uncertain — cannot confidently classify | TOOL_CALL KnowledgeQuery |
| KnowledgeQuery confirms blocking pattern | CONCLUDE with confirmed pattern |
| KnowledgeQuery returns no match | MOCK — pattern not recognized as blocking |
```

---

## KnowledgeQuery Tool 设计

两步交互，避免 agent 自行构造模糊 query：

### 第一步：列出目录（list）

agent 申请查询知识库时，工具先返回 metadata（知识库目录），
让 agent 根据当前分析场景自主选择要查的条目。

```python
# 参数
{ "action": "list" }

# 返回
{
  "entries": [
    { "id": "FILE_IO",         "summary": "文件系统访问，StrictMode 可检测" },
    { "id": "DATABASE",        "summary": "SQL 查询/写入，StrictMode 可检测" },
    { "id": "NETWORK",         "summary": "网络 I/O，主线程调用直接抛异常" },
    { "id": "CPU_INTENSIVE",   "summary": "纯计算密集，StrictMode 不可检测" },
    { "id": "SYNCHRONIZATION", "summary": "锁等待，单线程环境下不触发" }
  ]
}
```

### 第二步：获取具体条目（get）

agent 根据目录选定条目后，再发起精准查询获取完整描述。

```python
# 参数
{ "action": "get", "id": "CPU_INTENSIVE" }

# 返回
{
  "id": "CPU_INTENSIVE",
  "description": "纯计算，无 I/O，不触发 StrictMode，但长时间占用主线程",
  "typical_scenarios": ["矩阵运算", "排序大数据集", "递归", "图像处理"],
  "detection_keywords": ["嵌套循环", "递归调用自身", "大数组操作"],
  "severity": "MEDIUM",
  "strictmode_detectable": false
}
```

两步交互的好处：agent 不需要猜测 query 关键词，先浏览目录再精准取值，
消除词面不匹配问题，同时让 agent 的决策过程更透明。

---

## 实施步骤

1. 创建 `knowledge/` 目录，编写各模式的 `.md` 文档
2. 实现 `KnowledgeQueryTool`（Tool 6），支持按模式名或关键词检索
3. 修改 `prompts.py`：移除硬编码的模式列表和 API 清单，加入知识库使用说明
4. 更新 `TAG_PATTERNS`（`android_constants.py`）：加入 `CPU_INTENSIVE` 标签规则
5. 在 `main.py` 中注册 `KnowledgeQueryTool`
