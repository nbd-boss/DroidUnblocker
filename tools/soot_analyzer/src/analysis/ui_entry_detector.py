import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from tools.soot_analyzer.src.analysis.class_hierarchy import ClassHierarchy
from tools.soot_analyzer.src.parser.java_parser import MethodInfo
from tools.soot_analyzer.src.parser.xml_parser import LayoutCallbackBinding
from tools.soot_analyzer.src.utils import android_constants as C

logger = logging.getLogger(__name__)


class EntryCategory(str, Enum):
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


@dataclass
class UIEntryPoint:
    method_signature: str
    class_fqn: str
    category: str
    confidence: float
    source_file: str
    line_number: int
    details: dict


def _determine_category(method: MethodInfo, hierarchy: ClassHierarchy) -> Optional[str]:
    name = method.method_name
    cn = method.class_name

    if any(a in ("MainThread", "UiThread") for a in method.annotations):
        return EntryCategory.ANNOTATION_MARKED
    if "@MainThread" in method.body or "@UiThread" in method.body:
        return EntryCategory.ANNOTATION_MARKED

    if hierarchy.any_ancestor_in(cn, C.ACTIVITY_PARENTS):
        if name in C.ACTIVITY_LIFECYCLE:
            return EntryCategory.ACTIVITY_LIFECYCLE
        if name in C.MENU_CALLBACK:
            return EntryCategory.MENU_CALLBACK
        if name in C.PERMISSION_CALLBACK:
            return EntryCategory.PERMISSION_CALLBACK
        if name in C.NAVIGATION_CALLBACK:
            return EntryCategory.NAVIGATION_CALLBACK
        if name in C.DIALOG_CALLBACK:
            return EntryCategory.DIALOG_CALLBACK

    if hierarchy.any_ancestor_in(cn, C.FRAGMENT_PARENTS):
        if name in C.FRAGMENT_LIFECYCLE or name in C.ACTIVITY_LIFECYCLE:
            return EntryCategory.FRAGMENT_LIFECYCLE
        if name in C.MENU_CALLBACK:
            return EntryCategory.MENU_CALLBACK

    if hierarchy.any_ancestor_in(cn, C.SERVICE_PARENTS):
        if name in C.SERVICE_LIFECYCLE or name == "onCreate":
            return EntryCategory.SERVICE_LIFECYCLE

    if hierarchy.is_subtype_of(cn, "BroadcastReceiver") or name == "onReceive":
        return EntryCategory.RECEIVER_CALLBACK

    if hierarchy.is_subtype_of(cn, "ContentProvider") or name in C.SYSTEM_CALLBACK:
        return EntryCategory.PROVIDER_CALLBACK

    if hierarchy.is_subtype_of(cn, "Application"):
        return EntryCategory.APPLICATION_LIFECYCLE

    if hierarchy.is_subtype_of(cn, "Handler") or name == "handleMessage":
        return EntryCategory.HANDLER_MESSAGE

    if hierarchy.is_subtype_of(cn, "AsyncTask") or name in C.ASYNC_TASK_UI:
        return EntryCategory.ASYNC_TASK_UI_CALLBACK

    if hierarchy.any_ancestor_in(cn, C.ADAPTER_PARENTS) or name in C.ADAPTER_CALLBACK:
        return EntryCategory.ADAPTER_CALLBACK

    if hierarchy.any_ancestor_in(cn, C.VIEW_PARENTS) or name in C.VIEW_CALLBACK:
        return EntryCategory.VIEW_CALLBACK

    if name == "onClick" or hierarchy.any_interface_in(cn, {"OnClickListener", "View.OnClickListener"}):
        return EntryCategory.CLICK_HANDLER

    if name == "onTouch" or hierarchy.any_interface_in(cn, {"OnTouchListener", "View.OnTouchListener"}):
        return EntryCategory.TOUCH_HANDLER

    if name in C.MENU_CALLBACK:
        return EntryCategory.MENU_CALLBACK
    if name in C.DIALOG_CALLBACK:
        return EntryCategory.DIALOG_CALLBACK
    if name in C.PERMISSION_CALLBACK:
        return EntryCategory.PERMISSION_CALLBACK
    if name in C.NAVIGATION_CALLBACK:
        return EntryCategory.NAVIGATION_CALLBACK
    if name in C.UI_EVENT_HANDLER:
        return EntryCategory.UI_EVENT_HANDLER

    return None


def detect(
    method_infos: Dict[str, MethodInfo],
    hierarchy: ClassHierarchy,
    xml_bindings: Optional[List[LayoutCallbackBinding]] = None,
    llm_client=None,
) -> List[UIEntryPoint]:
    entries: List[UIEntryPoint] = []

    # xml_onclick 方法名集合
    xml_onclick_names = {b.callback_method_name for b in (xml_bindings or [])}

    for sig, method in method_infos.items():
        category = _determine_category(method, hierarchy)

        if category is None and method.method_name in xml_onclick_names:
            category = EntryCategory.XML_ONCLICK

        if category is None:
            if _is_ambiguous(method, hierarchy) and llm_client:
                if _llm_judge(method, hierarchy, llm_client):
                    category = EntryCategory.UI_EVENT_HANDLER
                    entries.append(UIEntryPoint(
                        method_signature=sig,
                        class_fqn=method.class_name,
                        category=category,
                        confidence=0.9,
                        source_file=method.source_file,
                        line_number=method.line,
                        details={},
                    ))
            continue

        entries.append(UIEntryPoint(
            method_signature=sig,
            class_fqn=method.class_name,
            category=str(category),
            confidence=1.0,
            source_file=method.source_file,
            line_number=method.line,
            details={},
        ))

    return entries


def _is_ambiguous(method: MethodInfo, hierarchy: ClassHierarchy) -> bool:
    if method.method_name == "run":
        if hierarchy.any_interface_in(method.class_name, {"Runnable"}):
            return True
    return False


def _llm_judge(method: MethodInfo, hierarchy: ClassHierarchy, llm_client) -> bool:
    import json
    system = (
        "You are an Android UI thread analysis expert. "
        "Output ONLY valid JSON: {\"is_ui_thread\": true/false, \"reason\": \"...\"}"
    )
    user = (
        f"Class: {method.class_name}\n"
        f"Method: {method.method_name}\n"
        f"Ancestors: {hierarchy.get_ancestors(method.class_name)}\n"
        f"Interfaces: {hierarchy.get_interfaces(method.class_name)}\n"
        f"Body excerpt:\n```\n{method.body[:500]}\n```\n"
        "Is this method called on the Android UI thread?"
    )
    try:
        raw = llm_client.complete(system=system, user=user,
                                  response_format={"type": "json_object"})
        return bool(json.loads(raw).get("is_ui_thread", False))
    except Exception as e:
        logger.warning(f"LLM judge failed for {method.signature}: {e}")
        return False
