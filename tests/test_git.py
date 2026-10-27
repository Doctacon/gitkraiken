from __future__ import annotations

import os
import shlex
from pathlib import Path

import pytest

from rhizome.git import GitError, Repository, _git_environment
from rhizome.models import Action


def commit_file(git_run, root: Path, path: str, text: str, message: str) -> str:
    file_path = root / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text)
    git_run(root, "add", "--", path)
    git_run(root, "commit", "-m", message)
    return git_run(root, "rev-parse", "HEAD").stdout.strip()


def test_discovers_root_and_rejects_non_repository_and_bare(
    git_repo: Path, bare_remote: Path, tmp_path: Path
) -> None:
    nested = git_repo / "one" / "two"
    nested.mkdir(parents=True)
    assert Repository(nested).root == git_repo.resolve()

    ordinary = tmp_path / "not-a-repository"
    ordinary.mkdir()
    with pytest.raises(GitError, match="not a git repository"):
        Repository(ordinary)
    with pytest.raises(GitError, match="Bare repositories are not supported"):
        Repository(bare_remote)


def test_stash_selection_does_not_drop_a_newly_inserted_stash(git_repo, git_run):
    commit_file(git_run, git_repo, "file.txt", "base\n", "base")
    repo = Repository(git_repo)
    (git_repo / "file.txt").write_text("original stash\n")
    repo.execute(Action("stash", message="selected stash"))
    selected = repo.snapshot().stashes[0]
    (git_repo / "file.txt").write_text("new stash\n")
    repo.execute(Action("stash", message="newer stash"))
    with pytest.raises(GitError, match="selected stash changed"):
        repo.execute(Action("stash-drop", target=selected.ref, expected_oid=selected.oid))
    assert len(repo.snapshot().stashes) == 2


def test_empty_repository_snapshot(git_repo: Path) -> None:
    snapshot = Repository(git_repo).snapshot()
    assert snapshot.root == git_repo.resolve()
    assert snapshot.branch == "main"
    assert snapshot.head == ""
    assert snapshot.refs == ()
    assert snapshot.files == ()
    assert snapshot.history == ()
    assert not snapshot.has_more


def test_history_detail_root_commit_detached_and_limit(git_repo: Path, git_run) -> None:
    first = commit_file(git_run, git_repo, "first.txt", "one\n", "first subject")
    second = commit_file(git_run, git_repo, "second.txt", "two\n", "second subject")
    repo = Repository(git_repo)

    snapshot = repo.snapshot(limit=1)
    commits = [line.commit for line in snapshot.history if line.commit]
    assert [commit.oid for commit in commits] == [second]
    assert snapshot.has_more
    assert snapshot.history[0].graph == ""

    detail = repo.detail(first)
    assert detail.commit.subject == "first subject"
    assert detail.message == "first subject"
    assert [(item.index, item.path) for item in detail.files] == [("A", "first.txt")]
    assert "first.txt" in detail.stat
    assert "+one" in repo.diff(oid=first)

    git_run(git_repo, "switch", "--detach", first)
    detached = repo.snapshot(ref=first)
    assert detached.branch == ""
    assert detached.head == first
    assert detached.history[0].commit is not None
    assert detached.history[0].commit.oid == first


