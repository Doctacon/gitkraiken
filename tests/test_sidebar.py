from dataclasses import replace
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Tree
from ui_helpers import click, post_click

from gitkraiken.models import Ref, Remote, Snapshot, Stash
from gitkraiken.sidebar import ReferenceSidebar, SidebarSelection


class SidebarApp(App[None]):
    def __init__(self):
        super().__init__()
        self.selections = []
        self.checkouts = []

    def compose(self) -> ComposeResult:
        yield ReferenceSidebar()

    def on_reference_sidebar_selection_changed(self, event):
        self.selections.append(event)

    def on_reference_sidebar_checkout_requested(self, event):
        self.checkouts.append(event)


async def hit_point(sidebar, pilot, key, *, caret=False):
    node = sidebar._tree_nodes[key]
    parent = node.parent
    while parent is not None:
        parent.expand()
        parent = parent.parent
    await pilot.pause()
    tree = sidebar.query_one(Tree)
    tree.scroll_to_node(node, animate=False)
    await pilot.pause()
    y = node.line - int(tree.scroll_y)
    assert 0 <= y < tree.size.height
    for x in range(tree.size.width - 1):
        style = pilot.app.screen.get_style_at(tree.region.x + x, tree.region.y + y)
        if style.meta.get("line") == node.line and bool(style.meta.get("toggle")) == caret:
            return tree, (x, y)
    raise AssertionError(f"No {'caret' if caret else 'label'} for {key}")


def snapshot(root: str = "/repo") -> Snapshot:
    return Snapshot(
        Path(root),
        "main",
        refs=(
            Ref("main", "refs/heads/main", "a" * 40, "local", current=True),
            Ref(
                "origin/feature/jlott/demo",
                "refs/remotes/origin/feature/jlott/demo",
                "b" * 40,
                "remote",
                remote="origin",
            ),
            Ref("up/one/two", "refs/remotes/up/one/two", "c" * 40, "remote", remote="up"),
        ),
        remotes=(
            Remote("origin", "https://example.test/origin"),
            Remote("up", "https://example.test/up"),
        ),
        stashes=(Stash("stash@{0}", "d" * 40, "literal [not markup]"),),
    )


@pytest.mark.asyncio
async def test_builds_literal_remote_paths_and_reconciles_stash_by_oid() -> None:
    async with SidebarApp().run_test() as pilot:
        sidebar = pilot.app.query_one(ReferenceSidebar)
        old = snapshot()
        sidebar.update_snapshot(old, None)
        assert sidebar.get_selection(
            "remote:refs/remotes/origin/feature/jlott/demo"
        ) == SidebarSelection(
            "remote:refs/remotes/origin/feature/jlott/demo",
            "remote",
            "demo",
            "refs/remotes/origin/feature/jlott/demo",
            "b" * 40,
            "origin",
        )
        assert (
            sidebar._tree_nodes["group:namespace:remote:origin:feature/jlott"].data.label == "jlott"
        )
        changed = Snapshot(
            old.root,
            old.branch,
            refs=old.refs,
            remotes=old.remotes,
            stashes=(Stash("stash@{1}", "d" * 40, "literal [not markup]"),),
        )
        selected = SidebarSelection("stash:" + "d" * 40, "stash", "old", "stash@{0}", "d" * 40)
        assert sidebar.update_snapshot(changed, selected).target == "stash@{1}"


@pytest.mark.asyncio
async def test_filter_reveals_only_matching_refs_and_root_resets_folds() -> None:
    async with SidebarApp().run_test() as pilot:
        sidebar = pilot.app.query_one(ReferenceSidebar)
        sidebar.update_snapshot(snapshot(), None)
        remote = sidebar._tree_nodes["group:remote:origin"]
        remote.collapse()
        sidebar.query_one("#ref-filter").value = "jlott"
        await pilot.pause()
        assert "remote:refs/remotes/origin/feature/jlott/demo" in sidebar._tree_nodes
        assert "local:refs/heads/main" not in sidebar._tree_nodes
        sidebar.query_one("#ref-filter").value = ""
        await pilot.pause()
        assert sidebar._tree_nodes["group:remote:origin"].is_collapsed
        sidebar.update_snapshot(snapshot("/other"), None)
        assert sidebar._tree_nodes["group:remote:origin"].is_expanded


