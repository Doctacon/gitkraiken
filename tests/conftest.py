from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

GitRun = Callable[..., subprocess.CompletedProcess[str]]


@pytest.fixture(autouse=True)
def isolate_system_clipboard(monkeypatch):
    """Headless tests must never overwrite the developer's real clipboard."""
    which = shutil.which

    def without_clipboard(command, *args, **kwargs):
        if command in {"pbcopy", "clip.exe", "wl-copy", "xclip", "xsel"}:
            return None
        return which(command, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", without_clipboard)


@pytest.fixture
def local_timezone(monkeypatch):
    """Set the process-local timezone and restore it even after a failed assertion."""
    with monkeypatch.context() as local_env:

        def use_timezone(name: str) -> None:
            local_env.setenv("TZ", name)
            time.tzset()

        yield use_timezone
    time.tzset()


@pytest.fixture
def git_run() -> GitRun:
    """Run Git with deterministic test identity and no interactive prompting."""

    def run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test User",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=cwd,
            check=check,
            text=True,
            capture_output=True,
        )

    return run


@pytest.fixture
def git_repo(tmp_path: Path, git_run: GitRun) -> Path:
    """A disposable, empty, non-bare repository on branch main."""
    root = tmp_path / "worktree"
    root.mkdir()
    git_run(root, "init", "-b", "main")
    git_run(root, "config", "user.name", "Test User")
    git_run(root, "config", "user.email", "test@example.invalid")
    git_run(root, "config", "commit.gpgsign", "false")
    return root


@pytest.fixture
def overlapping_remotes(git_repo: Path, git_run: GitRun) -> None:
    """Seed legacy remote names without depending on `git remote add` validation.

    Git 2.55 rejects adding team/origin alongside team. Repositories created
    with older Git can still contain both, and must retain exact ref ownership.
    """
    for name in ("team", "team/origin"):
        url = git_repo.parent / f"{name.replace('/', '-')}.git"
        git_run(git_repo, "config", f"remote.{name}.url", str(url))
        git_run(
            git_repo,
            "config",
            "--add",
            f"remote.{name}.fetch",
            f"+refs/heads/*:refs/remotes/{name}/*",
        )


@pytest.fixture
def bare_remote(tmp_path: Path, git_run: GitRun) -> Path:
    """A disposable local bare repository suitable as a network-free remote."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git_run(remote, "init", "--bare", "-b", "main")
    return remote
