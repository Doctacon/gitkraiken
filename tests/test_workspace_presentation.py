"""History-first presentation without losing keyboard access or repository context."""

import pytest
from textual.widgets import Button, DataTable, Static, TabbedContent

from rhizome.app import RhizomeApp


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.parametrize("width", [80, 100, 120, 170, 240])
async def test_history_prioritizes_subject_and_fits_default_columns(git_repo, git_run, width):
    subject = "Make the primary reading surface clear without losing investigation controls"
    git_run(git_repo, "commit", "--allow-empty", "-m", subject)
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(width, 32)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        columns = [column.key.value for column in table.ordered_columns]
        assert columns[:2] == ["graph", "subject"]
        assert table.columns["subject"].width >= 24
        assert table.max_scroll_x == 0
        assert table.get_cell(app.snapshot_data.head, "subject").plain == subject
        assert ("author" in columns) is (width == 240)
        assert ("sha" in columns) is (width == 240)
        assert app.query_one("#refs-panel").display is (width >= 120)
        assert app.query_one("#history-count", Static).renderable.plain == "1 commit"
        # Hidden metadata is still inspectable, never erased from the snapshot.
        assert app._selected_commit.author in str(app.query_one("#commit-detail").render())
        for ident in ("pull", "stash", "pop", "branch"):
            assert not app.query_one(f"#{ident}").display
        for ident in (
            "actions",
            "show-primary",
            "show-files",
            "show-composer",
            "workspace-settings",
        ):
            button = app.query_one(f"#{ident}", Button)
            assert button.region.right <= width
            assert button.visible
            assert button.content_size.height >= 1


async def test_scrollable_history_reserves_rail_and_search_labels_fit(git_repo, git_run):
    for number in range(25):
        git_run(git_repo, "commit", "--allow-empty", "-m", f"Readable commit {number}")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 32)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        assert table.max_scroll_y > 0
        assert table.max_scroll_x == 0
        for ident in ("search-next", "search-criteria", "graph-style"):
            button = app.query_one(f"#{ident}", Button)
            assert button.content_size.width >= button.label.cell_len + 2
        # Explicit refresh must not change the width budget when the vertical
        # rail is already present (it was absent before the first rows loaded).
        widths = {key: column.width for key, column in table.columns.items()}
        app.action_refresh()
        await settle(app, pilot)
        assert {key: column.width for key, column in table.columns.items()} == widths
        assert table.max_scroll_x == 0


async def test_medium_refs_are_summonable_without_losing_history(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "A useful history")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(100, 28)) as pilot:
        await settle(app, pilot)
        head = app.snapshot_data.head
        assert not app.query_one("#refs-panel").display
        await pilot.click("#show-refs")
        await settle(app, pilot)
        assert app.query_one("#refs-panel").display
        assert app.query_one("#show-refs").has_class("nav-active")
        await pilot.click("#show-primary")
        await settle(app, pilot)
        assert not app.query_one("#refs-panel").display
        assert app.query_one("#show-primary").has_class("nav-active")
        assert app._history_key == head
        assert app.query_one("#graph-table", DataTable).max_scroll_x == 0
        await pilot.press("ctrl+left")
        await settle(app, pilot)
        assert app.query_one("#refs-panel").display
        # Focusing/blurring the one-line filter must not shift tree targets
        # between MouseDown and Click. Ctrl+Left in the filter itself remains
        # word navigation rather than a global pane key.
        tree = app.query_one("#refs-tree")
        region = tree.region
        tree.focus()
        await pilot.pause()
        assert tree.region == region
        await pilot.press("ctrl+left")
        await settle(app, pilot)
        assert not app.query_one("#refs-panel").display


async def test_narrow_enter_opens_commit_inspection_and_escape_returns(git_repo, git_run):
    (git_repo / "notes.txt").write_text("review me\n")
    git_run(git_repo, "add", "notes.txt")
    git_run(git_repo, "commit", "-m", "Inspect without checkout")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        head = app.snapshot_data.head
        table = app.query_one("#graph-table", DataTable)
        table.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.query_one("#inspector-panel").display
        assert not app.query_one("#history-panel").display
        assert app.query_one("#inspector-tabs", TabbedContent).active == "details-tab"
        assert "Inspect without checkout" in str(app.query_one("#commit-detail").render())
        await pilot.press("escape")
        await settle(app, pilot)
        assert app.query_one("#history-panel").display
        assert not app.query_one("#inspector-panel").display
        assert app._history_key == head
        assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == head


async def test_navigation_marks_current_task_and_history_exits_diff(git_repo, git_run):
    (git_repo / "notes.txt").write_text("before\n")
    git_run(git_repo, "add", "notes.txt")
    git_run(git_repo, "commit", "-m", "Initial")
    (git_repo / "notes.txt").write_text("after\n")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await pilot.press("f2")
        await settle(app, pilot)
        assert app.query_one("#show-files").has_class("nav-active")
        await pilot.press("enter")
        await settle(app, pilot)
        assert app._active_diff is not None
        await pilot.press("f3")
        await settle(app, pilot)
        assert app.query_one("#show-composer").has_class("nav-active")
        await pilot.click("#show-primary")
        await settle(app, pilot)
        assert app._active_diff is None
        assert app.query_one("#graph-workspace").display
        assert app.query_one("#show-primary").has_class("nav-active")
        assert not app.query_one("#show-composer").has_class("nav-active")
