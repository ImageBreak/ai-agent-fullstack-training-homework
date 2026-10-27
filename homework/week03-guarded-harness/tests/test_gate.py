import pytest
from conftest import approved
from smolagents.utils import AgentToolCallError

from domain import PauseRun, Status
from harness import Harness


@pytest.mark.parametrize("args", [{}, {"path": 3}, {"path": "src/ranges.py", "approved": True}])
def test_g1_schema_rejected_before_handler(make_harness, args, monkeypatch):
    h = make_harness()
    entered = []
    monkeypatch.setattr(h.agent.tools["read_file"], "forward", lambda **kw: entered.append(kw))
    with pytest.raises(AgentToolCallError, match="ARGUMENT_SCHEMA"):
        h.invoke("bad", "read_file", args)
    assert entered == []


def test_g2_third_identical_call_is_blocked(make_harness, monkeypatch):
    h = make_harness()
    tool = h.agent.tools["read_file"]
    original = tool.forward
    entered = []

    def handler(**kw):
        entered.append(kw)
        return original(**kw)

    monkeypatch.setattr(tool, "forward", handler)
    for identifier in ["a", "b"]:
        h.invoke(identifier, "read_file", {"path": "src/ranges.py"})
    with pytest.raises(AgentToolCallError, match="REPEATED_ACTION"):
        h.invoke("c", "read_file", {"path": "src/ranges.py"})
    assert len(entered) == 2


def test_g3_unapproved_write_pauses_and_changes_nothing(make_harness):
    h = make_harness()
    before = h.sandbox.snapshot()
    with pytest.raises(PauseRun):
        h.invoke("patch", "apply_patch", {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"})
    assert h.state.status == Status.WAITING_APPROVAL
    assert h.sandbox.snapshot() == before
    assert h.state.pending.id == "patch"


def test_g4_approval_is_consumed_once_and_replay_rejected(make_harness, monkeypatch):
    h = make_harness()
    entered = []
    tool = h.agent.tools["apply_patch"]
    original = tool.forward

    def handler(**kw):
        entered.append(kw)
        return original(**kw)

    monkeypatch.setattr(tool, "forward", handler)
    args = {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"}
    approved(h, "patch", "apply_patch", args)
    with pytest.raises(AgentToolCallError, match="CALL_REPLAY"):
        h.invoke("patch", "apply_patch", args)
    assert len(entered) == 1
    assert h.state.approvals["patch"].consumed


def test_approval_does_not_authorize_different_arguments(make_harness):
    h = make_harness()
    args = {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"}
    with pytest.raises(PauseRun):
        h.invoke("patch", "apply_patch", args)
    h.approve("patch", "reviewer")
    with pytest.raises(AgentToolCallError, match="CALL_ID_REBOUND"):
        h.invoke("patch", "apply_patch", {**args, "after": "upper + 1"})
    assert not h.state.approvals["patch"].consumed


def test_permission_and_understanding_mode_block_source_write(make_harness):
    args = {"path": "src/ranges.py", "before": "upper", "after": "upper + 1"}
    for h in [make_harness(permissions=["read"]), make_harness(kind="understanding")]:
        with pytest.raises(AgentToolCallError, match="PERMISSION|READ_ONLY_TASK"):
            h.invoke("write", "apply_patch", args)
        assert h.state.status == Status.RUNNING


def test_repeat_guard_survives_process_reload(make_harness):
    h = make_harness()
    for identifier in ["first", "second"]:
        h.invoke(identifier, "read_file", {"path": "src/ranges.py"})
    restored = Harness.open(h.store.directory)
    with pytest.raises(AgentToolCallError, match="REPEATED_ACTION"):
        restored.invoke("third", "read_file", {"path": "src/ranges.py"})
    assert len([c for c in restored.state.calls.values() if c.phase == "SUCCEEDED"]) == 2


def test_unknown_tool_and_expired_approval_are_fail_closed(make_harness):
    h = make_harness()
    with pytest.raises(AgentToolCallError, match="UNKNOWN_TOOL"):
        h.invoke("unknown", "shell", {"command": "echo bypass"})
    args = {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"}
    with pytest.raises(PauseRun):
        h.invoke("patch", "apply_patch", args)
    h.approve("patch", "reviewer")
    h.state.approvals["patch"].expires_at = 0
    h.gate.persist()
    with pytest.raises(PauseRun):
        h.invoke("patch", "apply_patch", args)
    assert h.state.status == Status.WAITING_APPROVAL
    assert "upper - 1" in h.sandbox.read_text("src/ranges.py")
