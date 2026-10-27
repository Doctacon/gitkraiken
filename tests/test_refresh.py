"""Automatic refresh stays current without flashing or resetting the workspace."""

import threading
from unittest.mock import Mock

import pytest
from test_app import settle
from textual import events
from textual.widgets import Button, Static

import gitkraiken.app as app_module
from gitkraiken.app import GitKraikenApp, HistoryTable
from gitkraiken.dialogs import ActionPicker
from gitkraiken.git import GitError, Repository
from gitkraiken.models import Action
from gitkraiken.pull_requests import PullRequest, PullRequestListing, hosted_repository
from gitkraiken.sidebar import ReferenceSidebar


@pytest.fixture
def listings():
    return (
        PullRequestListing(
            "origin",
            hosted_repository("https://github.com/org/repo"),
            (PullRequest(42, "Fix", "https://github.com/org/repo/pull/42", "topic", "main"),),
        ),
    )


async def test_local_poll_keeps_controls_and_unchanged_graph_stable(git_repo):
    app = GitKraikenApp(git_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        table = app.query_one(HistoryTable)
        revision = table.revision
        controls = {button.id: button.disabled for button in app.query(Button)}
        app.poll_repository()
        assert {button.id: button.disabled for button in app.query(Button)} == controls
        await settle(app, pilot)
        assert table.revision == revision


async def test_pull_requests_load_on_open_without_a_click(git_repo, git_run, monkeypatch, listings):
    git_run(git_repo, "remote", "add", "origin", "https://github.com/org/repo")
    load = Mock(return_value=listings)
    monkeypatch.setattr(Repository, "open_pull_requests", load)
    monkeypatch.setattr(Repository, "execute", Mock(return_value=""))
    app = GitKraikenApp(git_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        load.assert_called_once()
        assert app.query_one(ReferenceSidebar)._pull_request_listings == listings


async def test_open_fetches_remote_commits_without_pulling(
    git_repo, git_run, bare_remote, tmp_path
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "Base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "-u", "origin", "main")
    head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    peer = tmp_path / "peer"
    git_run(tmp_path, "clone", str(bare_remote), str(peer))
    git_run(peer, "commit", "--allow-empty", "-m", "Remote update")
    git_run(peer, "push")
    app = GitKraikenApp(git_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert app.snapshot_data.behind == 1
        assert app.snapshot_data.head == head
        assert git_run(git_repo, "status", "--porcelain").stdout == ""


@pytest.fixture
def remote_workspace(git_repo, git_run, bare_remote):
    git_run(git_repo, "commit", "--allow-empty", "-m", "Base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "-u", "origin", "main")
    return git_repo


async def test_remote_poll_cadence_and_resume_refresh(remote_workspace, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(app_module, "monotonic", lambda: clock[0])
    load = Mock(return_value=())
    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(remote_workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        load.assert_called_once()
        clock[0] += 29
        app.poll_repository()
        await settle(app, pilot)
        load.assert_called_once()
        clock[0] += 1
        app.poll_repository()
        await settle(app, pilot)
        assert load.call_count == 2
        clock[0] += 5
        app.post_message(events.AppFocus())
        await settle(app, pilot)
        assert load.call_count == 3  # resume does not wait for the 30-second timer
        app.post_message(events.AppFocus())
        await settle(app, pilot)
        assert load.call_count == 3  # duplicate focus notification is coalesced


async def wait_started(started, pilot):
    for _ in range(100):
        if started.is_set():
            return
        await pilot.pause(0.01)
    raise AssertionError("Background read never started")


async def test_background_fetch_keeps_cached_ui_and_does_not_disturb_modal(
    remote_workspace, monkeypatch, listings
):
    monkeypatch.setattr(Repository, "open_pull_requests", Mock(return_value=listings))
    app = GitKraikenApp(remote_workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        nodes = sidebar._tree_nodes.copy()
        table = app.query_one(HistoryTable)
        revision = table.revision
        started, release = threading.Event(), threading.Event()
        execute = app.repo.execute
        calls = []

        def delayed(action):
            calls.append(action.operation)
            started.set()
            assert release.wait(10)
            return execute(action)

        monkeypatch.setattr(app.repo, "execute", delayed)
        try:
            app._next_remote_refresh = 0
            app.poll_repository()
            await wait_started(started, pilot)
            assert not app.busy
            assert not app.query_one("#branch", Button).disabled
            assert sidebar._pull_request_listings == listings
            assert sidebar._tree_nodes == nodes
            app.poll_repository()
            app.poll_repository()
            app.action_command_picker()
            await pilot.pause()
            menu = app.screen
            assert isinstance(menu, ActionPicker)
        finally:
            release.set()
        await settle(app, pilot)
        assert calls == ["fetch"]
        assert app.screen is menu
        assert sidebar._tree_nodes == nodes
        assert table.revision == revision
        assert app._last_error == ""


async def test_mutation_waits_for_background_git_without_overlap(remote_workspace, monkeypatch):
    app = GitKraikenApp(remote_workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        started, release = threading.Event(), threading.Event()
        execute = app.repo.execute
        calls = []

        def delayed(action):
            calls.append(action.operation)
            if action.operation == "fetch":
                started.set()
                assert release.wait(10)
                calls.append("fetch-finished")
            return execute(action)

        monkeypatch.setattr(app.repo, "execute", delayed)
        try:
            app._next_remote_refresh = 0
            app.poll_repository()
            await wait_started(started, pilot)
            app.run_mutation(Action("create-branch", name="topic"))
            await pilot.pause()
            assert app.busy
            assert calls == ["fetch"]
        finally:
            release.set()
        await settle(app, pilot)
        assert calls == ["fetch", "fetch-finished", "create-branch"]
        assert any(ref.name == "topic" for ref in app.snapshot_data.refs)
        assert not app.busy


async def test_failed_fetch_still_loads_prs_and_backs_off(remote_workspace, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(app_module, "monotonic", lambda: clock[0])
    fetch = Mock(side_effect=GitError("Offline"))
    load = Mock(return_value=())
    monkeypatch.setattr(Repository, "execute", fetch)
    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(remote_workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        fetch.assert_called_once_with(Action("fetch"))
        load.assert_called_once()
        assert not app.busy
        assert "Offline" in app.query_one("#status-line", Static).renderable.plain
        clock[0] += 2
        app.poll_repository()
        await settle(app, pilot)
        fetch.assert_called_once()
        clock[0] += 30
        app.poll_repository()
        await settle(app, pilot)
        assert fetch.call_count == 2
        fetch.side_effect = None
        fetch.return_value = ""
        clock[0] += 30
        app.poll_repository()
        await settle(app, pilot)
        assert "Offline" not in app.query_one("#status-line", Static).renderable.plain


async def test_focus_refresh_waits_until_dialog_closes(remote_workspace, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(app_module, "monotonic", lambda: clock[0])
    load = Mock(return_value=())
    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(remote_workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        app.action_help()
        await pilot.pause()
        clock[0] += 10
        app.post_message(events.AppFocus())
        await pilot.pause()
        load.assert_called_once()
        await pilot.press("escape")
        app.poll_repository()
        await settle(app, pilot)
        assert load.call_count == 2


async def test_failed_listing_preserves_cache_and_action_result(
    remote_workspace, monkeypatch, listings
):
    load = Mock(return_value=listings)
    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(remote_workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        nodes = sidebar._tree_nodes.copy()
        app.status("Pull request created: https://github.com/org/repo/pull/43")
        load.side_effect = RuntimeError("Hosted service unavailable")
        app._next_remote_refresh = 0
        app.poll_repository()
        await settle(app, pilot)
        assert sidebar._pull_request_listings == listings
        assert sidebar._tree_nodes == nodes
        status = app.query_one("#status-line", Static).renderable.plain
        assert "https://github.com/org/repo/pull/43" in status
        assert "Hosted service unavailable" in status
        assert not sidebar._pull_requests_loading
        assert app._last_error == ""  # no intrusive background error toast


async def test_closed_pull_request_selection_is_reconciled(remote_workspace, monkeypatch, listings):
    load = Mock(return_value=listings)
    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(remote_workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        selection = sidebar.get_selection(listings[0].requests[0].url)
        app._sidebar_selection = selection
        sidebar.set_active(selection)
        load.return_value = ()
        app._next_remote_refresh = 0
        app.poll_repository()
        await settle(app, pilot)
        assert app._sidebar_selection is None
        assert sidebar._active_key is None


async def test_remote_change_during_automatic_read_discards_and_retries(
    remote_workspace, git_run, monkeypatch, listings
):
    clock = [100.0]
    monkeypatch.setattr(app_module, "monotonic", lambda: clock[0])
    load = Mock(return_value=listings)
    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(remote_workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)

        def change_remote():
            git_run(remote_workspace, "remote", "rename", "origin", "renamed")
            return listings

        load.side_effect = change_remote
        app._next_remote_refresh = 0
        app.poll_repository()
        await settle(app, pilot)
        assert sidebar._pull_request_listings is None
        assert not sidebar._pull_requests_loading
        assert "Remotes changed" in app.query_one("#status-line", Static).renderable.plain
        load.side_effect = None
        load.return_value = ()
        app.poll_repository()
        await settle(app, pilot)
        assert load.call_count == 3  # retry immediately, not after the old remote's interval
        assert sidebar._pull_request_listings == ()