def test_history_order_is_stable_when_checking_out_equal_timestamp_tips(
    git_repo: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    timestamp = "2001-02-03T04:05:06+00:00"
    monkeypatch.setenv("GIT_AUTHOR_DATE", timestamp)
    monkeypatch.setenv("GIT_COMMITTER_DATE", timestamp)
    base = commit_file(git_run, git_repo, "base.txt", "base\n", "base")

    git_run(git_repo, "switch", "-c", "left")
    commit_file(git_run, git_repo, "left.txt", "left\n", "left")
    git_run(git_repo, "switch", "main")
    git_run(git_repo, "switch", "-c", "right")
    commit_file(git_run, git_repo, "right.txt", "right\n", "right")
    repo = Repository(git_repo)

    before = repo.snapshot()
    repo.execute(Action("checkout", target="refs/heads/left"))
    after = repo.snapshot()

    def history_oids(snapshot) -> list[str]:
        return [line.commit.oid for line in snapshot.history if line.commit is not None]

    assert history_oids(before)[-1] == base
    assert history_oids(after) == history_oids(before)
    repo.execute(Action("checkout", target="refs/heads/right"))
    assert history_oids(repo.snapshot()) == history_oids(before)


def test_history_is_one_newest_first_timeline_across_branches(
    git_repo: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    def dated_commit(day: int) -> str:
        timestamp = f"2026-10-{day:02d}T12:00:00+00:00"
        monkeypatch.setenv("GIT_AUTHOR_DATE", timestamp)
        monkeypatch.setenv("GIT_COMMITTER_DATE", timestamp)
        return commit_file(git_run, git_repo, f"day-{day}.txt", "content\n", f"day {day}")

    base = dated_commit(1)
    git_run(git_repo, "switch", "-c", "topic")
    middle = dated_commit(3)
    git_run(git_repo, "switch", "main")
    older = dated_commit(2)
    newest = dated_commit(4)

    repo = Repository(git_repo)
    commits = [line.commit for line in repo.snapshot().history if line.commit]
    assert [item.date for item in commits] == sorted((item.date for item in commits), reverse=True)
    expected = [newest, middle, older, base]
    assert [item.oid for item in commits] == expected

    for limit in range(1, len(expected) + 2):
        page = repo.snapshot(limit=limit)
        assert [line.commit.oid for line in page.history if line.commit] == expected[:limit]
        assert page.has_more is (limit < len(expected))


def test_history_includes_newer_hidden_ancestor_before_its_child(
    git_repo: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2035-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00+00:00")
    parent = commit_file(git_run, git_repo, "parent.txt", "parent\n", "parent")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2025-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2021-01-01T00:00:00+00:00")
    child = commit_file(git_run, git_repo, "child.txt", "child\n", "child")

    repo = Repository(git_repo)
    first_page = repo.snapshot(limit=1)
    assert [line.commit.oid for line in first_page.history if line.commit] == [parent]
    assert first_page.has_more

    commits = [line.commit for line in repo.snapshot(limit=2).history if line.commit]
    assert [commit.oid for commit in commits] == [parent, child]
    assert commits[1].parents == (parent,)


def test_history_uses_author_epoch_not_committer_date_or_displayed_offset(
    git_repo: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2000-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2000-01-01T00:00:00+00:00")
    base = commit_file(git_run, git_repo, "base.txt", "base\n", "base")

    git_run(git_repo, "switch", "-c", "far-east")
    east_date = "2024-01-01T10:00:00+14:00"
    monkeypatch.setenv("GIT_AUTHOR_DATE", east_date)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2040-01-01T00:00:00+00:00")
    east = commit_file(git_run, git_repo, "east.txt", "east\n", "east")

    git_run(git_repo, "switch", "main")
    west_date = "2023-12-31T23:00:00-12:00"
    monkeypatch.setenv("GIT_AUTHOR_DATE", west_date)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2010-01-01T00:00:00+00:00")
    west = commit_file(git_run, git_repo, "west.txt", "west\n", "west")

    commits = [line.commit for line in Repository(git_repo).snapshot().history if line.commit]
    assert [commit.oid for commit in commits] == [west, east, base]
    assert {commit.oid: commit.date for commit in commits} == {
        west: west_date,
        east: east_date,
        base: "2000-01-01T00:00:00Z",
    }


def test_history_reaches_remote_tag_and_detached_tips_and_honors_ref(
    git_repo: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    def dated_commit(day: int, name: str) -> str:
        timestamp = f"2028-06-{day:02d}T00:00:00+00:00"
        monkeypatch.setenv("GIT_AUTHOR_DATE", timestamp)
        monkeypatch.setenv("GIT_COMMITTER_DATE", timestamp)
        return commit_file(git_run, git_repo, f"{name}.txt", f"{name}\n", name)

    base = dated_commit(1, "base")

    git_run(git_repo, "switch", "-c", "remote-work")
    remote_only = dated_commit(2, "remote")
    git_run(git_repo, "update-ref", "refs/remotes/origin/only", remote_only)
    git_run(git_repo, "switch", "main")
    git_run(git_repo, "branch", "-D", "remote-work")

    git_run(git_repo, "switch", "-c", "tag-work")
    tag_only = dated_commit(3, "tag")
    git_run(git_repo, "tag", "only-tag", tag_only)
    git_run(git_repo, "switch", "main")
    git_run(git_repo, "branch", "-D", "tag-work")

    git_run(git_repo, "switch", "--detach", "main")
    detached = dated_commit(4, "detached")

    repo = Repository(git_repo)
    commits = [line.commit for line in repo.snapshot().history if line.commit]
    assert [commit.oid for commit in commits] == [detached, tag_only, remote_only, base]
    assert "HEAD" in commits[0].refs

    tag_history = [
        line.commit.oid for line in repo.snapshot(ref="refs/tags/only-tag").history if line.commit
    ]
    assert tag_history == [tag_only, base]


def test_history_merge_preserves_parent_metadata(git_repo: Path, git_run) -> None:
    base = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "switch", "-c", "topic")
    topic = commit_file(git_run, git_repo, "topic.txt", "topic\n", "topic")
    git_run(git_repo, "switch", "main")
    main = commit_file(git_run, git_repo, "main.txt", "main\n", "main")
    git_run(git_repo, "merge", "--no-edit", "topic")
    merge = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()

    snapshot = Repository(git_repo).snapshot()
    commits = [line.commit for line in snapshot.history if line.commit]
    by_oid = {commit.oid: commit for commit in commits}
    assert by_oid[merge].parents == (main, topic)
    assert {base, topic, main, merge} <= set(by_oid)
    assert all(line.commit is not None and line.graph == "" for line in snapshot.history)


def test_status_and_diffs_handle_rename_and_special_literal_paths(git_repo: Path, git_run) -> None:
    special = ":(glob)* literal name.txt"
    newline_name = "line\nbreak.txt"
    for name, content in (
        ("staged.txt", "old\n"),
        ("unstaged.txt", "old\n"),
        ("old name.txt", "rename\n"),
        (special, "special old\n"),
        (newline_name, "newline old\n"),
    ):
        (git_repo / name).write_text(content)
    git_run(git_repo, "add", "--all")
    git_run(git_repo, "commit", "-m", "base")

    (git_repo / "staged.txt").write_text("staged new\n")
    git_run(git_repo, "add", "staged.txt")
    (git_repo / "unstaged.txt").write_text("unstaged new\n")
    git_run(git_repo, "mv", "old name.txt", "new name.txt")
    (git_repo / special).write_text("special new\n")
    (git_repo / newline_name).write_text("newline new\n")
    untracked = "--untracked :(literal).txt"
    (git_repo / untracked).write_text("new file body\n")
    binary = "binary.bin"
    (git_repo / binary).write_bytes(b"\x00\x01\x02")

    repo = Repository(git_repo)
    by_path = {change.path: change for change in repo.snapshot().files}
    assert by_path["staged.txt"].index == "M"
    assert by_path["unstaged.txt"].worktree == "M"
    assert by_path["new name.txt"].original_path == "old name.txt"
    assert by_path[special].worktree == "M"
    assert by_path[newline_name].worktree == "M"
    assert by_path[untracked].index == "?"

    assert "+staged new" in repo.diff("staged.txt", staged=True)
    assert "+unstaged new" in repo.diff("unstaged.txt")
    assert "+special new" in repo.diff(special)
    assert "+newline new" in repo.diff(newline_name)
    assert "+new file body" in repo.diff(untracked)
    binary_patch = repo.diff(binary)
    assert "Binary files" in binary_patch
    assert "GIT binary patch" not in binary_patch
    rename_patch = repo.diff("new name.txt", staged=True)
    assert "old name.txt" in rename_patch and "new name.txt" in rename_patch

    repo.execute(Action("unstage", paths=("new name.txt",)))
    rename_paths = {
        change.path: change for change in repo.snapshot().files if "name.txt" in change.path
    }
    assert rename_paths
    assert not any(change.staged for change in rename_paths.values())


def test_large_text_diff_is_bounded_without_binary_patch(git_repo: Path, git_run) -> None:
    path = git_repo / "large.txt"
    path.write_text("old line\n" * 300_000)
    git_run(git_repo, "add", "large.txt")
    git_run(git_repo, "commit", "-m", "large base")
    path.write_text("new line\n" * 300_000)

    patch = Repository(git_repo).diff("large.txt")
    assert "[diff truncated by Rhizome]" in patch
    assert len(patch.encode("utf-8", "surrogateescape")) < 2_100_000
    assert "GIT binary patch" not in patch


def test_stage_unstage_discard_and_commit_safety(git_repo: Path, git_run) -> None:
    commit_file(git_run, git_repo, "tracked.txt", "old\n", "base")
    repo = Repository(git_repo)
    (git_repo / "tracked.txt").write_text("worktree\n")
    (git_repo / "untracked.txt").write_text("untracked\n")

    repo.execute(Action("stage", paths=("tracked.txt",)))
    (git_repo / "tracked.txt").write_text("after index\n")
    repo.execute(Action("discard", paths=("tracked.txt",)))
    assert (git_repo / "tracked.txt").read_text() == "worktree\n"
    assert repo.snapshot().files[0].index == "M"

    with pytest.raises(GitError, match="explicit tracked"):
        repo.execute(Action("discard"))
    with pytest.raises(GitError, match="untracked"):
        repo.execute(Action("discard", paths=("untracked.txt",)))
    assert (git_repo / "untracked.txt").exists()

    intent_path = git_repo / "intent.txt"
    intent_path.write_text("intent content must survive\n")
    git_run(git_repo, "add", "-N", "--", "intent.txt")
    with pytest.raises(GitError, match="intent-to-add"):
        repo.execute(Action("discard", paths=("intent.txt",)))
    assert intent_path.read_text() == "intent content must survive\n"

    repo.execute(Action("unstage"))
    assert not any(change.staged for change in repo.snapshot().files)
    with pytest.raises(GitError, match="no staged changes"):
        repo.execute(Action("commit", message="must not commit worktree"))

    repo.execute(Action("stage", paths=("tracked.txt",)))
    repo.execute(Action("commit", message="only staged"))
    assert git_run(git_repo, "show", "HEAD:tracked.txt").stdout == "worktree\n"
    assert (git_repo / "untracked.txt").exists()


def test_unstage_in_unborn_repository(git_repo: Path) -> None:
    repo = Repository(git_repo)
    (git_repo / "new.txt").write_text("new\n")
    repo.execute(Action("stage"))
    assert repo.snapshot().files[0].staged
    repo.execute(Action("unstage"))
    change = repo.snapshot().files[0]
    assert change.index == "?"
    assert (git_repo / "new.txt").exists()


def test_branch_tag_checkout_rename_delete_and_soft_reset(git_repo: Path, git_run) -> None:
    first = commit_file(git_run, git_repo, "one.txt", "one\n", "one")
    second = commit_file(git_run, git_repo, "two.txt", "two\n", "two")
    repo = Repository(git_repo)

    repo.execute(Action("create-branch", name="feature", target=first))
    assert repo.snapshot().branch == "feature"
    repo.execute(Action("rename-branch", target="refs/heads/feature", name="renamed"))
    assert repo.snapshot().branch == "renamed"
    repo.execute(Action("checkout", target="main"))
    repo.execute(Action("delete-branch", target="refs/heads/renamed"))

    repo.execute(Action("tag", name="v1", target=first))
    assert any(ref.name == "v1" for ref in repo.snapshot().refs)
    repo.execute(Action("checkout", target="v1"))
    assert repo.snapshot().branch == ""
    repo.execute(Action("checkout", target="main"))
    repo.execute(Action("delete-tag", target="refs/tags/v1"))

    repo.execute(Action("reset-soft", target=first))
    assert repo.snapshot().head == first
    assert any(change.path == "two.txt" and change.staged for change in repo.snapshot().files)
    assert second != first


def test_merge_rebase_cherry_pick_and_revert_actions(git_repo: Path, git_run) -> None:
    base = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    repo = Repository(git_repo)

    repo.execute(Action("create-branch", name="onto", target=base))
    onto = commit_file(git_run, git_repo, "onto.txt", "onto\n", "onto")
    repo.execute(Action("checkout", target="main"))
    repo.execute(Action("create-branch", name="work", target=base))
    original_work = commit_file(git_run, git_repo, "work.txt", "work\n", "work")

    repo.execute(Action("rebase", target="onto"))
    rebased = repo.snapshot().head
    assert rebased != original_work
    assert repo.detail(rebased).commit.parents == (onto,)

    repo.execute(Action("checkout", target="main"))
    repo.execute(Action("cherry-pick", target=original_work))
    assert (git_repo / "work.txt").exists()
    picked = repo.snapshot().head
    repo.execute(Action("revert", target=picked))
    assert not (git_repo / "work.txt").exists()

    repo.execute(Action("merge", target="onto"))
    assert (git_repo / "onto.txt").read_text() == "onto\n"
    assert len(repo.detail("HEAD").commit.parents) == 2


def test_rebase_does_not_implicitly_stash_or_move_other_branches(git_repo, git_run):
    commit_file(git_run, git_repo, "base.txt", "base\n", "Base")
    git_run(git_repo, "switch", "-c", "feature")
    original = commit_file(git_run, git_repo, "feature.txt", "feature\n", "Feature")
    git_run(git_repo, "branch", "sibling")
    git_run(git_repo, "switch", "main")
    onto = commit_file(git_run, git_repo, "teammate.txt", "teammate\n", "New base work")
    git_run(git_repo, "switch", "feature")
    git_run(git_repo, "config", "rebase.autoStash", "true")
    git_run(git_repo, "config", "rebase.updateRefs", "true")
    repo = Repository(git_repo)
    (git_repo / "feature.txt").write_text("uncommitted work\n")
    with pytest.raises(GitError, match="unstaged changes"):
        repo.execute(Action("rebase", target="refs/heads/main"))
    assert repo.snapshot().head == original
    assert not repo.snapshot().stashes
    assert (git_repo / "feature.txt").read_text() == "uncommitted work\n"
    (git_repo / "feature.txt").write_text("feature\n")
    repo.execute(Action("rebase", target="refs/heads/main"))
    assert repo.snapshot().head != original
    assert repo.snapshot().branch == "feature"
    assert git_run(git_repo, "rev-parse", "HEAD^").stdout.strip() == onto
    assert git_run(git_repo, "rev-parse", "sibling").stdout.strip() == original
    assert git_run(git_repo, "rev-parse", "main").stdout.strip() == onto


@pytest.mark.parametrize("change", ["branch", "head", "target", "deleted-target"])
def test_rebase_rejects_context_changed_since_menu_opened(git_repo, git_run, change):
    base = commit_file(git_run, git_repo, "base.txt", "base\n", "Base")
    git_run(git_repo, "branch", "test")
    git_run(git_repo, "switch", "-c", "feature")
    head = commit_file(git_run, git_repo, "feature.txt", "feature\n", "Feature")
    repo = Repository(git_repo)
    action = Action(
        "rebase",
        target="refs/heads/test",
        expected_oid=base,
        expected_branch="feature",
        expected_head=head,
    )
    if change == "branch":
        git_run(git_repo, "switch", "-c", "another")  # Same OID, different branch.
    elif change == "head":
        commit_file(git_run, git_repo, "extra.txt", "extra\n", "Extra feature work")
    elif change == "target":
        git_run(git_repo, "update-ref", "refs/heads/test", head)
    else:
        git_run(git_repo, "branch", "-d", "test")
    before = repo.snapshot()
    with pytest.raises(GitError, match="Branch changed|does not name a commit"):
        repo.execute(action)
    assert repo.snapshot() == before


def test_stash_includes_untracked_apply_and_drop(git_repo: Path, git_run) -> None:
    commit_file(git_run, git_repo, "tracked.txt", "old\n", "base")
    repo = Repository(git_repo)
    (git_repo / "tracked.txt").write_text("changed\n")
    (git_repo / "new.txt").write_text("new\n")
    repo.execute(Action("stash", message="saved state"))
    stash = repo.snapshot().stashes[0]
    assert stash.ref == "stash@{0}"
    assert not (git_repo / "new.txt").exists()

    repo.execute(Action("stash-apply", target=stash.ref))
    assert (git_repo / "new.txt").read_text() == "new\n"
    # Restore the worktree before testing exact-ref drop independently.
    git_run(git_repo, "reset", "--hard", "HEAD")
    os.remove(git_repo / "new.txt")
    repo.execute(Action("stash-drop", target=stash.ref))
    assert repo.snapshot().stashes == ()
    with pytest.raises(GitError, match="Unknown stash"):
        repo.execute(Action("stash-pop", target="stash@{99}"))


def test_merge_conflict_abort_and_continue(git_repo: Path, git_run) -> None:
    commit_file(git_run, git_repo, "conflict.txt", "base\n", "base")
    git_run(git_repo, "switch", "-c", "topic")
    commit_file(git_run, git_repo, "conflict.txt", "topic\n", "topic")
    git_run(git_repo, "switch", "main")
    commit_file(git_run, git_repo, "conflict.txt", "main\n", "main")
    repo = Repository(git_repo)

    with pytest.raises(GitError):
        repo.execute(Action("merge", target="topic"))
    snapshot = repo.snapshot()
    assert snapshot.operation == "merge"
    assert snapshot.conflicts
    repo.execute(Action("abort"))
    assert repo.snapshot().operation == ""

    with pytest.raises(GitError):
        repo.execute(Action("merge", target="topic"))
    (git_repo / "conflict.txt").write_text("resolved\n")
    repo.execute(Action("stage", paths=("conflict.txt",)))
    repo.execute(Action("continue"))
    assert repo.snapshot().operation == ""
    assert len(repo.detail("HEAD").commit.parents) == 2
    with pytest.raises(GitError, match="no Git operation"):
        repo.execute(Action("abort"))


def test_am_conflict_is_detected_and_can_abort_or_continue(git_repo: Path, git_run) -> None:
    commit_file(git_run, git_repo, "mail.txt", "base\n", "base")
    git_run(git_repo, "switch", "-c", "mail-topic")
    commit_file(git_run, git_repo, "mail.txt", "from patch\n", "mail patch")
    patch = git_repo.parent / "change.patch"
    patch.write_text(git_run(git_repo, "format-patch", "-1", "--stdout").stdout)
    git_run(git_repo, "switch", "main")
    commit_file(git_run, git_repo, "mail.txt", "from main\n", "main change")
    repo = Repository(git_repo)

    assert git_run(git_repo, "am", str(patch), check=False).returncode != 0
    assert repo.snapshot().operation == "am"
    repo.execute(Action("abort"))
    assert repo.snapshot().operation == ""

    assert git_run(git_repo, "am", str(patch), check=False).returncode != 0
    (git_repo / "mail.txt").write_text("resolved mail\n")
    repo.execute(Action("stage", paths=("mail.txt",)))
    repo.execute(Action("continue"))
    assert repo.snapshot().operation == ""
    assert repo.detail("HEAD").commit.subject == "mail patch"


@pytest.mark.usefixtures("overlapping_remotes")
def test_remote_ref_owner_uses_longest_configured_remote_name(git_repo: Path, git_run) -> None:
    commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "update-ref", "refs/remotes/team/topic", "HEAD")
    git_run(git_repo, "update-ref", "refs/remotes/team/origin/topic", "HEAD")
    git_run(git_repo, "update-ref", "refs/remotes/unassigned/topic", "HEAD")

    remote_refs = {
        ref.full_name: ref for ref in Repository(git_repo).snapshot().refs if ref.kind == "remote"
    }
    assert remote_refs["refs/remotes/team/topic"].remote == "team"
    slash_remote_ref = remote_refs["refs/remotes/team/origin/topic"]
    assert slash_remote_ref.name == "team/origin/topic"
    assert slash_remote_ref.remote == "team/origin"
    assert remote_refs["refs/remotes/unassigned/topic"].remote == ""


@pytest.mark.usefixtures("overlapping_remotes")
def test_slash_remote_checkout_creates_and_reuses_exact_tracking_branch(
    git_repo: Path, git_run
) -> None:
    commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "update-ref", "refs/remotes/team/origin/topic", "HEAD")
    repo = Repository(git_repo)
    checkout = Action(
        "checkout",
        target="refs/remotes/team/origin/topic",
        remote="team/origin",
    )

    repo.execute(checkout)
    assert repo.snapshot().branch == "topic"
    upstream = git_run(
        git_repo,
        "for-each-ref",
        "--format=%(upstream)",
        "refs/heads/topic",
    ).stdout.strip()
    assert upstream == "refs/remotes/team/origin/topic"

    repo.execute(Action("checkout", target="refs/heads/main"))
    repo.execute(checkout)
    assert repo.snapshot().branch == "topic"


def test_remote_checkout_tracks_custom_wildcard_fetch_source(git_repo: Path, git_run) -> None:
    commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "config", "--unset-all", "remote.origin.fetch")
    git_run(
        git_repo,
        "config",
        "--add",
        "remote.origin.fetch",
        "+refs/heads/release/*:refs/remotes/origin/stable/*",
    )
    git_run(git_repo, "update-ref", "refs/remotes/origin/stable/topic", "HEAD")

    Repository(git_repo).execute(
        Action(
            "checkout",
            target="refs/remotes/origin/stable/topic",
            remote="origin",
        )
    )

    assert git_run(git_repo, "branch", "--show-current").stdout.strip() == "stable/topic"
    assert (
        git_run(git_repo, "config", "branch.stable/topic.merge").stdout.strip()
        == "refs/heads/release/topic"
    )
    assert (
        git_run(
            git_repo,
            "for-each-ref",
            "--format=%(upstream)",
            "refs/heads/stable/topic",
        ).stdout.strip()
        == "refs/remotes/origin/stable/topic"
    )


def test_remote_checkout_tracks_custom_direct_full_ref_source(git_repo: Path, git_run) -> None:
    commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "config", "--unset-all", "remote.origin.fetch")
    git_run(
        git_repo,
        "config",
        "--add",
        "remote.origin.fetch",
        "+refs/changes/42:refs/remotes/origin/review/topic",
    )
    git_run(git_repo, "update-ref", "refs/remotes/origin/review/topic", "HEAD")

    Repository(git_repo).execute(
        Action(
            "checkout",
            target="refs/remotes/origin/review/topic",
            remote="origin",
        )
    )

    assert git_run(git_repo, "branch", "--show-current").stdout.strip() == "review/topic"
    assert (
        git_run(git_repo, "config", "branch.review/topic.merge").stdout.strip() == "refs/changes/42"
    )
    assert (
        git_run(
            git_repo,
            "for-each-ref",
            "--format=%(upstream)",
            "refs/heads/review/topic",
        ).stdout.strip()
        == "refs/remotes/origin/review/topic"
    )


