from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from gitkraiken.git import GitError, Repository
from gitkraiken.models import Action


def commit_file(git_run, root: Path, path: str, text: str, message: str) -> str:
    file_path = root / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text)
    git_run(root, "add", "--", path)
    git_run(root, "commit", "-m", message)
    return git_run(root, "rev-parse", "HEAD").stdout.strip()


@pytest.mark.parametrize(
    ("operation", "staged", "extra_exists", "tracked_text"),
    [
        ("reset-soft", True, True, "dirty\n"),
        ("reset-mixed", False, True, "dirty\n"),
        ("reset-hard", False, False, "base\n"),
    ],
)
def test_reset_modes_have_explicit_index_and_worktree_semantics(
    git_repo: Path,
    git_run,
    operation: str,
    staged: bool,
    extra_exists: bool,
    tracked_text: str,
) -> None:
    base = commit_file(git_run, git_repo, "tracked.txt", "base\n", "base")
    commit_file(git_run, git_repo, "extra.txt", "extra\n", "extra")
    (git_repo / "tracked.txt").write_text("dirty\n")

    Repository(git_repo).execute(Action(operation, target=base))

    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == base
    assert (git_repo / "tracked.txt").read_text() == tracked_text
    assert (git_repo / "extra.txt").exists() is extra_exists
    cached = git_run(git_repo, "diff", "--cached", "--name-only").stdout.splitlines()
    assert ("extra.txt" in cached) is staged


def test_edit_message_only_amends_head_and_preserves_staged_and_unstaged_changes(
    git_repo: Path, git_run
) -> None:
    old_head = commit_file(git_run, git_repo, "tracked.txt", "committed\n", "old message")
    (git_repo / "tracked.txt").write_text("staged\n")
    git_run(git_repo, "add", "tracked.txt")
    (git_repo / "unstaged.txt").write_text("unstaged\n")
    repo = Repository(git_repo)

    repo.execute(
        Action(
            "edit-message",
            target="refs/heads/main",
            message="new message",
            expected_oid=old_head,
        )
    )

    new_head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert new_head != old_head
    assert git_run(git_repo, "show", "-s", "--format=%B", "HEAD").stdout.strip() == "new message"
    assert git_run(git_repo, "show", "HEAD:tracked.txt").stdout == "committed\n"
    assert git_run(git_repo, "diff", "--cached", "--name-only").stdout.strip() == "tracked.txt"
    assert (git_repo / "unstaged.txt").read_text() == "unstaged\n"

    with pytest.raises(GitError, match="current HEAD"):
        repo.execute(
            Action("edit-message", target=old_head, message="must fail", expected_oid=old_head)
        )
    git_run(git_repo, "switch", "--detach", "HEAD")
    with pytest.raises(GitError, match="detached"):
        repo.execute(Action("edit-message", target="HEAD", message="must fail"))


def test_create_worktree_uses_exact_target_and_requires_explicit_branch_choice(
    git_repo: Path, git_run
) -> None:
    first = commit_file(git_run, git_repo, "one.txt", "one\n", "one")
    current = commit_file(git_run, git_repo, "two.txt", "two\n", "two")
    repo = Repository(git_repo)

    repo.execute(Action("create-worktree", target=first, paths=("../detached",)))
    detached = git_repo.parent / "detached"
    assert git_run(detached, "rev-parse", "HEAD").stdout.strip() == first
    assert git_run(detached, "branch", "--show-current").stdout.strip() == ""

    destination = git_repo.parent / "named"
    destination.mkdir()
    repo.execute(
        Action("create-worktree", target=first, name="worktree/topic", paths=(str(destination),))
    )
    assert git_run(destination, "branch", "--show-current").stdout.strip() == "worktree/topic"
    assert git_run(destination, "rev-parse", "HEAD").stdout.strip() == first
    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == current
    assert git_run(git_repo, "branch", "--show-current").stdout.strip() == "main"

    occupied = git_repo.parent / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("keep\n")
    with pytest.raises(GitError, match="not empty"):
        repo.execute(Action("create-worktree", target=first, paths=(str(occupied),)))
    with pytest.raises(GitError, match="worktree branch"):
        repo.execute(
            Action(
                "create-worktree",
                target=first,
                name="--unsafe",
                paths=(str(git_repo.parent / "not-created"),),
            )
        )
    assert not (git_repo.parent / "not-created").exists()


