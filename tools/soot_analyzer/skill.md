---
name: SootStaticAnalyzer
description: >
  遍历 Android 项目源码目录，使用 AST（tree-sitter）解析 Java/Kotlin 源文件及 XML 配置，
  构建项目级函数调用图（FCG）并识别所有 UI 线程入口函数。
  分析流水线共六步：扫描文件 → AST 解析 → 构建类层次（CHA）→ 解析回调注册 → 构建调用图
  → 检测 UI 入口。结果写入 output_dir/ui_entry_points.json 和 output_dir/call_graph.json，
  供 CallChainExpander 和 ProgramSlicer 共享使用。
parameters:
  type: object
  properties:
    project_dir:
      type: string
      description: Android 项目源码目录路径（包含 .java/.kt 文件的根目录）
    output_dir:
      type: string
      description: 分析结果输出目录（可选），默认为 <agent目录>/skill1_output/
  required:
    - project_dir
returns: >
  {
    "ui_entry_points_file": "/path/to/output/ui_entry_points.json",
    "call_graph_file": "/path/to/output/call_graph.json",
    "total_entry_points": 42,
    "total_methods": 348,
    "total_edges": 1024,
    "ambiguous_resolved_by_llm": 3
  }
usage_hints:
  - 必须最先调用（Phase 1 开始时），整个 Agent 生命周期内只调用一次。
  - ui_entry_points.json：平铺的 UI 入口列表，含 category、confidence、details 字段。
  - call_graph.json：平铺调用图，含 nodes（方法节点 + 元数据）和 edges（调用边）。
  - 方法签名格式为完全限定名，如 com.example.app.MainActivity.onCreate(Bundle)。
  - LLM 判定仅针对模糊节点，规则可判定的节点不消耗模型调用。
  - 若 total_entry_points 为 0，检查 project_dir 是否包含继承自已知 Android 基类的源文件。
---

## 项目结构

```
tools/soot_analyzer/
├── skill.md
├── readme.md
├── config/
│   ├── ui_entry_points.yaml           # 可配置的 UI 入口函数定义
│   └── android_framework_model.yaml   # Android 框架类层次结构模型
└── src/
    ├── __init__.py
    ├── main.py                        # Skill 入口（AndroidUIAnalysisSkill）
    ├── parser/
    │   ├── __init__.py
    │   ├── java_parser.py             # Java 源文件 AST 解析（tree-sitter-java）
    │   ├── kotlin_parser.py           # Kotlin 源文件 AST 解析（tree-sitter-kotlin）
    │   ├── xml_parser.py              # AndroidManifest + 布局 XML 解析
    │   └── project_scanner.py         # 扫描并索引项目中所有源文件
    ├── analysis/
    │   ├── __init__.py
    │   ├── class_hierarchy.py         # 类层次分析（CHA）+ 虚方法分派解析
    │   ├── call_graph_builder.py      # 调用图构建（直接调用 + CHA + 回调边）
    │   ├── ui_entry_detector.py       # UI 线程入口函数检测（规则 + LLM）
    │   ├── callback_resolver.py       # 回调注册解析（匿名内部类 → 实现方法）
    │   └── android_component_analyzer.py  # AndroidManifest + 组件类分析
    ├── graph/
    │   ├── __init__.py
    │   ├── call_graph.py              # 调用图核心数据结构
    │   ├── graph_query.py             # 查询 API（供 CallChainExpander 使用）
    │   └── graph_exporter.py          # 导出为 JSON / DOT / GraphML
    └── utils/
        ├── __init__.py
        ├── logger.py
        ├── signature.py               # 方法签名标准化（FQN 格式）
        └── android_constants.py       # Android SDK 常量与已知框架方法
```

---

## Overview

SootStaticAnalyzer 是 DroidUnblocker 的第一个 Tool Skill，负责将 Android 源码转化为
两份结构化 JSON 文件，为后续的 ReAct 探索提供完整的数据基础。

与旧版基于正则的实现不同，当前版本使用 `tree-sitter` 进行 AST 级解析，
能够准确提取接收者类型，彻底解决变量名与类名混淆导致的 callee 签名不准问题。

