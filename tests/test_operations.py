from __future__ import annotations

import os
import secrets
import shlex
import subprocess
import threading
import time
from dataclasses import FrozenInstanceError, replace

import pytest

import rhizome.git as git_module
import rhizome.recovery as recovery_module
from rhizome.git import GitError, Repository
from rhizome.models import Action
from rhizome.operations import OperationCoordinator
from rhizome.patches import PatchService
from rhizome.recovery import RecoveryJournal


def _change_range(document, *needles: str) -> tuple[int, int]:
    selected = [
        line.id
        for line in document.lines
        if line.kind in {"add", "delete"} and any(needle in line.text for needle in needles)
    ]
    assert selected
    return min(selected), max(selected)


@pytest.mark.parametrize("change", ("intent-to-add", "signing-configuration"))
def test_commit_review_identity_binds_index_flags_and_effective_configuration(
    git_repo, git_run, monkeypatch, change
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    empty = git_repo / "empty.txt"
    empty.write_bytes(b"")
    git_run(git_repo, "add", path.name, empty.name)
    operations = OperationCoordinator(Repository(git_repo))
    action = Action("commit", message="Captured review identity")
    # Control only external entropy: equal public plan IDs then expose a missing
    # reviewed-state component without inspecting the coordinator's internals or
    # bypassing its temporary submission gate.
    monkeypatch.setattr(secrets, "token_bytes", lambda size: b"\x01" * size)
    before = operations.plan(action)
    assert operations.plan(action).fingerprint == before.fingerprint
    entries = git_run(git_repo, "ls-files", "--stage", "-z").stdout
    if change == "intent-to-add":
        git_run(git_repo, "reset", "--", empty.name)
        git_run(git_repo, "add", "--intent-to-add", empty.name)
    else:
        git_run(git_repo, "config", "commit.gpgsign", "true")
    assert git_run(git_repo, "ls-files", "--stage", "-z").stdout == entries
    assert operations.plan(action).fingerprint != before.fingerprint


def test_commit_excludes_an_external_index_writer_after_review(git_repo, git_run, monkeypatch):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    git_run(git_repo, "add", "example.txt")
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("commit", message="Commit the reviewed index"))
    run = subprocess.run
    popen = subprocess.Popen
    competing = []

    def external_writer(command, *args, **kwargs):
        if "commit" in command and not competing:
            path.write_text("changed after review\n")
            competing.append(
                run(
                    ["git", "-C", str(git_repo), "add", "example.txt"],
                    capture_output=True,
                )
            )
        return popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", external_writer)
    operations.submit(plan)
    assert len(competing) == 1
    assert competing[0].returncode != 0
    assert b"index.lock" in competing[0].stderr
    assert git_run(git_repo, "show", "HEAD:example.txt").stdout == "reviewed\n"
    assert path.read_text() == "changed after review\n"


