from enum import Enum
from dataclasses import dataclass, field
from typing import Any, List, Optional


class DecisionLevel(Enum):
    CONCLUDE = "CONCLUDE"
    EXPLORE = "EXPLORE"
    SHALLOW = "SHALLOW"
    MOCK = "MOCK"


class Confidence(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class VerificationStatus(Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    REFUTED = "REFUTED"


class Verdict(Enum):
    BLOCKED = "BLOCKED"  # 找到阻塞根因，进入 Phase 2
    CLEAN = "CLEAN"      # 确认无阻塞，跳过 Phase 2


@dataclass
class CalleeInfo:
    method: str
    tags: List[str] = field(default_factory=list)


@dataclass
class ShallowSummary:
    method: str
    callees: List[CalleeInfo] = field(default_factory=list)
    has_io: bool = False
    has_threading: bool = False
    has_network: bool = False
    has_database: bool = False
    has_synchronization: bool = False
    estimated_complexity: str = "low"


@dataclass
class FullExpandNode:
    signature: str
    class_name: str
    method_name: str
    tags: List[str] = field(default_factory=list)
    body_excerpt: str = ""
    callees: List["FullExpandNode"] = field(default_factory=list)
    expandable: bool = False


@dataclass
class SliceResult:
    method: str
    criterion_stmt: str
    criterion_var: str
    slice: List[str] = field(default_factory=list)
    slice_size: int = 0


@dataclass
class AnalysisConclusion:
    call_chain: List[str]
    root_cause: str
    blocking_pattern: str
    confidence: Confidence
    entry_method: str
    verdict: str = "BLOCKED"   # BLOCKED | CLEAN
    slice_evidence: str = ""


@dataclass
class SandboxResult:
    strict_mode_violations: List[str] = field(default_factory=list)
    has_violations: bool = False
    blocking_time_ms: int = -1
    systrace: str = ""
    summary: str = ""


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""