@pytest.mark.parametrize(
    ("fetch_refspecs", "message"),
    [
        (
            ("+refs/heads/other/*:refs/remotes/origin/other/*",),
            "No fetch refspec",
        ),
        (
            (
                "+refs/heads/release/*:refs/remotes/origin/stable/*",
                "+refs/heads/archive/*:refs/remotes/origin/stable/*",
            ),
            "ambiguous fetch refspecs",
        ),
        (
            (
                "+refs/heads/release/*:refs/remotes/origin/primary/*",
                "+refs/heads/release/*:refs/remotes/origin/stable/*",
            ),
            "ambiguous fetch refspecs",
        ),
        (
            (
                "+refs/heads/release/*:refs/remotes/origin/stable/*",
                "^refs/heads/release/topic",
            ),
            "No fetch refspec",
        ),
    ],
)
def test_remote_checkout_rejects_missing_or_ambiguous_fetch_mapping_before_change(
    git_repo: Path,
    git_run,
    fetch_refspecs: tuple[str, ...],
    message: str,
) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "config", "--unset-all", "remote.origin.fetch")
    for refspec in fetch_refspecs:
        git_run(git_repo, "config", "--add", "remote.origin.fetch", refspec)
    tracking_ref = "refs/remotes/origin/stable/topic"
    git_run(git_repo, "update-ref", tracking_ref, "HEAD")
    repo = Repository(git_repo)

    with pytest.raises(GitError, match=message):
        repo.execute(Action("checkout", target=tracking_ref, remote="origin"))

    assert repo.snapshot().branch == "main"
    assert repo.snapshot().head == head
    branch = git_run(
        git_repo,
        "show-ref",
        "--verify",
        "--quiet",
        "refs/heads/stable/topic",
        check=False,
    )
    assert branch.returncode != 0


