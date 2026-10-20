"""A local-first Git workspace. Git calls are serialized and never block the UI."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Self

from rich.markup import escape
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from .commit_files import CommitFileTree
from .dates import format_commit_date
from .dialogs import ActionPicker, BranchContextMenu, HelpDialog, PromptDialog
from .git import Repository
from .graph import GraphRow, GraphStyle, render_history
from .models import Action, Commit, FileChange, HistoryLine, Snapshot
from .ref_badges import RefBadges, build_ref_badges
from .sidebar import ReferenceSidebar, SidebarSelection

TEAL = "#62d6c5"
MUTED = "#7e91a4"


def patch_text(patch: str) -> Text:
    """Color a plain patch without interpreting markup or terminal escape sequences."""
    text = Text()
    for line in patch.splitlines(keepends=True):
        style = None
        if line.startswith(("diff ", "index ", "--- ", "+++ ")):
            style = "bold #8baac4"
        elif line.startswith("@@"):
            style = "#c5a6ef"
        elif line.startswith("+"):
            style = "#9ed8a5"
        elif line.startswith("-"):
            style = "#ee939b"
        text.append(line, style)
    return text


class HistoryTable(DataTable):
    """History tooltips and double-click activation, separate from row selection."""

    class CommitActivated(Message):
        def __init__(self, key: str, revision: int) -> None:
            self.key, self.revision = key, revision
            super().__init__()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.revision = 0
        self._click_latch: str | None = None

    def clear(self, columns: bool = False) -> Self:
        self.revision += 1
        self._click_latch = None
        return super().clear(columns=columns)

    async def _on_click(self, event: events.Click) -> None:
        # DataTable selects on every Click but discards Click.chain. Handle it
        # here so inspection stays single-click and checkout needs two clicks
        # on the same row, with no rebuild in between.
        event.prevent_default()
        event.stop()
        if event.button != 1:
            self._click_latch = None
            return
        row_index = event.style.meta.get("row", -1)
        key = (
            str(self.ordered_rows[row_index].key.value) if 0 <= row_index < self.row_count else None
        )
        first = self._click_latch
        self._click_latch = key if event.chain == 1 else None
        if event.chain == 1:
            await super()._on_click(event)
        elif event.chain == 2 and key is not None and key == first:
            self.post_message(self.CommitActivated(key, self.revision))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        meta = event.style.meta
        description = meta.get("ref_description") or meta.get("commit_date")
        self.tooltip = Text(description) if isinstance(description, str) else None


class GitKraikenApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "GitKraiken"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+p", "command_picker", "Actions", priority=True),
        Binding("ctrl+o", "open_repository", "Open", priority=True),
        Binding("ctrl+r", "refresh", "Refresh", priority=True),
        Binding("ctrl+b", "new_branch", "Branch", priority=True),
        Binding("f,slash", "focus_filter", "Search"),
        Binding("r", "refresh", "Refresh"),
        Binding("x", "command_picker", "Actions"),
        Binding("g", "toggle_graph_style", "Graph style"),
        Binding("question_mark", "help", "Help"),
        Binding("j", "next_row", "Next", show=False),
        Binding("k", "previous_row", "Previous", show=False),
        Binding("ctrl+left", "toggle_refs", "Refs", show=False),
        Binding("ctrl+right", "toggle_inspector", "Inspector", show=False),
        Binding("escape", "clear_filter", "Clear selection", show=False),
    ]

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.repo: Repository | None = None
        self.snapshot_data: Snapshot | None = None
        self.limit = 300
        self.busy = False
        self._serial = asyncio.Lock()
        self._generation = 0
        self._detail_token = 0
        self._diff_token = 0
        self._diff_tokens = {"worktree": 0, "commit": 0}
        self._commits: dict[str, Commit] = {}
        self._file_rows: dict[str, tuple[FileChange, bool]] = {}
        self._selected_commit: Commit | None = None
        self._selected_file: tuple[FileChange, bool] | None = None
        self._sidebar_selection: SidebarSelection | None = None
        self._reveal_ref_key: str | None = None
        self._ref_badges: dict[str, RefBadges] = {}
        self._rebuilding = False
        self._history_key = "wip"
        self.graph_style: GraphStyle = "smooth"
        self._graph_rows: tuple[GraphRow, ...] = ()
        self._graph_cache_key: tuple[tuple[HistoryLine, ...], GraphStyle] | None = None
        self._graph_geometry: tuple[tuple[str, int], ...] = ()
        self._search_matches: list[str] = []
        self._last_error = ""

    @property
    def _selected_ref(self) -> str:
        return self._sidebar_selection.target if self._sidebar_selection else ""

    @property
    def _selected_kind(self) -> str:
        return self._sidebar_selection.kind if self._sidebar_selection else ""

    def compose(self) -> ComposeResult:
        yield Static("GITKRAIKEN  /  Opening repository…", id="repo-header")
        with Horizontal(id="toolbar"):
            for ident, label in [
                ("open", "Open"),
                ("fetch", "↓ Fetch"),
                ("pull", "↓ Pull"),
                ("push", "↑ Push"),
                ("branch", "⑂ Branch"),
                ("stash", "Stash"),
                ("pop", "Pop"),
                ("actions", "Actions ▾"),
                ("refresh", "↻"),
                ("help", "?"),
            ]:
                yield Button(label, id=ident, classes="tool primary" if ident == "push" else "tool")
        with Horizontal(id="operation-banner"):
            yield Static(id="operation-text")
            yield Button("Continue", id="continue", classes="operation-button")
            yield Button("Abort", id="abort", classes="operation-button")
        with Horizontal(id="workspace"):
            with Vertical(id="refs-panel"):
                yield ReferenceSidebar(id="reference-sidebar")
            with Vertical(id="history-panel"):
                yield Static("COMMIT GRAPH", id="history-heading")
                with Horizontal(id="search-bar"):
                    yield Input(
                        placeholder="Search commits, authors, refs or SHA…", id="graph-filter"
                    )
                    yield Button("Next", id="search-next", classes="tool")
                    yield Button(
                        Text("Smooth [g]"),
                        id="graph-style",
                        tooltip="Switch Smooth / Compact graph (g). Smooth uses rounded bends; "
                        "broken horizontal lines pass behind vertical lanes without joining them.",
                    )
                yield Static("Unified history · local + remote branches", id="history-caption")
                yield HistoryTable(
                    id="graph-table",
                    cursor_type="row",
                    zebra_stripes=False,
                    cursor_foreground_priority="renderable",
                )
                yield Button("Load 300 more commits", id="load-more")
            with Vertical(id="inspector-panel"):
                with TabbedContent(id="inspector-tabs"):
                    with TabPane("Commit", id="details-tab"):
                        yield Static("Select a commit to inspect its changes.", id="commit-detail")
                        yield CommitFileTree(id="commit-file-tree")
                        with VerticalScroll(id="commit-diff-scroll"):
                            yield Static(
                                "Select a changed file to read its diff.", id="commit-diff"
                            )
                    with TabPane("Working directory", id="wip-tab"):
                        with Horizontal(classes="wip-actions"):
                            yield Button("Stage", id="stage")
                            yield Button("Unstage", id="unstage")
                            yield Button("Discard", id="discard", variant="error")
                        with Horizontal(classes="wip-actions"):
                            yield Button("Stage all", id="stage-all")
                            yield Button("Unstage all", id="unstage-all")
                        yield DataTable(id="wip-table", cursor_type="row")
                        yield Static("Select a file to inspect its changes", id="diff-title")
                        with VerticalScroll(id="diff-scroll"):
                            yield Static("Working directory is clean.", id="diff-view")
                        yield Label("COMMIT MESSAGE", classes="composer-label")
                        yield TextArea(id="commit-message")
                        yield Button("Commit staged changes", id="commit", variant="primary")
        yield Static("Ready", id="status-line")
        yield Static(
            "Ctrl+P actions   / search   g graph   Ctrl+R refresh   ? help   Ctrl+Q quit",
            id="key-hint",
        )

    def on_mount(self) -> None:
        self.query_one("#operation-banner").display = False
        self.query_one("#load-more").display = False
        self.query_one("#wip-table", DataTable).add_columns("Area", "File")
        self._configure_history_columns()
        self.query_one("#history-heading", Static).tooltip = Text(
            "💻 Local branch / HEAD · 🐙 GitHub · ☁ Azure DevOps · 🦊 GitLab · ↗ Other remote\n"
            "Both markers share a commit when local and remote tips agree.\n"
            "Remote markers reflect last-fetched refs; Fetch updates them."
        )
        self.set_interval(5, self.poll_repository)
        self.request_refresh()
        self.query_one("#graph-table").focus()

    def _configure_history_columns(self) -> None:
        table = self.query_one("#graph-table", DataTable)
        table.clear(columns=True)
        # Never crop away graph lanes: wide histories scroll horizontally instead.
        graph_width = max(5, max((row.width for row in self._graph_rows), default=3) + 1)
        available = max(36, table.size.width)
        ref_width = min(23, max(12, available // 5))
        self._show_sha = available >= 80
        date_width = 16  # YYYY-MM-DD HH:MM, in the computer's local timezone.
        subject_width = max(
            24, available - graph_width - ref_width - date_width - (10 if self._show_sha else 0) - 9
        )
        table.add_column("GRAPH", width=graph_width, key="graph")
        table.add_column("BRANCH / TAG", width=ref_width, key="refs")
        table.add_column("DATE / TIME", width=date_width, key="date")
        table.add_column("COMMIT", width=subject_width, key="subject")
        if self._show_sha:
            table.add_column("SHA", width=8, key="sha")

    def on_resize(self, event: events.Resize) -> None:
        self.screen.set_class(event.size.width < 140, "compact")
        self.screen.set_class(event.size.width < 105, "narrow")
        if self.is_mounted and self.snapshot_data:
            self.call_after_refresh(self.repaint_history)

    def poll_repository(self) -> None:
        if not self.busy and len(self.screen_stack) == 1:
            self.request_refresh(quiet=True)

    def request_refresh(self, *, quiet: bool = False) -> None:
        if self.busy:
            return
        self.set_busy(True, "Refreshing local repository…" if not quiet else "")
        # Never cancel a worker running to_thread: the underlying Git process would
        # keep mutating after cancellation and could overlap a second operation.
        self.run_worker(self.load_snapshot(quiet=quiet), group="repository", exit_on_error=False)

    async def load_snapshot(self, *, quiet: bool = False) -> None:
        try:
            async with self._serial:
                if self.repo is None:
                    self.repo = await asyncio.to_thread(Repository, self.path)
                snapshot = await asyncio.to_thread(self.repo.snapshot, self.limit)
            if snapshot != self.snapshot_data:
                await self.apply_snapshot(snapshot)
            elif self._selected_file:
                # Status codes can stay unchanged while the selected file is edited.
                self.inspect_worktree_file(*self._selected_file)
            if not quiet:
                self.status("Local state refreshed · no network request")
                self._last_error = ""
        except Exception as error:
            self.show_error(str(error))
        finally:
            self.set_busy(False)

    async def apply_snapshot(self, snapshot: Snapshot) -> None:
        self._generation += 1
        self._detail_token += 1
        self._diff_token += 1
        self.snapshot_data = snapshot
        header = Text(" GITKRAIKEN ", style=f"bold {TEAL}")
        if "DEMO" in self.title:
            header.append(" DEMO ", style="bold #f2c779")
        header.append(f" / {snapshot.root.name}  ", style="bold #e1e7ef")
        header.append(f"⑂ {snapshot.branch or 'detached HEAD'}", style=TEAL)
        header.append(f"   ↑{snapshot.ahead} ↓{snapshot.behind}", style=MUTED)
        if snapshot.upstream:
            header.append(f"   {snapshot.upstream}", style=MUTED)
        self.query_one("#repo-header", Static).update(header)
        previous = self._sidebar_selection
        self._sidebar_selection = self.query_one(ReferenceSidebar).update_snapshot(
            snapshot, previous
        )
        if previous and self._sidebar_selection is None:
            self._reveal_ref_key = None
            self.notify(escape(f"{previous.label} is no longer available."), severity="warning")
        selection = self._sidebar_selection
        if selection and self._reveal_ref_key == selection.key:
            if any(line.commit and line.commit.oid == selection.oid for line in snapshot.history):
                self._history_key = selection.oid
                self._reveal_ref_key = None
        self._ref_badges = build_ref_badges(snapshot, self._selected_ref)
        self.repaint_history()
        self.paint_wip(snapshot.files)
        banner = self.query_one("#operation-banner")
        banner.display = bool(snapshot.operation or snapshot.conflicts)
        self.query_one("#operation-text", Static).update(
            Text(
                f"{snapshot.operation.upper() or 'CONFLICTS'}  ·  {len(snapshot.conflicts)} unresolved files"
                "  ·  Resolve in your editor, stage, then continue"
            )
        )
        self.query_one("#continue", Button).disabled = bool(snapshot.conflicts)
        self.query_one("#load-more").display = snapshot.has_more and self.limit < 5000
        # Re-inspect the selected item against this snapshot, including external edits.
        if self._history_key in self._commits:
            self.inspect_commit(self._commits[self._history_key])
        else:
            self._selected_commit = None
            self.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
        if self._selected_file:
            old_file, staged = self._selected_file
            current = next(
                (
                    (f, s)
                    for f, s in self._file_rows.values()
                    if f.path == old_file.path and s == staged
                ),
                None,
            )
            self._selected_file = current
            if current:
                self.inspect_worktree_file(*current)
            else:
                self.query_one("#diff-view", Static).update(
                    Text("Select a file to inspect its changes.")
                )
        self.update_controls()

    def _clear_reference_selection(self) -> None:
        self._sidebar_selection = None
        self._reveal_ref_key = None
        self.query_one(ReferenceSidebar).set_active(None)
        self._update_reference_badges()

    def _update_reference_badges(self) -> None:
        if not self.snapshot_data:
            return
        old = self._ref_badges
        self._ref_badges = build_ref_badges(self.snapshot_data, self._selected_ref)
        table = self.query_one("#graph-table", DataTable)
        for oid in self._commits:
            if oid in table.rows and old.get(oid) != self._ref_badges.get(oid):
                badges = self._ref_badges.get(oid)
                table.update_cell(oid, "refs", badges.text if badges else Text())

    def _current_sidebar_selection(
        self, root: Path, selection: SidebarSelection
    ) -> SidebarSelection | None:
        if len(self.screen_stack) > 1 or not self.repo or root != self.repo.root:
            return None
        current = self.query_one(ReferenceSidebar).get_selection(selection.key)
        return current if current == selection else None

    def on_reference_sidebar_selection_changed(
        self, event: ReferenceSidebar.SelectionChanged
    ) -> None:
        event.stop()
        selection = self._current_sidebar_selection(event.root, event.selection)
        if selection is None:
            return
        self._sidebar_selection = selection
        self._reveal_ref_key = None
        self.query_one(ReferenceSidebar).set_active(selection)
        if selection.kind == "worktree":
            self.jump_to("wip")
        elif selection.kind == "all":
            self.action_clear_filter()
            self.status("Unified graph · all local and remote branches")
        elif selection.oid in self._commits:
            hint = " · double-click to check out" if selection.kind in {"local", "remote"} else ""
            self.status(f"Selected {selection.label}{hint} · graph position unchanged")
        else:
            self.status(f"Selected {selection.label} · tip is outside the loaded unified graph")
        self._update_reference_badges()
        self.update_search(jump=False)

    def on_reference_sidebar_checkout_requested(
        self, event: ReferenceSidebar.CheckoutRequested
    ) -> None:
        event.stop()
        selection = self._current_sidebar_selection(event.root, event.selection)
        if selection is None or selection.kind not in {"local", "remote"} or self.busy:
            return
        self._sidebar_selection = selection
        self.query_one(ReferenceSidebar).set_active(selection)
        self.run_mutation(Action("checkout", target=selection.target, remote=selection.remote))

    def on_reference_sidebar_context_requested(
        self, event: ReferenceSidebar.ContextRequested
    ) -> None:
        event.stop()
        selection = self._current_sidebar_selection(event.root, event.selection)
        snapshot = self.snapshot_data
        if (
            self.busy
            or selection is None
            or selection.kind not in {"local", "remote"}
            or not snapshot
        ):
            return
        if not snapshot.branch:
            self.notify("Check out a branch before rebasing.", severity="warning")
            return
        if selection.target == f"refs/heads/{snapshot.branch}":
            self.notify(
                "This is already the current branch. Choose a different base.", severity="warning"
            )
            return
        if snapshot.operation or snapshot.conflicts:
            self.notify(
                "Finish or abort the current operation before rebasing.", severity="warning"
            )
            return
        self._sidebar_selection = selection
        self._reveal_ref_key = None
        self.query_one(ReferenceSidebar).set_active(selection)
        self._update_reference_badges()
        self.update_search(jump=False)
        repo = self.repo
        action = Action(
            "rebase",
            target=selection.target,
            expected_oid=selection.oid,
            expected_branch=snapshot.branch,
            expected_head=snapshot.head,
        )

        def selected(operation: str | None) -> None:
            if operation != "rebase":
                return
            if (
                self.repo is not repo
                or self.snapshot_data is None
                or self.snapshot_data.branch != snapshot.branch
                or self.snapshot_data.head != snapshot.head
                or self._current_sidebar_selection(event.root, selection) is None
            ):
                self.notify(
                    "Repository or branch changed. Choose the rebase again.", severity="warning"
                )
                return
            self.run_mutation(action)

        target = selection.target.removeprefix("refs/heads/").removeprefix("refs/remotes/")
        self.push_screen(
            BranchContextMenu(
                snapshot.branch, target, screen_x=event.screen_x, screen_y=event.screen_y
            ),
            selected,
        )

    def on_history_table_commit_activated(self, event: HistoryTable.CommitActivated) -> None:
        event.stop()
        if (
            self.busy
            or len(self.screen_stack) > 1
            or not self.snapshot_data
            or event.revision != self.query_one(HistoryTable).revision
            or event.key not in self._commits
        ):
            return
        # Branch tips should switch branches, not silently detach at their OID.
        # Local + tracking pointers at one tip represent a single local choice.
        refs = [ref for ref in self.snapshot_data.refs if ref.oid == event.key]
        candidates = [ref for ref in refs if ref.kind == "local"] or [
            ref for ref in refs if ref.kind == "remote"
        ]
        if not candidates:
            self.run_mutation(Action("checkout", target=event.key))
            return
        repo = self.repo

        def selected(target: str | None) -> None:
            if not target or self.repo is not repo:
                return
            ref = next((ref for ref in candidates if ref.full_name == target), None)
            if ref is None or not self.snapshot_data or ref not in self.snapshot_data.refs:
                self.notify("Branch changed. Choose the checkout again.", severity="warning")
                return
            self.run_mutation(Action("checkout", target=ref.full_name, remote=ref.remote))

        if len(candidates) == 1:
            selected(candidates[0].full_name)
        else:
            self.push_screen(
                ActionPicker(
                    "CHECK OUT BRANCH AT COMMIT",
                    [(ref.full_name, ref.full_name) for ref in candidates],
                ),
                selected,
            )

    def repaint_history(self) -> None:
        if not self.snapshot_data:
            return
        self._rebuilding = True
        table = self.query_one("#graph-table", DataTable)
        previous_scroll = table.scroll_offset
        cache_key = (self.snapshot_data.history, self.graph_style)
        if cache_key != self._graph_cache_key:
            self._graph_rows = render_history(self.snapshot_data.history, self.graph_style)
            self._graph_cache_key = cache_key
        heading = Text("COMMIT GRAPH", no_wrap=True, overflow="ellipsis")
        if self.graph_style == "smooth" and any("╴" in row.graph.plain for row in self._graph_rows):
            heading.append("   ╴│╶ crossing, not a join", style=MUTED)
        self.query_one("#history-heading", Static).update(heading)
        geometry = tuple(
            (row.commit.oid if row.commit else f"connector-{index}", row.height)
            for index, row in enumerate(self._graph_rows)
        )
        preserve_scroll = bool(geometry) and geometry == self._graph_geometry
        self._graph_geometry = geometry
        self._configure_history_columns()
        self._commits = {}
        dirty = len(self.snapshot_data.files)
        wip_cells = [
            Text(" ◉" if self.graph_style == "smooth" else "◉", style=TEAL),
            Text("WIP", style=TEAL),
            "",  # Working changes have no commit timestamp.
            Text(
                f"Working directory · {dirty} changed files"
                if dirty
                else "Working directory · clean",
                style=f"bold {TEAL}",
            ),
        ]
        table.add_row(
            *wip_cells,
            *([""] if self._show_sha else []),
            height=2 if self.graph_style == "smooth" else 1,
            key="wip",
        )
        for index, row in enumerate(self._graph_rows):
            if not row.commit:
                table.add_row(
                    row.graph,
                    "",
                    "",
                    "",
                    *([""] if self._show_sha else []),
                    key=f"connector-{index}",
                )
                continue
            commit = row.commit
            self._commits[commit.oid] = commit
            badges = self._ref_badges.get(commit.oid)
            refs = badges.text if badges else Text()
            message = Text(commit.subject, style="#dce3ed", no_wrap=True, overflow="ellipsis")
            table.add_row(
                row.graph,
                refs,
                Text(
                    format_commit_date(commit.date),
                    style=Style(
                        color=MUTED,
                        meta={"commit_date": format_commit_date(commit.date, full=True)},
                    ),
                    no_wrap=True,
                ),
                message,
                *([Text(commit.short_id, style=MUTED)] if self._show_sha else []),
                height=row.height,
                key=commit.oid,
            )
        key = self._history_key if self._history_key in table.rows else "wip"
        table.move_cursor(row=table.get_row_index(key), animate=False)
        self._history_key = key
        self.update_search(jump=False)
        if preserve_scroll:
            self.call_after_refresh(
                table.scroll_to, previous_scroll.x, previous_scroll.y, animate=False
            )
        self.call_after_refresh(self._finish_rebuild)

    def _finish_rebuild(self) -> None:
        self._rebuilding = False

    def paint_wip(self, files: tuple[FileChange, ...]) -> None:
        table = self.query_one("#wip-table", DataTable)
        table.clear()
        self._file_rows = {}
        for staged in (False, True):
            for number, file in enumerate(files):
                if (staged and not file.staged) or (not staged and not file.unstaged):
                    continue
                key = f"{'s' if staged else 'u'}-{number}"
                self._file_rows[key] = (file, staged)
                state = "CONFLICT" if file.conflicted else ("STAGED" if staged else "UNSTAGED")
                color = "#f2c779" if file.conflicted else (TEAL if staged else "#a3b1c4")
                table.add_row(Text(state, style=color), Text(file.path), key=key)
                if (
                    self._selected_file
                    and file.path == self._selected_file[0].path
                    and staged == self._selected_file[1]
                ):
                    table.move_cursor(row=table.get_row_index(key), animate=False)
        if not files:
            self._selected_file = None
            self.query_one("#diff-title", Static).update("No pending changes")
            self.query_one("#diff-view", Static).update("Working directory is clean.")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not self._rebuilding:
            self.select_row(event.data_table.id, str(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.select_row(event.data_table.id, str(event.row_key.value), explicit=True)

    def select_row(self, table_id: str | None, key: str, *, explicit: bool = False) -> None:
        if table_id == "graph-table":
            selection = self._sidebar_selection
            if (
                selection
                and (explicit or key != self._history_key)
                and (key == "wip" or key in self._commits)
            ):
                same_target = key == ("wip" if selection.kind == "worktree" else selection.oid)
                if not same_target:
                    self._clear_reference_selection()
                    self.update_search(jump=False)
            if key == "wip":
                self._history_key = key
                self._detail_token += 1
                self._selected_commit = None
                self.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
            elif key in self._commits:
                self._history_key = key
                self.inspect_commit(self._commits[key])
        elif table_id == "wip-table" and key in self._file_rows:
            self.inspect_worktree_file(*self._file_rows[key])

    def on_commit_file_tree_file_selected(self, event: CommitFileTree.FileSelected) -> None:
        event.stop()
        if self._selected_commit and event.identity == self._selected_commit.oid:
            self.inspect_commit_file(event.file)

    def inspect_commit(self, commit: Commit) -> None:
        changed_commit = self._selected_commit is None or self._selected_commit.oid != commit.oid
        self._selected_commit = commit
        self._detail_token += 1
        self._diff_token += 1
        token, generation, repo = self._detail_token, self._generation, self.repo
        self.query_one("#inspector-tabs", TabbedContent).active = "details-tab"
        self.query_one("#commit-detail", Static).update(Text(f"{commit.subject}\nLoading commit…"))
        if changed_commit:
            self.query_one(CommitFileTree).clear_files()
        self.query_one("#commit-diff", Static).update("Loading changes…")

        async def load() -> None:
            try:
                async with self._serial:
                    if (
                        token != self._detail_token
                        or generation != self._generation
                        or repo is not self.repo
                    ):
                        return
                    detail = await asyncio.to_thread(repo.detail, commit.oid)
                if token != self._detail_token or generation != self._generation:
                    return
                local_date = format_commit_date(commit.date, full=True)
                text = Text(
                    commit.subject + "\n", style="bold #dce3ed", no_wrap=True, overflow="ellipsis"
                )
                text.append(f"{commit.author} · {commit.short_id}\n{local_date}", style=MUTED)
                metadata = Text(
                    f"{commit.subject}\n{commit.author} <{commit.email}>\n{local_date}\n{commit.oid}"
                )
                badges = self._ref_badges.get(commit.oid)
                if badges:
                    metadata.append("\n")
                    metadata.append(badges.description)
                header = self.query_one("#commit-detail", Static)
                header.update(text)
                header.tooltip = metadata
                tree = self.query_one(CommitFileTree)
                tree.set_files(detail.files, identity=commit.oid)
                self.query_one("#commit-diff", Static).update(
                    "Select a changed file." if detail.files else "No file changes."
                )
                if tree.selected_file:
                    self.inspect_commit_file(tree.selected_file)
                elif detail.files:
                    tree.select_file(detail.files[0])
            except Exception as error:
                if token == self._detail_token and generation == self._generation:
                    self.show_error(str(error))

        self.run_worker(load(), group="detail", exit_on_error=False)

    def inspect_commit_file(self, file: FileChange) -> None:
        if not self._selected_commit:
            return
        self.load_diff(file, oid=self._selected_commit.oid)

    def inspect_worktree_file(self, file: FileChange, staged: bool) -> None:
        self._selected_file = (file, staged)
        self.query_one("#diff-title", Static).update(
            Text(f"{'INDEX' if staged else 'WORKTREE'} · {file.path}")
        )
        self.load_diff(file, staged=staged)
        self.update_controls()

    def load_diff(self, file: FileChange, *, staged: bool = False, oid: str = "") -> None:
        scope = "commit" if oid else "worktree"
        self._diff_tokens[scope] += 1
        token, epoch = self._diff_tokens[scope], self._diff_token
        generation, repo = self._generation, self.repo
        target = "#commit-diff" if oid else "#diff-view"
        self.query_one(target, Static).update("Loading diff…")

        async def load() -> None:
            try:
                async with self._serial:
                    if (
                        token != self._diff_tokens[scope]
                        or epoch != self._diff_token
                        or generation != self._generation
                        or repo is not self.repo
                    ):
                        return
                    patch = await asyncio.to_thread(repo.diff, file.path, staged=staged, oid=oid)
            except Exception as error:
                patch = str(error)
            if (
                token == self._diff_tokens[scope]
                and epoch == self._diff_token
                and generation == self._generation
            ):
                self.query_one(target, Static).update(
                    patch_text(patch or "No textual changes (mode-only change or empty file).")
                )
                self.query_one("#commit-diff-scroll" if oid else "#diff-scroll").scroll_home(
                    animate=False
                )

        self.run_worker(load(), group="diff", exit_on_error=False)

    def update_search(self, *, jump: bool = True) -> None:
        needle = self.query_one("#graph-filter", Input).value.strip().casefold()
        self._search_matches = [
            c.oid
            for c in self._commits.values()
            if needle
            and needle in " ".join((c.subject, c.author, c.email, c.oid, *c.refs)).casefold()
        ]
        caption = f"All branches + remotes · {len(self._commits)} commits"
        if needle:
            caption += f" · {len(self._search_matches)} matches (graph preserved)"
        if not self._commits:
            caption += " · No commits yet. Stage files to begin."
        selection = self._sidebar_selection
        if selection and selection.oid and selection.oid not in self._commits:
            if self.snapshot_data and self.snapshot_data.has_more and self.limit < 5000:
                caption = f"Tip not loaded · Load more to locate {selection.label} · unified graph"
            elif self.limit >= 5000:
                caption = f"Tip outside 5,000-commit display limit · {selection.label}"
            else:
                caption = f"Tip not in loaded commit graph · {selection.label} · Actions available"
        self.query_one("#history-caption", Static).update(Text(caption))
        self.query_one("#history-caption", Static).tooltip = Text(caption)
        self.query_one("#search-next", Button).disabled = not self._search_matches
        if jump and self._search_matches:
            self.jump_to(self._search_matches[0])

    def jump_to(self, key: str) -> None:
        table = self.query_one("#graph-table", DataTable)
        if key in table.rows:
            table.move_cursor(row=table.get_row_index(key), animate=False)
            self.select_row("graph-table", key)

    def next_match(self) -> None:
        if self._search_matches:
            index = (
                self._search_matches.index(self._history_key) + 1
                if self._history_key in self._search_matches
                else 0
            )
            self.jump_to(self._search_matches[index % len(self._search_matches)])

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "graph-filter" and self.snapshot_data:
            self.update_search()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "graph-filter":
            self.next_match()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ident = event.button.id or ""
        if len(self.screen_stack) > 1:
            return
        if ident == "help":
            self.action_help()
        elif ident == "open":
            self.action_open_repository()
        elif ident == "search-next":
            self.next_match()
        elif ident == "graph-style":
            self.action_toggle_graph_style()
        elif ident == "actions":
            self.action_command_picker()
        elif ident == "refresh":
            self.action_refresh()
        elif self.busy:
            return
        elif ident == "load-more":
            if self._sidebar_selection and self._sidebar_selection.oid not in self._commits:
                self._reveal_ref_key = self._sidebar_selection.key
            self.limit = min(5000, self.limit + 300)
            self.request_refresh()
        elif ident == "branch":
            self.action_new_branch()
        elif ident == "stash":
            self.push_screen(
                PromptDialog("Stash tracked and untracked changes", "Label", "Work in progress"),
                lambda message: (
                    self.run_mutation(Action("stash", message=message)) if message else None
                ),
            )
        elif ident == "pop":
            self.choose_stash("stash-pop")
        elif ident in {"fetch", "pull", "push"}:
            self.network_action(ident)
        elif ident in {"stage", "unstage", "discard", "stage-all", "unstage-all"}:
            self.wip_action(ident)
        elif ident == "commit":
            self.commit_changes()
        elif ident in {"continue", "abort"}:
            self.run_mutation(Action(ident))

    def action_command_picker(self) -> None:
        if self.busy or len(self.screen_stack) > 1:
            return
        actions = [
            ("open", "Open another repository"),
            ("refresh", "Refresh local repository"),
            ("create-branch", "Create and check out a branch"),
        ]
        if self._sidebar_selection and self._sidebar_selection.oid:
            actions.append(
                ("locate-ref", f"Locate {self._sidebar_selection.label} in unified graph")
            )
        if self._selected_ref and self._selected_kind in {"local", "remote", "tag"}:
            actions.extend(
                [
                    (op, f"{label} {self._selected_ref}")
                    for op, label in [
                        ("checkout", "Check out"),
                        ("merge", "Merge into current branch from"),
                        ("rebase", "Rebase current branch onto"),
                    ]
                ]
            )
            if self._selected_kind == "local":
                actions.extend(
                    [
                        ("rename-branch", "Rename selected branch"),
                        ("delete-branch", "Delete selected branch (merged only)"),
                    ]
                )
            elif self._selected_kind == "tag":
                actions.append(("delete-tag", "Delete selected local tag"))
        if self._selected_kind == "stash":
            actions.extend(
                [
                    ("stash-apply", "Apply selected stash (keep it)"),
                    ("stash-pop", "Pop selected stash"),
                    ("stash-drop", "Delete selected stash"),
                ]
            )
        if self._selected_commit:
            actions.extend(
                [
                    ("checkout-commit", "Check out selected commit (detached HEAD)"),
                    ("cherry-pick", "Cherry-pick selected commit"),
                    ("revert", "Revert selected commit"),
                    ("reset-soft", "Soft reset to selected commit (keep changes staged)"),
                    ("tag", "Tag selected commit"),
                ]
            )
        actions.extend(
            [
                ("stash", "Stash working changes"),
                ("fetch", "Fetch all remotes"),
                ("pull", "Pull upstream (fast-forward only)"),
                ("push", "Push branch (never force)"),
            ]
        )
        if self.snapshot_data and self.snapshot_data.operation:
            actions.extend(
                [("continue", "Continue current operation"), ("abort", "Abort current operation")]
            )
        actions.extend(
            [
                ("all", "Unified graph / clear reference selection"),
                ("graph-style", "Switch Smooth / Compact commit graph"),
                ("help", "Keyboard shortcuts and help"),
            ]
        )
        self.push_screen(ActionPicker("REPOSITORY ACTIONS", actions), self.handle_picked_action)

    def handle_picked_action(self, operation: str | None) -> None:
        if not operation:
            return
        if operation == "open":
            self.action_open_repository()
        elif operation == "refresh":
            self.action_refresh()
        elif operation == "all":
            self.action_clear_filter()
        elif operation == "locate-ref":
            selection = self._sidebar_selection
            if selection and selection.oid in self._commits:
                self.jump_to(selection.oid)
                self.query_one("#graph-table", DataTable).focus()
            else:
                self.status("Selected tip is outside loaded history. Use Load more to locate it.")
        elif operation == "help":
            self.action_help()
        elif operation == "graph-style":
            self.action_toggle_graph_style()
        elif operation in {"fetch", "pull", "push"}:
            self.network_action(operation)
        elif operation in {"create-branch", "tag", "rename-branch"}:
            self.push_screen(
                PromptDialog(operation.replace("-", " ").title(), "Name"),
                lambda name: self.named_action(operation, name),
            )
        elif operation == "stash":
            self.run_mutation(Action("stash", message="Work in progress"))
        elif operation.startswith("stash-"):
            self.choose_stash(operation)
        else:
            commit_op = operation in {"checkout-commit", "cherry-pick", "revert", "reset-soft"}
            target = (
                self._selected_commit.oid
                if commit_op and self._selected_commit
                else self._selected_ref
            )
            if operation == "checkout-commit":
                operation = "checkout"
            remote = (
                self._sidebar_selection.remote
                if operation == "checkout" and not commit_op and self._sidebar_selection
                else ""
            )
            self.run_mutation(Action(operation, target=target, remote=remote))

    def named_action(self, operation: str, name: str | None) -> None:
        if not name:
            return
        target = (
            self._sidebar_selection.oid
            if operation == "create-branch"
            and self._sidebar_selection
            and self._sidebar_selection.kind in {"local", "remote", "tag"}
            else (self._selected_commit.oid if self._selected_commit else "")
        )
        if operation == "rename-branch":
            target = self._selected_ref
        self.run_mutation(Action(operation, name=name, target=target))

    def choose_stash(self, operation: str) -> None:
        if not self.snapshot_data or not self.snapshot_data.stashes:
            self.notify("There are no stashes.", severity="warning")
            return

        stashes = {stash.ref: stash for stash in self.snapshot_data.stashes}

        def selected(target: str | None) -> None:
            if target and target in stashes:
                stash = stashes[target]
                self.run_mutation(Action(operation, target=target, expected_oid=stash.oid))

        if self._selected_kind == "stash" and self._selected_ref:
            selected(self._selected_ref)
        else:
            self.push_screen(
                ActionPicker(
                    "SELECT STASH",
                    [(s.ref, f"{s.ref} · {s.subject}") for s in self.snapshot_data.stashes],
                ),
                selected,
            )

    def network_action(self, operation: str) -> None:
        if not self.snapshot_data or not self.snapshot_data.remotes:
            self.notify(
                "No remote configured. Add one with git remote add, then refresh.",
                severity="warning",
            )
            return

        def run(remote: str | None = "") -> None:
            if remote is not None:
                self.run_mutation(Action(operation, remote=remote))

        if (
            operation == "push"
            and not self.snapshot_data.upstream
            and len(self.snapshot_data.remotes) > 1
        ):
            self.push_screen(
                ActionPicker(
                    "PUBLISH BRANCH TO",
                    [
                        (r.name, f"{r.name} · {r.provider} · {r.push_url or r.url}")
                        for r in self.snapshot_data.remotes
                    ],
                ),
                run,
            )
        else:
            run()

    def wip_action(self, operation: str) -> None:
        all_files = operation.endswith("-all")
        operation = operation.removesuffix("-all")
        if not all_files and not self._selected_file:
            self.notify("Select a file, or use Stage all / Unstage all.", severity="warning")
            return
        paths = () if all_files else (self._selected_file[0].path,)
        self.run_mutation(Action(operation, paths=paths))

    def commit_changes(self) -> None:
        message = self.query_one("#commit-message", TextArea).text.strip()
        if not message:
            self.notify("Write a commit message first.", severity="warning")
            return
        self.run_mutation(Action("commit", message=message))

    def run_mutation(self, action: Action) -> None:
        if self.busy or not self.repo or len(self.screen_stack) > 1:
            return
        self._detail_token += 1
        self._diff_token += 1
        self.set_busy(True, f"Running {action.operation}…")
        self.run_worker(self.mutate(action), group="repository", exit_on_error=False)

    async def mutate(self, action: Action) -> None:
        try:
            async with self._serial:
                result = await asyncio.to_thread(self.repo.execute, action)
            self._last_error = ""
            self.status(result or f"{action.operation.title()} complete")
            # Textual toasts require markup strings, not Rich Text renderables.
            self.notify(escape(result or "Done"))
            if action.operation == "commit":
                self.query_one("#commit-message", TextArea).clear()
            if action.operation in {
                "delete-branch",
                "rename-branch",
                "delete-tag",
                "stash-pop",
                "stash-drop",
            }:
                self._clear_reference_selection()
            # Index changes invalidate the selected staged/unstaged version.
            if action.operation in {
                "stage",
                "unstage",
                "commit",
                "discard",
                "stash",
                "stash-pop",
                "stash-apply",
            }:
                self._selected_file = None
        except Exception as error:
            self.show_error(str(error))
        finally:
            # A failed merge/rebase may have changed HEAD, index, and operation state.
            await self.load_snapshot(quiet=True)

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = busy
        if message:
            self.status(message)
        self.update_controls()

    def update_controls(self) -> None:
        if not self.is_mounted:
            return
        for button in self.query(Button):
            button.disabled = self.busy and button.id not in {"help", "search-next", "graph-style"}
        if self.snapshot_data:
            files = self.snapshot_data.files
            selected = self._selected_file
            self.query_one("#stage", Button).disabled = self.busy or not selected or selected[1]
            self.query_one("#unstage", Button).disabled = (
                self.busy or not selected or not selected[1]
            )
            self.query_one("#discard", Button).disabled = (
                self.busy
                or not selected
                or selected[1]
                or selected[0].index == "?"
                or selected[0].conflicted
            )
            self.query_one("#stage-all", Button).disabled = self.busy or not any(
                f.unstaged for f in files
            )
            self.query_one("#unstage-all", Button).disabled = self.busy or not any(
                f.staged for f in files
            )
            self.query_one("#commit", Button).disabled = (
                self.busy or not any(f.staged for f in files) or bool(self.snapshot_data.conflicts)
            )
            self.query_one("#continue", Button).disabled = self.busy or bool(
                self.snapshot_data.conflicts
            )
            for ident in ("fetch", "pull", "push"):
                self.query_one(f"#{ident}", Button).disabled = (
                    self.busy or not self.snapshot_data.remotes
                )
            self.query_one("#pop", Button).disabled = self.busy or not self.snapshot_data.stashes
        self.query_one("#search-next", Button).disabled = not self._search_matches

    def status(self, message: str) -> None:
        self.query_one("#status-line", Static).update(
            Text(message.splitlines()[0] if message else "Ready")
        )

    def show_error(self, message: str) -> None:
        self.status("Git: " + message)
        if message != self._last_error:
            self.notify(escape(message), severity="error", timeout=12)
        self._last_error = message

    def action_open_repository(self) -> None:
        if not self.busy and len(self.screen_stack) == 1:
            self.push_screen(
                PromptDialog("OPEN LOCAL REPOSITORY", "/path/to/repository"), self.open_repository
            )

    def open_repository(self, path: str | None) -> None:
        if not path or self.busy or len(self.screen_stack) > 1:
            return
        self.set_busy(True, "Opening repository…")

        async def open_path() -> None:
            try:
                async with self._serial:
                    repo = await asyncio.to_thread(Repository, Path(path).expanduser())
                    snapshot = await asyncio.to_thread(repo.snapshot)
                self.repo, self.path = repo, repo.root
                self._clear_reference_selection()
                self._selected_commit = self._selected_file = None
                self._history_key = "wip"
                self._graph_geometry = ()
                self.limit = 300
                self.query_one("#commit-message", TextArea).clear()
                self.query_one("#graph-filter", Input).value = ""
                await self.apply_snapshot(snapshot)
                self.jump_to("wip")
                self.status(f"Opened {repo.root}")
            except Exception as error:
                self.show_error(str(error))
            finally:
                self.set_busy(False)

        self.run_worker(open_path(), group="repository", exit_on_error=False)

    def action_refresh(self) -> None:
        if len(self.screen_stack) == 1:
            self.request_refresh()

    def action_new_branch(self) -> None:
        if not self.busy and len(self.screen_stack) == 1:
            self.handle_picked_action("create-branch")

    def action_focus_filter(self) -> None:
        self.query_one("#graph-filter", Input).focus()

    def action_clear_filter(self) -> None:
        if len(self.screen_stack) > 1:
            return
        self.query_one("#graph-filter", Input).value = ""
        self._clear_reference_selection()
        self.update_search(jump=False)
        self.query_one("#graph-table").focus()

    def action_toggle_refs(self) -> None:
        self.query_one("#refs-panel").toggle_class("collapsed")
        self.call_after_refresh(self.repaint_history)

    def action_toggle_inspector(self) -> None:
        self.query_one("#inspector-panel").toggle_class("collapsed")
        self.call_after_refresh(self.repaint_history)

    def action_toggle_graph_style(self) -> None:
        if len(self.screen_stack) > 1:
            return
        self.graph_style = "compact" if self.graph_style == "smooth" else "smooth"
        self.query_one("#graph-style", Button).label = Text(f"{self.graph_style.title()} [g]")
        self.repaint_history()
        self.status(f"{self.graph_style.title()} graph · g to switch")

    def action_help(self) -> None:
        if len(self.screen_stack) == 1:
            self.push_screen(HelpDialog())

    def action_next_row(self) -> None:
        if isinstance(self.focused, (DataTable, CommitFileTree)):
            self.focused.action_cursor_down()

    def action_previous_row(self) -> None:
        if isinstance(self.focused, (DataTable, CommitFileTree)):
            self.focused.action_cursor_up()
