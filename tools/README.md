# tools — 5 个 Tool Skill

本包提供 DroidUnblocker Agent 的全部工具能力：注册中心、5 个标准化 Tool Skill 及其 skill.md 元数据文件。

---

## 文件列表

| 文件 | 职责 |
|------|------|
| [`registry.py`](#registrypy) | `BaseTool` 抽象基类 + `ToolRegistry` 统一调度 |
| [`soot_analyzer.py`](#soot_analyzerpy) | Tool 1：源码静态分析器 |
| [`call_chain_expander.py`](#call_chain_expanderpy) | Tool 2：调用链展开器 |
| [`program_slicer.py`](#program_slicerpy) | Tool 3：程序切片器 |
| [`test_generator.py`](#test_generatorpy) | Tool 4：测试用例生成器 |
| [`sandbox.py`](#sandboxpy) | Tool 5：Android 沙箱执行器 |

每个工具还有独立的 `skill.md`（LLM 可读的工具元数据）：

| Skill 文件 | 说明 |
|-----------|------|
| [`soot_analyzer/skill.md`](soot_analyzer/skill.md) | 源码静态分析器元数据 |
| [`call_chain_expander/skill.md`](call_chain_expander/skill.md) | 调用链展开器元数据 |
| [`program_slicer/skill.md`](program_slicer/skill.md) | 程序切片器元数据 |
| [`test_generator/skill.md`](test_generator/skill.md) | 测试用例生成器元数据 |
| [`sandbox/skill.md`](sandbox/skill.md) | Android 沙箱执行器元数据 |

---

## registry.py

### `BaseTool`

所有工具的抽象基类，强制实现三个属性/方法：

```python
class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...            # 工具名称（LLM 调用时使用）

    @property
    @abstractmethod
    def skill_metadata(self) -> dict: ... # Skill 元数据（注入 LLM 系统提示词）

    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> ToolResult: ...
```

### `ToolRegistry`

工具注册与执行的统一入口。

```python
registry = ToolRegistry()
registry.register(SootAnalyzerTool())
registry.register(CallChainExpanderTool())

# LLM 调用工具
result: ToolResult = registry.execute("CallChainExpander", {"method": "...", "mode": "SHALLOW"})

# 生成 LLM 系统提示词片段
tools_prompt: str = registry.get_tools_prompt()
```

**`get_tools_prompt()` 输出格式：**

```
### CallChainExpander
Description: 从指定方法出发，展开调用链信息...
Parameters: {"type": "object", "properties": {...}}
Returns: SHALLOW: {...} | FULL_EXPAND: {...}
Usage hints:
  默认使用 SHALLOW 模式获取风险标签，再决定是否升级。
  ...
```

**异常处理：**
- 工具名不存在：返回 `ToolResult(success=False, error="Unknown tool: ...")`
- 工具执行抛异常：捕获并返回 `ToolResult(success=False, error=str(e))`

---

## 5 个工具概览

### Tool 1 — 源码静态分析器（`soot_analyzer.py`）

**调用时机：** Phase 1 最先调用，仅一次。

遍历 Android 项目源码目录（`.java`/`.kt`），构建 `SourceCodeIndex`：方法签名索引、类继承关系、调用图。识别 UI 线程入口方法并缓存到模块级单例 `_INDEX`，供后续工具共享。

详见 [`README_soot_analyzer.md`](README_soot_analyzer.md)

---

### Tool 2 — 调用链展开器（`call_chain_expander.py`）

**调用时机：** ReAct 循环中，对每个待决策节点调用。

两种模式：
- **SHALLOW**：单层 callee 摘要 + 规则标签（I/O / DATABASE / NETWORK / THREADING / SYNCHRONIZATION / HANDLER），零 LLM 开销
- **FULL_EXPAND**：完整两层 BFS 子树，含方法体片段

详见 [`README_call_chain_expander.md`](README_call_chain_expander.md)

---

### Tool 3 — 程序切片器（`program_slicer.py`）

**调用时机：** 确认可疑方法后，用于精确定位阻塞根因语句。

基于文本近似 PDG 后向切片（def-use 链 + 控制依赖），将上下文从数百行压缩到 5-15 条语句。

详见 [`README_program_slicer.md`](README_program_slicer.md)

---

### Tool 4 — 测试用例生成器（`test_generator.py`）

**调用时机：** Phase 2 开始时，在 CONCLUDE 决策后。

调用 LLM 生成 Kotlin Instrumented Test 测试体，嵌入 StrictMode 检测 + `DroidUnblocker: elapsed=Xms` timing tag 的固定模板。

详见 [`README_test_generator.md`](README_test_generator.md)

---

### Tool 5 — Android 沙箱执行器（`sandbox.py`）

**调用时机：** Phase 2，紧随 TestCaseGenerator 之后。

将测试代码写入 Android 测试项目，通过 `adb` + `gradlew` 在真机/模拟器上运行，解析 logcat 获取 StrictMode 违规和阻塞时间，为 Reflection 提供 ground truth。

详见 [`README_sandbox.md`](README_sandbox.md)

---

## 工具协作拓扑

```
SootStaticAnalyzer ─────────── 构建 SourceCodeIndex（_INDEX）
        │                              ↑
        │ 返回 entry_methods     ┌─────┴─────┐
        ▼                       │           │
  [ReAct 循环]           CallChainExpander  ProgramSlicer
  LLM 四级决策                  │
        │                       └── 共享 _INDEX.get_method()
        │ CONCLUDE
        ▼
  TestCaseGenerator ──── LLM 生成测试体
        │
        ▼
  SandboxExecutor ──── adb + gradle ──── logcat 解析
        │
        ▼
  LLM Reflection ──── VerificationStatus
```
