from __future__ import annotations

import subprocess

import pytest
from textual.widgets import (
    DataTable,
    Input,
    OptionList,
    Static,
    TabbedContent,
    TextArea,
    Tooltip,
    Tree,
)
from textual.widgets._toast import Toast
from ui_helpers import click, drag, header_grip, post_click

from rhizome.app import RhizomeApp
from rhizome.commit_files import CommitFileTree
from rhizome.demo import create_demo
from rhizome.dialogs import ActionPicker, BranchContextMenu, MessageDialog, PromptDialog
from rhizome.pull_request_dialog import PullRequestDialog
from rhizome.resizable_table import ResizableDataTable
from rhizome.sidebar import ReferenceSidebar, SidebarSelection


@pytest.fixture
def demo_repo(tmp_path):
    return create_demo(tmp_path / "demo")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def fast_commit_chain(root, ref, start, count):
    stream = []
    previous = start
    for index in range(count):
        mark = index + 1
        subject = f"window commit {index}"
        stream.extend(
            (
                f"commit {ref}\n",
                f"mark :{mark}\n",
                f"committer Test User <test@example.invalid> {946684800 + index} +0000\n",
                f"data {len(subject)}\n{subject}\n",
                f"from {previous if index == 0 else f':{index}'}\n",
                "\n",
            )
        )
        previous = f":{mark}"
    stream.append("done\n")
    subprocess.run(
        ["git", "fast-import", "--quiet"],
        cwd=root,
        check=True,
        text=True,
        input="".join(stream),
        capture_output=True,
    )


async def settle(app, pilot):
    for _ in range(100):
        await pilot.pause(0.05)
        if not app.busy:
            await app.workers.wait_for_complete()
            await pilot.pause()
            # Detail workers and queued selection events can spawn diff workers
            # after wait_for_complete took its snapshot of the worker set.
            if not app.busy and not app.workers:
                return
    raise AssertionError("UI did not become idle")


def reference_node(app, target):
    tree = app.query_one("#refs-tree", Tree)
    pending = list(tree.root.children)
    while pending:
        node = pending.pop()
        if isinstance(node.data, SidebarSelection) and node.data.target == target:
            return node
        pending.extend(node.children)
    raise AssertionError(f"Missing reference node: {target}")


async def click_reference(app, pilot, target, *, times=1, button=1):
    if not app.query_one("#refs-panel").display:
        await pilot.click("#show-refs")
        await pilot.pause()
    tree = app.query_one("#refs-tree", Tree)
    node = reference_node(app, target)
    parent = node.parent
    depth = 0
    while parent is not None:
        parent.expand()
        parent = parent.parent
        depth += 1
    await pilot.pause()
    tree.scroll_to_node(node, animate=False)
    await pilot.pause()
    x = min(tree.size.width - 2, depth * tree.guide_depth + 3)
    y = node.line - int(tree.scroll_y)
    if button == 1:
        await click(pilot, tree, (x, y), times=times)
    else:
        await post_click(pilot, tree, (x, y), button=button)
    return node


async def choose_option(app, pilot, operation):
    options = app.screen.query_one(OptionList)
    options.highlighted = options.get_option_index(operation)
    await pilot.press("enter")
    await pilot.pause()


async def graph_point(app, pilot, key, *, column="graph"):
    table = app.query_one("#graph-table", DataTable)
    table.move_cursor(row=table.get_row_index(key), animate=False)
    await pilot.pause()
    x = table.cell_padding + sum(
        col.width + 2 * table.cell_padding
        for col in table.ordered_columns[: table.get_column_index(column)]
    )
    y = (
        table.header_height
        + sum(row.height for row in table.ordered_rows[: table.get_row_index(key)])
        - int(table.scroll_y)
    )
    return table, (x, y)


@pytest.fixture
def branch_repo(git_repo, git_run, bare_remote):
    (git_repo / "base.txt").write_text("base\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "Base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "-u", "origin", "main")
    git_run(git_repo, "switch", "-c", "test")
    (git_repo / "topic.txt").write_text("topic\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "Topic")
    git_run(git_repo, "push", "-u", "origin", "test")
    git_run(git_repo, "switch", "main")
    return git_repo


async def test_real_repository_mount_and_commit_diff(demo_repo):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        assert app.snapshot_data is not None
        assert app.snapshot_data.branch == "feature/workspace"
        assert len(app._commits) >= 12
        table = app.query_one("#graph-table", DataTable)
        assert table.row_count == len(app._commits) + 1  # WIP + one multiline row per commit
        oid = next(iter(app._commits))
        app.jump_to(oid)
        await settle(app, pilot)
        assert app.query_one("#inspector-tabs", TabbedContent).active == "details-tab"
        tree = app.query_one(CommitFileTree)
        selected = next(iter(tree._files.values()))
        # Detail loading only populates the inspector; activation opens the center diff.
        app.inspect_commit_file(selected)
        await settle(app, pilot)
        preview = app.query_one("#center-diff", Static).renderable.plain
        assert "@@" in preview
        assert "diff --git" not in preview
        assert not any(line.startswith(("index ", "--- ", "+++ ")) for line in preview.splitlines())
        # The presentation filter must not alter the raw repository patch.
        assert app.repo.diff(selected.path, oid=oid).startswith("diff --git ")


async def test_hover_tooltips_stay_hidden_in_workspace_and_context_menu(demo_repo):
    app = RhizomeApp(demo_repo)
    app.TOOLTIP_DELAY = 0.01
    # Textual disables tooltips in run_test by default; exercise the real hover path.
    async with app.run_test(size=(170, 50), tooltips=True) as pilot:
        await settle(app, pilot)
        app.jump_to(app.snapshot_data.head)
        await settle(app, pilot)

        async def hover_without_popup(widget, offset=(0, 0)):
            await pilot.hover(offset=(0, 0))
            assert await pilot.hover(widget, offset=offset)
            await pilot.pause(0.05)
            tooltip = app.screen.query_one(Tooltip)
            # Verify the hover timer actually fired, not just the initial hidden state.
            assert widget.tooltip is not None
            assert tooltip.renderable == widget.tooltip
            assert not tooltip.visible
            assert app.screen.get_widget_at(*app.mouse_position)[0] is widget

        for selector in ("#history-heading", "#history-caption", "#graph-style", "#commit-detail"):
            await hover_without_popup(app.query_one(selector))
        for column in ("refs", "date"):
            table, offset = await graph_point(app, pilot, app.snapshot_data.head, column=column)
            await hover_without_popup(table, offset)

        # Hover suppression must not interfere with deliberate clicks or modal menus.
        await post_click(pilot, table, offset, button=3)
        await pilot.pause()
        assert isinstance(app.screen, BranchContextMenu)
        options = app.screen.query_one(OptionList)
        await hover_without_popup(options, (2, 1))
        await pilot.press("escape")
        assert len(app.screen_stack) == 1
        assert await pilot.click("#graph-style")
        assert app.graph_style == "compact"


@pytest.mark.parametrize("width", [240, 270])
async def test_history_author_and_date_time_columns_survive_style_and_size_changes(
    demo_repo, git_run, monkeypatch, local_timezone, width
):
    local_timezone("America/Denver")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2024-02-03T04:05:06+05:30")
    git_run(demo_repo, "commit", "--allow-empty", "-m", "Known author timestamp")
    tip = git(demo_repo, "rev-parse", "HEAD")
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(width, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        for _ in range(2):
            columns = [column.key.value for column in table.ordered_columns]
            assert columns[:5] == ["graph", "subject", "refs", "author", "date"]
            assert table.get_cell(tip, "author").plain == app._commits[tip].author
            assert str(table.get_cell("wip", "author")) == ""
            assert ("sha" in columns) == app._show_sha
            cell = table.get_cell(tip, "date")
            assert cell.plain == "2024-02-02 15:35"
            assert (
                cell.get_style_at_offset(app.console, 0).meta["commit_date"]
                == "2024-02-02 15:35:06 MST (-0700)"
            )
            assert table.columns["date"].width >= len(cell.plain)
            assert str(table.get_cell("wip", "date")) == ""
            for key in table.rows:
                if str(key.value).startswith("connector-"):
                    assert str(table.get_cell(key, "date")) == ""
                    assert str(table.get_cell(key, "author")) == ""
            app.action_toggle_graph_style()
            await settle(app, pilot)
        await pilot.resize_terminal(width + 20, 50)
        await settle(app, pilot)
        assert table.get_cell(tip, "date").plain == "2024-02-02 15:35"
        app.jump_to(tip)
        await settle(app, pilot)
        detail = app.query_one("#commit-detail", Static)
        assert "2024-02-02 15:35" in str(detail.render())
        assert "2024-02-02 15:35:06 MST (-0700)" in detail.tooltip.plain
        assert app._selected_commit.date == "2024-02-03T04:05:06+05:30"


async def test_graph_order_is_explicit_and_independent_of_style(
    git_repo, git_run, monkeypatch, local_timezone
):
    local_timezone("UTC")

    def dated_commit(day):
        timestamp = f"2026-10-{day:02d}T12:00:00+00:00"
        monkeypatch.setenv("GIT_AUTHOR_DATE", timestamp)
        monkeypatch.setenv("GIT_COMMITTER_DATE", timestamp)
        git_run(git_repo, "commit", "--allow-empty", "-m", f"day {day}")
        return git(git_repo, "rev-parse", "HEAD")

    base = dated_commit(1)
    git_run(git_repo, "switch", "-c", "topic")
    middle = dated_commit(3)
    git_run(git_repo, "switch", "main")
    older = dated_commit(2)
    newest = dated_commit(4)
    chronological = [newest, middle, older, base]
    topology = git(git_repo, "rev-list", "--topo-order", "--all").splitlines()
    expected = set(chronological)
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)

        def commit_rows():
            return [row.key.value for row in table.ordered_rows if row.key.value in expected]

        assert commit_rows() == topology
        assert await pilot.click("#graph-style")
        await settle(app, pilot)
        assert commit_rows() == topology

        assert await pilot.click("#graph-order")
        await settle(app, pilot)
        assert commit_rows() == chronological
        dates = [table.get_cell(oid, "date").plain for oid in chronological]
        assert dates == sorted(dates, reverse=True)
        assert await pilot.click("#graph-style")
        await settle(app, pilot)
        assert commit_rows() == chronological


async def test_workspace_pane_drags_survive_refresh_collapse_and_terminal_resize(demo_repo):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        app.jump_to(app.snapshot_data.head)
        await settle(app, pilot)
        before_head = git(demo_repo, "rev-parse", "HEAD")
        key = app._history_key
        refs = app.query_one("#refs-panel")
        inspector = app.query_one("#inspector-panel")
        history = app.query_one("#history-panel")
        refs_width, inspector_width = refs.region.width, inspector.region.width
        handle = app.query_one("#refs-splitter")
        x, y = handle.region.x, handle.region.y + 4
        table = app.query_one("#graph-table")
        revision = table.revision
        assert await pilot.mouse_down(handle, offset=(0, 4))
        for delta in (3, 6):
            await pilot.hover(offset=(x + delta, y))
            await pilot.pause()
            assert refs.region.width == refs_width + delta
            assert table.revision == revision  # No whole-history rebuild per mouse cell.
        await pilot.mouse_up(offset=(x + 6, y))
        await settle(app, pilot)
        assert table.revision > revision
        await drag(pilot, app.query_one("#inspector-splitter"), (0, 4), (-12, 0))
        await settle(app, pilot)
        assert refs.region.width == refs_width + 6
        assert inspector.region.width == inspector_width + 12
        assert history.region.width >= 34
        assert app._history_key == key
        assert app.mouse_captured is None
        app.action_toggle_graph_style()
        app.action_refresh()
        await settle(app, pilot)
        for toggle, panel, handle in (
            (app.action_toggle_refs, refs, "#refs-splitter"),
            (app.action_toggle_inspector, inspector, "#inspector-splitter"),
        ):
            toggle()
            await settle(app, pilot)
            assert not panel.display
            assert not app.query_one(handle).display
            toggle()
            await settle(app, pilot)
            assert app.query_one(handle).display
        await pilot.resize_terminal(100, 40)
        await settle(app, pilot)
        assert history.region.width >= 34
        assert inspector.region.right <= app.query_one("#workspace").region.right
        await pilot.resize_terminal(170, 50)
        await settle(app, pilot)
        assert refs.region.width == refs_width + 6
        assert inspector.region.width == inspector_width + 12
        assert app._history_key == key
        assert git(demo_repo, "rev-parse", "HEAD") == before_head


@pytest.mark.parametrize("tab", ["details-tab", "wip-tab"])
async def test_inspector_file_lists_use_reclaimed_preview_space(demo_repo, tab):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(170, 55)) as pilot:
        await settle(app, pilot)
        app.jump_to(app.snapshot_data.head if tab == "details-tab" else "wip")
        await settle(app, pilot)
        files = app.query_one("#commit-file-tree" if tab == "details-tab" else "#wip-table")
        assert files.region.height >= 8
        assert not app.query("#commit-files-splitter")
        assert not app.query("#wip-files-splitter")
        assert not app.query("#commit-diff-scroll")
        assert not app.query("#diff-scroll")
        assert files.region.bottom <= app.query_one("#workspace").region.bottom


