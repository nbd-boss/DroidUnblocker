from typing import Optional


def make_sig(class_name: str, method_name: str) -> str:
    return f"{class_name}.{method_name}"


def split_sig(sig: str):
    """返回 (class_name, method_name)，若无 '.' 则 class_name 为空字符串。"""
    parts = sig.rsplit(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", parts[0]


def fuzzy_match(sig: str, candidates: dict) -> Optional[str]:
    """在 candidates 的 key 中模糊匹配 sig，返回首个命中的 key，否则 None。"""
    if sig in candidates:
        return sig
    for key in candidates:
        if key.endswith("." + sig) or sig in key:
            return key
    return None
