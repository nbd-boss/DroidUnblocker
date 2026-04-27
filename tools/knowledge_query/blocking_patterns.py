"""
阻塞模式知识库数据

每个条目描述一种 UI 线程阻塞模式，包含特征、典型 API、检测关键词、
危险等级及 StrictMode 是否可检测。
"""

ENTRIES = {
    "FILE_IO": {
        "id": "FILE_IO",
        "summary": "文件系统访问，StrictMode 可检测",
        "description": (
            "直接在 UI 线程上访问文件系统。耗时取决于存储速度，"
            "外部存储（SD 卡）可能极慢。Android StrictMode 会触发 DiskReadViolation / DiskWriteViolation。"
        ),
        "typical_apis": [
            "FileInputStream", "FileOutputStream", "BufferedReader", "FileReader", "FileWriter",
            "Context.getExternalFilesDirs", "Context.getExternalFilesDir",
            "Context.openFileInput", "Context.openFileOutput",
            "Environment.getExternalStorageDirectory",
        ],
        "detection_keywords": [
            "FileInputStream", "FileOutputStream", "BufferedReader", "FileReader", "FileWriter",
            "getExternalFilesDirs", "getExternalFilesDir", "openFileInput", "openFileOutput",
            "getExternalStorageDirectory", "listFiles", "File(",
        ],
        "severity": "HIGH",
        "strictmode_detectable": True,
        "strictmode_violation": "DiskReadViolation / DiskWriteViolation",
    },
    "DATABASE": {
        "id": "DATABASE",
        "summary": "SQL 查询/写入，StrictMode 可检测",
        "description": (
            "在 UI 线程上执行 SQLite 数据库操作。耗时取决于数据量和查询复杂度。"
            "StrictMode 会触发 DiskReadViolation（数据库读取本质是磁盘 I/O）。"
        ),
        "typical_apis": [
            "SQLiteDatabase.rawQuery", "SQLiteDatabase.execSQL",
            "SQLiteOpenHelper", "Cursor", "ContentResolver",
            "Room @Query", "Room @Insert", "Room @Delete",
        ],
        "detection_keywords": [
            "rawQuery", "execSQL", "SQLiteDatabase", "SQLiteOpenHelper",
            "Cursor", "ContentResolver", "@Query", "getWritableDatabase", "getReadableDatabase",
        ],
        "severity": "HIGH",
        "strictmode_detectable": True,
        "strictmode_violation": "DiskReadViolation",
    },
    "NETWORK": {
        "id": "NETWORK",
        "summary": "网络 I/O，Android 4.0+ 主线程调用直接抛异常",
        "description": (
            "在 UI 线程上发起网络请求。Android 4.0+ 会直接抛出 NetworkOnMainThreadException，"
            "导致应用崩溃而非仅卡顿。耗时完全不确定。"
        ),
        "typical_apis": [
            "HttpURLConnection", "OkHttpClient", "Retrofit",
            "URL.openConnection", "Socket", "Volley",
        ],
        "detection_keywords": [
            "HttpURLConnection", "OkHttpClient", "openConnection", "connect",
            "URL(", "Socket(", "Retrofit", "Volley",
        ],
        "severity": "CRITICAL",
        "strictmode_detectable": False,
        "strictmode_violation": "NetworkOnMainThreadException（崩溃，非 StrictMode）",
    },
    "CPU_INTENSIVE": {
        "id": "CPU_INTENSIVE",
        "summary": "纯计算密集，StrictMode 不可检测，只能靠耗时判定",
        "description": (
            "在 UI 线程上执行大量纯 CPU 计算，无文件/网络/数据库访问。"
            "StrictMode 无法检测此类操作，只能通过 elapsed > 300ms 判定是否阻塞。"
            "典型场景：矩阵运算、大数据集排序、深度递归、图像像素处理、大 JSON 解析。"
        ),
        "typical_apis": [],
        "detection_keywords": [
            "for.*for", "while.*while", "递归", "matrix", "sort", "Arrays.sort",
            "Collections.sort", "bitmap", "BitmapFactory", "JSONArray", "JSONObject",
        ],
        "detection_heuristics": [
            "方法体中存在多层嵌套循环（O(n²) 或更高）",
            "方法递归调用自身且无明确终止深度限制",
            "操作对象为大数组、大集合或高分辨率 Bitmap",
            "调用链末端无任何 I/O API，但调用链深度 > 3",
        ],
        "severity": "MEDIUM",
        "strictmode_detectable": False,
        "strictmode_violation": "无，只能通过 elapsed > 300ms 在沙箱阶段判定",
    },
    "SYNCHRONIZATION": {
        "id": "SYNCHRONIZATION",
        "summary": "锁等待，单线程环境下不触发，多线程下可能长时间阻塞",
        "description": (
            "UI 线程持有或等待某个锁，若其他线程长时间持锁则导致 UI 线程挂起。"
            "在单线程测试环境下通常不触发阻塞，沙箱验证效果有限。"
            "StrictMode 不检测此类操作。"
        ),
        "typical_apis": [
            "synchronized", "ReentrantLock", "CountDownLatch",
            "Semaphore", "Object.wait", "Object.notify",
        ],
        "detection_keywords": [
            "synchronized", "ReentrantLock", "CountDownLatch",
            "Semaphore", ".wait(", ".notify(", ".notifyAll(",
        ],
        "severity": "MEDIUM",
        "strictmode_detectable": False,
        "strictmode_violation": "无，沙箱单线程环境下通常不触发",
    },
}

METADATA = [
    {"id": k, "summary": v["summary"]}
    for k, v in ENTRIES.items()
]
