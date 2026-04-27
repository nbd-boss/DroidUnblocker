# ProgramSlicer — Tool 3

对可疑方法的源码做**后向静态切片**，将 LLM 需要分析的上下文从数百行压缩到 5-15 条语句。

---

## 功能

给定切片准则 `<target_stmt, target_var>`：

1. 在目标方法体内定位包含 `target_stmt` 的种子语句
2. 反向追踪数据依赖（def-use 链）和控制依赖（条件语句）
3. 若赋值右侧为项目内方法调用，自动追踪 1 层跨方法数据流
4. 返回带**文件绝对行号**的精简语句列表

---

## 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `method` | string | ✓ | 待切片方法签名，如 `HistoryActivity.onCreate` |
| `target_stmt` | string | ✓ | 目标语句关键词，如 `rawQuery`、`openStream` |
| `target_var` | string | | 关注变量名（可选），如 `cursor`、`query` |

---

## 输出格式

```json
{
  "method": "HistoryActivity.onCreate",
  "found": true,
  "criterion_stmt": "rawQuery",
  "criterion_var": "cursor",
  "method_line": 42,
  "slice": [
    {"line": 45, "code": "    String sql = buildQuery(filter);"},
    {"line": 46, "code": "    cursor = db.rawQuery(sql, null);"},
    {"line": 48, "code": "    if (cursor.moveToFirst()) {"}
  ],
  "interprocedural_context": [
    {"line": 88, "code": "  ↳[DataHelper.buildQuery] String base = \"SELECT * FROM history\";", "interprocedural": true},
    {"line": 90, "code": "  ↳[DataHelper.buildQuery] return base;", "interprocedural": true}
  ],
  "slice_size": 3
}
```

| 字段 | 说明 |
|------|------|
| `found` | `true`=方法在 index 中；`false`=方法不存在，slice 为空 |
| `method_line` | 方法在文件中的起始行，`found=false` 时为 `-1` |
| `slice[].line` | 文件内绝对行号（1-based） |
| `slice[].code` | 该行源代码（保留原始缩进） |
| `interprocedural_context[].interprocedural` | 始终为 `true`，标记跨方法补充行 |
| `slice_size` | 主切片行数（不含跨方法补充） |

---

## 核心实现

### 方法内后向切片

```
种子行 = {含 target_stmt 的所有行}
use_vars = {target_var} ∪ 种子行中出现的所有变量

迭代（最多 5 轮）：
  for each 行 ln:
    if _defined_var(ln) ∈ use_vars:              # 数据依赖
        加入切片；将 ln 中所有变量并入 use_vars
    if 条件分支 且 ln 中变量 ∩ use_vars ≠ ∅:    # 控制依赖
        加入切片
  直到切片不再增长
```

`_defined_var` 依次匹配三类赋值语句：

| 类型 | 示例 |
|------|------|
| Java 类型声明赋值 | `String sql = ...`、`Cursor c = ...` |
| Kotlin val/var 声明 | `val sql = ...`、`var cursor: Cursor = ...` |
| 纯赋值 / 复合赋值 | `sql = ...`、`sql += ...` |

### 1 层跨方法数据流追踪

对切片中每条 `var = method(...)` 形式的赋值，若被调用方法在项目 index 中存在，则提取其内部的 `return` 语句和赋值语句（最多 8 行）附加到 `interprocedural_context`。

---

## 示例

**源码**（`Queries.java`，第 157 行）：

```java
public static Cursor getAllFavoriteCursorDeprecated() {
    String sql = "SELECT * FROM " + TABLE_NAME + " WHERE (" + FAVORITE + " =? OR " + FAVORITE + "=3)";
    sql += " AND (" + TITLE_ENG + " LIKE ? OR " + TITLE_JP + " LIKE ? OR " + TITLE_PRETTY + " LIKE ? )";
    String q = "%%%";
    Cursor cursor = db.rawQuery(sql, new String[]{"1", q, q, q});
    return cursor;
}
```

**调用**：

```python
ProgramSlicer(
    method      = "GalleryTable.getAllFavoriteCursorDeprecated",
    target_stmt = "rawQuery",
    target_var  = "cursor",
)
```

**输出**：

```json
{
  "method": "GalleryTable.getAllFavoriteCursorDeprecated",
  "found": true,
  "criterion_stmt": "rawQuery",
  "criterion_var": "cursor",
  "method_line": 157,
  "slice": [
    {"line": 159, "code": "    String sql = \"SELECT * FROM \" + TABLE_NAME + ..."},
    {"line": 161, "code": "    sql += \" AND (\" + TITLE_ENG + \" LIKE ? OR \" + ..."},
    {"line": 164, "code": "    String q = \"%%%\";"},
    {"line": 165, "code": "    Cursor cursor = db.rawQuery(sql, new String[]{\"1\", q, q, q});"}
  ],
  "interprocedural_context": [],
  "slice_size": 4
}
```
