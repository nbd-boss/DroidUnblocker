"""
根因分析报告生成器

将 AnalysisConclusion + 动态验证结果组装为最终 JSON 报告。
"""
import datetime
import json
import logging
import re
import uuid
from pathlib import Path
from typing import List, Optional

from core.types import AnalysisConclusion, Confidence, VerificationStatus

logger = logging.getLogger(__name__)


def build_report(
    conclusion: AnalysisConclusion,
    verification_status: VerificationStatus,
    blocking_time_ms: int = -1,
    evidence_dynamic: str = "N/A",
) -> dict:
    bug_id = f"ANR-{uuid.uuid4().hex[:6].upper()}"

    # 综合静态置信度 + 验证结果确定最终置信度
    if verification_status == VerificationStatus.CONFIRMED:
        final_confidence = "HIGH"
    elif verification_status == VerificationStatus.PARTIAL:
        final_confidence = "MEDIUM"
    elif verification_status == VerificationStatus.REFUTED:
        final_confidence = "LOW"
    else:
        final_confidence = {
            Confidence.HIGH: "HIGH",
            Confidence.MEDIUM: "MEDIUM",
            Confidence.LOW: "LOW",
        }.get(conclusion.confidence, "MEDIUM")

    return {
        "bug_id": bug_id,
        "confidence": final_confidence,
        "verification_status": verification_status.value,
        "call_chain": conclusion.call_chain,
        "root_cause": conclusion.root_cause,
        "blocking_pattern": conclusion.blocking_pattern,
        "blocking_time_ms": blocking_time_ms,
        "evidence": {
            "static": conclusion.slice_evidence or conclusion.root_cause,
            "dynamic": evidence_dynamic,
        },
    }



def save_history(entry_method: str, memory, conclusions, output_dir: str) -> None:
    safe_name = re.sub(r'[^\w]', '_', entry_method)
    path = Path(output_dir) / "history" / f"{safe_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    steps = [{"role": e.role, "content": e.content} for e in memory.entries]

    if isinstance(conclusions, list):
        conclusion_data = [
            {
                "call_chain": c.call_chain,
                "root_cause": c.root_cause,
                "blocking_pattern": c.blocking_pattern,
                "confidence": c.confidence.value,
                "verdict": c.verdict,
            }
            for c in conclusions
        ] if conclusions else None
    else:
        c = conclusions
        conclusion_data = {
            "call_chain": c.call_chain,
            "root_cause": c.root_cause,
            "blocking_pattern": c.blocking_pattern,
            "confidence": c.confidence.value,
        } if c else None

    record = {
        "entry_method": entry_method,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "total_steps": len(steps),
        "conclusions": conclusion_data,
        "steps": steps,
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"History saved: {path}")


def save_report(reports: dict, output_path: str = "result/root_cause_report.json") -> None:
    path = Path(output_path)
    path.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"Report saved: {path.absolute()}")
    print(f"\n[DroidUnblocker] Report → {path.absolute()}")
    if not reports:
        print("  (no blocking patterns found)")
        return
    for entry_method, entry_reports in reports.items():
        print(f"\n  Entry: {entry_method}")
        for r in entry_reports:
            status = r["verification_status"]
            bug_id = r["bug_id"]
            root = r["root_cause"]
            ms = r["blocking_time_ms"]
            time_str = f"{ms}ms" if ms > 0 else "N/A"
            print(f"    [{status}] {bug_id}: {root} ({time_str})")
