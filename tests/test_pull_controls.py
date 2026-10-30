from __future__ import annotations

import pytest
from textual.widgets import Button, OptionList

from rhizome.app import RhizomeApp
from rhizome.demo import create_demo
from rhizome.persistence import WorkspaceStateStore
from rhizome.pull_controls import PullModeMenu


@pytest.mark.asyncio
@pytest.mark.parametrize("plain", [False, True])
async def test_pull_menu_keyboard_click_escape_and_saved_default(
    tmp_path, monkeypatch, plain
) -> None:
    """The real split control changes only its preference, never starts Git work."""
    monkeypatch.setenv("RHIZOME_STATE_HOME", str(tmp_path / "state"))
    WorkspaceStateStore().save("global", {"plain_glyphs": plain, "remote_refresh_enabled": False})
    repo = create_demo(tmp_path / "demo")
    git_before = {
        path.relative_to(repo): path.read_bytes()
        for path in (repo / ".git").rglob("*")
        if path.is_file()
    }
    app = RhizomeApp(repo)
    async with app.run_test(size=(80, 24)) as pilot:
        for _ in range(80):
            await pilot.pause(0.02)
            if app.snapshot_data is not None and not app.busy:
                break
        primary = app.query_one("#fetch", Button)
        chevron = app.query_one("#pull-menu", Button)
        assert primary.display and chevron.display

        await pilot.click("#pull-menu")
        await pilot.pause()
        options = app.screen.query_one(OptionList)
        assert options.content_region.height >= 4, (
            "All four modes must be visible without scrolling"
        )
        assert (
            options.region.bottom <= app.screen.query_one("#pull-mode-menu").content_region.bottom
        )
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        assert app.pull_fetch_mode() == "pull-rebase"
        assert not app.busy

        await pilot.click("#pull-menu")
        await pilot.press("escape")
        await pilot.pause()
        assert app.pull_fetch_mode() == "pull-rebase"

        # Exercise the actual option hit target as well as keyboard selection.
        await pilot.click("#pull-menu")
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, PullModeMenu)
        choices = menu.query_one("#pull-mode-options", OptionList)
        await pilot.click(choices, offset=(2, 3))
        await pilot.pause()
        assert app.pull_fetch_mode() == "pull-rebase"
        await pilot.click("#pull-menu")
        await pilot.pause()
        choices = app.screen.query_one("#pull-mode-options", OptionList)
        await pilot.click(choices, offset=(2, 0))
        await pilot.pause()
        assert app.pull_fetch_mode() == "fetch-all"
        assert str(app.query_one("#fetch", Button).label) == "Fetch"
        await pilot.press("ctrl+l")
        await pilot.pause()
        activity = app.screen.query_one("#activity-list", OptionList)
        assert activity.option_count == 1
        assert activity.get_option_at_index(0).disabled
        assert str(activity.get_option_at_index(0).prompt) == "No Git operations yet"
        assert {
            path.relative_to(repo): path.read_bytes()
            for path in (repo / ".git").rglob("*")
            if path.is_file()
        } == git_before
        await pilot.press("escape")

    reopened = RhizomeApp(repo)
    async with reopened.run_test(size=(80, 24)) as pilot:
        await reopened.workers.wait_for_complete()
        await pilot.pause()
        assert reopened.pull_fetch_mode() == "fetch-all"
        assert str(reopened.query_one("#fetch", Button).label) == "Fetch"
        await pilot.click("#pull-menu")
        await pilot.pause()
        options = reopened.screen.query_one(OptionList)
        assert options.highlighted == 0
        assert str(options.get_option_at_index(0).prompt).startswith("(*)" if plain else "◉")
        await pilot.click(offset=(2, 0))  # Outside the popup must not click through.
        await pilot.pause()
        assert not isinstance(reopened.screen, PullModeMenu)
        assert reopened.pull_fetch_mode() == "fetch-all"
