"""Changed-file navigator for a commit inspector.

The widget is deliberately presentation-only: callers supply ``FileChange`` values
and use :class:`CommitFileTree.FileSelected` to load a diff.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import events, on
from textual.message import Message
from textual.widgets import Tree
from textual.widgets._tree import TreeNode

from .models import FileChange
from .theme import TEAL


@dataclass(frozen=True)
class _Directory:
    """A visible folder, keyed by its uncompressed path."""

    key: str
    path: str


class CommitFileTree(Tree[FileChange | _Directory]):
    """A compact, foldable tree of the files changed by one commit.

    ``identity`` is normally the commit OID. Refreshing an identity retains its
    folders and file selection; changing it deliberately starts a new view.
    """

    class FileSelected(Message):
        """A file was activated, never a directory."""

        def __init__(self, file: FileChange, identity: str) -> None:
            self.file = file
            self.identity = identity
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(Text("CHANGED FILES (0)"), id=id)
        self.show_root = True
        self.show_guides = True
        self.guide_depth = 2
        self.auto_expand = False
        self.root.expand()
        self._identity: str | None = None
        self._expanded: dict[str, bool] = {}
        # Keep semantic path keys separate from Tree's own NodeID registry.
        self._path_nodes: dict[str, TreeNode[FileChange | _Directory]] = {}
        self._files: dict[str, FileChange] = {}
        self._selected_key: str | None = None

    @property
    def selected_file(self) -> FileChange | None:
        """The selected current file, or ``None`` while the tree is loading."""
        return self._files.get(self._selected_key or "")

    def set_files(self, files: tuple[FileChange, ...], *, identity: str) -> None:
        """Populate the tree for ``identity`` without consulting Git."""
        changed_identity = identity != self._identity
        if changed_identity:
            self._expanded.clear()
            self._selected_key = None
            self.root.expand()
        else:
            self._capture_expanded()
        self._identity = identity
        self._rebuild(files, reset_scroll=changed_identity)

    def clear_files(self) -> None:
        """Show an empty/loading tree while retaining state for this identity."""
        self._capture_expanded()
        self.root.set_label(Text("CHANGED FILES (0)"))
        self.root.remove_children()
        self._path_nodes.clear()
        self._files.clear()
        self.unselect()
        self._cursor_node = None

    async def _on_click(self, event: events.Click) -> None:
        # Tree's stock handler selects folder labels rather than treating them
        # as a compact disclosure control. Keep the hit-tested node semantics.
        event.prevent_default()
        event.stop()
        if event.button != 1:
            return
        meta = event.style.meta
        if "line" not in meta:
            return
        node = self.get_node_at_line(meta["line"])
        if node is None:
            return
        self.cursor_line = node.line
        self._activate(node)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        meta = event.style.meta
        node = self.get_node_at_line(meta["line"]) if meta and "line" in meta else None
        if node is not None and isinstance(node.data, FileChange):
            detail = node.data.path
            if node.data.original_path:
                detail += f"\nRenamed from: {node.data.original_path}"
            self.tooltip = Text(detail)
        elif node is not None and isinstance(node.data, _Directory):
            self.tooltip = Text(node.data.path)
        else:
            self.tooltip = None

    @on(Tree.NodeSelected)
    def _node_selected(self, event: Tree.NodeSelected[FileChange | _Directory]) -> None:
        event.stop()
        self._activate(event.node)

    @on(Tree.NodeHighlighted)
    def _node_highlighted(self, event: Tree.NodeHighlighted[FileChange | _Directory]) -> None:
        """Remember arrow-key navigation without opening a diff."""
        if self._is_current_file_node(event.node):
            self._select_file(event.node.data)

    @on(Tree.NodeExpanded)
    def _remember_expanded(self, event: Tree.NodeExpanded[FileChange | _Directory]) -> None:
        if (
            isinstance(event.node.data, _Directory)
            and self._path_nodes.get(event.node.data.key) is event.node
        ):
            self._expanded[event.node.data.key] = True

    @on(Tree.NodeCollapsed)
    def _remember_collapsed(self, event: Tree.NodeCollapsed[FileChange | _Directory]) -> None:
        if (
            isinstance(event.node.data, _Directory)
            and self._path_nodes.get(event.node.data.key) is event.node
        ):
            self._expanded[event.node.data.key] = False

    def select_file(self, file: FileChange) -> None:
        """Select a current file for the caller without opening its folders."""
        key = self._key_for(file)
        node = self._path_nodes.get(key)
        current = self._files.get(key)
        if node is None or current is None or not self._is_current_file_node(node):
            return
        if node.line >= 0:
            self.move_cursor(node, animate=False)
        self._select_file(current)

    def _activate(self, node: TreeNode[FileChange | _Directory]) -> None:
        if node is self.root:
            node.toggle()
        elif isinstance(node.data, _Directory) and self._path_nodes.get(node.data.key) is node:
            node.toggle()
        elif self._is_current_file_node(node):
            self._select_file(node.data)
            self.post_message(self.FileSelected(node.data, self._identity or ""))

    def _is_current_file_node(self, node: TreeNode[FileChange | _Directory]) -> bool:
        if not isinstance(node.data, FileChange):
            return False
        key = self._key_for(node.data)
        return self._path_nodes.get(key) is node and key in self._files

    def _select_file(self, file: FileChange) -> None:
        if self._identity is None or self._key_for(file) not in self._files:
            return
        key = self._key_for(file)
        if self._selected_key == key:
            return
        self._selected_key = key

    @staticmethod
    def _key_for(file: FileChange) -> str:
        # Git returns one record per destination path; original_path is retained
        # here so even unusual duplicate records keep an unambiguous selection.
        return f"file:{file.path}\0{file.original_path}"

    @staticmethod
    def _label(file: FileChange) -> Text:
        name = file.path.rsplit("/", 1)[-1]
        text = Text(f"{file.index} {name}", no_wrap=True, overflow="ellipsis")
        text.stylize(f"bold {TEAL}", 0, len(file.index))
        return text

    def _capture_expanded(self) -> None:
        """Synchronously retain folds even when a caller refreshes immediately."""
        for node in self._path_nodes.values():
            if isinstance(node.data, _Directory):
                self._expanded[node.data.key] = node.is_expanded

    def _rebuild(self, files: tuple[FileChange, ...], *, reset_scroll: bool) -> None:
        cursor_key = None if reset_scroll else self._cursor_key()
        scroll_y = self.scroll_offset.y
        self.unselect()
        self._cursor_node = None
        self.root.set_label(Text(f"CHANGED FILES ({len(files)})"))
        self.root.remove_children()
        self._path_nodes.clear()
        self._files = {self._key_for(file): file for file in files}

        trie: dict[str, object] = {"dirs": {}, "files": []}
        for file in files:
            branch = trie
            parts = [part for part in file.path.split("/") if part]
            for part in parts[:-1]:
                branch = branch["dirs"].setdefault(part, {"dirs": {}, "files": []})  # type: ignore[index]
            branch["files"].append(file)  # type: ignore[index]
        self._add_branch(self.root, trie, "", "")
        self.get_node_at_line(0)
        cursor = self._path_nodes.get(cursor_key) if cursor_key else None
        if cursor is not None and cursor.line >= 0:
            self.move_cursor(cursor, animate=False)
        self.call_after_refresh(
            self.scroll_to,
            x=0 if reset_scroll else None,
            y=0 if reset_scroll else scroll_y,
            animate=False,
            immediate=True,
        )

    def _add_branch(
        self, parent: TreeNode, branch: dict[str, object], prefix: str, label: str
    ) -> None:
        directories = branch["dirs"]  # type: ignore[assignment]
        while len(directories) == 1 and not branch["files"]:
            part, child = next(iter(directories.items()))  # type: ignore[union-attr]
            prefix = f"{prefix}/{part}" if prefix else part
            label = f"{label}/{part}" if label else part
            branch = child
            directories = branch["dirs"]  # type: ignore[assignment]
        if prefix:
            key = f"dir:{prefix}"
            folder = parent.add(
                Text(label, no_wrap=True, overflow="ellipsis"),
                _Directory(key, prefix),
                expand=self._expanded.get(key, True),
            )
            self._path_nodes[key] = folder
            parent = folder
        for file in branch["files"]:  # type: ignore[union-attr]
            key = self._key_for(file)
            node = parent.add_leaf(self._label(file), file)
            self._path_nodes[key] = node
        for part in sorted(directories, key=str.casefold):  # type: ignore[arg-type]
            child = directories[part]  # type: ignore[index]
            path = f"{prefix}/{part}" if prefix else part
            self._add_branch(parent, child, path, part)

    def _cursor_key(self) -> str | None:
        node = self.cursor_node
        if isinstance(node.data if node is not None else None, FileChange):
            return self._key_for(node.data)
        if isinstance(node.data if node is not None else None, _Directory):
            return node.data.key
        return None
