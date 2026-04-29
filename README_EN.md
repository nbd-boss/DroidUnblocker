[中文](README.md) | English

<p align="center">
  <img src="DroidUnblocker.png" width="180" alt="DroidUnblocker">
</p>

<h1 align="center">DroidUnblocker Agent</h1>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-≥3.11-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/platform-Android-green.svg" alt="Platform">
</p>

An LLM-powered system for automatically localizing the root cause of Android UI thread blocking.

Given an Android project source directory, the Agent autonomously runs a full pipeline of static analysis, reasoning, and dynamic verification, producing a root-cause report down to the function call-chain level.

---

## Architecture

> **Tool-Augmented Reasoning with ReAct Loop + Reflection Verification**

```
Android project source directory
 │
 ▼ Phase 1: Exploration (ReAct Loop)
 │   SootStaticAnalyzer  →  identifies UI thread entry methods
 │   For each entry, runs ReActLoop:
 │     [Thought]     LLM reasons about the current node
 │     [Action]      selects a tool or makes a decision
 │     [Observation] updates working memory, enters next iteration
 │
 ▼ Phase 2: Reflection (Dynamic Verification)
 │   TestCaseGenerator  →  generates Kotlin Instrumented Test
 │   SandboxExecutor    →  Gradle build + ADB run + logcat parsing
 │                          (auto LLM repair on compile failure, up to 3 attempts)
 │   LLM Reflection     →  compares static conclusions with runtime data
 │
 ▼
result/root_cause_report.json
result/history/<entry_method>.json
```

---

## Project Structure

```
agent/
├── main.py                        # Entry point: two-phase orchestration + token usage stats
│
├── core/                          # ReAct engine and data models
│   ├── types.py                   # Enums + dataclasses (AnalysisConclusion, etc.)
│   ├── memory.py                  # AgentMemory: structured summary + sliding-window context
│   ├── react_loop.py              # ReActLoop: Thought→Action→Observation + P2/P4/P5 guardrails
│   └── prompt/                    # explore_system.md / explore_user.md
│                                  # reflect_system.md / reflect_user.md
│
├── tools/                         # 5 standardized Tool Skills
│   ├── registry.py                # BaseTool + ToolRegistry unified dispatch
│   ├── soot_analyzer/             # Tool 1: source code static analyzer
│   │   └── src/                   #   parser / analysis / graph / utils layers
│   ├── call_chain_expander/       # Tool 2: call chain expander (memoized tree search)
│   ├── program_slicer/            # Tool 3: program slicer
│   ├── test_generator/            # Tool 4: test case generator
│   │   └── prompt/                #   system_prompt.md / user_prompt.md
│   └── sandbox/                   # Tool 5: Android sandbox executor
│       ├── compile_fixer.py       #   LLM-driven compile error repair
│       └── prompt/                #   repair_system.md / repair_user.md
│
├── llm/                           # LLM infrastructure (pure communication layer)
│   ├── config.py                  # API Key / Base URL / model config
│   └── client.py                  # LLMClient: complete() + token counting
│
├── output/
│   └── report.py                  # Report generation and history saving
│
├── test-project/                  # Phase 2 sandbox Android project
│   └── app/src/androidTest/java/com/droidunblocker/test/
│       └── UIBlockingTest.kt      #   target file for SandboxExecutor to write test code
│
└── testcase/                      # Archive of successfully compiled test code (named by target_method)
```

---

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | ≥ 3.11 | |
| openai | ≥ 1.30.0 | LLM API client (OpenAI-compatible protocol) |
| javalang | ≥ 0.13.0 | Java source AST parsing |
| Android SDK + ADB | any | Phase 2 sandbox execution (optional) |

```bash
pip install -r requirements.txt
```

---

## Quick Start

**Step 1: Configure API**

Edit `llm/config.py`:

```python
API_KEY      = "your-api-key-here"
API_BASE_URL = "https://api.example.com/v1"
MODEL        = "qwen3.5-plus"
```

**Step 2: Run analysis**

```bash
cd E:/UI_Skill/agent

python main.py path/to/MyAndroidApp/app/src/main

# Override model or key
python main.py path/to/src --model gpt-4o --max-entries 5
python main.py path/to/src --api-key sk-...
```

> API Key priority: `--api-key` > env var `OPENAI_API_KEY` > `llm/config.py`

**Sample output**

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

Terminal also prints token usage:

```
============================================================
 Token Usage
 Prompt     : 48,302
 Completion : 6,871
 Total      : 55,173
============================================================
```

---

## ReAct Decision System

Each call node makes one of three decisions:

| Decision | Meaning | Trigger |
|----------|---------|---------|
| `CONCLUDE` | Output conclusion (verdict: `BLOCKED` / `CLEAN` / `ALL_CLEAR`) | Sufficient evidence to judge blocking status |
| `TOOL_CALL` | Expand further, layer by layer | Method internals unknown, further analysis needed |
| `MOCK` | Skip | Method not in index or clearly irrelevant |

---

## Working Memory

`AgentMemory` lifetime is scoped to a single UI entry analysis. Context is two-layered:

**Structured summary (pinned at top)**: entry method, current focus path (root to current node), mocked methods, blocking points found; path nodes include full body, off-path explored nodes show only signature and verdict, alias nodes reference their original, unexplored nodes are listed as expandable.

**Sliding window (last 15 entries)**: complete thought/action/observation history.

The tree-based context ensures the LLM always sees complete information for the current path without being distracted by unrelated branch bodies.

---

## Guardrails

| Guardrail | Purpose |
|-----------|---------|
| P2 | Hard-block re-invocation of SootStaticAnalyzer inside the ReAct loop |
| P4 | Block repeated CallChainExpander calls on already-expanded methods |
| P5 | Require at least one successfully analyzed project method before allowing a BLOCKED conclusion |

---

## Sandbox Environment

`test-project/` is a standalone Android project dedicated to Phase 2 dynamic verification:

```
test-project/
├── app/src/androidTest/java/com/droidunblocker/test/
│   └── UIBlockingTest.kt    ← SandboxExecutor writes generated test code here
├── build.gradle.kts
└── gradlew.bat
```

`SandboxExecutor` execution flow:
1. Write the Kotlin test code generated by `TestCaseGenerator` into `UIBlockingTest.kt`
2. Run `gradlew assembleDebugAndroidTest` to compile; on failure, call `compile_fixer.py` for LLM repair, up to 3 retries
3. On successful compile, run `gradlew connectedAndroidTest` on a connected device/emulator
4. Parse logcat to extract `elapsed=Xms` and StrictMode violation records
5. Successfully repaired test code is also written to `testcase/<target_method>.kt`

The test project is fully isolated from the target project. Test code inlines the target method body via `DependencyInliner`, with no dependency on the target project's build artifacts.

---

## Module Docs

- [`core/README.md`](core/README.md) — ReAct engine core
- [`tools/README.md`](tools/README.md) — 5 Tool Skills
- [`llm/README.md`](llm/README.md) — LLM client
- [`output/README.md`](output/README.md) — report generation
