
# Android UI 线程入口函数分析与调用图构建 Skill

## README

---

### 1. 概述

本 Skill 用于分析 Android 项目源代码，实现以下两个核心功能：

1. **构建项目级函数调用图（Function Call Graph, FCG）**
2. **识别 UI 线程入口函数**（生命周期回调、事件处理器等）

本 Skill 作为分析流水线的上游模块，为下游的向后按需切片（Backward On-Demand Slicing）等 Skill 提供调用图与 UI 入口函数作为输入。

> **当前版本限制：** 暂不处理 Lambda 表达式与方法引用。回调仅通过匿名内部类和具名类的接口实现进行解析。

---

### 2. 背景知识与定义

#### 2.1 UI 线程入口函数

在 Android 中，**UI 线程（主线程）** 是框架分发 UI 事件和生命周期回调的线程。以下类别的函数被视为 **UI 线程入口函数**：

| 类别 | 示例 |
|------|------|
| **Activity 生命周期** | `onCreate()`, `onStart()`, `onResume()`, `onPause()`, `onStop()`, `onDestroy()`, `onRestart()`, `onActivityResult()`, `onNewIntent()`, `onSaveInstanceState()`, `onRestoreInstanceState()` |
| **Fragment 生命周期** | `onAttach()`, `onCreateView()`, `onViewCreated()`, `onActivityCreated()`, `onDestroyView()`, `onDetach()` |
| **Service 生命周期（主线程部分）** | `onCreate()`, `onStartCommand()`, `onBind()`, `onUnbind()`, `onDestroy()` |
| **BroadcastReceiver** | `onReceive()` |
| **ContentProvider** | `onCreate()` |
| **Application 生命周期** | `onCreate()`, `onTerminate()`, `onConfigurationChanged()`, `onLowMemory()`, `onTrimMemory()` |
| **UI 事件处理器** | `onClick()`, `onLongClick()`, `onTouch()`, `onItemClick()`, `onItemSelected()`, `onCheckedChanged()`, `onTextChanged()`, `afterTextChanged()`, `beforeTextChanged()`, `onEditorAction()`, `onFocusChange()`, `onKey()`, `onScrollChanged()`, `onPageSelected()`, `onTabSelected()` |
| **菜单回调** | `onCreateOptionsMenu()`, `onOptionsItemSelected()`, `onContextItemSelected()`, `onCreateContextMenu()`, `onPrepareOptionsMenu()` |
| **对话框回调** | `onCreateDialog()`, `onDismiss()`, `onCancel()`, DialogInterface.OnClickListener 的 `onClick()` |
| **View 回调** | `onDraw()`, `onMeasure()`, `onLayout()`, `onSizeChanged()`, `onFinishInflate()`, `onAttachedToWindow()`, `onDetachedFromWindow()`, `onWindowFocusChanged()` |
| **RecyclerView / Adapter** | `onCreateViewHolder()`, `onBindViewHolder()`, `getView()`, `getItemCount()` |
| **Handler.handleMessage()** | 当 Handler 绑定到主线程 Looper 时 |
| **AsyncTask 回调（UI 线程部分）** | `onPreExecute()`, `onPostExecute()`, `onProgressUpdate()` |
| **权限回调** | `onRequestPermissionsResult()` |
| **导航回调** | `onBackPressed()`, `onNavigationItemSelected()` |

#### 2.2 函数调用图（FCG）

一个**有向图**，其中：
- **节点** 表示项目中的函数/方法
- **边** 表示调用关系（`调用者 → 被调用者`）

当前版本需要处理：
- 直接方法调用
- 虚方法 / 接口分派（多态）
- 匿名内部类方法调用
- 回调注册（如 `setOnClickListener(new OnClickListener() { onClick() {...} })`）

> **暂不处理：** Lambda 表达式、方法引用、Kotlin 高阶函数传递、协程挂起入口。

---

### 3. 项目结构

