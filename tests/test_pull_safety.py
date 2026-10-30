"""Adversarial pull plans observed through the coordinator and real local Git."""

from dataclasses import replace

import pytest

from rhizome.git import GitError, Repository
from rhizome.models import Action
from rhizome.operations import OperationCoordinator
from rhizome.recovery import RecoveryJournal


@pytest.fixture
def pull_pair(git_repo, bare_remote, git_run, tmp_path):
    (git_repo / "base.txt").write_text("base\n")
    git_run(git_repo, "add", "base.txt")
    git_run(git_repo, "commit", "-m", "base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "-u", "origin", "main")
    writer = tmp_path / "upstream-writer"
    git_run(tmp_path, "clone", str(bare_remote), str(writer))
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "branch", "--set-upstream-to=origin/main", "feature")
    journal = RecoveryJournal(tmp_path / "recovery")
    coordinator = OperationCoordinator(Repository(git_repo), pull_journal=journal)
    return git_repo, writer, coordinator, journal


def commit_file(git_run, root, name, contents):
    (root / name).write_text(contents)
    git_run(root, "add", "--", name)
    git_run(root, "commit", "-m", f"Change {name}")
    return git_run(root, "rev-parse", "HEAD").stdout.strip()


def remote_advance(git_run, writer, name="remote.txt"):
    oid = commit_file(git_run, writer, name, "remote\n")
    git_run(writer, "push", "origin", "main")
    return oid


def test_pull_plan_cannot_be_retargeted_or_reused(pull_pair, git_run):
    root, writer, coordinator, _journal = pull_pair
    head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    remote_advance(git_run, writer)
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff-only"))
    with pytest.raises(GitError, match="changed|altered"):
        coordinator.submit_pull(replace(plan, branch="main"))
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == head
    with pytest.raises(GitError, match="unknown|expired|submitted|consumed"):
        coordinator.submit_pull(plan)


@pytest.mark.parametrize("change", ["config", "branch", "staged", "untracked"])
def test_changed_local_state_refuses_reviewed_pull(pull_pair, git_run, change):
    root, writer, coordinator, _journal = pull_pair
    head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    remote_advance(git_run, writer)
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff"))
    if change == "config":
        git_run(root, "config", "branch.feature.merge", "refs/heads/another")
    elif change == "branch":
        git_run(root, "switch", "main")  # Same OID, different branch identity.
    else:
        (root / "keep.txt").write_text("Do not overwrite my work\n")
        if change == "staged":
            git_run(root, "add", "keep.txt")
    with pytest.raises(GitError):
        coordinator.submit_pull(plan)
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == head
    if change in {"staged", "untracked"}:
        assert (root / "keep.txt").read_text() == "Do not overwrite my work\n"


def test_rebase_refuses_local_head_already_published_on_another_remote_ref(pull_pair, git_run):
    root, writer, coordinator, _journal = pull_pair
    head = commit_file(git_run, root, "local.txt", "local\n")
    git_run(root, "update-ref", "refs/remotes/published/feature", head)
    remote_advance(git_run, writer)
    with pytest.raises(GitError, match="published|remote tip|reachable"):
        coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == head
    assert (root / "local.txt").read_text() == "local\n"


def test_rebase_eligibility_is_rechecked_after_review(pull_pair, git_run):
    root, writer, coordinator, _journal = pull_pair
    original = commit_file(git_run, root, "local.txt", "local\n")
    remote_advance(git_run, writer)
    plan = coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
    git_run(root, "update-ref", "refs/remotes/published/feature", original)
    with pytest.raises(GitError):
        coordinator.submit_pull(plan, confirmed=True)
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == original
    assert (root / "local.txt").read_text() == "local\n"


