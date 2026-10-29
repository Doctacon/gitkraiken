"""Durable workspace behavior through the application and real Git repositories."""

import os
import subprocess
from pathlib import Path

from test_app import choose_option, click_reference, settle
from textual.widgets import Button, Input, Static, TabbedContent, TextArea

from rhizome.app import RhizomeApp
from rhizome.persistence import WorkspaceStateStore


async def test_offline_preference_prevents_automatic_remote_fetch(
    git_repo, bare_remote, git_run, monkeypatch
):
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    WorkspaceStateStore().save("global", {"offline": True})
    calls = []
    run = subprocess.run

    def observe_network(command, *args, **kwargs):
        if "fetch" in command:
            calls.append(command)
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", observe_network)
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert not calls


async def test_graph_style_preference_survives_application_restart(git_repo):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.click("#graph-style")
        assert str(app.query_one("#graph-style", Button).label) == "Compact [g]"

    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await reopened.workers.wait_for_complete()
        await pilot.pause()
        assert str(reopened.query_one("#graph-style", Button).label) == "Compact [g]"


async def test_offline_mode_allows_pr_drafting_but_refuses_publication(
    git_repo, git_run, monkeypatch
):
    WorkspaceStateStore().save("global", {"offline": True})
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "remote", "add", "origin", "https://github.com/example/project.git")
    network = []
    run = subprocess.run

    def observe(command, *args, **kwargs):
        if "ls-remote" in command or command[0] in {"gh", "az"}:
            network.append(command)
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", observe)
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/main", button=3)
        await choose_option(app, pilot, "create-pr")
        await settle(app, pilot)
        app.screen.query_one("#pr-title", Input).value = "Keep this offline draft"
        await pilot.click("#pr-submit")
        await settle(app, pilot)
        assert not network
        assert "Offline" in str(app.screen.query_one("#pr-feedback", Static).renderable)
        assert app.screen.query_one("#pr-title", Input).value == "Keep this offline draft"


async def test_pull_request_draft_survives_cancel_and_application_restart(git_repo, git_run):
    WorkspaceStateStore().save("global", {"remote_refresh_enabled": False})
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "commit", "--allow-empty", "-m", "feature")
    git_run(git_repo, "remote", "add", "origin", "https://github.com/example/project.git")
    title, body = "Review these changes", "Context\n\nA durable PR draft."
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/main", button=3)
        await choose_option(app, pilot, "create-pr")
        await settle(app, pilot)
        app.screen.query_one("#pr-title", Input).value = title
        app.screen.query_one("#pr-description", TextArea).load_text(body)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await settle(reopened, pilot)
        await click_reference(reopened, pilot, "refs/heads/main", button=3)
        await choose_option(reopened, pilot, "create-pr")
        await settle(reopened, pilot)
        assert reopened.screen.query_one("#pr-title", Input).value == title
        assert reopened.screen.query_one("#pr-description", TextArea).text == body


async def test_pull_request_drafts_keep_distinct_target_intents(git_repo, git_run):
    WorkspaceStateStore().save("global", {"remote_refresh_enabled": False})
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    git_run(git_repo, "branch", "release")
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "remote", "add", "origin", "https://github.com/example/project.git")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        for target, title in (("main", "Merge the feature"), ("release", "Backport the fix")):
            await click_reference(app, pilot, f"refs/heads/{target}", button=3)
            await choose_option(app, pilot, "create-pr")
            await settle(app, pilot)
            assert app.screen.query_one("#pr-title", Input).value == ""
            assert app.screen.query_one("#pr-description", TextArea).text == ""
            app.screen.query_one("#pr-title", Input).value = title
            app.screen.query_one("#pr-description", TextArea).load_text(f"Only for {target}.")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await settle(reopened, pilot)
        for target, title in (("release", "Backport the fix"), ("main", "Merge the feature")):
            await click_reference(reopened, pilot, f"refs/heads/{target}", button=3)
            await choose_option(reopened, pilot, "create-pr")
            await settle(reopened, pilot)
            assert reopened.screen.query_one("#pr-title", Input).value == title
            assert (
                reopened.screen.query_one("#pr-description", TextArea).text == f"Only for {target}."
            )
            await pilot.press("escape")
            await pilot.pause()


