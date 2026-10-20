"""Values shared by the Git engine and terminal interface. No UI or subprocess dependencies."""

from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class HistoryLine:
    graph: str  # Git's own graph, with ANSI color; render with rich.Text.from_ansi.
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

    @property
    def conflicts(self) -> tuple[FileChange, ...]:
        return tuple(file for file in self.files if file.conflicted)


@dataclass(frozen=True)
class Action:
    """Allowlisted mutation request. Values remain argv, never shell source.

    operation: checkout, create-branch, rename-branch, delete-branch, merge,
    rebase, cherry-pick, revert, reset-soft, tag, delete-tag, stash, stash-apply,
    stash-pop, stash-drop, stage, unstage, discard, commit, fetch, pull, push,
    continue, abort. Fields interpreted by the engine, not raw Git arguments.
    """

    operation: str
    target: str = ""
    name: str = ""
    message: str = ""
    paths: tuple[str, ...] = field(default_factory=tuple)
    remote: str = ""
    expected_oid: str = ""  # Guard mutable stash selectors across a confirmation dialog.