```
android-ui-analysis-skill/
├── README.md
├── requirements.txt
├── config/
│   ├── ui_entry_points.yaml           # 可配置的 UI 入口函数定义
│   └── android_framework_model.yaml   # Android 框架类层次结构模型
├── src/
│   ├── __init__.py
│   ├── main.py                        # Skill 入口
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── java_parser.py             # Java 源文件 AST 解析
│   │   ├── kotlin_parser.py           # Kotlin 源文件 AST 解析
│   │   ├── xml_parser.py              # XML 布局 / 清单文件解析
│   │   └── project_scanner.py         # 扫描并索引项目中所有源文件
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── class_hierarchy.py         # 类层次分析（CHA）
│   │   ├── call_graph_builder.py      # 调用图构建
│   │   ├── ui_entry_detector.py       # UI 线程入口函数检测
│   │   ├── callback_resolver.py       # 回调注册解析（匿名内部类 → 实现方法）
│   │   └── android_component_analyzer.py  # AndroidManifest + 组件类分析
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── call_graph.py              # 调用图核心数据结构
│   │   ├── graph_query.py             # 查询 API
│   │   └── graph_exporter.py          # 导出为 DOT / JSON / GraphML
│   └── utils/
│       ├── __init__.py
│       ├── logger.py
│       ├── signature.py               # 方法签名标准化
│       └── android_constants.py       # Android SDK 常量与已知框架方法
├── tests/
│   ├── test_java_parser.py
│   ├── test_kotlin_parser.py
│   ├── test_call_graph_builder.py
│   ├── test_ui_entry_detector.py
│   └── fixtures/
│       ├── SampleActivity.java
│       ├── SampleFragment.kt
│       ├── AndroidManifest.xml
│       └── activity_main.xml
└── output/
    ├── call_graph.json
    └── ui_entry_points.json
```

---

### 4. 各模块详细规格

#### 4.1 `parser/project_scanner.py` — 项目扫描器

**目标：** 递归扫描 Android 项目目录，发现所有相关源文件。

**行为：**
- 接受项目根路径作为输入
- 识别 `src/main/java/`、`src/main/kotlin/`、`src/main/res/layout/`、`AndroidManifest.xml`
- 支持单模块和多模块 Gradle 项目（扫描所有模块）
- 忽略 `build/`、`.gradle/`、`test/`、`androidTest/` 目录
- 返回结构化索引：

```python
@dataclass
class ProjectIndex:
    java_files: List[str]
    kotlin_files: List[str]
    layout_xmls: List[str]
    manifests: List[str]
```

---

#### 4.2 `parser/java_parser.py` — Java 源文件解析器

**目标：** 将 Java 源文件解析为 AST，提取结构化的方法 / 类信息。

**工具选型：** `tree-sitter` + `tree-sitter-java`（首选）；备选 `javalang`。

**对每个 Java 文件提取：**
- **类 / 接口：** 完全限定名、父类、实现的接口、修饰符
- **方法：** 签名、修饰符、方法体 AST、行号范围
- **方法内的调用点：** 被调用方法名、接收者表达式 / 类型、参数、行号
- **匿名内部类：** 所在方法、实现的接口 / 父类、重写的方法
- **字段声明：** 类型、名称（用于接收者类型推断）

**核心数据模型：**

```python
@dataclass
class ClassInfo:
    fqn: str                          # 完全限定名
    superclass: Optional[str]
    interfaces: List[str]
    modifiers: List[str]
    methods: List[MethodInfo]
    fields: List[FieldInfo]
    inner_classes: List[ClassInfo]    # 含匿名内部类
    source_file: str
    line_range: Tuple[int, int]

@dataclass
class MethodInfo:
    signature: str                    # 如 "com.example.MyActivity.onCreate(Bundle)"
    name: str
    class_fqn: str
    parameters: List[Tuple[str, str]] # [(类型, 参数名), ...]
    return_type: str
    modifiers: List[str]
    call_sites: List[CallSite]
    body_ast: Any                     # 原始 AST 节点
    line_range: Tuple[int, int]
    is_override: bool
    annotations: List[str]

@dataclass
class CallSite:
    callee_name: str
    receiver_type: Optional[str]      # 静态解析的接收者类型（尽力而为）
    receiver_expr: Optional[str]      # 原始接收者表达式文本
    arguments: List[str]
    line_number: int
    is_static: bool
    is_constructor: bool

@dataclass
class FieldInfo:
    name: str
    type_fqn: str
    modifiers: List[str]
```

---