---

### 分析流程

```
Android 项目源码目录
        ↓
1. 扫描项目文件（project_scanner）
   识别 .java / .kt 源文件、AndroidManifest.xml、布局 XML
        ↓
2. AST 解析（java_parser / kotlin_parser / xml_parser）
   Java/Kotlin：提取类继承、接口实现、字段声明、方法签名、调用点（含接收者类型）
   XML：提取组件声明、android:onClick 绑定
        ↓
3. 构建类层次结构（class_hierarchy，CHA）
   合并项目类 + Android 框架桩类，支持虚方法分派解析
        ↓
4. 解析回调注册（callback_resolver）
   匿名内部类 / 具名类 / this 三种模式 → 注册点到回调实现方法的边
        ↓
5. 构建函数调用图（call_graph_builder）
   直接调用 + 虚方法 CHA 分派 + 回调边 + 框架合成边
        ↓
6. 检测 UI 线程入口函数（ui_entry_detector）
   规则判定（置信度 1.0）→ LLM 判定（仅模糊节点，置信度 0.9）
        ↓
输出：
   ├── ui_entry_points.json  — UI 入口平铺列表
   └── call_graph.json       — 全量调用图（节点 + 边）
```

---

### 规则判定覆盖的入口类别

| 类别（category） | 代表方法 | 所属类条件 |
|------|---------|-----------|
| `ACTIVITY_LIFECYCLE` | `onCreate` `onResume` `onPause` `onStop` `onDestroy` `onRestart` `onActivityResult` `onNewIntent` `onSaveInstanceState` | 继承 `Activity` / `AppCompatActivity` |
| `FRAGMENT_LIFECYCLE` | `onAttach` `onCreateView` `onViewCreated` `onActivityCreated` `onDestroyView` `onDetach` | 继承 `Fragment` |
| `SERVICE_LIFECYCLE` | `onCreate` `onStartCommand` `onBind` `onUnbind` `onDestroy` | 继承 `Service` |
| `RECEIVER_CALLBACK` | `onReceive` | 继承 `BroadcastReceiver` |
| `PROVIDER_CALLBACK` | `onCreate` `query` `insert` `update` `delete` | 继承 `ContentProvider` |
| `APPLICATION_LIFECYCLE` | `onCreate` `onTerminate` `onConfigurationChanged` `onLowMemory` `onTrimMemory` | 继承 `Application` |
| `CLICK_HANDLER` | `onClick` | 实现 `OnClickListener` |
| `TOUCH_HANDLER` | `onTouch` | 实现 `OnTouchListener` |
| `UI_EVENT_HANDLER` | `onLongClick` `onCheckedChanged` `onTextChanged` `onFocusChange` 等 | 实现对应接口 |
| `MENU_CALLBACK` | `onCreateOptionsMenu` `onOptionsItemSelected` 等 | Activity / Fragment |
| `DIALOG_CALLBACK` | `onCreateDialog` `onDismiss` `onCancel` | — |
| `VIEW_CALLBACK` | `onDraw` `onMeasure` `onLayout` `onWindowFocusChanged` 等 | 继承 `View` |
| `ADAPTER_CALLBACK` | `onCreateViewHolder` `onBindViewHolder` `getView` `getItemCount` | 继承 `RecyclerView.Adapter` |
| `HANDLER_MESSAGE` | `handleMessage` | 继承 `Handler`（绑定主线程 Looper） |
| `ASYNC_TASK_UI_CALLBACK` | `onPreExecute` `onPostExecute` `onProgressUpdate` | 继承 `AsyncTask` |
| `PERMISSION_CALLBACK` | `onRequestPermissionsResult` | Activity |
| `NAVIGATION_CALLBACK` | `onBackPressed` `onNavigationItemSelected` | Activity |
| `XML_ONCLICK` | 方法名匹配布局 XML 中 `android:onClick` 属性值 | — |
| `ANNOTATION_MARKED` | 任意方法 | 标注 `@MainThread` / `@UiThread` |

### LLM 判定触发条件

