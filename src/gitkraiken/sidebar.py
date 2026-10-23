"""A compact, self-contained reference navigator.

The sidebar deliberately only describes a Snapshot.  It never talks to Git; the
application decides what a selection or checkout request means.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Tree
from textual.widgets._tree import TreeNode

from .models import Ref, Snapshot, Stash


@dataclass(frozen=True)
class SidebarSelection:
    key: str
    kind: Literal["all", "worktree", "local", "remote", "tag", "stash"]
    label: str
    target: str = ""
    oid: str = ""
    remote: str = ""


@dataclass(frozen=True)
class _Group:
    """Tree-only data. Keys are stable across TreeNode IDs and rebuilds."""

    key: str
    label: str
    tooltip: str = ""


class _ReferenceTree(Tree[SidebarSelection | _Group]):
    """Tree which reports click semantics that Textual 1.0's Tree drops."""

    def __init__(self, owner: ReferenceSidebar) -> None:
        super().__init__(Text("References"), id="refs-tree")
        self.owner = owner
        self.show_root = False
        self.show_guides = True
        self.guide_depth = 2
        self.auto_expand = False
        self.root.expand()

    async def _on_click(self, event: events.Click) -> None:
        # Tree._on_click intentionally discards Click.chain.  Do not call it:
        # doing so would post a second NodeSelected and make a double-click
        # ambiguous after a folder has moved rows.
        event.prevent_default()
        event.stop()
        if event.button not in {1, 3}:
            return
        meta = event.style.meta
        if "line" not in meta:
            if event.button == 3:
                self.owner._click_latch = None
            return
        node = self.get_node_at_line(meta["line"])
        if node is None:
            if event.button == 3:
                self.owner._click_latch = None
            return
        label_hit = not meta.get("toggle", False)
        if event.button == 3:
            self.owner._tree_context_requested(
                node,
                label_hit=label_hit,
                screen_x=event.screen_x,
                screen_y=event.screen_y,
            )
            return
        self.owner._tree_clicked(node, label_hit=label_hit, chain=event.chain)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        meta = event.style.meta
        node = self.get_node_at_line(meta["line"]) if meta and "line" in meta else None
        if node is None:
            self.tooltip = None
        elif isinstance(node.data, SidebarSelection):
            self.tooltip = Text(node.data.target or node.data.label)
        elif isinstance(node.data, _Group):
            self.tooltip = Text(node.data.tooltip or node.data.label)


