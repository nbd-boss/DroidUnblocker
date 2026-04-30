[English](README.md) | 中文

<p align="center">
  <img src="DroidUnblocker.png" width="180" alt="DroidUnblocker">
</p>

<h1 align="center">DroidUnblocker Agent</h1>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-≥3.11-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/platform-Android-green.svg" alt="Platform">
</p>

基于 LLM 的 Android UI 线程阻塞根因自动定位系统。

输入一个 Android 项目源码目录，Agent 自主完成静态分析、推理和动态验证的全流程，输出精确到函数调用链级别的卡顿根因报告。

---

## 核心架构

> **Tool-Augmented Reasoning with ReAct Loop + Reflection 验证**

```
Android 项目源码目录
 │
 ▼ Phase 1: Exploration（ReAct 探索循环）
 │   SootStaticAnalyzer  →  识别 UI 线程入口方法列表
 │   对每个入口运行 ReActLoop：
 │     [Thought]     LLM 推理当前节点
 │     [Action]      选择工具 / 做出决策
 │     [Observation] 更新工作记忆，进入下一轮
 │
 ▼ Phase 2: Reflection（动态验证）
 │   TestCaseGenerator  →  生成 Kotlin Instrumented Test
 │   SandboxExecutor    →  Gradle 编译 + ADB 运行 + logcat 解析
 │                          （编译失败时自动 LLM 修复，最多 3 次）
 │   LLM Reflection     →  对比静态结论与运行时数据
 │
 ▼
result/root_cause_report.json
result/history/<entry_method>.json
```

---

## 项目结构

```
agent/
├── main.py                        # 入口：两阶段流程编排 + token 消耗统计
│
├── core/                          # ReAct 引擎与数据模型
│   ├── types.py                   # 枚举 + 数据类（AnalysisConclusion、FullExpandNode 等）
│   ├── memory.py                  # AgentMemory：结构化摘要 + 滑动窗口上下文
│   ├── react_loop.py              # ReActLoop：Thought→Action→Observation + P2/P4/P5 护栏
│   └── prompt/                    # explore_system.md / explore_user.md
│                                  # reflect_system.md / reflect_user.md
│
├── tools/                         # 5 个标准化 Tool Skill
│   ├── registry.py                # BaseTool + ToolRegistry 统一调度
│   ├── soot_analyzer/             # Tool 1：源码静态分析器
│   │   └── src/                   #   parser / analysis / graph / utils 四层模块
│   ├── call_chain_expander/       # Tool 2：调用链展开器（方案C分层 + 记忆化）
│   ├── program_slicer/            # Tool 3：程序切片器
│   ├── test_generator/            # Tool 4：测试用例生成器
│   │   └── prompt/                #   system_prompt.md / user_prompt.md
│   └── sandbox/                   # Tool 5：Android 沙箱执行器
│       ├── compile_fixer.py       #   LLM 驱动的编译错误修复
│       └── prompt/                #   repair_system.md / repair_user.md
│
├── llm/                           # LLM 基础设施（纯通信层）
│   ├── config.py                  # API Key / Base URL / 模型配置
│   └── client.py                  # LLMClient：complete() + token 计数
│
├── output/
│   └── report.py                  # 报告生成与历史记录保存
│
├── test-project/                  # Phase 2 沙箱 Android 工程
│   └── app/src/androidTest/java/com/droidunblocker/test/
│       └── UIBlockingTest.kt      #   SandboxExecutor 写入测试代码的目标文件
│
└── testcase/                      # 编译通过的最终测试代码存档（按 target_method 命名）
```

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | ≥ 3.11 | |
| openai | ≥ 1.30.0 | LLM API 客户端（OpenAI 兼容协议） |
| javalang | ≥ 0.13.0 | Java 源码 AST 解析 |
| Android SDK + ADB | 任意 | Phase 2 沙箱执行（可选） |

```bash
pip install -r requirements.txt
```

---

## 快速开始

**第一步：配置 API**

编辑 `llm/config.py`：

```python
API_KEY      = "your-api-key-here"
API_BASE_URL = "https://api.example.com/v1"
MODEL        = "qwen3.5-plus"
```

