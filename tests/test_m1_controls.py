"""M1 controls exercised through the actual app and durable store."""

import json

from textual.widgets import Button, DataTable, Input, Static, Switch, TextArea

from rhizome.app import RhizomeApp
from rhizome.dialogs import ActionPicker
from rhizome.persistence import WorkspaceStateStore
from rhizome.workspace_controls import DiagnosticsDialog


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


async def control(app, pilot, name):
    await pilot.press("f4")
    await pilot.pause()
    button = app.screen.query_one(f"#{name}", Button)
    button.focus()
    await pilot.press("enter")
    await pilot.pause()


async def test_diagnostics_exports_exact_captured_document_not_newer_records(git_repo, tmp_path):
    store = WorkspaceStateStore()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await control(app, pilot, "diagnostics")
        assert isinstance(app.screen, DiagnosticsDialog)
        preview = str(app.screen.query_one("#diagnostics-preview").query_one(Static).renderable)
        document = json.loads(preview)
        assert document["policy"]["telemetry"] is False
        assert document["diagnostics"]
        store.record_diagnostic("arrived.after.preview", {"status": "ok"})
        await pilot.click("#diagnostics-export")
        destination = tmp_path / "export.json"
        app.screen.query_one(Input).value = str(destination)
        await pilot.press("enter")
        await settle(app, pilot)
        assert destination.read_bytes() == preview.encode("utf-8")
        assert b"arrived.after.preview" not in destination.read_bytes()
        assert store.diagnostics_preview()[-1]["event"] == "arrived.after.preview"


async def test_diagnostics_export_refuses_existing_destination(git_repo, tmp_path):
    destination = tmp_path / "keep.txt"
    destination.write_text("existing private content")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await control(app, pilot, "diagnostics")
        await pilot.click("#diagnostics-export")
        app.screen.query_one(Input).value = str(destination)
        await pilot.press("enter")
        await settle(app, pilot)
        assert destination.read_text() == "existing private content"
        assert "export" in str(app.query_one("#status-line", Static).renderable).lower()


async def test_plain_preference_preserves_live_selection_and_literal_unicode(git_repo, git_run):
    path = git_repo / "café.txt"
    path.write_text("before café\nsecond line\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("after café\nsecond line\n")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await pilot.press("f2")
        app.query_one("#wip-table", DataTable).focus()
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("v", "down")
        before = str(app.query_one("#precision-status", Static).renderable)
        assert "SELECTION:" in before
        index = (git_repo / ".git" / "index").read_bytes()
        await pilot.press("f4")
        await pilot.pause()
        app.screen.query_one("#plain-glyphs", Switch).value = True
        await pilot.click("#controls-apply")
        await settle(app, pilot)
        after = str(app.query_one("#precision-status", Static).renderable)
        assert after == before.replace(" · ", " | ")
        table = app.query_one("#precision-lines", DataTable)
        rows = [str(table.get_row_at(row)[0]) for row in range(table.row_count)]
        assert any("café" in row for row in rows)
        assert not any("−" in row for row in rows)
        assert (git_repo / ".git" / "index").read_bytes() == index
        assert path.read_text() == "after café\nsecond line\n"


async def test_global_reset_keeps_protection_exclusion_and_resets_curated_settings(git_repo):
    store = WorkspaceStateStore()
    store.save(
        "global",
        {
            "offline": True,
            "remote_refresh_interval": 42,
            "protected_branches": ["production"],
            "excluded_workspaces": ["a" * 64],
            "keymap": {"command_picker": "ctrl+alt+p"},
        },
    )
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await control(app, pilot, "reset-global")
        assert isinstance(app.screen, ActionPicker)
        await pilot.press("enter")
        await settle(app, pilot)
        settings = store.load("global")
        assert settings["offline"] is False
        assert settings["remote_refresh_interval"] == 60
        assert settings["protected_branches"] == ["production"]
        assert settings["excluded_workspaces"] == ["a" * 64]
        assert settings["keymap"] == {}


async def test_exclusion_removes_saved_state_keeps_session_text_and_never_resurrects(git_repo):
    store = WorkspaceStateStore()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        editor = app.query_one("#commit-message", TextArea)
        editor.load_text("Saved private draft")
        await pilot.pause()
        documents = list((store.root / "workspaces").glob("*.json"))
        assert len(documents) == 1
        key = documents[0].stem
        await control(app, pilot, "toggle-exclusion")
        await pilot.press("enter")
        await settle(app, pilot)
        assert editor.text == "Saved private draft"
        assert not documents[0].exists()
        assert key in store.load("global")["excluded_workspaces"]
        editor.load_text("Session-only private draft")
        await pilot.pause()
        assert "Session only" in str(app.query_one("#draft-status", Static).renderable)
        assert not documents[0].exists()
    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(80, 24)) as pilot:
        await settle(reopened, pilot)
        editor = reopened.query_one("#commit-message", TextArea)
        assert editor.text == ""
        editor.load_text("New session text")
        await pilot.pause()
        await control(reopened, pilot, "toggle-exclusion")
        await pilot.press("enter")
        await settle(reopened, pilot)
        assert key not in store.load("global")["excluded_workspaces"]
        assert store.load(key)["draft"] == "New session text"