def test_set_upstream_and_branch_remote_target_preserve_custom_mapping_and_slash_owner(
    git_repo: Path, git_run
) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "team", str(git_repo.parent / "team.git"))
    git_run(
        git_repo,
        "remote",
        "add",
        "team/origin",
        str(git_repo.parent / "team-origin.git"),
    )
    git_run(git_repo, "config", "--unset-all", "remote.team/origin.fetch")
    git_run(
        git_repo,
        "config",
        "--add",
        "remote.team/origin.fetch",
        "+refs/heads/release/*:refs/remotes/team/origin/stable/*",
    )
    tracking = "refs/remotes/team/origin/stable/topic"
    git_run(git_repo, "update-ref", tracking, head)
    repo = Repository(git_repo)

    with pytest.raises(GitError, match="owned by team/origin"):
        repo.execute(
            Action(
                "set-upstream",
                target="refs/heads/main",
                name=tracking,
                remote="team",
            )
        )
    assert git_run(git_repo, "config", "--get", "branch.main.remote", check=False).returncode == 1

    repo.execute(
        Action(
            "set-upstream",
            target="refs/heads/main",
            name=tracking,
            remote="team/origin",
        )
    )
    assert git_run(git_repo, "config", "branch.main.remote").stdout.strip() == "team/origin"
    assert git_run(git_repo, "config", "branch.main.merge").stdout.strip() == (
        "refs/heads/release/topic"
    )
    assert repo.branch_remote_target("refs/heads/main") == (
        "team/origin",
        "refs/heads/release/topic",
    )
    assert repo.branch_remote_target(tracking) == (
        "team/origin",
        "refs/heads/release/topic",
    )
    with pytest.raises(GitError, match="Unsupported branch ref"):
        repo.branch_remote_target("refs/tags/v1")


def test_set_upstream_rejects_exact_custom_non_branch_source(git_repo: Path, git_run) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "config", "--unset-all", "remote.origin.fetch")
    git_run(
        git_repo,
        "config",
        "--add",
        "remote.origin.fetch",
        "+refs/changes/42:refs/remotes/origin/review/topic",
    )
    tracking = "refs/remotes/origin/review/topic"
    git_run(git_repo, "update-ref", tracking, head)
    repo = Repository(git_repo)

    with pytest.raises(GitError, match="does not map to a branch source"):
        repo.execute(
            Action(
                "set-upstream",
                target="refs/heads/main",
                name=tracking,
                remote="origin",
            )
        )
    assert git_run(git_repo, "config", "--get", "branch.main.remote", check=False).returncode == 1
    assert git_run(git_repo, "config", "--get", "branch.main.merge", check=False).returncode == 1


def test_set_upstream_rejects_ambiguous_reverse_mapping_without_config_write(
    git_repo: Path, git_run
) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "config", "--unset-all", "remote.origin.fetch")
    for source in ("release", "archive"):
        git_run(
            git_repo,
            "config",
            "--add",
            "remote.origin.fetch",
            f"+refs/heads/{source}/*:refs/remotes/origin/stable/*",
        )
    tracking = "refs/remotes/origin/stable/topic"
    git_run(git_repo, "update-ref", tracking, head)

    with pytest.raises(GitError, match="ambiguous fetch refspecs"):
        Repository(git_repo).execute(
            Action(
                "set-upstream",
                target="refs/heads/main",
                name=tracking,
                remote="origin",
            )
        )
    assert git_run(git_repo, "config", "--get", "branch.main.remote", check=False).returncode == 1


def test_delete_remote_branch_uses_exact_source_lease_and_ignores_broad_defaults(
    git_repo: Path, bare_remote: Path, git_run
) -> None:
    commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "switch", "-c", "topic")
    old = commit_file(git_run, git_repo, "topic.txt", "old\n", "old topic")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "origin", "refs/heads/topic:refs/heads/topic")
    tracking = "refs/remotes/origin/topic"
    assert git_run(git_repo, "rev-parse", tracking).stdout.strip() == old

    advanced = commit_file(git_run, git_repo, "topic.txt", "advanced\n", "advanced topic")
    git_run(git_repo, "push", "origin", "HEAD:refs/heads/staging")
    git_run(bare_remote, "update-ref", "refs/heads/topic", advanced)
    repo = Repository(git_repo)
    with pytest.raises(GitError, match="stale info|rejected|lease"):
        repo.execute(
            Action(
                "delete-remote-branch",
                target=tracking,
                remote="origin",
                expected_oid=old,
            )
        )
    assert git_run(bare_remote, "rev-parse", "refs/heads/topic").stdout.strip() == advanced

    git_run(git_repo, "fetch", "origin")
    git_run(git_repo, "config", "push.default", "matching")
    git_run(git_repo, "config", "--add", "remote.origin.push", "+refs/heads/*:refs/heads/*")
    git_run(git_repo, "config", "remote.origin.mirror", "true")
    git_run(git_repo, "config", "push.followTags", "true")
    git_run(git_repo, "tag", "must-not-follow")
    repo.execute(
        Action(
            "delete-remote-branch",
            target=tracking,
            remote="origin",
            expected_oid=advanced,
        )
    )
    assert (
        git_run(bare_remote, "rev-parse", "--verify", "refs/heads/topic", check=False).returncode
        != 0
    )
    assert git_run(bare_remote, "rev-parse", "refs/heads/staging").stdout.strip() == advanced
    assert (
        git_run(
            bare_remote, "rev-parse", "--verify", "refs/tags/must-not-follow", check=False
        ).returncode
        != 0
    )


