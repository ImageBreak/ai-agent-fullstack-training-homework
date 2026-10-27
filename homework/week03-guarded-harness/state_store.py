"""SQLite is the authoritative execution ledger, including pre-side-effect intents."""

from __future__ import annotations

import fcntl
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from domain import RunState


class StateStore:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "state.sqlite3"
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, revision INTEGER, data TEXT)"
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def save(self, state: RunState) -> None:
        old = state.revision
        new = old + 1
        data = state.model_copy(update={"revision": new}).model_dump_json()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM state WHERE id=1").fetchone()
            if row is None:
                if old != 0:
                    raise RuntimeError("Missing execution ledger")
                db.execute("INSERT INTO state VALUES (1, ?, ?)", (new, data))
            else:
                if row[0] != old:
                    raise RuntimeError("Concurrent/stale state writer")
                db.execute("UPDATE state SET revision=?, data=? WHERE id=1", (new, data))
        state.revision = new

    def load(self) -> RunState:
        with self.connect() as db:
            row = db.execute("SELECT data FROM state WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Run does not exist")
        state = RunState.model_validate_json(row[0])
        if state.schema_version != 1:
            raise ValueError("Unsupported state schema")
        return state

    @contextmanager
    def locked(self):
        with (self.directory / "run.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Run is already active") from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
