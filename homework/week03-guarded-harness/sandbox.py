"""Repository access boundaries plus a fail-closed native process sandbox on macOS."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from domain import Denied, Policy, digest

IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "artifacts", ".sandbox-tmp"}
SENSITIVE = {".git", ".ssh", ".aws", ".codex", ".agents", ".harness", ".runs", ".sandbox-tmp"}


class Sandbox:
    def __init__(self, workspace: Path, policy: Policy):
        self.root = workspace.resolve(strict=True)
        self.policy = policy
        self.cancel_event = threading.Event()
        self.last_pid: int | None = None

    @staticmethod
    def sensitive(parts) -> bool:
        return any(p in SENSITIVE or p.startswith(".env") for p in parts)

    def path(self, raw: str, *, write: bool = False, report: bool = False) -> Path:
        p = Path(raw)
        if not raw or "\x00" in raw or "\\" in raw or p.is_absolute() or ".." in p.parts:
            raise Denied("PATH_ESCAPE", "Only relative repository paths without '..' are allowed")
        if self.sensitive(p.parts):
            raise Denied("SENSITIVE_PATH", "Access to sensitive directories/files is forbidden")
        target = self.root / p
        real = target.resolve()
        if not real.is_relative_to(self.root):
            raise Denied("SYMLINK_ESCAPE", "Real file target is outside the repository")
        if self.sensitive(real.relative_to(self.root).parts):
            raise Denied("SENSITIVE_PATH", "Resolved target is sensitive")
        # Refuse all symlink components, including links that currently point inside the workspace.
        current = self.root
        for component in p.parts:
            current /= component
            if current.is_symlink():
                raise Denied("SYMLINK_PATH", "Symlink components are not executable file paths")
        if write:
            if report:
                if p.as_posix() not in {"artifacts/understanding.md", "artifacts/repair.md"}:
                    raise Denied("REPORT_PATH", "Report must use the task's designated artifact path")
            elif not any(p.as_posix().startswith(prefix) for prefix in self.policy.writable_prefixes):
                raise Denied("WRITE_SCOPE", "This file is outside the writable source scope")
        return target

    @contextmanager
    def parent_fd(self, target: Path):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in target.relative_to(self.root).parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    def read_text(self, raw: str) -> str:
        target = self.path(raw)
        with self.parent_fd(target) as parent:
            fd = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > self.policy.file_limit:
                    raise Denied("FILE_BOUNDARY", "Only bounded regular text files can be read")
                content = stream.read(self.policy.file_limit + 1)
        if len(content) > self.policy.file_limit:
            raise Denied("FILE_BOUNDARY", "File exceeded the configured byte limit")
        return content.decode("utf-8")

    def write_text(self, raw: str, content: str, *, report: bool = False) -> None:
        target = self.path(raw, write=True, report=report)
        if len(content.encode()) > self.policy.file_limit:
            raise Denied("FILE_BOUNDARY", "Write exceeds the configured byte limit")
        with self.parent_fd(target) as parent:
            temporary = f".write-{uuid.uuid4().hex}"
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
            )
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def snapshot(self) -> dict[str, str]:
        result = {}
        total = 0
        for base, directories, files in os.walk(self.root, followlinks=False):
            for directory in directories:
                target = Path(base) / directory
                if target.is_symlink() and directory not in IGNORED and not self.sensitive([directory]):
                    raw = target.relative_to(self.root).as_posix()
                    result[raw] = "<symlink:" + os.readlink(target) + ">"
            directories[:] = sorted(
                d
                for d in directories
                if d not in IGNORED and not self.sensitive([d]) and not (Path(base) / d).is_symlink()
            )
            for filename in sorted(files):
                raw = (Path(base) / filename).relative_to(self.root).as_posix()
                if self.sensitive(Path(raw).parts):
                    continue
                target = self.root / raw
                if target.is_symlink():
                    result[raw] = "<symlink:" + os.readlink(target) + ">"
                else:
                    result[raw] = self.read_text(raw)
                total += len(result[raw].encode())
                if len(result) > 1000 or total > 4_000_000:
                    raise Denied("REPOSITORY_LIMIT", "Teaching harness supports <=1000 files / 4MB text")
        return result

    def fingerprint(self) -> str:
        return digest(self.snapshot())

    def artifact_fingerprint(self) -> str:
        root = self.root / "artifacts"
        if root.is_symlink():
            return digest({"symlink": os.readlink(root)})
        files = {}
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    files[path.relative_to(root).as_posix()] = "<symlink>"
                elif path.is_file():
                    files[path.relative_to(root).as_posix()] = self.read_text(
                        path.relative_to(self.root).as_posix()
                    )
        return digest(files)

    def preflight(self, name: str, arguments: dict) -> None:
        if name in {"read_file", "apply_patch", "write_report"}:
            self.path(arguments["path"], write=name != "read_file", report=name == "write_report")
        if name == "run_test" and arguments["suite"] not in self.policy.test_suites:
            raise Denied("COMMAND_ALLOWLIST", "Unknown test suite; arbitrary commands are not accepted")

    def _native_command(self, argv: list[str], scratch: Path) -> list[str]:
        if self.policy.sandbox_backend != "native" or sys.platform != "darwin":
            raise Denied(
                "SANDBOX_UNAVAILABLE", "Native execution requires macOS sandbox-exec; no unsafe fallback"
            )
        python = str(Path(sys.executable).resolve())
        # Runtime/library reads are allowed; host home, credentials and network are not.
        read_roots = [
            self.root,
            Path(sys.base_prefix),
            Path(sys.prefix),
            Path(__file__).parent / ".venv",
            Path("/System"),
            Path("/usr/lib"),
            Path("/usr/share"),
            Path("/private/var/db"),
            Path("/Library/Apple"),
            Path("/dev"),
            scratch,
        ]
        rules = [
            "(version 1)",
            "(deny default)",
            "(allow file-read-metadata)",
            '(allow file-read* (literal "/"))',
            "(allow process-fork)",
            "(allow signal)",
            "(allow sysctl-read)",
            "(allow mach-lookup)",
            f"(allow process-exec (literal {json.dumps(python)}))",
        ]
        for root in read_roots:
            rules.append(f"(allow file-read* (subpath {json.dumps(str(root.resolve()))}))")
        rules.append(f"(allow file-write* (subpath {json.dumps(str(scratch))}))")
        rules.append('(allow file-write* (literal "/dev/null"))')
        # Specific denies also protect sensitive files inside otherwise readable roots.
        rules.append('(deny file-read* (regex #"(^|/)\\.env([^/]*)(/|$)"))')
        for part in SENSITIVE:
            rules.append(f"(deny file-read* (subpath {json.dumps(str(self.root / part))}))")
        return ["/usr/bin/sandbox-exec", "-p", "\n".join(rules), *argv]

    def run_test(self, suite: str) -> dict:
        self.preflight("run_test", {"suite": suite})
        argv = [str(Path(sys.executable).absolute()), *self.policy.test_suites[suite]]
        if not argv[1:] or argv[1] not in {"-m", "-c"}:
            raise Denied(
                "COMMAND_ALLOWLIST", "Trusted test commands must be Python module/script invocations"
            )
        scratch_parent = self.root / ".sandbox-tmp"
        scratch_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch_parent) as temp:
            scratch = Path(temp).resolve()
            command = self._native_command(argv, scratch)
            env = {
                "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
                "HOME": str(scratch),
                "TMPDIR": str(scratch),
                "LANG": "en_US.UTF-8",
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONPATH": str(self.root),
            }
            started = time.monotonic()
            process = subprocess.Popen(
                command,
                cwd=self.root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.last_pid = process.pid
            reason = None
            output = bytearray()
            selector = selectors.DefaultSelector()
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            try:
                while selector.get_map():
                    if self.cancel_event.is_set():
                        reason = "CANCELLED"
                    elif time.monotonic() - started > self.policy.timeout_seconds:
                        reason = "TIMEOUT"
                    if reason:
                        self._kill_group(process.pid)
                        break
                    for key, _ in selector.select(timeout=0.03):
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            output.extend(chunk)
                            if len(output) > self.policy.output_limit:
                                reason = "OUTPUT_LIMIT"
                                self._kill_group(process.pid)
                                break
                # A child can close stdout while continuing to run. Bound the wait as well.
                while process.poll() is None and reason is None:
                    if self.cancel_event.is_set():
                        reason = "CANCELLED"
                    elif time.monotonic() - started > self.policy.timeout_seconds:
                        reason = "TIMEOUT"
                    else:
                        time.sleep(0.01)
                if reason:
                    self._kill_group(process.pid)
                returncode = process.wait(timeout=3)
            finally:
                self._kill_group(process.pid)  # Also reap descendants after a normally exiting parent.
                process.wait(timeout=3)
                selector.close()
                process.stdout.close()
            return {
                "suite": suite,
                "argv": ["python", *argv[1:]],
                "exit_code": returncode,
                "reason": reason,
                "passed": returncode == 0 and reason is None,
                "output": bytes(output[: self.policy.output_limit]).decode("utf-8", errors="replace"),
                "output_sha256": hashlib.sha256(output).hexdigest(),
                "pid": process.pid,
                "sandbox": "macOS-seatbelt",
                "duration": round(time.monotonic() - started, 3),
            }

    @staticmethod
    def _kill_group(pid: int) -> None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin returns EPERM for a group containing only zombies after an earlier SIGKILL.
            # Confirm that no live member remains; a real signal permission failure still propagates.
            result = subprocess.run(
                ["/bin/ps", "-axo", "pgid=,stat="], capture_output=True, text=True, check=True
            )
            members = [
                line.split()[1]
                for line in result.stdout.splitlines()
                if len(line.split()) == 2 and line.split()[0] == str(pid)
            ]
            if any(not status.startswith("Z") for status in members):
                raise