def test_remote_checkout_rejects_ambiguous_mapping_before_reusing_branch(
    git_repo: Path, git_run
) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "config", "--unset-all", "remote.origin.fetch")
    git_run(
        git_repo,
        "config",
        "--add",
        "remote.origin.fetch",
        "+refs/heads/release/*:refs/remotes/origin/stable/*",
    )
    git_run(git_repo, "update-ref", "refs/remotes/origin/stable/topic", "HEAD")
    git_run(git_repo, "branch", "stable/topic", "HEAD")
    git_run(git_repo, "config", "branch.stable/topic.remote", "origin")
    git_run(git_repo, "config", "branch.stable/topic.merge", "refs/heads/release/topic")
    git_run(
        git_repo,
        "config",
        "--add",
        "remote.origin.fetch",
        "+refs/heads/archive/*:refs/remotes/origin/stable/*",
    )
    repo = Repository(git_repo)

    with pytest.raises(GitError, match="ambiguous fetch refspecs"):
        repo.execute(
            Action(
                "checkout",
                target="refs/remotes/origin/stable/topic",
                remote="origin",
            )
        )

    assert repo.snapshot().branch == "main"
    assert repo.snapshot().head == head


def test_remote_checkout_rejects_an_excluded_source_despite_same_upstream_destination(
    git_repo: Path, git_run
) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "config", "--unset-all", "remote.origin.fetch")
    for mapping in (
        "+refs/heads/release/*:refs/remotes/origin/stable/*",
        "+refs/heads/archive/*:refs/remotes/origin/stable/*",
        "^refs/heads/archive/*",
    ):
        git_run(git_repo, "config", "--add", "remote.origin.fetch", mapping)
    git_run(git_repo, "update-ref", "refs/remotes/origin/stable/topic", head)
    git_run(git_repo, "branch", "stable/topic", head)
    git_run(git_repo, "config", "branch.stable/topic.remote", "origin")
    git_run(git_repo, "config", "branch.stable/topic.merge", "refs/heads/archive/topic")
    assert git_run(
        git_repo, "rev-parse", "--symbolic-full-name", "stable/topic@{upstream}"
    ).stdout.strip() == ("refs/remotes/origin/stable/topic")
    repo = Repository(git_repo)
    with pytest.raises(GitError, match="cannot be reused"):
        repo.execute(Action("checkout", target="refs/remotes/origin/stable/topic", remote="origin"))
    assert repo.snapshot().branch == "main"
    assert repo.snapshot().head == head


