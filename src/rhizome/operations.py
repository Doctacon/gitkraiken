"""Fail-closed planning and serialized submission of Git mutations."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import threading
import time
import weakref
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .git import GitError, GitOutcomeUncertain, Repository, _redact
from .models import Action
from .patches import PatchDocument, PatchService
from .pull import ManagedPull, PullBackend, PullPlan
from .pull_lease import PullLease, PullLeaseError, PullWriterGuard, PullWriterGuardError
from .recovery import RecoveryError, RecoveryJournal

_ROUTINE_OPERATIONS = frozenset(
    {
        "stage",
        "unstage",
        "commit",
        "checkout",
        "create-branch",
        "create-worktree",
        "tag",
        "set-upstream",
        "fetch",
        "push",
    }
)
_COMPLEX_GATED_OPERATIONS = frozenset(
    {
        "merge",
        "rebase",
        "cherry-pick",
        "revert",
        "reset-soft",
        "reset-mixed",
        "reset-hard",
        "edit-message",
        "stash",
        "stash-apply",
        "stash-pop",
        "stash-drop",
        "apply-patch",
        "pull",
        "continue",
        "abort",
    }
)
_REF_CHANGE_OPERATIONS = frozenset(
    {"rename-branch", "delete-branch", "delete-tag", "delete-remote-branch"}
)
_REWRITE_OPERATIONS = frozenset(
    {"rebase", "reset-soft", "reset-mixed", "reset-hard", "edit-message"}
)
_TARGET_OPERATIONS = frozenset(
    {
        "checkout",
        "create-branch",
        "create-worktree",
        "tag",
        "merge",
        "rebase",
        "cherry-pick",
        "revert",
        "reset-soft",
        "reset-mixed",
        "reset-hard",
        "edit-message",
    }
)
_LOCAL_CONTENT_OPERATIONS = frozenset(
    {"stage", "unstage", "commit", "checkout", "create-branch", "discard"}
)
_MAX_PENDING_PLANS = 256
_MAX_ACTIVITY = 20
_MAX_ACTIVITY_MESSAGE = 2_000

_LOCKS_GUARD = threading.Lock()
_REPOSITORY_LOCKS: weakref.WeakValueDictionary[tuple[int, int], threading.RLock] = (
    weakref.WeakValueDictionary()
)


@dataclass(frozen=True)
class OperationPlan:
    """An immutable, single-use review of one exact mutation request."""

    action: Action
    repository: str
    fingerprint: str
    available: bool
    reason: str
    requires_confirmation: bool
    consequences: tuple[str, ...]


@dataclass(frozen=True)
class OperationActivity:
    """A bounded, immutable view of queued and completed coordinator work."""

    id: str
    operation: str
    state: str
    message: str
    started: datetime
    elapsed: float
    checkpoint_id: str = ""


@dataclass(frozen=True)
class _RepositoryIdentity:
    root_path: str
    root_device: int
    root_inode: int
    git_path: str
    git_device: int
    git_inode: int
    common_path: str
    common_device: int
    common_inode: int

    @property
    def lock_key(self) -> tuple[int, int]:
        return self.common_device, self.common_inode


@dataclass(frozen=True)
class _PendingPlan:
    plan: OperationPlan
    state: str
    patch: PatchDocument | None = None
    selection: tuple[int, int] | None = None


def _decode(value: bytes) -> str:
    return value.decode("utf-8", "surrogateescape")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_activity_message(value: str) -> str:
    redacted = _redact(value)
    cleaned = "".join(
        character if character in "\n\t" or ord(character) >= 32 else "�" for character in redacted
    ).strip()
    if len(cleaned) > _MAX_ACTIVITY_MESSAGE:
        return cleaned[: _MAX_ACTIVITY_MESSAGE - 18] + "\n[output truncated]"
    return cleaned


def _repository_lock(key: tuple[int, int]) -> threading.RLock:
    with _LOCKS_GUARD:
        lock = _REPOSITORY_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REPOSITORY_LOCKS[key] = lock
        return lock


class OperationCoordinator:
    """Review, serialize, revalidate, and execute bounded M1 Git operations.

    Methods are synchronous by design and may be called with ``asyncio.to_thread``.
    Plans are coordinator-owned and single-use; a caller cannot manufacture or
    alter a plan and retain its authority.
    """

    def __init__(
        self,
        repo: Repository,
        *,
        journal: RecoveryJournal | None = None,
        pull_journal: RecoveryJournal | None = None,
        protected_branches: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(repo, Repository):
            raise TypeError("repo must be a Repository")
        if journal is not None and not isinstance(journal, RecoveryJournal):
            raise TypeError("journal must be a RecoveryJournal or None")
        if pull_journal is not None and not isinstance(pull_journal, RecoveryJournal):
            raise TypeError("pull_journal must be a RecoveryJournal or None")
        if not isinstance(protected_branches, tuple) or not all(
            isinstance(value, str) and value for value in protected_branches
        ):
            raise TypeError("protected_branches must be a tuple of nonempty strings")
        if any("\x00" in value for value in protected_branches):
            raise GitError("Protected branch names must not contain NUL bytes.")

        self.repository = repo
        self.journal = journal
        self.pull_journal = pull_journal
        self.protected_branches = protected_branches
        self._identity = self._read_identity()
        self._mutation_lock = _repository_lock(self._identity.lock_key)
        self._pull_lease = PullLease(
            Path(self._identity.common_path),
            repository=self._identity.common_path,
            worktree=self._identity.root_path,
        )
        self._pull_writer_guard = PullWriterGuard(Path(self._identity.common_path))
        self._pending_lock = threading.Lock()
        self._pending: OrderedDict[str, _PendingPlan] = OrderedDict()
        self._activity_lock = threading.Lock()
        self._activities: deque[OperationActivity] = deque(maxlen=_MAX_ACTIVITY)
        self._pull = PullBackend(self, journal=pull_journal)

    def prepare_pull(self, action: Action) -> PullPlan:
        """Fetch and review one exact guarded pull request."""
        return self._pull.prepare(action)

    def submit_pull(self, plan: PullPlan, *, confirmed: bool = False) -> str:
        """Consume and execute one coordinator-owned pull plan."""
        return self._pull.submit(plan, confirmed=confirmed)

    def cancel_pull(self, plan: PullPlan) -> None:
        """Consume a pull plan without integrating its fetched target."""
        self._pull.cancel(plan)

    def managed_pull(self) -> ManagedPull | None:
        """Return a trusted paused pull-owned operation, if one exists."""
        return self._pull.managed()

    def plan(
        self,
        action: Action,
        *,
        patch: PatchDocument | None = None,
        selection: tuple[int, int] | None = None,
    ) -> OperationPlan:
        """Read repository state and return a single-use immutable review plan."""
        if not isinstance(action, Action):
            raise TypeError("action must be an Action")
        self._validate_precision_request(action, patch, selection)
        with self._mutation_lock:
            self._assert_identity()
            self.repository._preflight_context(action)
            state, details = self._capture_state(action)
            if selection is not None:
                details["selection_start"] = str(selection[0])
                details["selection_end"] = str(selection[1])
            available, reason, confirmation = self._availability(action)
            consequences = self._consequences(action, details)
            nonce = secrets.token_bytes(32)
            fingerprint = hashlib.sha256(
                nonce
                + state.encode("ascii")
                + json.dumps(
                    asdict(action),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            plan = OperationPlan(
                action=action,
                repository=os.fspath(self.repository.root),
                fingerprint=fingerprint,
                available=available,
                reason=reason,
                requires_confirmation=confirmation,
                consequences=consequences,
            )
            with self._pending_lock:
                self._pending[fingerprint] = _PendingPlan(
                    plan=plan,
                    state=state,
                    patch=patch,
                    selection=selection,
                )
                self._pending.move_to_end(fingerprint)
                while len(self._pending) > _MAX_PENDING_PLANS:
                    self._pending.popitem(last=False)
            return plan

    def submit(self, plan: OperationPlan, *, confirmed: bool = False) -> str:
        """Submit one reviewed plan once, revalidating after any queue wait."""
        if not isinstance(plan, OperationPlan):
            raise GitError("Only a plan issued by this coordinator can be submitted.")
        if not isinstance(confirmed, bool):
            raise TypeError("confirmed must be a boolean")
        if not plan.available:
            raise GitError(plan.reason or "This operation is unavailable.")
        if plan.requires_confirmation and not confirmed:
            raise GitError("This destructive operation requires explicit confirmation.")

        # Consume authority before waiting. Concurrent or later submissions of
        # the same object cannot execute the mutation twice.
        with self._pending_lock:
            pending = self._pending.pop(plan.fingerprint, None)
        if pending is None:
            raise GitError("This operation plan is unknown, expired, or already submitted.")
        if pending.plan != plan:
            raise GitError("This operation plan was changed after review.")

        activity_id = secrets.token_hex(12)
        started = datetime.now(UTC)
        self._add_activity(
            OperationActivity(
                id=activity_id,
                operation=plan.action.operation,
                state="queued",
                message="Waiting for earlier repository mutations.",
                started=started,
                elapsed=0.0,
            )
        )
        began = time.monotonic()
        checkpoint_id = ""
        mutation_started = False
        checkpoint_outcome_uncertain = False
        try:
            with self._mutation_lock, self._writer_guard():
                self._assert_pull_lease_clear(plan.action)
                self._update_activity(
                    activity_id,
                    state="running",
                    message="Revalidating reviewed repository state.",
                    elapsed=time.monotonic() - began,
                )
                self._assert_identity()
                self._assert_state(plan.action, pending.state)

                if plan.requires_confirmation:
                    if self.journal is None:  # Defensive even if availability changes later.
                        raise GitError("This operation requires a durable recovery checkpoint.")
                    checkpoint = self._prepare_checkpoint(plan.action)
                    checkpoint_id = checkpoint.id
                    self._update_activity(
                        activity_id,
                        state="running",
                        message="Recovery checkpoint saved; revalidating before mutation.",
                        elapsed=time.monotonic() - began,
                        checkpoint_id=checkpoint_id,
                    )
                    try:
                        self._assert_identity()
                        self._assert_state(plan.action, pending.state)
                    except Exception:
                        self._complete_checkpoint(checkpoint_id, "cancelled")
                        raise

                try:
                    mutation_started = True
                    if pending.patch is not None and pending.selection is not None:
                        result = PatchService(self.repository).apply(
                            pending.patch,
                            *pending.selection,
                        )
                    elif plan.action.operation == "commit":

                        def validate_commit() -> None:
                            self._assert_identity()
                            self._assert_state(plan.action, pending.state)

                        result = self.repository._commit_reviewed(plan.action, validate_commit)
                    else:
                        result = self.repository.execute(plan.action)
                except Exception:
                    if checkpoint_id:
                        outcome = (
                            "uncertain" if self._remote_outcome_uncertain(plan.action) else "failed"
                        )
                        self._complete_checkpoint(checkpoint_id, outcome)
                    raise

                if checkpoint_id:
                    try:
                        self._complete_checkpoint(checkpoint_id, "succeeded")
                    except GitError as error:
                        checkpoint_outcome_uncertain = True
                        message = (
                            "Git completed, but the recovery checkpoint outcome could not be "
                            f"recorded. Treat the result as uncertain: {error}"
                        )
                        self._update_activity(
                            activity_id,
                            state="uncertain",
                            message=message,
                            elapsed=time.monotonic() - began,
                            checkpoint_id=checkpoint_id,
                        )
                        raise GitError(message) from error

            self._update_activity(
                activity_id,
                state="succeeded",
                message=result,
                elapsed=time.monotonic() - began,
                checkpoint_id=checkpoint_id,
            )
            return result
        except Exception as error:
            remote_uncertain = (
                mutation_started
                and self._remote_outcome_uncertain(plan.action)
                and isinstance(error, GitError)
            )
            uncertain = (
                checkpoint_outcome_uncertain
                or remote_uncertain
                or isinstance(error, GitOutcomeUncertain)
            )
            state = "uncertain" if uncertain else "failed"
            prefix = (
                "Remote outcome is uncertain. Inspect the destination before retrying. "
                if remote_uncertain
                else ""
            )
            self._update_activity(
                activity_id,
                state=state,
                message=prefix + str(error),
                elapsed=time.monotonic() - began,
                checkpoint_id=checkpoint_id,
            )
            raise

    def activity(self) -> tuple[OperationActivity, ...]:
        """Return a bounded immutable snapshot, newest activity first."""
        with self._activity_lock:
            return tuple(reversed(self._activities))

    @staticmethod
    def _validate_precision_request(
        action: Action,
        patch: PatchDocument | None,
        selection: tuple[int, int] | None,
    ) -> None:
        if patch is None and selection is None:
            return
        if patch is None or selection is None:
            raise GitError("Precision staging requires both a patch and a selection.")
        if not isinstance(patch, PatchDocument):
            raise GitError("Precision staging requires an original PatchDocument.")
        if action.operation not in {"stage", "unstage"}:
            raise GitError("Patch selections are only valid for stage or unstage.")
        if action.paths != (patch.path,):
            raise GitError("The action path must exactly match the selected patch path.")
        expected_staged = action.operation == "unstage"
        if patch.staged != expected_staged:
            direction = "staged" if expected_staged else "unstaged"
            raise GitError(f"{action.operation} requires a selectable {direction} patch.")
        if not patch.selectable:
            raise GitError(patch.reason or "This patch does not support line selection.")
        if (
            not isinstance(selection, tuple)
            or len(selection) != 2
            or any(not isinstance(value, int) or isinstance(value, bool) for value in selection)
            or selection[0] > selection[1]
        ):
            raise GitError("Select a valid inclusive contiguous line range.")
        ids = {line.id for line in patch.lines}
        if selection[0] not in ids or selection[1] not in ids:
            raise GitError("The selected line range is outside this patch.")
        if not any(
            selection[0] <= line.id <= selection[1] and line.kind in {"add", "delete"}
            for line in patch.lines
        ):
            raise GitError("The selected range must contain at least one changed line.")

    def _availability(self, action: Action) -> tuple[bool, str, bool]:
        operation = action.operation
        if operation in _ROUTINE_OPERATIONS:
            return True, "", False
        if operation == "discard":
            if self.journal is not None:
                return True, "", True
            return (
                False,
                "discard is unavailable without a durable recovery checkpoint journal.",
                True,
            )
        if operation in _REF_CHANGE_OPERATIONS:
            protected = self._protected_target(action)
            if protected:
                return (
                    False,
                    f"{protected} is protected from rename or deletion.",
                    True,
                )
            return (
                False,
                f"{operation} is unavailable until exact target-ref restoration is proven.",
                True,
            )
        if operation in _REWRITE_OPERATIONS:
            branch = self.repository._branch()
            full_branch = f"refs/heads/{branch}" if branch else ""
            if branch and ({branch, full_branch} & self._protected_refs()):
                return (
                    False,
                    f"{full_branch} is protected from history rewriting.",
                    True,
                )
        if operation == "reset-hard":
            return (
                False,
                "reset-hard is unavailable because untracked obstruction recovery is not proven.",
                True,
            )
        if operation in _COMPLEX_GATED_OPERATIONS:
            return (
                False,
                f"{operation} is unavailable until its M3 guarded plan and recovery are proven.",
                True,
            )
        return (
            False,
            f"{operation or '(empty)'} is not an enabled M1 operation.",
            False,
        )

    def _protected_target(self, action: Action) -> str:
        target = action.target
        source = action.name if action.operation == "delete-remote-branch" else target
        candidates = {target, source}
        for value in tuple(candidates):
            if not value:
                continue
            candidates.add(value.removeprefix("refs/heads/"))
            candidates.add(f"refs/heads/{value.removeprefix('refs/heads/')}")
            if value.startswith("refs/remotes/"):
                candidates.add("refs/heads/" + value.split("/", 3)[-1])
        protected = self._protected_refs()
        match = next((value for value in candidates if value in protected), "")
        return match

    def _protected_refs(self) -> set[str]:
        protected: set[str] = set()
        for value in self.protected_branches:
            protected.add(value)
            if value.startswith("refs/"):
                if value.startswith("refs/remotes/"):
                    protected.add("refs/heads/" + value.split("/", 3)[-1])
                continue
            protected.add(f"refs/heads/{value}")
            protected.add(f"refs/remotes/{value}")

        result = self.repository._git(
            ["for-each-ref", "--format=%(refname)%00%(symref)", "refs/remotes"],
            operation="detect default branches",
        )
        for line in _decode(result.stdout).splitlines():
            fields = line.split("\x00", 1)
            if len(fields) != 2 or not fields[0].endswith("/HEAD") or not fields[1]:
                continue
            protected.update(fields)
            if fields[1].startswith("refs/remotes/"):
                protected.add("refs/heads/" + fields[1].split("/", 3)[-1])

        local_names = self.repository._local_branches()
        for conventional in ("main", "master", "trunk"):
            if conventional in local_names:
                protected.add(conventional)
                protected.add(f"refs/heads/{conventional}")
        return protected

    def _capture_state(self, action: Action) -> tuple[str, dict[str, str]]:
        operation = action.operation
        state: dict[str, Any] = {
            "identity": asdict(self._read_identity()),
            "head": self.repository._head(),
            "branch": self.repository._branch(),
        }
        details: dict[str, str] = {}
        target = ""

        if operation == "discard" and not action.paths:
            raise GitError("Discard requires one or more explicit tracked file paths.")
        if operation == "checkout" and not action.target:
            raise GitError("Checkout target is required.")
        if operation == "create-worktree" and not action.target:
            raise GitError("Worktree target is required.")

        if operation in _LOCAL_CONTENT_OPERATIONS:
            state["index"] = _hash_bytes(
                self.repository._git(
                    ["ls-files", "--stage", "-z"], operation="review operation index"
                ).stdout
            )
        worktree_paths: tuple[str, ...] = ()
        if operation in {"stage", "unstage", "checkout", "create-branch", "discard"}:
            state["worktree"], worktree_paths = self._worktree_state(action)
        if operation in {"stage", "checkout", "create-branch"}:
            state["config"] = self._config_state()

        if operation in _TARGET_OPERATIONS and action.target:
            target = self.repository._resolve_commit(action.target, label=f"{operation} target")
            state["target_oid"] = target
            details["target_oid"] = target
        elif operation in {"create-branch", "tag"}:
            target = self.repository._resolve_commit("HEAD", label=f"{operation} target")
            state["target_oid"] = target
            details["target_oid"] = target

        if operation == "stage":
            state["attributes"] = self._attributes_state(
                worktree_paths,
                reject_stage_conversions=True,
            )
        elif operation in {"checkout", "create-branch"}:
            target_paths = self._target_changed_paths(state["head"], target)
            attribute_paths = tuple(sorted(set(worktree_paths) | set(target_paths)))
            state["attributes_current"] = self._attributes_state(
                attribute_paths,
                reject_stage_conversions=False,
            )
            state["attributes_target"] = self._attributes_state(
                attribute_paths,
                reject_stage_conversions=False,
                source=target,
            )

        if operation == "commit":
            if not action.message.strip():
                raise GitError("A commit message is required.")
            staged = self.repository._git(
                ["diff", "--cached", "--quiet", "--exit-code", "--no-ext-diff", "--no-textconv"],
                ok_codes=(0, 1),
                operation="review staged changes",
            )
            if staged.returncode == 0:
                raise GitError("There are no staged changes to commit.")
            state["index"] = self.repository._commit_index_fingerprint().hex()
            state["config"] = self._config_state()
        elif operation == "create-branch":
            name = self.repository._validate_branch(action.name)
            state["destination_ref"] = self._ref_oid(f"refs/heads/{name}")
            if state["destination_ref"]:
                raise GitError(f"A branch named {name} already exists.")
            details["name"] = name
        elif operation == "create-worktree":
            if len(action.paths) != 1:
                raise GitError("Create worktree requires exactly one destination path.")
            destination = self.repository._external_path(action.paths[0], "Worktree destination")
            state["destination"] = self._destination_state(destination)
            state["worktrees"] = _hash_bytes(
                self.repository._git(
                    ["worktree", "list", "--porcelain", "-z"],
                    operation="review worktree destinations",
                ).stdout
            )
            details["destination"] = os.fspath(destination)
            if action.name:
                name = self.repository._validate_branch(action.name, "New worktree branch name")
                state["destination_ref"] = self._ref_oid(f"refs/heads/{name}")
                if state["destination_ref"]:
                    raise GitError(f"A branch named {name} already exists.")
                details["name"] = name
        elif operation == "tag":
            name = self.repository._tag_name(action.name)
            state["destination_ref"] = self._ref_oid(f"refs/tags/{name}")
            if state["destination_ref"]:
                raise GitError(f"A tag named {name} already exists.")
            details["name"] = name
        elif operation == "set-upstream":
            if not action.target.startswith("refs/heads/"):
                raise GitError("Set upstream target must be a full refs/heads ref.")
            branch = self.repository._local_branch_name(action.target)
            if action.target not in {
                ref.full_name for ref in self.repository._refs() if ref.kind == "local"
            }:
                raise GitError(f"Unknown local branch: {action.target}")
            selected, source = self.repository._exact_remote_tracking_ref(
                action.name, action.remote
            )
            if not source.startswith("refs/heads/"):
                raise GitError(
                    f"Remote tracking ref {selected.full_name} does not map to a branch source."
                )
            state["refs"] = self._refs_state()
            state["config"] = self._config_state()
            details.update(branch=branch, remote=selected.remote, source=source)
        elif operation == "fetch":
            scopes = self.repository._validate_fetch_scope(action.remote)
            state["fetch_scope"] = [asdict(scope) for scope in scopes]
            details["tags"] = (
                "Native non-force tag auto-follow may create tags; existing tag values are preserved."
                if any(not scope.no_tags for scope in scopes)
                else "No tag auto-follow for the selected remotes."
            )
            remote = self.repository._remote(action.remote) if action.remote else "all remotes"
            state["config"] = self._config_state()
            details["remote"] = remote
        elif operation == "push":
            branch = self.repository._branch()
            if not branch or not self.repository._head():
                raise GitError("Cannot push a detached or unborn HEAD; check out a branch first.")
            upstream = self.repository._upstream(branch)
            if action.remote:
                remote = self.repository._remote(action.remote)
                destination = f"refs/heads/{branch}"
            elif upstream:
                remote, destination = self.repository._upstream_push_target(branch)
                if not remote:
                    raise GitError("The current branch has an invalid upstream configuration.")
            else:
                remote = self.repository._remote("", sole_when_empty=True)
                destination = f"refs/heads/{branch}"
            push_urls = self.repository._remote_push_urls(remote)
            if len(push_urls) != 1:
                if push_urls:
                    raise GitError(
                        f"Remote {remote} has multiple push destinations; configure exactly one before pushing."
                    )
                raise GitError(f"Remote {remote} has no push destination.")
            state["config"] = self._config_state()
            state["push_urls"] = [_hash_bytes(os.fsencode(url)) for url in push_urls]
            details.update(branch=branch, remote=remote, destination=destination)
        elif operation == "checkout":
            state["refs"] = self._refs_state()
        elif operation in _REF_CHANGE_OPERATIONS or operation in _COMPLEX_GATED_OPERATIONS:
            state["refs"] = self._refs_state()
            state["config"] = self._config_state()

        if (
            asdict(self._read_identity()) != state["identity"]
            or self.repository._head() != state["head"]
            or self.repository._branch() != state["branch"]
        ):
            raise GitError("Repository identity or HEAD changed while the operation was reviewed.")

        encoded = json.dumps(
            state,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _hash_bytes(encoded), details

    def _assert_state(self, action: Action, expected: str) -> None:
        current, _ = self._capture_state(action)
        if current != expected:
            raise GitError(
                "Repository content or configuration changed. Review and choose the operation again."
            )

    def _worktree_state(self, action: Action) -> tuple[str, tuple[str, ...]]:
        status = self.repository._status()
        selected: list[str]
        if action.paths:
            requested = self.repository._related_status_paths(self.repository._paths(action.paths))
            selected = list(requested)
            for requested_path in requested:
                prefix = requested_path.rstrip("/") + "/"
                selected.extend(
                    path
                    for change in status
                    for path in (change.path, change.original_path)
                    if path and path.startswith(prefix)
                )
        else:
            selected = [
                path for change in status for path in (change.path, change.original_path) if path
            ]
        digest = hashlib.sha256()
        status_by_path = {
            path: (change.index, change.worktree, change.path, change.original_path)
            for change in status
            for path in (change.path, change.original_path)
            if path
        }
        names = tuple(sorted(set(selected)))
        for name in names:
            self._include_digest(digest, os.fsencode(name))
            self._include_digest(
                digest,
                repr(status_by_path.get(name, ("", "", "", ""))).encode("utf-8", "surrogateescape"),
            )
            self._include_path(digest, name)
        return digest.hexdigest(), names

    def _target_changed_paths(self, current: str, target: str) -> tuple[str, ...]:
        if not target or current == target:
            return ()
        base = current or self.repository._empty_tree()
        result = self.repository._git(
            [
                "diff",
                "--name-only",
                "-z",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                base,
                target,
                "--",
            ],
            operation="review checkout target paths",
        )
        return tuple(sorted(_decode(path) for path in result.stdout.split(b"\x00") if path))

    def _attributes_state(
        self,
        paths: tuple[str, ...],
        *,
        reject_stage_conversions: bool,
        source: str = "",
    ) -> str:
        digest = hashlib.sha256()
        for path in paths:
            self._include_digest(digest, os.fsencode(path))
        if not paths:
            return digest.hexdigest()

        attributes = self.repository._attribute_snapshot(paths, source=source)
        self._include_digest(digest, attributes)
        if reject_stage_conversions:
            fields = attributes.split(b"\x00")
            if fields and fields[-1] == b"":
                fields.pop()
            if len(fields) % 3:
                raise GitError("Git returned malformed effective path attributes.")
            conversion_attributes = {
                b"filter",
                b"ident",
                b"text",
                b"eol",
                b"working-tree-encoding",
            }
            for _path, attribute, value in zip(
                fields[0::3], fields[1::3], fields[2::3], strict=True
            ):
                if attribute in conversion_attributes and value not in {b"unset", b"unspecified"}:
                    raise GitError(
                        "Staging paths with effective content-conversion attributes is "
                        "unsupported; use external Git for this transformation."
                    )

            autocrlf = self.repository._git(
                ["config", "--get", "core.autocrlf"],
                ok_codes=(0, 1),
                operation="review staging conversion configuration",
            )
            value = autocrlf.stdout.strip().lower()
            if autocrlf.returncode == 0 and value not in {
                b"",
                b"0",
                b"false",
                b"no",
                b"off",
            }:
                raise GitError(
                    "Staging with effective core.autocrlf conversion is unsupported; "
                    "use external Git for this transformation."
                )
        return digest.hexdigest()

    def _include_path(self, digest: Any, name: str) -> None:
        path = self.repository.root / self.repository._path(name)
        root = self.repository.root.resolve()
        try:
            parent = path.parent.resolve()
        except OSError as error:
            raise GitError(f"Cannot safely review worktree path {name!r}: {error}") from error
        if not parent.is_relative_to(root):
            raise GitError("Cannot safely review a path outside this worktree.")
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            self._include_digest(digest, b"absent")
            return
        except OSError as error:
            raise GitError(f"Cannot inspect worktree path {name!r}: {error}") from error
        mode = stat.S_IFMT(metadata.st_mode) | stat.S_IMODE(metadata.st_mode)
        self._include_digest(digest, str(mode).encode("ascii"))
        if stat.S_ISLNK(metadata.st_mode):
            try:
                target = os.readlink(path)
                after = path.lstat()
            except OSError as error:
                raise GitError(f"Cannot review worktree path {name!r}: {error}") from error
            if self._file_identity(after) != self._file_identity(metadata):
                raise GitError("A worktree path changed while its operation was reviewed.")
            self._include_digest(digest, os.fsencode(target))
        elif stat.S_ISREG(metadata.st_mode):
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            else:  # Supported targets provide O_NOFOLLOW; fail closed elsewhere.
                raise GitError("This platform cannot safely review worktree files.")
            try:
                descriptor = os.open(path, flags)
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode) or self._file_identity(
                        opened
                    ) != self._file_identity(metadata):
                        raise GitError("A worktree path changed while its operation was reviewed.")
                    file_digest = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        file_digest.update(block)
                    after = os.fstat(stream.fileno())
                if self._file_identity(after) != self._file_identity(opened):
                    raise GitError("A worktree path changed while its operation was reviewed.")
                self._include_digest(digest, file_digest.digest())
            except GitError:
                raise
            except OSError as error:
                raise GitError(f"Cannot review worktree path {name!r}: {error}") from error
        elif stat.S_ISDIR(metadata.st_mode):
            self._include_digest(digest, b"directory")
        else:
            raise GitError("Directory and special-file changes require an external Git review.")

    @staticmethod
    def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            stat.S_IMODE(info.st_mode),
        )

    @staticmethod
    def _include_digest(digest: Any, value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    def _ref_oid(self, ref: str) -> str:
        result = self.repository._git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", ref],
            ok_codes=(0, 1),
            operation="review destination ref",
        )
        return _decode(result.stdout).strip() if result.returncode == 0 else ""

    def _refs_state(self) -> str:
        result = self.repository._git(
            [
                "for-each-ref",
                "--format=%(refname)%00%(objectname)%00%(*objectname)%00%(symref)",
                "refs/heads",
                "refs/remotes",
                "refs/tags",
            ],
            operation="review refs",
        )
        return _hash_bytes(result.stdout)

    def _config_state(self) -> str:
        result = self.repository._git(
            [
                "config",
                "--includes",
                "--null",
                "--list",
                "--show-origin",
                "--show-scope",
            ],
            operation="review effective repository configuration",
        )
        # Config values and origins may contain credentials or private paths.
        # Retain only their digest in the plan state and never surface raw output.
        return _hash_bytes(result.stdout)

    @staticmethod
    def _destination_state(destination: Path) -> dict[str, Any]:
        state: dict[str, Any] = {"path": os.fspath(destination)}
        try:
            info = destination.lstat()
        except FileNotFoundError:
            state["kind"] = "absent"
            ancestor = destination.parent
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            try:
                ancestor_info = ancestor.stat()
                state["ancestor"] = [
                    os.fspath(ancestor.resolve()),
                    ancestor_info.st_dev,
                    ancestor_info.st_ino,
                ]
            except OSError as error:
                raise GitError(f"Could not inspect worktree destination: {error}") from error
            return state
        except OSError as error:
            raise GitError(f"Could not inspect worktree destination: {error}") from error
        state["identity"] = [info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns]
        if stat.S_ISDIR(info.st_mode):
            try:
                state["kind"] = "directory"
                state["entries"] = sorted(
                    os.fsencode(item.name).hex() for item in destination.iterdir()
                )
            except OSError as error:
                raise GitError(f"Could not inspect worktree destination: {error}") from error
        elif stat.S_ISLNK(info.st_mode):
            state["kind"] = "symlink"
            state["target"] = os.fsencode(os.readlink(destination)).hex()
        else:
            state["kind"] = "other"
        return state

    def _read_identity(self) -> _RepositoryIdentity:
        try:
            root = self.repository.root
            root_info = root.stat()
            git_result = self.repository._git(
                ["rev-parse", "--path-format=absolute", "--git-dir"],
                operation="identify Git worktree",
            )
            common_result = self.repository._git(
                ["rev-parse", "--path-format=absolute", "--git-common-dir"],
                operation="identify common Git repository",
            )
            git_path = Path(_decode(git_result.stdout).removesuffix("\n")).resolve()
            common_path = Path(_decode(common_result.stdout).removesuffix("\n")).resolve()
            git_info = git_path.stat()
            common_info = common_path.stat()
        except (OSError, ValueError) as error:
            raise GitError(f"Cannot establish exact repository identity: {error}") from error
        if not stat.S_ISDIR(root_info.st_mode):
            raise GitError("The repository worktree root is no longer a directory.")
        return _RepositoryIdentity(
            root_path=os.fspath(root),
            root_device=root_info.st_dev,
            root_inode=root_info.st_ino,
            git_path=os.fspath(git_path),
            git_device=git_info.st_dev,
            git_inode=git_info.st_ino,
            common_path=os.fspath(common_path),
            common_device=common_info.st_dev,
            common_inode=common_info.st_ino,
        )

    @contextmanager
    def _writer_guard(self):
        try:
            with self._pull_writer_guard.hold():
                yield
        except PullWriterGuardError as error:
            raise GitError(f"Cannot acquire common-repository writer guard: {error}") from error

    def _assert_pull_lease_clear(self, action: Action) -> None:
        try:
            lease = self._pull_lease.read()
        except PullLeaseError as error:
            raise GitError(f"Cannot verify durable pull writer lease: {error}") from error
        if (
            lease is not None
            and lease.phase == "paused"
            and action.operation in {"stage", "unstage"}
            and self._pull.managed() is not None
        ):
            # A verified paused pull has no live native writer. Permit only
            # resolution staging in its owning worktree, not other mutations
            # or a lease whose exact recovery ownership cannot be established.
            return
        if lease is not None:
            raise GitError(
                f"Repository mutations are blocked by a durable {lease.phase} pull writer lease "
                f"at {self._pull_lease.path}. Inspect PID/PGID {lease.pid} and repository state; "
                "Rhizome will not kill, retry, or clear it automatically."
            )

    def _assert_identity(self) -> None:
        if self._read_identity() != self._identity:
            raise GitError(
                "Repository or worktree identity changed. Reopen the repository before mutating it."
            )

    def _prepare_checkpoint(self, action: Action):
        assert self.journal is not None
        try:
            if action.operation == "discard":
                return self.journal.prepare(
                    self.repository,
                    operation=action.operation,
                    paths=action.paths,
                    history=False,
                )
            raise GitError("This operation has no verified checkpoint scope.")
        except RecoveryError as error:
            raise GitError(f"Required recovery checkpoint failed: {error}") from error

    def _complete_checkpoint(self, checkpoint_id: str, outcome: str) -> None:
        assert self.journal is not None
        try:
            self.journal.complete(checkpoint_id, outcome=outcome)
        except RecoveryError as error:
            raise GitError(f"Could not record recovery checkpoint outcome: {error}") from error

    @staticmethod
    def _remote_outcome_uncertain(action: Action) -> bool:
        # No retry is attempted. A failed push may have reached the server even
        # when transport acknowledgement did not reach this process.
        return action.operation in {"push", "delete-remote-branch"}

    def _consequences(self, action: Action, details: dict[str, str]) -> tuple[str, ...]:
        operation = action.operation
        count = len(action.paths)
        if operation == "stage":
            if "selection_start" in details:
                return (
                    "Stage selected patch lines "
                    f"{details['selection_start']}–{details['selection_end']} in {action.paths[0]}.",
                )
            scope = f"{count} selected path(s)" if count else "all current changes"
            return (f"Update the Git index from {scope}.",)
        if operation == "unstage":
            if "selection_start" in details:
                return (
                    "Unstage selected patch lines "
                    f"{details['selection_start']}–{details['selection_end']} in {action.paths[0]}.",
                )
            scope = f"{count} selected path(s)" if count else "all staged changes"
            return (f"Remove {scope} from the Git index without discarding worktree content.",)
        if operation == "commit":
            branch = self.repository._branch() or "detached HEAD"
            return (f"Create one commit from the reviewed index on {branch}.",)
        if operation == "checkout":
            oid = details.get("target_oid", "")[:12]
            return (f"Check out {action.target} ({oid}) in this worktree.",)
        if operation == "create-branch":
            return (
                f"Create and check out branch {details.get('name', action.name)} at {details.get('target_oid', '')[:12]}.",
            )
        if operation == "create-worktree":
            branch = details.get("name")
            mode = f"new branch {branch}" if branch else "detached HEAD"
            return (
                f"Create worktree {details.get('destination', action.paths[0] if action.paths else '')} at {details.get('target_oid', '')[:12]} using {mode}.",
            )
        if operation == "tag":
            return (
                f"Create tag {details.get('name', action.name)} at {details.get('target_oid', '')[:12]}.",
            )
        if operation == "set-upstream":
            return (
                f"Set {details.get('branch', action.target)} to track {details.get('remote', action.remote)}/{details.get('source', action.name).removeprefix('refs/heads/')}.",
            )
        if operation == "fetch":
            return (
                f"Fetch configured sources into FETCH_HEAD and remote-tracking caches from {details.get('remote', 'all remotes')}.",
                details["tags"],
                "Configured pruning may remove obsolete cached refs, not user-owned branches or tags.",
            )
        if operation == "push":
            return (
                f"Non-force push {details.get('branch', '')} to {details.get('remote', '')}:{details.get('destination', '')}.",
                "A transport failure can leave the remote outcome uncertain; Rhizome will not retry automatically.",
            )
        if operation == "discard":
            return (
                f"Replace worktree content for {count} tracked path(s) from the index.",
                "Save index and worktree preimages in the recovery journal first.",
            )
        return (f"{operation or 'Unknown operation'} remains gated in M1.",)

    def _start_pull_activity(self, operation: str, message: str) -> tuple[str, float]:
        activity_id = secrets.token_hex(12)
        self._add_activity(
            OperationActivity(
                id=activity_id,
                operation=operation,
                state="running",
                message=_safe_activity_message(message),
                started=datetime.now(UTC),
                elapsed=0.0,
            )
        )
        return activity_id, time.monotonic()

    def _update_pull_activity(
        self,
        activity_id: str,
        began: float,
        *,
        state: str,
        message: str,
        checkpoint_id: str | None = None,
    ) -> None:
        self._update_activity(
            activity_id,
            state=state,
            message=message,
            elapsed=time.monotonic() - began,
            checkpoint_id=checkpoint_id,
        )

    def _add_activity(self, record: OperationActivity) -> None:
        with self._activity_lock:
            self._activities.append(record)

    def _update_activity(
        self,
        activity_id: str,
        *,
        state: str,
        message: str,
        elapsed: float,
        checkpoint_id: str | None = None,
    ) -> None:
        with self._activity_lock:
            for index, current in enumerate(self._activities):
                if current.id != activity_id:
                    continue
                self._activities[index] = OperationActivity(
                    id=current.id,
                    operation=current.operation,
                    state=state,
                    message=_safe_activity_message(message),
                    started=current.started,
                    elapsed=max(0.0, elapsed),
                    checkpoint_id=(
                        current.checkpoint_id if checkpoint_id is None else checkpoint_id
                    ),
                )
                break
