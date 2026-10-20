from __future__ import annotations

import os
import shlex
from pathlib import Path

import pytest

from gitkraiken.git import GitError, Repository, _git_environment
from gitkraiken.models import Action


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


def test_stash_confirmation_does_not_drop_a_newly_inserted_stash(git_repo, git_run):
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
    assert snapshot.history[0].graph.startswith("*")

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


def test_native_merge_graph_has_real_topology(git_repo: Path, git_run) -> None:
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
    assert any(line.commit is None for line in snapshot.history)
    assert any("|" in line.graph or "\\" in line.graph for line in snapshot.history)


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
    assert "[diff truncated by GitKraiken]" in patch
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


def test_remote_redaction_and_error_validation(git_repo: Path, git_run, tmp_path: Path) -> None:
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
