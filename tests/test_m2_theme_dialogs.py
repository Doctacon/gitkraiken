import pytest
from test_app import settle
from test_theme import contrast, luminance
from textual.widgets import DataTable

from rhizome.app import RhizomeApp
from rhizome.persistence import WorkspaceStateStore


@pytest.mark.parametrize("theme", ["dark", "high-contrast"])
async def test_workspace_preferences_dialog_uses_selected_dark_surface(git_repo, theme):
    WorkspaceStateStore().save("global", {"theme": theme})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await pilot.press("f4")
        await pilot.pause()
        dialog = app.screen.query_one("#workspace-controls-dialog")
        assert luminance(dialog.styles.background.hex) < 0.15
        await pilot.click("#controls-close")
        await pilot.pause()
        assert len(app.screen_stack) == 1


@pytest.mark.parametrize("theme", ["dark", "high-contrast"])
async def test_dark_activity_and_working_change_status_have_readable_surfaces(
    git_repo, git_run, theme
):
    path = git_repo / "colors.txt"
    path.write_text("before\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("after\n")
    WorkspaceStateStore().save("global", {"theme": theme})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert luminance(app.screen.query_one("#activity-dialog").styles.background.hex) < 0.15
        await pilot.press("escape")
        await pilot.press("f2")
        await settle(app, pilot)
        table = app.query_one("#wip-table", DataTable)
        state = table.get_row_at(0)[0]
        foreground = state.get_style_at_offset(app.console, 0).color.get_truecolor().hex
        for background in (
            table.styles.background.hex,
            table.get_component_styles("datatable--cursor").background.hex,
        ):
            assert contrast(foreground, background) >= 4.5
        table.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("v", "down")
        lines = app.query_one("#precision-lines", DataTable)
        for row in range(lines.row_count):
            cell = lines.get_row_at(row)[0]
            style = cell.get_style_at_offset(app.console, 0)
            if style.color:
                background = (
                    style.bgcolor.get_truecolor().hex
                    if style.bgcolor
                    else lines.styles.background.hex
                )
                assert contrast(style.color.get_truecolor().hex, background) >= 4.5