#### 4.3 `parser/kotlin_parser.py` — Kotlin 源文件解析器

**目标：** 与 Java 解析器功能对等，但针对 Kotlin。

**工具选型：** `tree-sitter` + `tree-sitter-kotlin`。

**当前版本处理的 Kotlin 构造：**
- 普通类、`abstract class`、`open class`
- `object` 声明与 `companion object`
- `data class`、`sealed class`
- 扩展函数（映射为以扩展接收者类型为首参的静态方法）
- 方法/属性声明与调用点提取

> **暂不处理：** `suspend` 函数协程分派、高阶函数参数传递、尾随 Lambda。

输出数据模型复用 4.2 节定义的 `ClassInfo`、`MethodInfo`、`CallSite` 等。

---

#### 4.4 `parser/xml_parser.py` — XML 文件解析器

**目标：** 解析 AndroidManifest.xml 与布局 XML，提取回调绑定和组件声明。

**行为：**

- **AndroidManifest.xml：** 提取所有声明的组件及其类名和 intent-filter
- **布局 XML：** 提取 `android:onClick` 属性、自定义 View 类引用、`tools:context` 属性

**输出模型：**

```python
@dataclass
class ManifestInfo:
    package_name: str
    activities: List[ComponentDecl]
    services: List[ComponentDecl]
    receivers: List[ComponentDecl]
    providers: List[ComponentDecl]

@dataclass
class ComponentDecl:
    class_name: str                   # 完全限定类名
    intent_filters: List[dict]
    is_exported: bool
    is_main_launcher: bool

@dataclass
class LayoutCallbackBinding:
    layout_file: str
    view_id: str
    callback_method_name: str         # 来自 android:onClick
    context_activity: Optional[str]   # 来自 tools:context
```

---

#### 4.5 `analysis/class_hierarchy.py` — 类层次分析

**目标：** 构建项目及 Android 框架类的继承层次结构。

**行为：**
- 从所有 `ClassInfo` 构建继承 DAG
- 加载 `config/android_framework_model.yaml` 中的框架桩类，包括：
  - `android.app.Activity`, `androidx.appcompat.app.AppCompatActivity`
  - `android.app.Service`, `android.content.BroadcastReceiver`, `android.content.ContentProvider`
  - `android.app.Fragment`, `androidx.fragment.app.Fragment`
  - `android.view.View`, `android.view.ViewGroup`
  - `android.os.AsyncTask`, `android.os.Handler`
  - 常见监听器接口：`View.OnClickListener`, `View.OnTouchListener`, `TextWatcher`, `AdapterView.OnItemClickListener` 等

**API：**

```python
class ClassHierarchy:
    def build(self, classes: List[ClassInfo], framework_model: dict) -> None:
        """构建类层次结构"""

    def get_subtypes(self, class_fqn: str) -> Set[str]:
        """获取所有子类型（传递闭包）"""

    def get_supertypes(self, class_fqn: str) -> List[str]:
        """获取所有父类型（继承链）"""

    def is_subtype_of(self, child: str, parent: str) -> bool:
        """判断 child 是否为 parent 的子类型"""

    def resolve_virtual_dispatch(
        self, receiver_type: str, method_name: str, param_types: List[str]
    ) -> Set[str]:
        """返回所有可能的具体实现方法签名"""
```

---

#### 4.6 `analysis/callback_resolver.py` — 回调注册解析器

**目标：** 将回调注册语句解析到具体的实现方法。

**当前版本支持的模式：**

```java
// 匿名内部类
button.setOnClickListener(new View.OnClickListener() {
    public void onClick(View v) { ... }
});

// 具名类
button.setOnClickListener(new MyClickHandler());
// MyClickHandler implements View.OnClickListener
```

**行为：**
- 检测 `setXxxListener()` / `addXxxListener()` 调用
- 若参数为匿名内部类实例化 → 从匿名类中提取重写的回调方法
- 若参数为具名类实例化 → 通过类层次结构找到该类对监听器接口方法的实现
- 若参数为 `this` → 当前类自身实现了监听器接口

**输出：**
```python
@dataclass
class CallbackRegistration:
    registration_site: CallSite       # 注册调用点
    listener_interface: str           # 监听器接口 FQN
    callback_method: str              # 被解析出的回调方法签名
    impl_class: str                   # 实现类 FQN
```