@pytest.mark.parametrize(
    "case", ["short-ref", "wrong-owner", "missing-oid", "multiple-urls", "moved-ref"]
)
def test_remote_delete_rejects_ambiguous_or_incomplete_targets(
    git_repo: Path, bare_remote: Path, git_run, case: str
) -> None:
    oid = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "remote", "add", "other", str(bare_remote))
    git_run(git_repo, "push", "origin", "HEAD:refs/heads/topic")
    target, remote, expected = "refs/remotes/origin/topic", "origin", oid
    if case == "short-ref":
        target = "origin/topic"
    elif case == "wrong-owner":
        remote = "other"
    elif case == "missing-oid":
        expected = ""
    elif case == "moved-ref":
        expected = "0" * 40
    else:
        git_run(git_repo, "config", "--add", "remote.origin.pushurl", str(bare_remote))
        git_run(
            git_repo,
            "config",
            "--add",
            "remote.origin.pushurl",
            str(git_repo.parent / "elsewhere.git"),
        )
    with pytest.raises(GitError):
        Repository(git_repo).execute(
            Action(
                "delete-remote-branch",
                target=target,
                remote=remote,
                expected_oid=expected,
            )
        )
    assert git_run(bare_remote, "rev-parse", "refs/heads/topic").stdout.strip() == oid


def test_remote_delete_rejects_changed_push_destination(
    git_repo: Path, bare_remote: Path, git_run
) -> None:
    oid = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "origin", "HEAD:refs/heads/topic")
    git_run(git_repo, "config", "remote.origin.pushurl", str(git_repo.parent / "unexpected.git"))
    with pytest.raises(GitError, match="push destination changed"):
        Repository(git_repo).execute(
            Action(
                "delete-remote-branch",
                target="refs/remotes/origin/topic",
                name="refs/heads/topic",
                remote="origin",
                expected_oid=oid,
                expected_push_url=str(bare_remote),
            )
        )
    assert git_run(bare_remote, "rev-parse", "refs/heads/topic").stdout.strip() == oid


def test_remote_delete_rejects_changed_mapping_even_when_source_oids_match(
    git_repo: Path, bare_remote: Path, git_run
) -> None:
    oid = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "origin", "HEAD:refs/heads/topic", "HEAD:refs/heads/other")
    tracking = "refs/remotes/origin/topic"
    action = Action(
        "delete-remote-branch",
        target=tracking,
        name="refs/heads/topic",
        remote="origin",
        expected_oid=oid,
    )
    git_run(git_repo, "config", "remote.origin.fetch", f"+refs/heads/other:{tracking}")
    with pytest.raises(GitError, match="mapping changed"):
        Repository(git_repo).execute(action)
    assert git_run(bare_remote, "rev-parse", "refs/heads/topic").stdout.strip() == oid
    assert git_run(bare_remote, "rev-parse", "refs/heads/other").stdout.strip() == oid


def test_apply_patch_rejects_fifo_without_waiting_for_a_writer(git_repo: Path, git_run) -> None:
    commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    fifo = git_repo / "patch.fifo"
    os.mkfifo(fifo)

    started = time.monotonic()
    with pytest.raises(GitError, match="not a regular file"):
        Repository(git_repo).execute(Action("apply-patch", paths=("patch.fifo",)))
    assert time.monotonic() - started < 1.0