def test_checkout_attribute_review_does_not_require_newer_git_source_flag(
    git_repo, git_run, monkeypatch
):
    (git_repo / "example.txt").write_text("main\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "main")
    git_run(git_repo, "switch", "-c", "topic")
    (git_repo / "example.txt").write_text("topic\n")
    (git_repo / ".gitattributes").write_text("example.txt text eol=lf\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "topic")
    git_run(git_repo, "switch", "main")
    run = subprocess.run

    def minimum_version_flags(command, *args, **kwargs):
        # Exercise real Git, while refusing a flag absent from the Git 2.36 API.
        if "check-attr" in command and any(str(arg).startswith("--source") for arg in command):
            return subprocess.CompletedProcess(command, 129, b"", b"unknown option: source")
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", minimum_version_flags)
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("checkout", target="refs/heads/topic"))
    operations.submit(plan)

    assert git_run(git_repo, "branch", "--show-current").stdout.strip() == "topic"
    assert (git_repo / "example.txt").read_text() == "topic\n"


def test_plan_is_an_immutable_explanation_of_exact_user_intent(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    operations = OperationCoordinator(Repository(git_repo), protected_branches=("main",))

    plan = operations.plan(Action("checkout", target="HEAD"))

    assert plan.action == Action("checkout", target="HEAD")
    assert plan.repository == str(git_repo)
    assert plan.available is True
    assert plan.reason == ""
    assert plan.requires_confirmation is False
    assert plan.consequences
    with pytest.raises(FrozenInstanceError):
        plan.available = False


def test_plan_cannot_be_tampered_with_or_replayed(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_text("content\n")
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("stage", paths=("example.txt",)))

    with pytest.raises(GitError, match="changed after review"):
        operations.submit(replace(plan, action=Action("stage")))
    with pytest.raises(GitError, match="already submitted"):
        operations.submit(plan)

    assert git_run(git_repo, "status", "--short").stdout == "?? example.txt\n"


def test_routine_stage_unstage_and_commit_have_exact_real_git_effects(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_text("original\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("reviewed\n")
    operations = OperationCoordinator(Repository(git_repo))

    operations.submit(operations.plan(Action("stage", paths=("example.txt",))))
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"

    operations.submit(operations.plan(Action("unstage", paths=("example.txt",))))
    assert git_run(git_repo, "show", ":example.txt").stdout == "original\n"
    assert file.read_text() == "reviewed\n"

    operations.submit(operations.plan(Action("stage", paths=("example.txt",))))
    operations.submit(operations.plan(Action("commit", message="reviewed commit")))
    assert git_run(git_repo, "show", "HEAD:example.txt").stdout == "reviewed\n"
    assert git_run(git_repo, "log", "-1", "--format=%s").stdout.strip() == "reviewed commit"


def test_precision_stage_and_unstage_use_coordinator_with_exact_index_effects(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_text("a\nb\nc\nd\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("a\nB\nc\nD\n")
    repo = Repository(git_repo)
    patches = PatchService(repo)
    operations = OperationCoordinator(repo)

    stage_document = patches.open("example.txt", context=0)
    stage_selection = _change_range(stage_document, "b", "B")
    stage_plan = operations.plan(
        Action("stage", paths=("example.txt",)),
        patch=stage_document,
        selection=stage_selection,
    )
    assert f"{stage_selection[0]}–{stage_selection[1]}" in stage_plan.consequences[0]
    operations.submit(stage_plan)

    assert git_run(git_repo, "show", ":example.txt").stdout == "a\nB\nc\nd\n"
    assert file.read_text() == "a\nB\nc\nD\n"
    assert operations.activity()[0].state == "succeeded"

    git_run(git_repo, "add", "example.txt")
    unstage_document = patches.open("example.txt", staged=True, context=0)
    unstage_selection = _change_range(unstage_document, "b", "B")
    unstage_plan = operations.plan(
        Action("unstage", paths=("example.txt",)),
        patch=unstage_document,
        selection=unstage_selection,
    )
    operations.submit(unstage_plan)

    assert git_run(git_repo, "show", ":example.txt").stdout == "a\nb\nc\nD\n"
    assert file.read_text() == "a\nB\nc\nD\n"
    assert operations.activity()[0].state == "succeeded"


def test_precision_publication_durability_failure_is_uncertain(git_repo, git_run, monkeypatch):
    path = git_repo / "example.txt"
    path.write_text("old\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    repo = Repository(git_repo)
    document = PatchService(repo).open("example.txt")
    coordinator = OperationCoordinator(repo)
    plan = coordinator.plan(
        Action("stage", paths=("example.txt",)),
        patch=document,
        selection=_change_range(document, "old", "reviewed"),
    )
    git_dir = (git_repo / ".git").stat()
    sync = os.fsync
    failures = []

    def fail_publication_sync(descriptor):
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) == (git_dir.st_dev, git_dir.st_ino):
            failures.append(True)
            raise OSError("simulated directory durability failure")
        return sync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_publication_sync)
    with pytest.raises(GitError, match="Index replaced"):
        coordinator.submit(plan)

    assert failures
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert path.read_text() == "reviewed\n"
    assert coordinator.activity()[0].state == "uncertain"
    with pytest.raises(GitError, match="already submitted"):
        coordinator.submit(plan)


def test_precision_stage_rejects_stale_original_document(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_text("old\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("reviewed\n")
    repo = Repository(git_repo)
    document = PatchService(repo).open("example.txt", context=0)
    selection = _change_range(document, "old", "reviewed")
    file.write_text("newer\n")
    operations = OperationCoordinator(repo)
    plan = operations.plan(
        Action("stage", paths=("example.txt",)),
        patch=document,
        selection=selection,
    )

    with pytest.raises(GitError, match="changed"):
        operations.submit(plan)

    assert git_run(git_repo, "show", ":example.txt").stdout == "old\n"
    assert file.read_text() == "newer\n"
    assert operations.activity()[0].state == "failed"


def test_precision_plan_rejects_mismatched_patch_intent(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_text("old\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("new\n")
    document = PatchService(Repository(git_repo)).open("example.txt", context=0)
    selection = _change_range(document, "old", "new")
    operations = OperationCoordinator(Repository(git_repo))

    with pytest.raises(GitError, match="exactly match"):
        operations.plan(Action("stage", paths=("other.txt",)), patch=document, selection=selection)
    with pytest.raises(GitError, match="staged patch"):
        operations.plan(
            Action("unstage", paths=("example.txt",)), patch=document, selection=selection
        )
    with pytest.raises(GitError, match="only valid"):
        operations.plan(Action("commit", message="no"), patch=document, selection=selection)


def test_unstage_plan_refuses_index_edits_after_review(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_text("original\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("reviewed\n")
    git_run(git_repo, "add", "example.txt")
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("unstage", paths=("example.txt",)))

    file.write_text("different\n")
    git_run(git_repo, "add", "example.txt")
    with pytest.raises(GitError, match="changed"):
        operations.submit(plan)

    assert git_run(git_repo, "show", ":example.txt").stdout == "different\n"


def test_stage_plan_refuses_worktree_edits_after_review(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_text("original\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("reviewed\n")
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("stage", paths=("example.txt",)))

    file.write_text("not reviewed\n")
    with pytest.raises(GitError, match="changed"):
        operations.submit(plan)

    assert git_run(git_repo, "show", ":example.txt").stdout == "original\n"
    assert file.read_text() == "not reviewed\n"


def test_stage_plan_refuses_effective_included_conversion_config_change(
    git_repo, git_run, tmp_path, monkeypatch
):
    file = git_repo / "example.txt"
    file.write_bytes(b"original\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_bytes(b"reviewed\r\n")

    home = tmp_path / "home"
    home.mkdir()
    included = home / "conversion.conf"
    included.write_text("[core]\n\tautocrlf = false\n")
    (home / ".gitconfig").write_text(f"[include]\n\tpath = {included}\n")
    monkeypatch.setenv("HOME", str(home))

    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("stage", paths=("example.txt",)))
    included.write_text("[core]\n\tautocrlf = true\n")

    with pytest.raises(GitError, match="unsupported|changed"):
        operations.submit(plan)

    assert git_run(git_repo, "show", ":example.txt").stdout == "original\n"
    assert file.read_bytes() == b"reviewed\r\n"


def test_stage_plan_fails_closed_for_existing_content_conversion(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_bytes(b"original\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_bytes(b"reviewed\r\n")
    git_run(git_repo, "config", "core.autocrlf", "true")
    operations = OperationCoordinator(Repository(git_repo))

    with pytest.raises(GitError, match="core.autocrlf conversion is unsupported"):
        operations.plan(Action("stage", paths=("example.txt",)))

    assert git_run(git_repo, "show", ":example.txt").stdout == "original\n"
    assert file.read_bytes() == b"reviewed\r\n"


def test_stage_plan_refuses_new_conversion_attributes_for_expanded_paths(git_repo, git_run):
    directory = git_repo / "nested"
    directory.mkdir()
    file = directory / "example.txt"
    file.write_text("original\n")
    git_run(git_repo, "add", "nested/example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("reviewed\n")
    git_run(git_repo, "config", "filter.review.clean", "tr a-z A-Z")
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("stage", paths=("nested",)))

    (git_repo / ".gitattributes").write_text("nested/*.txt filter=review\n")
    with pytest.raises(GitError, match="conversion|changed"):
        operations.submit(plan)

    assert git_run(git_repo, "show", ":nested/example.txt").stdout == "original\n"
    assert file.read_text() == "reviewed\n"


def test_discard_with_confirmation_saves_recoverable_preimage_before_mutation(
    git_repo, git_run, tmp_path
):
    file = git_repo / "example.txt"
    file.write_text("original\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("precious work\n")
    journal = RecoveryJournal(tmp_path / "operation-journal")
    operations = OperationCoordinator(Repository(git_repo), journal=journal)
    plan = operations.plan(Action("discard", paths=("example.txt",)))

    assert plan.available is True
    assert plan.requires_confirmation is True
    assert journal.list() == ()
    with pytest.raises(GitError, match="explicit confirmation"):
        operations.submit(plan)
    operations.submit(plan, confirmed=True)

    assert file.read_text() == "original\n"
    records = operations.activity()
    assert records[0].state == "succeeded"
    assert records[0].checkpoint_id
    with pytest.raises(FrozenInstanceError):
        records[0].state = "hidden"
    checkpoint = journal.list(str(git_repo))[0]
    assert checkpoint.id == records[0].checkpoint_id
    assert checkpoint.active is False
    recovered = journal.recover_aside(Repository(git_repo), checkpoint.id)
    assert recovered.directory is not None
    assert (recovered.directory / "worktree" / "example.txt").read_text() == "precious work\n"
    assert (recovered.directory / "index" / "example.txt").read_text() == "original\n"


def test_checkpoint_completion_failure_after_discard_remains_uncertain_and_active(
    git_repo, git_run, tmp_path, monkeypatch
):
    file = git_repo / "example.txt"
    file.write_text("original\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("precious work\n")
    journal = RecoveryJournal(tmp_path / "operation-journal")
    operations = OperationCoordinator(Repository(git_repo), journal=journal)
    plan = operations.plan(Action("discard", paths=("example.txt",)))

    replace = recovery_module.os.replace

    def fail_checkpoint_completion(source, destination, *args, **kwargs):
        if recovery_module.Path(destination).name == "checkpoint.json":
            raise OSError("injected checkpoint metadata failure")
        return replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(recovery_module.os, "replace", fail_checkpoint_completion)
    with pytest.raises(GitError, match="Treat the result as uncertain"):
        operations.submit(plan, confirmed=True)
    monkeypatch.setattr(recovery_module.os, "replace", replace)

    assert file.read_text() == "original\n"
    activity = operations.activity()[0]
    assert activity.state == "uncertain"
    assert activity.checkpoint_id
    checkpoint = journal.list(str(git_repo))[0]
    assert checkpoint.id == activity.checkpoint_id
    assert checkpoint.active is True


def test_discard_stops_when_checkpoint_cannot_cover_tracked_symlink(git_repo, git_run, tmp_path):
    target = git_repo / "target.txt"
    target.write_text("outside tracked symlink\n")
    link = git_repo / "link.txt"
    link.symlink_to("target.txt")
    git_run(git_repo, "add", "link.txt")
    git_run(git_repo, "commit", "-m", "tracked symlink")
    link.unlink()
    link.symlink_to("other.txt")
    journal = RecoveryJournal(tmp_path / "operation-journal")
    operations = OperationCoordinator(Repository(git_repo), journal=journal)
    plan = operations.plan(Action("discard", paths=("link.txt",)))

    with pytest.raises(GitError, match="checkpoint failed"):
        operations.submit(plan, confirmed=True)

    assert str(link.readlink()) == "other.txt"
    assert journal.list() == ()
    assert operations.activity()[0].state == "failed"


def test_discard_without_recovery_is_unavailable_even_if_confirmed(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_text("original\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("precious work\n")
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("discard", paths=("example.txt",)))

    with pytest.raises(GitError, match="checkpoint"):
        operations.submit(plan, confirmed=True)

    assert file.read_text() == "precious work\n"
    assert git_run(git_repo, "show", ":example.txt").stdout == "original\n"


def test_fetch_set_upstream_and_non_force_push_use_exact_local_remote(
    git_repo, git_run, bare_remote
):
    file = git_repo / "example.txt"
    file.write_text("one\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "one")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "origin", "refs/heads/main:refs/heads/main")
    git_run(git_repo, "fetch", "origin")
    operations = OperationCoordinator(Repository(git_repo))

    upstream_plan = operations.plan(
        Action(
            "set-upstream",
            target="refs/heads/main",
            name="refs/remotes/origin/main",
            remote="origin",
        )
    )
    assert "origin/main" in upstream_plan.consequences[0]
    operations.submit(upstream_plan)
    assert (
        git_run(git_repo, "rev-parse", "--abbrev-ref", "@{upstream}").stdout.strip()
        == "origin/main"
    )

    remote_commit = git_run(
        bare_remote,
        "commit-tree",
        "main^{tree}",
        "-p",
        "main",
        "-m",
        "remote",
    ).stdout.strip()
    git_run(bare_remote, "update-ref", "refs/heads/main", remote_commit)
    operations.submit(operations.plan(Action("fetch", remote="origin")))
    assert (
        git_run(git_repo, "rev-parse", "refs/remotes/origin/main").stdout.strip() == remote_commit
    )

    git_run(git_repo, "merge", "--ff-only", "refs/remotes/origin/main")
    file.write_text("two\n")
    git_run(git_repo, "commit", "-am", "two")
    local_head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    push_plan = operations.plan(Action("push"))
    assert "Non-force push" in push_plan.consequences[0]
    operations.submit(push_plan)
    assert git_run(bare_remote, "rev-parse", "refs/heads/main").stdout.strip() == local_head


def test_push_revalidation_failure_is_failed_not_remote_uncertainty(
    git_repo, git_run, bare_remote, tmp_path
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("push", remote="origin"))

    replacement = tmp_path / "other.git"
    replacement.mkdir()
    git_run(replacement, "init", "--bare", "-b", "main")
    git_run(git_repo, "remote", "set-url", "origin", str(replacement))
    with pytest.raises(GitError, match="configuration changed"):
        operations.submit(plan)

    assert operations.activity()[0].state == "failed"
    assert (
        git_run(bare_remote, "show-ref", "--verify", "refs/heads/main", check=False).returncode != 0
    )
    assert (
        git_run(replacement, "show-ref", "--verify", "refs/heads/main", check=False).returncode != 0
    )


def test_timed_out_push_is_visible_as_uncertain_and_is_not_retried(
    git_repo, git_run, tmp_path, monkeypatch
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    attempts = tmp_path / "ssh-attempts"
    ssh = tmp_path / "slow-ssh"
    ssh.write_text(f"#!/bin/sh\necho attempt >> {shlex.quote(str(attempts))}\nsleep 2\nexit 1\n")
    ssh.chmod(0o700)
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "ssh")
    monkeypatch.setenv("GIT_SSH_COMMAND", str(ssh))
    monkeypatch.setattr(git_module, "_NETWORK_TIMEOUT", 0.1)
    git_run(git_repo, "remote", "add", "origin", "ssh://git@example.invalid/repo")
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("push", remote="origin"))

    with pytest.raises(GitError, match="timed out"):
        operations.submit(plan)

    activity = operations.activity()[0]
    assert activity.state == "uncertain"
    assert "Inspect the destination before retrying" in activity.message
    deadline = time.monotonic() + 1
    while not attempts.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert attempts.read_text().splitlines() == ["attempt"]
    with pytest.raises(GitError, match="already submitted"):
        operations.submit(plan)


def test_checkout_branch_tag_and_worktree_creation_have_exact_targets(git_repo, git_run, tmp_path):
    file = git_repo / "example.txt"
    file.write_text("one\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "one")
    first = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    file.write_text("two\n")
    git_run(git_repo, "commit", "-am", "two")
    operations = OperationCoordinator(Repository(git_repo))

    operations.submit(operations.plan(Action("checkout", target=first)))
    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == first
    assert git_run(git_repo, "branch", "--show-current").stdout.strip() == ""

    operations.submit(operations.plan(Action("create-branch", name="reviewed", target="HEAD")))
    assert git_run(git_repo, "branch", "--show-current").stdout.strip() == "reviewed"
    operations.submit(operations.plan(Action("tag", name="reviewed-tag", target="HEAD")))
    assert git_run(git_repo, "rev-parse", "reviewed-tag^{}").stdout.strip() == first

    destination = tmp_path / "review-worktree"
    operations.submit(
        operations.plan(Action("create-worktree", target="HEAD", paths=(str(destination),)))
    )
    assert git_run(destination, "rev-parse", "HEAD").stdout.strip() == first
    assert git_run(destination, "branch", "--show-current").stdout.strip() == ""


def test_tag_plan_refuses_a_moved_explicit_target(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    first = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "branch", "topic")
    later = git_run(
        git_repo, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "later"
    ).stdout.strip()
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("tag", name="reviewed-tag", target="refs/heads/topic"))

    git_run(git_repo, "update-ref", "refs/heads/topic", later)
    with pytest.raises(GitError, match="changed"):
        operations.submit(plan)

    assert (
        git_run(
            git_repo,
            "show-ref",
            "--verify",
            "refs/tags/reviewed-tag",
            check=False,
        ).returncode
        != 0
    )
    assert git_run(git_repo, "rev-parse", "refs/heads/topic").stdout.strip() != first


def test_checkout_plan_refuses_changed_tracking_destination(git_repo, git_run, bare_remote):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "origin", "refs/heads/main:refs/heads/main")
    git_run(git_repo, "fetch", "origin")
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(
        Action(
            "checkout",
            target="refs/remotes/origin/main",
            remote="origin",
        )
    )

    git_run(
        git_repo,
        "config",
        "--replace-all",
        "remote.origin.fetch",
        "+refs/heads/*:refs/remotes/reviewed-elsewhere/*",
    )
    with pytest.raises(GitError, match="changed"):
        operations.submit(plan)

    assert git_run(git_repo, "branch", "--show-current").stdout.strip() == "main"


def test_checkout_plan_refuses_changed_external_target_attributes(git_repo, git_run, tmp_path):
    file = git_repo / "example.txt"
    file.write_text("one\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "one")
    first = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    file.write_text("two\n")
    git_run(git_repo, "commit", "-am", "two")
    second = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    attributes = tmp_path / "global-attributes"
    attributes.write_text("*.txt -text\n")
    git_run(git_repo, "config", "core.attributesFile", str(attributes))
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("checkout", target=first))

    attributes.write_text("*.txt text eol=crlf\n")
    with pytest.raises(GitError, match="changed"):
        operations.submit(plan)

    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == second
    assert file.read_bytes() == b"two\n"


def test_fetch_plan_refuses_included_global_destination_change(
    git_repo, git_run, bare_remote, tmp_path, monkeypatch
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "origin", "refs/heads/main:refs/heads/main")

    home = tmp_path / "home"
    home.mkdir()
    included = home / "fetch.conf"
    included.write_text(
        '[remote "origin"]\n\tfetch = +refs/heads/main:refs/remotes/reviewed/main\n'
    )
    (home / ".gitconfig").write_text(f"[include]\n\tpath = {included}\n")
    monkeypatch.setenv("HOME", str(home))
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("fetch", remote="origin"))

    included.write_text('[remote "origin"]\n\tfetch = +refs/heads/main:refs/remotes/changed/main\n')
    with pytest.raises(GitError, match="changed"):
        operations.submit(plan)

    assert (
        git_run(
            git_repo,
            "show-ref",
            "--verify",
            "refs/remotes/changed/main",
            check=False,
        ).returncode
        != 0
    )


def test_create_worktree_plan_refuses_destination_created_after_review(git_repo, git_run, tmp_path):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    destination = tmp_path / "new-worktree"
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("create-worktree", target="HEAD", paths=(str(destination),)))

    destination.mkdir()
    (destination / "precious.txt").write_text("do not overwrite\n")
    with pytest.raises(GitError, match="changed"):
        operations.submit(plan)

    assert (destination / "precious.txt").read_text() == "do not overwrite\n"
    assert len(git_run(git_repo, "worktree", "list", "--porcelain").stdout.split("worktree ")) == 2


def test_repository_identity_replacement_rejects_reviewed_plan(git_repo, git_run):
    file = git_repo / "example.txt"
    file.write_text("original\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    file.write_text("reviewed\n")
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("stage", paths=("example.txt",)))

    original = git_repo.with_name("original-worktree")
    git_repo.rename(original)
    git_repo.mkdir()
    git_run(git_repo, "init", "-b", "main")
    with pytest.raises(GitError, match="identity changed"):
        operations.submit(plan)

    assert git_run(original, "show", ":example.txt").stdout == "original\n"
    assert git_run(git_repo, "status", "--short").stdout == ""


def test_default_and_configured_refs_remain_protected_and_ref_changes_stay_gated(
    git_repo, git_run, tmp_path
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    git_run(git_repo, "branch", "topic")
    git_run(git_repo, "branch", "release")
    journal = RecoveryJournal(tmp_path / "operation-journal")
    operations = OperationCoordinator(
        Repository(git_repo), journal=journal, protected_branches=("release",)
    )

    default_delete = operations.plan(Action("delete-branch", target="main"))
    assert default_delete.available is False
    assert "protected" in default_delete.reason
    configured_delete = operations.plan(Action("delete-branch", target="release"))
    assert configured_delete.available is False
    assert "protected" in configured_delete.reason
    topic_rename = operations.plan(Action("rename-branch", target="topic", name="renamed-topic"))
    assert topic_rename.available is False
    assert "restoration" in topic_rename.reason
    protected_rewrite = operations.plan(Action("reset-soft", target="HEAD"))
    assert protected_rewrite.available is False
    assert "protected" in protected_rewrite.reason
    git_run(git_repo, "switch", "topic")
    hard_reset = operations.plan(Action("reset-hard", target="HEAD"))
    assert hard_reset.available is False
    assert "untracked" in hard_reset.reason

    with pytest.raises(GitError, match="protected"):
        operations.submit(default_delete, confirmed=True)
    assert git_run(git_repo, "show-ref", "--verify", "refs/heads/main").returncode == 0
    assert git_run(git_repo, "show-ref", "--verify", "refs/heads/topic").returncode == 0


def test_detected_remote_default_source_ref_is_protected(git_repo, git_run, tmp_path):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    remote = tmp_path / "production.git"
    remote.mkdir()
    git_run(remote, "init", "--bare", "-b", "production")
    git_run(git_repo, "remote", "add", "origin", str(remote))
    git_run(git_repo, "push", "origin", "refs/heads/main:refs/heads/production")
    git_run(git_repo, "fetch", "origin")
    git_run(git_repo, "remote", "set-head", "origin", "--auto")
    oid = git_run(git_repo, "rev-parse", "refs/remotes/origin/production").stdout.strip()
    operations = OperationCoordinator(Repository(git_repo))

    plan = operations.plan(
        Action(
            "delete-remote-branch",
            target="refs/remotes/origin/production",
            name="refs/heads/production",
            remote="origin",
            expected_oid=oid,
        )
    )

    assert plan.available is False
    assert "protected" in plan.reason
    with pytest.raises(GitError, match="protected"):
        operations.submit(plan, confirmed=True)
    assert git_run(remote, "rev-parse", "refs/heads/production").stdout.strip() == oid


def test_mutations_are_serialized_across_worktrees_of_one_common_repository(
    git_repo, git_run, tmp_path
):
    file = git_repo / "example.txt"
    file.write_text("one\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "initial")
    other = tmp_path / "other-worktree"
    git_run(git_repo, "worktree", "add", "-b", "other", str(other))

    file.write_text("two\n")
    git_run(git_repo, "add", "example.txt")
    first = OperationCoordinator(Repository(git_repo))
    second = OperationCoordinator(Repository(other))
    commit_plan = first.plan(Action("commit", message="blocked commit"))
    tag_plan = second.plan(Action("tag", name="queued-tag", target="HEAD"))

    entered = tmp_path / "hook-entered"
    release = tmp_path / "hook-release"
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"touch {shlex.quote(str(entered))}\n"
        f"while [ ! -e {shlex.quote(str(release))} ]; do sleep 0.02; done\n"
    )
    hook.chmod(0o700)
    errors: list[Exception] = []

    def submit(coordinator, plan):
        try:
            coordinator.submit(plan)
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    commit_thread = threading.Thread(target=submit, args=(first, commit_plan))
    commit_thread.start()
    deadline = time.monotonic() + 5
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert entered.exists()

    tag_thread = threading.Thread(target=submit, args=(second, tag_plan))
    tag_thread.start()
    time.sleep(0.1)
    assert second.activity()[0].state == "queued"
    assert (
        git_run(other, "show-ref", "--verify", "refs/tags/queued-tag", check=False).returncode != 0
    )

    release.touch()
    commit_thread.join(timeout=5)
    tag_thread.join(timeout=5)
    assert not commit_thread.is_alive()
    assert not tag_thread.is_alive()
    assert errors == []
    assert (
        git_run(other, "rev-parse", "queued-tag^{}").stdout.strip()
        == git_run(other, "rev-parse", "HEAD").stdout.strip()
    )


def test_activity_is_bounded_to_the_most_recent_twenty_real_operations(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    operations = OperationCoordinator(Repository(git_repo))

    for number in range(22):
        operations.submit(operations.plan(Action("tag", name=f"activity-{number}", target="HEAD")))

    records = operations.activity()
    assert len(records) == 20
    assert all(record.state == "succeeded" for record in records)
    assert records[0].message


def test_checkout_plan_refuses_a_moved_target_ref(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    original = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "branch", "topic")
    later = git_run(
        git_repo, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "later"
    ).stdout.strip()
    operations = OperationCoordinator(Repository(git_repo))
    plan = operations.plan(Action("checkout", target="refs/heads/topic"))

    git_run(git_repo, "update-ref", "refs/heads/topic", later)
    with pytest.raises(GitError, match="changed"):
        operations.submit(plan)

    assert git_run(git_repo, "branch", "--show-current").stdout.strip() == "main"
    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == original
