"""The single authoritative entry point for every tool call, including final_answer."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from audit import Audit
from checkpoint import Checkpoint
from completion import check_completion
from domain import (
    Approval,
    CallRecord,
    Denied,
    Evidence,
    PauseRun,
    RunState,
    Status,
    StopReason,
    ToolRequest,
    digest,
)
from sandbox import Sandbox
from state_store import StateStore


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NoArguments(Arguments):
    pass


class ReadArgs(Arguments):
    path: str = Field(min_length=1, max_length=500)


class SearchArgs(Arguments):
    query: str = Field(min_length=1, max_length=200)


class PatchArgs(ReadArgs):
    before: str = Field(min_length=1, max_length=100000)
    after: str = Field(max_length=100000)


class ReportArgs(ReadArgs):
    content: str = Field(min_length=40, max_length=100000)


class TestArgs(Arguments):
    suite: str = Field(min_length=1, max_length=100)


class PlanArgs(Arguments):
    steps: list[str] = Field(min_length=1, max_length=12)


class AnswerArgs(Arguments):
    answer: str = Field(min_length=1, max_length=16000)


SCHEMAS = {
    "list_files": NoArguments,
    "read_file": ReadArgs,
    "search_code": SearchArgs,
    "apply_patch": PatchArgs,
    "write_report": ReportArgs,
    "run_test": TestArgs,
    "set_plan": PlanArgs,
    "final_answer": AnswerArgs,
}
WRITES = {"apply_patch", "write_report"}


class Gate:
    def __init__(
        self, state: RunState, sandbox: Sandbox, store: StateStore, checkpoint: Checkpoint, audit: Audit
    ):
        self.state, self.sandbox, self.store = state, sandbox, store
        self.checkpoint, self.audit = checkpoint, audit
        self.after_effect: Callable[[ToolRequest], None] | None = None

    def check_batch(self, calls: list[dict]) -> None:
        if len(calls) == 1:
            return
        for call in calls:
            self.audit.append(
                "gate",
                rule="SINGLE_ACTION_STEP",
                decision="DENY",
                actor="model",
                tool_call_id=call["id"],
                tool=call["name"],
            )
        self.persist()
        raise Denied("SINGLE_ACTION_STEP", "Exactly one tool call is allowed per turn")

    def persist(self, *, safe: bool = True) -> None:
        self.store.save(self.state)
        if safe:
            self.checkpoint.save(self.state)
            self.audit.append(
                "checkpoint",
                rule="SAFE_BOUNDARY",
                decision="SAVED",
                revision=self.state.revision,
                status=self.state.status.value,
            )

    def workspace_check(self, call_id: str | None = None) -> None:
        if (
            self.sandbox.fingerprint() != self.state.workspace_version
            or self.sandbox.artifact_fingerprint() != self.state.artifact_version
        ):
            self.state.invalidate()
            self.state.status = Status.NEEDS_REVIEW
            self.state.stop_reason = StopReason.WORKSPACE_CHANGED
            self.audit.append("state", rule="WORKSPACE_CHANGED", decision="PAUSE", tool_call_id=call_id)
            self.persist()
            raise PauseRun()

    def approval_version(self) -> str:
        return digest([self.state.workspace_version, self.state.artifact_version])

    def approve(self, call_id: str, actor: str) -> None:
        if not actor.strip() or self.state.status != Status.WAITING_APPROVAL:
            raise ValueError("Approval requires a named trusted actor and a waiting run")
        request = self.state.pending
        if request is None or request.id != call_id:
            raise ValueError("Approval must match the pending call")
        self.workspace_check(call_id)
        self.state.approvals[call_id] = Approval(
            call_id=call_id,
            signature=request.signature,
            workspace_version=self.approval_version(),
            actor=actor,
            expires_at=time.time() + self.state.policy.approval_ttl,
        )
        self.state.status = Status.RUNNING
        self.audit.append(
            "approval",
            rule="HUMAN_APPROVAL",
            decision="GRANTED",
            actor=actor,
            tool_call_id=call_id,
            signature=request.signature,
            workspace_version=self.state.workspace_version,
        )
        self.audit.append(
            "state", rule="APPROVAL_RESUME", decision="RUNNING", actor=actor, tool_call_id=call_id
        )
        self.persist()

    def _deny(self, call_id: str, name: str, denial: Denied) -> None:
        self.audit.append(
            "gate",
            rule=denial.rule,
            decision="DENY",
            actor="model",
            tool_call_id=call_id,
            tool=name,
            message=denial.message,
        )
        self.persist()
        raise denial

    def invoke(self, call_id: str, name: str, raw: Any, handler: Callable[[dict], Any]) -> Any:
        state = self.state
        try:
            if state.status != Status.RUNNING:
                raise Denied("RUN_NOT_ACTIVE", f"Run is {state.status.value}")
            if not call_id or len(call_id) > 200:
                raise Denied("CALL_ID", "A bounded nonempty tool call ID is required")
            if name not in SCHEMAS:
                raise Denied("UNKNOWN_TOOL", "Tool is not registered")
            try:
                arguments = SCHEMAS[name].model_validate(raw).model_dump()
            except ValidationError as exc:
                details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors())
                raise Denied("ARGUMENT_SCHEMA", details) from None
            request = ToolRequest(id=call_id, name=name, arguments=arguments)
            existing = state.calls.get(call_id)
            if existing is not None:
                if existing.request.signature != request.signature:
                    raise Denied("CALL_ID_REBOUND", "Call ID is already bound to different arguments")
                if existing.phase != "WAITING_APPROVAL":
                    raise Denied("CALL_REPLAY", "An existing call cannot be executed again")
            else:
                if state.tool_count >= state.policy.max_tools:
                    state.status, state.stop_reason = Status.STOPPED, StopReason.TOOL_LIMIT
                    self.audit.append("state", rule="TOOL_LIMIT", decision="STOP", tool_call_id=call_id)
                    self.persist()
                    raise PauseRun()
                state.tool_count += 1
                if state.last_signature == request.signature:
                    state.consecutive_count += 1
                else:
                    state.last_signature, state.consecutive_count = request.signature, 1
                if state.consecutive_count >= 3:
                    raise Denied("REPEATED_ACTION", "Third consecutive identical tool/arguments blocked")
            permission = (
                "write"
                if name in WRITES
                else "test"
                if name == "run_test"
                else ("finish" if name == "final_answer" else "read")
            )
            if permission not in state.policy.permissions:
                raise Denied("PERMISSION", "Trusted execution context does not grant this capability")
            if name == "apply_patch" and state.task_kind == "understanding":
                raise Denied("READ_ONLY_TASK", "Source edits are forbidden in understanding tasks")
            self.sandbox.preflight(name, arguments)
            if name == "write_report" and arguments["path"] != f"artifacts/{state.task_kind}.md":
                raise Denied("REPORT_PATH", "Report path must match the task kind")
            self.audit.append("sandbox", rule="PREFLIGHT", decision="ALLOW", tool_call_id=call_id, tool=name)
            self.workspace_check(call_id)
            if name == "final_answer":
                check_completion(state, self.sandbox)
            if name in WRITES:
                approval = state.approvals.get(call_id)
                if (
                    not approval
                    or approval.consumed
                    or approval.revoked
                    or approval.expires_at <= time.time()
                    or (
                        approval.signature != request.signature
                        or approval.workspace_version != self.approval_version()
                    )
                ):
                    state.pending = request
                    state.calls[call_id] = CallRecord(
                        request=request, phase="WAITING_APPROVAL", workspace_version=state.workspace_version
                    )
                    state.status = Status.WAITING_APPROVAL
                    self.audit.append(
                        "gate",
                        rule="APPROVAL_REQUIRED",
                        decision="WAITING_APPROVAL",
                        actor="model",
                        tool_call_id=call_id,
                        tool=name,
                        signature=request.signature,
                    )
                    self.audit.append(
                        "state", rule="APPROVAL_WAIT", decision="WAITING_APPROVAL", tool_call_id=call_id
                    )
                    self.persist()
                    raise PauseRun()
                approval.consumed = True
                self.audit.append(
                    "approval",
                    rule="ONE_TIME_APPROVAL",
                    decision="CONSUMED",
                    actor=approval.actor,
                    tool_call_id=call_id,
                    signature=request.signature,
                )
            self.audit.append(
                "gate",
                rule="APPROVED" if name in WRITES else "POLICY_ALLOW",
                decision="ALLOW",
                actor="model",
                tool_call_id=call_id,
                tool=name,
                signature=request.signature,
                step=state.step_count,
                workspace_version=state.workspace_version,
            )
            state.calls[call_id] = CallRecord(
                request=request, phase="STARTED", workspace_version=state.workspace_version
            )
            self.audit.append("state", rule="CALL_STARTED", decision="STARTED", tool_call_id=call_id)
            self.persist(safe=False)  # Durable intent/approval consumption BEFORE entering the handler.
        except Denied as denial:
            self._deny(call_id, name, denial)

        try:
            result = handler(arguments)
            if self.after_effect is not None:
                self.after_effect(request)
        except Denied as denial:
            state.calls[call_id].phase = "FAILED"
            self._deny(call_id, name, denial)
        except Exception:
            state.calls[call_id].phase = "UNKNOWN_RESULT" if name in WRITES else "FAILED"
            if name in WRITES:
                state.status, state.stop_reason = Status.UNKNOWN_RESULT, StopReason.UNKNOWN_RESULT
            self.audit.append(
                "execution",
                rule="HANDLER_ERROR",
                decision=state.calls[call_id].phase,
                tool_call_id=call_id,
                tool=name,
            )
            self.persist()
            raise
        version = self.sandbox.fingerprint()
        if version != state.workspace_version:
            if name != "apply_patch":
                state.calls[call_id].phase = "FAILED"
                state.invalidate()
                state.status, state.stop_reason = Status.NEEDS_REVIEW, StopReason.WORKSPACE_CHANGED
                self.audit.append("state", rule="UNEXPECTED_MUTATION", decision="PAUSE", tool_call_id=call_id)
                self.persist()
                raise PauseRun()
            state.invalidate()
            state.workspace_version = version
        artifact_version = self.sandbox.artifact_fingerprint()
        if artifact_version != state.artifact_version:
            if name != "write_report":
                state.calls[call_id].phase = "FAILED"
                state.invalidate()
                state.status, state.stop_reason = Status.NEEDS_REVIEW, StopReason.WORKSPACE_CHANGED
                self.audit.append(
                    "state", rule="UNEXPECTED_ARTIFACT_MUTATION", decision="PAUSE", tool_call_id=call_id
                )
                self.persist()
                raise PauseRun()
            state.artifact_version = artifact_version
            for approval in state.approvals.values():
                if not approval.consumed:
                    approval.revoked = True
        state.calls[call_id].phase = "SUCCEEDED"
        state.calls[call_id].result = result
        kinds = {"read_file": "read", "apply_patch": "patch", "write_report": "artifact", "run_test": "test"}
        if name in kinds:
            state.evidence.append(
                Evidence(kind=kinds[name], call_id=call_id, workspace_version=version, data=result)
            )
        if state.pending is not None and state.pending.id == call_id:
            state.pending = None
        self.audit.append(
            "execution",
            rule="HANDLER_RESULT",
            decision="SUCCEEDED",
            tool_call_id=call_id,
            tool=name,
            result_type=type(result).__name__,
            workspace_version=version,
        )
        self.audit.append("state", rule="CALL_COMMITTED", decision="SUCCEEDED", tool_call_id=call_id)
        # Tools add observations after returning, while the Gate binds them to the committed version.
        self.persist()
        return result
