from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from test_app import settle
from textual.containers import VerticalScroll
from textual.widgets import Button, OptionList, Static

from rhizome.app import RhizomeApp
from rhizome.persistence import WorkspaceStateStore
from rhizome.pull_controls import PullReviewDialog


@dataclass(frozen=True)
class PullRepositories:
    remote: Path
    writer: Path
    local: Path


def _oid(git_run, root: Path, revision: str = "HEAD") -> str:
    return git_run(root, "rev-parse", revision).stdout.strip()


def _commit(git_run, root: Path, content: str, message: str, path: str = "shared.txt") -> str:
    (root / path).write_text(content)
    git_run(root, "add", "--", path)
    git_run(root, "commit", "-m", message)
    return _oid(git_run, root)


@pytest.fixture
def pull_repositories(tmp_path: Path, git_run) -> PullRepositories:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git_run(remote, "init", "--bare", "-b", "main")

    writer = tmp_path / "writer"
    writer.mkdir()
    git_run(writer, "init", "-b", "main")
    git_run(writer, "config", "user.name", "Test User")
    git_run(writer, "config", "user.email", "test@example.invalid")
    git_run(writer, "config", "commit.gpgsign", "false")
    _commit(git_run, writer, "base\n", "Base")
    git_run(writer, "remote", "add", "origin", str(remote))
    git_run(writer, "push", "-u", "origin", "main")

    local = tmp_path / "local"
    git_run(tmp_path, "clone", str(remote), str(local))
    git_run(local, "config", "user.name", "Test User")
    git_run(local, "config", "user.email", "test@example.invalid")
    git_run(local, "config", "commit.gpgsign", "false")
    return PullRepositories(remote, writer, local)


def _save_global_settings(**changes: object) -> None:
    store = WorkspaceStateStore()
    current = store.load("global")
    store.save("global", {**current, **changes}, expected=current)


def _disable_automatic_fetch() -> None:
    _save_global_settings(remote_refresh_enabled=False)


async def _wait_for_review(app: RhizomeApp, pilot) -> PullReviewDialog:
    for _ in range(100):
        await pilot.pause(0.05)
        if isinstance(app.screen, PullReviewDialog):
            await app.workers.wait_for_complete()
            await pilot.pause()
            return app.screen
    raise AssertionError("Pull review did not open")


async def _choose_pull_mode(app: RhizomeApp, pilot, mode: str) -> None:
    assert await pilot.click("#pull-menu")
    await pilot.pause()
    options = app.screen.query_one(OptionList)
    options.highlighted = options.get_option_index(mode)
    await pilot.press("enter")
    await pilot.pause()


async def test_primary_pull_fast_forwards_and_noop_without_review(
    pull_repositories: PullRepositories, git_run
) -> None:
    _disable_automatic_fetch()
    repos = pull_repositories
    original = _oid(git_run, repos.local)
    target = _commit(git_run, repos.writer, "remote\n", "Remote advance")
    git_run(repos.writer, "push", "origin", "main")

    app = RhizomeApp(repos.local)
    async with app.run_test(size=(120, 38)) as pilot:
        await settle(app, pilot)
        assert app.pull_fetch_mode() == "pull-ff"
        assert len(app.screen_stack) == 1

        assert await pilot.click("#fetch")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert _oid(git_run, repos.local) == target != original, (
            app._last_error,
            app._status_message,
        )
        assert git_run(repos.local, "status", "--porcelain=v1").stdout == ""

        # Equal local/upstream is a fetch-only no-op and must not ask for review.
        assert await pilot.click("#fetch")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert _oid(git_run, repos.local) == target