> **暂不处理：** Lambda 表达式回调、方法引用回调。

---

#### 4.7 `analysis/ui_entry_detector.py` — UI 线程入口函数检测器

**目标：** 识别项目中所有的 UI 线程入口函数。

**检测规则（按优先级）：**

| # | 规则 | 置信度 |
|---|------|--------|
| 1 | 类继承 Android 组件 + 方法重写已知生命周期回调 | 1.0 |
| 2 | 方法为已知 UI 回调接口的实现（如 `OnClickListener.onClick`） | 1.0 |
| 3 | 方法名匹配布局 XML 中 `android:onClick` 绑定 | 1.0 |
| 4 | `Handler.handleMessage()` 且 Handler 绑定主线程 Looper | 0.9 |
| 5 | `AsyncTask` 子类的 `onPreExecute()` / `onPostExecute()` / `onProgressUpdate()` | 1.0 |
| 6 | 方法被 `@UiThread` / `@MainThread` 注解标记 | 1.0 |

**输出模型：**

```python
@dataclass
class UIEntryPoint:
    method_signature: str
    class_fqn: str
    category: str                     # 如 "ACTIVITY_LIFECYCLE", "CLICK_HANDLER" 等
    confidence: float
    source_file: str
    line_number: int
    details: dict                     # 附加上下文信息
```

**类别枚举：**

```python
class EntryCategory(Enum):
    ACTIVITY_LIFECYCLE = "ACTIVITY_LIFECYCLE"
    FRAGMENT_LIFECYCLE = "FRAGMENT_LIFECYCLE"
    SERVICE_LIFECYCLE = "SERVICE_LIFECYCLE"
    RECEIVER_CALLBACK = "RECEIVER_CALLBACK"
    PROVIDER_CALLBACK = "PROVIDER_CALLBACK"
    APPLICATION_LIFECYCLE = "APPLICATION_LIFECYCLE"
    CLICK_HANDLER = "CLICK_HANDLER"
    TOUCH_HANDLER = "TOUCH_HANDLER"
    UI_EVENT_HANDLER = "UI_EVENT_HANDLER"
    MENU_CALLBACK = "MENU_CALLBACK"
    DIALOG_CALLBACK = "DIALOG_CALLBACK"
    VIEW_CALLBACK = "VIEW_CALLBACK"
    ADAPTER_CALLBACK = "ADAPTER_CALLBACK"
    HANDLER_MESSAGE = "HANDLER_MESSAGE"
    ASYNC_TASK_UI_CALLBACK = "ASYNC_TASK_UI_CALLBACK"
    XML_ONCLICK = "XML_ONCLICK"
    ANNOTATION_MARKED = "ANNOTATION_MARKED"
    PERMISSION_CALLBACK = "PERMISSION_CALLBACK"
    NAVIGATION_CALLBACK = "NAVIGATION_CALLBACK"
```

---

#### 4.8 `analysis/call_graph_builder.py` — 调用图构建器

**目标：** 构建项目级函数调用图。

**算法：** 类层次分析（CHA）。

**构建步骤：**

1. **初始化节点：** 将所有已解析的方法添加为图节点
2. **处理每个方法中的调用点：**
   - **静态调用 / 构造函数调用：** 直接解析 → 添加边
   - **实例方法调用：** 使用 CHA `resolve_virtual_dispatch()` → 添加边到所有可能目标
   - **`super.method()` 调用：** 解析到直接父类方法
3. **处理回调注册：** 使用 `callback_resolver` 的结果 → 从注册所在方法添加边到回调实现方法
4. **添加框架合成边：**
   - 从合成的 `ANDROID_FRAMEWORK` 根节点到所有已声明组件的生命周期方法
   - 从 `setOnClickListener()` 调用到 `onClick()` 实现

**配置：**

```python
@dataclass
class CallGraphConfig:
    algorithm: str = "CHA"
    include_android_framework_edges: bool = True
    resolve_callbacks: bool = True
    max_virtual_dispatch_targets: int = 50
```

---

#### 4.9 `graph/call_graph.py` — 调用图核心数据结构

