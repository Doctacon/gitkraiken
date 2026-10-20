from __future__ import annotations

import subprocess

import pytest
from textual.widgets import DataTable, Input, OptionList, Static, TabbedContent, TextArea, Tree
from textual.widgets._toast import Toast
from ui_helpers import click, post_click

from gitkraiken.app import GitKraikenApp
from gitkraiken.commit_files import CommitFileTree
from gitkraiken.demo import create_demo
from gitkraiken.dialogs import ActionPicker, BranchContextMenu
from gitkraiken.sidebar import ReferenceSidebar, SidebarSelection


@pytest.fixture
def demo_repo(tmp_path):
    return create_demo(tmp_path / "demo")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


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
    app = GitKraikenApp(demo_repo)
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
        assert app.query_one(CommitFileTree).selected_file
        assert "diff --git" in str(app.query_one("#commit-diff", Static).render())


@pytest.mark.parametrize("width", [120, 170])
async def test_history_date_time_column_survives_style_and_size_changes(
    demo_repo, git_run, monkeypatch, local_timezone, width
):
    local_timezone("America/Denver")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2024-02-03T04:05:06+05:30")
    git_run(demo_repo, "commit", "--allow-empty", "-m", "Known author timestamp")
    tip = git(demo_repo, "rev-parse", "HEAD")
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(width, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        for _ in range(2):
            columns = [column.key.value for column in table.ordered_columns]
            assert columns[:4] == ["graph", "refs", "date", "subject"]
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


async def test_stage_commit_and_refresh(demo_repo):
    app = GitKraikenApp(demo_repo)
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
    app = GitKraikenApp(demo_repo)
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


async def test_discard_runs_without_confirmation_and_preserves_index(demo_repo):
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        file = next(f for f in app.snapshot_data.files if f.path == "README.md")
        original = (demo_repo / file.path).read_text()
        staged = git(demo_repo, "show", f":{file.path}")
        app.inspect_worktree_file(file, False)
        app.wip_action("discard")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert (demo_repo / file.path).read_text() != original
        assert (demo_repo / file.path).read_text().strip() == staged
        assert git(demo_repo, "show", f":{file.path}") == staged


async def test_mixed_index_worktree_file_has_two_diff_choices(demo_repo):
    file = demo_repo / "src/workspace.py"
    file.write_text(file.read_text() + "UNSTAGED_ONLY = True\n")
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(app, pilot)
        rows = [
            (f, staged) for f, staged in app._file_rows.values() if f.path == "src/workspace.py"
        ]
        assert len(rows) == 2
        app.inspect_worktree_file(*next(row for row in rows if row[1]))
        await settle(app, pilot)
        assert "UNSTAGED_ONLY" not in str(app.query_one("#diff-view", Static).render())
        app.inspect_worktree_file(*next(row for row in rows if not row[1]))
        await settle(app, pilot)
        assert "UNSTAGED_ONLY" in str(app.query_one("#diff-view", Static).render())


async def test_empty_repository_and_narrow_screen(tmp_path):
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    app = GitKraikenApp(tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(app, pilot)
        assert app.snapshot_data is not None
        assert not app._commits
        assert app.query_one("#graph-table", DataTable).row_count == 1
        app.action_toggle_refs()
        app.action_toggle_inspector()
        await pilot.pause()
        app.action_help()
        await pilot.pause()
        await pilot.press("escape")
        assert len(app.screen_stack) == 1


async def test_mutations_are_not_cancelled_by_refresh(demo_repo, monkeypatch):
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        from gitkraiken.models import Action

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
    app = GitKraikenApp(demo_repo)
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
    app = GitKraikenApp(demo_repo)
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
    app = GitKraikenApp(demo_repo)
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
    app = GitKraikenApp(demo_repo)
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
    from gitkraiken.dialogs import PromptDialog

    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        await pilot.click("#branch")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        app.screen.query_one(Input).value = "feature/from-ui"
        await pilot.click("#confirm")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert app.snapshot_data.branch == "feature/from-ui"


async def test_smooth_graph_toggle_preserves_selection_search_and_navigation(demo_repo):
    from textual.widgets import Button

    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        commits = list(app._commits)
        assert app.graph_style == "smooth"
        assert table.cursor_foreground_priority == "renderable"
        assert table.row_count == len(commits) + 1
        assert "crossing, not a join" in str(app.query_one("#history-heading", Static).render())
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
        assert "crossing" not in str(app.query_one("#history-heading", Static).render())
        assert all(row.height == 1 for row in app._graph_rows)
        assert table.row_count == len(app.snapshot_data.history) + 1
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
        assert "crossing, not a join" in str(app.query_one("#history-heading", Static).render())


async def test_graph_shortcut_does_not_steal_text_input_or_escape_dialog(demo_repo):
    app = GitKraikenApp(demo_repo)
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

    from gitkraiken.models import Commit, HistoryLine

    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    app = GitKraikenApp(tmp_path)
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


async def test_double_click_checkout_runs_immediately_and_keeps_graph_unified(branch_repo):
    app = GitKraikenApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before = [(row.commit.oid, row.graph.plain) for row in app._graph_rows]
        cursor = app._history_key
        await click_reference(app, pilot, "refs/heads/test")
        await settle(app, pilot)
        assert git(branch_repo, "branch", "--show-current") == "main"
        assert len(app.screen_stack) == 1
        assert app._history_key == cursor
        assert [(row.commit.oid, row.graph.plain) for row in app._graph_rows] == before

        await click_reference(app, pilot, "refs/heads/test", times=2)
        await settle(app, pilot)
        assert git(branch_repo, "branch", "--show-current") == "test"
        assert app._history_key == cursor
        assert [(row.commit.oid, row.graph.plain) for row in app._graph_rows] == before
        assert len(app.screen_stack) == 1


@pytest.mark.parametrize("style", ["smooth", "compact"])
async def test_graph_node_double_click_requests_branch_checkout(branch_repo, style):
    app = GitKraikenApp(branch_repo)
    app.graph_style = style
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        tip = git(branch_repo, "rev-parse", "test")
        table, point = await graph_point(app, pilot, tip)
        before = [
            (row.commit.oid if row.commit else None, row.graph.plain) for row in app._graph_rows
        ]
        await click(pilot, table, point)
        await settle(app, pilot)
        assert app._selected_commit.oid == tip
        assert len(app.screen_stack) == 1
        assert git(branch_repo, "branch", "--show-current") == "main"
        await click(pilot, table, point, times=2)
        await settle(app, pilot)
        assert git(branch_repo, "branch", "--show-current") == "test"
        assert app.snapshot_data.branch == "test"
        assert app._history_key == tip
        assert [
            (row.commit.oid if row.commit else None, row.graph.plain) for row in app._graph_rows
        ] == before
        assert len(app.screen_stack) == 1
        assert app.is_running


async def test_graph_double_click_chooses_between_local_branches(branch_repo, git_run):
    git_run(branch_repo, "branch", "another", "test")
    app = GitKraikenApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        tip = git(branch_repo, "rev-parse", "test")
        table, point = await graph_point(app, pilot, tip)
        await click(pilot, table, point, times=2)
        assert isinstance(app.screen, ActionPicker)
        options = app.screen.query_one(OptionList)
        assert options.option_count == 2  # No redundant remote option.
        options.highlighted = options.get_option_index("refs/heads/test")
        await pilot.press("enter")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert app.snapshot_data.branch == "test"
        assert app.is_running


async def test_graph_double_click_remote_only_tip_uses_exact_owner(
    branch_repo, git_run, monkeypatch
):
    from gitkraiken.models import Action

    git_run(branch_repo, "branch", "-D", "test")
    app = GitKraikenApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        tip = git(branch_repo, "rev-parse", "origin/test")
        calls = []
        original = app.repo.execute

        def execute(action):
            calls.append(action)
            return original(action)

        monkeypatch.setattr(app.repo, "execute", execute)
        table, point = await graph_point(app, pilot, tip)
        await click(pilot, table, point, times=2)
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert calls == [Action("checkout", target="refs/remotes/origin/test", remote="origin")]
        assert app.snapshot_data.branch == "test"
        assert app.snapshot_data.upstream == "origin/test"


async def test_graph_double_click_non_tip_checks_out_detached_head(branch_repo, git_run):
    base = git(branch_repo, "rev-parse", "main")
    git_run(branch_repo, "commit", "--allow-empty", "-m", "Advance main")
    git_run(branch_repo, "push", "origin", "main")
    app = GitKraikenApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        table, point = await graph_point(app, pilot, base)
        await click(pilot, table, point, times=2)
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert not app.snapshot_data.branch
        assert app.snapshot_data.head == base
        assert app.is_running


async def test_graph_double_click_ignores_inert_rows_and_stale_clicks(branch_repo):
    app = GitKraikenApp(branch_repo)
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
    app = GitKraikenApp(branch_repo)
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
        assert "No file changes" in str(app.query_one("#commit-diff", Static).render())
        assert "[bold]literal[/bold]" in app.query_one(Toast).render().plain
        await pilot.press("ctrl+r")
        await settle(app, pilot)
        assert app.is_running


async def test_checkout_error_notification_is_literal_and_keeps_workspace_running(branch_repo):
    from gitkraiken.models import Action

    app = GitKraikenApp(branch_repo)
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


async def test_remote_double_click_uses_full_ref_and_exact_owner(branch_repo, monkeypatch):
    from gitkraiken.models import Action

    app = GitKraikenApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        calls = []
        original = app.repo.execute

        def execute(action):
            calls.append(action)
            return original(action)

        monkeypatch.setattr(app.repo, "execute", execute)
        await click_reference(app, pilot, "refs/remotes/origin/test", times=2)
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert calls == [Action("checkout", target="refs/remotes/origin/test", remote="origin")]
        assert app.snapshot_data.branch == "test"
        assert len(app._commits) == 2


async def test_out_of_window_tip_does_not_filter_or_autoload_but_explicit_load_reveals_it(
    branch_repo,
):
    app = GitKraikenApp(branch_repo)
    app.limit = 1
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        main = git(branch_repo, "rev-parse", "main")
        topic = git(branch_repo, "rev-parse", "test")
        assert main not in app._commits and topic in app._commits
        app.jump_to(topic)
        await settle(app, pilot)
        before = app._graph_rows
        await click_reference(app, pilot, "refs/heads/main")
        await settle(app, pilot)
        assert app.limit == 1
        assert app._graph_rows is before
        assert app._history_key == topic
        assert app._selected_commit.oid == topic
        assert app._selected_ref == "refs/heads/main"
        assert "Tip not loaded" in str(app.query_one("#history-caption", Static).render())
        app.action_refresh()
        await settle(app, pilot)
        assert app._selected_ref == "refs/heads/main"
        assert app.limit == 1
        await pilot.click("#load-more")
        await settle(app, pilot)
        assert app.limit == 301
        assert main in app._commits and topic in app._commits
        assert app._history_key == main
        assert "Tip not loaded" not in str(app.query_one("#history-caption", Static).render())


async def test_sidebar_search_does_not_change_graph_or_steal_graph_shortcuts(demo_repo):
    app = GitKraikenApp(demo_repo)
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
    app = GitKraikenApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        base = git(branch_repo, "rev-parse", "main")
        assert "💻" in table.get_cell(base, "refs").plain
        assert "↗" in table.get_cell(base, "refs").plain
        (branch_repo / "local.txt").write_text("local advance\n")
        git_run(branch_repo, "add", ".")
        git_run(branch_repo, "commit", "-m", "Advance local main")
        tip = git(branch_repo, "rev-parse", "HEAD")
        app.action_refresh()
        await settle(app, pilot)
        assert "💻" in table.get_cell(tip, "refs").plain
        assert "↗" not in table.get_cell(tip, "refs").plain
        assert "↗" in table.get_cell(base, "refs").plain
        assert "💻" not in table.get_cell(base, "refs").plain
        git_run(branch_repo, "push", "origin", "main")
        app.action_refresh()
        await settle(app, pilot)
        assert "💻" in table.get_cell(tip, "refs").plain
        assert "↗" in table.get_cell(tip, "refs").plain


async def test_new_branch_at_same_tip_does_not_invent_divergence(branch_repo):
    from gitkraiken.models import Action

    app = GitKraikenApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        before = [(row.commit.oid, row.graph.plain) for row in app._graph_rows]
        app.run_mutation(Action("create-branch", name="Connor"))
        await settle(app, pilot)
        assert app.snapshot_data.branch == "Connor"
        assert [(row.commit.oid, row.graph.plain) for row in app._graph_rows] == before
        cell = app.query_one("#graph-table", DataTable).get_cell(app.snapshot_data.head, "refs")
        assert all(marker in cell.plain for marker in ("💻", "↗", "Connor", "main"))


async def test_single_click_and_checkout_preserve_scrolled_graph_viewport(branch_repo, git_run):
    for index in range(12):
        git_run(branch_repo, "switch", "-c", f"parallel-{index:02}", "main")
        git_run(branch_repo, "commit", "--allow-empty", "-m", f"Independent tip {index}")
    git_run(branch_repo, "switch", "main")
    app = GitKraikenApp(branch_repo)
    async with app.run_test(size=(130, 32)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
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
        await click_reference(app, pilot, "refs/heads/test", times=2)
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
    app = GitKraikenApp(branch_repo)
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
    app = GitKraikenApp(git_repo)
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
        diff = app.query_one("#commit-diff-scroll")
        assert diff.region.height >= 5
        assert diff.region.bottom <= app.query_one("#workspace").region.bottom
        assert tree.root.label.plain == "CHANGED FILES (12)"
        assert tree.selected_file.path == paths[0]
        assert "select 0 as metric" in str(app.query_one("#commit-diff", Static).render())
        nodes = [
            node for node in tree._path_nodes.values() if getattr(node.data, "path", "") == paths[1]
        ]
        assert len(nodes) == 1
        assert nodes[0].label.plain == "A fact_metric_01.sql"
        tree.focus()
        await pilot.press("j")
        await settle(app, pilot)
        assert tree.selected_file.path == paths[1]
        assert "select 1 as metric" in str(app.query_one("#commit-diff", Static).render())
        parent = nodes[0].parent
        parent.collapse()
        await pilot.pause()
        git_run(git_repo, "tag", "extra-pointer")
        app.action_refresh()
        await settle(app, pilot)
        assert tree.selected_file.path == paths[1]
        assert tree._path_nodes[parent.data.key].is_collapsed
        assert "select 1 as metric" in str(app.query_one("#commit-diff", Static).render())


@pytest.mark.parametrize("operation", ["fetch", "pull", "push"])
async def test_network_toolbar_actions_run_without_confirmation(branch_repo, operation):
    app = GitKraikenApp(branch_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        await pilot.click(f"#{operation}")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert not app._last_error
        assert app.snapshot_data.branch == "main"


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
async def test_right_click_rebases_current_feature_onto_clicked_branch(
    rebase_repo, target, monkeypatch
):
    before = git(rebase_repo, "rev-parse", "HEAD")
    base = git(rebase_repo, "rev-parse", target)
    app = GitKraikenApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        calls = []
        execute = app.repo.execute

        def recorded(action):
            calls.append(action)
            return execute(action)

        monkeypatch.setattr(app.repo, "execute", recorded)
        # Neither opening nor dismissing the dropdown changes Git state.
        await click_reference(app, pilot, target, button=3)
        assert isinstance(app.screen, BranchContextMenu)
        menu = app.screen.query_one("#branch-context-menu", OptionList)
        assert app.screen.styles.background.a == 0
        assert menu.region.right <= app.screen.size.width
        assert menu.region.bottom <= app.screen.size.height
        label = menu.get_option_at_index(0).prompt.plain
        assert label == "Rebase feature/payments onto " + (
            "origin/test" if "remotes" in target else "test"
        )
        assert not calls
        await pilot.press("escape")
        assert git(rebase_repo, "rev-parse", "HEAD") == before
        await click_reference(app, pilot, target, button=3)
        await pilot.press("enter")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert len(calls) == 1
        assert calls[0].operation == "rebase" and calls[0].target == target
        assert app.snapshot_data.branch == "feature/payments"
        assert app.snapshot_data.head != before
        assert git(rebase_repo, "rev-parse", "HEAD^") == base
        assert git(rebase_repo, "rev-parse", target) == base  # The base branch never moves.
        assert (rebase_repo / "feature.txt").read_text() == "my feature\n"
        assert (rebase_repo / "teammate.txt").exists()
        assert not app.snapshot_data.operation
        assert app.is_running and not app._last_error


async def test_rebase_menu_rejects_external_checkout_while_open(rebase_repo, git_run):
    before = git(rebase_repo, "rev-parse", "feature/payments")
    app = GitKraikenApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/test", button=3)
        assert isinstance(app.screen, BranchContextMenu)
        git_run(rebase_repo, "switch", "main")
        main = git(rebase_repo, "rev-parse", "HEAD")
        await pilot.press("enter")
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
    app = GitKraikenApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        before = git(rebase_repo, "rev-parse", "HEAD")
        if state == "busy":
            app.set_busy(True)
        target = "refs/heads/feature/payments" if state == "current" else "refs/heads/test"
        await click_reference(app, pilot, target, button=3)
        assert len(app.screen_stack) == 1
        assert git(rebase_repo, "rev-parse", "HEAD") == before
        app.set_busy(False)


async def test_rebase_dirty_worktree_error_keeps_changes_and_refreshes(rebase_repo):
    before = git(rebase_repo, "rev-parse", "HEAD")
    (rebase_repo / "feature.txt").write_text("uncommitted edits\n")
    app = GitKraikenApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/test", button=3)
        await pilot.press("enter")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert app._last_error
        assert app.snapshot_data.branch == "feature/payments"
        assert app.snapshot_data.head == before
        assert not app.snapshot_data.operation
        assert (rebase_repo / "feature.txt").read_text() == "uncommitted edits\n"
        assert app.snapshot_data.files and not app.busy and app.is_running


@pytest.mark.parametrize("finish", ["abort", "continue"])
async def test_right_click_rebase_conflicts_can_abort_or_continue(rebase_repo, git_run, finish):
    from gitkraiken.models import Action

    (rebase_repo / "base.txt").write_text("feature version\n")
    git_run(rebase_repo, "add", ".")
    git_run(rebase_repo, "commit", "-m", "Feature changes base")
    before = git(rebase_repo, "rev-parse", "HEAD")
    git_run(rebase_repo, "switch", "test")
    (rebase_repo / "base.txt").write_text("test version\n")
    git_run(rebase_repo, "add", ".")
    git_run(rebase_repo, "commit", "-m", "Teammate changes base")
    git_run(rebase_repo, "switch", "feature/payments")
    app = GitKraikenApp(rebase_repo)
    async with app.run_test(size=(170, 50), notifications=True) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/test", button=3)
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.snapshot_data.operation == "rebase"
        assert app.snapshot_data.conflicts
        assert app.query_one("#operation-banner").display
        assert app._last_error
        if finish == "continue":
            (rebase_repo / "base.txt").write_text("resolved together\n")
            app.run_mutation(Action("stage", paths=("base.txt",)))
            await settle(app, pilot)
            assert not app.snapshot_data.conflicts
        await pilot.click(f"#{finish}")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert app.snapshot_data.branch == "feature/payments"
        assert not app.snapshot_data.operation
        assert not app.query_one("#operation-banner").display
        if finish == "abort":
            assert app.snapshot_data.head == before
            assert (rebase_repo / "base.txt").read_text() == "feature version\n"
        else:
            assert app.snapshot_data.head != before
            assert (rebase_repo / "base.txt").read_text() == "resolved together\n"
            git_run(rebase_repo, "merge-base", "--is-ancestor", "test", "HEAD")


async def test_commit_selection_waits_for_chained_diff_workers(demo_repo, monkeypatch):
    import time

    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        original_diff = app.repo.diff

        def slow_diff(*args, **kwargs):
            time.sleep(0.5)  # Model slower Git I/O; detail creates this worker later.
            return original_diff(*args, **kwargs)

        monkeypatch.setattr(app.repo, "diff", slow_diff)
        app.jump_to(next(iter(app._commits)))
        await settle(app, pilot)
        assert "diff --git" in str(app.query_one("#commit-diff", Static).render()), [
            worker.group for worker in app.workers
        ]
