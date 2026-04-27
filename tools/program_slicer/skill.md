---
name: ProgramSlicer
description: >
  基于切片准则（目标语句关键词 + 关注变量），对方法的源码做后向静态切片，
  提取所有可能影响该变量在目标语句处取值的相关语句。
  算法：文本近似 PDG 遍历，包含数据依赖（def-use 链）和控制依赖（if/while/for 分支）。
  将 LLM 需要分析的上下文从数百行压缩到 5-15 条语句，大幅降低推理噪声，
  精确回答"哪个变量构造了这条 SQL"或"这个阻塞调用是否总会被执行"等因果问题。

  【前置依赖】必须在同一 Agent 会话中先调用 SootStaticAnalyzer，再调用 CallChainExpander
  定位可疑方法后，才调用本工具。本工具依赖 SootStaticAnalyzer 写入内存的索引（get_index()），
  若索引未初始化将直接返回错误。
parameters:
  type: object
  properties:
    method:
      type: string
      description: >
        待切片方法的签名，应直接取自 CallChainExpander 返回结果中的 callee.method 字段，
        或取自 call_graph.json（<agent目录>/skill1_output/call_graph.json）中节点的 signature 字段。
        格式示例："DBHelper.queryUser"、"DataManager.loadUserProfile"。
    target_stmt:
      type: string
      description: >
        目标语句的关键词（切片准则中的语句 s），如 "rawQuery"、"openStream"、"listFiles"。
        切片从包含此关键词的语句出发，向后追踪所有数据 / 控制依赖。
    target_var:
      type: string
      description: >
        目标变量（切片准则中的变量 v），如 "query"、"cursor"、"$r2"。
        可为空；为空时追踪目标语句处所有变量的依赖。
  required:
    - method
    - target_stmt
returns: >
  {
    "method": "DBHelper.queryUser",
    "found": true,
    "criterion_stmt": "rawQuery",
    "criterion_var": "query",
    "method_line": 42,
    "slice": [
      {"line": 45, "code": "    String sql = \"SELECT * FROM user_profiles\""},
      {"line": 46, "code": "    val cursor = db.rawQuery(sql, null)"},
      {"line": 48, "code": "    while (cursor.moveToNext()) { ... }"}
    ],
    "interprocedural_context": [
      {"line": 88, "code": "  ↳[DataHelper.buildQuery] return base;", "interprocedural": true}
    ],
    "slice_size": 3
  }
  — found=true 表示方法在 index 中存在；found=false 表示方法不存在，slice 为空。
  — slice 按原始代码顺序排列，每条携带文件绝对行号（line）和原始代码（code）。
  — 若 target_stmt 在方法体中未找到匹配，返回方法体前 15 行作为兜底。
  — interprocedural_context 为 1 层跨方法补充，条目带 interprocedural=true 标记。
usage_hints:
  - SootStaticAnalyzer 必须在同一会话中先调用，CallChainExpander 确认可疑方法后再调用本工具。
  - method 参数应直接使用 CallChainExpander 返回的 callee.method 值，避免手写签名出错。
  - target_stmt 设置为阻塞 API 关键词（如 rawQuery、openStream、openConnection）。
  - target_var 设置为查询字符串变量，可追踪完整数据流链，揭示是否存在无 WHERE/LIMIT 约束的全表扫描。
  - 不要对所有方法都做切片 — 只对已确认可疑的节点使用，否则浪费 Token 且引入噪声。
  - 切片结果通常 5-15 行，可直接放入 LLM 上下文做因果分析，无需裁剪。
  - found=false 时方法不在 index，立即 MOCK，不要猜测或重试其他方法名。
  - interprocedural_context 提供 1 层跨方法补充上下文，重点关注 interprocedural=true 的条目。
---

## Overview

ProgramSlicer 解决的问题：当 CallChainExpander 锁定了一个可疑方法后，LLM 往往还需要
精确理解该方法内部的因果关系。程序切片以极低 Token 代价提取精确的因果链。

### 后向切片算法

给定切片准则 `<s, v>`（语句 s 和变量 v），提取所有可能影响 v 在 s 处取值的语句：

```
1. 找到包含 target_stmt 关键词的行集合（种子行 seeds）
2. 初始 use 集合 = seeds 中出现的变量 ∪ {target_var}
3. 后向不动点循环（MAX_PASSES = 5）：
   对每一行 L：
     - L 定义了 use 集合中某变量（赋值语句）→ 加入切片，扩展 use 集合
     - L 是条件分支（if/while/for）且包含 use 集合中的变量 → 加入切片（控制依赖）
   直到 slice 集合不再增长
4. 按原始顺序输出切片行
```

### 正则模式

| 用途 | 正则 |
|------|------|
| 变量提取 | `\b(\$?[a-z_]\w*)\b`（匹配 `r0`、`$r1`、`query` 等） |
| 纯赋值/复合赋值 | `^(\$?\w+)\s*[+\-*/%&\|^]?=(?!=)`（匹配 `a = ...`、`a += ...`） |
| Java 类型声明赋值 | `^(?:final\|static\|...)?(?:[A-Z]\w*\|int\|long\|...)\s+(\$?[a-z_]\w*)\s*=`（匹配 `String sql = ...`、`Cursor c = ...`） |
| Kotlin val/var 声明 | `^(?:val\|var)\s+(\$?[a-z_]\w*)(?:\s*:\s*\w[\w.<>?, ]*)?\s*=`（匹配 `val sql = ...`、`var cursor: Cursor = ...`） |
| 控制分支 | `^\s*(if\|while\|for\|switch)\s*\(` |

### 与 RAG 的类比

程序切片本质上是"与 LLM 推理相关的上下文精准提取"，和 RAG 中的检索增强异曲同工——
只不过 RAG 用语义检索，这里用**程序分析级别的精确依赖追踪**。

### Token 效率对比

| 传入 LLM 的内容 | Token 估算 | 推理质量 |
|----------------|-----------|---------|
| 完整方法体（100+ 行） | 高 | 差（噪声干扰） |
| 只传方法体 | 中 | 中（缺跨语句因果） |
| **切片结果（5-15 行）** | **低** | **高（精确因果链）** |

### 使用时机（正确 vs 错误）

```
✅ 正确：
   CallChainExpander(FULL_EXPAND) → 发现 DBHelper.queryUser 调用了 rawQuery
        ↓
   ProgramSlicer(target_stmt="rawQuery", target_var="query")
        ↓
   发现 SELECT * FROM user_profiles 无 WHERE/LIMIT → CONCLUDE

❌ 错误：
   在探索早期对所有入口方法都做切片（准则不明确，结果无意义）
```