@pytest.mark.parametrize("selector", ["#refs-splitter", "#graph-table"])
async def test_opening_modal_cancels_mouse_resize_capture(demo_repo, selector):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        widget = app.query_one(selector)
        point = header_grip(widget, "subject") if selector == "#graph-table" else (0, 3)
        assert await pilot.mouse_down(widget, offset=point)
        assert app.mouse_captured is widget
        app.action_help()
        await pilot.pause()
        assert app.mouse_captured is None
        await pilot.press("escape")
        await pilot.hover(offset=(70, 20))
        assert app.mouse_captured is None
        if isinstance(widget, ResizableDataTable):
            assert not widget.column_widths
        else:
            assert widget.preferred_size is None


async def test_history_column_drags_preserve_sizes_and_selection(demo_repo, git_run):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(240, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", ResizableDataTable)
        app.jump_to(app.snapshot_data.head)
        await settle(app, pilot)
        key, rows = app._history_key, app._graph_rows
        widths = {}
        for column in ("graph", "subject", "refs", "author", "date", "sha"):
            right = sum(
                col.get_render_width(table)
                for col in table.ordered_columns[: table.get_column_index(column) + 1]
            )
            table.scroll_to(x=max(0, right - table.scrollable_content_region.width), animate=False)
            await pilot.pause()
            original = table.columns[column].width
            await drag(pilot, table, header_grip(table, column), (4, 2))
            widths[column] = original + 4
            assert table.columns[column].width == widths[column]
            assert app._history_key == key
            assert app._graph_rows is rows
        git_run(demo_repo, "tag", "layout-refresh")
        app.action_refresh()
        await settle(app, pilot)
        app.action_toggle_graph_style()
        await settle(app, pilot)
        await pilot.resize_terminal(120, 45)  # SHA temporarily disappears.
        await settle(app, pilot)
        await pilot.resize_terminal(240, 50)
        await settle(app, pilot)
        for column, width in widths.items():
            assert table.columns[column].width == width
        assert app._history_key == key
        assert git(demo_repo, "rev-parse", "HEAD") == key
        table.scroll_home(animate=False)
        await pilot.pause()
        await drag(pilot, table, header_grip(table, "graph"), (-100, 0))
        assert table.columns["graph"].width >= max(row.width for row in app._graph_rows)


async def test_stage_commit_and_refresh(demo_repo):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        app.jump_to("wip")
        await pilot.click("#stage-all")
        await settle(app, pilot)
        assert all(f.staged for f in app.snapshot_data.files)
        message = app.query_one("#commit-message", TextArea)
        message.load_text("Commit from the terminal UI")
        await pilot.click("#commit")
        await settle(app, pilot)
        assert git(demo_repo, "log", "-1", "--format=%s") == "Commit from the terminal UI"
        assert not app.snapshot_data.files
        assert message.text == ""


async def test_search_and_branch_selection_preserve_unified_history(demo_repo):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        count = table.row_count
        history = app.snapshot_data.history
        rows = app._graph_rows
        app.query_one("#graph-filter", Input).value = "credential"
        await settle(app, pilot)
        assert app._search_matches
        assert table.row_count == count
        cursor = app._history_key
        scroll = table.scroll_offset
        await click_reference(app, pilot, "refs/heads/main")
        await settle(app, pilot)
        assert app._selected_ref == "refs/heads/main"
        assert app.snapshot_data.history == history
        assert app._graph_rows is rows
        assert app._history_key == cursor
        assert table.scroll_offset == scroll
        assert len(app.screen_stack) == 1  # Single-click does not request checkout.
        assert git(demo_repo, "branch", "--show-current") == "feature/workspace"
        assert table.row_count == count
        app.handle_picked_action("locate-ref")
        await settle(app, pilot)
        assert app._history_key == git(demo_repo, "rev-parse", "main")
        assert app._graph_rows is rows
        await pilot.press("escape")
        await settle(app, pilot)
        assert not app._selected_ref
        assert table.row_count == count


async def test_discard_without_recovery_fails_closed_and_preserves_content(demo_repo):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        file = next(f for f in app.snapshot_data.files if f.path == "README.md")
        path = demo_repo / file.path
        before_worktree = path.read_bytes()
        before_index = git(demo_repo, "show", f":{file.path}")
        before_head = git(demo_repo, "rev-parse", "HEAD")
        before_status = git(demo_repo, "status", "--porcelain=v1", "--", file.path)
        app.inspect_worktree_file(file, False)
        app.wip_action("discard")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert "unavailable without a durable recovery checkpoint" in app._last_error
        assert path.read_bytes() == before_worktree
        assert git(demo_repo, "show", f":{file.path}") == before_index
        assert git(demo_repo, "rev-parse", "HEAD") == before_head
        assert git(demo_repo, "status", "--porcelain=v1", "--", file.path) == before_status


async def test_mixed_index_worktree_file_has_two_diff_choices(demo_repo):
    file = demo_repo / "src/workspace.py"
    file.write_text(file.read_text() + "UNSTAGED_ONLY = True\n")
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(app, pilot)
        rows = [
            (f, staged) for f, staged in app._file_rows.values() if f.path == "src/workspace.py"
        ]
        assert len(rows) == 2
        app.inspect_worktree_file(*next(row for row in rows if row[1]))
        await settle(app, pilot)
        assert "UNSTAGED_ONLY" not in str(app.query_one("#center-diff", Static).render())
        app.inspect_worktree_file(*next(row for row in rows if not row[1]))
        await settle(app, pilot)
        preview = app.query_one("#center-diff", Static).renderable.plain
        assert "UNSTAGED_ONLY" in preview
        assert "diff --git" not in preview
        assert "@@" in preview


async def test_empty_repository_and_narrow_screen(git_repo):
    # Keep isolated XDG/application state outside the repository being inspected.
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(app, pilot)
        assert app.snapshot_data is not None
        assert not app._commits
        assert app.query_one("#graph-table", DataTable).row_count == 0
        app.action_toggle_refs()
        app.action_toggle_inspector()
        await pilot.pause()
        app.action_help()
        await pilot.pause()
        await pilot.press("escape")
        assert len(app.screen_stack) == 1


async def test_mutations_are_not_cancelled_by_refresh(demo_repo, monkeypatch):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        from rhizome.models import Action

        real_execute = app.repo.execute
        import time

        def delayed(action):
            time.sleep(0.1)
            return real_execute(action)

        monkeypatch.setattr(app.repo, "execute", delayed)
        app.run_mutation(Action("stage", paths=("README.md",)))
        app.action_refresh()
        app.run_mutation(Action("unstage"))  # ignored while busy, never overlaps
        await settle(app, pilot)
        assert "README.md" in git(demo_repo, "diff", "--cached", "--name-only")


async def test_open_repository_replaces_state_without_touching_original(demo_repo, tmp_path):
    second = tmp_path / "second"
    subprocess.run(["git", "init", "-b", "main", str(second)], check=True, capture_output=True)
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        app.open_repository(str(second))
        await settle(app, pilot)
        assert app.repo.root == second
        assert app.snapshot_data.root == second
        assert not app.snapshot_data.files
        assert git(demo_repo, "branch", "--show-current") == "feature/workspace"
        app.open_repository(str(tmp_path / "missing"))
        await settle(app, pilot)
        assert app.repo.root == second
        assert "does not exist" in app._last_error


async def test_global_shortcuts_cannot_replace_repository_under_action_picker(demo_repo, tmp_path):
    second = tmp_path / "second"
    subprocess.run(["git", "init", "-b", "main", str(second)], check=True, capture_output=True)
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        app.action_command_picker()
        await pilot.pause()
        assert isinstance(app.screen, ActionPicker)
        await pilot.press("ctrl+o", "ctrl+b", "ctrl+r")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert isinstance(app.screen, ActionPicker)
        app.open_repository(str(second))
        assert app.repo.root == demo_repo
        await pilot.press("escape")
        assert "Getting started" in (demo_repo / "README.md").read_text()


async def test_obsolete_selection_reads_are_skipped(demo_repo, monkeypatch):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        calls = []
        real_detail = app.repo.detail

        def detail(oid):
            calls.append(oid)
            return real_detail(oid)

        monkeypatch.setattr(app.repo, "detail", detail)
        async with app._serial:
            for commit in app._commits.values():
                app.inspect_commit(commit)
        await settle(app, pilot)
        assert len(calls) <= 2
        assert calls[-1] == app._selected_commit.oid


@pytest.mark.parametrize("width", [120, 170])
async def test_hierarchical_sidebar_fits_and_graph_starts_near_top(demo_repo, width):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(width, 45)) as pilot:
        await settle(app, pilot)
        sidebar = app.query_one(ReferenceSidebar)
        tree = sidebar.query_one(Tree)
        assert tree.guide_depth <= 2
        assert sidebar.query_one("#ref-filter").region.height == 1
        assert tree.region.height > 20
        assert app.query_one("#graph-table").region.y < 12
        assert app.query_one("#graph-table").region.height > 20
        assert reference_node(app, "refs/heads/feature/workspace").data.label


