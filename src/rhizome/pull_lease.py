"""Durable common-repository lease for native pull writers.

The lease is intentionally independent of recovery preferences.  Its presence
blocks every other coordinator mutation in the common Git repository until the
native writer has a classified, quiescent outcome.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class PullLeaseError(RuntimeError):
    """A durable pull writer lease cannot be safely trusted or changed."""


class PullWriterGuardError(RuntimeError):
    """The common-repository cross-process writer guard is unsafe or unavailable."""


class PullWriterGuard:
    """Serialize cooperating Rhizome writers across processes.

    This short-lived guard is separate from :class:`PullLease`: the durable lease
    describes native pull ownership, while this lock closes validation-to-execution
    races among ordinary Rhizome processes.
    """

    def __init__(self, common_dir: Path) -> None:
        self.common_dir = Path(common_dir)
        self.path = self.common_dir / "rhizome-writer.lock"

    @contextmanager
    def hold(self):
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = None
        try:
            descriptor = os.open(self.path, flags, 0o600)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
            ):
                raise PullWriterGuardError(
                    "Repository writer guard is not a private, singly linked regular file."
                )
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = os.fstat(descriptor)
            current = self.path.lstat()
            if (
                not stat.S_ISREG(locked.st_mode)
                or locked.st_nlink != 1
                or (hasattr(os, "getuid") and locked.st_uid != os.getuid())
                or (current.st_dev, current.st_ino) != (locked.st_dev, locked.st_ino)
            ):
                raise PullWriterGuardError("Repository writer guard changed while locking it.")
            yield
        except PullWriterGuardError:
            raise
        except OSError as error:
            raise PullWriterGuardError(f"Cannot lock repository writer guard: {error}") from error
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


@dataclass(frozen=True)
class PullLeaseRecord:
    token: str
    repository: str
    worktree: str
    operation: str
    phase: str
    pid: int
    pgid: int


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
    )


class PullLease:
    """One exclusive native pull writer lease per common Git directory."""

    def __init__(self, common_dir: Path, *, repository: str, worktree: str) -> None:
        self.common_dir = Path(common_dir)
        self.repository = repository
        self.worktree = worktree
        self.path = self.common_dir / "rhizome-pull-lease.json"
        self.lock_path = self.common_dir / "rhizome-pull-lease.lock"

    @contextmanager
    def _lock(self):
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = None
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
            ):
                raise PullLeaseError(
                    "Pull lease lock is not a private, singly linked regular file."
                )
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except PullLeaseError:
            raise
        except OSError as error:
            raise PullLeaseError(f"Cannot lock durable pull writer lease: {error}") from error
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def read(self) -> PullLeaseRecord | None:
        # Ordinary workspace reads and preference changes must not create Git
        # metadata. Acquiring a writer lease, not observing an absent one,
        # creates the private lock file. Existing leases still read under lock.
        try:
            self.path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise PullLeaseError(f"Cannot inspect durable pull writer lease: {error}") from error
        with self._lock():
            return self._read_locked()

    def acquire(self, operation: str) -> PullLeaseRecord:
        if operation not in {"fast-forward", "merge", "rebase"}:
            raise PullLeaseError("Unsupported pull lease operation.")
        with self._lock():
            current = self._read_locked()
            if current is not None:
                raise PullLeaseError(
                    f"A durable pull writer lease is already present at {self.path}; "
                    "inspect the recorded process and repository before manual removal."
                )
            token = os.urandom(32).hex()
            record = PullLeaseRecord(
                token=token,
                repository=self.repository,
                worktree=self.worktree,
                operation=operation,
                phase="prepared",
                pid=0,
                pgid=0,
            )
            self._write_locked(record, create=True)
            return record

    def update(self, token: str, *, phase: str, pid: int = 0) -> PullLeaseRecord:
        with self._lock():
            current = self._require_locked(token)
            transitions = {
                "prepared": {"running", "uncertain"},
                "running": {"paused", "uncertain", "running"},
                "paused": {"running", "uncertain"},
                "uncertain": {"uncertain"},
            }
            if phase not in transitions.get(current.phase, set()):
                raise PullLeaseError("Invalid pull writer lease transition.")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid < 0:
                raise PullLeaseError("Pull writer process identifier is invalid.")
            if phase == "running" and pid <= 0:
                raise PullLeaseError("Running pull writer lease requires a process identifier.")
            if phase != "running" and pid == 0:
                pid = current.pid
            updated = PullLeaseRecord(
                token=current.token,
                repository=current.repository,
                worktree=current.worktree,
                operation=current.operation,
                phase=phase,
                pid=pid,
                pgid=pid,
            )
            self._write_locked(updated)
            return updated

    def clear(self, token: str) -> None:
        with self._lock():
            self._require_locked(token)
            try:
                self.path.unlink()
            except OSError as error:
                raise PullLeaseError(f"Cannot clear durable pull writer lease: {error}") from error
            # Failure to fsync an already successful unlink can only resurrect a
            # conservative stale blocker after a crash; it cannot lose protection
            # for a still-running writer, whose outcome was classified first.
            try:
                descriptor = os.open(self.common_dir, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                pass

    def require(self, token: str, *, phase: str | None = None) -> PullLeaseRecord:
        with self._lock():
            record = self._require_locked(token)
            if phase is not None and record.phase != phase:
                raise PullLeaseError("Durable pull writer lease is not in the required state.")
            return record

    def _require_locked(self, token: str) -> PullLeaseRecord:
        current = self._read_locked()
        if current is None or current.token != token:
            raise PullLeaseError("Durable pull writer lease ownership changed.")
        if current.repository != self.repository or current.worktree != self.worktree:
            raise PullLeaseError("Durable pull writer lease belongs to another worktree.")
        return current

    def _read_locked(self) -> PullLeaseRecord | None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise PullLeaseError(f"Cannot inspect durable pull writer lease: {error}") from error
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_size > 64 * 1024
            or stat.S_IMODE(info.st_mode) & 0o077
            or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        ):
            raise PullLeaseError("Durable pull writer lease file is unsafe or not private.")
        try:
            raw = self.path.read_bytes()
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or set(envelope) != {"body", "sha256"}:
                raise ValueError("invalid envelope")
            body = envelope["body"]
            if raw != _canonical(envelope):
                raise ValueError("noncanonical lease")
            if envelope["sha256"] != hashlib.sha256(_canonical(body)).hexdigest():
                raise ValueError("lease digest mismatch")
            return self._record(body)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise PullLeaseError(
                "Durable pull writer lease is corrupt; manual inspection is required."
            ) from error

    def _write_locked(self, record: PullLeaseRecord, *, create: bool = False) -> None:
        body = {
            "version": 1,
            "token": record.token,
            "repository": record.repository,
            "worktree": record.worktree,
            "operation": record.operation,
            "phase": record.phase,
            "pid": record.pid,
            "pgid": record.pgid,
        }
        self._record(body)
        envelope = {"body": body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()}
        data = _canonical(envelope)
        temporary = self.path.with_name(f".{self.path.name}.{os.urandom(8).hex()}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if create and self.path.exists():
                raise PullLeaseError("Durable pull writer lease appeared concurrently.")
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
            directory = os.open(self.common_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except PullLeaseError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise PullLeaseError(f"Cannot durably publish pull writer lease: {error}") from error

    @staticmethod
    def _record(body: object) -> PullLeaseRecord:
        required = {
            "version",
            "token",
            "repository",
            "worktree",
            "operation",
            "phase",
            "pid",
            "pgid",
        }
        if not isinstance(body, dict) or set(body) != required or body["version"] != 1:
            raise ValueError("invalid lease body")
        if not isinstance(body["token"], str) or not re.fullmatch(r"[0-9a-f]{64}", body["token"]):
            raise ValueError("invalid lease token")
        for key in ("repository", "worktree"):
            if not isinstance(body[key], str) or not body[key] or "\x00" in body[key]:
                raise ValueError("invalid lease repository identity")
        if body["operation"] not in {"fast-forward", "merge", "rebase"}:
            raise ValueError("invalid lease operation")
        if body["phase"] not in {"prepared", "running", "paused", "uncertain"}:
            raise ValueError("invalid lease phase")
        if (
            not isinstance(body["pid"], int)
            or isinstance(body["pid"], bool)
            or body["pid"] < 0
            or not isinstance(body["pgid"], int)
            or isinstance(body["pgid"], bool)
            or body["pgid"] != body["pid"]
        ):
            raise ValueError("invalid lease process identity")
        if body["phase"] == "running" and body["pid"] <= 0:
            raise ValueError("running lease lacks process identity")
        return PullLeaseRecord(
            token=body["token"],
            repository=body["repository"],
            worktree=body["worktree"],
            operation=body["operation"],
            phase=body["phase"],
            pid=body["pid"],
            pgid=body["pgid"],
        )
