import pytest
from smolagents import ToolCallingAgent

from demo import script
from domain import Status, StopReason
from harness import Harness


@pytest.mark.parametrize("kind", ["understanding", "repair"])
def test_real_tool_calling_agent_runs_both_tasks_with_fake_model(make_harness, kind):
    h = make_harness(kind=kind, script=script(kind))
    assert isinstance(h.agent, ToolCallingAgent)
    pauses = []
    while h.run().status == Status.WAITING_APPROVAL:
        pauses.append(h.state.pending.id)
        h.approve(h.state.pending.id, "integration-reviewer")
    assert h.state.status == Status.COMPLETED
    assert pauses == (["patch", "report"] if kind == "repair" else ["report"])
    assert h.state.final_answer
    assert any(e["event"] == "completion" for e in h.audit.read())


def test_bad_arguments_are_observed_by_model_then_corrected(make_harness):
    actions = [
        {"name": "read_file", "arguments": {"path": 4}},
        {"name": "read_file", "arguments": {"path": "src/ranges.py"}},
    ]
    h = make_harness(script=actions, max_steps=2)
    h.run()
    assert h.state.stop_reason == StopReason.STEP_LIMIT
    assert len(h.model.received_messages) == 2
    assert "ARGUMENT_SCHEMA" in str(h.model.received_messages[1])
    assert len([e for e in h.state.evidence if e.kind == "read"]) == 1


def test_batch_cannot_execute_write_and_another_tool_in_same_step(make_harness):
    h = make_harness(
        script=[
            {
                "batch": [
                    {"name": "read_file", "arguments": {"path": "src/ranges.py"}},
                    {
                        "name": "apply_patch",
                        "arguments": {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"},
                    },
                ]
            }
        ],
        max_steps=1,
    )
    h.run()
    assert not h.state.calls
    assert h.state.status == Status.STOPPED
    assert len([e for e in h.audit.read() if e["rule"] == "SINGLE_ACTION_STEP"]) == 2


def test_repeat_counter_and_budget_survive_reload(make_harness):
    h = make_harness(script=[{"name": "read_file", "arguments": {"path": "src/ranges.py"}}], max_steps=1)
    h.run()
    restarted = Harness.open(h.store.directory)
    assert restarted.state.step_count == 1
    assert restarted.state.token_count == 2
    assert restarted.state.consecutive_count == 1
    assert restarted.run().status == Status.STOPPED


def test_provider_exception_is_redacted_before_framework_logging(make_harness, monkeypatch, capsys):
    secret = "test-provider-secret-that-must-not-appear"
    monkeypatch.setenv("HARNESS_API_KEY", secret)
    h = make_harness()

    def fail(*args, **kwargs):
        raise RuntimeError(f"Server echoed credential: {secret}")

    monkeypatch.setattr(h.model, "generate", fail)
    with pytest.raises(Exception, match="REDACTED"):
        h.run()
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert secret not in h.audit.path.read_text()
    assert h.state.status == Status.FAILED
    assert h.state.step_count == 1
