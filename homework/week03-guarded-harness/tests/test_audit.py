import json

import pytest
from conftest import ready
from smolagents.utils import AgentToolCallError

from audit import Audit


def test_a1_denial_contains_rule_decision_actor_and_tool_call_id(make_harness):
    h = make_harness()
    with pytest.raises(AgentToolCallError):
        h.invoke("rejected-call", "read_file", {"path": "artifacts/../../.env"})
    entry = next(e for e in h.audit.read() if e["decision"] == "DENY")
    assert entry["rule"] == "PATH_ESCAPE"
    assert entry["actor"] == "model"
    assert entry["toolCallId"] == "rejected-call"
    assert entry["run_id"] == h.state.run_id


def test_a2_full_chain_correlates_gate_sandbox_state_approval_and_results(make_harness):
    h = make_harness()
    ready(h)
    events = h.audit.read()
    for identifier in ["patch", "report"]:
        related = [e for e in events if e["toolCallId"] == identifier]
        assert {"gate", "sandbox", "state", "approval", "execution"} <= {e["event"] for e in related}
        assert any(e["decision"] == "CONSUMED" and e["actor"] == "test-reviewer" for e in related)
    assert any(e["event"] == "checkpoint" for e in events)
    assert any(e["event"] == "state" for e in events)
    assert all(e["run_id"] == h.state.run_id for e in events)


def test_audit_is_append_only_and_detects_tampering(make_harness):
    h = make_harness()
    prefix = h.audit.path.read_bytes()
    h.invoke("read", "read_file", {"path": "src/ranges.py"})
    assert h.audit.path.read_bytes().startswith(prefix)
    entries = h.audit.path.read_text().splitlines()
    changed = json.loads(entries[0])
    changed["actor"] = "tampered"
    entries[0] = json.dumps(changed)
    h.audit.path.write_text("\n".join(entries) + "\n")
    with pytest.raises(ValueError, match="hash"):
        Audit(h.store.directory, h.state.run_id)


def test_rejected_sensitive_parameters_are_not_written_into_audit(make_harness):
    h = make_harness()
    with pytest.raises(AgentToolCallError):
        h.invoke("injected", "read_file", {"path": "src/ranges.py", "api_key": "do-not-log-me"})
    assert "do-not-log-me" not in h.audit.path.read_text()