async def test_real_mouse_selection_checkout_and_carets_are_separate():
    app = SidebarApp()
    async with app.run_test(size=(40, 25)) as pilot:
        sidebar = app.query_one(ReferenceSidebar)
        sidebar.update_snapshot(snapshot(), None)
        await pilot.pause()
        tree, point = await hit_point(sidebar, pilot, "local:refs/heads/main")
        await click(pilot, tree, point)
        assert len(app.selections) == 1
        assert not app.checkouts
        await click(pilot, tree, point, times=2)
        assert len(app.checkouts) == 1
        assert app.checkouts[0].selection.target == "refs/heads/main"
        assert app.checkouts[0].root == Path("/repo")

        tree, point = await hit_point(sidebar, pilot, "group:remote:origin", caret=True)
        await click(pilot, tree, point, times=2)
        assert sidebar._tree_nodes["group:remote:origin"].is_collapsed
        assert len(app.checkouts) == 1
        # Category labels toggle but never select a branch or request checkout.
        tree, point = await hit_point(sidebar, pilot, "group:local")
        await click(pilot, tree, point, times=2)
        assert sidebar._tree_nodes["group:local"].is_collapsed
        assert len(app.checkouts) == 1
        assert len(app.selections) == 2


async def test_remote_label_double_click_uses_exact_owner_and_full_target():
    app = SidebarApp()
    async with app.run_test(size=(40, 25)) as pilot:
        sidebar = app.query_one(ReferenceSidebar)
        sidebar.update_snapshot(snapshot(), None)
        tree, point = await hit_point(
            sidebar, pilot, "remote:refs/remotes/origin/feature/jlott/demo"
        )
        await click(pilot, tree, point, times=2)
        assert len(app.checkouts) == 1
        selection = app.checkouts[0].selection
        assert selection.target == "refs/remotes/origin/feature/jlott/demo"
        assert selection.remote == "origin"


async def test_keyboard_navigation_and_inert_nodes_never_request_checkout():
    app = SidebarApp()
    async with app.run_test(size=(40, 25)) as pilot:
        sidebar = app.query_one(ReferenceSidebar)
        sidebar.update_snapshot(snapshot(), None)
        tree = sidebar.query_one(Tree)
        tree.focus()
        tree.move_cursor(sidebar._tree_nodes["local:refs/heads/main"])
        await pilot.press("enter", "up", "down")
        assert len(app.selections) == 1
        assert not app.checkouts
        tree.move_cursor(sidebar._tree_nodes["group:local"])
        await pilot.press("space")
        assert sidebar._tree_nodes["group:local"].is_collapsed
        await pilot.press("enter")
        assert sidebar._tree_nodes["group:local"].is_expanded
        assert len(app.selections) == 1
        assert not app.checkouts
        for key in ("worktree:worktree", "all:all", "stash:" + "d" * 40):
            tree, point = await hit_point(sidebar, pilot, key)
            await click(pilot, tree, point, times=2)
        assert not app.checkouts


async def test_rebuild_invalidates_a_pending_double_click():
    app = SidebarApp()
    async with app.run_test(size=(40, 25)) as pilot:
        sidebar = app.query_one(ReferenceSidebar)
        old = snapshot()
        sidebar.update_snapshot(old, None)
        tree, point = await hit_point(sidebar, pilot, "local:refs/heads/main")
        await click(pilot, tree, point)
        changed = replace(old, refs=old.refs + (Ref("aaa", "refs/heads/aaa", "e" * 40, "local"),))
        sidebar.update_snapshot(changed, app.selections[-1].selection)
        await pilot.pause()
        await post_click(pilot, tree, point, chain=2)
        assert len(app.selections) == 1
        assert not app.checkouts
        await post_click(pilot, tree, point, chain=1, button=3)
        await post_click(pilot, tree, point, chain=2, button=3)
        assert len(app.selections) == 1
        assert not app.checkouts


async def test_search_preserves_hidden_selection_and_temporarily_reveals_folds():
    app = SidebarApp()
    async with app.run_test(size=(40, 25)) as pilot:
        sidebar = app.query_one(ReferenceSidebar)
        old = snapshot()
        sidebar.update_snapshot(old, None)
        selected = sidebar.get_selection("local:refs/heads/main")
        sidebar.set_active(selected)
        sidebar._tree_nodes["group:remote:origin"].collapse()
        await pilot.pause()
        search = sidebar.query_one(Input)
        search.value = "JLOTT"
        await pilot.pause()
        assert "local:refs/heads/main" not in sidebar._tree_nodes
        assert sidebar.get_selection(selected.key) == selected
        assert sidebar._tree_nodes["group:remote:origin"].is_expanded
        changed = replace(old, stashes=())
        assert sidebar.update_snapshot(changed, selected) == selected
        assert sidebar.query_one(Input) is search
        assert search.value == "JLOTT"
        # Folds changed during search do not replace the pre-search choices.
        sidebar._tree_nodes["group:remotes"].collapse()
        await pilot.pause()
        search.value = ""
        await pilot.pause()
        assert sidebar._tree_nodes["group:remote:origin"].is_collapsed
        assert sidebar._tree_nodes["group:remotes"].is_expanded
        assert sidebar._active_key == selected.key
        assert sidebar.update_snapshot(snapshot("/other"), selected) is None
        assert sidebar._active_key is None
        assert sidebar._tree_nodes["group:remote:origin"].is_expanded


