"""The workspace must use reviewed coordination, never direct mutation shortcuts."""

from test_app import settle
from textual.widgets import Button, DataTable, OptionList, Static, TabbedContent

from rhizome.app import RhizomeApp


async def test_activity_shows_real_stage_outcome_and_fits_narrow_terminal(git_repo, git_run):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await pilot.click("#show-files")
        await pilot.click("#stage-all")
        await settle(app, pilot)
        await pilot.click("#activity")
        await pilot.pause()
        options = app.screen.query_one("#activity-list", OptionList)
        assert any(
            "SUCCEEDED" in str(options.get_option_at_index(index).prompt)
            and "stage" in str(options.get_option_at_index(index).prompt)
            for index in range(options.option_count)
        )
        assert "never cancels" in str(app.screen.query_one("#activity-notice", Static).renderable)
        close = app.screen.query_one("#activity-close", Button)
        assert close.region.bottom <= app.screen.size.height
        await pilot.click("#activity-close")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"


async def test_discard_is_refused_without_an_available_recovery_workflow(git_repo, git_run):
    path = git_repo / "important.txt"
    path.write_text("committed\n")
    git_run(git_repo, "add", "important.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("must not be lost\n")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
        app.query_one("#wip-table", DataTable).focus()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.click("#discard")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert path.read_text() == "must not be lost\n"
        assert "unavailable" in app.query_one("#status-line", Static).renderable.plain.lower()
