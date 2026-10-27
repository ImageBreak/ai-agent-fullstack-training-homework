import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest
from smolagents.utils import AgentToolCallError

from domain import Denied


@pytest.mark.parametrize("path", ["artifacts/../../.env", "/etc/passwd", "src/../../outside"])
def test_s1_path_escape_never_enters_handler(make_harness, monkeypatch, path):
    h = make_harness()
    entered = []
    monkeypatch.setattr(h.agent.tools["read_file"], "forward", lambda **kw: entered.append(kw))
    with pytest.raises(AgentToolCallError, match="PATH_ESCAPE"):
        h.invoke("escape", "read_file", {"path": path})
    assert entered == []


def test_s2_symlinks_and_sensitive_targets_are_rejected_and_audited(make_harness, tmp_path):
    h = make_harness()
    outside = tmp_path / "private.txt"
    outside.write_text("secret")
    (Path(h.state.workspace) / "leak.txt").symlink_to(outside)
    # Preflight must reject the attempted access even if the worktree also changed.
    for i, path in enumerate(["leak.txt", ".env", ".ssh/id_rsa"]):
        with pytest.raises(AgentToolCallError, match="SYMLINK_ESCAPE|SENSITIVE_PATH"):
            h.invoke(f"escape-{i}", "read_file", {"path": path})
    assert len([e for e in h.audit.read() if e["decision"] == "DENY"]) == 3


def test_s3_command_outside_configuration_never_executes(make_harness, monkeypatch):
    h = make_harness()
    entered = []
    monkeypatch.setattr(h.agent.tools["run_test"], "forward", lambda **kw: entered.append(kw))
    for i, args in enumerate([{"suite": "rm -rf /"}, {"suite": "unit", "command": "echo bypass"}]):
        with pytest.raises(AgentToolCallError, match="COMMAND_ALLOWLIST|ARGUMENT_SCHEMA"):
            h.invoke(f"bad-command-{i}", "run_test", args)
    assert entered == []


def is_running(pid):
    result = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


@pytest.mark.parametrize("cancel", [False, True])
def test_s4_timeout_and_cancel_terminate_process_group(make_harness, cancel):
    code = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)']); "
        "print(child.pid,flush=True); time.sleep(90)"
    )
    h = make_harness(test_suites={"slow": ["-c", code]}, timeout_seconds=0.9)
    timer = threading.Timer(0.5, h.sandbox.cancel_event.set) if cancel else None
    if timer:
        timer.start()
    try:
        result = h.sandbox.run_test("slow")
    finally:
        if timer:
            timer.cancel()
    assert result["reason"] == ("CANCELLED" if cancel else "TIMEOUT"), result
    child_pid = int(result["output"].strip().splitlines()[0])
    deadline = time.monotonic() + 2
    while is_running(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert not is_running(child_pid)
        assert not is_running(result["pid"])
    finally:
        if is_running(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def test_native_sandbox_denies_external_files_source_writes_and_network(make_harness, tmp_path):
    private = tmp_path / "outside-secret"
    private.write_text("must never be observed")
    code = f"""
import pathlib, socket
for operation in [
    lambda: pathlib.Path({str(private)!r}).read_text(),
    lambda: pathlib.Path('src/ranges.py').write_text('corrupted'),
    lambda: socket.create_connection(('127.0.0.1', 1), timeout=0.2),
]:
    try:
        operation()
    except PermissionError:
        print('DENIED', flush=True)
    else:
        raise AssertionError('Sandbox boundary was bypassed')
"""
    h = make_harness(test_suites={"boundary": ["-c", code]})
    result = h.invoke("boundary", "run_test", {"suite": "boundary"})
    assert result["passed"], result["output"]
    assert result["output"].count("DENIED") == 3


def test_child_environment_and_output_are_bounded(make_harness, monkeypatch):
    monkeypatch.setenv("HARNESS_API_KEY", "never-inherit-this-secret")
    code = "import os; print(os.getenv('HARNESS_API_KEY', 'CLEAN')); print('x'*10000)"
    h = make_harness(test_suites={"noisy": ["-c", code]}, output_limit=128)
    result = h.invoke("bounded", "run_test", {"suite": "noisy"})
    assert result["reason"] == "OUTPUT_LIMIT"
    assert result["output"].startswith("CLEAN")
    assert len(result["output"].encode()) <= 128
    assert "never-inherit" not in result["output"]


def test_no_unsafe_fallback_when_native_sandbox_unavailable(make_harness):
    h = make_harness(sandbox_backend="unsafe")
    with pytest.raises(Denied, match="SANDBOX_UNAVAILABLE"):
        h.sandbox.run_test("unit")


def test_test_configuration_never_searches_parent_repository(make_harness, tmp_path):
    # Parent config is outside the allowed repository; pytest must not try to read it.
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts="--unknown-parent-option"\n')
    h = make_harness(kind="understanding")
    result = h.invoke("isolated-config", "run_test", {"suite": "unit"})
    assert result["passed"], result["output"]