async def test_refresh_preserves_cursor_scroll_search_and_unchanged_nodes():
    app = SidebarApp()
    async with app.run_test(size=(30, 12)) as pilot:
        sidebar = app.query_one(ReferenceSidebar)
        old = replace(
            snapshot(),
            refs=tuple(
                Ref(f"branch-{i:02}", f"refs/heads/branch-{i:02}", str(i) * 40, "local")
                for i in range(35)
            ),
        )
        sidebar.update_snapshot(old, None)
        await pilot.pause()
        tree = sidebar.query_one(Tree)
        selected = sidebar.get_selection("local:refs/heads/branch-22")
        node = sidebar._tree_nodes[selected.key]
        tree.move_cursor(node, animate=False)
        await pilot.pause()
        scroll = tree.scroll_offset
        assert scroll.y > 0
        sidebar.update_snapshot(old, selected)
        await pilot.pause()
        assert sidebar._tree_nodes[selected.key] is node
        assert tree.cursor_node is node
        assert tree.scroll_offset == scroll
        sidebar.update_snapshot(replace(old, stashes=()), selected)
        await pilot.pause()
        assert tree.cursor_node.data == selected
        assert tree.scroll_offset == scroll


async def test_slash_remote_and_unassigned_namespaces_do_not_collide():
    app = SidebarApp()
    async with app.run_test(size=(40, 25)) as pilot:
        sidebar = app.query_one(ReferenceSidebar)
        data = replace(
            snapshot(),
            refs=(
                Ref(
                    "team/origin/feature/user/topic",
                    "refs/remotes/team/origin/feature/user/topic",
                    "a" * 40,
                    "remote",
                    remote="team/origin",
                ),
                Ref(
                    "unassigned/gone/path",
                    "refs/remotes/unassigned/gone/path",
                    "b" * 40,
                    "remote",
                    remote="unassigned",
                ),
                Ref("gone/path", "refs/remotes/gone/path", "c" * 40, "remote"),
            ),
            remotes=(
                Remote("team/origin", "https://example.invalid/team"),
                Remote("unassigned", "https://example.invalid/other"),
            ),
        )
        sidebar.update_snapshot(data, None)
        await pilot.pause()
        assert "group:remote:team/origin" in sidebar._tree_nodes
        assert "group:remote:team" not in sidebar._tree_nodes
        assert "group:namespace:remote:team/origin:feature/user" in sidebar._tree_nodes
        assert "group:namespace:remote:unassigned:gone" in sidebar._tree_nodes
        assert "group:namespace:unassigned:gone" in sidebar._tree_nodes
        assert "group:remote:unassigned" in sidebar._tree_nodes
        assert "group:unassigned" in sidebar._tree_nodes


async def test_tooltips_keep_full_targets_and_literal_remote_urls():
    app = SidebarApp()
    async with app.run_test(size=(40, 25)) as pilot:
        sidebar = app.query_one(ReferenceSidebar)
        data = replace(snapshot(), remotes=(Remote("origin", "https://example.test/[red]"),))
        sidebar.update_snapshot(data, None)
        tree, point = await hit_point(sidebar, pilot, "group:remote:origin")
        await pilot.hover(tree, offset=point)
        assert tree.tooltip.plain == "Git: https://example.test/[red]"
        tree, point = await hit_point(
            sidebar, pilot, "remote:refs/remotes/origin/feature/jlott/demo"
        )
        await pilot.hover(tree, offset=point)
        assert tree.tooltip.plain == "refs/remotes/origin/feature/jlott/demo"


async def test_local_namespaces_and_branch_labels_with_children_remain_distinct():
    app = SidebarApp()
    async with app.run_test(size=(40, 25)) as pilot:
        sidebar = app.query_one(ReferenceSidebar)
        data = replace(
            snapshot(),
            refs=(
                Ref("feature/user/topic", "refs/heads/feature/user/topic", "a" * 40, "local"),
                Ref("origin/team", "refs/remotes/origin/team", "b" * 40, "remote", remote="origin"),
                Ref(
                    "origin/team/child",
                    "refs/remotes/origin/team/child",
                    "c" * 40,
                    "remote",
                    remote="origin",
                ),
            ),
        )
        sidebar.update_snapshot(data, None)
        await pilot.pause()
        assert sidebar._tree_nodes["group:namespace:local:feature/user"].is_collapsed
        key = "remote:refs/remotes/origin/team"
        tree, point = await hit_point(sidebar, pilot, key, caret=True)
        await click(pilot, tree, point, times=2)
        assert sidebar._tree_nodes[key].is_expanded
        assert not app.selections and not app.checkouts
        tree, point = await hit_point(sidebar, pilot, key)
        await click(pilot, tree, point, times=2)
        assert len(app.checkouts) == 1
        assert app.checkouts[0].selection.target == "refs/remotes/origin/team"
        sidebar.update_snapshot(replace(data, stashes=()), app.selections[-1].selection)
        await pilot.pause()
        assert sidebar._tree_nodes[key].is_expanded