def test_remote_checkout_refuses_existing_branch_without_exact_canonical_upstream(
    git_repo: Path, git_run
) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "update-ref", "refs/remotes/origin/topic", "HEAD")
    git_run(git_repo, "branch", "topic", "HEAD")
    repo = Repository(git_repo)
    checkout = Action(
        "checkout",
        target="refs/remotes/origin/topic",
        remote="origin",
    )

    with pytest.raises(GitError, match="differently named local branch") as missing:
        repo.execute(checkout)
    assert "refs/heads/topic" in str(missing.value)
    assert "refs/remotes/origin/topic" in str(missing.value)
    assert repo.snapshot().branch == "main"
    assert repo.snapshot().head == head

    # This local upstream has the same short spelling as origin/topic, but its
    # canonical namespace is refs/heads and must not be treated as the remote ref.
    git_run(git_repo, "branch", "origin/topic", "HEAD")
    git_run(git_repo, "config", "branch.topic.remote", ".")
    git_run(git_repo, "config", "branch.topic.merge", "refs/heads/origin/topic")
    assert repo.snapshot().refs

    with pytest.raises(GitError, match="differently named local branch") as different:
        repo.execute(checkout)
    message = str(different.value)
    assert "refs/heads/topic" in message
    assert "refs/heads/origin/topic" in message
    assert "refs/remotes/origin/topic" in message
    assert repo.snapshot().branch == "main"
    assert repo.snapshot().head == head


