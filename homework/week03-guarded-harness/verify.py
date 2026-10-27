"""Run deterministic acceptance and export reproducible cross-process fixture demonstrations."""

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from demo import script
from domain import canonical
from harness import Harness
from state_store import StateStore

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"


def source_fingerprint():
    paths = [*ROOT.glob("*.py"), *ROOT.glob("tests/*.py"), *ROOT.glob("fixtures/**/*")]
    paths += [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    files = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    return hashlib.sha256(canonical(files).encode()).hexdigest()


def command(arguments, log):
    result = subprocess.run([sys.executable, "cli.py", *arguments], cwd=ROOT, capture_output=True, text=True)
    log.append(
        {
            "arguments": arguments,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    )
    if result.returncode:
        raise RuntimeError(f"Demonstration command failed: {result.stderr}")
    return result


def offline_demo(kind: str, session: str):
    destination = REPORTS / f"fake-{kind}"
    workspace = ROOT / ".workspaces" / f"{session}-{kind}"
    runs = ROOT / ".runs" / session
    log = []
    command(
        [
            "start",
            "--kind",
            kind,
            "--workspace",
            str(workspace),
            "--runs-dir",
            str(runs),
            "--fixture",
            "--model",
            "fake",
        ],
        log,
    )
    candidates = [path for path in runs.iterdir() if path.is_dir()]
    run_dir = max(candidates, key=lambda p: p.stat().st_mtime_ns)
    expected = {action["id"]: action for action in script(kind)}
    recoveries = []
    while True:
        state = StateStore(run_dir).load()
        if state.status != "WAITING_APPROVAL":
            break
        request = state.pending
        action = expected[request.id]
        if request.name != action["name"] or request.arguments != action["arguments"]:
            raise RuntimeError("Fixture reviewer refused an unexpected action")
        # This is a scripted test reviewer, explicitly not a claim of human approval.
        command(["approve", str(run_dir), "--call-id", request.id, "--actor", "scripted-test-reviewer"], log)
        before = StateStore(run_dir).load()
        command(["resume", str(run_dir)], log)  # Every resume runs in a different OS process.
        after = StateStore(run_dir).load()
        recoveries.append(
            {
                "call_id": request.id,
                "before_steps": before.step_count,
                "after_steps": after.step_count,
                "before_tools": before.tool_count,
                "after_tools": after.tool_count,
                "status": after.status,
                "completed_writes": [
                    k
                    for k, c in after.calls.items()
                    if c.request.name in {"apply_patch", "write_report"} and c.phase == "SUCCEEDED"
                ],
            }
        )
    if state.status != "COMPLETED":
        raise RuntimeError(f"Fixture demonstration did not complete: {state.status}")
    h = Harness.open(run_dir)
    h.export(destination)
    (destination / "execution.jsonl").write_text("".join(canonical(entry) + "\n" for entry in log))
    (destination / "recovery.json").write_text(json.dumps(recoveries, indent=2))
    return {"status": state.status, "run_id": state.run_id, "directory": str(destination.relative_to(ROOT))}


MATRIX = {
    "G1": "test_g1_",
    "G2": "test_g2_",
    "G3": "test_g3_",
    "G4": "test_g4_",
    "S1": "test_s1_",
    "S2": "test_s2_",
    "S3": "test_s3_",
    "S4": "test_s4_",
    "C1": "test_c1_",
    "C2": "test_c2_",
    "C3": "test_c3_",
    "C4": "test_c4_",
    "A1": "test_a1_",
    "A2": "test_a2_",
    "H1": "test_h1_",
    "H2": "test_h2_",
    "H3": "test_h3_",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-tests", action="store_true", help="Regenerate fixture runs using existing test evidence"
    )
    args = parser.parse_args()
    REPORTS.mkdir(exist_ok=True)
    fingerprint = source_fingerprint()
    if not args.skip_tests:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--junitxml=reports/junit.xml"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        (REPORTS / "pytest-result.txt").write_text(output)
        print(output, end="")
        if result.returncode:
            raise SystemExit(result.returncode)
        if source_fingerprint() != fingerprint:
            raise RuntimeError("Source changed while tests were running; rerun acceptance")
        (REPORTS / "test-manifest.json").write_text(json.dumps({"source_sha256": fingerprint}))
    else:
        manifest = json.loads((REPORTS / "test-manifest.json").read_text())
        if manifest["source_sha256"] != fingerprint:
            raise RuntimeError("Stored test evidence is stale; run verify.py without --skip-tests")
    cases = list(ET.parse(REPORTS / "junit.xml").iter("testcase"))
    checks = {}
    for identifier, prefix in MATRIX.items():
        selected = [c for c in cases if c.attrib["name"].startswith(prefix)]
        checks[identifier] = {
            "status": "PASS" if selected and all(len(c) == 0 for c in selected) else "FAIL",
            "tests": [c.attrib["classname"] + "::" + c.attrib["name"] for c in selected],
        }
    checks["T1"] = {
        "status": "PASS" if cases and all(len(c) == 0 for c in cases) else "FAIL",
        "test_count": len(cases),
    }
    session = uuid.uuid4().hex[:10]
    demos = {kind: offline_demo(kind, session) for kind in ["understanding", "repair"]}
    live = {}
    for kind in ["understanding", "repair"]:
        path = REPORTS / f"live-{kind}" / "run.json"
        live[kind] = json.loads(path.read_text()) if path.exists() else {"status": "NOT_RUN"}
    checks["T2"] = {
        "status": "PASS"
        if all(r.get("status") == "COMPLETED" and r.get("model") == "live" for r in live.values())
        else "NOT_RUN",
        "runs": live,
    }
    payload = {
        "source_sha256": fingerprint,
        "timestamp": time.time(),
        "python": sys.version.split()[0],
        "smolagents": importlib.metadata.version("smolagents"),
        "checks": checks,
        "fake_demonstrations": demos,
        "acceptance_complete": all(item["status"] == "PASS" for item in checks.values()),
    }
    (REPORTS / "verification.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "test_count": len(cases),
                "T2": checks["T2"]["status"],
                "acceptance_complete": payload["acceptance_complete"],
            }
        )
    )


if __name__ == "__main__":
    main()
