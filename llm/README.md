# llm — LLM 客户端

本包只负责与 OpenAI API 的底层通信，不包含任何业务逻辑或提示词内容。
各模块的 prompt 由模块自己的 `prompt/` 目录管理。

---

## 文件列表

| 文件 | 职责 |
|------|------|
| `client.py` | LLMClient：OpenAI 调用封装，含自动重试 |
| `config.py` | API Key、模型名、Base URL 等配置项 |

---

## client.py

### `LLMClient`

```python
LLMClient(api_key: str = "", model: str = "", base_url: str = "", max_retries: int = 0)
```

**唯一公开方法：**

| 方法 | 说明 |
|------|------|
| `complete(system, user, response_format)` | 通用接口，prompt 由调用方传入 |

**重试策略：**
- 最多重试 `max_retries` 次（默认从 `config.py` 读取）
- 每次失败等待 `2^attempt` 秒（指数退避）
- 所有重试失败后抛出 `RuntimeError`

**无 API Key 时的行为：**
- 构造时打印 warning，`_client` 置为 `None`
- 调用 `complete()` 时抛出 `RuntimeError`（上层捕获后降级处理）

---

## Prompt 管理

各模块自己管理自己的 prompt 文件：

| 模块 | Prompt 位置 |
|------|------------|
| ReAct 探索循环 | `core/prompt/explore_system.md`、`explore_user.md` |
| Reflection 验证 | `core/prompt/reflect_system.md`、`reflect_user.md` |
| 测试用例生成 | `tools/test_generator/prompt/system_prompt.md`、`user_prompt.md` |
| 编译错误修复 | `tools/sandbox/prompt/repair_system.md`、`repair_user.md` |