@pytest.mark.usefixtures("overlapping_remotes")
def test_remote_checkout_rejects_same_tracking_namespace_from_another_remote(
    git_repo: Path, git_run
) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "update-ref", "refs/remotes/team/origin/topic", head)
    git_run(git_repo, "branch", "topic", head)
    git_run(git_repo, "config", "branch.topic.remote", "team")
    git_run(git_repo, "config", "branch.topic.merge", "refs/heads/origin/topic")
    assert git_run(
        git_repo, "rev-parse", "--symbolic-full-name", "topic@{upstream}"
    ).stdout.strip() == ("refs/remotes/team/origin/topic")
    repo = Repository(git_repo)
    with pytest.raises(GitError, match="via remote team"):
        repo.execute(
            Action("checkout", target="refs/remotes/team/origin/topic", remote="team/origin")
        )
    assert repo.snapshot().branch == "main"
    assert repo.snapshot().head == head


@pytest.mark.usefixtures("overlapping_remotes")
def test_remote_checkout_rejects_mismatched_explicit_owner(git_repo: Path, git_run) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "update-ref", "refs/remotes/team/origin/topic", "HEAD")
    repo = Repository(git_repo)

    with pytest.raises(GitError, match="owned by team/origin"):
        repo.execute(
            Action(
                "checkout",
                target="refs/remotes/team/origin/topic",
                remote="team",
            )
        )
    assert repo.snapshot().branch == "main"
    assert repo.snapshot().head == head


def test_explicit_remote_checkout_is_not_shadowed_by_local_branch_names(
    git_repo: Path, git_run
) -> None:
    commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "update-ref", "refs/remotes/origin/topic", "HEAD")
    git_run(git_repo, "branch", "origin/topic", "HEAD")
    repo = Repository(git_repo)

    repo.execute(Action("checkout", target="origin/topic", remote="origin"))
    assert repo.snapshot().branch == "topic"
    repo.execute(Action("checkout", target="refs/heads/main"))

    # Git permits this unusual local branch. A fully qualified remote ref must
    # still select refs/remotes rather than refs/heads/refs/remotes.
    git_run(git_repo, "branch", "refs/remotes/origin/topic", "HEAD")
    repo.execute(Action("checkout", target="refs/remotes/origin/topic"))
    assert repo.snapshot().branch == "topic"


def test_remote_checkout_config_lock_fails_before_changing_head_or_refs(
    git_repo: Path, git_run
) -> None:
    head = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "update-ref", "refs/remotes/origin/topic", head)
    lock = git_repo / ".git" / "config.lock"
    lock.write_text("")
    repo = Repository(git_repo)
    action = Action("checkout", target="refs/remotes/origin/topic", remote="origin")
    with pytest.raises(GitError, match="config"):
        repo.execute(action)
    assert repo.snapshot().branch == "main"
    assert repo.snapshot().head == head
    assert (
        git_run(
            git_repo, "show-ref", "--verify", "--quiet", "refs/heads/topic", check=False
        ).returncode
        != 0
    )
    lock.unlink()
    repo.execute(action)
    assert repo.snapshot().branch == "topic"
    assert git_run(
        git_repo, "rev-parse", "--symbolic-full-name", "topic@{upstream}"
    ).stdout.strip() == ("refs/remotes/origin/topic")


