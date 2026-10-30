"""Values shared by the Git engine and terminal interface. No UI or subprocess dependencies."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Ref:
    name: str
    full_name: str
    oid: str
    kind: Literal["local", "remote", "tag"]
    current: bool = False
    upstream: str = ""
    remote: str = ""  # Exact configured owner for a remote-tracking ref, if known.


@dataclass(frozen=True)
class Remote:
    name: str
    url: str  # Redacted for display; never expose embedded credentials.
    provider: str = "Git"
    web_url: str = ""
    push_url: str = ""  # Actual redacted push destination; may differ from fetch URL.


@dataclass(frozen=True)
class Stash:
    ref: str
    oid: str
    subject: str


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str = ""
    branch: str = ""
    current: bool = False
    bare: bool = False
    detached: bool = False
    locked: bool = False
    lock_reason: str = ""
    prunable: bool = False
    prune_reason: str = ""
    primary: bool = False  # Main checkout (or bare repository), independent of current/branch.


@dataclass(frozen=True)
class FileChange:
    path: str
    index: str = " "
    worktree: str = " "
    original_path: str = ""

    @property
    def staged(self) -> bool:
        return self.index not in (" ", "?", "!")

    @property
    def unstaged(self) -> bool:
        return self.worktree not in (" ", "!") or self.index == "?"

    @property
    def conflicted(self) -> bool:
        return self.index + self.worktree in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


@dataclass(frozen=True)
class Commit:
    oid: str
    parents: tuple[str, ...]
    author: str
    email: str
    date: str
    subject: str
    refs: tuple[str, ...] = ()

    @property
    def short_id(self) -> str:
        return self.oid[:8]


HistoryOrder = Literal["topology", "author-time"]


@dataclass(frozen=True)
class CapturedRef:
    full_name: str
    oid: str


@dataclass(frozen=True)
class InvestigationScope:
    """Exact local commit roots; moving names are evidence, never query targets."""

    repository_id: str
    scope_id: str
    kind: Literal["unified", "focused"]
    label: str
    root_oids: tuple[str, ...]
    captured_refs: tuple[CapturedRef, ...]
    bindings: tuple[CapturedRef, ...]
    head_oid: str
    branch: str
    shallow_fingerprint: str
    worktree_heads: tuple[CapturedRef, ...] = ()


@dataclass(frozen=True)
class ScopeState:
    current: bool
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class HistoryPage:
    scope_id: str
    order: HistoryOrder
    commits: tuple[Commit, ...]
    next_cursor: str | None
    has_more: bool
    omitted_before: int
    state: ScopeState


@dataclass(frozen=True)
class SearchQuery:
    text: str = ""
    path: str | None = None
    authored_from: datetime | None = None
    authored_through: datetime | None = None


@dataclass(frozen=True)
class SearchHit:
    commit: Commit
    matched_fields: tuple[str, ...]
    matching_refs: tuple[str, ...]
    history_cursor: str


@dataclass(frozen=True)
class SearchPage:
    scope_id: str
    query: SearchQuery
    order: HistoryOrder
    hits: tuple[SearchHit, ...]
    examined: int
    examined_total: int
    next_cursor: str | None
    complete: bool
    state: ScopeState


ComparisonIntent = Literal["branch-review", "endpoints"]


@dataclass(frozen=True)
class ComparisonResult:
    scope_id: str
    intent: ComparisonIntent
    left_oid: str
    right_oid: str
    merge_base_oid: str | None
    diff_from_oid: str
    diff_to_oid: str
    files: tuple[FileChange, ...]
    stat: str
    patch: str
    truncated: bool
    state: ScopeState


@dataclass(frozen=True)
class FileHistoryEntry:
    commit: Commit
    path: str
    previous_path: str
    status: str


@dataclass(frozen=True)
class FileHistoryPage:
    scope_id: str
    start_oid: str
    path: str
    follow_renames: bool
    entries: tuple[FileHistoryEntry, ...]
    next_cursor: str | None
    complete: bool
    limitations: tuple[str, ...]
    state: ScopeState


@dataclass(frozen=True)
class BlameLine:
    commit_oid: str
    original_path: str
    original_line: int
    final_line: int
    author: str
    email: str
    date: str
    text: str
    boundary: bool


@dataclass(frozen=True)
class BlamePage:
    scope_id: str
    start_oid: str
    path: str
    lines: tuple[BlameLine, ...]
    next_cursor: str | None
    complete: bool
    limitations: tuple[str, ...]
    state: ScopeState


@dataclass(frozen=True)
class HistoryLine:
    graph: str  # Empty for chronological history; optional legacy native ANSI graph.
    commit: Commit | None = None  # Connector-only rows are not actionable commits.


@dataclass(frozen=True)
class CommitDetail:
    commit: Commit
    message: str
    files: tuple[FileChange, ...]
    stat: str


@dataclass(frozen=True)
class Snapshot:
    root: Path
    branch: str
    head: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    refs: tuple[Ref, ...] = ()
    remotes: tuple[Remote, ...] = ()
    stashes: tuple[Stash, ...] = ()
    files: tuple[FileChange, ...] = ()
    history: tuple[HistoryLine, ...] = ()
    operation: str = ""  # merge, rebase, cherry-pick, revert, am, or empty
    # True when history may have more commits; user can explicitly load more.
    has_more: bool = False
    worktrees: tuple[Worktree, ...] = ()

    @property
    def conflicts(self) -> tuple[FileChange, ...]:
        return tuple(file for file in self.files if file.conflicted)


@dataclass(frozen=True)
class Action:
    """Allowlisted mutation request. Values remain argv, never shell source.

    operation: checkout, create-branch, create-worktree, rename-branch, delete-branch,
    merge, rebase, cherry-pick, revert, reset-soft, reset-mixed, reset-hard,
    edit-message, tag, delete-tag, stash, stash-apply, stash-pop, stash-drop, stage,
    unstage, discard, commit, apply-patch, fetch, pull, push, set-upstream,
    delete-remote-branch, continue, abort. Fields are interpreted by the engine,
    not raw Git arguments.
    """

    operation: str
    target: str = ""
    name: str = ""  # Also captures the resolved source ref for a remote deletion picker.
    message: str = ""
    paths: tuple[str, ...] = field(default_factory=tuple)
    remote: str = ""  # Network destination, or exact owner of a remote checkout target.
    expected_oid: str = ""  # Captured object identifier used by operation-specific guards.
    expected_branch: str = ""  # Branch captured with expected_head (empty means detached).
    expected_head: str = ""  # Require both this HEAD and expected_branch before mutation.
    expected_push_url: str = ""  # Redacted destination shown by the remote-deletion picker.
    expected_ref: str = ""  # Ref captured independently of target for an exact OID guard.
    pull_mode: str = ""  # ff, ff-only or rebase; empty retains legacy ff-only policy.