@pytest.mark.parametrize("mode", ["fetch-all", "pull-ff-only"])
async def test_saved_fetch_all_and_ff_only_do_not_integrate_divergence(
    pull_repositories: PullRepositories, git_run, mode
) -> None:
    _disable_automatic_fetch()
    repos = pull_repositories
    original = _commit(git_run, repos.local, "local\n", "Local divergence")
    target = _commit(git_run, repos.writer, "remote\n", "Remote divergence")
    git_run(repos.writer, "push", "origin", "main")
    git_run(repos.local, "remote", "add", "backup", str(repos.remote))
    tracking_before = _oid(git_run, repos.local, "refs/remotes/origin/main")

    app = RhizomeApp(repos.local)
    async with app.run_test(size=(120, 38)) as pilot:
        await settle(app, pilot)
        await _choose_pull_mode(app, pilot, mode)
        assert _oid(git_run, repos.local, "refs/remotes/origin/main") == tracking_before
        assert await pilot.click("#fetch")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert _oid(git_run, repos.local) == original
        assert _oid(git_run, repos.local, "refs/remotes/origin/main") == target
        assert git_run(repos.local, "status", "--porcelain=v1").stdout == ""
        assert not app.snapshot_data.operation
        if mode == "fetch-all":
            assert not app._last_error
            assert _oid(git_run, repos.local, "refs/remotes/backup/main") == target
        else:
            assert "fast-forward" in app._last_error.lower()


@pytest.mark.parametrize("size", [(80, 24), (170, 48)])
async def test_divergent_merge_review_cancel_then_confirm(
    pull_repositories: PullRepositories, git_run, size
) -> None:
    _disable_automatic_fetch()
    repos = pull_repositories
    original = _commit(git_run, repos.local, "local\n", "Local advance", "local.txt")
    target = _commit(git_run, repos.writer, "remote\n", "Remote advance", "remote.txt")
    git_run(repos.writer, "push", "origin", "main")
    tracking_before = _oid(git_run, repos.local, "refs/remotes/origin/main")

    app = RhizomeApp(repos.local)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        assert await pilot.click("#fetch")
        review = await _wait_for_review(app, pilot)
        assert review.plan.kind == "merge"
        assert review.plan.target_oid == target
        assert review.plan.original_head == original
        for ident in ("pull-review-cancel", "pull-review-confirm"):
            region = review.query_one(f"#{ident}", Button).region
            assert 0 <= region.x < region.right <= size[0]
            assert 0 <= region.y < region.bottom <= size[1]

        assert await pilot.click("#pull-review-cancel")
        await settle(app, pilot)
        assert _oid(git_run, repos.local) == original
        assert _oid(git_run, repos.local, "refs/remotes/origin/main") == target != tracking_before
        assert "review cancelled" in app._status_message.lower()

        assert await pilot.click("#fetch")
        review = await _wait_for_review(app, pilot)
        assert review.plan.kind == "merge"
        assert await pilot.click("#pull-review-confirm")
        await settle(app, pilot)

        head = _oid(git_run, repos.local)
        parents = git_run(repos.local, "show", "-s", "--format=%P", head).stdout.split()
        assert parents == [original, target], (app._last_error, app._status_message)
        assert git_run(repos.local, "status", "--porcelain=v1").stdout == ""


async def test_owned_merge_conflict_continues_after_staged_resolution(
    pull_repositories: PullRepositories, git_run
) -> None:
    _disable_automatic_fetch()
    repos = pull_repositories
    original = _commit(git_run, repos.local, "local\n", "Local conflict")
    target = _commit(git_run, repos.writer, "remote\n", "Remote conflict")
    git_run(repos.writer, "push", "origin", "main")

    app = RhizomeApp(repos.local)
    async with app.run_test(size=(120, 38)) as pilot:
        await settle(app, pilot)
        assert await pilot.click("#fetch")
        assert (await _wait_for_review(app, pilot)).plan.kind == "merge"
        assert await pilot.click("#pull-review-confirm")
        await settle(app, pilot)

        assert app.query_one("#operation-banner").display
        assert app.query_one("#continue", Button).disabled
        assert (
            git_run(repos.local, "diff", "--name-only", "--diff-filter=U").stdout.strip()
            == "shared.txt"
        )

        (repos.local / "shared.txt").write_text("resolved\n")
        app.action_refresh()
        await settle(app, pilot)
        await pilot.press("f2")
        await pilot.pause()
        assert await pilot.click("#stage-all")
        await settle(app, pilot)
        assert not app.query_one("#continue", Button).disabled, app._last_error

        assert await pilot.click("#continue")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert not app.query_one("#operation-banner").display, (
            app._last_error,
            app._status_message,
        )
        assert git_run(repos.local, "status", "--porcelain=v1").stdout == ""
        assert git_run(repos.local, "show", "HEAD:shared.txt").stdout == "resolved\n"
        parents = git_run(repos.local, "show", "-s", "--format=%P", "HEAD").stdout.split()
        assert parents == [original, target]


