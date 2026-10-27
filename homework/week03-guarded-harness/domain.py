"""Trusted configuration and JSON-serializable durable business state."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Status(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class StopReason(StrEnum):
    COMPLETED = "COMPLETED"
    STEP_LIMIT = "STEP_LIMIT"
    TOOL_LIMIT = "TOOL_LIMIT"
    TOKEN_LIMIT = "TOKEN_LIMIT"
    CANCELLED = "CANCELLED"
    WORKSPACE_CHANGED = "WORKSPACE_CHANGED"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    MODEL_ERROR = "MODEL_ERROR"


class Policy(Record):
    permissions: list[str] = Field(default_factory=lambda: ["read", "write", "test", "finish"])
    writable_prefixes: list[str] = Field(default_factory=lambda: ["src/"])
    test_suites: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "unit": [
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-c",
                "pytest.ini",
                "--rootdir=.",
                "--confcutdir=.",
                "tests",
            ]
        }
    )
    timeout_seconds: float = Field(default=15, gt=0, le=300)
    output_limit: int = Field(default=32768, ge=64, le=1_000_000)
    file_limit: int = Field(default=262144, gt=0)
    max_steps: int = Field(default=40, gt=0)
    max_tools: int = Field(default=50, gt=0)
    max_tokens: int = Field(default=100000, gt=0)
    approval_ttl: float = Field(default=900, gt=0)
    sandbox_backend: str = "native"


class ToolRequest(Record):
    id: str
    name: str
    arguments: dict[str, Any]

    @property
    def signature(self) -> str:
        return digest({"name": self.name, "arguments": self.arguments})


class CallRecord(Record):
    request: ToolRequest
    phase: str
    workspace_version: str
    result: Any = None


class Approval(Record):
    call_id: str
    signature: str
    workspace_version: str
    actor: str
    expires_at: float
    consumed: bool = False
    revoked: bool = False


class Evidence(Record):
    kind: str
    call_id: str
    workspace_version: str
    data: dict[str, Any]
    valid: bool = True


class RunState(Record):
    schema_version: int = 1
    revision: int = 0
    run_id: str
    task: str
    task_kind: str
    workspace: str
    workspace_version: str
    initial_version: str
    artifact_version: str
    baseline: dict[str, str]
    policy: Policy
    status: Status = Status.RUNNING
    stop_reason: StopReason | None = None
    step_count: int = 0
    tool_count: int = 0
    token_count: int = 0
    estimated_usage: bool = False
    plan: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    approvals: dict[str, Approval] = Field(default_factory=dict)
    calls: dict[str, CallRecord] = Field(default_factory=dict)
    pending: ToolRequest | None = None
    last_signature: str | None = None
    consecutive_count: int = 0
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    model_state: dict[str, Any] = Field(default_factory=dict)
    final_answer: str | None = None

    def invalidate(self) -> None:
        for item in self.evidence:
            item.valid = False
        for item in self.approvals.values():
            if not item.consumed:
                item.revoked = True


class Denied(Exception):
    def __init__(self, rule: str, message: str):
        self.rule = rule
        self.message = message
        super().__init__(f"{rule}: {message}")


class PauseRun(BaseException):
    """Lifecycle signal: deliberately bypass smolagents' recoverable tool-error handler."""