async def test_failed_draft_write_is_visible_and_preserves_last_saved_text(git_repo, monkeypatch):
    store = WorkspaceStateStore()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        await pilot.press("f3")
        editor = app.query_one("#commit-message", TextArea)
        editor.load_text("Safely saved draft")
        await pilot.pause()
        saved = {path: path.read_bytes() for path in store.root.rglob("*.json")}
        assert saved
        replace = os.replace

        def full_disk(source, destination, *args, **kwargs):
            if Path(destination).is_relative_to(store.root):
                raise OSError("Injected storage failure")
            return replace(source, destination, *args, **kwargs)

        with monkeypatch.context() as failure:
            failure.setattr(os, "replace", full_disk)
            editor.load_text("Still visible, not saved")
            await pilot.pause()
            assert editor.text == "Still visible, not saved"
            assert "not saved" in str(app.query_one("#draft-status", Static).renderable).lower()
            assert {path: path.read_bytes() for path in saved} == saved

    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await settle(reopened, pilot)
        assert reopened.query_one("#commit-message", TextArea).text == "Safely saved draft"


async def test_corrupt_draft_is_reported_and_never_silently_replaced(git_repo):
    store = WorkspaceStateStore()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        await pilot.press("f3")
        app.query_one("#commit-message", TextArea).load_text("Original private draft")
        await pilot.pause()
    documents = [path for path in store.root.rglob("*.json") if path != store.settings_path]
    assert len(documents) == 1
    documents[0].write_bytes(b"{corrupt fixture")
    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await settle(reopened, pilot)
        assert "not saved" in str(reopened.query_one("#draft-status", Static).renderable).lower()
        await pilot.press("f3")
        reopened.query_one("#commit-message", TextArea).load_text(
            "Do not overwrite unknown saved content"
        )
        await pilot.pause()
        assert documents[0].read_bytes() == b"{corrupt fixture"


async def test_failed_hook_retains_draft_until_a_successful_commit(git_repo, git_run):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    head = git_run(git_repo, "rev-parse", "HEAD").stdout
    path.write_text("reviewed\n")
    git_run(git_repo, "add", "example.txt")
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nprintf 'Fixture hook refused commit\\n' >&2\nexit 1\n")
    hook.chmod(0o755)
    draft = "Keep the reviewed change\n\nA body that must survive hook failure."
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        await pilot.press("f3")
        app.query_one("#commit-message", TextArea).load_text(draft)
        await pilot.click("#commit")
        await settle(app, pilot)
        assert app.query_one("#commit-message", TextArea).text == draft
        assert "Fixture hook refused" in app.query_one("#status-line", Static).renderable.plain
        assert git_run(git_repo, "rev-parse", "HEAD").stdout == head

    hook.unlink()
    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await settle(reopened, pilot)
        await pilot.press("f3")
        assert reopened.query_one("#commit-message", TextArea).text == draft
        await pilot.click("#commit")
        await settle(reopened, pilot)
        assert git_run(git_repo, "log", "-1", "--format=%B").stdout.strip() == draft
        assert reopened.query_one("#commit-message", TextArea).text == ""

    completed = RhizomeApp(git_repo)
    async with completed.run_test(size=(170, 48)) as pilot:
        await settle(completed, pilot)
        assert completed.query_one("#commit-message", TextArea).text == ""


async def test_switching_linked_workspaces_keeps_separate_drafts(git_repo, git_run, tmp_path):
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    linked = tmp_path / "linked"
    git_run(git_repo, "worktree", "add", "-b", "linked", str(linked))
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        await pilot.press("f3")
        app.query_one("#commit-message", TextArea).load_text("Main worktree draft")
        await pilot.pause()
        await pilot.press("ctrl+o")
        app.screen.query_one(Input).value = str(linked)
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.query_one("#commit-message", TextArea).text == ""
        await pilot.press("f3")
        app.query_one("#commit-message", TextArea).load_text("Linked worktree draft")
        await pilot.pause()
        await pilot.press("ctrl+o")
        app.screen.query_one(Input).value = str(git_repo)
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.query_one("#commit-message", TextArea).text == "Main worktree draft"

    reopened = RhizomeApp(linked)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await settle(reopened, pilot)
        assert reopened.query_one("#commit-message", TextArea).text == "Linked worktree draft"


async def test_recreated_repository_does_not_inherit_the_old_draft(git_repo, git_run, tmp_path):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        await pilot.press("f3")
        app.query_one("#commit-message", TextArea).load_text("Belongs to the original repository")
        await pilot.pause()
    # Retain the original administrative directory rather than deleting content;
    # the replacement is a genuinely new Git identity at the same checkout path.
    (git_repo / ".git").rename(tmp_path / "original-git-directory")
    git_run(git_repo, "init", "-b", "main")
    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await settle(reopened, pilot)
        assert reopened.query_one("#commit-message", TextArea).text == ""


async def test_commit_draft_survives_application_restart(git_repo):
    draft = "Keep the reviewed change\n\nExplain why it belongs in this commit."
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
        app.query_one("#commit-message", TextArea).load_text(draft)
        await pilot.pause()

    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await reopened.workers.wait_for_complete()
        await pilot.pause()
        assert reopened.query_one("#commit-message", TextArea).text == draft
