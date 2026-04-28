from enum import Enum
from dataclasses import dataclass, field
from typing import Any, List, Optional


class DecisionLevel(Enum):
    CONCLUDE = "CONCLUDE"
    EXPLORE = "EXPLORE"
    EXPAND = "EXPAND"
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
