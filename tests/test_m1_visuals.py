"""Focused M1 visual affordance checks using the real application workspace."""

import pytest
from textual.widgets import Button, DataTable, Select, Static, Switch

from rhizome.app import RhizomeApp
from rhizome.persistence import WorkspaceStateStore
from rhizome.workspace_controls import DiagnosticsDialog, WorkspaceControlsDialog


@pytest.mark.parametrize("plain", [False, True])
async def test_narrow_toolbar_labels_and_workspace_shortcut_are_not_clipped(git_repo, plain):
    WorkspaceStateStore().save("global", {"plain_glyphs": plain})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        for button in app.query("#workspace-nav Button, #toolbar Button"):
            if button.display:
                assert button.region.right <= 80, button.id
                assert button.content_size.width >= button.label.cell_len + 2, button.id
        await pilot.click("#workspace-settings")
        await pilot.pause()
        assert isinstance(app.screen, WorkspaceControlsDialog)


async def test_precision_range_has_explicit_textual_selection_feedback(git_repo, git_run):
    path = git_repo / "notes.txt"
    path.write_text("one\ntwo\nthree\n")
    git_run(git_repo, "add", "notes.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("one\nTWO\nthree\n")

    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.click("#show-files")
        app.query_one("#wip-table", DataTable).focus()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.press("v", "down")

        status = app.query_one("#precision-status", Static)
        assert "SELECTION:" in str(status.renderable)
        lines = app.query_one("#precision-lines", DataTable)
        assert lines.get_component_styles("datatable--cursor").text_style.underline


async def test_f4_controls_expose_all_actions_and_diagnostics_by_keyboard(git_repo):
    app = RhizomeApp(git_repo)
    action_ids = {
        "reset-overrides",
        "clear-drafts",
        "reset-view",
        "forget-workspace",
        "toggle-exclusion",
        "diagnostics",
        "reset-global",
    }
    async with app.run_test(size=(80, 24)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("f4")
        await pilot.pause()

        dialog = app.screen
        assert isinstance(dialog, WorkspaceControlsDialog)
        assert dialog.query_one("#controls-body").display
        assert dialog.query_one("#controls-apply").display
        assert "session-only" in str(dialog.query_one(".dialog-detail", Static).renderable).lower()
        assert all(dialog.query_one(f"#{action_id}", Button).display for action_id in action_ids)

        # The scroll region's tab sequence reaches every action at 80x24,
        # including controls currently below the short viewport.
        focused_ids: set[str] = set()
        for _ in range(16):
            if app.focused and app.focused.id:
                focused_ids.add(str(app.focused.id))
            await pilot.press("tab")
        assert action_ids <= focused_ids

        dialog.query_one("#diagnostics", Button).focus()
        await pilot.press("enter")
        await pilot.pause()
        diagnostics = app.screen
        assert isinstance(diagnostics, DiagnosticsDialog)
        assert (
            str(diagnostics.query_one("#diagnostics-clear", Button).label)
            == "Clear local diagnostics"
        )
        assert diagnostics.query_one("#diagnostics-export", Button).display
        await pilot.press("escape")

        # Error previews remain readable but cannot be exported.
        app.push_screen(
            DiagnosticsDialog(preview="[]", summary="Unavailable", error="log read failed")
        )
        await pilot.pause()
        error_dialog = app.screen
        assert isinstance(error_dialog, DiagnosticsDialog)
        assert error_dialog.query_one("#diagnostics-export", Button).disabled
        await pilot.press("escape")

        # A global offline setting is displayed as the effective workspace
        # value and locked there; changing the global scope remains available.
        app.push_screen(
            WorkspaceControlsDialog(
                global_settings={
                    "offline": True,
                    "remote_refresh_enabled": True,
                    "remote_refresh_interval": 60,
                    "plain_glyphs": False,
                    "diagnostics_enabled": True,
                },
                overrides={"offline": False},
                excluded=True,
            )
        )
        await pilot.pause()
        offline_dialog = app.screen
        assert isinstance(offline_dialog, WorkspaceControlsDialog)
        assert offline_dialog.query_one("#offline", Switch).value
        assert offline_dialog.query_one("#offline", Switch).disabled
        assert "cannot bypass" in str(
            offline_dialog.query_one("#controls-offline-policy", Static).renderable
        )
        scope = offline_dialog.query_one("#controls-scope", Select)
        scope.value = "global"
        await pilot.pause()
        assert not offline_dialog.query_one("#offline", Switch).disabled
        await pilot.press("escape")