**第二步：运行分析**

```bash
cd E:/UI_Skill/agent

python main.py path/to/MyAndroidApp/app/src/main

# 覆盖模型或 Key
python main.py path/to/src --model gpt-4o --max-entries 5
python main.py path/to/src --api-key sk-...
```

> API Key 优先级：`--api-key` > 环境变量 `OPENAI_API_KEY` > `llm/config.py`

**输出示例**

```json
{
  "MainActivity.onMatrixClick": [
    {
      "bug_id": "ANR-3F9A2C",
      "confidence": "HIGH",
      "verification_status": "CONFIRMED",
      "call_chain": ["MainActivity.onMatrixClick", "MatrixProcessor.runComputation"],
      "root_cause": "CPU-intensive matrix multiplication with O(n³) complexity on UI thread",
      "blocking_pattern": "CPU_INTENSIVE",
      "blocking_time_ms": 1243,
      "evidence": {
        "static": "Triple nested loop in multiply()",
        "dynamic": "Blocking time: 1243ms"
      }
    }
  ]
}
```

终端同时输出 token 消耗统计：

```
============================================================
 Token Usage
 Prompt     : 48,302
 Completion : 6,871
 Total      : 55,173
============================================================
```

---

## ReAct 循环决策体系

每个调用节点做出三选一决策：

| 决策 | 含义 | 触发条件 |
|------|------|---------|
| `CONCLUDE` | 输出结论（verdict: `BLOCKED` / `CLEAN` / `ALL_CLEAR`） | 已有足够证据判断是否阻塞 |
| `TOOL_CALL` | 继续展开，逐层按需深入 | 方法内部未知，需进一步分析 |
| `MOCK` | 跳过 | 方法不在 index 或明确无关 |

---

## 工作记忆机制

`AgentMemory` 生命周期与单个 UI 入口分析一致，上下文分两层：

**结构化摘要（置顶）**：入口方法、当前焦点路径（从根到当前节点）、已 MOCK 方法、已发现阻塞点；路径上各节点附完整 body，旁路已探索节点只显示签名和结论，alias 节点标注复用来源，未展开节点列为待探索。

**滑动窗口（最近 15 条）**：完整的 thought/action/observation 历史。

树状上下文确保 LLM 始终能看到当前路径的完整信息，同时不被无关分支的 body 干扰。

---

## 护栏机制

| 护栏 | 作用 |
|------|------|
| P2 | 硬拦截 ReAct 循环内的 SootStaticAnalyzer 重复调用 |
| P4 | 拦截对已展开过 body 的方法重复发起 CallChainExpander |
| P5 | BLOCKED 结案前要求至少成功分析过 1 个项目内方法 |

---

## 沙箱环境

`test-project/` 是一个独立的 Android 工程，专门用于 Phase 2 动态验证：

```
test-project/
├── app/src/androidTest/java/com/droidunblocker/test/
│   └── UIBlockingTest.kt    ← SandboxExecutor 写入生成的测试代码
├── build.gradle.kts
└── gradlew.bat
```

`SandboxExecutor` 的执行流程：
1. 将 `TestCaseGenerator` 生成的 Kotlin 测试代码写入 `UIBlockingTest.kt`
2. 执行 `gradlew assembleDebugAndroidTest` 编译；若失败，调用 `compile_fixer.py` LLM 修复，最多重试 3 次
3. 编译通过后执行 `gradlew connectedAndroidTest` 在连接设备/模拟器上运行
4. 解析 logcat 提取 `elapsed=Xms` 和 StrictMode 违规记录
5. 修复成功的测试代码同步覆盖写入 `testcase/<target_method>.kt`

测试项目与被测项目完全隔离，测试代码通过 `DependencyInliner` 将被测方法体内联到测试中，不依赖被测项目的编译产物。

---

## 模块文档

- [`core/README.md`](core/README.md) — ReAct 引擎核心
- [`tools/README.md`](tools/README.md) — 5 个 Tool Skill
- [`llm/README.md`](llm/README.md) — LLM 客户端
- [`output/README.md`](output/README.md) — 报告生成
