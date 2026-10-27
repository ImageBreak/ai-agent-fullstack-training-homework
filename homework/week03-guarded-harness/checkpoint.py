"""Atomic, versioned snapshots at safe tool/step boundaries; never pickle runtime objects."""

import json
import os
import tempfile
from pathlib import Path

from domain import RunState, canonical, digest


class Checkpoint:
    def __init__(self, directory: Path):
        self.path = directory / "checkpoint.json"

    def save(self, state: RunState) -> None:
        if any(c.phase == "STARTED" for c in state.calls.values()):
            return  # An unfinished side effect is not a safe checkpoint boundary.
        data = state.model_dump(mode="json")
        payload = canonical({"format": 1, "sha256": digest(data), "state": data})
        fd, name = tempfile.mkstemp(prefix="checkpoint-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def load(self) -> RunState:
        data = json.loads(self.path.read_text())
        if data.get("format") != 1 or digest(data["state"]) != data.get("sha256"):
            raise ValueError("Invalid/corrupted checkpoint")
        state = RunState.model_validate(data["state"])
        if state.schema_version != 1:
            raise ValueError("Unsupported checkpoint schema")
        return state