def test_apply_patch_checks_then_updates_only_worktree_and_rejects_unsafe_patch(
    git_repo: Path, git_run
) -> None:
    commit_file(git_run, git_repo, "patched.txt", "before\n", "base")
    (git_repo / "index.txt").write_text("index base\n")
    git_run(git_repo, "add", "index.txt")
    git_run(git_repo, "commit", "-m", "index base")
    (git_repo / "patched.txt").write_text("after\n")
    patch = git_repo / "change.patch"
    patch.write_text(git_run(git_repo, "diff", "--", "patched.txt").stdout)
    (git_repo / "patched.txt").write_text("before\n")
    (git_repo / "index.txt").write_text("staged\n")
    git_run(git_repo, "add", "index.txt")
    cached_before = git_run(git_repo, "diff", "--cached").stdout

    Repository(git_repo).execute(Action("apply-patch", paths=("change.patch",)))

    assert (git_repo / "patched.txt").read_text() == "after\n"
    assert git_run(git_repo, "diff", "--cached").stdout == cached_before
    bad = git_repo / "bad.patch"
    bad.write_text("not a patch\n")
    before = (git_repo / "patched.txt").read_text()
    with pytest.raises(GitError, match="check patch failed"):
        Repository(git_repo).execute(Action("apply-patch", paths=(str(bad),)))
    assert (git_repo / "patched.txt").read_text() == before

    unsafe = git_repo / "unsafe.patch"
    unsafe.write_text(
        "diff --git a/../outside.txt b/../outside.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/../outside.txt\n"
        "@@ -0,0 +1 @@\n"
        "+outside\n"
    )
    with pytest.raises(GitError):
        Repository(git_repo).execute(Action("apply-patch", paths=(str(unsafe),)))
    assert not (git_repo.parent / "outside.txt").exists()


@pytest.mark.parametrize("change", ["head", "ref", "detached-mismatch"])
def test_generic_context_guard_prevents_worktree_creation_after_stale_selection(
    git_repo: Path, git_run, change: str
) -> None:
    selected = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "branch", "selected", selected)
    expected_branch = "main"
    if change == "head":
        action = Action(
            "create-worktree",
            target=selected,
            paths=(f"../stale-{change}",),
            expected_head=selected,
            expected_branch=expected_branch,
            expected_ref="refs/heads/selected",
            expected_oid=selected,
        )
        commit_file(git_run, git_repo, "later.txt", "later\n", "later")
    elif change == "ref":
        action = Action(
            "create-worktree",
            target=selected,
            paths=(f"../stale-{change}",),
            expected_head=selected,
            expected_branch="main",
            expected_ref="refs/heads/selected",
            expected_oid=selected,
        )
        moved = commit_file(git_run, git_repo, "later.txt", "later\n", "later")
        git_run(git_repo, "update-ref", "refs/heads/selected", moved)
    else:
        action = Action(
            "create-worktree",
            target=selected,
            paths=(f"../stale-{change}",),
            expected_head=selected,
            expected_branch="",
            expected_ref="refs/heads/selected",
            expected_oid=selected,
        )

    with pytest.raises(GitError, match="changed"):
        Repository(git_repo).execute(action)
    assert not (git_repo.parent / f"stale-{change}").exists()


def test_expected_ref_guards_empty_unborn_head_context(git_repo: Path, git_run) -> None:
    selected = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "switch", "--orphan", "unborn")
    repo = Repository(git_repo)
    guarded = dict(
        expected_head="",
        expected_branch="unborn",
        expected_ref="refs/heads/main",
        expected_oid=selected,
    )

    repo.execute(
        Action(
            "create-worktree",
            target=selected,
            paths=("../from-unborn",),
            **guarded,
        )
    )
    assert git_run(git_repo.parent / "from-unborn", "rev-parse", "HEAD").stdout.strip() == selected

    git_run(git_repo, "switch", "main")
    with pytest.raises(GitError, match="Branch changed"):
        repo.execute(
            Action(
                "create-worktree",
                target=selected,
                paths=("../stale-unborn",),
                **guarded,
            )
        )
    assert not (git_repo.parent / "stale-unborn").exists()


def test_guarded_actions_reject_active_conflict_but_stage_remains_available(
    git_repo: Path, git_run
) -> None:
    commit_file(git_run, git_repo, "conflict.txt", "base\n", "base")
    git_run(git_repo, "switch", "-c", "topic")
    commit_file(git_run, git_repo, "conflict.txt", "topic\n", "topic")
    git_run(git_repo, "switch", "main")
    commit_file(git_run, git_repo, "conflict.txt", "main\n", "main")
    assert git_run(git_repo, "merge", "topic", check=False).returncode != 0
    repo = Repository(git_repo)
    snapshot = repo.snapshot()
    destination = git_repo.parent / "blocked"
    guarded = dict(
        expected_head=snapshot.head,
        expected_branch=snapshot.branch,
        expected_ref="refs/heads/main",
        expected_oid=snapshot.head,
    )

    with pytest.raises(GitError, match="operation is active"):
        repo.execute(
            Action(
                "create-worktree",
                target=snapshot.head,
                paths=(str(destination),),
                **guarded,
            )
        )
    assert not destination.exists()

    (git_repo / "conflict.txt").write_text("resolved\n")
    repo.execute(Action("stage", paths=("conflict.txt",), **guarded))
    assert not repo.snapshot().conflicts
