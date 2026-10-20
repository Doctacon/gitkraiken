import pytest
from textual.app import App, ComposeResult
from ui_helpers import click

from gitkraiken.commit_files import CommitFileTree
from gitkraiken.models import FileChange


class FileTreeApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.selected: list[CommitFileTree.FileSelected] = []

    def compose(self) -> ComposeResult:
        yield CommitFileTree(id="commit-file-tree")

    def on_commit_file_tree_file_selected(self, event: CommitFileTree.FileSelected) -> None:
        self.selected.append(event)


async def hit_point(tree: CommitFileTree, pilot, key: str):
    node = tree._path_nodes[key]
    parent = node.parent
    while parent is not None:
        parent.expand()
        parent = parent.parent
    await pilot.pause()
    tree.scroll_to_node(node, animate=False)
    await pilot.pause()
    y = node.line - int(tree.scroll_y)
    for x in range(tree.size.width - 1):
        style = pilot.app.screen.get_style_at(tree.region.x + x, tree.region.y + y)
        if style.meta.get("line") == node.line:
            return x, y
    raise AssertionError(f"No hit point for {key}")


async def root_point(tree: CommitFileTree, pilot):
    await pilot.pause()
    y = tree.root.line - int(tree.scroll_y)
    for x in range(tree.size.width - 1):
        style = pilot.app.screen.get_style_at(tree.region.x + x, tree.region.y + y)
        if style.meta.get("line") == tree.root.line:
            return x, y
    raise AssertionError("No root hit point")


@pytest.mark.asyncio
async def test_folders_toggle_and_files_select_with_keyboard() -> None:
    app = FileTreeApp()
    files = (
        FileChange("models/marts/finance/fact_core_metric.sql", index="M"),
        FileChange("models/marts/finance/schema.yml", index="A"),
        FileChange("models/raw/source.csv", index="A"),
    )
    async with app.run_test(size=(50, 20)) as pilot:
        tree = app.query_one(CommitFileTree)
        tree.set_files(files, identity="first")
        await pilot.pause()
        assert tree._path_nodes["dir:models"].label.plain == "models"
        folder = tree._path_nodes["dir:models/marts/finance"]
        assert folder.label.plain == "marts/finance"
        assert tree._path_nodes[tree._key_for(files[0])].label.plain == "M fact_core_metric.sql"
        assert tree.get_node_by_id(tree.root.id) is tree.root
        assert len(tree._tree_nodes) == len(tree._path_nodes) + 1
        await click(pilot, tree, await hit_point(tree, pilot, "dir:models/marts/finance"))
        assert folder.is_collapsed
        assert not app.selected
        tree.focus()
        tree.move_cursor(tree._path_nodes["dir:models/marts/finance"])
        await pilot.press("space")
        assert folder.is_expanded
        await pilot.press("down")
        assert app.selected[-1].file == files[0]
        await pilot.press("enter")
        assert app.selected[-1].file == files[0]
        assert app.selected[-1].identity == "first"
        assert tree.selected_file == files[0]


@pytest.mark.asyncio
async def test_same_basenames_have_distinct_nodes_and_literal_rename_tooltip() -> None:
    app = FileTreeApp()
    first = FileChange(
        "models/marts/finance/fact_core_metric.sql", index="R", original_path="old/[x].sql"
    )
    second = FileChange("models/staging/fact_core_metric.sql", index="M")
    async with app.run_test(size=(60, 20)) as pilot:
        tree = app.query_one(CommitFileTree)
        tree.set_files((first, second), identity="commit")
        await pilot.pause()
        assert tree._path_nodes[tree._key_for(first)].label.plain == "R fact_core_metric.sql"
        assert tree._path_nodes[tree._key_for(second)].label.plain == "M fact_core_metric.sql"
        point = await hit_point(tree, pilot, tree._key_for(first))
        await pilot.hover(tree, offset=point)
        assert (
            tree.tooltip.plain
            == "models/marts/finance/fact_core_metric.sql\nRenamed from: old/[x].sql"
        )
        await click(pilot, tree, point)
        assert app.selected[-1].file == first
        await click(pilot, tree, await hit_point(tree, pilot, tree._key_for(second)))
        assert app.selected[-1].file == second


@pytest.mark.asyncio
async def test_empty_and_same_identity_refresh_preserve_fold_and_file() -> None:
    app = FileTreeApp()
    file = FileChange("src/gitkraiken/commit_files.py", index="M")
    async with app.run_test(size=(50, 20)) as pilot:
        tree = app.query_one(CommitFileTree)
        tree.set_files((), identity="one")
        assert tree.root.label.plain == "CHANGED FILES (0)"
        assert tree.selected_file is None
        tree.set_files((file,), identity="one")
        await pilot.pause()
        folder = tree._path_nodes["dir:src/gitkraiken"]
        point = await hit_point(tree, pilot, tree._key_for(file))
        await click(pilot, tree, point)
        folder.collapse()
        tree.set_files((file,), identity="one")
        await pilot.pause()
        assert tree._path_nodes["dir:src/gitkraiken"].is_collapsed
        assert tree.selected_file == file
        tree.set_files((file,), identity="two")
        await pilot.pause()
        assert tree._path_nodes["dir:src/gitkraiken"].is_expanded
        assert tree.selected_file is None
        assert tree.cursor_node is None
        tree._path_nodes["dir:src/gitkraiken"].collapse()
        tree.clear_files()
        assert tree.root.label.plain == "CHANGED FILES (0)"
        assert tree.selected_file is None
        tree.set_files((file,), identity="two")
        assert tree._path_nodes["dir:src/gitkraiken"].is_collapsed
        assert tree.get_node_by_id(tree.root.id) is tree.root
        assert len(tree._tree_nodes) == len(tree._path_nodes) + 1
        for node in tree._path_nodes.values():
            assert tree.get_node_by_id(node.id) is node


@pytest.mark.asyncio
async def test_root_toggle_select_file_and_stale_nodes_are_inert() -> None:
    app = FileTreeApp()
    file = FileChange("models/marts/finance/fact_core_metric.sql", index="M")
    async with app.run_test(size=(50, 20)) as pilot:
        tree = app.query_one(CommitFileTree)
        tree.set_files((file,), identity="one")
        await click(pilot, tree, await root_point(tree, pilot))
        assert tree.root.is_collapsed
        tree.set_files((file,), identity="two")
        assert tree.root.is_expanded
        assert tree.cursor_node is None

        folder = tree._path_nodes["dir:models/marts/finance"]
        folder.collapse()
        tree.select_file(file)
        await pilot.pause()
        assert folder.is_collapsed
        assert tree.selected_file == file
        assert app.selected[-1].identity == "two"
        count = len(app.selected)
        tree.select_file(FileChange("missing.py", index="M"))
        assert len(app.selected) == count

        stale = tree._path_nodes[tree._key_for(file)]
        tree.set_files((file,), identity="two")
        tree._node_highlighted(tree.NodeHighlighted(stale))
        tree._node_selected(tree.NodeSelected(stale))
        await pilot.pause()
        assert len(app.selected) == count
