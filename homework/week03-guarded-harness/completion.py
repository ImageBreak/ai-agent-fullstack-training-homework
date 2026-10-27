"""Completion is a predicate over durable current-version evidence, not model prose."""

import hashlib
import re

from domain import Denied, RunState
from sandbox import Sandbox


def check_completion(state: RunState, sandbox: Sandbox) -> dict:
    version = sandbox.fingerprint()
    if version != state.workspace_version:
        raise Denied("STALE_WORKSPACE", "Workspace changed; evidence must be collected again")
    if any(c.phase in {"STARTED", "UNKNOWN_RESULT", "WAITING_APPROVAL"} for c in state.calls.values()):
        raise Denied("UNRESOLVED_ACTION", "An action is unfinished or awaiting review")
    evidence = [e for e in state.evidence if e.valid and e.workspace_version == version]
    report_path = f"artifacts/{state.task_kind}.md"
    try:
        report = sandbox.read_text(report_path)
    except (OSError, Denied):
        raise Denied("MISSING_DELIVERABLE", f"A verified report is required at {report_path}") from None
    report_hash = hashlib.sha256(report.encode()).hexdigest()
    if len(report.strip()) < 40 or not any(
        e.kind == "artifact" and e.data.get("sha256") == report_hash and e.data.get("path") == report_path
        for e in evidence
    ):
        raise Denied(
            "MISSING_DELIVERABLE", "Report is missing, too short, modified, or has no write evidence"
        )
    references = re.findall(r"(src/[A-Za-z0-9_./-]+\.py):(\d+)", report)
    if not references:
        raise Denied(
            "MISSING_CITATION", "Report must reference a source file and line, e.g. src/pricing.py:1"
        )
    for path, line in references:
        try:
            text = sandbox.read_text(path)
        except (OSError, Denied):
            raise Denied("INVALID_CITATION", "Report references an unreadable source file") from None
        source_hash = hashlib.sha256(text.encode()).hexdigest()
        if not 1 <= int(line) <= len(text.splitlines()) or not any(
            e.kind == "read" and e.data.get("path") == path and e.data.get("sha256") == source_hash
            for e in evidence
        ):
            raise Denied("INVALID_CITATION", "Citation needs a current read observation and a real line")
    tests = [e for e in evidence if e.kind == "test" and e.data.get("passed")]
    if not tests:
        raise Denied("TEST_EVIDENCE_REQUIRED", "Run the configured tests against the current source version")
    if state.task_kind == "understanding" and version != state.initial_version:
        raise Denied("READ_ONLY_TASK", "Understanding tasks must preserve repository sources")
    if state.task_kind == "repair" and (
        version == state.initial_version or not any(e.kind == "patch" for e in evidence)
    ):
        raise Denied("PATCH_REQUIRED", "Repair tasks require a real source change")
    return {"report": report_path, "workspace_version": version, "test_call_id": tests[-1].call_id}
