"""Utility dialogs scale with the terminal; their content and footer remain usable."""

from datetime import UTC, datetime

import pytest
from rich.cells import cell_len
from test_app import settle
from textual.widgets import Button, Input, OptionList, Select, Static

from rhizome.activity import ActivityDialog
from rhizome.app import RhizomeApp
from rhizome.operations import OperationActivity
from rhizome.workspace_controls import WorkspaceControlsDialog


@pytest.mark.parametrize("size", [(270, 80), (170, 48), (100, 32), (80, 24)])
@pytest.mark.parametrize(
    ("shortcut", "frame_id", "close_id"),
    [
        ("f4", "workspace-controls-dialog", "controls-close"),
        ("ctrl+l", "activity-dialog", "activity-close"),
    ],
)
async def test_utility_dialogs_use_available_space_and_keep_footer_clickable(
    git_repo, size, shortcut, frame_id, close_id
):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        await pilot.press(shortcut)
        await pilot.pause()
        frame = app.screen.query_one(f"#{frame_id}")
        assert frame.region.width >= int(size[0] * 0.8)
        assert frame.region.height >= int(size[1] * 0.8)
        assert 0 <= frame.region.x < frame.region.right <= size[0]
        assert 0 <= frame.region.y < frame.region.bottom <= size[1]
        if shortcut == "f4":
            body = app.screen.query_one("#controls-body")
            assert body.size.height >= max(8, int(frame.size.height * 0.55))
            for control_id in ("controls-scope", "graph-style", "diff-layout", "theme"):
                field = app.screen.query_one(f"#{control_id}", Select)
                if control_id == "diff-layout":
                    field.value = "split"
                elif control_id == "theme":
                    field.value = "auto"
                field.focus()
                await pilot.pause()
                current = field.query_one("SelectCurrent")
                label = current.query_one("#label", Static)
                assert current.region.bottom <= field.parent.region.bottom
                assert (
                    body.region.y <= current.region.y < current.region.bottom <= body.region.bottom
                )
                assert label.size.width >= cell_len(str(label.renderable))
        else:
            minimum = int(frame.size.height * 0.45) if size[0] > 100 else 3
            assert app.screen.query_one("#activity-list").size.height >= minimum
            assert app.screen.query_one("#activity-detail").size.height >= minimum
        close = app.screen.query_one(f"#{close_id}", Button)
        assert close.region.bottom <= frame.content_region.bottom
        assert close.region.right <= frame.content_region.right
        assert await pilot.click(f"#{close_id}")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_settings_resize_preserves_candidate_and_reaches_apply(git_repo):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(270, 80)) as pilot:
        await settle(app, pilot)
        await pilot.press("f4")
        await pilot.pause()
        interval = app.screen.query_one("#remote-refresh-interval", Input)
        interval.value = "93"
        theme = app.screen.query_one("#theme", Select)
        theme.value = "dark"
        for size in ((80, 24), (170, 48), (80, 24)):
            await pilot.resize_terminal(*size)
            await pilot.pause()
            assert interval.value == "93"
            assert theme.value == "dark"
            interval.focus()
            await pilot.pause()
            body = app.screen.query_one("#controls-body")
            assert body.region.y <= interval.region.y < interval.region.bottom <= body.region.bottom
        assert await pilot.click("#controls-apply")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert app._settings["remote_refresh_interval"] == 93
        assert app.theme == "rhizome-dark"


async def test_long_settings_diagnostics_do_not_displace_footer(git_repo):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        dialog = WorkspaceControlsDialog(
            global_settings={},
            overrides={},
            excluded=True,
            state_error="Could not read saved settings. " * 25,
            settings_path="/home/user/" + "long-settings-directory/" * 20 + "settings.json",
        )
        app.push_screen(dialog)
        await pilot.pause()
        body = dialog.query_one("#controls-body")
        assert body.size.height >= 12
        for control_id in ("controls-scope", "theme", "reload-settings"):
            control = dialog.query_one(f"#{control_id}")
            control.focus()
            await pilot.pause()
            assert body.region.y <= control.region.y < control.region.bottom <= body.region.bottom
        for control_id in ("controls-close", "controls-apply"):
            button = dialog.query_one(f"#{control_id}")
            assert (
                button.region.bottom
                <= dialog.query_one("#workspace-controls-dialog").content_region.bottom
            )
        assert await pilot.click("#controls-close")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_activity_resize_and_refresh_preserve_selected_record(git_repo):
    records = tuple(
        OperationActivity(
            id=f"operation-{index}",
            operation="stage",
            state="succeeded",
            message="\n".join(f"Result line {line}" for line in range(60)),
            started=datetime(2026, 1, 1, 12, index, tzinfo=UTC),
            elapsed=index,
        )
        for index in range(20)
    )
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(270, 80)) as pilot:
        await settle(app, pilot)
        dialog = ActivityDialog(lambda: records, lambda: ("Local polling: idle",))
        app.push_screen(dialog)
        await pilot.pause()
        options = dialog.query_one("#activity-list", OptionList)
        options.highlighted = 12
        await pilot.pause()
        selected = options.get_option_at_index(12).id
        for size in ((80, 24), (170, 48), (270, 80)):
            await pilot.resize_terminal(*size)
            await pilot.pause()
            dialog._refresh()
            await pilot.pause()
            assert options.get_option_at_index(options.highlighted).id == selected
            detail = dialog.query_one("#activity-detail")
            detail.scroll_end(animate=False)
            await pilot.pause()
            assert detail.scroll_y > 0
            assert dialog.query_one("#activity-close").region.bottom <= size[1]
        assert await pilot.click("#activity-close")
        await pilot.pause()
        assert len(app.screen_stack) == 1
