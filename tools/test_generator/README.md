# TestCaseGenerator — Tool 4

根据探索阶段的根因结论，生成可在 Android 设备上直接执行的 Instrumented Test（Kotlin）。

---

## 核心思路

生成一个"既能跑起来、又用真实输入触发"的测试用例，需要同时解决两个方向的问题：

**向内展开（依赖内联）**：把目标方法的具体实现作为测试主体，递归地将其依赖的项目内方法也内联进来，直到所有依赖都是 Android SDK / Java 标准库为止。这保证测试能真实运行，不会因缺少初始化而提前失败。

**向外收集（调用场景）**：从调用图中找到目标方法的所有调用者，提取他们传入的实际参数值和调用时的上下文，作为测试的输入数据。这保证测试使用真实场景下的输入，而不是 LLM 凭空构造的假数据。

```
                    ┌─────────────────────────────────────┐
                    │          目标方法的源码               │
                    └───────────────┬─────────────────────┘
                                    │
              ┌─────────────────────┴──────────────────────┐
              │ 向内展开                                    │ 向外收集
              ▼                                             ▼
   DependencyInliner                            CallerContextCollector
   递归内联项目内依赖                             从调用图收集调用者传入的
   直到全为 SDK/外部调用                          实际参数和上下文场景
              │                                             │
              └─────────────────────┬──────────────────────┘
                                    │
                                    ▼
                          TestBodyAssembler（LLM）
                          翻译为 Kotlin + 组装前置状态 + 填入真实输入
                                    │
                                    ▼
                          test_generator.py（固定模板注入）
                                    │
                                    ▼
                             完整 .kt 测试文件
```

---

## 模块结构

单文件实现难以维护，按职责拆分为四个模块：

```
tools/test_generator/
├── test_generator.py          ← 主入口：协调各模块，注入固定模板，对外暴露 Tool 接口
├── dependency_inliner.py      ← 向内展开：递归内联项目内依赖
├── caller_context_collector.py← 向外收集：从调用图提取真实调用场景
└── test_body_assembler.py     ← 组装：将内联代码 + 场景交给 LLM 翻译为 Kotlin
```

---

## 各模块职责

### `dependency_inliner.py` — 依赖内联

**输入**：目标方法签名、index（调用图 + MethodRecord）、最大展开深度（默认 3）

**流程**：
```
target_method
    │
    ├─ 读取 record.body（方法实现）
    ├─ 扫描 body 中的 call_sites
    │      │
    │      ├─ callee 在 index 中（项目内） → 递归内联，深度 -1
    │      │       若深度耗尽 → 标记为 [MOCK: callee_sig]
    │      │
    │      └─ callee 不在 index（SDK/外部）→ 保留原调用，不展开
    │
    └─ 输出：InlinedBlock（内联后的代码块 + mock 列表）
```

**输出结构**：
```python
@dataclass
class InlinedBlock:
    target_method: str
    inlined_code: str        # 内联后的 Java 代码，含注释标注来源
    mocked_callees: List[str]  # 超出深度被 mock 的方法列表
    sdk_dependencies: List[str]  # 依赖的 SDK 调用（直接保留）
```

**终止条件**（避免无限展开）：
- callee 不在项目 index → 停止
- 当前深度 == 0 → 标记为 MOCK，停止
- callee 已被展开过（避免循环依赖）→ 停止

---

### `caller_context_collector.py` — 调用场景收集

**输入**：目标方法签名、index（调用图 + MethodRecord）

**为什么不能只提取参数表达式**：

参数表达式只是一个符号（如 `context`、`this`），不告诉 LLM 这个参数在运行时的真实状态。
调用者在调用目标方法之前往往还做了前置操作（初始化单例、设置全局变量、加载配置），
这些操作共同构成了目标方法被调用时的真实运行状态，单纯提取参数表达式会完全丢失这部分信息。