@pytest.mark.parametrize("reload_protection", [False, True])
async def test_owned_rebase_conflict_restarts_and_abort_requires_confirmation(
    pull_repositories: PullRepositories, git_run, reload_protection
) -> None:
    _disable_automatic_fetch()
    repos = pull_repositories
    git_run(repos.local, "switch", "-c", "topic")
    git_run(repos.local, "branch", "--set-upstream-to=origin/main", "topic")
    original = _commit(git_run, repos.local, "local\n", "Local conflict")
    _commit(git_run, repos.writer, "remote\n", "Remote conflict")
    git_run(repos.writer, "push", "origin", "main")
    tracking_before = _oid(git_run, repos.local, "refs/remotes/origin/main")

    first = RhizomeApp(repos.local)
    async with first.run_test(size=(120, 38)) as pilot:
        await settle(first, pilot)
        await _choose_pull_mode(first, pilot, "pull-rebase")
        # Selecting a mode is preference-only; the main click performs transport.
        assert _oid(git_run, repos.local, "refs/remotes/origin/main") == tracking_before
        assert await pilot.click("#fetch")
        review = await _wait_for_review(first, pilot)
        assert review.plan.kind == "rebase"
        assert await pilot.click("#pull-review-confirm")
        await settle(first, pilot)
        assert first.query_one("#operation-banner").display
        assert (
            git_run(repos.local, "diff", "--name-only", "--diff-filter=U").stdout.strip()
            == "shared.txt"
        )

    # A new app instance must rediscover only the journal-owned paused operation.
    restarted = RhizomeApp(repos.local)
    async with restarted.run_test(size=(120, 38)) as pilot:
        await settle(restarted, pilot)
        assert restarted.query_one("#operation-banner").display
        assert not restarted.query_one("#abort", Button).disabled
        if reload_protection:
            _save_global_settings(protected_branches=["topic"])
            await pilot.press("f4")
            await pilot.pause()
            reload_button = restarted.screen.query_one("#reload-settings", Button)
            reload_button.focus()
            await pilot.pause()
            assert await pilot.click(reload_button)
            await settle(restarted, pilot)
            (repos.local / "shared.txt").write_text("resolved\n")
            git_run(repos.local, "add", "shared.txt")
            restarted.action_refresh()
            await settle(restarted, pilot)
            head = _oid(git_run, repos.local)
            assert await pilot.click("#continue")
            await settle(restarted, pilot)
            assert "protected" in restarted._last_error.lower()
            assert _oid(git_run, repos.local) == head
            assert _oid(git_run, repos.local, "refs/heads/topic") == original

        assert await pilot.click("#abort")
        review = await _wait_for_review(restarted, pilot)
        assert review.plan.kind == "abort"
        assert review.plan.requires_confirmation
        assert await pilot.click("#pull-review-cancel")
        await settle(restarted, pilot)
        assert restarted.query_one("#operation-banner").display

        assert await pilot.click("#abort")
        assert (await _wait_for_review(restarted, pilot)).plan.kind == "abort"
        assert await pilot.click("#pull-review-confirm")
        await settle(restarted, pilot)
        assert _oid(git_run, repos.local) == original
        assert git_run(repos.local, "status", "--porcelain=v1").stdout == ""
        assert not restarted.query_one("#operation-banner").display