async def test_branch_creation_runs_on_name_submission(demo_repo):
    from rhizome.dialogs import PromptDialog

    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        await pilot.click("#actions")
        await choose_option(app, pilot, "create-branch")
        assert isinstance(app.screen, PromptDialog)
        app.screen.query_one(Input).value = "feature/from-ui"
        await pilot.click("#confirm")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert app.snapshot_data.branch == "feature/from-ui"


async def test_smooth_graph_toggle_preserves_selection_search_and_navigation(demo_repo):
    from textual.widgets import Button

    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        commits = list(app._commits)
        assert app.graph_style == "smooth"
        assert table.cursor_foreground_priority == "renderable"
        assert table.row_count == len(commits) + 1
        assert "crossing, not a join" in app.query_one("#history-heading", Static).tooltip.plain
        assert any(row.height > 1 for row in app._graph_rows)
        assert any("╮" in row.graph.plain or "╯" in row.graph.plain for row in app._graph_rows)
        assert all(
            "╱" not in row.graph.plain and "╲" not in row.graph.plain for row in app._graph_rows
        )
        for row in app._graph_rows:
            assert table.rows[row.commit.oid].height == row.height

        app.jump_to(commits[0])
        await settle(app, pilot)
        table.focus()
        await pilot.press("down")
        await settle(app, pilot)
        assert app._history_key == commits[1]
        await pilot.press("j")
        await settle(app, pilot)
        assert app._history_key == commits[2]

        graph_rows = app._graph_rows
        app.query_one("#graph-filter", Input).value = "credential"
        await settle(app, pilot)
        selected = app._history_key
        matches = app._search_matches.copy()
        assert matches
        assert app._graph_rows is graph_rows
        table.focus()
        await pilot.press("g")
        await settle(app, pilot)
        assert app.graph_style == "compact"
        assert app.query_one("#graph-style", Button).label.plain == "Compact [g]"
        assert "crossing, not a join" in app.query_one("#history-heading", Static).tooltip.plain
        assert all(row.height == 1 for row in app._graph_rows)
        assert table.row_count == len(app._graph_rows) + 1
        assert app._history_key == selected
        assert app._search_matches == matches
        assert app._selected_commit.oid == selected

        await pilot.click("#graph-style")
        await settle(app, pilot)
        assert app.graph_style == "smooth"
        assert app._history_key == selected
        assert app._search_matches == matches
        assert table.row_count == len(commits) + 1
        graph_rows = app._graph_rows
        await pilot.resize_terminal(120, 45)
        await settle(app, pilot)
        assert app._graph_rows is graph_rows  # Resizing does not recompute topology.
        assert app._history_key == selected
        assert "crossing, not a join" in app.query_one("#history-heading", Static).tooltip.plain


async def test_graph_shortcut_does_not_steal_text_input_or_escape_dialog(demo_repo):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(app, pilot)
        search = app.query_one("#graph-filter", Input)
        search.focus()
        await pilot.press("g")
        await settle(app, pilot)
        assert search.value == "g"
        assert app.graph_style == "smooth"

        app.jump_to("wip")
        await settle(app, pilot)
        message = app.query_one("#commit-message", TextArea)
        message.focus()
        await pilot.press("g")
        assert message.text == "g"
        assert app.graph_style == "smooth"

        app.action_help()
        await pilot.pause()
        app.action_toggle_graph_style()
        await pilot.press("g")
        assert len(app.screen_stack) == 2
        assert app.graph_style == "smooth"
        await pilot.press("escape")


async def test_wide_graph_retains_all_lanes_and_scrolls_instead_of_clipping(tmp_path):
    from dataclasses import replace

    from rhizome.models import Commit, HistoryLine

    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    app = RhizomeApp(tmp_path)
    async with app.run_test(size=(120, 45)) as pilot:
        await settle(app, pilot)
        history = tuple(
            HistoryLine(
                "* ",
                Commit(
                    oid=f"{prefix}{i}",
                    parents=(f"parent{i}",) if prefix == "tip" else (),
                    author="Demo",
                    email="demo@example.invalid",
                    date="",
                    subject=f"{prefix} {i}",
                ),
            )
            for prefix in ("tip", "parent")
            for i in range(12)
        )
        await app.apply_snapshot(replace(app.snapshot_data, history=history))
        await pilot.pause()
        table = app.query_one("#graph-table", DataTable)
        required_width = max(row.width for row in app._graph_rows)
        assert required_width > 36
        assert table.ordered_columns[0].width >= required_width
        assert table.virtual_size.width > table.size.width
        table.scroll_to(x=table.max_scroll_x, animate=False)
        await pilot.pause()
        assert table.scroll_x > 0


async def test_sidebar_double_click_only_selects_without_checkout(branch_repo):
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before_head = git(branch_repo, "rev-parse", "HEAD")
        table = app.query_one("#graph-table", DataTable)
        before_rows = table.row_count
        await click_reference(app, pilot, "refs/heads/test", times=2)
        await settle(app, pilot)
        assert git(branch_repo, "branch", "--show-current") == "main"
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        assert table.row_count == before_rows
        assert len(app.screen_stack) == 1
        assert "Checkout" in app.query_one("#status-line", Static).renderable.plain