- 实现了 `Runnable` 接口的 `run()` 方法（可能被 `runOnUiThread()` / `Handler.post()` 投递）
- 方法名匹配已知 UI 入口集合，但继承自非标准 Android 类的自定义组件

---

## 输出文件格式

### ui_entry_points.json

```json
{
  "project": "/path/to/android/project",
  "analysis_timestamp": "2024-01-15T10:30:00Z",
  "total_entry_points": 42,
  "entry_points": [
    {
      "method_signature": "com.example.app.MainActivity.onCreate(Bundle)",
      "class_fqn": "com.example.app.MainActivity",
      "category": "ACTIVITY_LIFECYCLE",
      "confidence": 1.0,
      "source_file": "app/src/main/java/com/example/app/MainActivity.java",
      "line_number": 25,
      "details": {
        "component_type": "Activity",
        "declared_in_manifest": true
      }
    },
    {
      "method_signature": "com.example.app.MainActivity$1.onClick(View)",
      "class_fqn": "com.example.app.MainActivity$1",
      "category": "CLICK_HANDLER",
      "confidence": 1.0,
      "source_file": "app/src/main/java/com/example/app/MainActivity.java",
      "line_number": 38,
      "details": {
        "listener_interface": "android.view.View.OnClickListener",
        "registration_site": "com.example.app.MainActivity.onCreate(Bundle):35"
      }
    }
  ]
}
```

**confidence 含义：**
- `1.0` — 规则判定（rule）
- `0.9` — LLM 判定（llm）

### call_graph.json

```json
{
  "project": "/path/to/android/project",
  "analysis_timestamp": "2024-01-15T10:30:00Z",
  "algorithm": "CHA",
  "stats": {
    "total_nodes": 356,
    "total_edges": 1024,
    "total_classes": 45,
    "total_ui_entry_points": 42
  },
  "nodes": [
    {
      "signature": "com.example.app.MainActivity.onCreate(Bundle)",
      "metadata": {
        "class_fqn": "com.example.app.MainActivity",
        "source_file": "app/src/main/java/com/example/app/MainActivity.java",
        "line_range": [25, 50],
        "is_ui_entry": true,
        "tags": [],
        "annotations": ["Override"]
      }
    },
    {
      "signature": "com.example.app.DataManager.loadUserProfile()",
      "metadata": {
        "class_fqn": "com.example.app.DataManager",
        "source_file": "app/src/main/java/com/example/app/DataManager.java",
        "line_range": [88, 102],
        "is_ui_entry": false,
        "tags": ["DATABASE"],
        "annotations": []
      }
    }
  ],
  "edges": [
    {
      "caller": "com.example.app.MainActivity.onCreate(Bundle)",
      "callee": "com.example.app.DataManager.loadUserProfile()",
      "call_site": {
        "line_number": 30,
        "type": "DIRECT"
      }
    },
    {
      "caller": "com.example.app.MainActivity.onCreate(Bundle)",
      "callee": "com.example.app.MainActivity$1.onClick(View)",
      "call_site": {
        "line_number": 35,
        "type": "CALLBACK_REGISTRATION"
      }
    }
  ]
}
```

**edge type 含义：**
- `DIRECT` — 直接方法调用
- `VIRTUAL` — 虚方法 / 接口分派（CHA 解析）
- `CALLBACK_REGISTRATION` — 回调注册（如 `setOnClickListener` → `onClick`）
- `FRAMEWORK_SYNTHETIC` — 框架合成边（如 Android 系统 → `Activity.onCreate`）

**tags 含义：**
- `DATABASE` — 涉及 SQLite / Room / ContentResolver 操作
- `NETWORK` — 涉及 HTTP / Socket 网络请求
- `I/O` — 涉及文件读写 / SharedPreferences
- `SYNCHRONIZATION` — 涉及 synchronized / Lock / wait/notify

---

## 当前版本限制

- 暂不处理 Lambda 表达式与方法引用（回调仅通过匿名内部类和具名类解析）
- 暂不处理 Kotlin 协程 `suspend` 函数分派
- 暂不处理 Kotlin 高阶函数与尾随 Lambda
- 组件间通信（`startActivity` 跨 Activity 跳转）不建立调用边
