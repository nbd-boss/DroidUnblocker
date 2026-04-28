---
name: SandboxExecutor
description: >
  在 Android 模拟器或真机上运行由 TestCaseGenerator 生成的测试用例，
  收集动态执行证据，为 Reflection 阶段提供"地面真相（ground truth）"。
  执行流程：编译-修复-重跑闭环（最多 3 轮）→ adb logcat -c 清空缓冲 →
  Gradle connectedAndroidTest 编译并运行 → adb logcat -d 抓取日志 →
  解析 StrictMode 违规和 DroidUnblocker timing tag。
  是整个系统中唯一进行动态分析的工具，是将误报率控制在 3% 以内的关键保障。
parameters:
  type: object
  properties:
    test_code:
      type: string
      description: >
        TestCaseGenerator 输出的完整 Kotlin Instrumented Test 源代码（初稿，允许存在编译错误）。
        若包含 "Could not generate test body"，执行器跳过编译直接返回空结果。
    project_dir:
      type: string
      description: >
        被测项目源码目录路径（可选）。用于在修复阶段通过 SourceCodeIndex 查找未声明方法的实现。
  required:
    - test_code
returns: >
  {
    "strict_mode_violations": [
      "StrictMode policy violation; ~duration=847ms: android.os.StrictMode$StrictModeDiskReadViolation..."
    ],
    "has_violations": true,
    "blocking_time_ms": 847,
    "systrace": "<logcat 原始输出前 2000 字符>",
    "summary": "StrictMode violations: DiskRead | Blocking time: 847ms"
  }
  — blocking_time_ms: 从 "DroidUnblocker: elapsed=Xms" tag 解析，-1 表示未找到。
  — summary 字段直接注入 Reflection 提示词，供 LLM 与静态结论对比。
  — 沙箱不可用时（ADB 未连接、超过最大修复次数仍无法编译）优雅降级，返回 summary 说明原因，不抛出异常。
usage_hints:
  - 始终在 TestCaseGenerator 之后调用（Phase 2 Reflection 流程）。
  - has_violations=true 或 blocking_time_ms > 300 → 验证结果为 CONFIRMED。
  - blocking_time_ms > 0 但无 violation → 验证结果为 PARTIAL（阻塞存在但位置有偏差）。
  - 无 violation 且 blocking_time_ms ≤ 0 → 验证结果为 REFUTED（可能是静态分析误报）。
  - systrace 字段包含原始 logcat，LLM 可与静态分析结论交叉验证阻塞堆栈位置。
  - 运行前确认：adb devices 有设备、E:/UI_Skill/agent/test-project/ 可编译。
---

## Overview

SandboxExecutor 是 DroidUnblocker 系统的最后一道质量保障，通过动态执行将 LLM 的
主观静态推理与客观运行结果对比。职责不只是"跑一次"，而是**编译-修复-重跑**的闭环执行器。

### 执行流程

```
1. 编译-修复-重跑闭环（最多 3 轮）
   ├── 写入测试文件
   │   test_code → UIBlockingTest.kt
   ├── 尝试编译（gradlew.bat assembleDebugAndroidTest，仅编译不运行）
   ├── 编译通过 → 进入步骤 2
   └── 编译失败 → compile_fixer.fix() → LLM 返回修复后完整代码 → 重试
       ├── compile_fixer 提取编译错误信息
       ├── 对 Unresolved reference: methodName，从 SourceCodeIndex 附加方法体作为参考
       └── LLM 根据错误 + 参考直接返回修复后的完整测试代码

2. 清空 logcat 缓冲
   adb logcat -c

3. 运行 Instrumented Test
   gradlew.bat connectedAndroidTest

4. 抓取 logcat
   adb logcat -d -v threadtime

5. 解析结果
   ├── DroidUnblocker timing tag → blocking_time_ms
   ├── StrictMode violation 行   → strict_mode_violations / has_violations
   └── 构建 summary              → 注入 Reflection 提示词
```

### 模块结构

| 文件 | 职责 |
|------|------|
| `sandbox.py` | 主流程：写入文件、驱动编译-修复-重跑循环、调用 Gradle、抓取 logcat、解析结果 |
| `compile_fixer.py` | 修复逻辑：解析 Gradle 错误输出，构造携带错误信息和方法体参考的 prompt，调用 LLM 返回修复后的完整测试代码 |

`compile_fixer.py` 对外暴露单一接口：
```python
def fix(test_code: str, error_output: str, index, llm_client) -> str
```
`sandbox.py` 调用该接口，`compile_fixer.py` 对 `sandbox.py` 无感知。

### 编译错误修复策略（compile_fixer.py）

修复步骤交由 LLM 完成，`compile_fixer.py` 只做两件确定性的事：

1. **解析编译错误**：从 Gradle 输出中提取 `Unresolved reference: X` 等错误信息
2. **补充方法体参考**：对于 `Unresolved reference: methodName` 类型错误，若 `methodName` 在 `SourceCodeIndex` 中存在，将对应 `MethodRecord` 的方法体附加到 prompt 中作为参考

LLM 拿到原始测试代码 + 编译错误 + 方法体参考后，直接返回修复后的完整测试代码，覆盖所有错误类型（缺少 import、类型错误、方法不存在等）。

修复最多执行 3 轮，超过后降级返回编译失败结果。

### Reflection 三种结果判定

| 条件 | 建议 verification_status |
|------|--------------------------|
| `has_violations=true` 或 `blocking_time_ms > 300` | `CONFIRMED` |
| `blocking_time_ms > 0` 但无 violation | `PARTIAL` |
| 无 violation 且 `blocking_time_ms ≤ 0` | `REFUTED` |

LLM Reflection 可在此基础上结合 systrace 堆栈进一步调整判定。

### 降级处理

若 ADB 未连接、超过最大修复轮数仍编译失败，SandboxExecutor 捕获异常后返回：
```json
{
  "strict_mode_violations": [],
  "has_violations": false,
  "blocking_time_ms": -1,
  "systrace": "",
  "summary": "Sandbox unavailable: <error message>"
}
```
Agent 继续运行，最终报告中 `evidence.dynamic` 显示 `"N/A"`，`verification_status` 为 `PENDING`。

### 环境前置要求

| 要求 | 验证命令 |
|------|---------|
| ADB 已配置并在 PATH 中 | `adb devices`（期望有设备列出） |
| Android 模拟器或真机已连接 | `adb devices` 输出包含 `device` |
| Gradle 测试项目已配置 | `E:/UI_Skill/agent/test-project/` 存在且可编译 |
