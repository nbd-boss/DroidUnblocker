"""
LLM 客户端（OpenAI）

提供通用 complete() 接口，prompt 由各调用方模块自行管理。
"""
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False
    logger.warning("openai package not installed. LLM features disabled.")


class LLMClient:
    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        max_retries: int = 0,
        tools_description: str = "",
    ) -> None:
        import llm.config as _cfg
        self.model = model or _cfg.MODEL
        self.max_retries = max_retries or _cfg.MAX_RETRIES
        self.tools_description = tools_description
        self._client = None
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        resolved_key = api_key or _cfg.API_KEY
        resolved_url = base_url or _cfg.API_BASE_URL
        if _OPENAI_AVAILABLE and resolved_key:
            self._client = OpenAI(api_key=resolved_key, base_url=resolved_url)
        elif not resolved_key:
            logger.warning("No API key provided — LLM calls will be skipped.")

    # ── 通用完成接口 ───────────────────────────

    def complete(
        self,
        system: str,
        user: str,
        response_format: Optional[Dict] = None,
    ) -> str:
        if self._client is None:
            raise RuntimeError("LLM client not initialized (no API key or openai not installed)")

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_format:
            kwargs["response_format"] = response_format

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.chat.completions.create(**kwargs)
                if response.usage:
                    self.prompt_tokens += response.usage.prompt_tokens
                    self.completion_tokens += response.usage.completion_tokens
                    self.total_tokens += response.usage.total_tokens
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        f"LLM request failed (attempt {attempt + 1}/{self.max_retries}): "
                        f"{exc}. Retrying in {wait}s..."
                    )
                    time.sleep(wait)
        raise RuntimeError(f"LLM request failed after {self.max_retries} attempts") from last_exc

