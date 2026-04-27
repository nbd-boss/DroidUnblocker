# SandboxExecutor

在 Android 模拟器或真机上动态执行测试用例，为 Reflection 阶段提供运行时地面真相。内部包含编译-修复-重跑闭环，能自动修复 TestCaseGenerator 生成的测试代码中的编译错误。

## 输入

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `test_code` | string | 是 | TestCaseGenerator 输出的完整 Kotlin Instrumented Test 源代码（允许存在编译错误） |
| `project_dir` | string | 否 | 被测项目源码路径（当前未直接使用） |

## 输出

```json
{
  "strict_mode_violations": [
    "StrictMode policy violation; ~duration=847ms: android.os.StrictMode$StrictModeDiskReadViolation..."
  ],
  "has_violations": true,
  "blocking_time_ms": 847,
  "systrace": "<logcat 原始输出前 2000 字符>",
  "summary": "StrictMode violations: DiskRead | Blocking time: 847ms"
}
```

| 字段 | 说明 |
|------|------|
| `strict_mode_violations` | 匹配到的 StrictMode 违规日志列表 |
| `has_violations` | 是否存在 StrictMode 违规 |
| `blocking_time_ms` | 从 `DroidUnblocker: elapsed=Xms` tag 解析的耗时，-1 表示未找到 |
| `systrace` | logcat 原始输出前 2000 字符 |
| `summary` | 一行结论，直接注入 Reflection 提示词 |

## 实现逻辑

### 模块结构

| 文件 | 职责 |
|------|------|
| `sandbox.py` | 主流程：编译-修复-重跑循环、Gradle 执行、logcat 解析 |
| `compile_fixer.py` | 编译修复：解析错误、从 SourceCodeIndex 提取方法体、调用 LLM 返回修复后代码 |
| `prompt/repair_system.md` | LLM 修复任务的 system prompt |
| `prompt/repair_user.md` | LLM 修复任务的 user prompt 模板 |

### 执行流程

```
1. 编译-修复-重跑闭环（最多 3 轮）
   ├── 写入测试文件 → assembleDebugAndroidTest（仅编译）
   ├── 编译通过 → 进入步骤 2
   └── 编译失败 → compile_fixer.fix()
       ├── 从 Gradle 错误中提取 Unresolved reference
       ├── 若为项目内方法，从 SourceCodeIndex 读取方法体附加到 prompt
       └── LLM 返回修复后的完整测试代码 → 重试

2. adb logcat -c（清空缓冲）

3. gradlew connectedAndroidTest（编译 + 运行）

4. adb logcat -d -v threadtime（抓取日志）

5. 解析 logcat
   ├── DroidUnblocker: elapsed=Xms → blocking_time_ms
   ├── StrictMode violation 行     → strict_mode_violations / has_violations
   └── 构建 summary
```

### Reflection 判定参考

| 条件 | 建议 verdict |
|------|-------------|
| `has_violations=true` 或 `blocking_time_ms > 300` | `CONFIRMED` |
| `blocking_time_ms > 0` 且无 violation | `PARTIAL` |
| 无 violation 且 `blocking_time_ms ≤ 0` | `REFUTED` |

### 降级处理

ADB 未连接、Gradle 不可用、超过最大修复轮数时，返回：
```json
{ "has_violations": false, "blocking_time_ms": -1, "summary": "Sandbox unavailable: ..." }
```
不抛出异常，Reflection 阶段继续运行，`verification_status` 标记为 `PENDING`。

## 示例

**输入：**
```python
registry.execute("SandboxExecutor", {
    "test_code": "package com.droidunblocker.test\n..."
})
```

**输出（检测到阻塞）：**
```json
{
  "strict_mode_violations": [
    "StrictMode policy violation; ~duration=312ms: android.os.StrictMode$StrictModeDiskReadViolation"
  ],
  "has_violations": true,
  "blocking_time_ms": 312,
  "systrace": "04-27 18:05:17.432 ...",
  "summary": "StrictMode violations: DiskRead | Blocking time: 312ms"
}
```

**输出（无阻塞）：**
```json
{
  "strict_mode_violations": [],
  "has_violations": false,
  "blocking_time_ms": 45,
  "systrace": "...",
  "summary": "Blocking time: 45ms (no StrictMode violation)"
}
```
