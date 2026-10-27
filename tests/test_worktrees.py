from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from gitkraiken.git import GitError, Repository
from gitkraiken.models import Worktree


def _commit(git_run, root: Path) -> str:
    (root / "tracked.txt").write_text("tracked\n")
    git_run(root, "add", "tracked.txt")
    git_run(root, "commit", "-m", "initial")
    return git_run(root, "rev-parse", "HEAD").stdout.strip()


def test_worktrees_preserve_paths_states_and_reasons(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    head = _commit(git_run, git_repo)
    linked = tmp_path / "linked space\nline"
    detached = tmp_path / "detached"
    missing = tmp_path / "missing"
    git_run(git_repo, "worktree", "add", "-b", "feature/space", str(linked), head)
    git_run(git_repo, "worktree", "add", "--detach", str(detached), head)
    git_run(git_repo, "worktree", "lock", "--reason", "keep this\nworktree", str(detached))
    git_run(git_repo, "worktree", "add", "-b", "eventually-pruned", str(missing), head)
    shutil.rmtree(missing)

    worktrees = Repository(git_repo).worktrees()
    by_path = {worktree.path.resolve(): worktree for worktree in worktrees}

    assert by_path[git_repo.resolve()] == Worktree(
        path=by_path[git_repo.resolve()].path,
        head=head,
        branch="main",
        current=True,
        primary=True,
    )
    assert by_path[linked.resolve()].branch == "feature/space"
    assert by_path[linked.resolve()].path.name == "linked space\nline"
    assert by_path[detached.resolve()].detached
    assert by_path[detached.resolve()].branch == ""
    assert by_path[detached.resolve()].locked
    assert by_path[detached.resolve()].lock_reason == "keep this\nworktree"
    assert by_path[missing.resolve()].prunable
    assert by_path[missing.resolve()].prune_reason

    assert Repository(git_repo).snapshot().worktrees == worktrees

    for root in (git_repo, linked, detached):
        entries = Repository(root).worktrees()
        assert [tree.path.resolve() for tree in entries if tree.primary] == [git_repo.resolve()]
        assert [tree.path.resolve() for tree in entries if tree.current] == [root.resolve()]


def test_worktree_listing_reports_the_git_236_requirement(monkeypatch, git_repo: Path) -> None:
    repository = Repository(git_repo)

    def unsupported(args, **kwargs):
        assert args == ["worktree", "list", "--porcelain", "-z"]
        assert kwargs["ok_codes"] == (0, 129)
        return subprocess.CompletedProcess(["git", *args], 129, b"", b"unknown option: -z")

    monkeypatch.setattr(repository, "_git", unsupported)

    with pytest.raises(GitError, match=r"Git 2\.36 or newer") as caught:
        repository.worktrees()

    assert "unknown option" not in str(caught.value)


def test_unborn_worktree_preserves_its_symbolic_branch(git_repo: Path) -> None:
    worktree = Repository(git_repo).worktrees()[0]

    assert worktree.current
    assert worktree.primary
    assert worktree.head == ""
    assert worktree.branch == "main"


def test_bare_registration_is_reported_from_a_linked_worktree(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    head = _commit(git_run, git_repo)
    bare = tmp_path / "source.git"
    bare.mkdir()
    git_run(bare, "init", "--bare", "-b", "main")
    git_run(bare, "fetch", str(git_repo), f"{head}:refs/heads/main")
    linked = tmp_path / "from-bare"
    git_run(bare, "worktree", "add", str(linked), "main")

    worktrees = Repository(linked).worktrees()
    by_path = {worktree.path.resolve(): worktree for worktree in worktrees}

    assert by_path[bare.resolve()].bare
    assert by_path[bare.resolve()].primary
    assert not by_path[bare.resolve()].current
    assert by_path[bare.resolve()].head == ""
    assert by_path[bare.resolve()].branch == ""
    assert by_path[linked.resolve()].current
    assert not by_path[linked.resolve()].primary
    assert by_path[linked.resolve()].head == head
    assert by_path[linked.resolve()].branch == "main"
