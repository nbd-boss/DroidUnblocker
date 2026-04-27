import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

_ANDROID_NS = "http://schemas.android.com/apk/res/android"


@dataclass
class ComponentDecl:
    class_name: str
    is_main_launcher: bool = False


@dataclass
class ManifestInfo:
    package_name: str
    activities: List[ComponentDecl] = field(default_factory=list)
    services: List[ComponentDecl] = field(default_factory=list)
    receivers: List[ComponentDecl] = field(default_factory=list)
    providers: List[ComponentDecl] = field(default_factory=list)


@dataclass
class LayoutCallbackBinding:
    layout_file: str
    callback_method_name: str
    context_activity: Optional[str] = None


def parse_manifest(filepath: str) -> Optional[ManifestInfo]:
    try:
        tree = ElementTree.parse(filepath)
    except ElementTree.ParseError as e:
        logger.debug(f"XML parse error in {filepath}: {e}")
        return None

    root = tree.getroot()
    package = root.attrib.get("package", "")
    info = ManifestInfo(package_name=package)

    def _resolve(name: str) -> str:
        if name.startswith("."):
            return package + name
        return name

    def _is_main_launcher(elem) -> bool:
        for intent in elem.findall("intent-filter"):
            actions = [c.attrib.get(f"{{{_ANDROID_NS}}}name", "") for c in intent.findall("action")]
            categories = [c.attrib.get(f"{{{_ANDROID_NS}}}name", "") for c in intent.findall("category")]
            if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in categories:
                return True
        return False

    for tag, target in [
        ("activity", info.activities),
        ("service", info.services),
        ("receiver", info.receivers),
        ("provider", info.providers),
    ]:
        for elem in root.iter(tag):
            name = elem.attrib.get(f"{{{_ANDROID_NS}}}name", "")
            if name:
                target.append(ComponentDecl(
                    class_name=_resolve(name),
                    is_main_launcher=_is_main_launcher(elem),
                ))

    return info


def parse_layout(filepath: str) -> List[LayoutCallbackBinding]:
    try:
        tree = ElementTree.parse(filepath)
    except ElementTree.ParseError as e:
        logger.debug(f"XML parse error in {filepath}: {e}")
        return []

    bindings: List[LayoutCallbackBinding] = []
    root = tree.getroot()
    layout_name = Path(filepath).name

    for elem in root.iter():
        on_click = elem.attrib.get(f"{{{_ANDROID_NS}}}onClick")
        if on_click:
            context = elem.attrib.get(
                "{http://schemas.android.com/tools}context"
            )
            bindings.append(LayoutCallbackBinding(
                layout_file=layout_name,
                callback_method_name=on_click,
                context_activity=context,
            ))

    return bindings
