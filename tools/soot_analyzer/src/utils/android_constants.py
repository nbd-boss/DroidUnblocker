from typing import Dict, List, Set

# ── UI 线程入口方法名集合 ──────────────────────────────────────────────────────

ACTIVITY_LIFECYCLE: Set[str] = {
    "onCreate", "onStart", "onResume", "onPause", "onStop", "onDestroy",
    "onRestart", "onActivityResult", "onNewIntent",
    "onSaveInstanceState", "onRestoreInstanceState",
}
FRAGMENT_LIFECYCLE: Set[str] = {
    "onAttach", "onCreateView", "onViewCreated", "onActivityCreated",
    "onDestroyView", "onDetach",
}
SERVICE_LIFECYCLE: Set[str] = {"onStartCommand", "onBind", "onUnbind"}
APPLICATION_LIFECYCLE: Set[str] = {
    "onTerminate", "onConfigurationChanged", "onLowMemory", "onTrimMemory",
}
CLICK_HANDLER: Set[str] = {"onClick"}
TOUCH_HANDLER: Set[str] = {"onTouch"}
UI_EVENT_HANDLER: Set[str] = {
    "onLongClick", "onScrollChanged", "onItemClick", "onItemSelected",
    "onCheckedChanged", "onEditorAction", "onFocusChange", "onKey",
    "onPageSelected", "onTabSelected",
    "onTextChanged", "afterTextChanged", "beforeTextChanged",
}
MENU_CALLBACK: Set[str] = {
    "onCreateOptionsMenu", "onOptionsItemSelected", "onContextItemSelected",
    "onCreateContextMenu", "onPrepareOptionsMenu",
}
DIALOG_CALLBACK: Set[str] = {"onCreateDialog", "onDismiss", "onCancel"}
VIEW_CALLBACK: Set[str] = {
    "onDraw", "onMeasure", "onLayout", "onSizeChanged",
    "onFinishInflate", "onAttachedToWindow", "onDetachedFromWindow",
    "onWindowFocusChanged",
}
ADAPTER_CALLBACK: Set[str] = {
    "onCreateViewHolder", "onBindViewHolder", "getView", "getItemCount",
}
HANDLER_MESSAGE: Set[str] = {"handleMessage"}
ASYNC_TASK_UI: Set[str] = {"onPreExecute", "onPostExecute", "onProgressUpdate"}
PERMISSION_CALLBACK: Set[str] = {"onRequestPermissionsResult"}
NAVIGATION_CALLBACK: Set[str] = {"onBackPressed", "onNavigationItemSelected"}
SYSTEM_CALLBACK: Set[str] = {"onReceive", "query", "insert", "update", "delete"}

ALL_UI_METHODS: Set[str] = (
    ACTIVITY_LIFECYCLE | FRAGMENT_LIFECYCLE | SERVICE_LIFECYCLE
    | APPLICATION_LIFECYCLE | CLICK_HANDLER | TOUCH_HANDLER | UI_EVENT_HANDLER
    | MENU_CALLBACK | DIALOG_CALLBACK | VIEW_CALLBACK | ADAPTER_CALLBACK
    | HANDLER_MESSAGE | ASYNC_TASK_UI | PERMISSION_CALLBACK | NAVIGATION_CALLBACK
    | SYSTEM_CALLBACK
)

# ── 已知在 UI 线程上运行的父类 ────────────────────────────────────────────────

ACTIVITY_PARENTS: Set[str] = {
    "Activity", "FragmentActivity", "AppCompatActivity",
    "ListActivity", "ActionBarActivity", "ComponentActivity",
}
FRAGMENT_PARENTS: Set[str] = {
    "Fragment", "DialogFragment", "ListFragment", "PreferenceFragment",
}
SERVICE_PARENTS: Set[str] = {"Service", "IntentService", "JobIntentService"}
ADAPTER_PARENTS: Set[str] = {
    "RecyclerView.Adapter", "BaseAdapter", "ArrayAdapter",
}
VIEW_PARENTS: Set[str] = {"View", "ViewGroup", "TextView", "ImageView"}

UI_THREAD_PARENTS: Set[str] = (
    ACTIVITY_PARENTS | FRAGMENT_PARENTS | SERVICE_PARENTS
    | ADAPTER_PARENTS | VIEW_PARENTS
    | {"BroadcastReceiver", "ContentProvider", "Application", "AsyncTask", "Handler"}
)

UI_THREAD_INTERFACES: Set[str] = {
    "OnClickListener", "View.OnClickListener",
    "OnLongClickListener", "View.OnLongClickListener",
    "OnTouchListener", "View.OnTouchListener",
    "OnScrollChangeListener",
    "OnItemClickListener", "AdapterView.OnItemClickListener",
    "OnItemSelectedListener", "AdapterView.OnItemSelectedListener",
    "OnCheckedChangeListener", "CompoundButton.OnCheckedChangeListener",
    "TextWatcher",
    "OnEditorActionListener", "TextView.OnEditorActionListener",
    "OnFocusChangeListener", "View.OnFocusChangeListener",
    "OnKeyListener", "View.OnKeyListener",
    "OnMenuItemClickListener", "MenuItem.OnMenuItemClickListener",
    "DialogInterface.OnClickListener",
    "NavigationView.OnNavigationItemSelectedListener",
    "ViewPager.OnPageChangeListener",
    "TabLayout.OnTabSelectedListener",
}

# ── 风险标签检测模式（正则） ────────────────────────────────────────────────────

TAG_PATTERNS: Dict[str, List[str]] = {
    "DATABASE": [
        r'\bSQLiteDatabase\b', r'\bSQLiteOpenHelper\b', r'\bCursor\b',
        r'\bContentResolver\b', r'\bRoom\b', r'\.rawQuery\s*\(',
        r'\.execSQL\s*\(', r'@Query\b', r'\bDao\b',
    ],
    "NETWORK": [
        r'\bHttpURLConnection\b', r'\bOkHttpClient\b', r'\bRetrofit\b',
        r'\bURL\s*\(', r'\bSocket\b', r'\bVolley\b',
        r'\.openConnection\s*\(', r'\.connect\s*\(', r'\bOkHttp\b',
    ],
    "I/O": [
        r'\bFileInputStream\b', r'\bFileOutputStream\b', r'\bBufferedReader\b',
        r'\bFileReader\b', r'\bFileWriter\b', r'\bInputStream\b',
        r'\bOutputStream\b', r'\bSharedPreferences\b', r'\.openFileInput\s*\(',
    ],
    "SYNCHRONIZATION": [
        r'\bsynchronized\b', r'\bReentrantLock\b', r'\.wait\s*\(',
        r'\.notify\s*\(', r'\.notifyAll\s*\(', r'\bCountDownLatch\b',
        r'\bSemaphore\b',
    ],
}

# ── 调用提取时的噪声过滤 ────────────────────────────────────────────────────────

NOISE_RECEIVERS: Set[str] = {
    "System", "Log", "super", "String", "Math",
    "Object", "Arrays", "Collections", "TextUtils",
}
NOISE_METHODS: Set[str] = {"class", "length", "toString", "hashCode", "equals"}
