# output — 报告生成

本包将探索结论和动态验证数据组装为最终的 `root_cause_report.json`。

---

## 文件列表

| 文件 | 职责 |
|------|------|
| [`report.py`](#reportpy) | 构建报告 dict + 写入 JSON 文件 |

---

## report.py

### `build_report()`

```python
def build_report(
    conclusion: AnalysisConclusion,
    verification_status: VerificationStatus,
    blocking_time_ms: int = -1,
    evidence_dynamic: str = "N/A",
) -> dict
```

将 Phase 1 的静态分析结论和 Phase 2 的动态验证结果合并为一条报告记录。

**置信度合成规则：**

| verification_status | 最终 confidence |
|---------------------|----------------|
| `CONFIRMED` | `HIGH` |
| `PARTIAL` | `MEDIUM` |
| `REFUTED` | `LOW` |
| `PENDING` | 继承静态分析置信度 |

### `_suggest_fix()`

根据 `blocking_pattern` 自动生成修复建议：

| blocking_pattern | fix_suggestion | fix_template |
|-----------------|---------------|-------------|
| `DATABASE_QUERY` | 移至 Dispatchers.IO | `lifecycleScope.launch(Dispatchers.IO) { ... }` |
| `FILE_IO` | 移至后台线程 | `lifecycleScope.launch(Dispatchers.IO) { ... }` |
| `NETWORK` | 移至 Dispatchers.IO 或改用异步接口 | `lifecycleScope.launch(Dispatchers.IO) { ... }` |
| `SYNCHRONIZATION` | 考虑异步锁或消息队列 | `// Use Handler/MessageQueue` |
| 其他 | 移至后台线程 | `lifecycleScope.launch(Dispatchers.IO) { ... }` |

### `save_report()`

```python
def save_report(reports: List[dict], output_path: str = "root_cause_report.json") -> None
```

将报告列表序列化为 JSON（`ensure_ascii=False`，`indent=2`），并在控制台打印摘要：

```
[DroidUnblocker] Report → /path/to/root_cause_report.json
  [CONFIRMED] ANR-3F9A2C: UI 线程上执行无界全表查询 (847ms)
  [PARTIAL]   ANR-1B2D8E: initImageLoader 磁盘缓存初始化 (312ms)
```

### 报告字段说明

```json
{
  "bug_id":              "ANR-3F9A2C",         // 随机生成，格式 ANR-{6位大写十六进制}
  "confidence":          "HIGH",                // HIGH | MEDIUM | LOW
  "verification_status": "CONFIRMED",           // CONFIRMED | PARTIAL | REFUTED | PENDING
  "call_chain":          ["A.onCreate", "B.load", "C.query"],
  "root_cause":          "根因描述",
  "blocking_pattern":    "DATABASE_QUERY",
  "blocking_time_ms":    847,                   // -1 表示未测量
  "fix_suggestion":      "修复建议文字",
  "fix_template":        "lifecycleScope.launch(Dispatchers.IO) { ... }",
  "evidence": {
    "static":  "静态分析证据",
    "dynamic": "StrictMode DiskReadViolation | Blocking time: 847ms"
  }
}
```
