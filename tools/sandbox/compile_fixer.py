"""
编译错误修复器 — CompileFixer

解析 Gradle 编译错误，构造携带错误信息和方法体参考的 prompt，
调用 LLM 返回修复后的完整测试代码。
"""
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompt"
_SYSTEM_PROMPT = (_PROMPT_DIR / "repair_system.md").read_text(encoding="utf-8").strip()
_USER_TEMPLATE = (_PROMPT_DIR / "repair_user.md").read_text(encoding="utf-8")

_UNRESOLVED_RE = re.compile(r"error: Unresolved reference: (\w+)")


def fix(test_code: str, error_output: str, index, llm_client) -> str:
    if llm_client is None:
        logger.warning("CompileFixer: no LLM client, skipping repair")
        return test_code

    method_sources_section = _build_method_sources(error_output, index)

    user_prompt = _USER_TEMPLATE.replace(
        "{test_code}", test_code
    ).replace(
        "{error_output}", error_output
    ).replace(
        "{method_sources_section}", method_sources_section
    )

    try:
        raw = llm_client.complete(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            response_format={"type": "json_object"},
        )
        data = json.loads(raw)
        fixed = data.get("fixed_code", "")
        if fixed:
            logger.info("CompileFixer: LLM repair succeeded")
            return fixed
        logger.warning("CompileFixer: LLM returned empty fixed_code")
    except Exception as e:
        logger.error(f"CompileFixer LLM error: {e}")

    return test_code


def _build_method_sources(error_output: str, index) -> str:
    if index is None:
        return ""
    refs = set(_UNRESOLVED_RE.findall(error_output))
    lines = []
    for ref in refs:
        record = index.get_method(ref)
        if record and record.body and record.file != "<external>":
            lines.append(f"\n## Source of `{ref}` ({record.file}:{record.line})")
            lines.append(f"```java\n{record.body.strip()}\n```")
    if not lines:
        return ""
    return "\n## Method Source References\n" + "\n".join(lines) + "\n"