async def test_uncertain_pull_survives_restart_and_cannot_be_hidden_by_exclusion(
    pull_repositories: PullRepositories, git_run
) -> None:
    _disable_automatic_fetch()
    repos = pull_repositories
    target = _commit(git_run, repos.writer, "incoming\n", "Incoming", path="incoming.txt")
    git_run(repos.writer, "push", "origin", "main")
    hook = repos.local / ".git" / "hooks" / "post-merge"
    hook.write_text("#!/bin/sh\nexec git update-ref refs/heads/hook-side-effect HEAD\n")
    hook.chmod(0o700)

    first = RhizomeApp(repos.local)
    async with first.run_test(size=(120, 38)) as pilot:
        await settle(first, pilot)
        assert await pilot.click("#fetch")
        await settle(first, pilot)
        assert _oid(git_run, repos.local) == target
        assert first.query_one("#operation-banner").display
        assert first.query_one("#continue", Button).disabled
        assert first.query_one("#abort", Button).disabled

    restarted = RhizomeApp(repos.local)
    async with restarted.run_test(size=(80, 24)) as pilot:
        await settle(restarted, pilot)
        assert not restarted.snapshot_data.operation
        assert restarted.query_one("#operation-banner").display
        assert restarted.query_one("#continue", Button).disabled
        assert restarted.query_one("#abort", Button).disabled
        await pilot.press("ctrl+l")
        await pilot.pause()
        detail = restarted.screen.query_one("#activity-detail-text", Static).renderable
        assert "Retained pull ownership" in str(detail)
        assert "PID/PGID" in str(detail)
        assert "rhizome-pull-lease.json" in str(detail)
        scroll = restarted.screen.query_one("#activity-detail", VerticalScroll)
        scroll.focus()
        await pilot.press("end")
        await pilot.pause()
        assert scroll.scroll_y > 0
        await pilot.press("escape")
        await pilot.press("f4")
        await pilot.pause()
        exclusion = restarted.screen.query_one("#toggle-exclusion", Button)
        exclusion.focus()
        await pilot.pause()
        assert await pilot.click(exclusion)
        await pilot.pause()
        await pilot.press("enter")
        await settle(restarted, pilot)
        assert WorkspaceStateStore().load("global")["excluded_workspaces"] == []
        assert restarted.query_one("#operation-banner").display
        assert _oid(git_run, repos.local) == target


async def test_external_rebase_stays_gated_by_operation_banner(
    pull_repositories: PullRepositories, git_run
) -> None:
    _disable_automatic_fetch()
    repos = pull_repositories
    original = _commit(git_run, repos.local, "local\n", "Local conflict")
    _commit(git_run, repos.writer, "remote\n", "Remote conflict")
    git_run(repos.writer, "push", "origin", "main")
    git_run(repos.local, "fetch", "origin")
    result = git_run(repos.local, "rebase", "origin/main", check=False)
    assert result.returncode != 0

    app = RhizomeApp(repos.local)
    async with app.run_test(size=(120, 38)) as pilot:
        await settle(app, pilot)
        assert app.query_one("#operation-banner").display
        assert app.query_one("#continue", Button).disabled
        assert app.query_one("#abort", Button).disabled
        assert "External operation" in app.query_one("#operation-text").renderable.plain
        assert len(app.screen_stack) == 1
        assert _oid(git_run, repos.local, "REBASE_HEAD") == original


async def test_pull_recovery_journal_does_not_enable_discard(
    pull_repositories: PullRepositories, git_run
) -> None:
    _disable_automatic_fetch()
    repos = pull_repositories
    (repos.local / "shared.txt").write_text("private edit\n")

    app = RhizomeApp(repos.local)
    async with app.run_test(size=(120, 38)) as pilot:
        await settle(app, pilot)
        changed = next(item for item in app.snapshot_data.files if item.path == "shared.txt")
        app.inspect_worktree_file(changed, False)
        assert await pilot.click("#discard")
        await settle(app, pilot)
        assert "unavailable without a durable recovery checkpoint" in app._last_error
        assert (repos.local / "shared.txt").read_text() == "private edit\n"


@pytest.mark.parametrize("operation", ["fetch", "pull"])
async def test_offline_command_actions_do_not_transport(
    pull_repositories: PullRepositories, git_run, operation: str
) -> None:
    _save_global_settings(offline=True, remote_refresh_enabled=False)
    repos = pull_repositories
    original = _oid(git_run, repos.local)
    tracking = _oid(git_run, repos.local, "refs/remotes/origin/main")
    target = _commit(git_run, repos.writer, "remote\n", "Remote advance")
    git_run(repos.writer, "push", "origin", "main")

    app = RhizomeApp(repos.local)
    async with app.run_test(size=(120, 38)) as pilot:
        await settle(app, pilot)
        assert app.query_one("#fetch", Button).disabled
        assert await pilot.click("#actions")
        options = app.screen.query_one(OptionList)
        options.highlighted = options.get_option_index(operation)
        await pilot.press("enter")
        await settle(app, pilot)

        assert "Offline mode" in app._status_message
        assert _oid(git_run, repos.local) == original
        assert _oid(git_run, repos.local, "refs/remotes/origin/main") == tracking != target
