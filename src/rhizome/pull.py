"""Guarded fetch/review/integrate planning for the current branch.

This module deliberately does not implement ``git pull``.  A pull plan captures
one exact configured upstream, fetches it through Rhizome's validated refspec
scope, and grants single-use authority only for the captured object ID.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .git import GitError, GitOutcomeUncertain, Repository, _redact
from .models import Action
from .pull_lease import PullLeaseError
from .recovery import PullSession, RecoveryError, RecoveryJournal

_MAX_PENDING_PULL_PLANS = 256
_MAX_REBASE_COMMITS = 1_000


@dataclass(frozen=True)
class PullPlan:
    """An immutable, coordinator-owned review of one exact pull step."""

    action: Action
    repository: str
    fingerprint: str
    available: bool
    reason: str
    requires_confirmation: bool
    consequences: tuple[str, ...]
    kind: str
    branch: str
    original_head: str
    target_oid: str
    upstream: str
    remote: str
    source_ref: str
    incoming: int
    outgoing: int


@dataclass(frozen=True)
class ManagedPull:
    """The durable public identity of a paused pull-owned integration."""

    kind: str
    branch: str
    original_head: str
    target_oid: str
    checkpoint_id: str


@dataclass(frozen=True)
class _PullState:
    fingerprint: str
    branch_ref: str
    tracking_ref: str
    config: str
    index: str
    status: str
    local_refs: str
    remote_refs: str
    other_refs: str
    shallow: str
    operation: str
    remote_tips_before: tuple[str, ...]
    remote_tips_after: tuple[str, ...]


@dataclass(frozen=True)
class _FinishState:
    fingerprint: str
    checkpoint_id: str
    changed_paths: tuple[str, ...] = ()
    conflicted: bool = False
    other_refs: str = ""
    include_outgoing: bool = False


@dataclass(frozen=True)
class _PendingPull:
    plan: PullPlan
    state: _PullState | _FinishState


def _decode(value: bytes) -> str:
    return value.decode("utf-8", "surrogateescape")


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class PullBackend:
    """Stateful pull planner owned by one :class:`OperationCoordinator`."""

    def __init__(
        self,
        coordinator: Any,
        *,
        journal: RecoveryJournal | None,
    ) -> None:
        self.coordinator = coordinator
        self.repository: Repository = coordinator.repository
        self.journal = journal
        self.lease = coordinator._pull_lease
        self._pending: OrderedDict[str, _PendingPull] = OrderedDict()

    def prepare(self, action: Action) -> PullPlan:
        if not isinstance(action, Action):
            raise TypeError("action must be an Action")
        if action.operation not in {"pull", "continue", "abort"}:
            raise GitError("Pull planning accepts only pull, continue, or abort actions.")

        activity_id, began = self.coordinator._start_pull_activity(
            action.operation, "Reviewing guarded pull state."
        )
        try:
            writer_guard = (
                self.coordinator._writer_guard() if action.operation == "pull" else nullcontext()
            )
            with self.coordinator._mutation_lock, writer_guard:
                self.coordinator._assert_identity()
                if action.operation == "pull":
                    self._assert_no_lease()
                    mode = self._pull_mode(action)
                    self.repository._preflight_context(action)
                    before = self._capture_before_fetch()
                    self.coordinator._update_pull_activity(
                        activity_id,
                        began,
                        state="running",
                        message=f"Fetching exact configured upstream from {before['remote']}.",
                    )
                    self._fetch(before["remote"])
                    self.coordinator._assert_identity()
                    state: _PullState | _FinishState = self._capture_after_fetch(before)
                    plan = self._make_plan(action, mode, before, state)
                else:
                    self._finish_action(action)
                    self.repository._preflight_context(action)
                    session = self._managed_session()
                    if session is None:
                        raise GitError(
                            "There is no verified paused pull-owned operation to finish."
                        )
                    plan, state = self._make_finish_plan(action, session)
                self._remember(plan, state)
            self.coordinator._update_pull_activity(
                activity_id, began, state="succeeded", message=self._prepared_message(plan)
            )
            return plan
        except Exception as error:
            self.coordinator._update_pull_activity(
                activity_id, began, state="failed", message=str(error)
            )
            raise

    def submit(self, plan: PullPlan, *, confirmed: bool = False) -> str:
        if not isinstance(plan, PullPlan):
            raise GitError("Only a pull plan issued by this coordinator can be submitted.")
        if not isinstance(confirmed, bool):
            raise TypeError("confirmed must be a boolean")
        if not plan.available:
            raise GitError(plan.reason or "This pull operation is unavailable.")
        if plan.requires_confirmation and not confirmed:
            raise GitError("This pull integration requires explicit confirmation.")

        pending = self._pending.pop(plan.fingerprint, None)
        if pending is None:
            raise GitError("This pull plan is unknown, expired, cancelled, or already submitted.")
        if pending.plan != plan:
            raise GitError("This pull plan was changed after review.")

        activity_id, began = self.coordinator._start_pull_activity(
            plan.action.operation, "Waiting to revalidate the reviewed pull plan."
        )
        mutation_started = False
        checkpoint_id = ""
        try:
            with self.coordinator._mutation_lock, self.coordinator._writer_guard():
                self.coordinator._update_pull_activity(
                    activity_id,
                    began,
                    state="running",
                    message="Revalidating exact repository state before native Git.",
                )
                self.coordinator._assert_identity()
                if isinstance(pending.state, _FinishState):
                    self._assert_finish_state(pending.state)
                    mutation_started = True
                    checkpoint_id = pending.state.checkpoint_id
                    result = self._finish(plan, pending.state)
                else:
                    self._assert_state(plan, pending.state)
                    if plan.kind == "noop":
                        result = "Fetch completed; the current branch needs no integration."
                    elif plan.kind == "fast-forward":
                        self._reject_ignored_obstructions(plan.original_head, plan.target_oid)
                        self._assert_state(plan, pending.state)
                        mutation_started = True
                        result = self._fast_forward(plan, pending.state)
                    else:
                        self._reject_ignored_obstructions(
                            plan.original_head,
                            plan.target_oid,
                            include_outgoing=plan.kind == "rebase",
                        )
                        self._assert_state(plan, pending.state)
                        mutation_started = True
                        result, checkpoint_id = self._integrate(plan, pending.state)
            self.coordinator._update_pull_activity(
                activity_id,
                began,
                state="succeeded",
                message=result,
                checkpoint_id=checkpoint_id or None,
            )
            return result
        except Exception as error:
            uncertain = mutation_started and isinstance(error, GitOutcomeUncertain)
            self.coordinator._update_pull_activity(
                activity_id,
                began,
                state="uncertain" if uncertain else "failed",
                message=str(error),
                checkpoint_id=checkpoint_id or None,
            )
            raise

    def cancel(self, plan: PullPlan) -> None:
        if not isinstance(plan, PullPlan):
            raise GitError("Only a pull plan issued by this coordinator can be cancelled.")
        pending = self._pending.pop(plan.fingerprint, None)
        if pending is None:
            raise GitError("This pull plan is unknown, expired, cancelled, or already submitted.")
        if pending.plan != plan:
            raise GitError("This pull plan was changed after review.")

    def managed(self) -> ManagedPull | None:
        with self.coordinator._mutation_lock:
            self.coordinator._assert_identity()
            session = self._managed_session()
            if session is None:
                return None
            return ManagedPull(
                kind=session.kind,
                branch=session.branch,
                original_head=session.original_head,
                target_oid=session.target_oid,
                checkpoint_id=session.checkpoint_id,
            )

    @staticmethod
    def _pull_mode(action: Action) -> str:
        mode = getattr(action, "pull_mode", "") or "ff-only"
        if mode not in {"ff", "ff-only", "rebase"}:
            raise GitError(f"Unsupported pull mode: {mode}")
        if any(
            (
                action.target,
                action.name,
                action.message,
                action.paths,
                action.remote,
                action.expected_push_url,
            )
        ):
            raise GitError("Pull always uses the current branch's exact configured upstream.")
        return mode

    @staticmethod
    def _finish_action(action: Action) -> None:
        if any(
            (
                action.target,
                action.name,
                action.message,
                action.paths,
                action.remote,
                action.expected_push_url,
                action.pull_mode,
            )
        ):
            raise GitError("Finishing pull cannot widen the owned operation scope.")

    def _capture_before_fetch(self) -> dict[str, Any]:
        repo = self.repository
        branch = repo._branch()
        head = repo._head()
        if not branch or not head:
            raise GitError("Pull requires a checked-out, born local branch.")
        branch_ref = f"refs/heads/{branch}"
        symbolic = repo._git(["symbolic-ref", "--quiet", "HEAD"], operation="review pull branch")
        if _decode(symbolic.stdout).strip() != branch_ref:
            raise GitError("HEAD is not attached to the reviewed local branch.")
        if repo._operation():
            raise GitError("Cannot pull while another Git operation is active.")
        if repo._status():
            raise GitError(
                "Pull requires a clean index and worktree, including no untracked files."
            )
        self._reject_hidden_index_entries()
        if repo._investigation_shallow():
            raise GitError("Pull is unavailable for shallow history because ancestry is ambiguous.")

        tracking, remote, source = repo._canonical_upstream(branch)
        if not tracking or not remote or not source:
            raise GitError("The current branch has no exact configured remote upstream.")
        if remote == ".":
            raise GitError("A local-dot upstream is not eligible for guarded pull.")
        remote = repo._remote(remote)
        if not tracking.startswith("refs/remotes/") or not source.startswith("refs/heads/"):
            raise GitError("The current branch upstream is not a remote branch.")
        mapped = repo._remote_source_ref(remote, tracking)
        if mapped != source:
            raise GitError("The current branch has an inconsistent or ambiguous upstream mapping.")
        scopes = repo._validate_fetch_scope(remote)
        if len(scopes) != 1 or scopes[0].remote != remote:
            raise GitError("The exact upstream remote has no unique safe fetch scope.")
        if not any(
            item.destination
            and repo._refspec_match(item.source, source) is not None
            and repo._refspec_match(item.destination, tracking) is not None
            for item in scopes[0].refspecs
        ):
            raise GitError("The configured fetch scope does not uniquely update this upstream.")

        return {
            "branch": branch,
            "branch_ref": branch_ref,
            "head": head,
            "tracking": tracking,
            "remote": remote,
            "source": source,
            "base": self._invariants(branch_ref, tracking),
            "remote_tips": self._remote_tips(),
        }

    def _fetch(self, remote: str) -> None:
        """Use Repository's pinned safe fetch path with recursion explicitly disabled."""
        try:
            self.repository._execute_fetch(remote, disable_submodule_recursion=True)
        except TypeError as error:
            # This keyword is supplied by the separately owned native fetch seam.
            # Never silently fall back to a hand-built or recursion-capable fetch.
            raise GitError(
                "Guarded pull transport is unavailable because this Git backend cannot "
                "explicitly disable submodule recursion."
            ) from error

    def _capture_after_fetch(self, before: dict[str, Any]) -> _PullState:
        if (
            self.repository._head() != before["head"]
            or self.repository._branch() != before["branch"]
        ):
            raise GitError("The checked-out branch or HEAD changed during fetch.")
        current = self._invariants(before["branch_ref"], before["tracking"])
        base: _PullState = before["base"]
        for field in (
            "branch_ref",
            "config",
            "index",
            "status",
            "local_refs",
            "shallow",
            "operation",
        ):
            if getattr(current, field) != getattr(base, field):
                raise GitError(
                    "Local branch, content, configuration, or operation state changed during fetch."
                )
        tracking, remote, source = self.repository._canonical_upstream(before["branch"])
        if (tracking, remote, source) != (
            before["tracking"],
            before["remote"],
            before["source"],
        ):
            raise GitError("The current branch upstream changed during fetch.")
        target = self.repository._resolve_commit(tracking, label="Fetched upstream")
        return _PullState(
            fingerprint=current.fingerprint,
            branch_ref=current.branch_ref,
            tracking_ref=target,
            config=current.config,
            index=current.index,
            status=current.status,
            local_refs=current.local_refs,
            remote_refs=current.remote_refs,
            other_refs=current.other_refs,
            shallow=current.shallow,
            operation=current.operation,
            remote_tips_before=before["remote_tips"],
            remote_tips_after=self._remote_tips(),
        )

    def _invariants(self, branch_ref: str, tracking_ref: str) -> _PullState:
        repo = self.repository
        head = repo._head()
        branch = repo._branch()
        branch_oid = self._ref_oid(branch_ref)
        tracking_oid = self._ref_oid(tracking_ref)
        index = (
            repo._git(["ls-files", "--stage", "-z"], operation="review pull index").stdout
            + repo._git(["ls-files", "-v", "-z"], operation="review pull index flags").stdout
        )
        status = repo._git(
            [
                "--no-optional-locks",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignored=no",
            ],
            operation="review pull worktree",
        ).stdout
        local_refs = repo._git(
            ["for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads"],
            operation="review pull local refs",
        ).stdout
        remote_refs = repo._git(
            [
                "for-each-ref",
                "--format=%(refname)%00%(objectname)%00%(symref)",
                "refs/remotes",
            ],
            operation="review pull remote refs",
        ).stdout
        other_refs = self._other_refs_state(branch_ref)
        values = {
            "head": head,
            "branch": branch,
            "branch_ref": branch_oid,
            "tracking": tracking_oid,
            "config": self.coordinator._config_state(),
            "index": _hash(index),
            "status": _hash(status),
            "local_refs": _hash(local_refs),
            "remote_refs": _hash(remote_refs),
            "other_refs": other_refs,
            "shallow": repo._investigation_shallow(),
            "operation": repo._operation(),
        }
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        return _PullState(
            fingerprint=_hash(encoded),
            branch_ref=branch_oid,
            tracking_ref=tracking_oid,
            config=values["config"],
            index=values["index"],
            status=values["status"],
            local_refs=values["local_refs"],
            remote_refs=values["remote_refs"],
            other_refs=values["other_refs"],
            shallow=values["shallow"],
            operation=values["operation"],
            remote_tips_before=(),
            remote_tips_after=(),
        )

    def _make_plan(
        self,
        action: Action,
        mode: str,
        before: dict[str, Any],
        state: _PullState,
    ) -> PullPlan:
        head = before["head"]
        target = state.tracking_ref
        if not target:
            raise GitError("Fetch did not produce the exact configured upstream target.")
        outgoing, incoming = self._ahead_behind(head, target)
        if head == target or incoming == 0:
            kind = "noop"
            available = True
            reason = ""
            confirmation = False
            consequences = (
                "Fetch updated only validated remote-tracking caches.",
                "The current branch is already equal to or ahead of its upstream.",
            )
        elif outgoing == 0 and self._is_ancestor(head, target):
            kind = "fast-forward"
            available = True
            reason = ""
            confirmation = False
            consequences = (
                f"Fast-forward {before['branch_ref']} from {head[:12]} to {target[:12]}.",
                "Integrate only the exact fetched upstream commit shown here.",
            )
        elif mode == "ff-only":
            kind = "noop"
            available = False
            reason = (
                "Fast-forward-only pull refused divergent history; fetch caches may have "
                "changed, but local history and files were not integrated."
            )
            confirmation = False
            consequences = (reason,)
        else:
            kind = "merge" if mode == "ff" else "rebase"
            confirmation = True
            available = self.journal is not None
            reason = (
                "Divergent pull requires a durable pull recovery journal."
                if self.journal is None
                else ""
            )
            if kind == "rebase":
                self._validate_rebase(before["branch"], head, target, state)
                consequences = (
                    f"Rebase {outgoing} unpublished linear commit(s) onto {target[:12]}.",
                    "Save a durable history checkpoint before rewriting this branch.",
                )
            else:
                consequences = (
                    f"Merge exact target {target[:12]} with {outgoing} local and {incoming} incoming commit(s).",
                    "Save a durable history checkpoint before creating the merge.",
                )
        nonce = secrets.token_bytes(32)
        fingerprint = _hash(
            nonce
            + state.fingerprint.encode("ascii")
            + json.dumps(asdict(action), sort_keys=True, separators=(",", ":")).encode()
            + target.encode("ascii")
        )
        return PullPlan(
            action=action,
            repository=os.fspath(self.repository.root),
            fingerprint=fingerprint,
            available=available,
            reason=reason,
            requires_confirmation=confirmation,
            consequences=consequences,
            kind=kind,
            branch=before["branch"],
            original_head=head,
            target_oid=target,
            upstream=before["tracking"],
            remote=before["remote"],
            source_ref=before["source"],
            incoming=incoming,
            outgoing=outgoing,
        )

    def _validate_rebase(self, branch: str, head: str, target: str, state: _PullState) -> None:
        tips = tuple(sorted(set(state.remote_tips_before) | set(state.remote_tips_after)))
        self._validate_rebase_eligibility(branch, head, target, tips)

    def _validate_rebase_eligibility(
        self, branch: str, head: str, target: str, tips: tuple[str, ...]
    ) -> None:
        protected = self.coordinator._protected_refs()
        if branch in protected or f"refs/heads/{branch}" in protected:
            raise GitError(f"refs/heads/{branch} is protected from pull rebase.")
        bases = self.repository._git(
            ["--no-replace-objects", "merge-base", "--all", head, target],
            ok_codes=(0, 1),
            operation="review pull rebase base",
        )
        merge_bases = tuple(line for line in _decode(bases.stdout).splitlines() if line)
        if len(merge_bases) != 1:
            raise GitError("Pull rebase requires exactly one merge base.")
        commits = self.repository._git(
            [
                "--no-replace-objects",
                "rev-list",
                "--parents",
                f"{merge_bases[0]}..{head}",
            ],
            operation="review pull rebase commits",
        )
        lines = tuple(line.split() for line in _decode(commits.stdout).splitlines() if line)
        if len(lines) > _MAX_REBASE_COMMITS:
            raise GitError("Pull rebase outgoing history exceeds the bounded review limit.")
        if any(len(fields) != 2 for fields in lines):
            raise GitError("Pull rebase is limited to linear outgoing history without merges.")
        outgoing = {fields[0] for fields in lines}
        if tips:
            unpublished_result = self.repository._git(
                [
                    "--no-replace-objects",
                    "rev-list",
                    head,
                    f"^{merge_bases[0]}",
                    *(f"^{tip}" for tip in tips),
                ],
                operation="review published pull rebase commits",
            )
            unpublished = {line for line in _decode(unpublished_result.stdout).splitlines() if line}
            if unpublished != outgoing:
                raise GitError(
                    "Pull rebase refuses commits already reachable from a known remote tip."
                )

    def _revalidate_rebase(self, plan: PullPlan, expected: _PullState) -> None:
        self._assert_state(plan, expected)
        tips = self._remote_tips()
        current = replace(expected, remote_tips_before=tips, remote_tips_after=tips)
        self._validate_rebase(plan.branch, plan.original_head, plan.target_oid, current)
        self._assert_state(plan, expected)

    def _revalidate_session_rebase(self, session: PullSession) -> None:
        self._validate_rebase_eligibility(
            session.branch,
            session.original_head,
            session.target_oid,
            self._remote_tips(),
        )

    def _assert_state(self, plan: PullPlan, expected: _PullState) -> None:
        current = self._invariants(f"refs/heads/{plan.branch}", plan.upstream)
        tracking, remote, source = self.repository._canonical_upstream(plan.branch)
        if (
            current.fingerprint != expected.fingerprint
            or current.tracking_ref != plan.target_oid
            or (tracking, remote, source) != (plan.upstream, plan.remote, plan.source_ref)
        ):
            raise GitError(
                "Repository content, configuration, branch, or fetched target changed. "
                "Review pull again."
            )

    def _assert_no_lease(self) -> None:
        try:
            lease = self.lease.read()
        except PullLeaseError as error:
            raise GitError(f"Cannot verify durable pull writer lease: {error}") from error
        if lease is not None:
            raise GitError(
                f"A durable {lease.phase} pull writer lease at {self.lease.path} blocks new pull work. "
                f"Inspect PID/PGID {lease.pid}; Rhizome will not retry, kill, or clear it automatically."
            )

    def _managed_session(self) -> PullSession | None:
        try:
            durable_lease = self.lease.read()
        except PullLeaseError as error:
            raise GitError(f"Cannot verify durable pull writer lease: {error}") from error
        if durable_lease is not None and durable_lease.phase != "paused":
            raise GitError(
                f"Pull ownership is {durable_lease.phase} with durable PID/PGID "
                f"{durable_lease.pid}; inspect {self.lease.path}. Rhizome will not retry or clear it."
            )
        if self.journal is None:
            if durable_lease is not None:
                raise GitError("Paused pull lease has no recovery journal ownership metadata.")
            return None
        try:
            sessions = self.journal.pull_sessions(os.fspath(self.repository.root))
        except RecoveryError as error:
            raise GitError(f"Cannot verify pull ownership metadata: {error}") from error
        if not sessions:
            if durable_lease is not None:
                raise GitError(
                    "Paused pull lease has no active matching recovery ownership metadata."
                )
            return None
        if len(sessions) != 1:
            raise GitError("Multiple active pull ownership records require manual recovery review.")
        session = sessions[0]
        if session.phase != "paused":
            try:
                lease = self.lease.require(session.lease_token)
            except PullLeaseError as error:
                raise GitError(f"Unsettled pull ownership lease is invalid: {error}") from error
            raise GitError(
                f"Pull ownership is {session.phase} with durable PID/PGID {lease.pid}; "
                f"inspect {self.lease.path} and repository state. Rhizome will not retry or clear it."
            )
        try:
            lease = self.lease.require(session.lease_token, phase="paused")
        except PullLeaseError as error:
            raise GitError(f"Paused pull ownership lease is invalid: {error}") from error
        if lease.operation != session.kind:
            raise GitError("Paused pull lease does not match recovery ownership.")
        if self.repository._operation() != session.kind:
            raise GitError("Pull ownership metadata does not match the active Git operation.")
        if self._ref_oid(f"refs/heads/{session.branch}") != session.original_head:
            raise GitError("The pull-owned branch moved while integration was paused.")
        self.repository._resolve_commit(session.target_oid, label="Owned pull target")
        if not self._operation_identity(session):
            raise GitError("The active Git operation is not the exact pull-owned integration.")
        if self._operation_manifest(session.kind) != session.operation_manifest:
            raise GitError("Pull operation metadata changed outside the owned native Git process.")
        return session

    def _operation_manifest(self, kind: str) -> str:
        paths: list[tuple[str, Any]] = []
        if kind == "merge":
            for name in ("MERGE_HEAD", "MERGE_MODE", "MERGE_MSG", "MERGE_AUTOSTASH"):
                path = self.repository._git_path(name)
                if path.exists() or path.is_symlink():
                    paths.append((name, path))
        else:
            for directory_name in ("rebase-merge", "rebase-apply"):
                directory = self.repository._git_path(directory_name)
                if directory.is_dir() and not directory.is_symlink():
                    for path in sorted(directory.rglob("*")):
                        if path.is_file() or path.is_symlink():
                            paths.append(
                                (f"{directory_name}/{path.relative_to(directory).as_posix()}", path)
                            )
                    break
        if not paths or len(paths) > 512:
            raise GitError("Pull operation metadata is missing or exceeds its bounded manifest.")
        digest = hashlib.sha256()
        total = 0
        for name, path in paths:
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise GitError("Pull operation metadata contains an unsafe filesystem node.")
                if info.st_size > 1024 * 1024 or total + info.st_size > 8 * 1024 * 1024:
                    raise GitError("Pull operation metadata exceeds its bounded manifest.")
                data = path.read_bytes()
                after = path.lstat()
            except GitError:
                raise
            except OSError as error:
                raise GitError(f"Cannot read pull operation metadata: {error}") from error
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or len(data) != info.st_size:
                raise GitError("Pull operation metadata changed while it was reviewed.")
            total += len(data)
            encoded_name = name.encode("utf-8", "surrogateescape")
            digest.update(len(encoded_name).to_bytes(4, "big"))
            digest.update(encoded_name)
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()

    def _operation_identity(self, session: PullSession) -> bool:
        if session.kind == "merge":
            marker = self.repository._git_path("MERGE_HEAD")
            try:
                if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
                    return False
                values = marker.read_text("ascii").splitlines()
            except (OSError, UnicodeError):
                return False
            return values == [session.target_oid]
        for directory_name in ("rebase-merge", "rebase-apply"):
            directory = self.repository._git_path(directory_name)
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                onto = (directory / "onto").read_text("ascii").strip()
                original = (directory / "orig-head").read_text("ascii").strip()
            except (OSError, UnicodeError):
                return False
            return onto == session.target_oid and original == session.original_head
        return False

    def _make_finish_plan(
        self, action: Action, session: PullSession
    ) -> tuple[PullPlan, _FinishState]:
        if action.operation == "continue" and session.kind == "rebase":
            self._revalidate_session_rebase(session)
        include_outgoing = action.operation == "continue" and session.kind == "rebase"
        fingerprint, changed_paths, conflicted, other_refs = self._finish_fingerprint(
            session, include_outgoing=include_outgoing
        )
        if action.operation == "continue":
            available = not conflicted
            reason = "" if available else "Resolve and stage every pull conflict before continuing."
            confirmation = False
            consequences = (
                f"Continue only the owned {session.kind} onto {session.target_oid[:12]}.",
                "A further conflict remains paused under the same recovery checkpoint.",
            )
        else:
            available = True
            reason = ""
            confirmation = True
            saved = (
                "Save private worktree copies; conflicted index stages remain reconstructible from protected histories."
                if conflicted
                else "Save distinct private index and worktree copies of resolved paths."
            )
            consequences = (
                f"Checkpoint {len(changed_paths)} resolution path(s), then abort the owned {session.kind}.",
                saved,
                f"Restore {session.branch} to {session.original_head[:12]} without deleting untracked or ignored files.",
            )
        nonce = secrets.token_bytes(32)
        plan_fingerprint = _hash(
            nonce
            + fingerprint.encode("ascii")
            + json.dumps(asdict(action), sort_keys=True, separators=(",", ":")).encode()
        )
        plan = PullPlan(
            action=action,
            repository=os.fspath(self.repository.root),
            fingerprint=plan_fingerprint,
            available=available,
            reason=reason,
            requires_confirmation=confirmation,
            consequences=consequences,
            kind=action.operation,
            branch=session.branch,
            original_head=session.original_head,
            target_oid=session.target_oid,
            upstream="",
            remote="",
            source_ref="",
            incoming=0,
            outgoing=session.outgoing,
        )
        return plan, _FinishState(
            fingerprint,
            session.checkpoint_id,
            changed_paths,
            conflicted,
            other_refs,
            include_outgoing,
        )

    def _finish_fingerprint(
        self, session: PullSession, *, include_outgoing: bool = False
    ) -> tuple[str, tuple[str, ...], bool, str]:
        self._reject_hidden_index_entries(allow_unmerged=True)
        status = self.repository._status()
        changed: set[str] = set()
        conflicted = False
        untracked: set[str] = set()
        for item in status:
            conflicted = conflicted or item.conflicted
            paths = {path for path in (item.path, item.original_path) if path}
            if item.index == "?" or item.worktree == "?":
                untracked.update(paths)
            else:
                changed.update(paths)
        if untracked:
            raise GitError(
                "Move untracked files aside before finishing this pull; "
                f"first path: {sorted(untracked)[0]!r}. Rhizome will not remove them."
            )
        ignored = self.repository._git(
            [
                "--no-optional-locks",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignored=matching",
            ],
            operation="review pull finish obstructions",
        ).stdout
        ignored_paths = {
            _decode(record[3:]).rstrip("/")
            for record in ignored.split(b"\x00")
            if record.startswith(b"!! ")
        }
        affected = self._owned_tree_paths(session, include_outgoing=include_outgoing)
        obstruction = next(
            (
                path
                for path in sorted(untracked | ignored_paths)
                if any(
                    path == tracked
                    or path.startswith(tracked + "/")
                    or tracked.startswith(path + "/")
                    for tracked in affected
                )
            ),
            "",
        )
        if obstruction:
            raise GitError(
                f"Untracked or ignored path {obstruction!r} may be lost while finishing pull."
            )
        other_refs = self._other_refs_state(f"refs/heads/{session.branch}")
        values = {
            "checkpoint": session.checkpoint_id,
            "kind": session.kind,
            "branch": session.branch,
            "original": session.original_head,
            "target": session.target_oid,
            "outgoing": session.outgoing,
            "phase": session.phase,
            "head": self.repository._head(),
            "branch_now": self.repository._branch(),
            "branch_ref": self._ref_oid(f"refs/heads/{session.branch}"),
            "operation": self.repository._operation(),
            "config": self.coordinator._config_state(),
            "index": _hash(
                self.repository._git(
                    ["ls-files", "--stage", "-z"], operation="review pull finish index"
                ).stdout
            ),
            "status": _hash(ignored),
            "content": self._content_fingerprint(tuple(sorted(changed))),
            "local_refs": _hash(
                self.repository._git(
                    ["for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads"],
                    operation="review pull finish refs",
                ).stdout
            ),
            "other_refs": other_refs,
        }
        return (
            _hash(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()),
            tuple(sorted(changed)),
            conflicted,
            other_refs,
        )

    def _content_fingerprint(self, paths: tuple[str, ...]) -> str:
        digest = hashlib.sha256()
        total = 0
        for name in paths:
            relative = Path(self.repository._path(name))
            current = self.repository.root
            for component in relative.parts[:-1]:
                current = current / component
                try:
                    ancestor = current.lstat()
                except OSError as error:
                    raise GitError(
                        f"Cannot inspect pull resolution ancestor for {name!r}: {error}"
                    ) from error
                if stat.S_ISLNK(ancestor.st_mode) or not stat.S_ISDIR(ancestor.st_mode):
                    raise GitError("Pull resolution path has an unsafe ancestor.")
            path = self.repository.root / relative
            encoded = os.fsencode(name)
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
            try:
                info = path.lstat()
            except FileNotFoundError:
                digest.update(b"absent")
                continue
            except OSError as error:
                raise GitError(
                    f"Cannot fingerprint pull resolution path {name!r}: {error}"
                ) from error
            digest.update(str(stat.S_IFMT(info.st_mode) | stat.S_IMODE(info.st_mode)).encode())
            if stat.S_ISLNK(info.st_mode):
                data = os.fsencode(os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                if info.st_size > 64 * 1024 * 1024 or total + info.st_size > 64 * 1024 * 1024:
                    raise GitError("Pull resolution content exceeds the bounded review limit.")
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags)
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    data = stream.read()
                    after = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise GitError("Pull resolution content changed while reviewed.")
                total += len(data)
            elif stat.S_ISDIR(info.st_mode):
                data = b"directory"
            else:
                raise GitError("Unsupported pull resolution filesystem node.")
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()

    def _owned_tree_paths(
        self, session: PullSession, *, include_outgoing: bool = False
    ) -> set[str]:
        paths: set[str] = set()
        for oid in (session.original_head, session.target_oid):
            result = self.repository._git(
                ["--no-replace-objects", "ls-tree", "-r", "--name-only", "-z", oid],
                operation="review pull finish path scope",
            )
            paths.update(_decode(path) for path in result.stdout.split(b"\x00") if path)
        if include_outgoing and session.kind == "rebase":
            paths.update(self._outgoing_transition_paths(session.original_head, session.target_oid))
        return paths

    def _assert_finish_state(self, expected: _FinishState) -> PullSession:
        session = self._managed_session()
        if session is None or session.checkpoint_id != expected.checkpoint_id:
            raise GitError("The verified paused pull operation changed. Review it again.")
        fingerprint, changed_paths, conflicted, other_refs = self._finish_fingerprint(
            session, include_outgoing=expected.include_outgoing
        )
        if (
            fingerprint != expected.fingerprint
            or changed_paths != expected.changed_paths
            or conflicted != expected.conflicted
            or other_refs != expected.other_refs
        ):
            raise GitError("Paused pull content or configuration changed. Review it again.")
        return session

    def _integrate(self, plan: PullPlan, expected: _PullState) -> tuple[str, str]:
        if self.journal is None:
            raise GitError("Divergent pull requires a durable pull recovery journal.")
        runner = getattr(self.repository, "_run_integration", None)
        if runner is None:
            raise GitError("Native guarded integration support is unavailable.")
        try:
            checkpoint = self.journal.prepare(
                self.repository, operation=f"pull-{plan.kind}", history=True
            )
        except RecoveryError as error:
            raise GitError(f"Required pull recovery checkpoint failed: {error}") from error
        try:
            lease = self.lease.acquire(plan.kind)
        except PullLeaseError as error:
            self._complete_pull_checkpoint(checkpoint.id, "cancelled")
            raise GitError(f"Could not publish durable pull writer lease: {error}") from error
        try:
            self.journal.start_pull_session(
                checkpoint.id,
                repository=os.fspath(self.repository.root),
                kind=plan.kind,
                branch=plan.branch,
                original_head=plan.original_head,
                target_oid=plan.target_oid,
                outgoing=plan.outgoing,
                lease_token=lease.token,
                other_refs=expected.other_refs,
            )
        except RecoveryError as error:
            self._clear_lease(lease.token)
            self._complete_pull_checkpoint(checkpoint.id, "cancelled")
            raise GitError(f"Could not publish pull ownership metadata: {error}") from error
        try:
            self.coordinator._assert_identity()
            self._assert_state(plan, expected)
            if plan.kind == "rebase":
                self._revalidate_rebase(plan, expected)
        except Exception:
            self._complete_pull_checkpoint(checkpoint.id, "cancelled")
            self._clear_lease(lease.token)
            raise

        if plan.kind == "merge":
            args = [
                "--no-replace-objects",
                "-c",
                "merge.autoStash=false",
                "-c",
                "submodule.recurse=false",
                "-c",
                f"branch.{plan.branch}.mergeOptions=",
                "merge",
                "--no-ff",
                "--commit",
                "--no-squash",
                "--no-edit",
                "--no-autostash",
                "--no-overwrite-ignore",
                plan.target_oid,
            ]
        else:
            base = self._single_merge_base(plan.original_head, plan.target_oid)
            args = [
                "--no-replace-objects",
                "-c",
                "rebase.autoStash=false",
                "-c",
                "rebase.updateRefs=false",
                "-c",
                "rebase.missingCommitsCheck=error",
                "-c",
                "submodule.recurse=false",
                "rebase",
                "--no-autostash",
                "--no-autosquash",
                "--no-rebase-merges",
                "--reapply-cherry-picks",
                "--empty=keep",
                "--keep-empty",
                "--onto",
                plan.target_oid,
                base,
            ]
        spawned = False

        def own(pid: int) -> None:
            nonlocal spawned
            try:
                self.lease.update(lease.token, phase="running", pid=pid)
                self.journal.update_pull_session(checkpoint.id, phase="running", pid=pid)
            except (PullLeaseError, RecoveryError) as error:
                raise GitOutcomeUncertain(
                    f"Could not record native pull process ownership: {error}"
                ) from error
            spawned = True

        self.coordinator._assert_identity()
        self._assert_state(plan, expected)
        if plan.kind == "rebase":
            self._revalidate_rebase(plan, expected)
        try:
            result = runner(args, operation=f"pull {plan.kind}", on_spawn=own)
        except GitOutcomeUncertain:
            self._mark_uncertain(checkpoint.id, lease.token)
            raise
        except Exception as error:
            if spawned or self.repository._head() != plan.original_head:
                self._mark_uncertain(checkpoint.id, lease.token)
                raise GitOutcomeUncertain(
                    "Pull integration may have changed repository state."
                ) from error
            self._complete_pull_checkpoint(checkpoint.id, "failed")
            self._clear_lease(lease.token)
            raise
        return self._classify_owned_result(
            plan, expected, checkpoint.id, lease.token, result
        ), checkpoint.id

    def _classify_owned_result(
        self,
        plan: PullPlan,
        expected: _PullState,
        checkpoint_id: str,
        lease_token: str,
        result: subprocess.CompletedProcess[bytes],
    ) -> str:
        operation = self.repository._operation()
        if result.returncode != 0:
            if operation == plan.kind and self._session_matches(checkpoint_id):
                try:
                    assert self.journal is not None
                    manifest = self._operation_manifest(plan.kind)
                    self.journal.update_pull_session(
                        checkpoint_id, phase="paused", operation_manifest=manifest
                    )
                    self.lease.update(lease_token, phase="paused")
                except (RecoveryError, PullLeaseError, GitError) as error:
                    self._mark_uncertain(checkpoint_id, lease_token)
                    raise GitOutcomeUncertain(
                        f"Pull conflicted but paused ownership could not be recorded: {error}"
                    ) from error
                return (
                    f"Pull {plan.kind} paused on conflicts; resolve, stage, then Continue or Abort."
                )
            if not operation and self.repository._head() == plan.original_head:
                try:
                    self._assert_state(plan, expected)
                except Exception as error:
                    self._mark_uncertain(checkpoint_id, lease_token)
                    raise GitOutcomeUncertain(
                        "Pull failed after changing reviewed repository state."
                    ) from error
                self._complete_pull_checkpoint(checkpoint_id, "failed")
                self._clear_lease(lease_token)
                raise GitError(self._integration_error(result, plan.kind))
            self._mark_uncertain(checkpoint_id, lease_token)
            raise GitOutcomeUncertain(
                f"Git pull {plan.kind} failed after repository state changed; inspect before retrying."
            )
        if operation:
            self._mark_uncertain(checkpoint_id, lease_token)
            raise GitOutcomeUncertain(
                "Git reported success while integration state remains active."
            )
        try:
            self._verify_success(plan, expected)
        except GitOutcomeUncertain:
            self._mark_uncertain(checkpoint_id, lease_token)
            raise
        except Exception as error:
            self._mark_uncertain(checkpoint_id, lease_token)
            raise GitOutcomeUncertain(
                f"Could not verify pull integration completion: {error}"
            ) from error
        self._complete_pull_checkpoint(checkpoint_id, "succeeded")
        self._clear_lease(lease_token)
        return f"Integrated exact upstream {plan.target_oid[:12]} by {plan.kind}."

    def _session_matches(self, checkpoint_id: str) -> bool:
        if self.journal is None:
            return False
        try:
            sessions = self.journal.pull_sessions(os.fspath(self.repository.root))
        except RecoveryError:
            return False
        if len(sessions) != 1 or sessions[0].checkpoint_id != checkpoint_id:
            return False
        try:
            lease = self.lease.require(sessions[0].lease_token, phase="running")
        except PullLeaseError:
            return False
        return (
            lease.operation == sessions[0].kind
            and self.repository._operation() == sessions[0].kind
            and self._operation_identity(sessions[0])
        )

    def _finish(self, plan: PullPlan, state: _FinishState) -> str:
        assert self.journal is not None
        session = self._assert_finish_state(state)
        resolution_id = ""
        continuation_id = ""
        if plan.kind == "continue":
            if session.kind == "rebase":
                self._revalidate_session_rebase(session)
            try:
                continuation = self.journal.prepare(
                    self.repository,
                    operation="pull-continue-resolution",
                    paths=state.changed_paths,
                    history=True,
                )
                continuation_id = continuation.id
                session = self.journal.update_pull_session(
                    state.checkpoint_id,
                    phase="paused",
                    resolution_checkpoint=continuation_id,
                )
            except RecoveryError as error:
                raise GitError(
                    f"Could not preserve resolved pull content before Continue: {error}"
                ) from error
            self.coordinator._assert_identity()
            self._assert_finish_state(state)
        if plan.kind == "abort":
            try:
                resolution = self.journal.prepare(
                    self.repository,
                    operation="pull-abort-resolution",
                    paths=state.changed_paths,
                    history=False,
                    worktree_only=state.conflicted,
                )
                resolution_id = resolution.id
            except RecoveryError as error:
                raise GitError(
                    f"Could not preserve pull resolution edits before abort: {error}"
                ) from error
            try:
                self.coordinator._assert_identity()
                self._assert_finish_state(state)
            except Exception:
                self._complete_pull_checkpoint(resolution_id, "cancelled")
                raise
        runner = getattr(self.repository, "_run_integration", None)
        if runner is None:
            raise GitError("Native guarded integration support is unavailable.")
        if session.kind == "merge":
            args = [
                "--no-replace-objects",
                "-c",
                "merge.autoStash=false",
                "-c",
                "submodule.recurse=false",
                "merge",
                f"--{plan.kind}",
            ]
        else:
            args = [
                "--no-replace-objects",
                "-c",
                "rebase.autoStash=false",
                "-c",
                "rebase.updateRefs=false",
                "-c",
                "submodule.recurse=false",
                "rebase",
                f"--{plan.kind}",
            ]

        def own(pid: int) -> None:
            try:
                self.lease.update(session.lease_token, phase="running", pid=pid)
                self.journal.update_pull_session(state.checkpoint_id, phase="running", pid=pid)
            except (PullLeaseError, RecoveryError) as error:
                raise GitOutcomeUncertain(
                    f"Could not record pull finish process ownership: {error}"
                ) from error

        try:
            self.coordinator._assert_identity()
            self._assert_finish_state(state)
            if plan.kind == "continue" and session.kind == "rebase":
                self._revalidate_session_rebase(session)
        except Exception:
            if resolution_id:
                self._complete_pull_checkpoint(resolution_id, "cancelled")
            raise
        try:
            result = runner(args, operation=f"pull {session.kind} {plan.kind}", on_spawn=own)
        except GitOutcomeUncertain:
            self._mark_uncertain(state.checkpoint_id, session.lease_token)
            raise
        if result.returncode != 0:
            marker_matches = (
                self.repository._operation() == session.kind and self._operation_identity(session)
            )
            try:
                if not marker_matches:
                    raise GitOutcomeUncertain("Owned operation marker changed during pull finish.")
                if plan.kind == "abort":
                    fingerprint, paths, conflicted, other_refs = self._finish_fingerprint(
                        session, include_outgoing=state.include_outgoing
                    )
                    manifest = self._operation_manifest(session.kind)
                    if (
                        fingerprint != state.fingerprint
                        or paths != state.changed_paths
                        or conflicted != state.conflicted
                        or other_refs != state.other_refs
                        or manifest != session.operation_manifest
                    ):
                        raise GitOutcomeUncertain(
                            "Failed pull abort changed operation, index, or worktree state."
                        )
                else:
                    manifest = self._operation_manifest(session.kind)
                self.journal.update_pull_session(
                    state.checkpoint_id, phase="paused", operation_manifest=manifest
                )
                self.lease.update(session.lease_token, phase="paused")
            except (RecoveryError, PullLeaseError, GitError) as error:
                self._mark_uncertain(state.checkpoint_id, session.lease_token)
                raise GitOutcomeUncertain(
                    f"Could not prove pull finish remained safely paused: {error}"
                ) from error
            return f"Pull {session.kind} remains paused; Git did not complete {plan.kind}."

        if self.repository._operation():
            self._mark_uncertain(state.checkpoint_id, session.lease_token)
            raise GitOutcomeUncertain(
                "Git reported pull finish success while operation state remains."
            )
        if plan.kind == "abort":
            try:
                restored = (
                    self.repository._head() == session.original_head
                    and self.repository._branch() == session.branch
                    and self._ref_oid(f"refs/heads/{session.branch}") == session.original_head
                    and not self.repository._status()
                    and self._other_refs_state(f"refs/heads/{session.branch}") == state.other_refs
                )
                self._reject_hidden_index_entries()
            except Exception as error:
                self._mark_uncertain(state.checkpoint_id, session.lease_token)
                raise GitOutcomeUncertain(
                    f"Could not verify pull abort completion: {error}"
                ) from error
            if not restored:
                self._mark_uncertain(state.checkpoint_id, session.lease_token)
                raise GitOutcomeUncertain(
                    "Pull abort did not restore the exact reviewed branch state."
                )
            self._complete_pull_checkpoint(state.checkpoint_id, "cancelled")
            for checkpoint_id in session.resolution_checkpoints:
                self._complete_pull_checkpoint(checkpoint_id, "succeeded")
            if resolution_id:
                self._complete_pull_checkpoint(resolution_id, "succeeded")
            self._clear_lease(session.lease_token)
            return f"Aborted owned pull {session.kind}; resolution worktree copies remain in checkpoint {resolution_id}."
        try:
            self._verify_session_success(session, other_refs=state.other_refs)
        except GitOutcomeUncertain:
            self._mark_uncertain(state.checkpoint_id, session.lease_token)
            raise
        except Exception as error:
            self._mark_uncertain(state.checkpoint_id, session.lease_token)
            raise GitOutcomeUncertain(
                f"Could not verify pull continuation completion: {error}"
            ) from error
        self._complete_pull_checkpoint(state.checkpoint_id, "succeeded")
        for checkpoint_id in session.resolution_checkpoints:
            self._complete_pull_checkpoint(checkpoint_id, "succeeded")
        self._clear_lease(session.lease_token)
        return f"Continued and completed owned pull {session.kind}."

    def _verify_success(self, plan: PullPlan, expected: _PullState) -> None:
        session = PullSession(
            "",
            os.fspath(self.repository.root),
            plan.kind,
            plan.branch,
            plan.original_head,
            plan.target_oid,
            plan.outgoing,
            "paused",
            0,
            0,
            "0" * 64,
            "",
            expected.other_refs,
            (),
        )
        self._verify_session_success(session)

    def _verify_session_success(
        self, session: PullSession, *, other_refs: str | None = None
    ) -> None:
        self.coordinator._assert_identity()
        head = self.repository._head()
        if (
            not head
            or self.repository._branch() != session.branch
            or self._ref_oid(f"refs/heads/{session.branch}") != head
            or self.repository._status()
            or not self._is_ancestor(session.target_oid, head)
        ):
            raise GitOutcomeUncertain(
                "Pull integration completion postconditions were not satisfied."
            )
        self._reject_hidden_index_entries()
        if self._other_refs_state(f"refs/heads/{session.branch}") != (
            session.other_refs if other_refs is None else other_refs
        ):
            raise GitOutcomeUncertain("Pull changed an unrelated local ref or tag.")
        if session.kind == "merge":
            parents = self.repository._git(
                ["--no-replace-objects", "rev-list", "--parents", "-n", "1", head],
                operation="verify exact pull merge result",
            )
            fields = _decode(parents.stdout).split()
            if fields != [head, session.original_head, session.target_oid]:
                raise GitOutcomeUncertain(
                    "Pull merge did not create the exact reviewed two-parent final commit."
                )
        else:
            commits = self.repository._git(
                ["--no-replace-objects", "rev-list", "--parents", f"{session.target_oid}..{head}"],
                operation="verify pull rebase result",
            )
            lines = [line.split() for line in _decode(commits.stdout).splitlines() if line]
            if len(lines) != session.outgoing or any(len(line) != 2 for line in lines):
                raise GitOutcomeUncertain(
                    "Pull rebase did not preserve the reviewed linear commit count."
                )

    def _single_merge_base(self, original: str, target: str) -> str:
        result = self.repository._git(
            ["--no-replace-objects", "merge-base", "--all", original, target],
            operation="revalidate pull rebase base",
        )
        bases = [line for line in _decode(result.stdout).splitlines() if line]
        if len(bases) != 1:
            raise GitError("Pull rebase requires exactly one stable merge base.")
        return bases[0]

    def _complete_pull_checkpoint(self, checkpoint_id: str, outcome: str) -> None:
        assert self.journal is not None
        try:
            self.journal.complete(checkpoint_id, outcome=outcome)
        except RecoveryError as error:
            try:
                self.journal.complete(checkpoint_id, outcome="uncertain")
            except RecoveryError:
                pass
            raise GitOutcomeUncertain(
                f"Could not durably record pull checkpoint outcome: {error}"
            ) from error

    def _mark_uncertain(self, checkpoint_id: str, lease_token: str = "") -> None:
        if lease_token:
            self._uncertain_lease(lease_token)
        if self.journal is None:
            return
        try:
            self.journal.update_pull_session(checkpoint_id, phase="uncertain")
        except RecoveryError:
            pass
        try:
            self.journal.complete(checkpoint_id, outcome="uncertain")
        except RecoveryError:
            pass

    def _uncertain_lease(self, token: str) -> None:
        try:
            self.lease.update(token, phase="uncertain")
        except PullLeaseError:
            pass

    def _clear_lease(self, token: str) -> None:
        try:
            self.lease.clear(token)
        except PullLeaseError as error:
            raise GitOutcomeUncertain(
                f"Native Git is quiescent but its durable pull writer lease could not be cleared: {error}"
            ) from error

    def _fast_forward(self, plan: PullPlan, expected: _PullState) -> str:
        runner = getattr(self.repository, "_run_integration", None)
        if runner is None:
            raise GitError("Native guarded integration support is unavailable.")
        args = [
            "--no-replace-objects",
            "-c",
            "merge.autoStash=false",
            "-c",
            "submodule.recurse=false",
            "-c",
            f"branch.{plan.branch}.mergeOptions=",
            "merge",
            "--ff-only",
            "--commit",
            "--no-squash",
            "--no-edit",
            "--no-autostash",
            "--no-overwrite-ignore",
            plan.target_oid,
        ]
        before = self.repository._head()
        try:
            lease = self.lease.acquire("fast-forward")
        except PullLeaseError as error:
            raise GitError(f"Could not publish durable pull writer lease: {error}") from error
        try:
            self.coordinator._assert_identity()
            self._assert_state(plan, expected)
        except Exception:
            self._clear_lease(lease.token)
            raise

        def own(pid: int) -> None:
            try:
                self.lease.update(lease.token, phase="running", pid=pid)
            except PullLeaseError as error:
                raise GitOutcomeUncertain(
                    f"Could not record fast-forward native process ownership: {error}"
                ) from error

        try:
            result = runner(args, operation="pull fast-forward", on_spawn=own)
        except GitOutcomeUncertain:
            self._uncertain_lease(lease.token)
            raise
        except Exception as error:
            try:
                unchanged = self.repository._head() == before
                self._assert_state(plan, expected)
            except Exception:
                unchanged = False
            if not unchanged:
                self._uncertain_lease(lease.token)
                raise GitOutcomeUncertain(
                    "Fast-forward integration may have changed repository state before an unclassified failure."
                ) from error
            self._clear_lease(lease.token)
            raise
        try:
            after = self.repository._head()
            if result.returncode != 0:
                if after != before:
                    raise GitOutcomeUncertain(
                        "Git reported fast-forward failure after HEAD changed; inspect before retrying."
                    )
                try:
                    self._assert_state(plan, expected)
                except Exception as error:
                    raise GitOutcomeUncertain(
                        "Fast-forward failed after changing reviewed repository state."
                    ) from error
                self._clear_lease(lease.token)
                raise GitError(self._integration_error(result, "fast-forward"))
            if (
                after != plan.target_oid
                or self.repository._branch() != plan.branch
                or self._ref_oid(f"refs/heads/{plan.branch}") != plan.target_oid
                or self.repository._status()
            ):
                raise GitOutcomeUncertain(
                    "Git reported fast-forward success without the reviewed exact branch and clean content."
                )
            self._reject_hidden_index_entries()
            if self._other_refs_state(f"refs/heads/{plan.branch}") != expected.other_refs:
                raise GitOutcomeUncertain("Fast-forward changed an unrelated local ref or tag.")
        except GitOutcomeUncertain:
            self._uncertain_lease(lease.token)
            raise
        except GitError as error:
            if result.returncode != 0 and "after" in locals() and after == before:
                raise
            self._uncertain_lease(lease.token)
            raise GitOutcomeUncertain(
                "Fast-forward completed but exact repository postconditions could not be verified."
            ) from error
        except Exception as error:
            self._uncertain_lease(lease.token)
            raise GitOutcomeUncertain(
                f"Fast-forward completed but postconditions are uncertain: {error}"
            ) from error
        self._clear_lease(lease.token)
        return f"Fast-forwarded {plan.branch} to {plan.target_oid[:12]}."

    def _reject_hidden_index_entries(self, *, allow_unmerged: bool = False) -> None:
        flags = self.repository._git(
            ["ls-files", "-v", "-z"], operation="review hidden pull index flags"
        ).stdout
        visible = (b"H ", b"M ") if allow_unmerged else (b"H ",)
        for record in flags.split(b"\x00"):
            if record and not record.startswith(visible):
                raise GitError(
                    "Pull refuses assume-unchanged, skip-worktree, or otherwise hidden index entries."
                )

    def _reject_ignored_obstructions(
        self, original: str, target: str, *, include_outgoing: bool = False
    ) -> None:
        changed = self.repository._git(
            [
                "--no-replace-objects",
                "diff",
                "--name-only",
                "-z",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                original,
                target,
                "--",
            ],
            operation="review pull changed paths",
        ).stdout.split(b"\x00")
        changed_paths = {_decode(path) for path in changed if path}
        if include_outgoing:
            changed_paths.update(self._outgoing_transition_paths(original, target))
        if not changed_paths:
            return
        ignored = self.repository._git(
            [
                "--no-optional-locks",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignored=matching",
            ],
            operation="review ignored pull obstructions",
        ).stdout.split(b"\x00")
        ignored_paths = tuple(
            _decode(record[3:]).rstrip("/")
            for record in ignored
            if len(record) >= 4 and record[:3] == b"!! "
        )
        for ignored_path in ignored_paths:
            if any(
                changed_path == ignored_path
                or changed_path.startswith(ignored_path + "/")
                or ignored_path.startswith(changed_path + "/")
                for changed_path in changed_paths
            ):
                raise GitError(
                    f"Ignored path {ignored_path!r} may obstruct integration; no files were changed."
                )

    def _outgoing_transition_paths(self, original: str, target: str) -> set[str]:
        base = self._single_merge_base(original, target)
        result = self.repository._git(
            [
                "--no-replace-objects",
                "rev-list",
                "--reverse",
                "--topo-order",
                f"{base}..{original}",
            ],
            operation="review outgoing pull transitions",
        )
        commits = tuple(line for line in _decode(result.stdout).splitlines() if line)
        if len(commits) > _MAX_REBASE_COMMITS or any(
            not re.fullmatch(r"[0-9a-fA-F]{40,64}", oid) for oid in commits
        ):
            raise GitError("Pull rebase outgoing transitions exceed their bounded review.")
        if not commits:
            return set()
        transitions = self.repository._git(
            [
                "--no-replace-objects",
                "diff-tree",
                "--stdin",
                "--name-only",
                "--no-commit-id",
                "-r",
                "-z",
                "--no-renames",
            ],
            input_bytes=("\n".join(commits) + "\n").encode("ascii"),
            operation="review outgoing pull transition paths",
        )
        return {_decode(path) for path in transitions.stdout.split(b"\x00") if path}

    def _ahead_behind(self, head: str, target: str) -> tuple[int, int]:
        result = self.repository._git(
            ["--no-replace-objects", "rev-list", "--left-right", "--count", f"{head}...{target}"],
            operation="classify pull ancestry",
        )
        fields = _decode(result.stdout).split()
        if len(fields) != 2 or not all(field.isdigit() for field in fields):
            raise GitError("Git did not return a trustworthy pull ancestry count.")
        return int(fields[0]), int(fields[1])

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self.repository._git(
            ["--no-replace-objects", "merge-base", "--is-ancestor", ancestor, descendant],
            ok_codes=(0, 1),
            operation="classify pull ancestry",
        )
        return result.returncode == 0

    def _ref_oid(self, ref: str) -> str:
        result = self.repository._git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", ref],
            ok_codes=(0, 1),
            operation="review pull ref",
        )
        oid = _decode(result.stdout).strip() if result.returncode == 0 else ""
        if oid and not re.fullmatch(r"[0-9a-fA-F]{40,64}", oid):
            raise GitError("Git returned a malformed pull ref object ID.")
        return oid.lower()

    def _other_refs_state(self, branch_ref: str) -> str:
        result = self.repository._git(
            [
                "for-each-ref",
                "--format=%(refname)%00%(objectname)%00%(*objectname)",
                "refs/heads",
                "refs/tags",
            ],
            operation="review unrelated local refs and tags",
        )
        records = [
            line
            for line in result.stdout.splitlines()
            if not line.startswith(os.fsencode(branch_ref) + b"\x00")
        ]
        return _hash(b"\n".join(records))

    def _remote_tips(self) -> tuple[str, ...]:
        result = self.repository._git(
            ["for-each-ref", "--format=%(objectname)", "refs/remotes"],
            operation="review known remote tips",
        )
        tips = tuple(sorted({_decode(line).strip().lower() for line in result.stdout.splitlines()}))
        if any(not re.fullmatch(r"[0-9a-f]{40,64}", tip) for tip in tips):
            raise GitError("Git returned a malformed known remote tip.")
        return tips

    def _remember(self, plan: PullPlan, state: _PullState) -> None:
        self._pending[plan.fingerprint] = _PendingPull(plan, state)
        self._pending.move_to_end(plan.fingerprint)
        while len(self._pending) > _MAX_PENDING_PULL_PLANS:
            self._pending.popitem(last=False)

    @staticmethod
    def _prepared_message(plan: PullPlan) -> str:
        if not plan.available:
            return plan.reason
        if plan.kind == "noop":
            return "Fetch completed; no integration is needed."
        return f"Fetch completed; {plan.kind} review is ready."

    @staticmethod
    def _integration_error(result: subprocess.CompletedProcess[bytes], operation: str) -> str:
        raw = result.stderr or result.stdout
        detail = _redact(_decode(raw[: 64 * 1024])).strip()
        if len(raw) > 64 * 1024:
            detail += "\n[Git output truncated by Rhizome]"
        return f"Git pull {operation} failed: {detail or f'exit status {result.returncode}'}"