**应该提取的是**：调用者方法体中**从方法入口到目标方法调用点**的完整语句序列，
这才是能还原真实调用状态的最小前置上下文。

**流程**：
1. 从调用图中查找目标方法的所有直接调用者
2. 对每个调用者，读取其 `record.body`
3. 定位目标方法调用点在方法体中的位置
4. 提取**从方法入口到该调用点**的所有语句（即调用前的完整执行路径）

**输出结构**：
```python
@dataclass
class CallerContext:
    caller_method: str              # 调用者方法签名
    pre_call_statements: str        # 从方法入口到目标调用点的完整语句序列（源码原文）
    argument_expressions: List[str] # 实际传入的参数表达式（辅助信息）
```

**典型场景**：

```
目标方法: AndroidEnvironment.initEnvironment(Context context)

Caller 1: BootUpReceiver.onReceive
  pre_call_statements:
    // 方法入口到调用点的全部语句：
    AndroidEnvironment.initEnvironment(context);   ← 调用点（入口即调用，无前置语句）

  argument_expressions: ["context"]
  → context 是 BroadcastReceiver.onReceive 的参数，类型为 Context，无需构造

Caller 2: DNSFilterService.onCreate
  pre_call_statements:
    super.onCreate();
    AndroidEnvironment.initEnvironment(this);      ← 调用点

  argument_expressions: ["this"]
  → this 是 Service 实例，本身即 Context，测试中用模板的 context 替代
```

**有前置状态的复杂场景**：

```
目标方法: BootUpReceiver.getConfig()

Caller: BootUpReceiver.onReceive
  pre_call_statements:
    AndroidEnvironment.initEnvironment(context);   ← 前置：初始化环境（设置 WORKDIR）
    Properties config = getConfig();               ← 调用点

  → 说明 getConfig() 被调用前 WORKDIR 已被 initEnvironment 设置，
    测试体必须先调用 initEnvironment 才能让 getConfig 中的文件路径有效
```

这种情况下，仅提取参数表达式（无参）会完全遗漏 `initEnvironment` 这个关键前置操作。

---

### `test_body_assembler.py` — 测试体组装

**输入**：`InlinedBlock` + `List[CallerContext]` + `root_cause`

**职责**：调用 LLM，将内联的 Java 代码翻译为 Kotlin，并根据调用场景填入真实输入：

**LLM System prompt**：
```
You are a Kotlin Android test code generator.
Given an inlined Java implementation and real caller contexts, generate a minimal
Kotlin test body that:
1. Reproduces the exact execution path leading to the blocking operation
2. Reproduces the full pre-call state from actual callers (statements from entry to call site, not just argument values)
3. Mocks only the methods explicitly marked as [MOCK: ...]
4. Runs entirely on the calling thread — no coroutines, no new Thread()
5. Uses only Android SDK and Kotlin stdlib
Output ONLY valid JSON: { "test_body": "<kotlin, indented 8 spaces>" }
```

**LLM User prompt 结构**：
```
Root cause: <root_cause>

Inlined implementation (Java → translate to Kotlin):
<inlined_code>

Methods to mock (too deep to inline):
- <mocked_callee_1>
- <mocked_callee_2>

Real caller contexts (reproduce the full pre-call state, not just argument values):
Caller 1: BootUpReceiver.onReceive
  pre_call_statements (entry → call site):
    AndroidEnvironment.initEnvironment(context);   // call site, no prior statements
  argument: context  (BroadcastReceiver parameter → use template's context directly)

Caller 2: DNSFilterService.onCreate
  pre_call_statements (entry → call site):
    super.onCreate();
    AndroidEnvironment.initEnvironment(this);       // call site
  argument: this  (Service is-a Context → use template's context as substitute)
```

---

### `test_generator.py` — 主入口

协调三个模块，将 `test_body` 注入固定模板后输出完整 `.kt` 文件：

