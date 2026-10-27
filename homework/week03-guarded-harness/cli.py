"""Local operator commands. Approvals are never supplied by the model."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from demo import provision, script
from domain import PauseRun, Policy
from harness import Harness
from model_backend import FakeModel, live_model


def summary(h):
    state = h.state
    print(
        json.dumps(
            {
                "run_directory": str(h.store.directory),
                "run_id": state.run_id,
                "status": state.status.value,
                "stop_reason": state.stop_reason,
                "steps": state.step_count,
                "tools": state.tool_count,
                "pending": state.pending.model_dump() if state.pending else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--kind", choices=["understanding", "repair"], required=True)
    start.add_argument("--workspace", type=Path, required=True)
    start.add_argument("--runs-dir", type=Path, default=Path(".runs"))
    start.add_argument("--model", choices=["fake", "live"], default="fake")
    start.add_argument("--fixture", action="store_true", help="Create a NEW demonstration workspace")
    start.add_argument("--task", default=None)
    start.add_argument("--max-steps", type=int, default=40)
    for name in ["resume", "status", "approve", "review", "export"]:
        command = commands.add_parser(name)
        command.add_argument("run_directory", type=Path)
        if name in {"approve", "review"}:
            command.add_argument("--actor", required=True)
        if name == "approve":
            command.add_argument("--call-id", required=True)
        if name == "export":
            command.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "start":
        if args.fixture:
            provision(args.workspace, args.kind)
        model = FakeModel(script(args.kind)) if args.model == "fake" else live_model()
        task = args.task or (
            "Explain clamp's behavior, boundary cases and error handling with source citations. "
            "Preserve all sources and run the configured tests. Write artifacts/understanding.md."
            if args.kind == "understanding"
            else "Diagnose and repair the failing tests by editing source only. Preserve tests. "
            "Run tests before and after the fix and write artifacts/repair.md with source citations."
        )
        h = Harness.create(
            args.workspace,
            args.runs_dir,
            task=task,
            task_kind=args.kind,
            model=model,
            policy=Policy(max_steps=args.max_steps),
        )
        h.run()
    elif args.command == "status":
        # Status must not require provider credentials or mutate the execution ledger.
        from state_store import StateStore

        print(StateStore(args.run_directory).load().model_dump_json(indent=2))
        return
    else:
        h = Harness.open(args.run_directory)
        if args.command == "resume":
            h.run()
        elif args.command == "approve":
            try:
                h.approve(args.call_id, args.actor)
            except PauseRun:
                pass
        elif args.command == "review":
            h.review_workspace(args.actor)
        elif args.command == "export":
            h.export(args.destination)
    summary(h)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        root = exc
        while root.__cause__ is not None:
            root = root.__cause__
        message = str(root)
        key = os.getenv("HARNESS_API_KEY", "")
        if key:
            message = message.replace(key, "[REDACTED]")
        message = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", message)
        print(
            json.dumps(
                {
                    "error": type(root).__name__,
                    "status_code": getattr(root, "status_code", None),
                    "message": message[:800],
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