```python
class CallGraph:
    def add_node(self, method_signature: str, metadata: dict) -> None: ...
    def add_edge(self, caller: str, callee: str, call_site_info: dict) -> None: ...
    def get_callees(self, method_signature: str) -> List[Tuple[str, dict]]: ...
    def get_callers(self, method_signature: str) -> List[Tuple[str, dict]]: ...
    def get_all_nodes(self) -> List[str]: ...
    def get_all_edges(self) -> List[Tuple[str, str, dict]]: ...
    def get_node_metadata(self, method_signature: str) -> dict: ...
    def has_node(self, method_signature: str) -> bool: ...
    def has_edge(self, caller: str, callee: str) -> bool: ...
    def get_reachable_from(self, method_signature: str, direction: str = "forward") -> Set[str]: ...
    def get_subgraph(self, nodes: Set[str]) -> 'CallGraph': ...
    def node_count(self) -> int: ...
    def edge_count(self) -> int: ...
    def to_dict(self) -> dict: ...

    @classmethod
    def from_dict(cls, data: dict) -> 'CallGraph': ...
```

---

#### 4.10 `graph/graph_query.py` — 调用图查询工具

```python
class GraphQuery:
    def __init__(self, call_graph: CallGraph): ...

    def get_reachable_methods_from_entries(self, entry_points: List[str]) -> Set[str]:
        """从入口函数出发，获取所有正向可达的方法"""

    def get_call_chains(self, source: str, target: str, max_depth: int = 10) -> List[List[str]]:
        """查找从 source 到 target 的所有调用链"""

    def get_reverse_reachable(self, method: str) -> Set[str]:
        """获取所有能到达指定方法的调用者"""

    def get_entry_points_reaching(self, method: str, ui_entries: List[str]) -> List[str]:
        """获取能到达指定方法的所有 UI 入口函数"""
```

---

#### 4.11 `graph/graph_exporter.py` — 调用图导出

支持格式：**JSON**、**DOT**、**GraphML**。

支持按 UI 入口可达子图进行过滤导出。

---

### 5. Skill 入口与 API

```python
class AndroidUIAnalysisSkill:
    def __init__(self, project_path: str, config: Optional[dict] = None): ...

    def analyze(self) -> AnalysisResult:
        """
        运行完整分析流水线：
        1. 扫描项目文件
        2. 解析所有源文件（Java / Kotlin / XML）
        3. 构建类层次结构
        4. 解析回调注册
        5. 构建函数调用图
        6. 检测 UI 线程入口函数
        """
        ...

    def get_ui_entry_points(self) -> List[UIEntryPoint]: ...
    def get_call_graph(self) -> CallGraph: ...
    def export_results(self, output_dir: str, formats: List[str] = ["json"]) -> None: ...


@dataclass
class AnalysisResult:
    ui_entry_points: List[UIEntryPoint]
    call_graph: CallGraph
    class_hierarchy: ClassHierarchy
    project_stats: dict
```

---

### 6. 输出示例

#### 6.1 `ui_entry_points.json`

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

#### 6.2 `call_graph.json`

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
        "modifiers": ["public"],
        "annotations": ["Override"]
      }
    }
  ],
  "edges": [
    {
      "caller": "com.example.app.MainActivity.onCreate(Bundle)",
      "callee": "com.example.app.MainActivity.initViews()",
      "call_site": {
        "line_number": 30,
        "type": "DIRECT"
      }
    },
    {
      "caller": "com.example.app.MainActivity.initViews()",
      "callee": "com.example.app.MainActivity$1.onClick(View)",
      "call_site": {
        "line_number": 35,
        "type": "CALLBACK_REGISTRATION"
      }
    }
  ]
}
```

---

### 7. 依赖

```
tree-sitter>=0.20.0
tree-sitter-java>=0.20.0
tree-sitter-kotlin>=0.20.0
pyyaml>=6.0
```

---

### 8. 未来扩展（当前版本暂不实现）

- Lambda 表达式与方法引用解析
- Kotlin 协程 `suspend` 函数分派
- Kotlin 高阶函数与尾随 Lambda
- 快速类型分析（RTA）算法
- 组件间通信分析（`startActivity` → 目标 Activity）
- `Runnable.run()` 通过 `runOnUiThread()` / `View.post()` 投递的识别