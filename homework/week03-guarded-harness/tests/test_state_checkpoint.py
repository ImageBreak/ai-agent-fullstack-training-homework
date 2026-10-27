import json
import subprocess
import sys
from pathlib import Path

import pytest

from demo import script
from domain import PauseRun, Status
from harness import Harness
from state_store import StateStore


def test_c1_safe_checkpoint_contains_full_business_state(make_harness):
    h = make_harness()
    h.invoke("plan", "set_plan", {"steps": ["Inspect", "Repair", "Verify"]})
    h.invoke("read", "read_file", {"path": "src/ranges.py"})
    with pytest.raises(PauseRun):
        h.invoke("patch", "apply_patch", {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"})
    saved = h.checkpoint.load()
    assert saved.run_id == h.state.run_id
    assert saved.plan == ["Inspect", "Repair", "Verify"]
    assert saved.evidence and saved.policy.max_steps > 0
    assert saved.pending.id == "patch"
    assert saved.tool_count == 3
    assert StateStore(h.store.directory).load() == h.state


def test_c2_crash_after_write_before_result_is_unknown_and_not_replayed(make_harness):
    h = make_harness()
    args = {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"}
    with pytest.raises(PauseRun):
        h.invoke("patch", "apply_patch", args)
    h.approve("patch", "reviewer")
    code = """
import os, sys
from pathlib import Path
from harness import Harness
h = Harness.open(Path(sys.argv[1]))
h.gate.after_effect = lambda request: os._exit(86)
r = h.state.pending
h.invoke(r.id, r.name, r.arguments)
"""
    crash = subprocess.run([sys.executable, "-c", code, str(h.store.directory)])
    assert crash.returncode == 86
    restarted = Harness.open(h.store.directory)
    assert restarted.state.status == Status.UNKNOWN_RESULT
    assert restarted.state.calls["patch"].phase == "UNKNOWN_RESULT"
    changed = (Path(h.state.workspace) / "src/ranges.py").read_text()
    assert "upper - 1" not in changed
    assert restarted.run().status == Status.UNKNOWN_RESULT
    assert (Path(h.state.workspace) / "src/ranges.py").read_text() == changed
    assert restarted.state.approvals["patch"].consumed


def test_c3_new_process_resume_keeps_progress_and_does_not_rewrite(make_harness):
    h = make_harness(script=script("repair"))
    assert h.run().status == Status.WAITING_APPROVAL
    steps = h.state.step_count
    tools = h.state.tool_count
    h.approve("patch", "reviewer")
    resumed = subprocess.run(
        [sys.executable, "cli.py", "resume", str(h.store.directory)], capture_output=True, text=True
    )
    assert resumed.returncode == 0, resumed.stderr
    restarted = Harness.open(h.store.directory)
    assert restarted.state.status == Status.WAITING_APPROVAL
    assert restarted.state.pending.id == "report"
    assert restarted.state.step_count > steps
    assert restarted.state.tool_count > tools
    assert restarted.state.calls["patch"].phase == "SUCCEEDED"
    assert "upper - 1" not in (Path(h.state.workspace) / "src/ranges.py").read_text()
    approved = subprocess.run(
        [
            sys.executable,
            "cli.py",
            "approve",
            str(h.store.directory),
            "--call-id",
            "report",
            "--actor",
            "reviewer",
        ],
        capture_output=True,
        text=True,
    )
    assert approved.returncode == 0, approved.stderr
    finished = subprocess.run(
        [sys.executable, "cli.py", "resume", str(h.store.directory)], capture_output=True, text=True
    )
    assert finished.returncode == 0, finished.stderr
    final = Harness.open(h.store.directory)
    assert final.state.status == Status.COMPLETED
    writes = [
        e
        for e in final.audit.read()
        if e["event"] == "execution" and e["toolCallId"] == "patch" and e["decision"] == "SUCCEEDED"
    ]
    assert len(writes) == 1


@pytest.mark.parametrize("path", ["src/ranges.py", "artifacts/extra.md"])
def test_c4_workspace_change_invalidates_evidence_and_old_approval(make_harness, path):
    h = make_harness()
    h.invoke("read", "read_file", {"path": "src/ranges.py"})
    with pytest.raises(PauseRun):
        h.invoke("patch", "apply_patch", {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"})
    h.approve("patch", "reviewer")
    target = Path(h.state.workspace) / path
    target.write_text(target.read_text() + "\n# external edit\n" if target.exists() else "external file")
    restored = Harness.open(h.store.directory)
    assert restored.state.status == Status.NEEDS_REVIEW
    assert all(not e.valid for e in restored.state.evidence)
    assert restored.state.approvals["patch"].revoked
    assert restored.run().status == Status.NEEDS_REVIEW


def test_checkpoint_detects_corruption_and_state_store_rejects_stale_writer(make_harness):
    h = make_harness()
    stale = h.store.load()
    h.gate.persist()
    with pytest.raises(RuntimeError, match="stale"):
        h.store.save(stale)
    data = json.loads(h.checkpoint.path.read_text())
    data["state"]["step_count"] = -999
    h.checkpoint.path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="corrupted"):
        Harness.open(h.store.directory)