async def test_other_instance_draft_is_not_overwritten_and_quit_requires_confirmation(git_repo):
    store = WorkspaceStateStore()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        editor = app.query_one("#commit-message", TextArea)
        editor.load_text("Original saved text")
        await pilot.pause()
        key = next((store.root / "workspaces").glob("*.json")).stem
        store.save(key, {**store.load(key), "draft": "Newer text from another window"})
        editor.load_text("Unsaved current-window text")
        await pilot.pause()
        assert store.load(key)["draft"] == "Newer text from another window"
        assert "not saved" in str(app.query_one("#draft-status", Static).renderable).lower()
        await pilot.press("f4")
        await pilot.pause()
        # Non-priority Quit is intentionally unavailable inside modals. Close
        # controls first, then require the unsaved-draft decision before exiting.
        await pilot.press("escape", "ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ActionPicker)
        assert app.is_running
        await pilot.press("escape")
        assert editor.text == "Unsaved current-window text"


async def test_restart_restores_navigation_without_replaying_git_or_patch_selection(
    git_repo, git_run
):
    (git_repo / "file.txt").write_text("base\n")
    git_run(git_repo, "add", "file.txt")
    git_run(git_repo, "commit", "-m", "base")
    head = git_run(git_repo, "rev-parse", "HEAD").stdout
    index = (git_repo / ".git" / "index").read_bytes()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        app.query_one("#graph-filter", Input).value = "base"
        app.query_one("#graph-table", DataTable).focus()
        await pilot.press("ctrl+left", "f3")
        assert app.query_one("#refs-panel").has_class("collapsed")
        app.query_one("#commit-message", TextArea).load_text("Retained draft")
        await pilot.pause()
        await pilot.press("ctrl+q")
    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(170, 48)) as pilot:
        await settle(reopened, pilot)
        assert reopened.query_one("#graph-filter", Input).value == "base"
        assert reopened.query_one("#refs-panel").has_class("collapsed")
        assert reopened.screen.has_class("task-composer")
        assert reopened.query_one("#commit-message", TextArea).text == "Retained draft"
        assert not reopened.query_one("#diff-workspace").display
        assert git_run(git_repo, "rev-parse", "HEAD").stdout == head
        assert (git_repo / ".git" / "index").read_bytes() == index


async def test_excluded_pull_request_draft_is_honestly_labeled_session_only(git_repo, git_run):
    from test_app import choose_option, click_reference

    store = WorkspaceStateStore()
    store.save("global", {"offline": True})
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "remote", "add", "origin", "https://github.com/example/project.git")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        await control(app, pilot, "toggle-exclusion")
        await pilot.press("enter")
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/main", button=3)
        await choose_option(app, pilot, "create-pr")
        await settle(app, pilot)
        app.screen.query_one("#pr-title", Input).value = "Session-only PR title"
        await pilot.pause()
        status = str(app.screen.query_one("#pr-draft-status", Static).renderable)
        assert "Session-only" in status
        assert "saved locally" not in status
        assert not list((store.root / "workspaces").glob("*.json"))


async def test_workspace_preference_change_does_not_persist_untouched_template(git_repo, git_run):
    (git_repo / "message-template").write_text("# Write a summary\n\n# Explain why\n")
    git_run(git_repo, "config", "commit.template", "message-template")
    store = WorkspaceStateStore()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        template = app.query_one("#commit-message", TextArea).text
        await pilot.press("f4")
        await pilot.pause()
        app.screen.query_one("#plain-glyphs", Switch).value = True
        await pilot.click("#controls-apply")
        await settle(app, pilot)
        assert app.query_one("#commit-message", TextArea).text == template
        documents = [
            json.loads(path.read_text())["data"]
            for path in (store.root / "workspaces").glob("*.json")
        ]
        assert len(documents) == 1
        assert not documents[0].get("draft")
        assert documents[0]["preferences"]["plain_glyphs"] is True
        assert "Template loaded" in str(app.query_one("#draft-status", Static).renderable)
