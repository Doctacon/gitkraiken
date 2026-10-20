from __future__ import annotations

import subprocess

import pytest
from textual.widgets import DataTable, Input, Static, TabbedContent, TextArea, Tree
from ui_helpers import click

from gitkraiken.app import GitKraikenApp
from gitkraiken.demo import create_demo
from gitkraiken.dialogs import ConfirmDialog
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


async def click_reference(app, pilot, target, *, times=1):
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
    await click(pilot, tree, (x, y), times=times)
    return node


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
        assert app._commit_files
        assert "diff --git" in str(app.query_one("#commit-diff", Static).render())


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


async def test_destructive_action_cancel_is_noop(demo_repo):
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        file = next(f for f in app.snapshot_data.files if f.path == "README.md")
        original = (demo_repo / file.path).read_text()
        app.inspect_worktree_file(file, False)
        app.wip_action("discard")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("escape")
        await settle(app, pilot)
        assert (demo_repo / file.path).read_text() == original


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


async def test_global_shortcuts_cannot_replace_repository_under_confirmation(demo_repo, tmp_path):
    second = tmp_path / "second"
    subprocess.run(["git", "init", "-b", "main", str(second)], check=True, capture_output=True)
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        file = next(f for f in app.snapshot_data.files if f.path == "README.md")
        app.inspect_worktree_file(file, False)
        app.wip_action("discard")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("ctrl+o", "ctrl+b", "ctrl+r")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert isinstance(app.screen, ConfirmDialog)
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


async def test_branch_creation_confirmation_is_wired(demo_repo):
    from gitkraiken.dialogs import PromptDialog

    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        await pilot.click("#branch")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        app.screen.query_one(Input).value = "feature/from-ui"
        await pilot.click("#confirm")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.click("#confirm")
        await settle(app, pilot)
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


async def test_double_click_checkout_requires_confirmation_and_keeps_graph_unified(branch_repo):
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
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        assert git(branch_repo, "branch", "--show-current") == "main"
        await pilot.press("escape")
        await settle(app, pilot)
        assert git(branch_repo, "branch", "--show-current") == "main"

        await click_reference(app, pilot, "refs/heads/test", times=2)
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.click("#confirm")
        await settle(app, pilot)
        assert git(branch_repo, "branch", "--show-current") == "test"
        assert app._history_key == cursor
        assert [(row.commit.oid, row.graph.plain) for row in app._graph_rows] == before
        assert len(app.screen_stack) == 1


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
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        assert not calls
        await pilot.click("#confirm")
        await settle(app, pilot)
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
        await pilot.click("#confirm")
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
    async with app.run_test(size=(170, 50)) as pilot:
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
