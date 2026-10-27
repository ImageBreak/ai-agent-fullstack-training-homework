"""Append-only, fsynced, hash-chained audit. Do not log source contents or credentials."""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from domain import canonical


class Audit:
    def __init__(self, directory: Path, run_id: str):
        self.path = directory / "audit.jsonl"
        self.run_id = run_id
        entries = self.read()
        self.sequence = len(entries)
        self.previous = entries[-1]["hash"] if entries else "0" * 64

    def append(
        self,
        event: str,
        *,
        rule: str,
        decision: str,
        actor: str = "harness",
        tool_call_id: str | None = None,
        **details: Any,
    ) -> None:
        entry = {
            "sequence": self.sequence + 1,
            "timestamp": time.time(),
            "run_id": self.run_id,
            "event": event,
            "rule": rule,
            "decision": decision,
            "actor": actor,
            "toolCallId": tool_call_id,
            "previous_hash": self.previous,
            "details": details,
        }
        entry["hash"] = hashlib.sha256(canonical(entry).encode()).hexdigest()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            payload = (canonical(entry) + "\n").encode()
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            raise
        self.sequence += 1
        self.previous = entry["hash"]

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        entries = []
        previous = "0" * 64
        for line in self.path.read_text().splitlines():
            entry = json.loads(line)
            saved = entry.pop("hash")
            if entry["sequence"] != len(entries) + 1 or entry["previous_hash"] != previous:
                raise ValueError("Broken audit sequence")
            if hashlib.sha256(canonical(entry).encode()).hexdigest() != saved:
                raise ValueError("Broken audit hash")
            entry["hash"] = saved
            previous = saved
            entries.append(entry)
        return entries
