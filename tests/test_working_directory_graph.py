"""Working-directory rows represent actual changes, never a phantom commit."""

import pytest
from test_app import click_reference, settle
from textual.widgets import DataTable, TabbedContent

from rhizome.app import RhizomeApp
from rhizome.commit_files import CommitFileTree


@pytest.fixture
def clean_branch(git_repo, git_run):
    (git_repo / "tracked.txt").write_text("committed\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "Base")
    git_run(git_repo, "switch", "-c", "test")
    return git_repo


@pytest.mark.parametrize("style", ["smooth", "compact"])
@pytest.mark.parametrize("detached", [False, True])
async def test_clean_checkout_has_no_wip_node(clean_branch, git_run, style, detached):
    if detached:
        git_run(clean_branch, "checkout", "--detach")
    app = RhizomeApp(clean_branch)
    app.graph_style = style
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(app, pilot)
        assert app.snapshot_data.files == ()
        table = app.query_one("#graph-table", DataTable)
        assert "wip" not in table.rows
        assert app._history_key == app.snapshot_data.head
        assert table.ordered_rows[table.cursor_row].key.value == app.snapshot_data.head
        assert app._selected_commit.oid == app.snapshot_data.head
        assert app.query_one("#inspector-tabs", TabbedContent).active == "details-tab"

        app.action_toggle_graph_style()
        app.action_refresh()
        await settle(app, pilot)
        assert "wip" not in table.rows
        assert app._history_key == app.snapshot_data.head


@pytest.mark.parametrize("kind", ["unstaged", "staged", "untracked", "deleted"])
async def test_wip_node_tracks_changes_and_returns_to_head(clean_branch, git_run, kind):
    app = RhizomeApp(clean_branch)
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        assert "wip" not in table.rows
        path = clean_branch / ("new.txt" if kind == "untracked" else "tracked.txt")
        if kind == "deleted":
            path.unlink()
        else:
            path.write_text("pending change\n")
        if kind == "staged":
            git_run(clean_branch, "add", "tracked.txt")
        app.action_refresh()
        await settle(app, pilot)
        assert "wip" in table.rows
        assert app._history_key == app.snapshot_data.head  # Do not steal commit selection.
        app.jump_to("wip")
        await settle(app, pilot)
        assert app.query_one("#inspector-tabs", TabbedContent).active == "wip-tab"

        if kind == "untracked":
            path.unlink()
        elif kind == "staged":
            git_run(clean_branch, "commit", "-m", "Commit pending change")
        else:
            git_run(clean_branch, "restore", "tracked.txt")
        app.action_refresh()
        await settle(app, pilot)
        assert app.snapshot_data.files == ()
        assert "wip" not in table.rows
        assert app._history_key == app.snapshot_data.head
        assert table.ordered_rows[table.cursor_row].key.value == app.snapshot_data.head
        # Working directory is still available even with no graph row.
        tabs = app.query_one("#inspector-tabs", TabbedContent)
        tabs.active = "wip-tab"
        await settle(app, pilot)
        assert tabs.active == "wip-tab"
        assert app.query_one("#wip-table", DataTable).row_count == 0


@pytest.mark.parametrize("limit", [300, 1])
async def test_clean_checkout_selects_head_not_newer_other_branch(clean_branch, git_run, limit):
    head = git_run(clean_branch, "rev-parse", "HEAD").stdout.strip()
    git_run(clean_branch, "switch", "-c", "newer")
    git_run(
        clean_branch,
        "commit",
        "--allow-empty",
        "--date=2040-01-01T00:00:00+00:00",
        "-m",
        "Newer commit on another branch",
    )
    other = git_run(clean_branch, "rev-parse", "HEAD").stdout.strip()
    git_run(clean_branch, "switch", "test")
    app = RhizomeApp(clean_branch)
    app.limit = limit
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        assert table.ordered_rows[0].key.value == other
        expected = head if limit > 1 else other  # HEAD may be outside the loaded history.
        assert app._history_key == expected
        assert app._selected_commit.oid == expected
        app.jump_to(other)
        await settle(app, pilot)
        (clean_branch / "new.txt").write_text("change\n")
        app.action_refresh()
        await settle(app, pilot)
        assert "wip" in table.rows
        assert app._history_key == other
        (clean_branch / "new.txt").unlink()
        app.action_refresh()
        await settle(app, pilot)
        assert "wip" not in table.rows
        assert app._history_key == other  # Cleaning up must preserve a valid selection.
        assert app._selected_commit.oid == other


async def test_clean_working_directory_accessible_from_sidebar(clean_branch):
    app = RhizomeApp(clean_branch)
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "worktree")
        await settle(app, pilot)
        assert app.query_one("#inspector-tabs", TabbedContent).active == "wip-tab"
        assert "wip" not in app.query_one("#graph-table", DataTable).rows
        assert app._history_key == app.snapshot_data.head
        app.query_one("#inspector-tabs", TabbedContent).active = "details-tab"
        await settle(app, pilot)
        tree = app.query_one(CommitFileTree)
        tree.select_file(next(iter(tree._files.values())))
        tree.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        assert app._active_diff and app._active_diff[2] == app.snapshot_data.head


async def test_open_clean_repository_selects_head(clean_branch, git_run, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    git_run(other, "init", "-b", "main")
    (other / "pending.txt").write_text("untracked change\n")
    app = RhizomeApp(other)
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(app, pilot)
        assert app._history_key == "wip"
        app.open_repository(str(clean_branch))
        await settle(app, pilot)
        assert app.repo.root == clean_branch
        assert "wip" not in app.query_one("#graph-table", DataTable).rows
        assert app._history_key == app.snapshot_data.head
        assert app.query_one("#inspector-tabs", TabbedContent).active == "details-tab"
        assert app._selected_commit.oid == app.snapshot_data.head


async def test_empty_repository_has_no_wip_until_a_file_exists(git_repo):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        assert table.row_count == 0
        assert app._selected_commit is None
        assert app.query_one("#inspector-tabs", TabbedContent).active == "wip-tab"
        app.action_toggle_graph_style()
        app.action_clear_filter()
        app.action_refresh()
        await settle(app, pilot)
        assert table.row_count == 0
        (git_repo / "first.txt").write_text("first change\n")
        app.action_refresh()
        await settle(app, pilot)
        assert list(row.key.value for row in table.ordered_rows) == ["wip"]
        assert app._history_key == "wip"
        (git_repo / "first.txt").unlink()
        app.action_refresh()
        await settle(app, pilot)
        assert table.row_count == 0
        assert app._last_error == ""
