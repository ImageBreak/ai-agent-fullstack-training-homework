"""Only explicit repository tools are registered; no generic shell or interpreter tool."""

import difflib
import hashlib

from smolagents import Tool

from domain import Denied, RunState
from sandbox import Sandbox


class RepositoryTool(Tool):
    def __init__(self, sandbox: Sandbox, state: RunState):
        self.sandbox, self.run_state = sandbox, state
        super().__init__()


class ListFiles(RepositoryTool):
    name = "list_files"
    description = "List readable repository text files. Artifacts and sensitive files are excluded."
    inputs = {}
    output_type = "object"

    def forward(self):
        return {"files": sorted(self.sandbox.snapshot())}


class ReadFile(RepositoryTool):
    name = "read_file"
    description = "Read a relative text file with line numbers. Use this to collect source evidence."
    inputs = {"path": {"type": "string", "description": "Relative path, e.g. src/pricing.py."}}
    output_type = "object"

    def forward(self, path: str):
        text = self.sandbox.read_text(path)
        numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(text.splitlines(), 1))
        if len(numbered.encode()) > self.run_state.policy.output_limit:
            raise Denied("OUTPUT_LIMIT", "File observation exceeds the output boundary")
        return {"path": path, "sha256": hashlib.sha256(text.encode()).hexdigest(), "content": numbered}


class SearchCode(RepositoryTool):
    name = "search_code"
    description = "Search a literal substring in repository text. Returns at most 30 matching source lines."
    inputs = {"query": {"type": "string", "description": "Nonempty literal substring to find."}}
    output_type = "object"

    def forward(self, query: str):
        hits = []
        for path, text in self.sandbox.snapshot().items():
            for number, line in enumerate(text.splitlines(), 1):
                if query in line:
                    hits.append({"path": path, "line": number, "text": line[:250]})
                    if len(hits) == 30:
                        return {"hits": hits, "truncated": True}
        return {"hits": hits, "truncated": False}


class ApplyPatch(RepositoryTool):
    name = "apply_patch"
    description = "Replace exactly one matching source fragment. A human must approve this exact call."
    inputs = {
        "path": {"type": "string", "description": "Relative writable source path under src/."},
        "before": {"type": "string", "description": "Exact existing text, occurring exactly once."},
        "after": {"type": "string", "description": "Replacement source text."},
    }
    output_type = "object"

    def forward(self, path: str, before: str, after: str):
        text = self.sandbox.read_text(path)
        if text.count(before) != 1 or before == after:
            raise Denied("PATCH_PRECONDITION", "Patch must make a real change and match exactly once")
        changed = text.replace(before, after, 1)
        self.sandbox.write_text(path, changed)
        return {
            "path": path,
            "before_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "sha256": hashlib.sha256(changed.encode()).hexdigest(),
        }


class WriteReport(RepositoryTool):
    name = "write_report"
    description = (
        "Write the task report to artifacts/understanding.md or artifacts/repair.md. "
        "Include source citations like src/pricing.py:1, findings, and test results. Requires human approval."
    )
    inputs = {
        "path": {"type": "string", "description": "The designated artifact path for this task kind."},
        "content": {
            "type": "string",
            "description": "Markdown report, >=40 characters, with source:line citations.",
        },
    }
    output_type = "object"

    def forward(self, path: str, content: str):
        if path != f"artifacts/{self.run_state.task_kind}.md":
            raise Denied("REPORT_PATH", "Report does not match the current task kind")
        self.sandbox.write_text(path, content, report=True)
        return {"path": path, "sha256": hashlib.sha256(content.encode()).hexdigest()}


class RunTest(RepositoryTool):
    name = "run_test"
    description = "Run a trusted configured test suite in the native sandbox. No shell commands are accepted."
    inputs = {"suite": {"type": "string", "description": "Configured test suite name; normally unit."}}
    output_type = "object"

    def forward(self, suite: str):
        return self.sandbox.run_test(suite)


class SetPlan(RepositoryTool):
    name = "set_plan"
    description = "Save or update a short task plan before taking repository actions."
    inputs = {
        "steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "One to twelve short textual steps.",
        }
    }
    output_type = "object"

    def forward(self, steps: list[str]):
        self.run_state.plan = steps
        return {"plan": steps}


def repository_tools(sandbox, state):
    return [
        cls(sandbox, state)
        for cls in (ListFiles, ReadFile, SearchCode, ApplyPatch, WriteReport, RunTest, SetPlan)
    ]


def repository_diff(state: RunState, sandbox: Sandbox) -> str:
    current = sandbox.snapshot()
    chunks = []
    for path in sorted(state.baseline.keys() | current.keys()):
        chunks.extend(
            difflib.unified_diff(
                state.baseline.get(path, "").splitlines(True),
                current.get(path, "").splitlines(True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
    return "".join(chunks)