def test_remote_checkout_partial_config_failure_is_retryable_without_switching(
    git_repo: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "update-ref", "refs/remotes/origin/topic", "HEAD")
    repo = Repository(git_repo)
    original = repo._git

    def fail_merge(args, **kwargs):
        if kwargs.get("operation") == "configure checkout upstream":
            raise GitError("Simulated second config write failure")
        return original(args, **kwargs)

    monkeypatch.setattr(repo, "_git", fail_merge)
    action = Action("checkout", target="refs/remotes/origin/topic", remote="origin")
    with pytest.raises(GitError, match="Simulated"):
        repo.execute(action)
    assert repo.snapshot().branch == "main"
    assert (
        git_run(
            git_repo, "show-ref", "--verify", "--quiet", "refs/heads/topic", check=False
        ).returncode
        != 0
    )
    monkeypatch.setattr(repo, "_git", original)
    repo.execute(action)
    assert repo.snapshot().branch == "topic"
    assert git_run(git_repo, "config", "branch.topic.remote").stdout.strip() == "origin"
    assert git_run(git_repo, "config", "branch.topic.merge").stdout.strip() == "refs/heads/topic"


def test_remote_checkout_preserves_dirty_worktree_on_switch_failure(
    git_repo: Path, git_run
) -> None:
    base = commit_file(git_run, git_repo, "tracked.txt", "base\n", "base")
    git_run(git_repo, "switch", "-c", "remote-source")
    remote_head = commit_file(git_run, git_repo, "tracked.txt", "remote\n", "remote")
    git_run(git_repo, "switch", "main")
    git_run(git_repo, "remote", "add", "origin", str(git_repo.parent / "origin.git"))
    git_run(git_repo, "update-ref", "refs/remotes/origin/topic", remote_head)
    (git_repo / "tracked.txt").write_text("dirty\n")
    repo = Repository(git_repo)

    with pytest.raises(GitError, match="local changes"):
        repo.execute(
            Action(
                "checkout",
                target="refs/remotes/origin/topic",
                remote="origin",
            )
        )
    assert repo.snapshot().branch == "main"
    assert repo.snapshot().head == base
    assert (git_repo / "tracked.txt").read_text() == "dirty\n"


def test_local_remote_push_tracking_clone_checkout_and_pull(
    git_repo: Path, bare_remote: Path, git_run, tmp_path: Path
) -> None:
    commit_file(git_run, git_repo, "shared.txt", "one\n", "one")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    source = Repository(git_repo)
    source.execute(Action("push"))
    first_snapshot = source.snapshot()
    assert first_snapshot.upstream == "origin/main"
    assert first_snapshot.remotes[0].name == "origin"

    clone_path = tmp_path / "clone"
    assert Repository.clone(str(bare_remote), str(clone_path)) == clone_path.resolve()
    clone = Repository(clone_path)
    assert clone.snapshot().branch == "main"

    commit_file(git_run, git_repo, "shared.txt", "two\n", "two")
    source.execute(Action("push"))
    clone.execute(Action("pull"))
    assert (clone_path / "shared.txt").read_text() == "two\n"

    git_run(git_repo, "switch", "-c", "remote-topic")
    commit_file(git_run, git_repo, "topic.txt", "topic\n", "topic")
    source.execute(Action("push", remote="origin"))
    clone.execute(Action("fetch", remote="origin"))
    clone.execute(Action("checkout", target="origin/remote-topic"))
    clone_snapshot = clone.snapshot()
    assert clone_snapshot.branch == "remote-topic"
    assert clone_snapshot.upstream == "origin/remote-topic"


