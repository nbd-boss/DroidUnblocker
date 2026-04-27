from typing import Dict, List, Optional, Set

from tools.soot_analyzer.src.parser.java_parser import ClassInfo


class ClassHierarchy:
    def __init__(self) -> None:
        self._parents: Dict[str, str] = {}
        self._interfaces: Dict[str, List[str]] = {}

    def build(self, classes: List[ClassInfo], framework_model: dict) -> None:
        for cls in classes:
            if cls.superclass:
                self._parents[cls.name] = cls.superclass
            if cls.interfaces:
                self._interfaces[cls.name] = cls.interfaces

        # 加载框架桩类（android_framework_model.yaml）
        for parent, children in framework_model.get("hierarchy", {}).items():
            for child in children:
                self._parents.setdefault(child, parent)

    def get_ancestors(self, class_name: str) -> List[str]:
        chain: List[str] = []
        visited: Set[str] = set()
        cn = class_name
        while True:
            parent = self._parents.get(cn, "")
            if not parent or parent in visited:
                break
            visited.add(parent)
            chain.append(parent)
            cn = parent
        return chain

    def get_interfaces(self, class_name: str) -> List[str]:
        return self._interfaces.get(class_name, [])

    def is_subtype_of(self, class_name: str, target: str) -> bool:
        if class_name == target:
            return True
        return target in self.get_ancestors(class_name)

    def any_ancestor_in(self, class_name: str, targets: Set[str]) -> bool:
        return bool(targets & set(self.get_ancestors(class_name)))

    def any_interface_in(self, class_name: str, targets: Set[str]) -> bool:
        return bool(targets & set(self.get_interfaces(class_name)))
