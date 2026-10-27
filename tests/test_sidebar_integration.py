"""Real-repository integration for sidebar worktrees and automatic hosted reads."""

import threading
from pathlib import Path

import pytest
from test_app import choose_option, settle
from test_sidebar import hit_point
from textual.widgets import Input, Static, TextArea
from ui_helpers import click

from gitkraiken.app import GitKraikenApp
from gitkraiken.dialogs import ActionPicker, PromptDialog
from gitkraiken.git import Repository
from gitkraiken.pull_request_dialog import PullRequestDetailsDialog
from gitkraiken.pull_requests import PullRequest, PullRequestListing, hosted_repository
from gitkraiken.sidebar import ReferenceSidebar


@pytest.fixture
def workspace(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "Initial")
    return git_repo


def selected(sidebar, kind, target):
    return next(
        item for item in sidebar._leaves.values() if item.kind == kind and item.target == target
    )


def listing():
    return PullRequestListing(
        "origin",
        hosted_repository("https://github.com/org/repo"),
        (
            PullRequest(
                42,
                "Fix [literal] navigation",
                "https://github.com/org/repo/pull/42",
                "feature/sidebar",
                "main",
                "octocat",
                True,
            ),
        ),
    )


@pytest.mark.parametrize("directory", ["another tree", "trailing newline\n"])
async def test_open_linked_worktree_without_modifying_either_checkout(
    workspace, git_run, tmp_path, directory
):
    linked = tmp_path / directory
    git_run(workspace, "worktree", "add", "-b", "feature/linked", str(linked))
    (workspace / "local.txt").write_text("keep these untracked changes\n")
    app = GitKraikenApp(workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        item = selected(sidebar, "linked-worktree", str(linked))
        tree, point = await hit_point(sidebar, pilot, item.key)
        await click(pilot, tree, point)
        assert await pilot.click("#sidebar-worktree-open")
        await settle(app, pilot)
        assert app.repo.root == linked
        assert app.snapshot_data.branch == "feature/linked"
        assert any(tree.current and tree.path == linked for tree in app.snapshot_data.worktrees)
        assert (workspace / "local.txt").read_text() == "keep these untracked changes\n"
        assert git_run(workspace, "branch", "--show-current").stdout.strip() == "main"
        assert git_run(linked, "status", "--porcelain").stdout == ""


async def test_open_current_worktree_keeps_commit_draft(workspace):
    app = GitKraikenApp(workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        composer = app.query_one("#commit-message", TextArea)
        composer.load_text("Do not lose this draft")
        item = selected(sidebar, "linked-worktree", str(workspace))
        sidebar.post_message(ReferenceSidebar.WorktreeOpenRequested(item, workspace))
        await settle(app, pilot)
        assert composer.text == "Do not lose this draft"
        assert app.repo.root == workspace
        assert "Already open" in app.query_one("#status-line", Static).renderable.plain


async def test_removed_worktree_cannot_open_an_obsolete_path(workspace, git_run, tmp_path):
    linked = tmp_path / "linked"
    git_run(workspace, "worktree", "add", "--detach", str(linked))
    app = GitKraikenApp(workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        item = selected(sidebar, "linked-worktree", str(linked))
        git_run(workspace, "worktree", "remove", str(linked))
        sidebar.post_message(ReferenceSidebar.WorktreeOpenRequested(item, workspace))
        await settle(app, pilot)
        assert app.repo.root == workspace
        assert "no longer available" in app._last_error


async def test_create_worktree_from_sidebar_uses_guarded_head_flow(workspace, git_run, tmp_path):
    destination = tmp_path / "new tree"
    app = GitKraikenApp(workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert await pilot.click("#sidebar-worktree-new")
        await pilot.pause()
        assert isinstance(app.screen, ActionPicker)
        await choose_option(app, pilot, "new-branch")
        assert isinstance(app.screen, PromptDialog)
        app.screen.query_one(Input).value = "feature/sidebar"
        await pilot.press("enter")
        app.screen.query_one(Input).value = str(destination)
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.repo.root == workspace
        assert app.snapshot_data.branch == "main"
        tree = next(item for item in app.snapshot_data.worktrees if item.path == destination)
        assert tree.branch == "feature/sidebar"
        assert tree.head == app.snapshot_data.head
        assert git_run(destination, "status", "--porcelain").stdout == ""


async def test_pull_requests_load_automatically_and_open_read_only_details(
    workspace, git_run, monkeypatch
):
    git_run(workspace, "remote", "add", "origin", "https://github.com/org/repo")
    calls = []

    def load(repo):
        calls.append(repo.root)
        return (listing(),)

    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        app.poll_repository()
        await settle(app, pilot)
        assert calls == [workspace]
        sidebar = app.query_one(ReferenceSidebar)
        tree, point = await hit_point(sidebar, pilot, "group:prs", caret=True)
        await click(pilot, tree, point)
        assert sidebar._tree_nodes["group:prs"].is_collapsed
        tree, point = await hit_point(sidebar, pilot, "group:prs")
        await click(pilot, tree, point)
        assert sidebar._tree_nodes["group:prs"].is_expanded
        assert calls == [workspace]
        assert await pilot.click("#sidebar-prs-load")
        await settle(app, pilot)
        assert calls == [workspace, workspace]
        pr = listing().requests[0]
        item = selected(sidebar, "pull-request", pr.url)
        app.set_busy(True)
        sidebar.post_message(ReferenceSidebar.SelectionChanged(item, workspace))
        await pilot.pause()
        assert len(app.screen_stack) == 1  # A PR modal must not interrupt a Git operation.
        app.set_busy(False)
        tree, point = await hit_point(sidebar, pilot, item.key)
        await click(pilot, tree, point)
        await pilot.pause()
        assert isinstance(app.screen, PullRequestDetailsDialog)
        assert app.screen.request == pr
        await pilot.press("escape")
        app.poll_repository()
        await settle(app, pilot)
        assert calls == [workspace, workspace]
        assert selected(sidebar, "pull-request", pr.url) == item
        assert git_run(workspace, "branch", "--show-current").stdout.strip() == "main"


async def test_pr_loading_rejects_duplicates_and_survives_help_modal(
    workspace, git_run, monkeypatch
):
    git_run(workspace, "remote", "add", "origin", "https://github.com/org/repo")
    release = threading.Event()
    calls = []

    def load(repo):
        calls.append(repo.root)
        if len(calls) > 1:
            assert release.wait(10)
        return (listing(),)

    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        try:
            sidebar.post_message(ReferenceSidebar.PullRequestsRequested(workspace))
            await pilot.pause()
            assert app.busy
            sidebar.post_message(ReferenceSidebar.PullRequestsRequested(workspace))
            app.action_help()
            await pilot.pause()
        finally:
            release.set()
        await settle(app, pilot)
        assert calls == [workspace, workspace]
        assert app._last_error == ""
        await pilot.press("escape")
        assert selected(sidebar, "pull-request", listing().requests[0].url)


async def test_changed_remote_during_listing_discards_old_results(workspace, git_run, monkeypatch):
    git_run(workspace, "remote", "add", "origin", "https://github.com/org/repo")
    change_remote = [False]

    def load(repo):
        if change_remote[0]:
            git_run(workspace, "remote", "set-url", "origin", "https://github.com/org/other")
        return (listing(),)

    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        change_remote[0] = True
        sidebar.post_message(ReferenceSidebar.PullRequestsRequested(workspace))
        await settle(app, pilot)
        assert not any(item.kind == "pull-request" for item in sidebar._leaves.values())
        assert "Remotes changed" in app.query_one("#status-line", Static).renderable.plain


async def test_repository_switch_clears_prs_and_rejects_old_sidebar_requests(
    workspace, git_run, tmp_path, monkeypatch
):
    git_run(workspace, "remote", "add", "origin", "https://github.com/org/repo")
    other = tmp_path / "other"
    other.mkdir()
    git_run(other, "init", "-b", "main")
    calls = []

    def load(repo):
        calls.append(repo.root)
        return (listing(),) if repo.root == workspace else ()

    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        old_pr = selected(sidebar, "pull-request", listing().requests[0].url)
        old_tree = selected(sidebar, "linked-worktree", str(workspace))
        app.open_repository(str(other))
        await settle(app, pilot)
        assert not any(item.kind == "pull-request" for item in sidebar._leaves.values())
        for event in (
            ReferenceSidebar.PullRequestsRequested(workspace),
            ReferenceSidebar.WorktreeCreateRequested(workspace),
            ReferenceSidebar.WorktreeOpenRequested(old_tree, workspace),
            ReferenceSidebar.SelectionChanged(old_pr, workspace),
        ):
            sidebar.post_message(event)
        await pilot.pause()
        assert calls == [workspace, other]
        assert app.repo.root == other
        assert len(app.screen_stack) == 1


async def test_failed_pr_read_leaves_local_navigation_usable(workspace, monkeypatch):
    def load(repo):
        raise RuntimeError("A safe listing failure")

    monkeypatch.setattr(Repository, "open_pull_requests", load)
    app = GitKraikenApp(workspace)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        sidebar.post_message(ReferenceSidebar.PullRequestsRequested(workspace))
        await settle(app, pilot)
        assert not app.busy
        assert app._last_error == "A safe listing failure"
        assert sidebar.get_selection("local:refs/heads/main") is not None
        sidebar.post_message(ReferenceSidebar.PullRequestsRequested(Path("/stale")))
        await pilot.pause()
        assert not app.busy