```python
def execute(self, params: dict) -> ToolResult:
    call_chain = params["call_chain"]
    root_cause  = params["root_cause"]
    target      = call_chain[-1]

    inlined   = DependencyInliner(index).inline(target)
    contexts  = CallerContextCollector(index).collect(target)
    test_body = TestBodyAssembler(self._llm).assemble(inlined, contexts, root_cause)
    test_code = _TEST_TEMPLATE.format(test_body=test_body)

    return ToolResult(success=True, data={"test_code": test_code, "target_method": target})
```

---

## 固定测试模板

```kotlin
package com.droidunblocker.test

import android.os.StrictMode
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class UIBlockingTest {

    @Test
    fun testForUIThreadBlocking() {
        StrictMode.setThreadPolicy(
            StrictMode.ThreadPolicy.Builder()
                .detectAll()
                .penaltyLog()
                .build()
        )

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val startTime = System.currentTimeMillis()

        // ← test_body 注入此处（缩进 8 空格）

        val elapsed = System.currentTimeMillis() - startTime
        println("DroidUnblocker: elapsed=${elapsed}ms")
        assert(elapsed < 300) { "UI thread blocked for ${elapsed}ms" }
    }
}
```

---

## 真实示例：BootUpReceiver.onReceive

### Phase 1 结论

```json
{
  "call_chain": ["BootUpReceiver.onReceive", "AndroidEnvironment.initEnvironment"],
  "root_cause": "Blocking file system I/O (getExternalFilesDirs, getExternalFilesDir, getExternalStorageDirectory) on main thread."
}
```

### DependencyInliner 输出

目标方法 `AndroidEnvironment.initEnvironment` 展开后：

```java
// ↳ [AndroidEnvironment.initEnvironment] inlined
ctx = context;
if (android.os.Build.VERSION.SDK_INT >= 19) {
    context.getExternalFilesDirs(null);   // SDK call — kept as-is
    File dir = context.getExternalFilesDir(null);  // SDK call — kept as-is
    if (dir != null)
        WORKDIR = dir.getAbsolutePath() + "/PersonalDNSFilter";
    // ...
}
// mocked_callees: []   （全为 SDK 调用，无需 mock）
// sdk_dependencies: [Context.getExternalFilesDirs, Context.getExternalFilesDir,
//                    Environment.getExternalStorageDirectory]
```

### CallerContextCollector 输出

```
Caller: BootUpReceiver.onReceive
  argument[0]: context  (Context，来自 BroadcastReceiver.onReceive 参数)
  excerpt: AndroidEnvironment.initEnvironment(context);
```

### LLM 生成的 test_body

```kotlin
        // Inlined: AndroidEnvironment.initEnvironment(context)
        // Caller context: context from BroadcastReceiver — use template's context directly
        AndroidEnvironment.initEnvironment(context)
```

本例依赖全为 SDK，内联结果简洁；若目标方法有项目内依赖（如 `getConfig()` 内部的
`ExecutionEnvironment.getWorkDir()`），`DependencyInliner` 会继续向内展开，
直到所有依赖都落在 SDK 边界或触发 MOCK 为止。

### 预期运行结果

- `StrictMode` 在 `getExternalFilesDirs` 触发 `DiskReadViolation`
- `elapsed > 300ms` → 断言失败
- Phase 2 Reflection 判定 `CONFIRMED`

---

## 局限性

| 局限 | 说明 |
|------|------|
| 内联深度有上限 | 超出 max_depth 的依赖改为 mock，mock 数量多时测试真实性下降 |
| 循环依赖 | A 调用 B、B 调用 A 时，第二次遇到标记为 [CYCLE: ...] 并停止，不走 mock 列表 |
| Java → Kotlin 翻译 | 复杂泛型、checked exception、lambda 可能出错 |
| 无编译验证 | 生成后直接交 SandboxExecutor，编译失败才能发现 |
| 私有方法 | 目标为 private 时需反射，生成复杂度上升 |
| 300ms 阈值固定 | 快速设备上的轻量 I/O 可能低于此值 |
