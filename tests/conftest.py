from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

GitRun = Callable[..., subprocess.CompletedProcess[str]]


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
def bare_remote(tmp_path: Path, git_run: GitRun) -> Path:
    """A disposable local bare repository suitable as a network-free remote."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git_run(remote, "init", "--bare", "-b", "main")
    return remote