@pytest.mark.parametrize("style", ["smooth", "compact"])
async def test_graph_double_click_inspects_without_checkout(branch_repo, style):
    app = RhizomeApp(branch_repo)
    app.graph_style = style
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        before_head = git(branch_repo, "rev-parse", "HEAD")
        tip = git(branch_repo, "rev-parse", "test")
        table, point = await graph_point(app, pilot, tip)
        before_rows = table.row_count
        await click(pilot, table, point, times=2)
        await settle(app, pilot)
        assert git(branch_repo, "branch", "--show-current") == "main"
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        assert tip[:8] in app.query_one("#commit-detail", Static).renderable.plain
        assert table.row_count == before_rows
        assert len(app.screen_stack) == 1
        assert app.is_running


async def test_graph_double_click_does_not_offer_ambiguous_checkout(branch_repo, git_run):
    git_run(branch_repo, "branch", "another", "test")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        tip = git(branch_repo, "rev-parse", "test")
        table, point = await graph_point(app, pilot, tip)
        await click(pilot, table, point, times=2)
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert git(branch_repo, "branch", "--show-current") == "main"
        assert tip[:8] in app.query_one("#commit-detail", Static).renderable.plain
        assert app.is_running


async def test_graph_double_click_remote_only_tip_does_not_create_branch(branch_repo, git_run):
    git_run(branch_repo, "branch", "-D", "test")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        tip = git(branch_repo, "rev-parse", "origin/test")
        table, point = await graph_point(app, pilot, tip)
        await click(pilot, table, point, times=2)
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert git(branch_repo, "branch", "--show-current") == "main"
        assert git(branch_repo, "for-each-ref", "--format=%(refname)", "refs/heads/test") == ""
        assert tip[:8] in app.query_one("#commit-detail", Static).renderable.plain


async def test_graph_double_click_non_tip_never_detaches_head(branch_repo, git_run):
    base = git(branch_repo, "rev-parse", "main")
    git_run(branch_repo, "commit", "--allow-empty", "-m", "Advance main")
    git_run(branch_repo, "push", "origin", "main")
    current = git(branch_repo, "rev-parse", "HEAD")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        table, point = await graph_point(app, pilot, base)
        await click(pilot, table, point, times=2)
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert git(branch_repo, "branch", "--show-current") == "main"
        assert git(branch_repo, "rev-parse", "HEAD") == current
        assert base[:8] in app.query_one("#commit-detail", Static).renderable.plain
        assert app.is_running


async def test_graph_double_click_ignores_inert_rows_and_stale_clicks(branch_repo):
    (branch_repo / "pending.txt").write_text("untracked change\n")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        table, point = await graph_point(app, pilot, "wip")
        await click(pilot, table, point, times=2)
        await click(pilot, table, (2, 0), times=2)  # Header.
        assert len(app.screen_stack) == 1
        tip = git(branch_repo, "rev-parse", "test")
        table, point = await graph_point(app, pilot, tip)
        await post_click(pilot, table, point, chain=1, button=3)
        await post_click(pilot, table, point, chain=2, button=3)
        assert isinstance(app.screen, BranchContextMenu)
        assert git(branch_repo, "branch", "--show-current") == "main"
        await pilot.press("escape")
        assert len(app.screen_stack) == 1
        await click(pilot, table, point)
        app.repaint_history()
        await pilot.pause()
        await post_click(pilot, table, point, chain=2)
        assert len(app.screen_stack) == 1
        assert git(branch_repo, "branch", "--show-current") == "main"


async def test_checkout_selected_commit_keeps_workspace_running(branch_repo, git_run):
    git_run(branch_repo, "switch", "test")
    git_run(branch_repo, "commit", "--allow-empty", "-m", "[bold]literal[/bold]")
    git_run(branch_repo, "switch", "main")
    app = RhizomeApp(branch_repo)
    # Textual's default test mode hides toasts; production renders them.
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        tip = git(branch_repo, "rev-parse", "test")
        app.jump_to(tip)
        await settle(app, pilot)
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, ActionPicker)
        options = app.screen.query_one(OptionList)
        options.highlighted = options.get_option_index("checkout-commit")
        await pilot.press("enter")
        await settle(app, pilot)
        assert git(branch_repo, "rev-parse", "HEAD") == tip
        assert app.snapshot_data.head == tip
        assert not app.snapshot_data.branch
        assert app._history_key == tip
        assert len(app.screen_stack) == 1
        assert app.is_running
        assert app.query_one(CommitFileTree).root.label.plain == "CHANGED FILES (0)"
        assert "[bold]literal[/bold]" in app.query_one(Toast).render().plain
        await pilot.press("ctrl+r")
        await settle(app, pilot)
        assert app.is_running


async def test_checkout_error_notification_is_literal_and_keeps_workspace_running(branch_repo):
    from rhizome.models import Action

    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        missing = "refs/heads/missing[/literal]"
        app.run_mutation(Action("checkout", target=missing))
        await settle(app, pilot)
        assert app.is_running
        assert app.snapshot_data.branch == "main"
        assert git(branch_repo, "branch", "--show-current") == "main"
        assert missing in app._last_error
        assert missing in app.query_one(Toast).render().plain
        assert not app.busy


async def test_remote_double_click_selects_without_changing_upstream(branch_repo):
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before_head = git(branch_repo, "rev-parse", "HEAD")
        before_upstream = git(branch_repo, "rev-parse", "--abbrev-ref", "@{upstream}")
        await click_reference(app, pilot, "refs/remotes/origin/test", times=2)
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert git(branch_repo, "branch", "--show-current") == "main"
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        assert git(branch_repo, "rev-parse", "--abbrev-ref", "@{upstream}") == before_upstream
        assert "Checkout" in app.query_one("#status-line", Static).renderable.plain


async def test_out_of_window_tip_does_not_filter_or_autoload_but_explicit_load_reveals_it(
    branch_repo,
):
    main = git(branch_repo, "rev-parse", "main")
    old_topic = git(branch_repo, "rev-parse", "test")
    fast_commit_chain(branch_repo, "refs/heads/test", old_topic, 301)
    topic = git(branch_repo, "rev-parse", "test")
    git(branch_repo, "switch", "test")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)

        def loaded_rows():
            return [
                row.key.value
                for row in table.ordered_rows
                if not str(row.key.value).startswith("connector-") and row.key.value != "wip"
            ]

        assert len(loaded_rows()) == 300
        assert main not in loaded_rows() and topic in loaded_rows()
        table.move_cursor(row=table.get_row_index(topic), animate=False)
        table.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        before = loaded_rows()

        await click_reference(app, pilot, "refs/heads/main")
        await settle(app, pilot)
        assert loaded_rows() == before
        assert table.ordered_rows[table.cursor_row].key.value == topic
        assert git(branch_repo, "branch", "--show-current") == "test"
        assert "Tip not in this window" in str(app.query_one("#history-caption", Static).render())

        assert await pilot.click("#load-more")
        await settle(app, pilot)
        assert len(loaded_rows()) <= 300
        assert main in loaded_rows()
        assert topic not in loaded_rows()
        assert git(branch_repo, "branch", "--show-current") == "test"
        assert "Tip not in this window" not in str(
            app.query_one("#history-caption", Static).render()
        )


async def test_sidebar_search_does_not_change_graph_or_steal_graph_shortcuts(demo_repo):
    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        graph_search = app.query_one("#graph-filter", Input)
        graph_search.value = "credential"
        await settle(app, pilot)
        before = app._graph_rows
        selected = app._history_key
        search = app.query_one("#ref-filter", Input)
        search.focus()
        await pilot.pause()
        search.value = "origin"
        search.action_end()
        await pilot.press("g")
        await settle(app, pilot)
        assert search.value == "origing"
        assert app.graph_style == "smooth"
        assert app._graph_rows is before
        assert app._history_key == selected
        await pilot.press("escape")
        await settle(app, pilot)
        assert search.value == ""
        assert graph_search.value == "credential"
        assert app._graph_rows is before
        assert app._history_key == selected
        search.value = "   "
        await pilot.pause()
        await pilot.press("escape")
        await settle(app, pilot)
        assert search.value == ""
        assert graph_search.value == "credential"
        assert app._history_key == selected


async def test_real_local_remote_badges_separate_when_local_branch_advances(branch_repo, git_run):
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        base = git(branch_repo, "rev-parse", "main")
        assert "⑂" in table.get_cell(base, "refs").plain
        assert "↗" in table.get_cell(base, "refs").plain
        assert "▤" not in table.get_cell(base, "refs").plain
        (branch_repo / "local.txt").write_text("local advance\n")
        git_run(branch_repo, "add", ".")
        git_run(branch_repo, "commit", "-m", "Advance local main")
        tip = git(branch_repo, "rev-parse", "HEAD")
        app.action_refresh()
        await settle(app, pilot)
        assert "⑂" in table.get_cell(tip, "refs").plain
        assert "↗" not in table.get_cell(tip, "refs").plain
        assert "↗" in table.get_cell(base, "refs").plain
        assert "⑂" not in table.get_cell(base, "refs").plain
        git_run(branch_repo, "push", "origin", "main")
        app.action_refresh()
        await settle(app, pilot)
        assert "⑂" in table.get_cell(tip, "refs").plain
        assert "↗" in table.get_cell(tip, "refs").plain