def test_changed_rebase_program_cannot_be_adopted_on_continue(pull_pair, git_run):
    root, writer, coordinator, _journal = pull_pair
    commit_file(git_run, root, "base.txt", "local conflicting version\n")
    commit_file(git_run, root, "later.txt", "later local commit\n")
    commit_file(git_run, writer, "base.txt", "remote conflicting version\n")
    git_run(writer, "push", "origin", "main")
    plan = coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
    coordinator.submit_pull(plan, confirmed=True)
    assert coordinator.managed_pull() is not None
    (root / "base.txt").write_text("resolved\n")
    git_run(root, "add", "base.txt")
    head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    todo = root / ".git" / "rebase-merge" / "git-rebase-todo"
    marker = root.parent / "unreviewed-program-ran"
    with todo.open("a") as stream:
        stream.write(f"\nexec touch '{marker}'\n")
    with pytest.raises(GitError):
        finish = coordinator.prepare_pull(Action("continue"))
        coordinator.submit_pull(finish)
    assert not marker.exists()
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == head


@pytest.mark.parametrize("changed_policy", ["published", "protected"])
def test_paused_rebase_rechecks_eligibility_but_still_allows_abort(
    pull_pair, git_run, changed_policy
):
    root, writer, coordinator, journal = pull_pair
    original = commit_file(git_run, root, "base.txt", "local conflicting version\n")
    commit_file(git_run, writer, "base.txt", "remote conflicting version\n")
    git_run(writer, "push", "origin", "main")
    plan = coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
    coordinator.submit_pull(plan, confirmed=True)
    assert coordinator.managed_pull() is not None
    (root / "base.txt").write_text("resolved\n")
    git_run(root, "add", "base.txt")
    paused_head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    if changed_policy == "published":
        git_run(root, "update-ref", "refs/remotes/published/feature", original)
    restarted = OperationCoordinator(
        Repository(root),
        pull_journal=journal,
        protected_branches=("feature",) if changed_policy == "protected" else (),
    )
    with pytest.raises(GitError):
        finish = restarted.prepare_pull(Action("continue"))
        restarted.submit_pull(finish)
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == paused_head
    assert git_run(root, "rev-parse", "refs/heads/feature").stdout.strip() == original

    abort = restarted.prepare_pull(Action("abort"))
    restarted.submit_pull(abort, confirmed=True)
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == original
    if changed_policy == "published":
        assert (
            git_run(root, "rev-parse", "refs/remotes/published/feature").stdout.strip() == original
        )


def test_ignored_file_obstruction_is_not_overwritten_by_fast_forward(pull_pair, git_run):
    root, writer, coordinator, _journal = pull_pair
    head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    (root / ".git" / "info" / "exclude").write_text("remote.txt\n")
    (root / "remote.txt").write_text("private ignored data\n")
    remote_advance(git_run, writer)
    with pytest.raises(GitError, match="[Ii]gnored|obstruct"):
        plan = coordinator.prepare_pull(Action("pull", pull_mode="ff-only"))
        coordinator.submit_pull(plan)
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == head
    assert (root / "remote.txt").read_text() == "private ignored data\n"


@pytest.mark.parametrize("finish", ["continue", "abort"])
def test_finish_refuses_untracked_files_before_mutation_then_can_retry(pull_pair, git_run, finish):
    root, writer, coordinator, _journal = pull_pair
    original = commit_file(git_run, root, "base.txt", "local conflict\n")
    commit_file(git_run, writer, "base.txt", "remote conflict\n")
    git_run(writer, "push", "origin", "main")
    coordinator.submit_pull(
        coordinator.prepare_pull(Action("pull", pull_mode="rebase")), confirmed=True
    )
    if finish == "continue":
        (root / "base.txt").write_text("resolved\n")
        coordinator.submit(coordinator.plan(Action("stage", paths=("base.txt",))))
    private = root / "notes.txt"
    private.write_text("untracked notes to retain\n")
    head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(GitError, match="[Uu]ntracked"):
        coordinator.submit_pull(coordinator.prepare_pull(Action(finish)), confirmed=True)
    assert private.read_text() == "untracked notes to retain\n"
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == head
    assert git_run(root, "rev-parse", "refs/heads/feature").stdout.strip() == original
    assert coordinator.managed_pull() is not None

    saved = root.parent / "saved-notes.txt"
    private.rename(saved)
    coordinator.submit_pull(coordinator.prepare_pull(Action(finish)), confirmed=True)
    assert coordinator.managed_pull() is None
    assert saved.read_text() == "untracked notes to retain\n"


