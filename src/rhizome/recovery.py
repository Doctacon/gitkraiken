"""Bounded, private recovery checkpoints for protected Git operations.

The journal captures only explicitly requested tracked paths and, optionally, a
self-contained Git bundle.  Recovery is deliberately aside: file and index
preimages remain separate review copies and history is imported under a new
branch without checking it out.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path, PurePosixPath
from typing import Any

from .git import Repository

_SCHEMA = 1
_ID_RE = re.compile(r"[0-9a-f]{32}")
_TERMINAL_OUTCOMES = frozenset({"succeeded", "failed", "cancelled"})
_SUPPORTED_MODES = frozenset({"100644", "100755"})
_METADATA = "checkpoint.json"
_HISTORY = "history.bundle"
_PREIMAGES = "files/preimages.json"


class RecoveryError(RuntimeError):
    """A checkpoint cannot be created, trusted, retained, or recovered safely."""


@dataclass(frozen=True)
class Checkpoint:
    id: str
    repository: str
    operation: str
    head: str
    created_at: datetime
    size_bytes: int
    active: bool
    pinned: bool


@dataclass(frozen=True)
class RecoveryResult:
    branch: str
    directory: Path | None
    summary: str


@dataclass(frozen=True)
class PullSession:
    """Validated durable ownership metadata for one guarded pull integration."""

    checkpoint_id: str
    repository: str
    kind: str
    branch: str
    original_head: str
    target_oid: str
    outgoing: int
    phase: str
    pid: int
    pgid: int
    lease_token: str
    operation_manifest: str
    other_refs: str
    resolution_checkpoints: tuple[str, ...]


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _envelope(body: dict[str, Any]) -> bytes:
    digest = hashlib.sha256(_canonical_json(body)).hexdigest()
    return _canonical_json({"body": body, "sha256": digest})


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_file(path: Path) -> str:
    fingerprint = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            fingerprint.update(chunk)
    return fingerprint.hexdigest()


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape")


def _safe_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise RecoveryError(
            "Recovery paths must be nonempty POSIX paths without NUL bytes or backslashes."
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or value != pure.as_posix()
        or pure == PurePosixPath(".")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise RecoveryError(f"Recovery path is not a canonical repository-relative path: {value!r}")
    return pure


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RecoveryError(f"Recovery storage is not a private directory: {path}")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RecoveryError(f"Recovery storage is not owned by the current user: {path}")
        path.chmod(0o700)
    except RecoveryError:
        raise
    except OSError as error:
        raise RecoveryError(f"Cannot create private recovery storage at {path}: {error}") from error


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        path.chmod(0o600)
    except OSError as error:
        raise RecoveryError(f"Cannot write recovery content at {path}: {error}") from error


def _replace_private(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.urandom(8).hex()}")
    _write_private(temporary, data)
    try:
        os.replace(temporary, path)
        path.chmod(0o600)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise RecoveryError(f"Cannot atomically update recovery metadata: {error}") from error


def _serialized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock():
            return method(self, *args, **kwargs)

    return locked


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise RecoveryError(f"Cannot durably publish recovery storage: {error}") from error


class RecoveryJournal:
    """A size/age-bounded journal rooted in a private app-owned directory."""

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = 1024**3,
        max_age_days: int = 30,
    ) -> None:
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise RecoveryError("Recovery content budget must be a positive byte count.")
        if not isinstance(max_age_days, int) or isinstance(max_age_days, bool) or max_age_days < 0:
            raise RecoveryError("Recovery maximum age must be a nonnegative day count.")
        self.root = Path(os.path.abspath(Path(root).expanduser()))
        self.max_bytes = max_bytes
        self.max_age_days = max_age_days
        _private_directory(self.root)
        self._checkpoints = self.root / "checkpoints"
        with self._lock():
            _private_directory(self._checkpoints)
            self._discard_unpublished()

    @contextmanager
    def _lock(self):
        lock_path = self.root / ".journal.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise RecoveryError("Recovery journal lock is not a regular private file.")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except RecoveryError:
            raise
        except OSError as error:
            raise RecoveryError(f"Cannot lock the recovery journal safely: {error}") from error
        finally:
            if "descriptor" in locals():
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    @_serialized
    def prepare(
        self,
        repo: Repository,
        *,
        operation: str,
        paths: tuple[str, ...] = (),
        history: bool = False,
        worktree_only: bool = False,
    ) -> Checkpoint:
        """Durably publish a checkpoint before a protected operation may start."""
        self._validate_label(operation, "Operation")
        if not isinstance(paths, tuple) or not all(isinstance(path, str) for path in paths):
            raise RecoveryError("Recovery paths must be supplied as a tuple of strings.")
        if not isinstance(history, bool):
            raise RecoveryError("History checkpoint selection must be true or false.")
        if not isinstance(worktree_only, bool):
            raise RecoveryError("Worktree-only checkpoint selection must be true or false.")
        pure_paths = tuple(_safe_path(path) for path in paths)
        if len(set(pure_paths)) != len(pure_paths):
            raise RecoveryError("Recovery paths must not contain duplicates.")

        bodies = self._clean_for_admission(0)
        # Completed, unpinned checkpoints are eligible for capacity eviction.
        # Admission is constrained by content which policy forbids evicting.
        protected_usage = sum(
            int(body["size_bytes"]) for body in bodies if body["active"] or body["pinned"]
        )
        available = self.max_bytes - protected_usage
        if available <= 0:
            raise RecoveryError(
                "Recovery capacity is exhausted by active or pinned checkpoints; operation stopped."
            )

        head = self._head(repo)
        repository = os.fspath(repo.root)
        checkpoint_id = os.urandom(16).hex()
        staging = self._checkpoints / f".tmp-{checkpoint_id}"
        destination = self._checkpoints / checkpoint_id
        try:
            staging.mkdir(mode=0o700)
            files_root = staging / "files"
            worktree_root = files_root / "worktree"
            index_root = files_root / "index"
            for directory in (files_root, worktree_root, index_root):
                directory.mkdir(mode=0o700)

            path_entries: list[dict[str, Any]] = []
            preflight_bytes = 0
            worktree_only_index = (
                repo._git(
                    ["ls-files", "--stage", "-z"],
                    operation="review resolution checkpoint index",
                ).stdout
                if worktree_only
                else b""
            )
            for path, pure in zip(paths, pure_paths, strict=True):
                if worktree_only:
                    index_state, tracked = self._worktree_only_index_state(repo, path, head)
                else:
                    index_state, tracked = self._index_state(repo, path, head)
                if not tracked:
                    raise RecoveryError(
                        f"Cannot checkpoint untracked path {path!r}; only requested tracked paths are supported."
                    )
                worktree_state = self._worktree_state(repo, path)
                preflight_bytes += int(index_state.get("size", 0))
                preflight_bytes += int(worktree_state.get("size", 0))
                if preflight_bytes >= available:
                    raise RecoveryError(
                        "Requested file preimages exceed the recovery content budget."
                    )

                index_record = self._materialize_git_state(
                    repo, index_root, pure, index_state, available
                )
                worktree_record = self._materialize_worktree_state(
                    repo, worktree_root, pure, worktree_state
                )
                path_entries.append(
                    {"path": path, "index": index_record, "worktree": worktree_record}
                )

            if path_entries:
                _write_private(
                    staging / _PREIMAGES,
                    _canonical_json(self._review_manifest(path_entries)),
                )

            # Revalidate the captured identities after all reads. A moving index,
            # worktree file, or HEAD is not a trustworthy pre-action checkpoint.
            for entry in path_entries:
                if worktree_only:
                    current, _ = self._worktree_only_index_state(repo, entry["path"], head)
                else:
                    current, _ = self._index_state(repo, entry["path"], head)
                recorded = entry["index"]
                if (
                    bool(current["present"]) != bool(recorded["present"])
                    or current.get("mode") != recorded.get("mode")
                    or current.get("oid") != recorded.get("oid")
                ):
                    raise RecoveryError("The Git index changed while its checkpoint was captured.")
            if (
                worktree_only
                and repo._git(
                    ["ls-files", "--stage", "-z"],
                    operation="revalidate resolution checkpoint index",
                ).stdout
                != worktree_only_index
            ):
                raise RecoveryError("The Git index changed while its checkpoint was captured.")
            if self._head(repo) != head:
                raise RecoveryError("HEAD changed while the recovery checkpoint was captured.")

            history_record: dict[str, Any] = {"present": False}
            if history and head:
                self._preflight_history(repo, available - preflight_bytes)
                bundle_path = staging / _HISTORY
                repo._git(
                    [
                        "--no-replace-objects",
                        "bundle",
                        "create",
                        os.fspath(bundle_path),
                        "--all",
                        "HEAD",
                    ],
                    operation="create recovery history bundle",
                )
                bundle_path.chmod(0o600)
                repo._git(
                    ["--no-replace-objects", "bundle", "verify", os.fspath(bundle_path)],
                    operation="verify recovery history bundle",
                )
                bundle_size = bundle_path.stat().st_size
                history_record = {
                    "present": True,
                    "payload": _HISTORY,
                    "size": bundle_size,
                    "sha256": _digest_file(bundle_path),
                }

            created_ns = time.time_ns()
            body: dict[str, Any] = {
                "version": _SCHEMA,
                "id": checkpoint_id,
                "repository": repository,
                "operation": operation,
                "head": head,
                "created_ns": created_ns,
                "size_bytes": 0,
                "active": True,
                "pinned": False,
                "outcome": "pending",
                "paths": path_entries,
                "history": history_record,
            }
            payload_size = self._tree_size(staging)
            while True:
                encoded = _envelope(body)
                exact_size = payload_size + len(encoded)
                if body["size_bytes"] == exact_size:
                    break
                body["size_bytes"] = exact_size
            # Bundle size is known only after Git creates it. The conservative
            # preflight normally catches oversize history; this exact admission
            # evicts only completed/unpinned checkpoints and is authoritative.
            self._admit_existing(bodies, exact_size)
            _write_private(staging / _METADATA, encoded)
            if self._tree_size(staging) != exact_size:
                raise RecoveryError("Recovery byte accounting changed before publication.")
            self._validate_tree(staging, body)
            _fsync_directory(staging)
            os.replace(staging, destination)
            _fsync_directory(self._checkpoints)
            return self._checkpoint(body)
        except Exception as error:
            try:
                if staging.exists() and not staging.is_symlink():
                    shutil.rmtree(staging)
            except OSError as cleanup_error:
                raise RecoveryError(
                    f"Checkpoint failed and its unpublished temporary data could not be removed: {cleanup_error}"
                ) from error
            if isinstance(error, RecoveryError):
                raise
            raise RecoveryError(
                f"Could not prepare the required recovery checkpoint: {error}"
            ) from error

    @_serialized
    def complete(self, checkpoint_id: str, outcome: str = "succeeded") -> None:
        """Mark a known terminal outcome inactive; unknown outcomes remain protected."""
        self._validate_label(outcome, "Outcome")
        directory, body = self._load_id(checkpoint_id)
        body["outcome"] = outcome
        body["active"] = outcome not in _TERMINAL_OUTCOMES
        self._rewrite(directory, body)
        self._cleanup(0)

    @_serialized
    def start_pull_session(
        self,
        checkpoint_id: str,
        *,
        repository: str,
        kind: str,
        branch: str,
        original_head: str,
        target_oid: str,
        outgoing: int,
        lease_token: str,
        other_refs: str,
    ) -> PullSession:
        """Attach immutable pull ownership to an active history checkpoint once."""
        directory, body = self._load_id(checkpoint_id)
        if not body["active"] or not body["history"].get("present"):
            raise RecoveryError("Pull ownership requires an active history checkpoint.")
        if body["repository"] != repository or body["head"] != original_head:
            raise RecoveryError("Pull ownership does not match its recovery checkpoint.")
        if body["operation"] != f"pull-{kind}":
            raise RecoveryError("Pull ownership does not match the checkpoint operation.")
        if "pull_session" in body:
            raise RecoveryError("Pull ownership metadata is single-use and already exists.")
        session = {
            "version": 1,
            "kind": kind,
            "branch": branch,
            "original_head": original_head,
            "target_oid": target_oid,
            "outgoing": outgoing,
            "phase": "prepared",
            "pid": 0,
            "pgid": 0,
            "lease_token": lease_token,
            "operation_manifest": "",
            "other_refs": other_refs,
            "resolution_checkpoints": [],
        }
        try:
            self._validate_pull_session(body, session)
        except ValueError as error:
            raise RecoveryError("Invalid pull ownership metadata.") from error
        body["pull_session"] = session
        self._rewrite(directory, body)
        return self._pull_session(body)

    @_serialized
    def update_pull_session(
        self,
        checkpoint_id: str,
        *,
        phase: str,
        pid: int = 0,
        operation_manifest: str | None = None,
        resolution_checkpoint: str = "",
    ) -> PullSession:
        """Durably record native ownership before wait or a verified paused/uncertain state."""
        directory, body = self._load_id(checkpoint_id)
        if not body["active"] or "pull_session" not in body:
            raise RecoveryError("There is no active pull ownership metadata to update.")
        session = dict(body["pull_session"])
        current = session["phase"]
        transitions = {
            "prepared": {"running", "uncertain"},
            "running": {"paused", "uncertain", "running"},
            "paused": {"paused", "running", "uncertain"},
            "uncertain": {"uncertain"},
        }
        if phase not in transitions.get(current, set()):
            raise RecoveryError("Invalid pull ownership metadata transition.")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid < 0:
            raise RecoveryError("Pull process identifier must be a nonnegative integer.")
        if phase == "running" and pid <= 0:
            raise RecoveryError("Running pull ownership requires a native process identifier.")
        if phase != "running" and pid == 0:
            pid = session["pid"]
        session.update(phase=phase, pid=pid, pgid=pid)
        if operation_manifest is not None:
            session["operation_manifest"] = operation_manifest
        if resolution_checkpoint:
            resolution_directory, resolution_body = self._load_id(resolution_checkpoint)
            del resolution_directory
            if (
                not resolution_body["active"]
                or resolution_body["repository"] != body["repository"]
                or resolution_body["operation"] != "pull-continue-resolution"
            ):
                raise RecoveryError("Pull resolution checkpoint has invalid scope.")
            if resolution_checkpoint in session["resolution_checkpoints"]:
                raise RecoveryError("Pull resolution checkpoint is already attached.")
            session["resolution_checkpoints"] = [
                *session["resolution_checkpoints"],
                resolution_checkpoint,
            ]
        try:
            self._validate_pull_session(body, session)
        except ValueError as error:
            raise RecoveryError("Invalid pull ownership metadata update.") from error
        body["pull_session"] = session
        self._rewrite(directory, body)
        return self._pull_session(body)

    @_serialized
    def pull_sessions(self, repository: str) -> tuple[PullSession, ...]:
        """Return only trusted active pull sessions; legacy checkpoints remain valid."""
        if not isinstance(repository, str):
            raise RecoveryError("Repository filter must be a path string.")
        loaded = self._load_all()
        bodies = {body["id"]: body for _directory, body in loaded}
        sessions = []
        for _directory, body in loaded:
            if body["active"] and body["repository"] == repository and "pull_session" in body:
                session = self._pull_session(body)
                for checkpoint_id in session.resolution_checkpoints:
                    resolution = bodies.get(checkpoint_id)
                    if (
                        resolution is None
                        or not resolution["active"]
                        or resolution["repository"] != repository
                        or resolution["operation"] != "pull-continue-resolution"
                    ):
                        raise RecoveryError(
                            "Pull ownership references missing or inactive resolution recovery."
                        )
                sessions.append(session)
        return tuple(sessions)

    @_serialized
    def list(self, repository: str | None = None) -> tuple[Checkpoint, ...]:
        """Return trusted retained checkpoints, newest first."""
        if repository is not None and not isinstance(repository, str):
            raise RecoveryError("Repository filter must be a path string or None.")
        bodies = self._cleanup(0)
        selected = (
            body for body in bodies if repository is None or body["repository"] == repository
        )
        return tuple(
            self._checkpoint(body)
            for body in sorted(selected, key=lambda item: int(item["created_ns"]), reverse=True)
        )

    @_serialized
    def pin(self, id: str, pinned: bool = True) -> None:
        if not isinstance(pinned, bool):
            raise RecoveryError("Pinned state must be true or false.")
        directory, body = self._load_id(id)
        body["pinned"] = pinned
        self._rewrite(directory, body)

    @_serialized
    def purge(self, id: str) -> None:
        """Explicitly remove a checkpoint, including active or pinned content."""
        directory, _ = self._load_id(id)
        try:
            shutil.rmtree(directory)
            _fsync_directory(self._checkpoints)
        except OSError as error:
            raise RecoveryError(f"Could not purge recovery checkpoint {id}: {error}") from error

    @_serialized
    def recover_aside(self, repo: Repository, id: str) -> RecoveryResult:
        """Import history to a new branch and expose file/index review copies."""
        directory, body = self._load_id(id)
        if os.fspath(repo.root) != body["repository"]:
            raise RecoveryError("Checkpoint belongs to a different repository worktree.")

        files_directory: Path | None = None
        if body["paths"]:
            files_directory = directory / "files"

        branch = ""
        history = body["history"]
        if history["present"]:
            bundle = directory / history["payload"]
            repo._git(
                ["--no-replace-objects", "bundle", "verify", os.fspath(bundle)],
                operation="verify recovery history bundle",
            )
            base = f"rhizome-recovery/{id[:12]}"
            candidate = base
            for suffix in range(1, 101):
                result = repo._git(
                    ["show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"],
                    ok_codes=(0, 1),
                    operation="check recovery branch",
                )
                if result.returncode == 1:
                    break
                candidate = f"{base}-{suffix + 1}"
            else:
                raise RecoveryError("Could not allocate a unique recovery branch name.")
            repo._git(
                ["--no-replace-objects", "bundle", "unbundle", os.fspath(bundle)],
                operation="import recovery history objects",
            )
            repo._git(
                ["--no-replace-objects", "cat-file", "-e", f"{body['head']}^{{commit}}"],
                operation="validate recovered history",
            )
            repo._git(
                ["branch", candidate, body["head"]],
                operation="create recovery branch",
            )
            branch = candidate

        recovered: list[str] = []
        if branch:
            recovered.append(f"history on branch {branch}")
        if files_directory is not None:
            recovered.append(f"worktree and index preimages in {files_directory}")
        if not recovered:
            summary = "Checkpoint contains no history or file preimages to recover."
        else:
            summary = (
                "Recovered aside: " + "; ".join(recovered) + ". Current checkout was not changed."
            )
        return RecoveryResult(branch=branch, directory=files_directory, summary=summary)

    @staticmethod
    def _review_manifest(path_entries: list[dict[str, Any]]) -> dict[str, Any]:
        paths: list[dict[str, Any]] = []
        for entry in path_entries:
            states: dict[str, Any] = {"path": entry["path"]}
            for kind in ("index", "worktree"):
                source = entry[kind]
                state: dict[str, Any] = {"present": source["present"]}
                for key in ("mode", "oid", "size"):
                    if key in source:
                        state[key] = source[key]
                states[kind] = state
            paths.append(states)
        return {"version": _SCHEMA, "paths": paths}

    @staticmethod
    def _validate_label(value: str, label: str) -> None:
        if not isinstance(value, str) or not value or len(value) > 200:
            raise RecoveryError(f"{label} must be a nonempty string of at most 200 characters.")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise RecoveryError(f"{label} contains unsupported control characters.")

    @staticmethod
    def _head(repo: Repository) -> str:
        result = repo._git(
            ["rev-parse", "--verify", "--quiet", "HEAD"],
            ok_codes=(0, 1),
            operation="read checkpoint HEAD",
        )
        return _decode(result.stdout).strip() if result.returncode == 0 else ""

    @staticmethod
    def _parse_index(data: bytes, requested: str) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        for raw in data.split(b"\x00"):
            if not raw:
                continue
            prefix, separator, raw_path = raw.partition(b"\t")
            fields = prefix.decode("ascii", "strict").split()
            path = _decode(raw_path)
            if not separator or len(fields) != 3:
                raise RecoveryError("Git returned malformed index metadata.")
            if path == requested:
                records.append({"mode": fields[0], "oid": fields[1].lower(), "stage": fields[2]})
        return records

    def _worktree_only_index_state(
        self, repo: Repository, path: str, head: str
    ) -> tuple[dict[str, Any], bool]:
        result = repo._git(
            ["ls-files", "--stage", "-z", "--", path],
            literal_paths=True,
            operation="inspect resolution checkpoint path",
        )
        try:
            records = self._parse_index(result.stdout, path)
        except (UnicodeDecodeError, ValueError) as error:
            raise RecoveryError("Git returned malformed index metadata.") from error
        stages = [record["stage"] for record in records]
        if any(stage not in {"0", "1", "2", "3"} for stage in stages) or len(stages) != len(
            set(stages)
        ):
            raise RecoveryError("Git returned malformed conflicted index metadata.")
        if "0" in stages:
            if len(records) != 1:
                raise RecoveryError("Git returned mixed resolved and conflicted index stages.")
            record = records[0]
            if record["mode"] not in _SUPPORTED_MODES:
                raise RecoveryError(
                    f"Unsupported tracked representation for {path!r} (mode {record['mode']})."
                )
            size_result = repo._git(
                ["--no-replace-objects", "cat-file", "-s", record["oid"]],
                operation="size resolution checkpoint index blob",
            )
            try:
                size = int(_decode(size_result.stdout).strip())
            except ValueError as error:
                raise RecoveryError("Git returned an invalid index blob size.") from error
            return {
                "present": True,
                "mode": record["mode"],
                "oid": record["oid"],
                "size": size,
            }, True
        if any(record["mode"] not in _SUPPORTED_MODES for record in records):
            raise RecoveryError(f"Unsupported conflicted index representation for {path!r}.")
        tracked = bool(records)
        if not tracked and head:
            tree = repo._git(
                ["--no-replace-objects", "ls-tree", "-z", "HEAD", "--", path],
                literal_paths=True,
                operation="inspect resolution checkpoint tracked path",
            )
            tracked = any(
                raw.partition(b"\t")[2] == os.fsencode(path)
                for raw in tree.stdout.split(b"\x00")
                if raw
            )
        # Stages 1-3 have no single index blob. Their source histories remain in
        # the active history checkpoint, while the exact worktree file is saved.
        return {"present": False}, tracked

    def _index_state(self, repo: Repository, path: str, head: str) -> tuple[dict[str, Any], bool]:
        result = repo._git(
            ["ls-files", "--stage", "-z", "--", path],
            literal_paths=True,
            operation="inspect checkpoint index path",
        )
        try:
            records = self._parse_index(result.stdout, path)
        except (UnicodeDecodeError, ValueError) as error:
            raise RecoveryError("Git returned malformed index metadata.") from error
        if len(records) > 1 or (records and records[0]["stage"] != "0"):
            raise RecoveryError(f"Conflicted index representation is unsupported for {path!r}.")
        index = records[0] if records else None

        head_record: dict[str, str] | None = None
        if head:
            tree = repo._git(
                ["--no-replace-objects", "ls-tree", "-z", "HEAD", "--", path],
                literal_paths=True,
                operation="inspect checkpoint tracked path",
            )
            for raw in tree.stdout.split(b"\x00"):
                if not raw:
                    continue
                prefix, separator, raw_path = raw.partition(b"\t")
                fields = prefix.decode("ascii", "strict").split()
                if not separator or len(fields) != 3:
                    raise RecoveryError("Git returned malformed tree metadata.")
                if _decode(raw_path) == path:
                    head_record = {"mode": fields[0], "type": fields[1], "oid": fields[2]}
        for record in (index, head_record):
            if record is not None and record["mode"] not in _SUPPORTED_MODES:
                raise RecoveryError(
                    f"Unsupported tracked representation for {path!r} (mode {record['mode']})."
                )
        if index is None:
            return {"present": False}, head_record is not None
        size_result = repo._git(
            ["--no-replace-objects", "cat-file", "-s", index["oid"]],
            operation="size checkpoint index blob",
        )
        try:
            size = int(_decode(size_result.stdout).strip())
        except ValueError as error:
            raise RecoveryError("Git returned an invalid index blob size.") from error
        return {
            "present": True,
            "mode": index["mode"],
            "oid": index["oid"],
            "size": size,
        }, True

    @staticmethod
    def _worktree_state(repo: Repository, path: str) -> dict[str, Any]:
        pure = _safe_path(path)
        current = repo.root
        for component in pure.parts[:-1]:
            current = current / component
            try:
                info = current.lstat()
            except FileNotFoundError:
                return {"present": False}
            except OSError as error:
                raise RecoveryError(f"Cannot inspect worktree path {path!r}: {error}") from error
            if stat.S_ISLNK(info.st_mode):
                raise RecoveryError(f"Refusing to follow symlink ancestor for {path!r}.")
            if not stat.S_ISDIR(info.st_mode):
                return {"present": False}
        target = repo.root.joinpath(*pure.parts)
        try:
            info = target.lstat()
        except FileNotFoundError:
            return {"present": False}
        except OSError as error:
            raise RecoveryError(f"Cannot inspect worktree path {path!r}: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise RecoveryError(
                f"Tracked symlink worktree representation is unsupported: {path!r}."
            )
        if not stat.S_ISREG(info.st_mode):
            raise RecoveryError(
                f"Only regular tracked worktree files can be checkpointed: {path!r}."
            )
        mode = "100755" if info.st_mode & 0o111 else "100644"
        return {
            "present": True,
            "mode": mode,
            "size": info.st_size,
            "identity": [
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                stat.S_IMODE(info.st_mode),
            ],
        }

    @staticmethod
    def _payload_path(root: Path, pure: PurePosixPath) -> Path:
        destination = root.joinpath(*pure.parts)
        _private_directory(destination.parent)
        return destination

    def _materialize_git_state(
        self,
        repo: Repository,
        root: Path,
        pure: PurePosixPath,
        state: dict[str, Any],
        remaining: int,
    ) -> dict[str, Any]:
        if not state["present"]:
            return {"present": False}
        if int(state["size"]) > remaining:
            raise RecoveryError("Requested index preimage exceeds the recovery content budget.")
        result = repo._git(
            ["--no-replace-objects", "cat-file", "blob", state["oid"]],
            operation="read checkpoint index blob",
        )
        data = result.stdout
        if len(data) != state["size"]:
            raise RecoveryError("Git index blob size changed while capturing a checkpoint.")
        destination = self._payload_path(root, pure)
        _write_private(destination, data)
        if state["mode"] == "100755":
            destination.chmod(0o700)
        return {
            "present": True,
            "mode": state["mode"],
            "oid": state["oid"],
            "payload": destination.relative_to(root.parent.parent).as_posix(),
            "size": len(data),
            "sha256": _digest(data),
        }

    def _materialize_worktree_state(
        self,
        repo: Repository,
        root: Path,
        pure: PurePosixPath,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        if not state["present"]:
            return {"present": False}
        source = repo.root.joinpath(*pure.parts)
        if not hasattr(os, "O_NOFOLLOW"):
            raise RecoveryError(
                "This platform cannot safely checkpoint worktree files without following symlinks."
            )
        try:
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as input_file:
                opened = os.fstat(input_file.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise RecoveryError(
                        f"Only regular tracked worktree files can be checkpointed: {pure.as_posix()!r}."
                    )
                data = input_file.read()
                after = os.fstat(input_file.fileno())
        except RecoveryError:
            raise
        except OSError as error:
            raise RecoveryError(
                f"Cannot read worktree preimage {pure.as_posix()!r}: {error}"
            ) from error
        identity = [
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            stat.S_IMODE(after.st_mode),
        ]
        if identity != state["identity"] or len(data) != state["size"]:
            raise RecoveryError("A worktree file changed while its checkpoint was captured.")
        destination = self._payload_path(root, pure)
        _write_private(destination, data)
        if state["mode"] == "100755":
            destination.chmod(0o700)
        return {
            "present": True,
            "mode": state["mode"],
            "payload": destination.relative_to(root.parent.parent).as_posix(),
            "size": len(data),
            "sha256": _digest(data),
        }

    def _preflight_history(self, repo: Repository, remaining: int) -> None:
        result = repo._git(["count-objects", "-v"], operation="estimate recovery history size")
        values: dict[str, int] = {}
        for line in _decode(result.stdout).splitlines():
            key, separator, value = line.partition(": ")
            if separator and value.isdigit():
                values[key] = int(value)
        if not {"size", "size-pack"}.issubset(values):
            raise RecoveryError("Git did not provide a trustworthy history size estimate.")
        stored = (values["size"] + values["size-pack"] + values.get("size-garbage", 0)) * 1024
        # A newly packed bundle can differ from current packing. Refuse unless a
        # conservative two-times storage estimate plus framing fits first.
        estimate = stored * 2 + 64 * 1024
        if estimate > remaining:
            raise RecoveryError(
                "History checkpoint may exceed the remaining recovery budget; operation stopped before bundling."
            )
        try:
            free = shutil.disk_usage(self.root).free
        except OSError as error:
            raise RecoveryError(
                f"Cannot preflight free space for history recovery: {error}"
            ) from error
        if estimate > free:
            raise RecoveryError("Insufficient free space for the required history checkpoint.")

    def _discard_unpublished(self) -> None:
        try:
            for entry in self._checkpoints.iterdir():
                if not entry.name.startswith(".tmp-"):
                    continue
                if entry.is_symlink() or not entry.is_dir():
                    raise RecoveryError(
                        "Unsafe unpublished recovery state requires manual inspection."
                    )
                shutil.rmtree(entry)
        except RecoveryError:
            raise
        except OSError as error:
            raise RecoveryError(f"Cannot clean unpublished recovery state: {error}") from error

    def _directories(self) -> list[Path]:
        self._discard_unpublished()
        try:
            entries = list(self._checkpoints.iterdir())
        except OSError as error:
            raise RecoveryError(f"Cannot inspect recovery journal: {error}") from error
        directories: list[Path] = []
        for entry in entries:
            if not _ID_RE.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
                raise RecoveryError(
                    f"Unexpected or unsafe recovery state at {entry}; refusing cleanup and recovery."
                )
            directories.append(entry)
        return directories

    def _read_body(self, directory: Path) -> dict[str, Any]:
        metadata = directory / _METADATA
        try:
            metadata_info = metadata.lstat()
            if (
                stat.S_ISLNK(metadata_info.st_mode)
                or not stat.S_ISREG(metadata_info.st_mode)
                or metadata_info.st_size > self.max_bytes
            ):
                raise ValueError("unsafe metadata file")
            raw = metadata.read_bytes()
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or set(envelope) != {"body", "sha256"}:
                raise ValueError("invalid envelope")
            body = envelope["body"]
            if not isinstance(body, dict):
                raise ValueError("invalid body")
            if raw != _canonical_json(envelope):
                raise ValueError("noncanonical metadata")
            if not isinstance(envelope["sha256"], str) or not hmac.compare_digest(
                envelope["sha256"], _digest(_canonical_json(body))
            ):
                raise ValueError("metadata fingerprint mismatch")
            self._validate_body(directory, body)
            return body
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise RecoveryError(
                f"Recovery checkpoint {directory.name} is corrupt; refusing to use or evict it."
            ) from error

    def _validate_body(self, directory: Path, body: dict[str, Any]) -> None:
        required = {
            "version",
            "id",
            "repository",
            "operation",
            "head",
            "created_ns",
            "size_bytes",
            "active",
            "pinned",
            "outcome",
            "paths",
            "history",
        }
        if (
            frozenset(body) not in {frozenset(required), frozenset(required | {"pull_session"})}
            or body["version"] != _SCHEMA
            or body["id"] != directory.name
        ):
            raise ValueError("invalid checkpoint identity")
        if not isinstance(body["repository"], str) or not isinstance(body["head"], str):
            raise ValueError("invalid repository identity")
        self._validate_label(body["operation"], "Operation")
        self._validate_label(body["outcome"], "Outcome")
        if not isinstance(body["created_ns"], int) or body["created_ns"] < 0:
            raise ValueError("invalid creation time")
        if not isinstance(body["size_bytes"], int) or body["size_bytes"] <= 0:
            raise ValueError("invalid size")
        if not isinstance(body["active"], bool) or not isinstance(body["pinned"], bool):
            raise ValueError("invalid retention state")
        if not isinstance(body["paths"], list) or not isinstance(body["history"], dict):
            raise ValueError("invalid payload records")
        if "pull_session" in body:
            self._validate_pull_session(body, body["pull_session"])
        self._validate_tree(directory, body)
        if self._tree_size(directory) != body["size_bytes"]:
            raise ValueError("retained-byte accounting mismatch")

    def _validate_tree(self, directory: Path, body: dict[str, Any]) -> None:
        expected = {_METADATA}
        seen_paths: set[str] = set()
        for entry in body["paths"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "index", "worktree"}:
                raise ValueError("invalid path record")
            path = entry["path"]
            if not isinstance(path, str) or path in seen_paths:
                raise ValueError("invalid path identity")
            seen_paths.add(path)
            _safe_path(path)
            for kind in ("index", "worktree"):
                state = entry[kind]
                if not isinstance(state, dict) or not isinstance(state.get("present"), bool):
                    raise ValueError("invalid preimage state")
                if not state["present"]:
                    if set(state) != {"present"}:
                        raise ValueError("invalid absent preimage")
                    continue
                required = {"present", "mode", "payload", "size", "sha256"}
                if kind == "index":
                    required.add("oid")
                if set(state) != required or state["mode"] not in _SUPPORTED_MODES:
                    raise ValueError("invalid present preimage")
                expected_payload = f"files/{kind}/{path}"
                if state["payload"] != expected_payload:
                    raise ValueError("invalid payload location")
                self._validate_payload(directory, state, expected_payload)
                expected.add(expected_payload)
        if body["paths"]:
            manifest = directory / _PREIMAGES
            manifest_info = manifest.lstat()
            if stat.S_ISLNK(manifest_info.st_mode) or not stat.S_ISREG(manifest_info.st_mode):
                raise ValueError("unsafe review manifest")
            if manifest.read_bytes() != _canonical_json(self._review_manifest(body["paths"])):
                raise ValueError("review manifest mismatch")
            expected.add(_PREIMAGES)

        history = body["history"]
        if not isinstance(history.get("present"), bool):
            raise ValueError("invalid history state")
        if history["present"]:
            if set(history) != {"present", "payload", "size", "sha256"}:
                raise ValueError("invalid history payload")
            if history["payload"] != _HISTORY:
                raise ValueError("invalid history location")
            self._validate_payload(directory, history, _HISTORY)
            expected.add(_HISTORY)
        elif set(history) != {"present"}:
            raise ValueError("invalid absent history")

        actual: set[str] = set()
        for candidate in directory.rglob("*"):
            relative = candidate.relative_to(directory).as_posix()
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("symlink in checkpoint")
            if stat.S_ISREG(info.st_mode):
                actual.add(relative)
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError("unsupported checkpoint filesystem node")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("checkpoint content is not private")
        # Metadata does not exist yet during pre-publication validation.
        if not (directory / _METADATA).exists():
            expected.remove(_METADATA)
        if actual != expected:
            raise ValueError("unexpected or missing checkpoint content")

    @staticmethod
    def _validate_payload(directory: Path, state: dict[str, Any], relative: str) -> None:
        pure = _safe_path(relative)
        current = directory
        for component in pure.parts[:-1]:
            current = current / component
            ancestor = current.lstat()
            if stat.S_ISLNK(ancestor.st_mode) or not stat.S_ISDIR(ancestor.st_mode):
                raise ValueError("unsafe payload ancestor")
        target = current / pure.parts[-1]
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError("payload is not a regular file")
        if not isinstance(state["size"], int) or info.st_size != state["size"]:
            raise ValueError("payload size mismatch")
        if not isinstance(state["sha256"], str):
            raise ValueError("payload fingerprint missing")
        if not hmac.compare_digest(_digest_file(target), state["sha256"]):
            raise ValueError("payload fingerprint mismatch")

    @staticmethod
    def _tree_size(directory: Path) -> int:
        total = 0
        for candidate in directory.rglob("*"):
            info = candidate.lstat()
            if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                total += info.st_size
        return total

    def _load_all(self) -> list[tuple[Path, dict[str, Any]]]:
        return [(directory, self._read_body(directory)) for directory in self._directories()]

    def _load_id(self, checkpoint_id: str) -> tuple[Path, dict[str, Any]]:
        if not isinstance(checkpoint_id, str) or not _ID_RE.fullmatch(checkpoint_id):
            raise RecoveryError("Unknown recovery checkpoint identifier.")
        directory = self._checkpoints / checkpoint_id
        if not directory.is_dir() or directory.is_symlink():
            raise RecoveryError(f"Unknown recovery checkpoint: {checkpoint_id}")
        return directory, self._read_body(directory)

    def _rewrite(self, directory: Path, body: dict[str, Any]) -> None:
        payload_size = self._tree_size(directory) - (directory / _METADATA).stat().st_size
        body["size_bytes"] = 0
        while True:
            encoded = _envelope(body)
            exact = payload_size + len(encoded)
            if body["size_bytes"] == exact:
                break
            body["size_bytes"] = exact
        _replace_private(directory / _METADATA, encoded)
        self._read_body(directory)

    def _cleanup(self, incoming: int) -> list[dict[str, Any]]:
        loaded = self._load_all()
        now_ns = time.time_ns()
        maximum_age_ns = self.max_age_days * 24 * 60 * 60 * 1_000_000_000
        retained: list[tuple[Path, dict[str, Any]]] = []
        for directory, body in loaded:
            expired = now_ns - int(body["created_ns"]) >= maximum_age_ns
            if expired and not body["active"] and not body["pinned"]:
                shutil.rmtree(directory)
            else:
                retained.append((directory, body))

        usage = sum(int(body["size_bytes"]) for _, body in retained)
        eligible = sorted(
            (
                (directory, body)
                for directory, body in retained
                if not body["active"] and not body["pinned"]
            ),
            key=lambda item: int(item[1]["created_ns"]),
        )
        evicted: set[str] = set()
        for directory, body in eligible:
            if usage + incoming <= self.max_bytes:
                break
            shutil.rmtree(directory)
            usage -= int(body["size_bytes"])
            evicted.add(body["id"])
        if loaded:
            _fsync_directory(self._checkpoints)
        return [body for _, body in retained if body["id"] not in evicted]

    def _clean_for_admission(self, incoming: int) -> list[dict[str, Any]]:
        bodies = self._cleanup(incoming)
        if sum(int(body["size_bytes"]) for body in bodies) + incoming > self.max_bytes:
            raise RecoveryError(
                "Recovery capacity is exhausted by active or pinned checkpoints; operation stopped."
            )
        return bodies

    def _admit_existing(self, bodies: list[dict[str, Any]], incoming: int) -> None:
        """Make exact room without treating this prepare's staging tree as stale."""
        usage = sum(int(body["size_bytes"]) for body in bodies)
        eligible = sorted(
            (body for body in bodies if not body["active"] and not body["pinned"]),
            key=lambda body: int(body["created_ns"]),
        )
        for body in eligible:
            if usage + incoming <= self.max_bytes:
                break
            try:
                shutil.rmtree(self._checkpoints / body["id"])
            except OSError as error:
                raise RecoveryError(
                    f"Cannot free recovery capacity for a required checkpoint: {error}"
                ) from error
            usage -= int(body["size_bytes"])
        if usage + incoming > self.max_bytes:
            raise RecoveryError(
                "Recovery capacity is exhausted by active or pinned checkpoints; operation stopped."
            )
        if eligible:
            _fsync_directory(self._checkpoints)

    @staticmethod
    def _validate_pull_session(body: dict[str, Any], session: object) -> None:
        if not isinstance(session, dict) or set(session) != {
            "version",
            "kind",
            "branch",
            "original_head",
            "target_oid",
            "outgoing",
            "phase",
            "pid",
            "pgid",
            "lease_token",
            "operation_manifest",
            "other_refs",
            "resolution_checkpoints",
        }:
            raise ValueError("invalid pull ownership metadata")
        if session["version"] != 1 or session["kind"] not in {"merge", "rebase"}:
            raise ValueError("invalid pull ownership kind")
        if (
            not isinstance(session["branch"], str)
            or not session["branch"]
            or len(session["branch"]) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in session["branch"])
        ):
            raise ValueError("invalid pull ownership branch")
        oid_pattern = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
        if not isinstance(session["original_head"], str) or not re.fullmatch(
            oid_pattern, session["original_head"]
        ):
            raise ValueError("invalid pull ownership original head")
        if not isinstance(session["target_oid"], str) or not re.fullmatch(
            oid_pattern, session["target_oid"]
        ):
            raise ValueError("invalid pull ownership target")
        if session["original_head"] != body["head"]:
            raise ValueError("pull ownership head does not match checkpoint")
        if body["operation"] != f"pull-{session['kind']}" or not body["history"].get("present"):
            raise ValueError("pull ownership does not match checkpoint scope")
        if (
            not isinstance(session["outgoing"], int)
            or isinstance(session["outgoing"], bool)
            or session["outgoing"] < 1
        ):
            raise ValueError("invalid pull ownership outgoing count")
        if session["phase"] not in {"prepared", "running", "paused", "uncertain"}:
            raise ValueError("invalid pull ownership phase")
        if not isinstance(session["lease_token"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", session["lease_token"]
        ):
            raise ValueError("invalid pull ownership lease token")
        if not isinstance(session["other_refs"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", session["other_refs"]
        ):
            raise ValueError("invalid pull ownership ref snapshot")
        manifest = session["operation_manifest"]
        if not isinstance(manifest, str) or (
            manifest and not re.fullmatch(r"[0-9a-f]{64}", manifest)
        ):
            raise ValueError("invalid pull operation metadata manifest")
        if session["phase"] == "paused" and not manifest:
            raise ValueError("paused pull ownership lacks operation metadata manifest")
        checkpoints = session["resolution_checkpoints"]
        if (
            not isinstance(checkpoints, list)
            or len(checkpoints) > 1000
            or len(set(checkpoints)) != len(checkpoints)
            or any(
                not isinstance(value, str) or not _ID_RE.fullmatch(value) for value in checkpoints
            )
        ):
            raise ValueError("invalid pull resolution checkpoint list")
        if (
            not isinstance(session["pid"], int)
            or isinstance(session["pid"], bool)
            or session["pid"] < 0
            or not isinstance(session["pgid"], int)
            or isinstance(session["pgid"], bool)
            or session["pgid"] < 0
            or session["pid"] != session["pgid"]
        ):
            raise ValueError("invalid pull process/group identifiers")
        if session["phase"] == "running" and session["pid"] <= 0:
            raise ValueError("running pull ownership lacks process identifier")

    @staticmethod
    def _pull_session(body: dict[str, Any]) -> PullSession:
        session = body["pull_session"]
        return PullSession(
            checkpoint_id=body["id"],
            repository=body["repository"],
            kind=session["kind"],
            branch=session["branch"],
            original_head=session["original_head"],
            target_oid=session["target_oid"],
            outgoing=session["outgoing"],
            phase=session["phase"],
            pid=session["pid"],
            pgid=session["pgid"],
            lease_token=session["lease_token"],
            operation_manifest=session["operation_manifest"],
            other_refs=session["other_refs"],
            resolution_checkpoints=tuple(session["resolution_checkpoints"]),
        )

    @staticmethod
    def _checkpoint(body: dict[str, Any]) -> Checkpoint:
        return Checkpoint(
            id=body["id"],
            repository=body["repository"],
            operation=body["operation"],
            head=body["head"],
            created_at=datetime.fromtimestamp(body["created_ns"] / 1_000_000_000, UTC),
            size_bytes=body["size_bytes"],
            active=body["active"],
            pinned=body["pinned"],
        )
