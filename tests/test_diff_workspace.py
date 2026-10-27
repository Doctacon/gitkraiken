from __future__ import annotations

import threading

import pytest
from test_app import settle
from textual.widgets import DataTable, Input, Static, TabbedContent, TextArea
from ui_helpers import click

from rhizome.app import RhizomeApp
from rhizome.commit_files import CommitFileTree


@pytest.fixture
def file_repo(git_repo, git_run):
    (git_repo / "a.txt").write_text("".join(f"line {i}\n" for i in range(100)))
    (git_repo / "b.txt").write_text("base\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "Base files")
    return git_repo


async def hit_point(tree, pilot, key):
    node = tree._path_nodes[key]
    tree.scroll_to_node(node, animate=False)
    await pilot.pause()
    y = tree.content_region.y - tree.region.y + node.line - int(tree.scroll_y)
    for x in range(tree.region.width):
        style = pilot.app.screen.get_style_at(tree.region.x + x, tree.region.y + y)
        if style.meta.get("line") == node.line:
            return x, y
    raise AssertionError(f"No hit point for {key}")


async def test_explicit_commit_file_diff_returns_to_unchanged_graph(git_repo, git_run):
    (git_repo / "note.txt").write_text("first\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "Initial note")
    oid = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()

    app = RhizomeApp(git_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        graph = app.query_one("#graph-table", DataTable)
        graph_widths = {key: column.width for key, column in graph.columns.items()}
        app.jump_to(oid)
        await settle(app, pilot)
        tree = app.query_one(CommitFileTree)
        assert tree.selected_file is None

        # Opening is activation-only: loading a commit or moving its tree cursor
        # leaves the mounted graph in place.
        file = next(iter(tree._files.values()))
        tree.select_file(file)
        await pilot.pause()
        assert not app._active_diff
        await click(pilot, tree, await hit_point(tree, pilot, tree._key_for(file)))
        await settle(app, pilot)
        assert app.query_one("#diff-workspace").display
        assert not app.query_one("#graph-workspace").display
        assert "note.txt" in app.query_one("#center-diff-title").renderable.plain

        await pilot.press("escape")
        await pilot.pause()
        assert not app._active_diff
        assert app.query_one("#graph-workspace").display
        assert not app.query_one("#diff-workspace").display
        assert app._history_key == oid
        assert app.focused is graph
        assert {key: column.width for key, column in graph.columns.items()} == graph_widths


async def test_worktree_enter_opens_only_the_selected_staging_area(git_repo, git_run):
    (git_repo / "note.txt").write_text("base\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "Base")
    (git_repo / "note.txt").write_text("staged\n")
    git_run(git_repo, "add", "note.txt")
    (git_repo / "note.txt").write_text("unstaged\n")

    app = RhizomeApp(git_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#wip-table", DataTable)
        keys = list(app._file_rows)
        assert len(keys) == 2
        # A highlight is only a staging-control selection, not a diff request.
        table.move_cursor(row=table.get_row_index(keys[0]), animate=False)
        await pilot.pause()
        assert not app._active_diff
        table.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        assert app._active_diff
        assert "note.txt" in app.query_one("#center-diff-title").renderable.plain
        assert app.query_one("#inspector-tabs", TabbedContent).active == "wip-tab"


async def test_refresh_keeps_working_tab_and_explicit_diff_not_highlight(file_repo):
    app = RhizomeApp(file_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        app.jump_to(app.snapshot_data.head)
        await settle(app, pilot)
        tabs = app.query_one("#inspector-tabs", TabbedContent)
        tabs.active = "wip-tab"
        await pilot.pause()
        composer = app.query_one("#commit-message", TextArea)
        composer.load_text("Keep this draft")
        (file_repo / "a.txt").write_text("first change\n")
        (file_repo / "b.txt").write_text("other change\n")
        app.poll_repository()
        await settle(app, pilot)
        assert tabs.active == "wip-tab"
        assert not app._active_diff

        table = app.query_one("#wip-table", DataTable)
        keys = {file.path: key for key, (file, _) in app._file_rows.items()}
        table.move_cursor(row=table.get_row_index(keys["a.txt"]), animate=False)
        table.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        assert app._active_diff[0].path == "a.txt"
        # Staging selection and the explicitly opened diff are independent.
        await pilot.press("down")
        assert app._selected_file[0].path == "b.txt"
        (file_repo / "a.txt").write_text("updated diff content\n")
        (file_repo / "untracked.txt").write_text("trigger a new snapshot\n")
        app.poll_repository()
        await settle(app, pilot)
        assert tabs.active == "wip-tab"
        assert app._active_diff[0].path == "a.txt"
        assert "a.txt" in app.query_one("#center-diff-title", Static).renderable.plain
        assert "+updated diff content" in app.query_one("#center-diff", Static).renderable.plain
        assert composer.text == "Keep this draft"
        # Same status codes still refresh file contents without a new snapshot.
        (file_repo / "a.txt").write_text("changed again\n")
        app.poll_repository()
        await settle(app, pilot)
        assert "+changed again" in app.query_one("#center-diff", Static).renderable.plain
        assert tabs.active == "wip-tab"

        (file_repo / "a.txt").write_text("".join(f"line {i}\n" for i in range(100)))
        app.poll_repository()
        await settle(app, pilot)
        assert not app._active_diff  # Disappeared diff closes, other files remain.
        assert app.query_one("#graph-workspace").display
        assert tabs.active == "wip-tab"


@pytest.mark.parametrize("scope", ["commit", "worktree"])
async def test_refresh_does_not_reset_diff_scroll(file_repo, scope):
    if scope == "worktree":
        (file_repo / "a.txt").write_text("".join(f"new {i}\n" for i in range(100)))
    app = RhizomeApp(file_repo)
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(app, pilot)
        if scope == "commit":
            app.jump_to(app.snapshot_data.head)
            await settle(app, pilot)
            tree = app.query_one(CommitFileTree)
            file = next(file for file in tree._files.values() if file.path == "a.txt")
            await click(pilot, tree, await hit_point(tree, pilot, tree._key_for(file)))
        else:
            table = app.query_one("#wip-table", DataTable)
            table.focus()
            await pilot.press("enter")
        await settle(app, pilot)
        preview = app.query_one("#center-diff-scroll")
        preview.scroll_to(y=30, animate=False)
        await pilot.pause()
        before = preview.scroll_offset
        assert before.y == 30
        # A changed snapshot and a no-change poll must both keep reading position.
        (file_repo / "new.txt").write_text("snapshot change\n")
        app.poll_repository()
        await settle(app, pilot)
        assert app._active_diff
        assert preview.scroll_offset == before
        app.poll_repository()
        await settle(app, pilot)
        assert preview.scroll_offset == before


async def test_late_diff_cannot_reopen_after_escape_or_tab_change(file_repo, monkeypatch):
    app = RhizomeApp(file_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        app.jump_to(app.snapshot_data.head)
        await settle(app, pilot)
        tree = app.query_one(CommitFileTree)
        file = next(iter(tree._files.values()))
        real_diff = app.repo.diff
        started, release = threading.Event(), threading.Event()

        def delayed(*args, **kwargs):
            started.set()
            assert release.wait(10)
            return real_diff(*args, **kwargs)

        monkeypatch.setattr(app.repo, "diff", delayed)
        try:
            await click(pilot, tree, await hit_point(tree, pilot, tree._key_for(file)))
            for _ in range(100):
                if started.is_set():
                    break
                await pilot.pause(0.01)
            assert started.is_set()
            assert app._active_diff
            await pilot.press("escape")
            assert not app._active_diff
        finally:
            release.set()
        await settle(app, pilot)
        assert app.query_one("#graph-workspace").display
        assert not app.query_one("#diff-workspace").display
        # The same selected file can be explicitly opened again after Back.
        await click(pilot, tree, await hit_point(tree, pilot, tree._key_for(file)))
        await settle(app, pilot)
        assert app._active_diff
        app.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
        await settle(app, pilot)
        assert not app._active_diff
        assert app.query_one("#graph-workspace").display


@pytest.mark.parametrize("width", [60, 100])
async def test_center_diff_fits_narrow_workspace(file_repo, width):
    app = RhizomeApp(file_repo)
    async with app.run_test(size=(width, 35)) as pilot:
        await settle(app, pilot)
        if not app.query_one("#inspector-panel").display:
            app.action_toggle_inspector()
            await settle(app, pilot)
        tree = app.query_one(CommitFileTree)
        file = next(iter(tree._files.values()))
        await click(pilot, tree, await hit_point(tree, pilot, tree._key_for(file)))
        await settle(app, pilot)
        workspace = app.query_one("#workspace")
        diff = app.query_one("#diff-workspace")
        back = app.query_one("#back-to-graph")
        assert diff.display
        assert diff.region.width >= 34
        assert diff.region.height > 10
        assert back.region.right <= diff.region.right
        assert diff.region.right <= workspace.region.right
        assert app.query_one("#inspector-panel").region.right <= workspace.region.right
        assert await pilot.click(back)
        await settle(app, pilot)
        assert app.query_one("#graph-workspace").display
        assert not app._active_diff


async def test_back_restores_graph_scroll_search_and_selection(file_repo, git_run):
    for number in range(25):
        git_run(file_repo, "commit", "--allow-empty", "-m", f"Later {number}")
    app = RhizomeApp(file_repo)
    async with app.run_test(size=(150, 35)) as pilot:
        await settle(app, pilot)
        search = app.query_one("#graph-filter", Input)
        search.value = "Base files"
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        tree = app.query_one(CommitFileTree)
        selection, position = app._history_key, table.scroll_offset
        assert position.y > 0
        widths = {key: column.width for key, column in table.columns.items()}
        file = next(iter(tree._files.values()))
        tree.select_file(file)
        tree.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        assert app._active_diff
        assert await pilot.click("#back-to-graph")
        await settle(app, pilot)
        assert not app._active_diff
        assert search.value == "Base files"
        assert app._history_key == selection
        assert table.scroll_offset == position
        assert app.focused is table
        assert {key: column.width for key, column in table.columns.items()} == widths