@pytest.mark.parametrize("detached", [False, True])
async def test_worktree_graph_marker_tracks_other_checkout_without_moving_this_one(
    branch_repo, git_run, tmp_path, detached
):
    linked = tmp_path / "linked tree"
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        base = app.snapshot_data.head
        assert "▤" not in app._ref_badges[base].text.plain
        geometry = [(row.commit.oid, row.graph.plain) for row in app._graph_rows]
        mode = ["--detach"] if detached else ["-b", "feature/linked"]
        git_run(branch_repo, "worktree", "add", *mode, str(linked), "main")
        app.action_refresh()
        await settle(app, pilot)
        assert [(row.commit.oid, row.graph.plain) for row in app._graph_rows] == geometry
        table = app.query_one("#graph-table", DataTable)
        assert table.get_cell(base, "refs").plain.count("▤") == 1
        assert str(linked) in app._ref_badges[base].description.plain

        git_run(linked, "commit", "--allow-empty", "-m", "Advance linked worktree")
        tip = git(linked, "rev-parse", "HEAD")
        app.action_refresh()
        await settle(app, pilot)
        assert tip in app._commits  # --all also reaches detached linked worktree HEADs.
        assert "▤" in table.get_cell(tip, "refs").plain
        assert ("⑂" in table.get_cell(tip, "refs").plain) is not detached
        assert "▤" not in table.get_cell(base, "refs").plain  # Only the primary checkout remains.
        assert "↗" in table.get_cell(base, "refs").plain
        assert app.snapshot_data.head == base
        assert app.snapshot_data.branch == "main"

        git_run(branch_repo, "worktree", "remove", str(linked))
        app.action_refresh()
        await settle(app, pilot)
        badge = app._ref_badges.get(tip)
        assert badge is None or "▤" not in badge.text.plain
        assert "▤" not in table.get_cell(base, "refs").plain
        assert git(branch_repo, "rev-parse", "HEAD") == base


async def test_new_branch_at_same_tip_does_not_invent_divergence(branch_repo):
    from rhizome.models import Action

    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before = [(row.commit.oid, row.graph.plain) for row in app._graph_rows]
        app.run_mutation(Action("create-branch", name="Connor"))
        await settle(app, pilot)
        assert app.snapshot_data.branch == "Connor"
        assert [(row.commit.oid, row.graph.plain) for row in app._graph_rows] == before
        cell = app.query_one("#graph-table", DataTable).get_cell(app.snapshot_data.head, "refs")
        assert all(marker in cell.plain for marker in ("⑂", "↗", "Connor", "main"))


async def test_single_click_and_checkout_preserve_scrolled_graph_viewport(branch_repo, git_run):
    for index in range(12):
        git_run(branch_repo, "switch", "-c", f"parallel-{index:02}", "main")
        git_run(branch_repo, "commit", "--allow-empty", "-m", f"Independent tip {index}")
    git_run(branch_repo, "switch", "main")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(130, 32)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", ResizableDataTable)
        # Default columns now fit the viewport; explicit user sizing may scroll.
        table.set_column_width("subject", table.size.width + 40)
        await pilot.pause()
        table.scroll_to(x=table.max_scroll_x, y=table.max_scroll_y, animate=False)
        await pilot.pause()
        position = table.scroll_offset
        assert position.x > 0 and position.y > 0
        cursor = table.cursor_coordinate
        key = app._history_key
        rows = app._graph_rows
        geometry = [(row.commit.oid, row.graph.plain) for row in rows]
        await click_reference(app, pilot, "refs/heads/test")
        await settle(app, pilot)
        assert app._graph_rows is rows
        assert app._history_key == key
        assert table.cursor_coordinate == cursor
        assert table.scroll_offset == position
        await click_reference(app, pilot, "refs/heads/test", button=3)
        await choose_option(app, pilot, "checkout")
        await settle(app, pilot)
        assert app.snapshot_data.branch == "test"
        assert [(row.commit.oid, row.graph.plain) for row in app._graph_rows] == geometry
        assert app._history_key == key
        assert table.cursor_coordinate == cursor
        assert table.scroll_offset == position


async def test_hidden_selection_survives_refresh_and_stale_checkout_messages_are_ignored(
    branch_repo,
    git_run,
):
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/test")
        stale = app._sidebar_selection
        search = app.query_one("#ref-filter", Input)
        search.value = "origin"
        await pilot.pause()
        git_run(branch_repo, "tag", "new-pointer")
        app.action_refresh()
        await settle(app, pilot)
        assert app._sidebar_selection == stale
        assert search.value == "origin"
        # Same branch name, different tip: an old UI event is no longer valid.
        git_run(branch_repo, "update-ref", "refs/heads/test", "main")
        app.action_refresh()
        await settle(app, pilot)
        assert app._sidebar_selection.oid != stale.oid
        app.post_message(ReferenceSidebar.CheckoutRequested(stale, app.repo.root))
        app.post_message(
            ReferenceSidebar.CheckoutRequested(
                app._sidebar_selection, app.repo.root / "different-repo"
            )
        )
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert git(branch_repo, "branch", "--show-current") == "main"
        git_run(branch_repo, "branch", "-d", "test")
        app.action_refresh()
        await settle(app, pilot)
        assert app._sidebar_selection is None
        assert search.value == "origin"
        assert app.is_running
        assert "test is no longer available." in app.query_one(Toast).render().plain


@pytest.mark.parametrize("size", [(120, 40), (170, 48), (100, 32)])
async def test_compact_commit_header_gives_space_to_file_tree(git_repo, git_run, size):
    title = "Finance model changes"
    body = "Long commit description that should not occupy the pane.\n" * 25
    paths = [f"models/marts/finance/fact_metric_{index:02}.sql" for index in range(12)]
    for index, path in enumerate(paths):
        file = git_repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"select {index} as metric;\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", title, "-m", body)
    app = RhizomeApp(git_repo)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        app.jump_to(app.snapshot_data.head)
        await settle(app, pilot)
        header = app.query_one("#commit-detail", Static)
        assert title in str(header.render())
        assert "Long commit description" not in str(header.render())
        assert header.region.height <= 4
        tree = app.query_one(CommitFileTree)
        assert tree.content_size.height >= 8
        assert tree.region.bottom <= app.query_one("#workspace").region.bottom
        assert tree.root.label.plain == "CHANGED FILES (12)"
        first = tree._path_nodes[tree._key_for(next(iter(tree._files.values())))]
        tree.move_cursor(first, animate=False)
        tree._activate(first)
        await settle(app, pilot)
        assert "select 0 as metric" in str(app.query_one("#center-diff", Static).render())
        nodes = [
            node for node in tree._path_nodes.values() if getattr(node.data, "path", "") == paths[1]
        ]
        assert len(nodes) == 1
        assert nodes[0].label.plain == "A fact_metric_01.sql"
        tree.focus()
        await pilot.press("j")
        await settle(app, pilot)
        assert tree.selected_file.path == paths[1]
        # Arrow navigation changes the file-tree selection but does not steal
        # the explicitly opened center diff.
        preview = app.query_one("#center-diff", Static).renderable.plain
        assert "select 0 as metric" in preview
        assert "diff --git" not in preview
        assert "+++ b/" not in preview
        parent = nodes[0].parent
        parent.collapse()
        await pilot.pause()
        git_run(git_repo, "tag", "extra-pointer")
        app.action_refresh()
        await settle(app, pilot)
        assert tree.selected_file.path == paths[1]
        assert tree._path_nodes[parent.data.key].is_collapsed
        assert "select 0 as metric" in str(app.query_one("#center-diff", Static).render())


@pytest.mark.parametrize("operation", ["fetch", "pull", "push"])
async def test_network_toolbar_actions_obey_m1_gates(branch_repo, bare_remote, git_run, operation):
    remote_before = git(bare_remote, "rev-parse", "refs/heads/main")
    operation_tip = ""
    if operation == "push":
        git_run(branch_repo, "commit", "--allow-empty", "-m", "Local main advance")
        operation_tip = git(branch_repo, "rev-parse", "HEAD")

    before_head = git(branch_repo, "rev-parse", "HEAD")
    before_content = (branch_repo / "base.txt").read_bytes()
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before_refs = git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)")
        if operation in {"fetch", "pull"}:
            tree = git(bare_remote, "rev-parse", "refs/heads/main^{tree}")
            operation_tip = git_run(
                bare_remote,
                "commit-tree",
                tree,
                "-p",
                remote_before,
                "-m",
                "Remote main advance",
            ).stdout.strip()
            git_run(bare_remote, "update-ref", "refs/heads/main", operation_tip)
            assert git(branch_repo, "rev-parse", "refs/remotes/origin/main") == remote_before
        if operation == "pull":
            # Infrequent/gated actions now live in the command menu.
            await pilot.click("#actions")
            await choose_option(app, pilot, "pull")
        else:
            await pilot.click(f"#{operation}")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert app.snapshot_data.branch == "main"
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        assert (branch_repo / "base.txt").read_bytes() == before_content
        if operation == "pull":
            assert "pull is unavailable until its M3 guarded plan" in app._last_error
            assert (
                git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
            )
            assert git(branch_repo, "rev-parse", "refs/remotes/origin/main") == remote_before
            assert git(bare_remote, "rev-parse", "refs/heads/main") == operation_tip
        elif operation == "fetch":
            assert not app._last_error
            assert git(branch_repo, "rev-parse", "refs/remotes/origin/main") == operation_tip
            assert git(bare_remote, "rev-parse", "refs/heads/main") == operation_tip
        else:
            assert not app._last_error
            assert git(bare_remote, "rev-parse", "refs/heads/main") == operation_tip


