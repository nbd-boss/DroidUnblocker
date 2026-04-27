import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_IGNORE_DIRS = {"build", ".gradle", "test", "androidTest", ".git", "generated"}


@dataclass
class ProjectIndex:
    java_files: List[str] = field(default_factory=list)
    kotlin_files: List[str] = field(default_factory=list)
    layout_xmls: List[str] = field(default_factory=list)
    manifests: List[str] = field(default_factory=list)


def scan(project_dir: str) -> ProjectIndex:
    root = Path(project_dir).resolve()
    if not root.is_dir():
        logger.error(f"project_dir does not exist or is not a directory: {root}")
        return ProjectIndex()

    index = ProjectIndex()

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # 只检查相对于 project_dir 的路径分段，避免误过滤绝对路径中的同名目录
        relative_parts = path.relative_to(root).parts
        if any(part in _IGNORE_DIRS for part in relative_parts):
            continue

        suffix = path.suffix.lower()
        name = path.name

        if suffix == ".java":
            index.java_files.append(str(path))
        elif suffix == ".kt":
            index.kotlin_files.append(str(path))
        elif name == "AndroidManifest.xml":
            index.manifests.append(str(path))
        elif suffix == ".xml" and "layout" in str(path):
            index.layout_xmls.append(str(path))

    logger.debug(
        f"Scan result: {len(index.java_files)} Java, {len(index.kotlin_files)} Kotlin, "
        f"{len(index.manifests)} manifests, {len(index.layout_xmls)} layouts"
    )
    return index
