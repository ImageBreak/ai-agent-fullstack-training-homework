from pathlib import Path

import pytest

from demo import provision, report
from domain import PauseRun, Policy
from harness import Harness
from model_backend import FakeModel


@pytest.fixture
def make_harness(tmp_path):
    counter = 0

    def make(*, kind="repair", script=None, **policy):
        nonlocal counter
        counter += 1
        workspace = provision(tmp_path / f"repo-{counter}", kind)
        return Harness.create(
            workspace,
            tmp_path / "runs",
            task="Inspect and validate the repository",
            task_kind=kind,
            model=FakeModel(script or []),
            policy=Policy(**policy),
        )

    return make


def approved(h, call_id, name, args):
    with pytest.raises(PauseRun):
        h.invoke(call_id, name, args)
    h.approve(call_id, "test-reviewer")
    return h.invoke(call_id, name, args)


def ready(h):
    if h.state.task_kind == "repair":
        approved(
            h, "patch", "apply_patch", {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"}
        )
    h.invoke("read", "read_file", {"path": "src/ranges.py"})
    result = h.invoke("test", "run_test", {"suite": "unit"})
    assert result["passed"], result["output"]
    approved(
        h,
        "report",
        "write_report",
        {
            "path": f"artifacts/{h.state.task_kind}.md",
            "content": report(h.state.task_kind),
        },
    )
    return Path(h.state.workspace)