@pytest.fixture
def rebase_repo(branch_repo, git_run):
    git_run(branch_repo, "switch", "-c", "feature/payments", "test")
    (branch_repo / "feature.txt").write_text("my feature\n")
    git_run(branch_repo, "add", ".")
    git_run(branch_repo, "commit", "-m", "Feature work")
    git_run(branch_repo, "switch", "test")
    (branch_repo / "teammate.txt").write_text("teammate's new test commit\n")
    git_run(branch_repo, "add", ".")
    git_run(branch_repo, "commit", "-m", "Teammate work")
    git_run(branch_repo, "push", "origin", "test")
    git_run(branch_repo, "switch", "feature/payments")
    return branch_repo


@pytest.mark.parametrize("target", ["refs/heads/test", "refs/remotes/origin/test"])
async def test_right_click_rebase_is_gated_without_mutating_selected_context(rebase_repo, target):
    before_head = git(rebase_repo, "rev-parse", "HEAD")
    before_index = git(rebase_repo, "ls-files", "--stage")
    before_status = git(rebase_repo, "status", "--porcelain=v1")
    feature_content = (rebase_repo / "feature.txt").read_bytes()
    target_oid = git(rebase_repo, "rev-parse", target)
    assert not (rebase_repo / "teammate.txt").exists()
    app = RhizomeApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        before_refs = git(rebase_repo, "for-each-ref", "--format=%(refname) %(objectname)")
        # Opening and dismissing retain the exact local/remote target context.
        await click_reference(app, pilot, target, button=3)
        assert isinstance(app.screen, BranchContextMenu)
        menu = app.screen.query_one("#branch-context-menu", OptionList)
        assert app.screen.styles.background.a == 0
        assert menu.region.right <= app.screen.size.width
        assert menu.region.bottom <= app.screen.size.height
        label = menu.get_option("rebase").prompt.plain
        assert label == "Rebase feature/payments onto " + (
            "origin/test" if "remotes" in target else "test"
        )
        await pilot.press("escape")
        assert git(rebase_repo, "rev-parse", "HEAD") == before_head

        await click_reference(app, pilot, target, button=3)
        await choose_option(app, pilot, "rebase")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert "rebase is unavailable until its M3 guarded plan" in app._last_error
        assert app.snapshot_data.branch == "feature/payments"
        assert app.snapshot_data.head == before_head
        assert git(rebase_repo, "rev-parse", target) == target_oid
        assert git(rebase_repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
        assert git(rebase_repo, "ls-files", "--stage") == before_index
        assert git(rebase_repo, "status", "--porcelain=v1") == before_status
        assert (rebase_repo / "feature.txt").read_bytes() == feature_content
        assert not (rebase_repo / "teammate.txt").exists()
        assert not app.snapshot_data.operation
        assert app.is_running


async def test_rebase_menu_rejects_external_checkout_while_open(rebase_repo, git_run):
    before = git(rebase_repo, "rev-parse", "feature/payments")
    app = RhizomeApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/test", button=3)
        assert isinstance(app.screen, BranchContextMenu)
        git_run(rebase_repo, "switch", "main")
        main = git(rebase_repo, "rev-parse", "HEAD")
        await choose_option(app, pilot, "rebase")
        await settle(app, pilot)
        assert "Branch changed" in app._last_error
        assert app.snapshot_data.branch == "main"
        assert app.snapshot_data.head == main
        assert git(rebase_repo, "rev-parse", "feature/payments") == before
        assert not app.snapshot_data.operation


@pytest.mark.parametrize("state", ["current", "detached", "busy"])
async def test_right_click_does_not_offer_invalid_rebase(rebase_repo, git_run, state):
    if state == "detached":
        git_run(rebase_repo, "switch", "--detach")
    app = RhizomeApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        before = git(rebase_repo, "rev-parse", "HEAD")
        if state == "busy":
            app.set_busy(True)
        target = "refs/heads/feature/payments" if state == "current" else "refs/heads/test"
        await click_reference(app, pilot, target, button=3)
        if state == "busy":
            assert len(app.screen_stack) == 1
        else:
            assert isinstance(app.screen, BranchContextMenu)
            options = app.screen.query_one(OptionList)
            assert "rebase" not in {
                options.get_option_at_index(i).id for i in range(options.option_count)
            }
            assert options.get_option("copy-sha")
            await pilot.press("escape")
        assert git(rebase_repo, "rev-parse", "HEAD") == before
        app.set_busy(False)


async def test_rebase_dirty_worktree_error_keeps_changes_and_refreshes(rebase_repo):
    before = git(rebase_repo, "rev-parse", "HEAD")
    (rebase_repo / "feature.txt").write_text("uncommitted edits\n")
    app = RhizomeApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/test", button=3)
        await choose_option(app, pilot, "rebase")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert app._last_error
        assert app.snapshot_data.branch == "feature/payments"
        assert app.snapshot_data.head == before
        assert not app.snapshot_data.operation
        assert (rebase_repo / "feature.txt").read_text() == "uncommitted edits\n"
        assert app.snapshot_data.files and not app.busy and app.is_running


@pytest.mark.parametrize("finish", ["abort", "continue"])
async def test_external_rebase_conflict_finish_is_gated_without_altering_operation(
    rebase_repo, git_run, finish
):
    (rebase_repo / "base.txt").write_text("feature version\n")
    git_run(rebase_repo, "add", ".")
    git_run(rebase_repo, "commit", "-m", "Feature changes base")
    feature_ref = git(rebase_repo, "rev-parse", "refs/heads/feature/payments")
    git_run(rebase_repo, "switch", "test")
    (rebase_repo / "base.txt").write_text("test version\n")
    git_run(rebase_repo, "add", ".")
    git_run(rebase_repo, "commit", "-m", "Teammate changes base")
    git_run(rebase_repo, "switch", "feature/payments")
    result = subprocess.run(
        ["git", "-C", str(rebase_repo), "rebase", "test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert git(rebase_repo, "ls-files", "-u", "--", "base.txt")
    rebase_state = rebase_repo / git(rebase_repo, "rev-parse", "--git-path", "rebase-merge")
    assert rebase_state.is_dir()

    app = RhizomeApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        assert app.snapshot_data.operation == "rebase"
        assert app.snapshot_data.conflicts
        assert app.query_one("#operation-banner").display

        if finish == "continue":
            (rebase_repo / "base.txt").write_text("resolved together\n")
            git_run(rebase_repo, "add", "base.txt")
            app.action_refresh()
            await settle(app, pilot)
            assert app.snapshot_data.operation == "rebase"
            assert not app.snapshot_data.conflicts

        before_head = git(rebase_repo, "rev-parse", "HEAD")
        before_refs = git(rebase_repo, "for-each-ref", "--format=%(refname) %(objectname)")
        before_index = git(rebase_repo, "ls-files", "--stage")
        before_status = git(rebase_repo, "status", "--porcelain=v1")
        before_content = (rebase_repo / "base.txt").read_bytes()
        await pilot.click(f"#{finish}")
        await settle(app, pilot)

        assert len(app.screen_stack) == 1
        assert f"{finish} is unavailable until its M3 guarded plan" in app._last_error
        assert app.snapshot_data.operation == "rebase"
        assert app.query_one("#operation-banner").display
        assert rebase_state.is_dir()
        assert git(rebase_repo, "rev-parse", "HEAD") == before_head
        assert git(rebase_repo, "rev-parse", "refs/heads/feature/payments") == feature_ref
        assert git(rebase_repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
        assert git(rebase_repo, "ls-files", "--stage") == before_index
        assert git(rebase_repo, "status", "--porcelain=v1") == before_status
        assert (rebase_repo / "base.txt").read_bytes() == before_content


@pytest.mark.parametrize("native", [True, False])
async def test_expanded_branch_menu_copy_actions_do_not_mutate(branch_repo, monkeypatch, native):
    from rhizome.clipboard import CopyResult

    copied = []
    monkeypatch.setattr(
        "rhizome.clipboard._copy_native", lambda value: copied.append(value) or native
    )
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(100, 24)) as pilot:
        await settle(app, pilot)
        before = app.snapshot_data.head
        await click_reference(app, pilot, "refs/heads/main", button=3)
        assert isinstance(app.screen, BranchContextMenu)
        options = app.screen.query_one(OptionList)
        ids = {options.get_option_at_index(i).id for i in range(options.option_count)}
        assert {"pull", "push", "edit-message", "reset"} <= ids, (
            app.screen.current_branch,
            app.screen.target,
            ids,
        )
        assert options.region.right <= 100 and options.region.bottom <= 24
        await choose_option(app, pilot, "copy-branch")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.clipboard == "main"
        result = CopyResult.SYSTEM if native else CopyResult.TERMINAL
        assert str(app.query_one("#status-line").renderable) == result.value
        await click_reference(app, pilot, "refs/remotes/origin/test", button=3)
        await choose_option(app, pilot, "copy-sha")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.clipboard == git(branch_repo, "rev-parse", "origin/test")
        assert copied == ["main", app.clipboard]
        assert git(branch_repo, "rev-parse", "HEAD") == before


async def test_context_menu_resize_keeps_workspace_alive_and_actions_usable(branch_repo):
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        before = app.snapshot_data.head
        await click_reference(app, pilot, "refs/heads/main", button=3)
        await pilot.resize_terminal(80, 20)
        await pilot.press("end")
        await pilot.pause(0.2)
        assert isinstance(app.screen, BranchContextMenu)
        options = app.screen.query_one(OptionList)
        assert options.region.right <= 80 and options.region.bottom <= 20
        await choose_option(app, pilot, "copy-sha")
        assert app.clipboard == before
        await pilot.resize_terminal(170, 50)
        await pilot.pause(0.2)
        assert app.query_one("#graph-table", DataTable).row_count > 1
        assert app.is_running and not app._last_error


async def test_context_branch_creation_uses_clicked_tip_not_inspector(branch_repo):
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        app.jump_to(app.snapshot_data.head)
        await settle(app, pilot)
        tip = git(branch_repo, "rev-parse", "test")
        await click_reference(app, pilot, "refs/remotes/origin/test", button=3)
        await choose_option(app, pilot, "create-branch")
        assert isinstance(app.screen, PromptDialog)
        app.screen.query_one(Input).value = "from-context"
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.snapshot_data.branch == "from-context"
        assert app.snapshot_data.head == tip
        assert not app._last_error


async def test_context_prompt_rejects_target_move_before_submission(branch_repo, git_run):
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before = app.snapshot_data.head
        await click_reference(app, pilot, "refs/heads/test", button=3)
        await choose_option(app, pilot, "create-branch")
        git_run(branch_repo, "branch", "-f", "test", "main")
        app.screen.query_one(Input).value = "must-not-exist"
        await pilot.press("enter")
        await settle(app, pilot)
        assert app._last_error
        assert "must-not-exist" not in git(branch_repo, "branch", "--format=%(refname:short)")
        assert app.snapshot_data.head == before


async def test_graph_context_uses_clicked_commit_and_ignores_inert_rows(branch_repo):
    from rhizome.app import HistoryTable

    (branch_repo / "pending.txt").write_text("untracked change\n")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/main")
        tip = git(branch_repo, "rev-parse", "test")
        table, point = await graph_point(app, pilot, tip)
        await post_click(pilot, table, point, button=3)
        assert isinstance(app.screen, BranchContextMenu)
        options = app.screen.query_one(OptionList)
        assert options.get_option("checkout")
        assert "rename-branch" not in {
            options.get_option_at_index(i).id for i in range(options.option_count)
        }
        await choose_option(app, pilot, "copy-sha")
        assert app.clipboard == tip
        table, point = await graph_point(app, pilot, "wip")
        await post_click(pilot, table, point, button=3)
        assert len(app.screen_stack) == 1
        app.post_message(HistoryTable.ContextRequested(tip, table.revision - 1, 40, 10))
        await pilot.pause()
        assert len(app.screen_stack) == 1
        table, point = await graph_point(app, pilot, tip)
        await post_click(pilot, table, point, button=3)
        await choose_option(app, pilot, "checkout")
        await choose_option(app, pilot, "detached")
        await settle(app, pilot)
        assert app.snapshot_data.head == tip and not app.snapshot_data.branch
        assert not app._last_error


async def test_graph_badge_context_resolves_exact_branch(branch_repo):
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        tip = git(branch_repo, "rev-parse", "test")
        table, point = await graph_point(app, pilot, tip, column="refs")
        # Badge names carry exact ref metadata; icon-only cells remain commit context.
        x, y = point
        for offset in range(table.columns["refs"].width):
            style = app.screen.get_style_at(table.region.x + x + offset, table.region.y + y)
            if style.meta.get("context_ref") == "refs/heads/test":
                await post_click(pilot, table, (x + offset, y), button=3)
                break
        else:
            raise AssertionError("No clickable test badge")
        assert isinstance(app.screen, BranchContextMenu)
        assert "test" in app.screen.query_one(OptionList).get_option("rename-branch").prompt.plain
        await choose_option(app, pilot, "copy-branch")
        assert app.clipboard == "test"


@pytest.mark.parametrize("mode", ["reset-soft", "reset-mixed", "reset-hard"])
async def test_graph_reset_picker_refuses_gated_modes_without_mutation(branch_repo, git_run, mode):
    git_run(branch_repo, "switch", "test")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        target = git(branch_repo, "rev-parse", "main")
        before_head = git(branch_repo, "rev-parse", "HEAD")
        before_refs = git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)")
        before_index = git(branch_repo, "ls-files", "--stage")
        before_status = git(branch_repo, "status", "--porcelain=v1")
        topic_content = (branch_repo / "topic.txt").read_bytes()
        table, point = await graph_point(app, pilot, target)
        await post_click(pilot, table, point, button=3)
        await choose_option(app, pilot, "reset")
        assert isinstance(app.screen, ActionPicker)
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        await pilot.press("escape")
        assert git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs

        await post_click(pilot, table, point, button=3)
        await choose_option(app, pilot, "reset")
        await choose_option(app, pilot, mode)
        await settle(app, pilot)
        assert mode in app._last_error and "unavailable" in app._last_error
        assert app.snapshot_data.head == before_head
        assert app.snapshot_data.branch == "test"
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        assert git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
        assert git(branch_repo, "ls-files", "--stage") == before_index
        assert git(branch_repo, "status", "--porcelain=v1") == before_status
        assert (branch_repo / "topic.txt").read_bytes() == topic_content


async def test_context_edit_latest_message_is_gated_and_preserves_staged_content(
    branch_repo, git_run
):
    (branch_repo / "base.txt").write_text("staged, not part of message edit\n")
    git_run(branch_repo, "add", "base.txt")
    before_head = git(branch_repo, "rev-parse", "HEAD")
    before_tree = git(branch_repo, "rev-parse", "HEAD^{tree}")
    before_index = git(branch_repo, "ls-files", "--stage")
    before_status = git(branch_repo, "status", "--porcelain=v1")
    staged_content = git(branch_repo, "show", ":base.txt")
    worktree_content = (branch_repo / "base.txt").read_bytes()
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before_refs = git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)")
        await click_reference(app, pilot, "refs/heads/main", button=3)
        await choose_option(app, pilot, "edit-message")
        await settle(app, pilot)
        assert isinstance(app.screen, MessageDialog)
        editor = app.screen.query_one(TextArea)
        assert "Base" in editor.text
        editor.load_text("A better subject\n\nWith a body.")
        await pilot.click("#confirm")
        await settle(app, pilot)
        assert "protected" in app._last_error.lower() or "unavailable" in app._last_error.lower()
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        assert git(branch_repo, "log", "-1", "--format=%B") == "Base"
        assert git(branch_repo, "rev-parse", "HEAD^{tree}") == before_tree
        assert git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
        assert git(branch_repo, "ls-files", "--stage") == before_index
        assert git(branch_repo, "status", "--porcelain=v1") == before_status
        assert git(branch_repo, "show", ":base.txt") == staged_content
        assert (branch_repo / "base.txt").read_bytes() == worktree_content


async def test_context_create_worktree_keeps_current_checkout(branch_repo, tmp_path):
    destination = tmp_path / "context worktree"
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before = app.snapshot_data.head
        target = git(branch_repo, "rev-parse", "test")
        await click_reference(app, pilot, "refs/heads/test", button=3)
        await choose_option(app, pilot, "create-worktree")
        await choose_option(app, pilot, "new-branch")
        app.screen.query_one(Input).value = "parallel-work"
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one(Input).value = str(destination)
        await pilot.press("enter")
        await settle(app, pilot)
        assert not app._last_error
        assert git(destination, "rev-parse", "HEAD") == target
        assert git(destination, "branch", "--show-current") == "parallel-work"
        assert app.snapshot_data.head == before and app.snapshot_data.branch == "main"


@pytest.mark.parametrize("target", ["refs/heads/main", "refs/remotes/origin/test"])
async def test_context_upstream_updates_explicit_branch(branch_repo, target):
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, target, button=3)
        await choose_option(app, pilot, "set-upstream")
        if target == "refs/heads/main":
            await choose_option(app, pilot, "refs/remotes/origin/test")
        await settle(app, pilot)
        assert not app._last_error
        assert git(branch_repo, "config", "branch.main.merge") == "refs/heads/test"
        assert app.snapshot_data.branch == "main"