class ReferenceSidebar(Widget):
    """Filterable ref tree whose messages are the public integration surface."""

    DEFAULT_CSS = """
    ReferenceSidebar {
        width: 1fr;
        min-width: 0;
        height: 1fr;
        background: #edf4f5;
        layout: vertical;
    }
    ReferenceSidebar > Input {
        height: 1;
        min-height: 1;
        margin: 0;
        padding: 0 1;
        background: #ffffff;
        border: none;
        color: #14232c;
    }
    ReferenceSidebar > Input:focus { background: #f6fcfc; border: none; }
    ReferenceSidebar > Tree {
        height: 1fr;
        margin: 1 0 0 0;
        padding: 0;
        background: #edf4f5;
        color: #14232c;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }
    ReferenceSidebar > Tree > .tree--guides { color: #8aa5ad; }
    ReferenceSidebar > Tree > .tree--guides-hover { color: #48616c; }
    ReferenceSidebar > Tree > .tree--guides-selected { color: #006d77; }
    ReferenceSidebar > Tree > .tree--cursor,
    ReferenceSidebar > Tree:focus > .tree--cursor {
        background: #d8eeee;
        color: #14232c;
        text-style: none;
    }
    """

    class SelectionChanged(Message):
        def __init__(self, selection: SidebarSelection, root: Path) -> None:
            self.selection = selection
            self.root = root
            super().__init__()

    class CheckoutRequested(Message):
        def __init__(self, selection: SidebarSelection, root: Path) -> None:
            self.selection = selection
            self.root = root
            super().__init__()

    class ContextRequested(Message):
        def __init__(
            self,
            selection: SidebarSelection,
            root: Path,
            screen_x: int,
            screen_y: int,
        ) -> None:
            self.selection = selection
            self.root = root
            self.screen_x = screen_x
            self.screen_y = screen_y
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._tree = _ReferenceTree(self)
        self._root: Path | None = None
        self._signature: object | None = None
        self._tree_nodes: dict[str, TreeNode[SidebarSelection | _Group]] = {}
        self._leaves: dict[str, SidebarSelection] = {}
        self._expanded: dict[str, bool] = {}
        self._active_key: str | None = None
        self._click_latch: tuple[str, bool] | None = None
        self._filter = ""
        self._snapshot: Snapshot | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter refs…", id="ref-filter")
        yield self._tree

    def get_selection(self, key: str) -> SidebarSelection | None:
        return self._leaves.get(key)

    def set_active(self, selection: SidebarSelection | None) -> None:
        """Style a selection in-place; this intentionally doesn't alter scroll."""
        old_key = self._active_key
        self._active_key = selection.key if selection and selection.key in self._leaves else None
        for key in {old_key, self._active_key}:
            node = self._tree_nodes.get(key) if key else None
            if node is not None and isinstance(node.data, SidebarSelection):
                self._set_leaf_label(node, node.data, key == self._active_key)

    def update_snapshot(
        self, snapshot: Snapshot, active: SidebarSelection | None
    ) -> SidebarSelection | None:
        """Synchronously reconcile a snapshot and return the still-valid selection."""
        root_changed = self._root is not None and self._root != snapshot.root
        signature = (snapshot.root, snapshot.refs, snapshot.remotes, snapshot.stashes)
        self._root = snapshot.root
        if root_changed:
            active = None
            self._active_key = None
            self._tree.move_cursor(None, animate=False)
            self._expanded.clear()
            self._click_latch = None
            self._filter = ""
            if self.is_mounted:
                self.query_one("#ref-filter", Input).value = ""
        self._snapshot = snapshot
        if signature != self._signature:
            self._rebuild(snapshot, reset_scroll=root_changed)
            self._signature = signature
        reconciled = self._reconcile(active)
        self.set_active(reconciled)
        return reconciled

    def _reconcile(self, active: SidebarSelection | None) -> SidebarSelection | None:
        if active is None:
            return None
        # A stash selector changes as stashes are added, while its OID does not.
        if active.kind == "stash" and active.oid:
            return next(
                (
                    item
                    for item in self._leaves.values()
                    if item.kind == "stash" and item.oid == active.oid
                ),
                None,
            )
        return self._leaves.get(active.key)

    @on(Input.Changed, "#ref-filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._filter = event.value.strip().casefold()
        if self._snapshot is not None:
            self._rebuild(self._snapshot)

    @on(events.Key)
    def _escape_filter(self, event: events.Key) -> None:
        search = self.query_one("#ref-filter", Input)
        if event.key == "escape" and search.value and self.app.focused is search:
            event.stop()
            event.prevent_default()
            search.value = ""

    @on(Tree.NodeSelected, "#refs-tree")
    def _tree_selected(self, event: Tree.NodeSelected[SidebarSelection | _Group]) -> None:
        event.stop()
        self._click_latch = None
        self._activate_node(event.node)

    @on(Tree.NodeExpanded, "#refs-tree")
    def _remember_expanded(self, event: Tree.NodeExpanded[SidebarSelection | _Group]) -> None:
        event.stop()
        if (
            not self._filter
            and isinstance(event.node.data, (SidebarSelection, _Group))
            and self._tree_nodes.get(event.node.data.key) is event.node
        ):
            self._expanded[event.node.data.key] = True

    @on(Tree.NodeCollapsed, "#refs-tree")
    def _remember_collapsed(self, event: Tree.NodeCollapsed[SidebarSelection | _Group]) -> None:
        event.stop()
        if (
            not self._filter
            and isinstance(event.node.data, (SidebarSelection, _Group))
            and self._tree_nodes.get(event.node.data.key) is event.node
        ):
            self._expanded[event.node.data.key] = False

    def _tree_clicked(
        self, node: TreeNode[SidebarSelection | _Group], *, label_hit: bool, chain: int
    ) -> None:
        data = node.data
        if chain != 1 and chain != 2:
            return
        if chain == 2:
            first = self._click_latch
            self._click_latch = None
            if (
                label_hit
                and isinstance(data, SidebarSelection)
                and data.kind in {"local", "remote"}
                and first == (data.key, True)
                and self._root is not None
            ):
                self.post_message(self.CheckoutRequested(data, self._root))
            return
        self._click_latch = (data.key, label_hit) if isinstance(data, SidebarSelection) else None
        self._tree.cursor_line = node.line
        if not label_hit:
            if node.allow_expand:
                node.toggle()
            return
        self._activate_node(node)

    def _tree_context_requested(
        self,
        node: TreeNode[SidebarSelection | _Group],
        *,
        label_hit: bool,
        screen_x: int,
        screen_y: int,
    ) -> None:
        self._click_latch = None
        data = node.data
        if (
            label_hit
            and isinstance(data, SidebarSelection)
            and data.kind in {"local", "remote"}
            and self._root is not None
        ):
            self.post_message(
                self.ContextRequested(data, self._root, screen_x=screen_x, screen_y=screen_y)
            )

    def _activate_node(self, node: TreeNode[SidebarSelection | _Group]) -> None:
        if isinstance(node.data, _Group):
            if node.allow_expand:
                node.toggle()
            return
        if not isinstance(node.data, SidebarSelection):
            return
        selection = node.data
        self.set_active(selection)
        if self._root is not None:
            self.post_message(self.SelectionChanged(selection, self._root))

    def _cursor_key(self) -> str | None:
        node = self._tree.cursor_node
        if node is not None and isinstance(node.data, (SidebarSelection, _Group)):
            return node.data.key
        return None

    def _set_leaf_label(
        self, node: TreeNode[SidebarSelection | _Group], item: SidebarSelection, active: bool
    ) -> None:
        label = item.label.rsplit("/", 1)[-1] if item.kind == "local" else item.label
        text = Text(label, no_wrap=True, overflow="ellipsis")
        if getattr(node, "_sidebar_current", False):
            text.stylize("bold #006d77")
        if active:
            text.stylize("bold underline #14232c")
        node.set_label(text)

    def _add_leaf(
        self, parent: TreeNode, item: SidebarSelection, *, current: bool = False
    ) -> TreeNode:
        node = parent.add_leaf(Text(item.label, no_wrap=True, overflow="ellipsis"), item)
        node._sidebar_current = current  # type: ignore[attr-defined]
        self._tree_nodes[item.key] = node
        self._leaves[item.key] = item
        self._set_leaf_label(node, item, item.key == self._active_key)
        return node

    def _group(
        self, parent: TreeNode, key: str, label: str, *, default: bool, tooltip: str = ""
    ) -> TreeNode:
        node = parent.add(
            Text(label, no_wrap=True, overflow="ellipsis"),
            _Group(key, label, tooltip),
            expand=self._expanded.get(key, default),
        )
        self._tree_nodes[key] = node
        return node

    def _rebuild(self, snapshot: Snapshot, *, reset_scroll: bool = False) -> None:
        cursor_key = self._cursor_key()
        scroll_y = self._tree.scroll_offset.y
        # Textual 1.0 retains cursor nodes across remove_children, and new
        # nodes have line=-1 until layout. Clear the old pointer and materialize
        # lines before restoring the semantic cursor, not an obsolete row ID.
        self._tree.unselect()
        self._tree._cursor_node = None
        self._build(snapshot)
        self._tree.get_node_at_line(0)
        cursor = self._tree_nodes.get(cursor_key) if cursor_key else None
        if cursor is not None and cursor.line >= 0:
            self._tree.move_cursor(cursor, animate=False)
        self._tree.call_after_refresh(
            self._tree.scroll_to,
            x=0 if reset_scroll else None,
            y=0 if reset_scroll else scroll_y,
            animate=False,
            immediate=True,
        )

    def _build(self, snapshot: Snapshot) -> None:
        self._click_latch = None
        refs = tuple(ref for ref in snapshot.refs if self._matches_ref(ref))
        stashes = tuple(stash for stash in snapshot.stashes if self._matches_stash(stash))
        self._tree_nodes.clear()
        # Action context is independent of sidebar visibility. A search must
        # never turn a still-existing selected reference into a deleted one.
        self._leaves = {item.key: item for item in map(self._selection, snapshot.refs)}
        self._leaves.update(
            {item.key: item for item in map(self._stash_selection, snapshot.stashes)}
        )
        self._tree.root.remove_children()
        root = self._tree.root
        repo = self._group(root, "group:repository", "Repository", default=True)
        self._add_leaf(
            repo, SidebarSelection("worktree:worktree", "worktree", "Working directory", "worktree")
        )
        self._add_leaf(repo, SidebarSelection("all:all", "all", "All history"))

        local_refs = sorted(
            (ref for ref in refs if ref.kind == "local"), key=lambda x: x.name.casefold()
        )
        local = self._group(root, "group:local", f"Local ({len(local_refs)})", default=True)
        self._add_ref_paths(local, local_refs)

        remote_refs = [ref for ref in refs if ref.kind == "remote"]
        remotes = self._group(root, "group:remotes", f"Remotes ({len(remote_refs)})", default=True)
        configured = {remote.name: remote for remote in snapshot.remotes}
        buckets: dict[str, list[Ref]] = {name: [] for name in configured}
        unassigned: list[Ref] = []
        for ref in remote_refs:
            (buckets[ref.remote] if ref.remote in configured else unassigned).append(ref)
        for owner in sorted(buckets, key=str.casefold):
            remote_bucket = buckets[owner]
            if not remote_bucket and self._filter:
                continue
            remote = configured[owner]
            group = self._group(
                remotes,
                f"group:remote:{owner}",
                f"{owner} ({len(remote_bucket)})",
                default=True,
                tooltip=f"{remote.provider}: {remote.url}",
            )
            self._add_ref_paths(group, remote_bucket, remote_owner=owner)
        if unassigned:
            group = self._group(
                remotes, "group:unassigned", f"Unassigned ({len(unassigned)})", default=False
            )
            self._add_ref_paths(group, unassigned, remote_owner="")

        tags = sorted((ref for ref in refs if ref.kind == "tag"), key=lambda x: x.name.casefold())
        tag_group = self._group(root, "group:tags", f"Tags ({len(tags)})", default=False)
        for ref in tags:
            self._add_leaf(tag_group, self._selection(ref))
        stash_group = self._group(root, "group:stashes", f"Stashes ({len(stashes)})", default=False)
        for stash in stashes:
            self._add_leaf(stash_group, self._stash_selection(stash))
        if self._filter and not refs and not stashes:
            root.add(
                Text("No matching references", no_wrap=True),
                _Group("group:no-matches", "No matching references"),
                allow_expand=False,
            )
        if self._filter:
            for node in self._tree_nodes.values():
                if isinstance(node.data, SidebarSelection):
                    ancestor = node.parent
                    while ancestor is not None and ancestor is not root:
                        ancestor._expanded = True
                        ancestor = ancestor.parent
        self._tree._invalidate()

    def _add_ref_paths(
        self, parent: TreeNode, refs: list[Ref], *, remote_owner: str | None = None
    ) -> None:
        owner = remote_owner
        namespace = "local" if owner is None else (f"remote:{owner}" if owner else "unassigned")
        paths: dict[tuple[str, ...], TreeNode] = {(): parent}
        # A real branch may also be a namespace prefix in synthetic input. In
        # that case its data stays SidebarSelection and it gains child nodes.
        for ref in sorted(refs, key=lambda x: x.name.casefold()):
            path = (
                ref.name[len(owner) + 1 :]
                if owner and ref.name.startswith(f"{owner}/")
                else ref.name
            )
            parts = path.split("/")
            parent_node = parent
            for depth, part in enumerate(parts[:-1], start=1):
                token = tuple(parts[:depth])
                if token not in paths:
                    paths[token] = self._group(
                        parent_node,
                        f"group:namespace:{namespace}:{'/'.join(token)}",
                        part,
                        default=False,
                    )
                    if isinstance(parent_node.data, SidebarSelection):
                        parent_node.allow_expand = True
                parent_node = paths[token]
                if isinstance(parent_node.data, SidebarSelection):
                    parent_node.allow_expand = True
                    parent_node._expanded = self._expanded.get(parent_node.data.key, False)
            token = tuple(parts)
            item = self._selection(ref)
            if token in paths:
                node = paths[token]
                old = node.data
                if isinstance(old, _Group):
                    self._tree_nodes.pop(old.key, None)
                node.data = item
                node.allow_expand = bool(node.children)
                node._sidebar_current = ref.current  # type: ignore[attr-defined]
                self._tree_nodes[item.key] = node
                self._leaves[item.key] = item
                self._set_leaf_label(node, item, item.key == self._active_key)
            else:
                paths[token] = self._add_leaf(parent_node, item, current=ref.current)

    @staticmethod
    def _selection(ref: Ref) -> SidebarSelection:
        return SidebarSelection(
            key=f"{ref.kind}:{ref.full_name}",
            kind=ref.kind,
            label=ref.name.rsplit("/", 1)[-1] if ref.kind == "remote" else ref.name,
            target=ref.full_name,
            oid=ref.oid,
            remote=ref.remote,
        )

    @staticmethod
    def _stash_selection(stash: Stash) -> SidebarSelection:
        return SidebarSelection(
            f"stash:{stash.oid}", "stash", stash.subject or stash.ref, stash.ref, stash.oid
        )

    def _matches_ref(self, ref: Ref) -> bool:
        if not self._filter:
            return True
        return self._filter in " ".join((ref.name, ref.full_name, ref.kind, ref.remote)).casefold()

    def _matches_stash(self, stash: Stash) -> bool:
        if not self._filter:
            return True
        return self._filter in " ".join((stash.ref, stash.oid, stash.subject, "stash")).casefold()
