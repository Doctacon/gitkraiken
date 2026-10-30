from __future__ import annotations

from pathlib import Path

import pytest

from rhizome.git import GitError, GitOutcomeUncertain, Repository
from rhizome.models import Action
from rhizome.operations import OperationCoordinator
from rhizome.recovery import RecoveryJournal


def _commit(git_run, repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    git_run(repo, "add", "--", path)
    git_run(repo, "commit", "-m", message)
    return git_run(repo, "rev-parse", "HEAD").stdout.strip()


def _repositories(tmp_path: Path, git_run) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git_run(remote, "init", "--bare", "-b", "main")
    seed = tmp_path / "seed"
    seed.mkdir()
    git_run(seed, "init", "-b", "main")
    git_run(seed, "config", "user.name", "Test User")
    git_run(seed, "config", "user.email", "test@example.invalid")
    _commit(git_run, seed, "shared.txt", "base\n", "base")
    git_run(seed, "remote", "add", "origin", str(remote))
    git_run(seed, "push", "-u", "origin", "main")
    local = tmp_path / "local"
    git_run(tmp_path, "clone", str(remote), str(local))
    git_run(local, "config", "user.name", "Test User")
    git_run(local, "config", "user.email", "test@example.invalid")
    git_run(local, "config", "commit.gpgsign", "false")
    return remote, seed, local


def _advance_remote(git_run, seed: Path, path: str, content: str, message: str) -> str:
    oid = _commit(git_run, seed, path, content, message)
    git_run(seed, "push", "origin", "main")
    return oid


def _checkout_rebase_topic(git_run, local: Path) -> None:
    git_run(local, "switch", "-c", "topic")
    git_run(local, "branch", "--set-upstream-to=origin/main", "topic")


def test_real_coordinator_fast_forwards_exact_fetched_upstream(tmp_path: Path, git_run) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    target = _advance_remote(git_run, seed, "remote.txt", "incoming\n", "incoming")
    original = git_run(local, "rev-parse", "HEAD").stdout.strip()
    coordinator = OperationCoordinator(Repository(local))

    plan = coordinator.prepare_pull(
        Action(
            "pull",
            pull_mode="ff",
            expected_head=original,
            expected_branch="main",
            expected_ref="refs/heads/main",
            expected_oid=original,
        )
    )

    assert plan.kind == "fast-forward"
    assert plan.target_oid == target
    assert coordinator.submit_pull(plan) == f"Fast-forwarded main to {target[:12]}."
    assert git_run(local, "rev-parse", "HEAD").stdout.strip() == target
    assert git_run(local, "status", "--porcelain=v1").stdout == ""


def test_divergent_merge_executes_with_history_checkpoint(tmp_path: Path, git_run) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _commit(git_run, local, "local.txt", "local\n", "local")
    target = _advance_remote(git_run, seed, "remote.txt", "remote\n", "remote")
    journal = RecoveryJournal(tmp_path / "pull-journal")
    coordinator = OperationCoordinator(Repository(local), pull_journal=journal)

    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff"))
    assert plan.kind == "merge"
    result = coordinator.submit_pull(plan, confirmed=True)

    assert "by merge" in result
    head = git_run(local, "rev-parse", "HEAD").stdout.strip()
    parents = git_run(local, "show", "-s", "--format=%P", head).stdout.split()
    assert len(parents) == 2
    assert target in parents
    assert journal.list(str(local))[0].active is False


def test_rebase_rejects_local_head_published_at_known_remote_tip(tmp_path: Path, git_run) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _checkout_rebase_topic(git_run, local)
    local_head = _commit(git_run, local, "local.txt", "local\n", "local")
    _advance_remote(git_run, seed, "remote.txt", "remote\n", "remote")
    git_run(local, "update-ref", "refs/remotes/published/topic", local_head)
    coordinator = OperationCoordinator(
        Repository(local), pull_journal=RecoveryJournal(tmp_path / "pull-journal")
    )

    with pytest.raises(GitError, match="already reachable from a known remote tip"):
        coordinator.prepare_pull(Action("pull", pull_mode="rebase"))


def test_conflicted_merge_continue_is_owned_across_restart(tmp_path: Path, git_run) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _commit(git_run, local, "shared.txt", "local\n", "local")
    _advance_remote(git_run, seed, "shared.txt", "remote\n", "remote")
    journal_root = tmp_path / "pull-journal"
    first = OperationCoordinator(Repository(local), pull_journal=RecoveryJournal(journal_root))

    plan = first.prepare_pull(Action("pull", pull_mode="ff"))
    paused = first.submit_pull(plan, confirmed=True)
    assert "paused on conflicts" in paused
    assert first.managed_pull() is not None

    # A tag created before finish review is part of the reviewed finish baseline,
    # not a mutation caused by Continue.
    git_run(local, "tag", "created-before-finish-review")
    (local / "shared.txt").write_text("resolved\n")
    git_run(local, "add", "--", "shared.txt")
    restarted = OperationCoordinator(Repository(local), pull_journal=RecoveryJournal(journal_root))
    finish = restarted.prepare_pull(Action("continue"))
    assert finish.kind == "continue"
    assert restarted.submit_pull(finish) == "Continued and completed owned pull merge."
    assert restarted.managed_pull() is None
    assert RecoveryJournal(journal_root).pull_sessions(str(local)) == ()
    assert all(not item.active for item in RecoveryJournal(journal_root).list(str(local)))
    assert git_run(local, "show", "HEAD:shared.txt").stdout == "resolved\n"
    assert git_run(local, "rev-parse", "created-before-finish-review").returncode == 0


@pytest.mark.parametrize("mode", ["ff", "rebase"])
@pytest.mark.parametrize("finish", ["continue", "abort"])
def test_native_pull_and_every_finish_pin_object_and_submodule_policy(
    tmp_path: Path, git_run, monkeypatch, mode, finish
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    if mode == "rebase":
        _checkout_rebase_topic(git_run, local)
    _commit(git_run, local, "shared.txt", "local\n", "local")
    _advance_remote(git_run, seed, "shared.txt", "remote\n", "remote")
    repository = Repository(local)
    native = repository._run_integration
    commands: list[tuple[str, ...]] = []

    def observe(args, **kwargs):
        commands.append(tuple(args))
        return native(args, **kwargs)

    monkeypatch.setattr(repository, "_run_integration", observe)
    coordinator = OperationCoordinator(
        repository, pull_journal=RecoveryJournal(tmp_path / "pull-journal")
    )
    coordinator.submit_pull(
        coordinator.prepare_pull(Action("pull", pull_mode=mode)), confirmed=True
    )
    assert coordinator.managed_pull() is not None
    if finish == "continue":
        (local / "shared.txt").write_text("resolved\n")
        coordinator.submit(coordinator.plan(Action("stage", paths=("shared.txt",))))
    coordinator.submit_pull(coordinator.prepare_pull(Action(finish)), confirmed=True)
    assert coordinator.managed_pull() is None
    assert len(commands) == 2
    assert f"--{finish}" in commands[-1]
    for command in commands:
        assert command[0] == "--no-replace-objects"
        assert ("-c", "submodule.recurse=false") in tuple(zip(command, command[1:], strict=False))


def test_rebase_continue_rechecks_current_protection_and_remote_publication(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _checkout_rebase_topic(git_run, local)
    original = _commit(git_run, local, "shared.txt", "local\n", "local")
    _advance_remote(git_run, seed, "shared.txt", "remote\n", "remote")
    journal_root = tmp_path / "pull-journal"
    first = OperationCoordinator(
        Repository(local),
        pull_journal=RecoveryJournal(journal_root),
        protected_branches=("other",),
    )
    plan = first.prepare_pull(Action("pull", pull_mode="rebase"))
    assert "paused on conflicts" in first.submit_pull(plan, confirmed=True)
    (local / "shared.txt").write_text("resolved\n")
    git_run(local, "add", "shared.txt")

    protected = OperationCoordinator(
        Repository(local),
        pull_journal=RecoveryJournal(journal_root),
        protected_branches=("topic",),
    )
    with pytest.raises(GitError, match="protected from pull rebase"):
        protected.prepare_pull(Action("continue"))

    git_run(local, "update-ref", "refs/remotes/published/topic", original)
    current = OperationCoordinator(
        Repository(local),
        pull_journal=RecoveryJournal(journal_root),
        protected_branches=("other",),
    )
    with pytest.raises(GitError, match="already reachable from a known remote tip"):
        current.prepare_pull(Action("continue"))

    abort = current.prepare_pull(Action("abort"))
    assert abort.available is True
    current.submit_pull(abort, confirmed=True)
    assert git_run(local, "rev-parse", "HEAD").stdout.strip() == original


def test_conflicted_rebase_abort_preserves_resolution_across_restart(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _checkout_rebase_topic(git_run, local)
    original = _commit(git_run, local, "shared.txt", "local\n", "local")
    _advance_remote(git_run, seed, "shared.txt", "remote\n", "remote")
    journal_root = tmp_path / "pull-journal"
    first = OperationCoordinator(
        Repository(local), pull_journal=RecoveryJournal(journal_root), protected_branches=("other",)
    )

    plan = first.prepare_pull(Action("pull", pull_mode="rebase"))
    assert "paused on conflicts" in first.submit_pull(plan, confirmed=True)

    # Unresolved conflict-marker content is user work too. Abort must first retain
    # a private worktree copy even though the index still has stages 1-3.
    conflict_content = (local / "shared.txt").read_bytes()
    restarted = OperationCoordinator(
        Repository(local), pull_journal=RecoveryJournal(journal_root), protected_branches=("other",)
    )
    abort = restarted.prepare_pull(Action("abort"))
    assert abort.available is True
    result = restarted.submit_pull(abort, confirmed=True)

    assert "resolution worktree copies remain in checkpoint" in result
    assert git_run(local, "rev-parse", "HEAD").stdout.strip() == original
    checkpoints = RecoveryJournal(journal_root).list(str(local))
    assert all(not item.active for item in checkpoints)
    assert RecoveryJournal(journal_root).pull_sessions(str(local)) == ()
    resolution = next(item for item in checkpoints if item.operation == "pull-abort-resolution")
    recovered = RecoveryJournal(journal_root).recover_aside(Repository(local), resolution.id)
    assert recovered.directory is not None
    assert (recovered.directory / "worktree" / "shared.txt").read_bytes() == conflict_content


def test_fetch_branch_switch_at_same_oid_is_detected(
    tmp_path: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _advance_remote(git_run, seed, "remote.txt", "incoming\n", "incoming")
    git_run(local, "branch", "other")
    repository = Repository(local)
    native_fetch = repository._execute_fetch

    def fetch_then_switch(remote: str, *, disable_submodule_recursion: bool = False):
        result = native_fetch(remote, disable_submodule_recursion=disable_submodule_recursion)
        git_run(local, "switch", "other")
        return result

    monkeypatch.setattr(repository, "_execute_fetch", fetch_then_switch)
    coordinator = OperationCoordinator(repository)
    with pytest.raises(GitError, match="branch or HEAD changed during fetch"):
        coordinator.prepare_pull(Action("pull", pull_mode="ff"))


def test_hidden_index_flags_are_rejected_but_unrelated_ignored_tree_is_preserved(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    git_run(local, "update-index", "--assume-unchanged", "shared.txt")
    coordinator = OperationCoordinator(Repository(local))
    with pytest.raises(GitError, match="hidden index"):
        coordinator.prepare_pull(Action("pull"))

    git_run(local, "update-index", "--no-assume-unchanged", "shared.txt")
    (local / ".git" / "info" / "exclude").write_text("node_modules/\n")
    ignored = local / "node_modules" / "cache.bin"
    ignored.parent.mkdir()
    ignored.write_bytes(b"private cache")
    target = _advance_remote(git_run, seed, "remote.txt", "incoming\n", "incoming")
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff-only"))
    coordinator.submit_pull(plan)
    assert git_run(local, "rev-parse", "HEAD").stdout.strip() == target
    assert ignored.read_bytes() == b"private cache"


def test_abort_checkpoint_preserves_mixed_conflict_and_resolved_path_preimages(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _commit(git_run, seed, "resolved.txt", "base B\n", "add B")
    git_run(seed, "push", "origin", "main")
    git_run(local, "pull", "--ff-only")
    _commit(git_run, local, "shared.txt", "local\n", "local conflict")
    _advance_remote(git_run, seed, "shared.txt", "remote\n", "remote conflict")
    journal_root = tmp_path / "pull-journal"
    coordinator = OperationCoordinator(
        Repository(local), pull_journal=RecoveryJournal(journal_root)
    )
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff"))
    assert "paused on conflicts" in coordinator.submit_pull(plan, confirmed=True)

    (local / "resolved.txt").write_text("index B\n")
    git_run(local, "add", "resolved.txt")
    (local / "resolved.txt").write_text("worktree B\n")
    abort = coordinator.prepare_pull(Action("abort"))
    coordinator.submit_pull(abort, confirmed=True)

    journal = RecoveryJournal(journal_root)
    checkpoint = next(
        item for item in journal.list(str(local)) if item.operation == "pull-abort-resolution"
    )
    recovered = journal.recover_aside(Repository(local), checkpoint.id)
    assert recovered.directory is not None
    assert (recovered.directory / "index" / "resolved.txt").read_text() == "index B\n"
    assert (recovered.directory / "worktree" / "resolved.txt").read_text() == "worktree B\n"
    assert not (recovered.directory / "index" / "shared.txt").exists()
    assert (recovered.directory / "worktree" / "shared.txt").exists()


def test_abort_scope_ignores_replace_objects_before_restoring_original_tree(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _checkout_rebase_topic(git_run, local)
    _commit(git_run, local, "private.txt", "tracked original\n", "add private")
    original = _commit(git_run, local, "shared.txt", "local\n", "local conflict")
    _advance_remote(git_run, seed, "shared.txt", "remote\n", "remote conflict")
    journal_root = tmp_path / "pull-journal"
    coordinator = OperationCoordinator(
        Repository(local),
        pull_journal=RecoveryJournal(journal_root),
        protected_branches=("other",),
    )
    plan = coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
    assert "paused on conflicts" in coordinator.submit_pull(plan, confirmed=True)

    git_run(local, "rm", "--cached", "private.txt")
    (local / ".git" / "info" / "exclude").write_text("private.txt\n")
    private = local / "private.txt"
    private.write_bytes(b"ignored private replacement\n")
    replacement_tree = git_run(local, "rev-parse", f"{plan.target_oid}^{{tree}}").stdout.strip()
    replacement = git_run(
        local, "commit-tree", replacement_tree, "-m", "replacement without private path"
    ).stdout.strip()
    git_run(local, "replace", original, replacement)

    with pytest.raises(GitError, match="[Uu]ntracked or ignored path 'private.txt' may be lost"):
        coordinator.prepare_pull(Action("abort"))
    assert private.read_bytes() == b"ignored private replacement\n"
    assert git_run(local, "rev-parse", "refs/heads/topic").stdout.strip() == original
    assert coordinator.managed_pull() is not None


def test_abort_checkpoint_preserves_distinct_resolved_index_and_worktree_versions(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _commit(git_run, local, "shared.txt", "local\n", "local")
    _advance_remote(git_run, seed, "shared.txt", "remote\n", "remote")
    journal_root = tmp_path / "pull-journal"
    coordinator = OperationCoordinator(
        Repository(local), pull_journal=RecoveryJournal(journal_root)
    )
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff"))
    coordinator.submit_pull(plan, confirmed=True)
    (local / "shared.txt").write_text("staged resolution\n")
    git_run(local, "add", "shared.txt")
    (local / "shared.txt").write_text("unstaged refinement\n")

    abort = coordinator.prepare_pull(Action("abort"))
    coordinator.submit_pull(abort, confirmed=True)
    journal = RecoveryJournal(journal_root)
    checkpoint = next(
        item for item in journal.list(str(local)) if item.operation == "pull-abort-resolution"
    )
    recovered = journal.recover_aside(Repository(local), checkpoint.id)
    assert recovered.directory is not None
    assert (recovered.directory / "index" / "shared.txt").read_text() == "staged resolution\n"
    assert (recovered.directory / "worktree" / "shared.txt").read_text() == "unstaged refinement\n"


def test_continue_checkpoint_preserves_first_resolution_when_rebase_hits_second_conflict(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _commit(git_run, seed, "first.txt", "base first\n", "add first")
    _commit(git_run, seed, "second.txt", "base second\n", "add second")
    git_run(seed, "push", "origin", "main")
    git_run(local, "pull", "--ff-only")
    _checkout_rebase_topic(git_run, local)
    _commit(git_run, local, "first.txt", "local first\n", "local first")
    original = _commit(git_run, local, "second.txt", "local second\n", "local second")
    (seed / "first.txt").write_text("remote first\n")
    (seed / "second.txt").write_text("remote second\n")
    git_run(seed, "add", "first.txt", "second.txt")
    git_run(seed, "commit", "-m", "remote conflicts")
    git_run(seed, "push", "origin", "main")
    journal_root = tmp_path / "pull-journal"
    coordinator = OperationCoordinator(
        Repository(local), pull_journal=RecoveryJournal(journal_root), protected_branches=("other",)
    )

    plan = coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
    assert "paused on conflicts" in coordinator.submit_pull(plan, confirmed=True)
    (local / "first.txt").write_text("resolved first\n")
    git_run(local, "add", "first.txt")
    continuation = coordinator.prepare_pull(Action("continue"))
    assert "remains paused" in coordinator.submit_pull(continuation)
    assert "<<<<<<<" in (local / "second.txt").read_text()

    abort = coordinator.prepare_pull(Action("abort"))
    coordinator.submit_pull(abort, confirmed=True)
    assert git_run(local, "rev-parse", "HEAD").stdout.strip() == original
    journal = RecoveryJournal(journal_root)
    retained = next(
        item for item in journal.list(str(local)) if item.operation == "pull-continue-resolution"
    )
    assert retained.active is False
    recovered = journal.recover_aside(Repository(local), retained.id)
    assert recovered.directory is not None
    assert (recovered.directory / "index" / "first.txt").read_text() == "resolved first\n"
    assert (recovered.directory / "worktree" / "first.txt").read_text() == "resolved first\n"


def test_rebase_refuses_ignored_path_touched_only_by_outgoing_transitions(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _checkout_rebase_topic(git_run, local)
    _commit(git_run, local, "private.tmp", "temporary tracked content\n", "add transient")
    git_run(local, "rm", "private.tmp")
    git_run(local, "commit", "-m", "delete transient")
    _advance_remote(git_run, seed, "remote.txt", "remote\n", "remote")
    (local / ".git" / "info" / "exclude").write_text("private.tmp\n")
    private = local / "private.tmp"
    private.write_text("private ignored content\n")
    journal = RecoveryJournal(tmp_path / "pull-journal")
    coordinator = OperationCoordinator(
        Repository(local), pull_journal=journal, protected_branches=("other",)
    )

    plan = coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
    with pytest.raises(GitError, match="Ignored path 'private.tmp' may obstruct"):
        coordinator.submit_pull(plan, confirmed=True)
    assert private.read_text() == "private ignored content\n"
    assert journal.list(str(local)) == ()


def test_rebase_continue_refuses_ignored_future_transition_but_abort_remains_available(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _checkout_rebase_topic(git_run, local)
    _commit(git_run, local, "shared.txt", "local\n", "conflicting first")
    _commit(git_run, local, "private.tmp", "temporary tracked content\n", "add transient")
    git_run(local, "rm", "private.tmp")
    git_run(local, "commit", "-m", "delete transient")
    original = git_run(local, "rev-parse", "HEAD").stdout.strip()
    _advance_remote(git_run, seed, "shared.txt", "remote\n", "remote conflict")
    journal_root = tmp_path / "pull-journal"
    coordinator = OperationCoordinator(
        Repository(local),
        pull_journal=RecoveryJournal(journal_root),
        protected_branches=("other",),
    )
    plan = coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
    assert "paused on conflicts" in coordinator.submit_pull(plan, confirmed=True)
    (local / "shared.txt").write_text("resolved\n")
    git_run(local, "add", "shared.txt")
    (local / ".git" / "info" / "exclude").write_text("private.tmp\n")
    private = local / "private.tmp"
    private.write_text("private ignored content\n")

    with pytest.raises(GitError, match="may be lost while finishing pull"):
        coordinator.prepare_pull(Action("continue"))
    assert private.read_text() == "private ignored content\n"
    abort = coordinator.prepare_pull(Action("abort"))
    coordinator.submit_pull(abort, confirmed=True)
    assert git_run(local, "rev-parse", "HEAD").stdout.strip() == original
    assert private.read_text() == "private ignored content\n"


def test_synchronous_post_merge_hook_extra_commit_is_uncertain_and_leased(
    tmp_path: Path, git_run
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _commit(git_run, local, "local.txt", "local\n", "local")
    _advance_remote(git_run, seed, "remote.txt", "remote\n", "remote")
    hook = local / ".git" / "hooks" / "post-merge"
    hook.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf 'hook commit\\n' > hook.txt\n"
        "git add -- hook.txt\n"
        "git commit -m 'synchronous hook commit' >/dev/null\n"
    )
    hook.chmod(0o700)
    journal = RecoveryJournal(tmp_path / "pull-journal")
    coordinator = OperationCoordinator(Repository(local), pull_journal=journal)
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff"))

    with pytest.raises(GitOutcomeUncertain, match="exact reviewed two-parent final commit"):
        coordinator.submit_pull(plan, confirmed=True)
    lease = coordinator._pull_lease.read()
    assert lease is not None and lease.phase == "uncertain"
    assert journal.list(str(local))[0].active is True


def test_successful_native_merge_with_unrelated_tag_write_is_uncertain_and_leased(
    tmp_path: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _commit(git_run, local, "local.txt", "local\n", "local")
    _advance_remote(git_run, seed, "remote.txt", "remote\n", "remote")
    repository = Repository(local)
    journal = RecoveryJournal(tmp_path / "pull-journal")
    coordinator = OperationCoordinator(repository, pull_journal=journal)
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff"))
    native = repository._run_integration

    def native_then_unscoped_tag(args, *, operation, on_spawn=None):
        result = native(args, operation=operation, on_spawn=on_spawn)
        git_run(local, "tag", "hook-created-tag")
        return result

    monkeypatch.setattr(repository, "_run_integration", native_then_unscoped_tag)
    with pytest.raises(GitOutcomeUncertain, match="unrelated local ref or tag"):
        coordinator.submit_pull(plan, confirmed=True)
    lease = coordinator._pull_lease.read()
    assert lease is not None and lease.phase == "uncertain"
    assert journal.list(str(local))[0].active is True


def test_uncertain_fast_forward_lease_survives_restart_and_blocks_all_coordinator_mutations(
    tmp_path: Path, git_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remote, seed, local = _repositories(tmp_path, git_run)
    _advance_remote(git_run, seed, "remote.txt", "incoming\n", "incoming")
    repository = Repository(local)
    coordinator = OperationCoordinator(repository)
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff-only"))

    def uncertain_native(args, *, operation, on_spawn=None):
        assert args and operation == "pull fast-forward"
        assert on_spawn is not None
        on_spawn(24680)
        raise GitOutcomeUncertain("injected native uncertainty")

    monkeypatch.setattr(repository, "_run_integration", uncertain_native)
    with pytest.raises(GitOutcomeUncertain, match="injected native uncertainty"):
        coordinator.submit_pull(plan)

    lease = coordinator._pull_lease.read()
    assert lease is not None
    assert (lease.phase, lease.pid, lease.pgid) == ("uncertain", 24680, 24680)
    restarted = OperationCoordinator(Repository(local))
    with pytest.raises(GitError, match="ownership is uncertain"):
        restarted.managed_pull()
    with pytest.raises(GitError, match="durable uncertain pull writer lease"):
        restarted.prepare_pull(Action("pull"))
    fetch = restarted.plan(Action("fetch", remote="origin"))
    with pytest.raises(GitError, match="durable uncertain pull writer lease"):
        restarted.submit(fetch)
    coordinator._pull_lease.path.chmod(0o644)
    with pytest.raises(GitError, match="unsafe or not private"):
        restarted.prepare_pull(Action("pull"))


def test_separate_pull_journal_does_not_enable_generic_destructive_operations(
    tmp_path: Path, git_run
) -> None:
    _remote, _seed, local = _repositories(tmp_path, git_run)
    coordinator = OperationCoordinator(
        Repository(local), pull_journal=RecoveryJournal(tmp_path / "pull-journal")
    )
    plan = coordinator.plan(Action("discard", paths=("shared.txt",)))
    assert plan.available is False
    assert "durable recovery checkpoint journal" in plan.reason


def test_pull_rejects_scope_inputs_but_honors_captured_branch_context(
    tmp_path: Path, git_run
) -> None:
    _remote, _seed, local = _repositories(tmp_path, git_run)
    head = git_run(local, "rev-parse", "HEAD").stdout.strip()
    coordinator = OperationCoordinator(Repository(local))
    plan = coordinator.prepare_pull(
        Action(
            "pull",
            expected_head=head,
            expected_branch="main",
            expected_ref="refs/heads/main",
            expected_oid=head,
        )
    )
    assert plan.available
    coordinator.cancel_pull(plan)

    with pytest.raises(GitError, match="exact configured upstream"):
        coordinator.prepare_pull(Action("pull", remote="origin"))
