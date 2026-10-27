import pytest
from conftest import approved, ready
from smolagents.utils import AgentToolCallError

from domain import PauseRun, Status, StopReason


def test_h1_final_answer_without_deliverables_is_rejected(make_harness):
    h = make_harness(
        script=[{"name": "final_answer", "arguments": {"answer": "Done, everything is fixed."}}], max_steps=1
    )
    h.run()
    assert h.state.status == Status.STOPPED
    assert not any(
        c.request.name == "final_answer" and c.phase == "SUCCEEDED" for c in h.state.calls.values()
    )
    assert any(e["rule"] == "MISSING_DELIVERABLE" for e in h.audit.read())


def test_h2_modifying_source_invalidates_previous_passing_test_evidence(make_harness):
    h = make_harness()
    ready(h)
    passed = next(e for e in h.state.evidence if e.kind == "test" and e.data["passed"])
    approved(
        h,
        "another-edit",
        "apply_patch",
        {
            "path": "src/ranges.py",
            "before": '"""Return value',
            "after": '"""Return the value',
        },
    )
    assert not passed.valid
    h.invoke("reread", "read_file", {"path": "src/ranges.py"})
    # Refresh the artifact, but deliberately do not rerun tests.
    from demo import report

    approved(h, "report-again", "write_report", {"path": "artifacts/repair.md", "content": report("repair")})
    with pytest.raises(AgentToolCallError, match="TEST_EVIDENCE_REQUIRED"):
        h.invoke("premature", "final_answer", {"answer": "Finished"})
    assert h.state.status == Status.RUNNING
    assert h.invoke("test-current", "run_test", {"suite": "unit"})["passed"]
    assert h.invoke("real-finish", "final_answer", {"answer": "Verified"}) == "Verified"


@pytest.mark.parametrize(
    "policy, reason",
    [
        ({"max_steps": 1}, StopReason.STEP_LIMIT),
        ({"max_tokens": 1}, StopReason.TOKEN_LIMIT),
        ({"max_tools": 1}, StopReason.TOOL_LIMIT),
    ],
)
def test_h3_limits_stop_with_explicit_reason_not_success(make_harness, policy, reason):
    h = make_harness(
        script=[
            {"name": "list_files", "arguments": {}},
            {"name": "read_file", "arguments": {"path": "src/ranges.py"}},
        ],
        **policy,
    )
    h.run()
    assert h.state.status == Status.STOPPED
    assert h.state.stop_reason == reason
    assert h.state.final_answer is None


def test_report_tampering_is_detected_before_completion(make_harness):
    h = make_harness()
    root = ready(h)
    (root / "artifacts/repair.md").write_text("tampered content" * 5)
    with pytest.raises(PauseRun):
        h.invoke("finish", "final_answer", {"answer": "Done"})
    assert h.state.status == Status.NEEDS_REVIEW


def test_citation_requires_real_current_source_evidence(make_harness):
    h = make_harness()
    root = ready(h)
    report_text = (root / "artifacts/repair.md").read_text().replace("src/ranges.py:1", "src/ranges.py:999")
    approved(h, "wrong-report", "write_report", {"path": "artifacts/repair.md", "content": report_text})
    with pytest.raises(AgentToolCallError, match="INVALID_CITATION"):
        h.invoke("finish", "final_answer", {"answer": "Done"})
