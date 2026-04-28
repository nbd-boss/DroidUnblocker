---
name: KnowledgeQuery
description: >
  查询 UI 线程阻塞模式知识库。当你对某个方法的阻塞性质无法肯定时使用。
  两步交互：
    1. action=list  → 获取知识库目录，了解有哪些已知阻塞模式
    2. action=get   → 获取指定模式的完整描述（特征、典型 API、检测启发式、StrictMode 可否检测）
  先 list 浏览目录，再根据当前分析场景选择最相关的条目 get。
parameters:
  type: object
  properties:
    action:
      type: string
      enum:
        - list
        - get
      description: "list=获取目录，get=获取具体条目"
    id:
      type: string
      description: "条目 ID，action=get 时必填，如 CPU_INTENSIVE"
  required:
    - action
returns: >
  action=list: { "entries": [{"id": "...", "summary": "..."},...] }
  action=get:  完整条目（description, typical_apis, detection_keywords, severity, strictmode_detectable）
usage_hints:
  - 当对方法的阻塞性质无法肯定时调用，不必每次分析都查询。
  - 先 list 获取目录，再根据当前代码特征选择最匹配的条目 get。
  - strictmode_detectable=false 的模式在沙箱阶段只能靠 elapsed > 300ms 判定，CONCLUDE 时需在 root_cause 中注明。
---
