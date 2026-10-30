import re

from test_app import settle
from test_investigation_workspace import _fast_history, _rows
from textual.widgets import Button, DataTable, Input, Static

from rhizome.app import RhizomeApp


def _caption(app):
    return str(app.query_one("#history-caption", Static).render())


async def test_stop_progressive_no_hit_search_ignores_late_batches_and_allows_new_query(
    git_repo, git_run
):
    _fast_history(git_repo, 30_000)
    head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    app = RhizomeApp(git_repo)

    async with app.run_test(size=(150, 42)) as pilot:
        await settle(app, pilot)
        search = app.query_one("#graph-filter", Input)
        search.value = "definitely absent from every commit"

        searching = ""
        examined = 0
        for _ in range(300):
            await pilot.pause(0.01)
            searching = _caption(app)
            progress = re.search(r"(\d+) examined", searching)
            examined = int(progress.group(1)) if progress else 0
            if examined >= 300 and "searching" in searching.lower():
                break
        assert examined >= 300
        assert "searching" in searching.lower()

        await pilot.press("f10")
        await pilot.pause()
        stopped = _caption(app)
        assert "stopped between batches" in stopped
        assert "incomplete" in stopped.lower()
        await pilot.pause(0.5)
        assert _caption(app) == stopped

        search.value = "fast commit 29999"
        await settle(app, pilot)
        assert "1 batch matches" in _caption(app)
        assert not app.query_one("#search-next", Button).disabled
        assert await pilot.click("#search-next")
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        assert table.ordered_rows[table.cursor_row].key.value == head


async def test_focus_and_exit_focus_restore_search_results_selection_window_and_scroll(
    git_repo, git_run
):
    _fast_history(git_repo, 601)
    oracle = git_run(git_repo, "rev-list", "--topo-order", "HEAD").stdout.splitlines()
    app = RhizomeApp(git_repo)

    async with app.run_test(size=(150, 35)) as pilot:
        await settle(app, pilot)
        search = app.query_one("#graph-filter", Input)
        search.value = "fast commit"
        await settle(app, pilot)
        assert "300 batch matches" in _caption(app)
        assert not app.query_one("#search-next", Button).disabled

        table = app.query_one("#graph-table", DataTable)
        table.focus()
        table.move_cursor(row=220)
        await pilot.pause()
        before_rows = _rows(app)
        before_scroll = table.scroll_offset
        selected = table.ordered_rows[table.cursor_row].key.value
        assert before_rows == oracle[:300]
        assert selected == oracle[220]
        assert before_scroll.y > 0

        await pilot.press("f5")
        await settle(app, pilot)
        assert "Focused" in _caption(app)

        await pilot.press("shift+f5")
        await settle(app, pilot)
        await pilot.pause()
        assert search.value == "fast commit"
        assert "300 batch matches" in _caption(app)
        assert not app.query_one("#search-next", Button).disabled
        assert _rows(app) == before_rows
        assert table.ordered_rows[table.cursor_row].key.value == selected
        assert table.scroll_offset == before_scroll
