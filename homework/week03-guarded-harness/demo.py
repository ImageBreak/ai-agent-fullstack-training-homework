"""Reproducible fixtures and scripted model responses used by tests and demonstration CLI."""

import shutil
from pathlib import Path

TEMPLATE = Path(__file__).parent / "fixtures" / "repository"


def provision(destination: Path, kind: str) -> Path:
    if destination.exists():
        raise ValueError("Workspace already exists; choose a new destination")
    shutil.copytree(TEMPLATE, destination, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    if kind == "understanding":
        source = destination / "src/ranges.py"
        source.write_text(source.read_text().replace("upper - 1", "upper"))
    return destination


def report(kind: str) -> str:
    return (
        f"# {kind.title()} report\n\n"
        "src/ranges.py:1 defines clamp with an inclusive lower and upper bound. "
        "src/ranges.py:5 constrains the value using min/max. Invalid intervals raise ValueError.\n\n"
        "The configured unit suite was executed against the current source and all four tests passed. "
        + (
            "The upper-bound off-by-one defect was repaired; see changes.diff.\n"
            if kind == "repair"
            else "Repository source was preserved; the source diff is empty.\n"
        )
    )


def script(kind: str) -> list[dict]:
    actions = [
        {"id": "plan", "name": "set_plan", "arguments": {"steps": ["Inspect", "Validate", "Report"]}},
        {"id": "source", "name": "read_file", "arguments": {"path": "src/ranges.py"}},
        {"id": "tests", "name": "read_file", "arguments": {"path": "tests/test_ranges.py"}},
        {"id": "baseline-test", "name": "run_test", "arguments": {"suite": "unit"}},
    ]
    if kind == "repair":
        actions += [
            {
                "id": "patch",
                "name": "apply_patch",
                "arguments": {"path": "src/ranges.py", "before": "upper - 1", "after": "upper"},
            },
            {"id": "read-current", "name": "read_file", "arguments": {"path": "src/ranges.py"}},
            {"id": "current-test", "name": "run_test", "arguments": {"suite": "unit"}},
        ]
    return actions + [
        {
            "id": "report",
            "name": "write_report",
            "arguments": {"path": f"artifacts/{kind}.md", "content": report(kind)},
        },
        {
            "id": "finish",
            "name": "final_answer",
            "arguments": {"answer": "Report and current test evidence are ready."},
        },
    ]