def test_remote_redaction_and_error_validation(
    git_repo: Path, git_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_url = "https://alice:supersecret@example.invalid/org/repo.git?token=querysecret"
    git_run(git_repo, "remote", "add", "origin", secret_url)
    remote = Repository(git_repo).snapshot().remotes[0]
    assert remote.url == "https://example.invalid/org/repo.git"
    assert "supersecret" not in repr(remote)
    assert "querysecret" not in repr(remote)

    git_run(
        git_repo,
        "remote",
        "add",
        "ssh-normal",
        "git@github.com:org/repo.git?token=querysecret",
    )
    git_run(
        git_repo,
        "remote",
        "add",
        "ssh-secret",
        "access-token@example.invalid:org/repo.git?token=querysecret",
    )
    remotes = {item.name: item for item in Repository(git_repo).snapshot().remotes}
    assert remotes["ssh-normal"].url == "git@github.com:org/repo.git"
    assert remotes["ssh-secret"].url == "example.invalid:org/repo.git"
    error = str(
        GitError(
            "failed access-token@example.invalid:org/repo.git?token=querysecret and "
            "git@github.com:org/repo.git?password=hidden"
        )
    )
    assert "access-token" not in error
    assert "querysecret" not in error
    assert "hidden" not in error
    assert "git@github.com:org/repo.git" in error

    repo = Repository(git_repo)
    (git_repo / "untracked").write_text("safe\n")
    with pytest.raises(GitError):
        repo.execute(Action("stage", paths=("../outside",)))
    with pytest.raises(GitError):
        repo.execute(Action("checkout", target="--help"))
    with pytest.raises(GitError):
        repo.execute(Action("definitely-not-allowed", target="status"))
    assert (git_repo / "untracked").read_text() == "safe\n"
    assert not (tmp_path / "outside").exists()

    # Exercise a real transport error, but only against a closed loopback port.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "https")
    with pytest.raises(GitError) as caught:
        Repository.clone(
            "https://alice:supersecret@127.0.0.1:1/repo.git?token=querysecret",
            str(tmp_path / "failed-clone"),
        )
    assert "supersecret" not in str(caught.value)
    assert "querysecret" not in str(caught.value)
    assert "https://127.0.0.1:1/repo.git" in str(caught.value)


def test_repository_environment_cannot_redirect_selected_worktree(
    git_repo: Path, bare_remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_DIR", str(bare_remote))
    monkeypatch.setenv("GIT_WORK_TREE", str(bare_remote.parent / "elsewhere"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(bare_remote.parent / "outside-index"))
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -F custom.conf -o BatchMode=no")
    monkeypatch.setenv("GIT_ASKPASS", "/tmp/interactive-git-askpass")
    monkeypatch.setenv("SSH_ASKPASS", "/tmp/interactive-ssh-askpass")

    assert Repository(git_repo).root == git_repo.resolve()
    environment = _git_environment()
    assert "GIT_DIR" not in environment
    assert "GIT_WORK_TREE" not in environment
    assert "GIT_INDEX_FILE" not in environment
    assert environment["GIT_SSH_COMMAND"].startswith("ssh -oBatchMode=yes")
    assert "BatchMode=no" not in environment["GIT_SSH_COMMAND"]
    assert environment["GIT_ASKPASS"] == "/usr/bin/false"
    assert environment["SSH_ASKPASS"] == "/usr/bin/false"

    monkeypatch.delenv("GIT_SSH_COMMAND")
    assert "GIT_SSH_COMMAND" not in _git_environment()


def test_network_ssh_preserves_repo_command_and_environment_precedence(
    git_repo: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The SSH executables below are local recording stubs, not network clients.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "ssh")
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    config_capture = git_repo.parent / "config-ssh-args"
    env_capture = git_repo.parent / "env-ssh-args"
    config_script = git_repo.parent / "config-ssh"
    env_script = git_repo.parent / "env-ssh"

    def write_fake_ssh(script: Path, capture: Path) -> None:
        script.write_text(
            "#!/bin/sh\n"
            f"printf 'CALL\\n' >> {shlex.quote(str(capture))}\n"
            f"printf 'ARG=%s\\n' \"$@\" >> {shlex.quote(str(capture))}\n"
            "exit 1\n"
        )
        script.chmod(0o755)

    write_fake_ssh(config_script, config_capture)
    write_fake_ssh(env_script, env_capture)
    config_key = git_repo.parent / "configured key"
    env_key = git_repo.parent / "environment key"
    git_run(git_repo, "remote", "add", "origin", "git@fake.invalid:org/repo.git")
    git_run(git_repo, "config", "ssh.variant", "ssh")
    git_run(
        git_repo,
        "config",
        "core.sshCommand",
        f"{shlex.quote(str(config_script))} -i {shlex.quote(str(config_key))}",
    )
    repo = Repository(git_repo)

    with pytest.raises(GitError):
        repo.execute(Action("fetch", remote="origin"))
    configured_args = config_capture.read_text()
    assert "ARG=-oBatchMode=yes" in configured_args
    assert "ARG=-i" in configured_args
    assert f"ARG={config_key}" in configured_args

    monkeypatch.setenv(
        "GIT_SSH_COMMAND",
        f"{shlex.quote(str(env_script))} -i {shlex.quote(str(env_key))} -oBatchMode=no",
    )
    with pytest.raises(GitError):
        repo.execute(Action("fetch", remote="origin"))
    environment_args = env_capture.read_text()
    assert "ARG=-oBatchMode=yes" in environment_args
    assert "ARG=-i" in environment_args
    assert f"ARG={env_key}" in environment_args
    assert "BatchMode=no" not in environment_args
    assert config_capture.read_text() == configured_args


def test_push_url_is_visible_and_multiple_destinations_are_rejected(
    git_repo: Path, bare_remote: Path, git_run, tmp_path: Path
) -> None:
    commit_file(git_run, git_repo, "file.txt", "content\n", "base")
    push_one = tmp_path / "push-one.git"
    push_two = tmp_path / "push-two.git"
    git_run(tmp_path, "init", "--bare", str(push_one))
    git_run(tmp_path, "init", "--bare", str(push_two))
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "remote", "set-url", "--add", "--push", "origin", str(push_one))

    repo = Repository(git_repo)
    remote = repo.snapshot().remotes[0]
    assert remote.push_url == str(push_one)

    git_run(git_repo, "remote", "set-url", "--add", "--push", "origin", str(push_two))
    with pytest.raises(GitError, match="multiple push destinations"):
        repo.execute(Action("push", remote="origin"))
    for destination in (push_one, push_two):
        result = git_run(destination, "rev-parse", "--verify", "refs/heads/main", check=False)
        assert result.returncode != 0


def test_push_ignores_hostile_widening_configuration(
    git_repo: Path, bare_remote: Path, git_run
) -> None:
    base = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    repo = Repository(git_repo)
    repo.execute(Action("push"))

    git_run(git_repo, "switch", "-c", "victim")
    victim_remote = commit_file(git_run, git_repo, "victim.txt", "remote\n", "victim")
    repo.execute(Action("push", remote="origin"))
    git_run(git_repo, "switch", "main")
    git_run(git_repo, "branch", "-f", "victim", base)
    main = commit_file(git_run, git_repo, "main.txt", "main\n", "main")
    git_run(git_repo, "tag", "must-not-follow")

    git_run(git_repo, "config", "push.default", "matching")
    git_run(
        git_repo,
        "config",
        "--add",
        "remote.origin.push",
        "+refs/heads/*:refs/heads/*",
    )
    git_run(git_repo, "config", "remote.origin.mirror", "true")
    git_run(git_repo, "config", "push.followTags", "true")

    repo.execute(Action("push"))
    assert git_run(bare_remote, "rev-parse", "refs/heads/main").stdout.strip() == main
    assert git_run(bare_remote, "rev-parse", "refs/heads/victim").stdout.strip() == victim_remote
    tag = git_run(bare_remote, "rev-parse", "--verify", "refs/tags/must-not-follow", check=False)
    assert tag.returncode != 0


def test_ambiguous_push_requires_remote(git_repo: Path, git_run, tmp_path: Path) -> None:
    commit_file(git_run, git_repo, "file.txt", "content\n", "base")
    for name in ("one", "two"):
        remote = tmp_path / f"{name}.git"
        git_run(tmp_path, "init", "--bare", str(remote))
        git_run(git_repo, "remote", "add", name, str(remote))
    with pytest.raises(GitError, match="More than one remote"):
        Repository(git_repo).execute(Action("push"))