async def test_context_apply_patch_is_gated_without_mutating_worktree_or_index(
    branch_repo, tmp_path
):
    patch = tmp_path / "change.patch"
    patch.write_text(
        "diff --git a/base.txt b/base.txt\n--- a/base.txt\n+++ b/base.txt\n@@ -1 +1 @@\n-base\n+patched\n"
    )
    before_head = git(branch_repo, "rev-parse", "HEAD")
    before_index = git(branch_repo, "ls-files", "--stage")
    before_status = git(branch_repo, "status", "--porcelain=v1")
    before_content = (branch_repo / "base.txt").read_bytes()
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before_refs = git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)")
        await click_reference(app, pilot, "refs/heads/main", button=3)
        await choose_option(app, pilot, "apply-patch")
        app.screen.query_one(Input).value = str(patch)
        await pilot.press("enter")
        await settle(app, pilot)
        assert "apply-patch is unavailable until its M3 guarded plan" in app._last_error
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        assert git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
        assert git(branch_repo, "ls-files", "--stage") == before_index
        assert git(branch_repo, "status", "--porcelain=v1") == before_status
        assert (branch_repo / "base.txt").read_bytes() == before_content


async def test_context_remote_delete_picker_is_explicit_but_m1_refuses_deletion(
    branch_repo, bare_remote
):
    before_head = git(branch_repo, "rev-parse", "HEAD")
    before_remote_refs = git(bare_remote, "for-each-ref", "--format=%(refname) %(objectname)")
    before_content = (branch_repo / "base.txt").read_bytes()
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before_local_refs = git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)")
        await click_reference(app, pilot, "refs/remotes/origin/test", button=3)
        await choose_option(app, pilot, "delete-remote-branch")
        await settle(app, pilot)
        assert isinstance(app.screen, ActionPicker)
        label = app.screen.query_one(OptionList).get_option("delete").prompt.plain
        assert "Delete test on origin" in label and str(bare_remote) in label
        await pilot.press("escape")
        assert (
            git(bare_remote, "for-each-ref", "--format=%(refname) %(objectname)")
            == before_remote_refs
        )

        await click_reference(app, pilot, "refs/remotes/origin/test", button=3)
        await choose_option(app, pilot, "delete-remote-branch")
        await settle(app, pilot)
        await choose_option(app, pilot, "delete")
        await settle(app, pilot)
        assert "unavailable until exact target-ref restoration is proven" in app._last_error
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        assert (
            git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)")
            == before_local_refs
        )
        assert (
            git(bare_remote, "for-each-ref", "--format=%(refname) %(objectname)")
            == before_remote_refs
        )
        assert (branch_repo / "base.txt").read_bytes() == before_content
        assert app.snapshot_data.branch == "main"