def test_abort_rejects_new_hidden_index_flags_before_touching_private_edits(pull_pair, git_run):
    root, writer, coordinator, _journal = pull_pair
    remote_advance(git_run, writer, "hidden.txt")
    coordinator.submit_pull(coordinator.prepare_pull(Action("pull", pull_mode="ff-only")))
    commit_file(git_run, root, "base.txt", "local conflict\n")
    commit_file(git_run, writer, "base.txt", "remote conflict\n")
    commit_file(git_run, writer, "hidden.txt", "upstream changed this\n")
    git_run(writer, "push", "origin", "main")
    coordinator.submit_pull(
        coordinator.prepare_pull(Action("pull", pull_mode="rebase")), confirmed=True
    )
    paused_head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    private = root / "hidden.txt"
    private.write_text("do not lose these hidden edits\n")
    git_run(root, "update-index", "--assume-unchanged", "hidden.txt")

    with pytest.raises(GitError):
        coordinator.submit_pull(coordinator.prepare_pull(Action("abort")), confirmed=True)
    assert private.read_text() == "do not lose these hidden edits\n"
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == paused_head
    assert (root / ".git" / "rebase-merge").is_dir()


def test_rebase_never_overwrites_ignored_data_in_transient_replay_paths(pull_pair, git_run):
    root, writer, coordinator, _journal = pull_pair
    commit_file(git_run, root, "transient.txt", "committed temporarily\n")
    git_run(root, "rm", "transient.txt")
    git_run(root, "commit", "-m", "Remove temporary path")
    original = git_run(root, "rev-parse", "HEAD").stdout.strip()
    (root / ".git" / "info" / "exclude").write_text("transient.txt\n")
    private = root / "transient.txt"
    private.write_text("private ignored preimage\n")
    remote_advance(git_run, writer)

    with pytest.raises(GitError):
        plan = coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
        coordinator.submit_pull(plan, confirmed=True)
    assert private.read_text() == "private ignored preimage\n"
    assert git_run(root, "rev-parse", "HEAD").stdout.strip() == original


def test_merge_mode_overrides_squash_configuration(pull_pair, git_run):
    root, writer, coordinator, _journal = pull_pair
    original = commit_file(git_run, root, "local.txt", "local\n")
    target = remote_advance(git_run, writer)
    git_run(root, "config", "branch.feature.mergeOptions", "--squash")
    git_run(root, "config", "merge.autoStash", "true")
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff"))
    assert plan.kind == "merge" and plan.requires_confirmation
    coordinator.submit_pull(plan, confirmed=True)
    parents = git_run(root, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:]
    assert parents == [original, target]
    assert git_run(root, "status", "--porcelain").stdout == ""


def test_rebase_does_not_update_other_local_refs_when_config_requests_it(pull_pair, git_run):
    root, writer, coordinator, _journal = pull_pair
    original = commit_file(git_run, root, "local.txt", "local\n")
    git_run(root, "branch", "keep-original")
    git_run(root, "config", "rebase.updateRefs", "true")
    target = remote_advance(git_run, writer)
    plan = coordinator.prepare_pull(Action("pull", pull_mode="rebase"))
    assert plan.kind == "rebase" and plan.requires_confirmation
    coordinator.submit_pull(plan, confirmed=True)
    assert git_run(root, "rev-parse", "keep-original").stdout.strip() == original
    assert git_run(root, "rev-parse", "HEAD^").stdout.strip() == target
    assert git_run(root, "branch", "--show-current").stdout.strip() == "feature"
    assert (root / "local.txt").read_text() == "local\n"
    assert git_run(root, "status", "--porcelain").stdout == ""
