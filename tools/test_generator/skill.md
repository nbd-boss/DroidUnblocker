---
name: TestCaseGenerator
description: >
  根据 ReAct 探索阶段给出的根因结论，生成一个可执行的
  Android Instrumented Test（Kotlin）。
  内部由三个子模块协作完成：
  (1) DependencyInliner 递归内联目标方法的项目内依赖，直到所有调用均落在 SDK 边界；
  (2) CallerContextCollector 从调用图反向找到所有调用者，提取从方法入口到调用点的
      完整前置语句序列（还原真实调用状态，而非仅提取参数表达式）；
  (3) TestBodyAssembler 将内联代码与调用场景交给 LLM，翻译为 Kotlin 测试体。
  生成的测试在主线程上直接调用目标方法，挂载 StrictMode 检测主线程 I/O / 数据库违规，
  并通过 DroidUnblocker timing tag 记录 wall-clock 执行耗时，供 SandboxExecutor 解析。
parameters:
  type: object
  properties:
    call_chain:
      type: array
      items:
        type: string
      description: >
        从 UI 线程入口到阻塞方法的完整调用链（方法签名列表）。
        DependencyInliner 以 call_chain 末端方法为起点向内展开依赖；
        CallerContextCollector 以末端方法为目标向外收集调用者上下文。
    root_cause:
      type: string
      description: >
        根因描述文字，引导 LLM 理解阻塞场景并生成有针对性的测试逻辑。
        例如："UI 线程上执行文件系统 I/O（getExternalFilesDirs）导致主线程阻塞"。
  required:
    - call_chain
    - root_cause
returns: >
  {
    "test_code": "<完整 Kotlin Instrumented Test 源文件内容>",
    "target_method": "<call_chain 最后一个方法签名>"
  }
  — test_code 可直接写入 Android 测试项目并由 Gradle 编译运行。
  — 若 LLM 无法生成有效测试体，test_code 包含 "// Could not generate test body"，
    SandboxExecutor 收到后跳过编译，验证状态标为 PENDING。
usage_hints:
  - 仅在探索阶段得出 CONCLUDE 决策后调用，不要在探索过程中调用。
  - 专属于 Phase 2（Reflection 验证阶段），是 SandboxExecutor 的前置步骤。
  - call_chain 直接取 CONCLUDE action 中的字段，无需手动构造。
  - DependencyInliner 最大展开深度默认为 3；超出深度的依赖以 mock 替代，mock 过多会降低测试真实性。
  - CallerContextCollector 提取的是调用者从入口到调用点的完整语句序列，而非仅参数表达式；
    这是还原真实运行状态的关键，尤其当目标方法依赖全局单例或需要前置初始化时。
  - 每次验证只调用一次，生成的测试代码会被 SandboxExecutor 覆盖写入同一测试文件。
---

## Overview

TestCaseGenerator 将静态分析结论转化为可执行的 Android Instrumented Test，
是 Reflection 验证阶段的第一步。核心目标是生成"既能跑起来、又能用真实输入触发阻塞"的测试用例。

---

### 内部模块流程

```
call_chain[-1]（目标方法）
        │
        ├──→ DependencyInliner
        │       递归内联项目内依赖（最大深度 3）
        │       超出深度 → 标记 [MOCK: callee_sig]
        │       输出：InlinedBlock（内联代码 + mock 列表）
        │
        └──→ CallerContextCollector
                反向查找调用图中的所有调用者
                提取每个调用者：方法入口 → 目标调用点 的完整语句序列
                输出：List[CallerContext]（前置语句 + 参数表达式）
                        │
                        ▼
              TestBodyAssembler（LLM）
              以内联代码为执行骨架，以调用者前置语句还原真实状态
              翻译为 Kotlin，补全 mock，输出 test_body
                        │
                        ▼
              test_generator.py 注入固定模板 → 完整 .kt 文件
```

---

### 生成的测试代码结构

```kotlin
@RunWith(AndroidJUnit4::class)
class UIBlockingTest {

    @Test
    fun testForUIThreadBlocking() {
        // 1. 挂载 StrictMode — 自动检测主线程 IO / 数据库违规
        StrictMode.setThreadPolicy(
            StrictMode.ThreadPolicy.Builder().detectAll().penaltyLog().build()
        )

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val startTime = System.currentTimeMillis()

        // 2. 【LLM 生成的测试逻辑】
        //    · 按 CallerContext.pre_call_statements 还原调用前状态
        //    · 按 InlinedBlock.inlined_code 翻译为 Kotlin 执行骨架
        //    · 对 [MOCK: ...] 标记的方法插入 mock 实现
        //    · 全程在主线程执行，禁止 launch / Thread / AsyncTask
        ...

        // 3. 记录耗时 — DroidUnblocker timing tag 供 SandboxExecutor 解析
        val elapsed = System.currentTimeMillis() - startTime
        println("DroidUnblocker: elapsed=${elapsed}ms")
        assert(elapsed < 300) { "UI thread blocked for ${elapsed}ms" }
    }
}
```

---

### CallerContext 说明

只提取参数表达式是不够的——参数符号（如 `context`、`this`）不包含调用者在调用目标方法
之前的前置操作（初始化单例、设置全局变量、加载配置文件等），而这些前置操作是目标方法能
正确运行的必要条件。

CallerContextCollector 提取的是**从调用者方法入口到目标方法调用点的完整语句序列**：

```
Caller: BootUpReceiver.onReceive
pre_call_statements:
  AndroidEnvironment.initEnvironment(context);  ← 调用点，入口即调用
→ 无需额外前置操作，context 直接使用模板变量

Caller: BootUpReceiver.onReceive（目标为 getConfig）
pre_call_statements:
  AndroidEnvironment.initEnvironment(context);  ← 前置：初始化 WORKDIR
  Properties config = getConfig();              ← 调用点
→ 测试体必须先调用 initEnvironment，否则 getConfig 中文件路径无效
```

---

### LLM Prompt 约束

| 约束 | 原因 |
|------|------|
| 在主线程直接调用（禁止 `launch` / `Thread` / `AsyncTask`） | 确保触发主线程阻塞 |
| 只用 Android SDK 和 Kotlin stdlib | 保证在任意 Android 项目可编译 |
| 按 CallerContext 前置语句还原调用状态 | 避免因缺少初始化而提前失败 |
| 只 mock `[MOCK: ...]` 标记的方法 | 其余方法已内联，不应重复 mock |
| 只生成测试方法体内部逻辑（8 格缩进） | 外层框架代码固定包裹 |

---

### 测试代码生命周期

```
TestCaseGenerator → test_code
      ↓
SandboxExecutor 写入 UIBlockingTest.kt
      ↓
Gradle connectedAndroidTest 编译并运行
      ↓
logcat 捕获 StrictMode violation + "DroidUnblocker: elapsed=Xms"
      ↓
SandboxExecutor 解析结果 → LLM Reflection 输入
```