async def test_context_remote_delete_mapping_change_remains_fail_closed(
    branch_repo, bare_remote, git_run
):
    oid = git(branch_repo, "rev-parse", "test")
    git_run(bare_remote, "update-ref", "refs/heads/other", oid)
    before_head = git(branch_repo, "rev-parse", "HEAD")
    before_content = (branch_repo / "base.txt").read_bytes()
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/remotes/origin/test", button=3)
        await choose_option(app, pilot, "delete-remote-branch")
        await settle(app, pilot)
        label = app.screen.query_one(OptionList).get_option("delete").prompt.plain
        assert "Delete test on origin" in label and str(bare_remote) in label
        git_run(
            branch_repo,
            "config",
            "remote.origin.fetch",
            "+refs/heads/other:refs/remotes/origin/test",
        )
        before_local_refs = git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)")
        before_remote_refs = git(bare_remote, "for-each-ref", "--format=%(refname) %(objectname)")
        await choose_option(app, pilot, "delete")
        await settle(app, pilot)
        assert "unavailable until exact target-ref restoration is proven" in app._last_error
        assert git(branch_repo, "rev-parse", "HEAD") == before_head
        assert (
            git(branch_repo, "for-each-ref", "--format=%(refname) %(objectname)")
            == before_local_refs
        )
        assert (
            git(bare_remote, "for-each-ref", "--format=%(refname) %(objectname)")
            == before_remote_refs
        )
        assert git(branch_repo, "config", "remote.origin.fetch") == (
            "+refs/heads/other:refs/remotes/origin/test"
        )
        assert (branch_repo / "base.txt").read_bytes() == before_content
        assert git(bare_remote, "rev-parse", "refs/heads/test") == oid
        assert git(bare_remote, "rev-parse", "refs/heads/other") == oid


async def test_context_copy_branch_link_uses_remote_source_not_tracking_alias(branch_repo, git_run):
    oid = git(branch_repo, "rev-parse", "test")
    git_run(branch_repo, "remote", "set-url", "origin", "https://github.com/org/repo.git")
    git_run(
        branch_repo,
        "config",
        "remote.origin.fetch",
        "+refs/heads/release/*:refs/remotes/origin/cache/*",
    )
    git_run(branch_repo, "update-ref", "refs/remotes/origin/cache/test", oid)
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/remotes/origin/cache/test", button=3)
        await choose_option(app, pilot, "copy-branch-link")
        await settle(app, pilot)
        assert not app._last_error
        assert app.clipboard == "https://github.com/org/repo/tree/release%2Ftest"


@pytest.mark.parametrize("target", ["refs/heads/test", "refs/remotes/origin/test"])
@pytest.mark.parametrize(
    ("remote_url", "created_url"),
    [
        ("git@github.com:org/repo.git", "https://github.com/org/repo/pull/7"),
        (
            "git@ssh.dev.azure.com:v3/org/project/repo",
            "https://dev.azure.com/org/project/_git/repo/pullrequest/7",
        ),
    ],
)
async def test_right_click_creates_pr_from_feature_to_clicked_target(
    branch_repo, git_run, monkeypatch, target, remote_url, created_url
):
    git_run(branch_repo, "switch", "-c", "feature/payments")
    git_run(branch_repo, "push", "-u", "origin", "feature/payments")
    git_run(branch_repo, "remote", "set-url", "origin", remote_url)
    before = git(branch_repo, "rev-parse", "HEAD")
    requests = []
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)

        def create(plan, title, description):
            requests.append((plan, title, description))
            return created_url

        monkeypatch.setattr(app.repo, "create_pull_request", create)
        await click_reference(app, pilot, target, button=3)
        actions = dict(action for action in app.screen.actions if action)
        assert actions["create-pr"].endswith("from feature/payments…")
        await choose_option(app, pilot, "create-pr")
        await settle(app, pilot)
        dialog = app.screen
        assert isinstance(dialog, PullRequestDialog)
        assert dialog.plan.source_branch == "feature/payments"
        assert dialog.plan.target_branch == "test"
        assert not requests
        dialog.query_one("#pr-title", Input).value = "Payments [literal]"
        dialog.query_one("#pr-description", TextArea).load_text("Summary\n\nDetails")
        await pilot.click("#pr-submit")
        await settle(app, pilot)
        assert requests == [(dialog.plan, "Payments [literal]", "Summary\n\nDetails")]
        assert dialog.created_url == created_url
        await pilot.click("#pr-copy")
        assert app.clipboard == created_url
        await pilot.click("#pr-cancel")
        await settle(app, pilot)
        assert created_url in str(app.query_one("#status-line").renderable)
        assert git(branch_repo, "branch", "--show-current") == "feature/payments"
        assert git(branch_repo, "rev-parse", "HEAD") == before


async def test_pr_form_rejects_external_checkout_and_preserves_input(branch_repo, git_run):
    git_run(branch_repo, "remote", "set-url", "origin", "https://github.com/org/repo.git")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/test", button=3)
        await choose_option(app, pilot, "create-pr")
        await settle(app, pilot)
        dialog = app.screen
        assert isinstance(dialog, PullRequestDialog)
        dialog.query_one("#pr-title", Input).value = "Keep this draft"
        await pilot.press("ctrl+o", "ctrl+b", "ctrl+p", "ctrl+r")
        assert app.screen is dialog
        git_run(branch_repo, "switch", "test")
        await pilot.click("#pr-submit")
        await settle(app, pilot)
        assert "Branch changed" in dialog.query_one("#pr-feedback").renderable.plain
        assert dialog.query_one("#pr-title", Input).value == "Keep this draft"
        assert not dialog.created_url
        assert git(branch_repo, "branch", "--show-current") == "test"


async def test_pr_ambiguous_repository_prompts_before_composer(branch_repo, git_run):
    git_run(branch_repo, "config", "--unset", "branch.main.remote")
    git_run(branch_repo, "config", "--unset", "branch.main.merge")
    git_run(branch_repo, "config", "--unset", "branch.test.remote")
    git_run(branch_repo, "config", "--unset", "branch.test.merge")
    git_run(branch_repo, "remote", "set-url", "origin", "https://github.com/org/repo.git")
    git_run(branch_repo, "remote", "add", "team", "https://dev.azure.com/org/project/_git/repo")
    app = RhizomeApp(branch_repo)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/test", button=3)
        await choose_option(app, pilot, "create-pr")
        await settle(app, pilot)
        assert isinstance(app.screen, ActionPicker)
        assert "CREATE PULL REQUEST IN" == app.screen.heading
        assert len(app.screen.actions) == 2
        await choose_option(app, pilot, "1")
        await settle(app, pilot)
        assert isinstance(app.screen, PullRequestDialog)
        assert app.screen.plan.remote == "team"
        assert app.screen.plan.repository.provider == "Azure DevOps"
        await pilot.press("escape")
        assert len(app.screen_stack) == 1


async def test_commit_selection_waits_for_chained_diff_workers(demo_repo, monkeypatch):
    import time

    app = RhizomeApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        original_diff = app.repo.diff

        def slow_diff(*args, **kwargs):
            time.sleep(0.5)  # Model slower Git I/O; detail creates this worker later.
            return original_diff(*args, **kwargs)

        monkeypatch.setattr(app.repo, "diff", slow_diff)
        app.jump_to(next(iter(app._commits)))
        await settle(app, pilot)
        file = next(iter(app.query_one(CommitFileTree)._files.values()))
        app.inspect_commit_file(file)
        await settle(app, pilot)
        preview = app.query_one("#center-diff", Static).renderable.plain
        assert "@@" in preview, [worker.group for worker in app.workers]
        assert "diff --git" not in preview
