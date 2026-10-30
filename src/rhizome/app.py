"""A local-first Git workspace. Git calls are serialized and never block the UI."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Literal, Self

from rich.markup import escape
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Offset
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

from .activity import ActivityDialog
from .clipboard import copy_text
from .commit_files import CommitFileTree
from .context_actions import ContextTarget, context_actions, remote_link
from .dates import format_commit_date
from .dialogs import ActionPicker, BranchContextMenu, HelpDialog, MessageDialog, PromptDialog
from .git import GitError, Repository
from .graph import GraphRow, GraphStyle, render_history
from .investigation import InvestigationService
from .investigation_dialogs import ComparisonDialog, FileInvestigationDialog, SearchCriteriaDialog
from .models import (
    Action,
    Commit,
    FileChange,
    HistoryLine,
    HistoryOrder,
    HistoryPage,
    InvestigationScope,
    Ref,
    ScopeState,
    SearchPage,
    SearchQuery,
    Snapshot,
)
from .operations import OperationActivity, OperationCoordinator
from .pane_splitter import PaneGroup, PaneSplitter
from .patches import PatchDocument, PatchService
from .persistence import (
    DEFAULT_SETTINGS,
    PersistenceError,
    StateValidationError,
    WorkspaceStateStore,
)
from .precision_diff import PrecisionDiff
from .pull import ManagedPull, PullPlan
from .pull_controls import PullModeMenu, PullReviewDialog, pull_mode_label
from .pull_request_dialog import PullRequestDetailsDialog, PullRequestDialog
from .pull_requests import (
    PullRequestError,
    PullRequestListing,
    PullRequestPlan,
    hosted_repository,
)
from .recovery import RecoveryError, RecoveryJournal
from .ref_badges import RefBadges, build_ref_badges
from .resizable_table import ResizableDataTable
from .settings import COMMANDS, CURATED_SETTINGS, validate_preferences
from .sidebar import ReferenceSidebar, SidebarSelection
from .split_diff import SplitDiff, UnifiedRequested
from .theme import (
    LIGHT,
    MUTED,
    TEAL,
    TEXT,
    TEXTUAL_THEMES,
    Palette,
    palette_for,
    resolve_theme,
    theme_text,
)
from .workspace_controls import DiagnosticsDialog, WorkspaceControlRequest, WorkspaceControlsDialog

_CORE_SETTINGS = CURATED_SETTINGS


@dataclass(frozen=True)
class _HistoryContext:
    scope: InvestigationScope
    page: HistoryPage
    snapshot: Snapshot
    state: ScopeState
    order: HistoryOrder
    roots: tuple[str, ...]
    cursors: tuple[str | None, ...]
    position: int
    selection: str
    search: str
    search_query: SearchQuery | None
    search_page: SearchPage | None
    scroll: Offset


def patch_text(patch: str, *, palette: Palette = LIGHT) -> Text:
    """Render a literal file diff without Git's redundant transport headers.

    Only hide headers before a hunk: removed/added content can itself start
    with ``---`` / ``+++``. Keep binary, rename, and mode-change summaries.
    The underlying repository patch is unchanged.
    """
    text = Text()
    in_header = False
    for line in patch.splitlines(keepends=True):
        if line.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
            in_header = True
            continue
        if in_header and line.startswith(("index ", "--- ", "+++ ")):
            continue
        style = None
        if line.startswith("@@"):
            in_header = False
            style = "#7049a8"
        elif line.startswith("+"):
            style = "#28733a"
        elif line.startswith("-"):
            style = "#b13a48"
        text.append(line, style)
    return (
        theme_text(text, palette)
        if text
        else Text("No textual changes (mode-only change or empty file).")
    )


class HistoryTable(ResizableDataTable):
    """History clicks inspect; only explicit commands request Git mutations."""

    class ViewportChanged(Message):
        """The graph's available width changed, including after a pane drag."""

    class ContextRequested(Message):
        def __init__(
            self, key: str, revision: int, screen_x: int, screen_y: int, ref_name: str = ""
        ) -> None:
            self.key, self.revision = key, revision
            self.screen_x, self.screen_y = screen_x, screen_y
            self.ref_name = ref_name
            super().__init__()

    class CommitActivated(Message):
        def __init__(self, key: str, revision: int) -> None:
            self.key, self.revision = key, revision
            super().__init__()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.revision = 0
        self._click_latch: str | None = None
        self._viewport_width = 0

    def on_resize(self, event: events.Resize) -> None:
        if event.size.width != self._viewport_width:
            self._viewport_width = event.size.width
            self.post_message(self.ViewportChanged())

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if "resize_column" in event.style.meta:
            self._click_latch = None

    def clear(self, columns: bool = False) -> Self:
        self.revision += 1
        self._click_latch = None
        return super().clear(columns=columns)

    async def _on_click(self, event: events.Click) -> None:
        if self.consume_resize_click(event):
            self._click_latch = None
            return
        # DataTable selects on every Click but discards Click.chain. Keep
        # activation associated with the same row/revision; both single and
        # double clicks are inspection, never checkout.
        event.prevent_default()
        event.stop()
        row_index = event.style.meta.get("row", -1)
        key = (
            str(self.ordered_rows[row_index].key.value) if 0 <= row_index < self.row_count else None
        )
        if event.button != 1:
            self._click_latch = None
            if event.button == 3 and key is not None:
                self.post_message(
                    self.ContextRequested(
                        key,
                        self.revision,
                        event.screen_x,
                        event.screen_y,
                        event.style.meta.get("context_ref", ""),
                    )
                )
            return
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


class RhizomeApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "Rhizome"
    ENABLE_COMMAND_PALETTE = False
    LOCAL_REFRESH_INTERVAL = 2
    REMOTE_REFRESH_INTERVAL = 60
    BINDINGS = [
        Binding(
            command.keys,
            command.action,
            command.description,
            priority=command.priority,
            show=command.show,
            id=f"rhizome.{command.id}",
        )
        for command in COMMANDS
    ]

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        for theme in TEXTUAL_THEMES:
            self.register_theme(theme)
        self.theme = "rhizome-light"
        self._palette = LIGHT
        self._appearance_reason = "light fallback"
        self.path = Path(path)
        self.repo: Repository | None = None
        self._operations: OperationCoordinator | None = None
        self._managed_pull: ManagedPull | None = None
        self._pull_attention = ""
        self._pull_attention_started: datetime | None = None
        self._pending_operation: tuple[str, datetime] | None = None
        self.snapshot_data: Snapshot | None = None
        self._investigation: InvestigationService | None = None
        self._history_scope: InvestigationScope | None = None
        self._history_page: HistoryPage | None = None
        self._scope_snapshot: Snapshot | None = None
        self._scope_state = ScopeState(True, True)
        self.history_order: HistoryOrder = "topology"
        self._focus_roots: tuple[str, ...] = ()
        self._history_trail: list[str | None] = [None]
        self._history_position = 0
        self._history_loading = False
        self._history_token = 0
        self._focus_return: _HistoryContext | None = None
        self.busy = False
        self._serial = asyncio.Lock()
        self._generation = 0
        self._mutation_epoch = 0
        self._detail_token = 0
        self._diff_token = 0
        self._diff_tokens = {"worktree": 0, "commit": 0}
        self._commits: dict[str, Commit] = {}
        self._file_rows: dict[str, tuple[FileChange, bool]] = {}
        self._selected_commit: Commit | None = None
        self._selected_file: tuple[FileChange, bool] | None = None
        # The graph remains mounted underneath this temporary center workspace.
        # A diff exists only after an explicit file activation.
        self._active_diff: tuple[FileChange, bool, str] | None = None
        self._patch_document: PatchDocument | None = None
        self._display_patch = ""
        self._prefer_unified_diff = False
        self._patch_stale = False
        self._diff_context = 3
        self._sidebar_selection: SidebarSelection | None = None
        self._reveal_ref_key: str | None = None
        self._ref_badges: dict[str, RefBadges] = {}
        self._rebuilding = False
        self._history_key = "wip"
        self.graph_style: GraphStyle = "smooth"
        self._graph_rows: tuple[GraphRow, ...] = ()
        self._graph_cache_key: tuple[tuple[HistoryLine, ...], GraphStyle, bool, str] | None = None
        self._graph_geometry: tuple[tuple[str, int], ...] = ()
        self._search_matches: list[str] = []
        self._search_query: SearchQuery | None = None
        self._search_page: SearchPage | None = None
        self._search_token = 0
        self._search_running = False
        self._search_note = ""
        self._last_error = ""
        self._status_message = "Ready"
        self._refresh_error = ""
        self._refreshing = False
        self._remote_refreshing: Repository | None = None
        self._remote_reading: Repository | None = None
        self._terminal_active = True
        self._next_remote_refresh = 0.0
        self._last_remote_refresh = float("-inf")
        self._state_store: WorkspaceStateStore | None = None
        self._workspace_key = ""
        self._workspace_state: dict = {}
        self._saved_draft = ""
        self._commit_template_seed: str | None = None
        self._restoring_draft = False
        self._state_error = ""
        self._settings = dict(DEFAULT_SETTINGS)
        self._global_settings = dict(DEFAULT_SETTINGS)
        self._settings_error = ""
        self._workspace_identity_key = ""
        self._state_excluded = False
        self._diagnostic_error = ""
        self._pending_view: dict = {}
        self._restoring_view = False
        self._view_error = ""
        self._preferences_error = ""
        self._task_view = "primary"

    @property
    def _selected_ref(self) -> str:
        return self._sidebar_selection.target if self._sidebar_selection else ""

    @property
    def _selected_kind(self) -> str:
        return self._sidebar_selection.kind if self._sidebar_selection else ""

    def compose(self) -> ComposeResult:
        with Horizontal(id="app-header"):
            yield Static("RHIZOME  /  Opening repository…", id="repo-header")
            with Horizontal(id="toolbar"):
                for ident, label in [
                    ("open", "Open"),
                    # The primary segment deliberately retains #fetch: it is the
                    # established toolbar target while its saved behavior is now a
                    # pull/fetch choice. The chevron never starts Git work.
                    ("fetch", "Pull"),
                    ("pull-menu", "▾"),
                    ("pull", "↓ Pull"),
                    ("push", "↑ Push"),
                    ("branch", "⑂ Branch"),
                    ("stash", "Stash"),
                    ("pop", "Pop"),
                    ("actions", "Actions ▾"),
                    ("refresh", "↻"),
                    ("help", "?"),
                ]:
                    yield Button(label, id=ident, classes="tool")
        with Horizontal(id="workspace-nav"):
            yield Button("History", id="show-primary", classes="nav-active")
            yield Button("Changes [F2]", id="show-files")
            yield Button("Commit [F3]", id="show-composer")
            yield Button("Refs", id="show-refs")
            yield Static(id="nav-spacer")
            yield Button("Activity", id="activity")
            yield Button("Settings [F4]", id="workspace-settings")
        with Horizontal(id="operation-banner"):
            yield Static(id="operation-text")
            yield Button("Continue", id="continue", classes="operation-button")
            yield Button("Abort", id="abort", classes="operation-button")
        with PaneGroup(id="workspace", classes="horizontal"):
            with Vertical(id="refs-panel"):
                yield Static("REPOSITORY", id="refs-heading")
                yield ReferenceSidebar(id="reference-sidebar")
            yield PaneSplitter(
                "#refs-panel", "#history-panel", minimum=15, flexible_minimum=34, id="refs-splitter"
            )
            with Vertical(id="history-panel"):
                # Keep this mounted while a diff is open so graph position, widths,
                # style, search, and selected commit survive the temporary view.
                with Vertical(id="graph-workspace"):
                    with Horizontal(id="history-titlebar"):
                        yield Static("History", id="history-heading")
                        yield Static(id="history-count")
                    with Horizontal(id="search-bar"):
                        yield Input(
                            placeholder="Search commits, authors, refs or SHA…", id="graph-filter"
                        )
                        yield Button("Next", id="search-next", classes="tool")
                        yield Button("Filters", id="search-criteria")
                        yield Button(
                            Text("Smooth [g]"),
                            id="graph-style",
                            tooltip="Switch Smooth / Compact graph (g). Smooth uses rounded bends; "
                            "broken horizontal lines pass behind vertical lanes without joining them.",
                        )
                    with Horizontal(id="search-controls"):
                        yield Button("Search more", id="search-more")
                        yield Button("Stop search", id="search-stop")
                        yield Button("Widen to all refs", id="search-widen")
                    yield Static("Unified history · local + remote branches", id="history-caption")
                    yield HistoryTable(
                        id="graph-table",
                        cursor_type="row",
                        zebra_stripes=False,
                        cursor_foreground_priority="renderable",
                    )
                    with Horizontal(id="history-navigation"):
                        yield Button("Topology", id="graph-order")
                        yield Button("Focus", id="focus-history")
                        yield Button("Exit focus", id="exit-focus")
                        yield Button("Newer", id="history-newer")
                        yield Button("Older 300", id="load-more")
                with Vertical(id="diff-workspace"):
                    with Horizontal(id="diff-toolbar"):
                        yield Button("← Back to graph", id="back-to-graph")
                        yield Static(id="center-diff-title")
                    with VerticalScroll(id="center-diff-scroll"):
                        yield Static("", id="center-diff")
                    yield PrecisionDiff(id="precision-diff")
                    yield SplitDiff()
            yield PaneSplitter(
                "#inspector-panel",
                "#history-panel",
                grow_direction=-1,
                minimum=25,
                flexible_minimum=34,
                id="inspector-splitter",
            )
            with Vertical(id="inspector-panel"):
                with TabbedContent(id="inspector-tabs"):
                    with TabPane("Commit", id="details-tab"):
                        yield Static("Select a commit to inspect its changes.", id="commit-detail")
                        with Vertical(id="commit-changes"):
                            yield CommitFileTree(id="commit-file-tree")
                    with TabPane("Working directory", id="wip-tab"):
                        with Horizontal(classes="wip-actions"):
                            yield Button("Stage", id="stage")
                            yield Button("Unstage", id="unstage")
                            yield Button("Discard", id="discard", variant="error")
                        with Horizontal(classes="wip-actions"):
                            yield Button("Stage all", id="stage-all")
                            yield Button("Unstage all", id="unstage-all")
                        with Vertical(id="wip-changes"):
                            yield ResizableDataTable(id="wip-table", cursor_type="row")
                            yield Static("Working directory is clean.", id="wip-empty")
                        yield Static("No staged changes", id="staged-summary")
                        yield Label(
                            "COMMIT MESSAGE · summary, then optional body", classes="composer-label"
                        )
                        yield TextArea(id="commit-message", theme="github_light")
                        yield Static("Summary, blank line, optional body", id="composer-guidance")
                        yield Static("Draft is empty", id="draft-status")
                        yield Button("Commit", id="commit", variant="primary")
        with Horizontal(id="status-bar"):
            yield Static("Ready", id="status-line")
            yield Static("Ctrl+P actions   / search   ? help", id="key-hint")

    def on_mount(self) -> None:
        try:
            self._state_store = WorkspaceStateStore()
            self._settings = self._state_store.load("global")
        except PersistenceError as error:
            # Corrupt settings must not silently fall back to network activity.
            self._settings["offline"] = True
            self._settings["remote_refresh_enabled"] = False
            self._settings_error = str(error)
            if self._state_store is None:
                self._draft_error(str(error))
            else:
                self.notify(escape("Settings: " + str(error)), severity="error", timeout=12)
        self._global_settings = dict(self._settings)
        self._apply_core_settings()
        self.query_one("#operation-banner").display = False
        self.query_one("#load-more").display = False
        self.query_one("#search-controls").display = False
        self.query_one("#diff-workspace").display = False
        self.query_one(PrecisionDiff).display = False
        wip_table = self.query_one("#wip-table", ResizableDataTable)
        wip_table.add_column("Area", key="area")
        wip_table.add_column("File", key="file")
        self._configure_history_columns()
        self.query_one("#history-heading", Static).tooltip = Text(
            "⑂ Local branch / HEAD · ▤ Linked worktree · ↗ Remote tracking ref · ◇ Tag\n"
            "Markers share a commit when branch, worktree, and remote tips agree.\n"
            "╴│╶ crossing, not a join · ↑ parent shown above\n"
            f"Remote refresh interval: {self.REMOTE_REFRESH_INTERVAL}s; settings can pause network access."
        )
        self.set_interval(self.LOCAL_REFRESH_INTERVAL, self.poll_repository)
        self.set_interval(1.0, self._save_navigation_context)
        self.request_refresh()
        self.query_one("#graph-table").focus()

    @staticmethod
    def _state_identity(repo: Repository) -> str:
        # A recreated repository at the same path is not the old workspace.
        # Linked worktrees share objects, but never share a commit draft.
        git_dir = repo._git_path("HEAD").parent.resolve()
        identity = git_dir.stat()
        raw = f"{repo.root}\0{git_dir}\0{identity.st_dev}\0{identity.st_ino}"
        return hashlib.sha256(raw.encode("utf-8", "surrogateescape")).hexdigest()

    async def _restore_workspace_state(self, repo: Repository) -> None:
        editor = self.query_one("#commit-message", TextArea)
        # Do not overwrite typing that arrived while state/template I/O was in
        # flight. Browsing stays available; the composer unlocks after restoration.
        editor.read_only = True
        self._restoring_draft = True
        self._workspace_key = ""
        self._workspace_identity_key = ""
        self._workspace_state = {}
        self._saved_draft = ""
        self._commit_template_seed = None
        self._pending_view = {}
        self._state_excluded = False
        self._settings = dict(self._global_settings)
        self._reset_workspace_view()
        editor.clear()
        try:
            # Keep destructive actions unavailable until the workspace has a
            # usable recovery review; ordinary commits keep their existing guard.
            self._operations = await asyncio.to_thread(
                OperationCoordinator,
                repo,
                protected_branches=tuple(self._settings["protected_branches"]),
            )
            if self._state_store is None:
                return
            key = await asyncio.to_thread(self._state_identity, repo)
            self._workspace_identity_key = key
            self._state_excluded = key in self._global_settings["excluded_workspaces"]
            state = (
                {} if self._state_excluded else await asyncio.to_thread(self._state_store.load, key)
            )
            self._preferences_error = self._view_error = ""
            try:
                overrides = self._validated_core_settings(state.get("preferences", {}))
                self._settings.update(overrides)
            except StateValidationError as error:
                self._preferences_error = str(error)
                self._settings["offline"] = True
                self.notify(escape(str(error)), severity="warning")
            self._apply_core_settings()
            self._configure_pull_coordinator(repo)
            try:
                self._pending_view = (
                    self._validated_view(state["view"]) if state.get("view") else {}
                )
            except StateValidationError as error:
                self._view_error = str(error)
                self._pending_view = {}
                self.notify(escape(str(error)), severity="warning")
            # Legacy history_limit is accepted on read but never restores a total ceiling.
            self._history_key = self._pending_view.get("history_key", "wip")
            draft = state.get("draft", "")
            if not isinstance(draft, str):
                raise StateValidationError(
                    "The saved commit draft is invalid; it was not replaced."
                )
            editor.load_text(draft)
            if editor.text != draft:
                editor.clear()
                raise StateValidationError(
                    "The saved draft's line endings cannot be preserved by the composer; "
                    "the original remains on disk."
                )
            self._workspace_state = state
            self._saved_draft = draft
            self._workspace_key = key
            self._state_error = ""
            self.query_one("#draft-status", Static).update(
                "Session only · saving excluded"
                if self._state_excluded
                else ("Draft restored locally" if draft else "Draft is empty")
            )
            self._record_diagnostic("workspace.opened", {"offline": self._settings["offline"]})
            # Any nonempty saved text is authoritative, including whitespace or
            # comments. Never even read a configured template over a user draft.
            if not draft:
                await self._seed_commit_template(repo, editor)
        except (PersistenceError, GitError, OSError) as error:
            self._draft_error(str(error))
        finally:
            self._restoring_draft = False
            editor.read_only = False
            self._update_composer_guidance()

    def _configure_pull_coordinator(self, repo: Repository) -> None:
        """Give guarded pull its own recovery storage, without enabling discard.

        The coordinator is rebuilt only after exclusion and workspace identity are
        known. Pending reviews cannot exist while workspace settings are open; a
        paused integration is rediscovered from the durable pull journal.
        """
        journal: RecoveryJournal | None = None
        if (
            not self._state_excluded
            and self._state_store is not None
            and self._workspace_identity_key
        ):
            try:
                journal = RecoveryJournal(
                    self._state_store.root / "pull-recovery" / self._workspace_identity_key
                )
            except (RecoveryError, OSError) as error:
                self._record_diagnostic(
                    "pull.recovery_unavailable", {"error": type(error).__name__}
                )
                self.status("Pull recovery is unavailable; divergent pull is disabled.")
        self._operations = OperationCoordinator(
            repo,
            pull_journal=journal,
            protected_branches=tuple(self._settings["protected_branches"]),
        )

    async def _seed_commit_template(self, repo: Repository, editor: TextArea) -> None:
        try:
            template = await asyncio.to_thread(repo._read_commit_template)
            if template is None:
                return
            previous = editor.text
            editor.load_text(template)
            if editor.text != template:
                # TextArea normalizes mixed line endings. Do not present altered
                # template text or persist it as though it were the user's draft.
                editor.load_text(previous)
                raise GitError(
                    "The commit template's line endings cannot be preserved by the composer."
                )
            self._commit_template_seed = template
            # Suppress the initial Changed event without saving the seed. An
            # untouched template must not become an authoritative restored draft.
            self._saved_draft = template
            self.query_one("#draft-status", Static).update(
                "Template loaded · edit before committing"
            )
        except (GitError, OSError) as error:
            message = "Template not loaded: " + str(error)
            self.query_one("#draft-status", Static).update(Text(message))
            self.notify(
                escape(message + " Write your own message or fix the template and reopen."),
                severity="warning",
                timeout=12,
            )

    def _draft_error(self, message: str) -> None:
        self.screen_stack[0].query_one("#draft-status", Static).update(
            Text("Draft not saved: " + message)
        )
        if message != self._state_error:
            self.notify(escape("Local state: " + message), severity="error", timeout=12)
        self._state_error = message

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "commit-message":
            return
        self._update_composer_guidance()
        if not self._workspace_key or self._restoring_draft:
            return
        draft = event.text_area.text
        if draft != self._saved_draft:
            self._save_commit_draft(draft)

    def _save_commit_draft(self, draft: str) -> bool:
        try:
            state = {**self._workspace_state, "draft": draft}
            if not self._state_excluded:
                if self._state_store is None or not self._workspace_key:
                    raise StateValidationError("Workspace storage is unavailable.")
                self._state_store.save(self._workspace_key, state, expected=self._workspace_state)
            self._workspace_state = state
            self._saved_draft = draft
            self._state_error = ""
            self.screen_stack[0].query_one("#draft-status", Static).update(
                "Session only · saving excluded"
                if self._state_excluded
                else ("Draft saved locally" if draft else "Draft cleared")
            )
            return True
        except PersistenceError as error:
            self._draft_error(str(error))
            return False

    def _update_composer_guidance(self) -> None:
        workspace = self.screen_stack[0]
        text = workspace.query_one("#commit-message", TextArea).text
        lines = text.splitlines()
        summary = lines[0] if lines else ""
        hint = f"First line: {len(summary)} characters"
        if len(summary) > 72:
            hint += " · consider a shorter summary (optional)"
        elif len(lines) > 1 and lines[1].strip():
            hint += " · a blank line can separate the body"
        else:
            hint += " · blank line, then optional body"
        workspace.query_one("#composer-guidance", Static).update(Text(hint))

    @staticmethod
    def _validated_core_settings(value: object) -> dict:
        if not isinstance(value, dict) or value.keys() - set(_CORE_SETTINGS):
            raise StateValidationError(
                "Saved workspace preferences are invalid; reset them in Workspace."
            )
        try:
            return validate_preferences(value)
        except ValueError as error:
            raise StateValidationError(str(error)) from error

    @staticmethod
    def _validated_view(value: object) -> dict:
        if not isinstance(value, dict):
            raise StateValidationError("Saved navigation is invalid; reset the view in Workspace.")
        view = {}
        for key in ("history_key", "graph_filter", "ref_filter"):
            item = value.get(key, "")
            if not isinstance(item, str) or len(item) > 1024:
                raise StateValidationError(
                    "Saved navigation is invalid; reset the view in Workspace."
                )
            view[key] = item
        for key, allowed, default in (
            ("task", {"primary", "files", "composer", "refs"}, "primary"),
            ("tab", {"wip-tab", "details-tab"}, "wip-tab"),
        ):
            view[key] = value.get(key, default)
            if not isinstance(view[key], str) or view[key] not in allowed:
                raise StateValidationError(
                    "Saved navigation is invalid; reset the view in Workspace."
                )
        for key in ("refs_collapsed", "inspector_collapsed"):
            view[key] = value.get(key, False)
            if type(view[key]) is not bool:
                raise StateValidationError(
                    "Saved navigation is invalid; reset the view in Workspace."
                )
        for key in ("refs_width", "inspector_width", "history_limit"):
            item = value.get(key, 300 if key == "history_limit" else None)
            if (key == "history_limit" and item is None) or (
                item is not None and (type(item) is not int or not 1 <= item <= 5000)
            ):
                raise StateValidationError(
                    "Saved navigation is invalid; reset the view in Workspace."
                )
            view[key] = item
        return view

    def _apply_core_settings(self) -> None:
        # Global offline is a master stop; a repository cannot opt out of it.
        self._settings["offline"] = self._global_settings["offline"] or self._settings["offline"]
        if self._operations is not None and self._operations.repository is self.repo:
            # Reloading branch protection must affect the next pull/Continue
            # without replacing the coordinator that owns a paused session.
            self._operations.protected_branches = tuple(self._settings["protected_branches"])
        self.REMOTE_REFRESH_INTERVAL = self._settings["remote_refresh_interval"]
        self.graph_style = self._settings["graph_style"]
        self.set_keymap(
            {f"rhizome.{key}": value for key, value in self._settings["keymap"].items()}
        )
        self.theme, self._appearance_reason = resolve_theme(self._settings["theme"], os.environ)
        self._palette = palette_for(self.theme)
        workspace = self.screen_stack[0]
        workspace.refresh_bindings()
        for ident, label, command_id in (
            ("show-files", "Changes", "show_files"),
            ("show-composer", "Commit", "show_composer"),
            ("workspace-settings", "Settings", "workspace_settings"),
        ):
            shortcut = self._shortcut(command_id)
            workspace.query_one(f"#{ident}", Button).label = (
                f"{label} [{shortcut.upper()}]" if len(shortcut) <= 5 else label
            )
        plain = self._settings["plain_glyphs"]
        workspace.query_one(ReferenceSidebar).set_plain_glyphs(plain)
        workspace.query_one(ReferenceSidebar).set_palette(self._palette)
        workspace.query_one(CommitFileTree).set_plain_glyphs(plain)
        workspace.query_one(CommitFileTree).set_palette(self._palette)
        workspace.query_one("#commit-message", TextArea).theme = (
            "monokai" if self.current_theme.dark else "github_light"
        )
        workspace.query_one(PrecisionDiff).set_plain_glyphs(plain)
        workspace.query_one(PrecisionDiff).set_palette(self._palette)
        workspace.query_one("#graph-style", Button).label = Text(
            f"{self.graph_style.title()} [{self._shortcut('toggle_graph_style')}]"
        )
        workspace.query_one("#key-hint", Static).update(
            Text(
                f"{self._shortcut('command_picker')} actions  ·  {self._shortcut('focus_filter')} search  ·  {self._shortcut('help')} help"
            )
        )
        for ident, normal, fallback in (
            ("pull", "↓ Pull", "Pull"),
            ("push", "↑ Push", "Push"),
            ("branch", "⑂ Branch", "Branch"),
            ("refresh", "↻", "Refresh"),
            ("actions", "Actions ▾", "Actions"),
            ("pull-menu", "▾", ">"),
        ):
            button = workspace.query_one(f"#{ident}", Button)
            button.label = fallback if plain else normal
            # Textual 1.0 label changes repaint but don't invalidate auto width.
            button.refresh(layout=True)
        self._update_pull_button()
        self._graph_cache_key = None
        self._next_remote_refresh = 0.0
        if self._active_diff:
            self._apply_diff_presentation()

    def _shortcut(self, command_id: str) -> str:
        default = next(command.keys for command in COMMANDS if command.id == command_id)
        return (
            self._settings["keymap"]
            .get(command_id, default)
            .split(",")[0]
            .replace("question_mark", "?")
            .replace("slash", "/")
        )

    def _save_view_preference(self, name: str, value: str) -> None:
        """Respect an existing workspace override; otherwise update only the global key."""
        self._settings[name] = value
        try:
            overrides = self._validated_core_settings(self._workspace_state.get("preferences", {}))
            if name in overrides:
                self._persist_workspace(
                    {**self._workspace_state, "preferences": {**overrides, name: value}}
                )
            elif self._state_store and not self._state_excluded:
                current = self._state_store.load("global")
                settings = {**current, name: value}
                self._state_store.save("global", settings, expected=current)
                self._global_settings = settings
        except PersistenceError as error:
            self.notify(
                escape("Preference changed for this session but not saved: " + str(error)),
                severity="warning",
            )

    def _record_diagnostic(self, event: str, metadata: dict) -> None:
        if (
            self._state_store is None
            or self._state_excluded
            or not self._settings["diagnostics_enabled"]
        ):
            return
        try:
            self._state_store.record_diagnostic(event, metadata)
            self._diagnostic_error = ""
        except PersistenceError as error:
            # Diagnostics never turn a successful Git operation into a failure.
            message = str(error)
            if message != self._diagnostic_error:
                self.notify(
                    "Local diagnostics unavailable; Git work is unaffected.", severity="warning"
                )
            self._diagnostic_error = message

    def _save_navigation_context(self) -> None:
        if (
            not self.is_running
            or not self.is_mounted
            or not self.snapshot_data
            or not self._workspace_key
            or self._restoring_draft
            or self._restoring_view
            or self._pending_view
            or self.busy
            or len(self.screen_stack) > 1
            or self._state_error
            or self._view_error
        ):
            return
        workspace = self.screen_stack[0]
        view = {
            "history_key": self._history_key,
            "graph_filter": workspace.query_one("#graph-filter", Input).value[:1024],
            "ref_filter": workspace.query_one("#ref-filter", Input).value[:1024],
            "task": self._task_view,
            "tab": workspace.query_one("#inspector-tabs", TabbedContent).active,
            "refs_collapsed": workspace.query_one("#refs-panel").has_class("collapsed"),
            "inspector_collapsed": workspace.query_one("#inspector-panel").has_class("collapsed"),
            "refs_width": workspace.query_one("#refs-splitter", PaneSplitter).preferred_size,
            "inspector_width": workspace.query_one(
                "#inspector-splitter", PaneSplitter
            ).preferred_size,
        }
        if view == self._workspace_state.get("view"):
            return
        try:
            self._persist_workspace({**self._workspace_state, "view": view})
        except PersistenceError as error:
            self._draft_error(str(error))

    def _restore_navigation_context(self) -> None:
        if not self._pending_view or len(self.screen_stack) > 1:
            return
        view, self._pending_view = self._pending_view, {}
        self._restoring_view = True
        try:
            workspace = self.screen_stack[0]
            search = workspace.query_one("#graph-filter", Input)
            with search.prevent(Input.Changed):
                search.value = view.get("graph_filter", "")
            self.update_search(jump=False)
            workspace.query_one("#ref-filter", Input).value = view.get("ref_filter", "")
            workspace.query_one("#inspector-tabs", TabbedContent).active = view.get(
                "tab", "wip-tab"
            )
            for pane in ("refs", "inspector"):
                splitter = workspace.query_one(f"#{pane}-splitter", PaneSplitter)
                splitter.preferred_size = view.get(f"{pane}_width")
            task = view.get("task", "primary")
            if task != "primary":
                getattr(self, f"action_show_{task}")()
            else:
                self.action_show_primary()
            for pane in ("refs", "inspector"):
                collapsed = view.get(f"{pane}_collapsed", False)
                workspace.query_one(f"#{pane}-panel").set_class(collapsed, "collapsed")
                workspace.query_one(f"#{pane}-splitter").set_class(collapsed, "collapsed")
            self.call_after_refresh(self._fit_panes)
            saved_key = view.get("history_key")
            if saved_key and saved_key != "wip" and saved_key not in self._commits:
                self.notify("Saved history selection is no longer loaded; showing current context.")
        finally:
            self._restoring_view = False

    def _persist_workspace(self, state: dict) -> None:
        if not self._state_excluded:
            if self._state_store is None or not self._workspace_key:
                raise StateValidationError(
                    "Workspace storage is unavailable; use Forget saved state to reset it."
                )
            self._state_store.save(self._workspace_key, state, expected=self._workspace_state)
        self._workspace_state = state

    def action_workspace_settings(self) -> None:
        if self.busy or len(self.screen_stack) > 1:
            return
        self._save_navigation_context()
        self.push_screen(
            WorkspaceControlsDialog(
                global_settings=self._global_settings,
                overrides=self._workspace_state.get("preferences", {}),
                excluded=self._state_excluded,
                settings_path=str(self._state_store.settings_path) if self._state_store else "",
                state_error=self._state_error
                or self._settings_error
                or self._preferences_error
                or self._view_error,
            ),
            self._workspace_control,
        )

    def _workspace_control(self, request: WorkspaceControlRequest | None) -> None:
        if request is None:
            return
        if request.action == "diagnostics":
            self.action_diagnostics()
            return
        if request.action in {"settings", "reset-overrides", "reload-settings"}:
            self._apply_workspace_control(request)
            return
        descriptions = {
            "clear-drafts": "Clear this worktree's commit and PR drafts? Git files are unchanged.",
            "reset-view": "Reset this workspace's navigation and layout? Drafts are kept.",
            "forget-workspace": "Forget this worktree's saved drafts, view and overrides? Git files and pull recovery checkpoints are kept.",
            "toggle-exclusion": (
                "Resume saving this worktree's current session? Old saved state will not be restored."
                if self._state_excluded
                else "Stop saving this worktree and remove its saved drafts, view and overrides? Current text stays session-only. Existing pull recovery is kept; divergent pulls will be unavailable."
            ),
            "reset-global": "Reset global preferences to defaults? Exclusion choices are kept.",
        }
        if request.action not in descriptions:
            return
        self.push_screen(
            ActionPicker(
                descriptions[request.action],
                [("confirm", "Confirm"), ("cancel", "Keep current state")],
            ),
            lambda choice: self._apply_workspace_control(request) if choice == "confirm" else None,
        )

    def _apply_workspace_control(self, request: WorkspaceControlRequest) -> None:
        try:
            store = self._state_store
            if store is None:
                raise StateValidationError("Private state storage is unavailable.")
            action = request.action
            if action == "settings":
                values = self._validated_core_settings(request.settings)
                if request.scope == "global":
                    current = store.load("global")
                    settings = {**current, **values}
                    store.save("global", settings, expected=current)
                    self._global_settings = settings
                else:
                    self._persist_workspace({**self._workspace_state, "preferences": values})
            elif action == "reload-settings":
                self._global_settings = store.load("global")
            elif action == "reset-global":
                current = store.load("global")
                # Reset curated preferences, not branch protection or exclusion policies.
                settings = {
                    **current,
                    **{name: DEFAULT_SETTINGS[name] for name in (*_CORE_SETTINGS, "graph_style")},
                }
                store.save("global", settings, expected=current)
                self._global_settings = settings
            elif action == "reset-overrides":
                self._persist_workspace({**self._workspace_state, "preferences": {}})
            elif action == "toggle-exclusion":
                try:
                    managed = self._operations.managed_pull() if self._operations else None
                except GitError as error:
                    raise StateValidationError(
                        f"Workspace saving cannot change while pull ownership needs inspection: {error}"
                    ) from error
                if managed is not None:
                    raise StateValidationError(
                        "Finish or abort the paused guarded pull before changing workspace saving."
                    )
                key = self._workspace_identity_key
                if not key:
                    raise StateValidationError(
                        "Reopen the workspace before changing its saving policy."
                    )
                current = store.load("global")
                excluded = set(current["excluded_workspaces"])
                if self._state_excluded:
                    # Discard old retained bytes before lifting exclusion; never
                    # resurrect an earlier draft after an incomplete purge.
                    store.clear(key)
                    excluded.discard(key)
                else:
                    excluded.add(key)
                settings = {**current, "excluded_workspaces": sorted(excluded)}
                store.save("global", settings, expected=current)
                self._global_settings = settings
                self._state_excluded = key in excluded
                self._workspace_key = key
                if self.repo is not None:
                    self._configure_pull_coordinator(self.repo)
                if self._state_excluded:
                    try:
                        store.clear(key)
                    except PersistenceError as error:
                        raise StateValidationError(
                            "Saving is excluded, but old saved data could not be purged. Use Forget saved state to retry removal."
                        ) from error
                else:
                    store.save(key, self._workspace_state, expected={})
            elif action == "forget-workspace":
                store.clear(self._workspace_identity_key)
                self._workspace_key = self._workspace_identity_key
                self._workspace_state = {}
                self._clear_visible_drafts()
                self._reset_workspace_view()
            elif action == "clear-drafts":
                self._persist_workspace(
                    {**self._workspace_state, "draft": "", "pull_request_drafts": {}}
                )
                self._clear_visible_drafts()
            elif action == "reset-view":
                self._persist_workspace({**self._workspace_state, "view": {}})
                self._reset_workspace_view()
            self._settings = {
                **self._global_settings,
                **self._validated_core_settings(self._workspace_state.get("preferences", {})),
            }
            self._apply_core_settings()
            self._state_error = ""
            self._preferences_error = ""
            if action in {"reset-global", "reload-settings"} or (
                action == "settings" and request.scope == "global"
            ):
                self._settings_error = ""
            text = self.query_one("#commit-message", TextArea).text
            if text != self._saved_draft and text != self._commit_template_seed:
                if not self._save_commit_draft(text):
                    return
            self.query_one("#draft-status", Static).update(
                "Session only · saving excluded"
                if self._state_excluded
                else (
                    "Template loaded · edit before committing"
                    if text == self._commit_template_seed
                    else "Draft saved locally"
                    if text
                    else "Draft is empty"
                )
            )
            self._record_diagnostic(
                "workspace.preferences", {"operation": action, "status": "saved"}
            )
            if self.snapshot_data:
                self.run_worker(
                    self.apply_snapshot(self.snapshot_data), group="view", exit_on_error=False
                )
                desired_order: HistoryOrder = (
                    "author-time"
                    if self._settings["graph_order"] == "chronological"
                    else "topology"
                )
                if desired_order != self.history_order:
                    self._request_history_page(None, 0, order=desired_order, persist_order=False)
            self.status("Workspace preferences updated · running Git work is not cancelled")
        except PersistenceError as error:
            self._draft_error(str(error))

    def _clear_visible_drafts(self) -> None:
        self._saved_draft = ""
        self._commit_template_seed = None
        self.query_one("#commit-message", TextArea).clear()

    def _reset_workspace_view(self) -> None:
        self._view_error = ""
        self._pending_view = {}
        self._history_key = "wip"
        self.query_one("#graph-filter", Input).value = ""
        self.query_one("#ref-filter", Input).value = ""
        for pane in ("refs", "inspector"):
            splitter = self.query_one(f"#{pane}-splitter", PaneSplitter)
            splitter.preferred_size = None
            splitter.remove_class("collapsed")
            panel = self.query_one(f"#{pane}-panel")
            panel.remove_class("collapsed")
            panel.styles.width = None
        self.back_to_graph()
        self.action_show_primary()

    def action_diagnostics(self) -> None:
        if len(self.screen_stack) > 1:
            return
        store = self._state_store
        if store is None:
            self.notify("Local diagnostics storage is unavailable.", severity="warning")
            return
        try:
            all_records = store.diagnostics_preview()
            records = all_records[-100:]
            preview = store.diagnostics_export_preview(records)
            self._diagnostic_error = ""
            error = ""
            summary = f"Latest {len(records)} of {len(all_records)} retained records · export uses this captured preview"
        except PersistenceError as failure:
            records, preview = [], "[]"
            error, summary = (
                str(failure),
                "Diagnostics could not be read; explicit Clear can discard the damaged log.",
            )

        def complete(action: str | None) -> None:
            if action == "clear":
                self.push_screen(
                    ActionPicker(
                        "Clear all local diagnostics? Drafts and settings are kept.",
                        [("confirm", "Clear diagnostics"), ("cancel", "Keep diagnostics")],
                    ),
                    lambda choice: self._clear_diagnostics() if choice == "confirm" else None,
                )
            elif action == "export" and not error:
                default = (
                    store.root / "exports" / f"diagnostics-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
                )

                def export(path: str | None) -> None:
                    if not path:
                        return
                    try:
                        result = store.export_diagnostics(Path(path).expanduser(), records=records)
                        self.status(f"Exported the previewed diagnostics to {result}")
                    except PersistenceError as failure:
                        self.show_error(str(failure))

                self.push_screen(
                    PromptDialog(
                        "EXPORT PREVIEWED DIAGNOSTICS · new file only", "Destination", str(default)
                    ),
                    export,
                )

        self.push_screen(DiagnosticsDialog(preview=preview, summary=summary, error=error), complete)

    def _clear_diagnostics(self) -> None:
        try:
            if self._state_store is not None:
                self._state_store.clear_diagnostics()
                self._diagnostic_error = ""
                self.status("Local diagnostics cleared; drafts and preferences unchanged")
        except PersistenceError as error:
            self.show_error(str(error))

    def _pull_request_draft(self, plan: PullRequestPlan) -> tuple[str, tuple[str, str]]:
        # A draft follows branch intent, not a moving commit OID. Distinct hosted
        # repositories, source branches, and targets never share composer text.
        identity = "\0".join(
            (
                plan.repository.provider,
                plan.repository.web_url,
                plan.source_branch,
                plan.target_branch,
            )
        )
        key = hashlib.sha256(identity.encode("utf-8", "surrogateescape")).hexdigest()
        drafts = self._workspace_state.get("pull_request_drafts", {})
        if not isinstance(drafts, dict):
            raise StateValidationError("Saved PR drafts are invalid; they were not replaced.")
        value = drafts.get(key, {"title": "", "description": ""})
        if (
            not isinstance(value, dict)
            or set(value) != {"title", "description"}
            or not all(isinstance(text, str) for text in value.values())
        ):
            raise StateValidationError("The saved PR draft is invalid; it was not replaced.")
        return key, (value["title"], value["description"])

    def _save_pull_request_draft(
        self,
        workspace: str,
        key: str,
        title: str,
        description: str,
    ) -> None:
        if not workspace or workspace != self._workspace_key or self._state_store is None:
            raise StateValidationError("This workspace's draft storage is unavailable.")
        drafts = dict(self._workspace_state.get("pull_request_drafts", {}))
        value = {"title": title, "description": description}
        if drafts.get(key, {"title": "", "description": ""}) == value:
            return
        if title or description:
            drafts[key] = value
        else:
            drafts.pop(key, None)
        state = {**self._workspace_state, "pull_request_drafts": drafts}
        if not self._state_excluded:
            self._state_store.save(workspace, state, expected=self._workspace_state)
        self._workspace_state = state

    def _configure_history_columns(self) -> None:
        table = self.query_one("#graph-table", HistoryTable)
        table.clear(columns=True)
        # The story comes first. Reveal secondary metadata only when it leaves
        # a useful message column; full metadata is always in the inspector.
        # Explicit user widths still win and may intentionally require scrolling.
        graph_minimum = max(3, max((row.width for row in self._graph_rows), default=2) + 1)
        table.column_minimums["graph"] = graph_minimum
        table.column_minimums["subject"] = 24
        # Reserve the rail even before rows make it visible; otherwise a full
        # history creates a horizontal scrollbar and clips the last column.
        available = max(34, table.size.width - table.styles.scrollbar_size_vertical)
        width = table.preferred_column_width
        graph_width = width("graph", graph_minimum)
        metadata = []
        if available >= 58:
            metadata.append(("refs", "Branches", width("refs", min(26, available // 5))))
        if available >= 116:
            metadata.append(("author", "Author", width("author", 14)))
        if available >= 90:
            metadata.append(("date", "Date", width("date", 16)))
        if available >= 145:
            metadata.append(("sha", "SHA", width("sha", 8)))
        padding = 2 * table.cell_padding
        # Dense merge graphs may need more lanes than the normal width budget.
        # Drop secondary columns rather than obscure the subject behind them.
        while metadata and (
            available
            - graph_width
            - sum(item[2] for item in metadata)
            - padding * (2 + len(metadata))
            < 24
        ):
            metadata.pop()
        self._show_sha = any(key == "sha" for key, _, _ in metadata)
        subject_width = max(
            24,
            available
            - graph_width
            - sum(item[2] for item in metadata)
            - padding * (2 + len(metadata)),
        )
        table.add_column("", width=graph_width, key="graph")
        table.add_column("Commit", width=subject_width, key="subject")
        for key, label, column_width in metadata:
            table.add_column(label, width=column_width, key=key)

    def on_history_table_viewport_changed(self, event: HistoryTable.ViewportChanged) -> None:
        event.stop()
        # Keep pane dragging cheap even with 5,000 loaded commits. Existing
        # columns scroll/clip during the drag; refit defaults once on release.
        if not isinstance(self.mouse_captured, PaneSplitter):
            self.call_after_refresh(self.repaint_history)

    def on_pane_splitter_resized(self, event: PaneSplitter.Resized) -> None:
        event.stop()
        if event.splitter.axis == "width":
            self.call_after_refresh(self.repaint_history)

    def on_resize(self, event: events.Resize) -> None:
        # Layout classes belong to the workspace, not whichever modal is active.
        workspace = self.screen_stack[0]
        width = event.size.width
        workspace.set_class(width < 140, "compact")
        workspace.set_class(width < 105, "narrow")
        workspace.set_class(width <= 60, "pane-tight")
        # A medium terminal keeps history + inspection legible instead of
        # dividing its width between three permanently squeezed panes.
        workspace.set_class(85 <= width < 120 and self._task_view != "refs", "refs-auto-hidden")

        # At narrow tmux widths the three pane minima cannot coexist. Keep the
        # graph (the primary workspace) whole, initially borrowing the inspector.
        # Ctrl+Right can swap it back in without making the workspace overflow.
        constrained = width < 85
        was_constrained = workspace.has_class("pane-constrained")
        workspace.set_class(constrained, "pane-constrained")
        if not constrained:
            workspace.remove_class(
                "responsive-inspector",
                "responsive-history-only",
                "responsive-refs",
                "task-files",
                "task-composer",
            )
        elif not was_constrained:
            workspace.remove_class("responsive-inspector", "responsive-refs")
            workspace.add_class("responsive-history-only")
        if self.is_mounted and self.snapshot_data:
            self.call_after_refresh(self.repaint_history)

    def push_screen(self, screen, callback=None, wait_for_dismiss=False):
        # A keyboard shortcut can open a modal mid-drag. Textual 1.0 otherwise
        # keeps routing mouse events to the captured widget on the old screen.
        if isinstance(self.mouse_captured, (PaneSplitter, ResizableDataTable)):
            self.capture_mouse(None)
        return super().push_screen(screen, callback, wait_for_dismiss)

    def _fit_panes(self) -> None:
        # Resize can arrive while a modal is open; query the workspace explicitly.
        for splitter in self.screen_stack[0].query(PaneSplitter):
            splitter.fit()

    def on_app_focus(self, event: events.AppFocus) -> None:
        self._terminal_active = True
        # Terminal focus and the tmux resume notification may arrive together.
        # Keep a request pending if a modal or mutation currently prevents it.
        if monotonic() - self._last_remote_refresh >= self.LOCAL_REFRESH_INTERVAL:
            self._next_remote_refresh = 0.0
        self.poll_repository()

    def on_app_blur(self, event: events.AppBlur) -> None:
        # Do not kill a running mutation/fetch. Pause subsequent automatic work.
        self._terminal_active = False

    def poll_repository(self) -> None:
        if self.repo and not self._refreshing and self._can_refresh(self.repo):
            self._refreshing = True
            self.run_worker(
                self.refresh_background(self.repo), group="refresh", exit_on_error=False
            )

    def _can_refresh(self, repo: Repository) -> bool:
        return (
            self.repo is repo
            and not self.busy
            and self._terminal_active
            and len(self.screen_stack) == 1
        )

    async def refresh_background(self, repo: Repository) -> None:
        """Local reads are single-flight and never wait behind network requests."""
        try:
            async with self._serial:
                if not self._can_refresh(repo):
                    return
                generation, epoch = self._generation, self._mutation_epoch
                snapshot = await asyncio.to_thread(repo.snapshot, include_history=False)
                await self._poll_history_state()
            if (
                not self._can_refresh(repo)
                or generation != self._generation
                or epoch != self._mutation_epoch
            ):
                return
            await self._apply_refresh(snapshot)
            self._start_remote_refresh()
        except Exception as error:
            if self.repo is repo:
                self._set_refresh_error(str(error))
        finally:
            self._refreshing = False

    def _start_remote_refresh(self) -> None:
        """Use the snapshot already presented; don't rescan just to schedule fetch."""
        repo, snapshot, coordinator = self.repo, self.snapshot_data, self._operations
        if (
            repo is None
            or snapshot is None
            or not self._can_refresh(repo)
            or self._settings["offline"]
            or not self._settings["remote_refresh_enabled"]
            or self._remote_refreshing is repo
            or monotonic() < self._next_remote_refresh
            or coordinator is None
            or coordinator.repository is not repo
        ):
            return
        self._remote_refreshing = repo
        self.run_worker(
            self.refresh_remotes(repo, coordinator, snapshot),
            group="remote-refresh",
            exit_on_error=False,
        )

    async def refresh_remotes(
        self,
        repo: Repository,
        coordinator: OperationCoordinator,
        requested: Snapshot,
    ) -> None:
        """Fetch is serialized with mutations, not with immutable/local inspection.

        A running fetch is never killed or silently retried. Local polling and
        detail/diff reads keep their own short queue while remote work is pending.
        """
        sidebar = self.screen_stack[0].query_one(ReferenceSidebar)
        remotes_changed = False
        try:
            if not self._can_refresh(repo) or self._settings["offline"]:
                return
            sidebar.set_pull_requests_loading(True)
            errors: list[str] = []
            listings = None
            if requested.remotes:
                try:
                    await asyncio.to_thread(self._execute_reviewed, coordinator, Action("fetch"))
                except Exception as error:
                    errors.append(str(error))
            if self.repo is not repo or not self._terminal_active or self._settings["offline"]:
                return
            self._remote_reading = repo
            try:
                listings = await asyncio.to_thread(repo.open_pull_requests)
            except Exception as error:
                errors.append(str(error))
            finally:
                if self._remote_reading is repo:
                    self._remote_reading = None
            async with self._serial:
                generation, epoch = self._generation, self._mutation_epoch
                snapshot = await asyncio.to_thread(repo.snapshot, include_history=False)
                await self._poll_history_state()
            if self.repo is not repo:
                return
            if (
                self._can_refresh(repo)
                and generation == self._generation
                and epoch == self._mutation_epoch
            ):
                await self._apply_refresh(snapshot)
            remotes_changed = (
                snapshot.remotes != requested.remotes
                or self.snapshot_data is None
                or self.snapshot_data.remotes != requested.remotes
            )
            if remotes_changed:
                sidebar.update_pull_requests(None)
                self._set_refresh_error(
                    "Remotes changed while loading PRs; refreshing new destinations shortly."
                )
                return
            if listings is not None:
                if not self._listings_match(requested, listings):
                    sidebar.update_pull_requests(None)
                    self._set_refresh_error(
                        "Remote context changed during the PR read; discarded mismatched results."
                    )
                    return
                sidebar.update_pull_requests(listings)
                if self._selected_kind == "pull-request":
                    self._sidebar_selection = sidebar.get_selection(self._sidebar_selection.key)
                    sidebar.set_active(self._sidebar_selection)
            self._set_refresh_error(errors[0] if errors else "")
        except Exception as error:
            if self.repo is repo:
                self._set_refresh_error(str(error))
        finally:
            if self.repo is repo:
                sidebar.set_pull_requests_loading(False)
                self._last_remote_refresh = monotonic()
                if (
                    not remotes_changed
                    and self.snapshot_data
                    and requested.remotes == self.snapshot_data.remotes
                ):
                    self._next_remote_refresh = monotonic() + self.REMOTE_REFRESH_INTERVAL
                else:
                    self._next_remote_refresh = 0.0
            if self._remote_refreshing is repo:
                self._remote_refreshing = None

    async def _capture_history(self, snapshot: Snapshot) -> None:
        self._history_token += 1
        self._history_loading = False
        self._stop_search(clear_results=True)
        if self._investigation is None:
            assert self.repo is not None
            self._investigation = InvestigationService(self.repo)
        scope = await asyncio.to_thread(self._investigation.capture, self._focus_roots)
        page = await asyncio.to_thread(self._investigation.history, scope, self.history_order)
        self._history_scope, self._history_page = scope, page
        self._scope_state = page.state
        self._scope_snapshot = self._captured_badges_snapshot(snapshot, scope)
        self._history_trail = [None]
        self._history_position = 0

    @staticmethod
    def _captured_badges_snapshot(snapshot: Snapshot, scope: InvestigationScope) -> Snapshot:
        previous = {ref.full_name: ref for ref in snapshot.refs}
        refs = []
        prefixes: tuple[tuple[str, Literal["local", "remote", "tag"]], ...] = (
            ("refs/heads/", "local"),
            ("refs/remotes/", "remote"),
            ("refs/tags/", "tag"),
        )
        for captured in scope.captured_refs:
            for prefix, kind in prefixes:
                if captured.full_name.startswith(prefix):
                    ref = previous.get(captured.full_name) or Ref(
                        captured.full_name.removeprefix(prefix),
                        captured.full_name,
                        captured.oid,
                        kind,
                    )
                    refs.append(
                        replace(ref, oid=captured.oid, current=captured.full_name == scope.branch)
                    )
                    break
        return replace(
            snapshot,
            refs=tuple(refs),
            head=scope.head_oid,
            branch=scope.branch.removeprefix("refs/heads/"),
        )

    async def _poll_history_state(self) -> None:
        service, scope = self._investigation, self._history_scope
        if service is None or scope is None:
            return
        state = await asyncio.to_thread(service.status, scope)
        if service is self._investigation and scope is self._history_scope:
            if not self._scope_state.current and state.current:
                state = ScopeState(False, state.available, self._scope_state.reason)
            self._scope_state = state
            if not state.current and self._search_running:
                self._stop_search()
                self._search_note = "scope changed — stopped"

    def _with_history(self, snapshot: Snapshot) -> Snapshot:
        page = self._history_page
        if page is None:
            return snapshot
        return replace(
            snapshot,
            history=tuple(HistoryLine("", commit) for commit in page.commits),
            has_more=page.has_more,
        )

    async def _apply_refresh(self, snapshot: Snapshot) -> None:
        snapshot = self._with_history(snapshot)
        if snapshot != self.snapshot_data:
            await self.apply_snapshot(snapshot)
        elif self._active_diff and self._active_diff[2] == "":
            # Porcelain status can stay unchanged while an open file is edited.
            self.load_diff(*self._active_diff[:2], preserve_scroll=True)
        self.update_search(jump=False)
        self._update_history_controls()

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
                    await self._restore_workspace_state(self.repo)
                self.history_order = (
                    "author-time"
                    if self._settings["graph_order"] == "chronological"
                    else "topology"
                )
                snapshot = await asyncio.to_thread(self.repo.snapshot, include_history=False)
                await self._capture_history(snapshot)
            if len(self.screen_stack) == 1:
                await self._apply_refresh(snapshot)
                if self._search_query:
                    self._start_scoped_search(self._search_query, force=True)
            if not quiet:
                freshness = (
                    "offline · network paused"
                    if self._settings["offline"]
                    else "remote updates run automatically"
                    if self._settings["remote_refresh_enabled"]
                    else "automatic remote updates paused"
                )
                self.status("Local state refreshed · " + freshness)
                self._last_error = ""
        except Exception as error:
            self.show_error(str(error))
        finally:
            self.set_busy(False)
            self._start_remote_refresh()

    async def apply_snapshot(self, snapshot: Snapshot) -> None:
        new_repository = self.snapshot_data is None or self.snapshot_data.root != snapshot.root
        if new_repository or self.snapshot_data.remotes != snapshot.remotes:
            self._next_remote_refresh = 0.0
            self._set_refresh_error("")
        self._generation += 1
        self._detail_token += 1
        self._diff_token += 1
        self.snapshot_data = snapshot
        header = Text("rhizome", style=f"bold {TEAL}", no_wrap=True, overflow="ellipsis")
        if "DEMO" in self.title:
            header.append(" DEMO", style="bold #9a5a00")
        header.append("  /  ", style=MUTED)
        header.append(f"{snapshot.root.name}   ", style=f"bold {TEXT}")
        plain = self._settings["plain_glyphs"]
        header.append(
            f"{'branch' if plain else '⑂'} {snapshot.branch or 'detached HEAD'}", style=TEAL
        )
        if snapshot.ahead or snapshot.behind:
            header.append(
                f"   ahead {snapshot.ahead} behind {snapshot.behind}"
                if plain
                else f"   ↑{snapshot.ahead} ↓{snapshot.behind}",
                style=MUTED,
            )
        self.query_one("#repo-header", Static).update(theme_text(header, self._palette))
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
        self._ref_badges = build_ref_badges(
            replace(self._scope_snapshot, worktrees=snapshot.worktrees, remotes=snapshot.remotes)
            if self._scope_snapshot
            else snapshot,
            self._selected_ref,
            plain_glyphs=self._settings["plain_glyphs"],
            palette=self._palette,
        )
        self.repaint_history()
        self.paint_wip(snapshot.files)
        coordinator = self._operations
        self._managed_pull = None
        pull_attention = ""
        if coordinator is not None and coordinator.repository is self.repo:
            try:
                self._managed_pull = await asyncio.to_thread(coordinator.managed_pull)
            except Exception as error:
                # A crashed or uncertain native writer may leave no ordinary
                # Git operation marker. Never silently treat it as absent.
                pull_attention = str(error) or "Pull recovery ownership could not be verified."
        if pull_attention != self._pull_attention:
            self._pull_attention_started = datetime.now(UTC) if pull_attention else None
            if pull_attention:
                self.show_error(pull_attention)
        self._pull_attention = pull_attention
        managed = self._managed_pull
        banner = self.query_one("#operation-banner")
        banner.display = bool(pull_attention or managed or snapshot.operation or snapshot.conflicts)
        banner.tooltip = pull_attention or None
        if pull_attention:
            self.query_one("#operation-text", Static).update(
                Text("PULL BLOCKED · Activity (Ctrl+L) has recovery details")
            )
        elif managed:
            kind = managed.kind.upper()
            self.query_one("#operation-text", Static).update(
                Text(f"PULL {kind} PAUSED  ·  Resolve, stage, then Continue or Abort")
            )
        else:
            self.query_one("#operation-text", Static).update(
                Text(
                    f"{snapshot.operation.upper() or 'CONFLICTS'}  ·  {len(snapshot.conflicts)} unresolved files"
                    "  ·  External operation: finish it in Git, then refresh"
                )
            )
        self.query_one("#continue", Button).disabled = not bool(managed) or bool(snapshot.conflicts)
        self.query_one("#abort", Button).disabled = not bool(managed)
        self._update_history_controls()
        # Reconcile details without treating polling as an explicit graph click.
        # In particular, a clean checkout can show Working directory while the
        # graph cursor remains on HEAD (there is no WIP row to select).
        if self._history_key in self._commits:
            self.inspect_commit(self._commits[self._history_key], activate=new_repository)
        else:
            self._selected_commit = None
            self.query_one("#commit-detail", Static).update(
                "Select a commit to inspect its changes."
            )
            self.query_one(CommitFileTree).clear_files()
            if new_repository:
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
        if self._active_diff and self._active_diff[2] == "":
            active, staged, _oid = self._active_diff
            current = next(
                (
                    (f, s)
                    for f, s in self._file_rows.values()
                    if f.path == active.path and s == staged
                ),
                None,
            )
            if current:
                self._active_diff = (current[0], current[1], "")
                self.load_diff(*current, preserve_scroll=True)
            elif self._patch_document is not None:
                self._patch_stale = True
                self.query_one(PrecisionDiff).set_stale()
            else:
                self.back_to_graph()
        self.update_controls()
        if new_repository and self._pending_view:
            self.call_after_refresh(self._restore_navigation_context)

    def _clear_reference_selection(self) -> None:
        self._sidebar_selection = None
        self._reveal_ref_key = None
        self.query_one(ReferenceSidebar).set_active(None)
        self._update_reference_badges()

    def _update_reference_badges(self) -> None:
        if not self.snapshot_data:
            return
        old = self._ref_badges
        self._ref_badges = build_ref_badges(
            replace(
                self._scope_snapshot,
                worktrees=self.snapshot_data.worktrees,
                remotes=self.snapshot_data.remotes,
            )
            if self._scope_snapshot
            else self.snapshot_data,
            self._selected_ref,
            plain_glyphs=self._settings["plain_glyphs"],
            palette=self._palette,
        )
        table = self.query_one("#graph-table", DataTable)
        if "refs" not in table.columns:
            return
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
        if selection is None or (self.busy and selection.kind == "pull-request"):
            return
        self._sidebar_selection = selection
        self._reveal_ref_key = None
        self.query_one(ReferenceSidebar).set_active(selection)
        if selection.kind == "worktree":
            self.jump_to("wip")
        elif selection.kind == "linked-worktree":
            self.status(
                f"{selection.target} · double-click or use Open selected worktree in Actions"
            )
        elif selection.kind == "pull-request":
            self.status(f"Selected {selection.label}")
        elif selection.kind == "all":
            self.action_clear_filter()
            self.status("Unified graph · all local and remote branches")
        elif selection.oid in self._commits:
            hint = (
                " · use Checkout in Actions or the context menu"
                if selection.kind in {"local", "remote"}
                else ""
            )
            self.status(f"Selected {selection.label}{hint} · graph position unchanged")
        else:
            self.status(f"Selected {selection.label} · tip is outside the loaded unified graph")
        self._update_reference_badges()
        self.update_search(jump=False)
        if selection.kind == "pull-request":
            request = self.query_one(ReferenceSidebar).get_pull_request(selection.key)
            if request is not None:
                self.push_screen(PullRequestDetailsDialog(request))

    def on_reference_sidebar_worktree_open_requested(
        self, event: ReferenceSidebar.WorktreeOpenRequested
    ) -> None:
        event.stop()
        selection = self._current_sidebar_selection(event.root, event.selection)
        snapshot = self.snapshot_data
        if self.busy or selection is None or selection.kind != "linked-worktree" or not snapshot:
            return
        worktree = next(
            (item for item in snapshot.worktrees if str(item.path) == selection.target), None
        )
        if worktree is None or worktree.bare or worktree.prunable:
            return
        if worktree.current:
            self.jump_to("wip")
            self.status(f"Already open: {worktree.path}")
            return
        self.open_repository(str(worktree.path), worktree=True)

    def on_reference_sidebar_worktree_create_requested(
        self, event: ReferenceSidebar.WorktreeCreateRequested
    ) -> None:
        event.stop()
        snapshot, repo = self.snapshot_data, self.repo
        if (
            self.busy
            or len(self.screen_stack) > 1
            or repo is None
            or snapshot is None
            or event.root != repo.root
            or not snapshot.head
            or snapshot.operation
            or snapshot.conflicts
        ):
            return
        self._handle_context_action("create-worktree", ContextTarget(snapshot.head), snapshot, repo)

    def on_reference_sidebar_pull_requests_requested(
        self, event: ReferenceSidebar.PullRequestsRequested
    ) -> None:
        event.stop()
        repo, snapshot = self.repo, self.snapshot_data
        if self._settings["offline"]:
            self.status("Offline mode: hosted requests are paused.")
            return
        if (
            self.busy
            or len(self.screen_stack) > 1
            or repo is None
            or snapshot is None
            or event.root != repo.root
            or self._remote_refreshing is repo
        ):
            return
        if self._selected_kind == "pull-request":
            self._clear_reference_selection()
        self.query_one(ReferenceSidebar).update_pull_requests(None, loading=True)
        self._remote_refreshing = repo
        self.status("Loading open pull requests…")
        self.run_worker(
            self._load_pull_requests(repo, snapshot), group="remote-refresh", exit_on_error=False
        )

    @staticmethod
    def _listings_match(snapshot: Snapshot, listings: tuple[PullRequestListing, ...]) -> bool:
        # Checking snapshots before/after a request alone misses an ABA remote
        # change. Results also carry the actual hosted identity queried in between.
        expected = {remote.name: hosted_repository(remote.url) for remote in snapshot.remotes}
        return all(
            (repository := expected.get(listing.remote)) is not None
            and Repository._same_hosted_repository(repository, listing.repository)
            for listing in listings
        )

    async def _load_pull_requests(self, repo: Repository, requested: Snapshot) -> None:
        """Explicit hosted read, isolated from the automatic local refresh loop."""
        sidebar = self.screen_stack[0].query_one(ReferenceSidebar)
        self._remote_reading = repo
        try:
            listings = await asyncio.to_thread(repo.open_pull_requests)
            async with self._serial:
                generation, epoch = self._generation, self._mutation_epoch
                snapshot = await asyncio.to_thread(repo.snapshot, include_history=False)
                await self._poll_history_state()
            if self.repo is not repo:
                return
            if (
                self._can_refresh(repo)
                and generation == self._generation
                and epoch == self._mutation_epoch
            ):
                await self._apply_refresh(snapshot)
            if (
                snapshot.remotes != requested.remotes
                or self.snapshot_data is None
                or self.snapshot_data.remotes != requested.remotes
                or not self._listings_match(requested, listings)
            ):
                sidebar.update_pull_requests(None)
                self.status(
                    "Remotes changed while loading PRs. Load again for the new destinations."
                )
                return
            sidebar.update_pull_requests(listings)
            self._last_error = ""
            count = sum(len(listing.requests) for listing in listings)
            failures = sum(bool(listing.error) for listing in listings)
            if not listings:
                self.status("Open PRs require a GitHub.com or Azure DevOps remote")
            elif failures:
                self.status(f"Loaded {count} open PRs · {failures} remote(s) unavailable; see PRs")
            else:
                self.status(f"Loaded {count} open pull requests")
        except Exception as error:
            if self.repo is repo:
                sidebar.update_pull_requests(None)
                self.show_error(str(error))
        finally:
            if self._remote_reading is repo:
                self._remote_reading = None
            if self._remote_refreshing is repo:
                self._remote_refreshing = None
            if self.repo is repo:
                sidebar.set_pull_requests_loading(False)
                self.update_controls()

    def on_reference_sidebar_checkout_requested(
        self, event: ReferenceSidebar.CheckoutRequested
    ) -> None:
        event.stop()
        selection = self._current_sidebar_selection(event.root, event.selection)
        if selection is None or selection.kind not in {"local", "remote"} or self.busy:
            return
        self._sidebar_selection = selection
        self.query_one(ReferenceSidebar).set_active(selection)
        self.status(f"Selected {selection.label} · use Checkout in Actions or the context menu")

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
        self._sidebar_selection = selection
        self._reveal_ref_key = None
        self.query_one(ReferenceSidebar).set_active(selection)
        self._update_reference_badges()
        self.update_search(jump=False)
        ref = next((ref for ref in snapshot.refs if ref.full_name == selection.target), None)
        if ref:
            self._show_context_menu(ContextTarget(ref.oid, ref), event.screen_x, event.screen_y)

    def on_history_table_context_requested(self, event: HistoryTable.ContextRequested) -> None:
        event.stop()
        snapshot = self.snapshot_data
        if (
            self.busy
            or len(self.screen_stack) > 1
            or not snapshot
            or event.revision != self.query_one(HistoryTable).revision
            or event.key not in self._commits
        ):
            return
        ref = next(
            (
                ref
                for ref in snapshot.refs
                if ref.full_name == event.ref_name
                and ref.oid == event.key
                and ref.kind in {"local", "remote"}
            ),
            None,
        )
        self._show_context_menu(ContextTarget(event.key, ref), event.screen_x, event.screen_y)

    def _show_context_menu(self, target: ContextTarget, screen_x: int, screen_y: int) -> None:
        snapshot, repo = self.snapshot_data, self.repo
        if not snapshot or not repo or self.busy or len(self.screen_stack) > 1:
            return
        actions = context_actions(snapshot, target)
        offered = {item[0] for item in actions if item is not None}

        def selected(operation: str | None) -> None:
            if operation in offered and self.repo is repo and not self.busy:
                self._handle_context_action(operation, target, snapshot, repo)

        self.push_screen(
            BranchContextMenu(
                snapshot.branch,
                target.label,
                screen_x=screen_x,
                screen_y=screen_y,
                actions=actions,
            ),
            selected,
        )

    def _handle_context_action(
        self, operation: str, target: ContextTarget, snapshot: Snapshot, repo: Repository
    ) -> None:
        # Every subsequent picker/prompt closes over these immutable values. In
        # particular, a stale inspector selection must never supply the target.
        def run(action: Action, *, guard: ContextTarget = target) -> None:
            if self.repo is not repo:
                self.notify("Repository changed. Choose the action again.", severity="warning")
                return
            self.run_mutation(
                replace(
                    action,
                    expected_branch=snapshot.branch,
                    expected_head=snapshot.head,
                    expected_ref=guard.revision,
                    expected_oid=guard.oid,
                )
            )

        def named(op: str, name: str | None) -> None:
            if name:
                run(Action(op, target=target.revision, name=name))

        if operation in {"copy-sha", "copy-branch"}:
            self._copy_context_value(target.oid if operation == "copy-sha" else target.label)
        elif operation == "copy-commit-link":
            links = [
                (remote.name, remote_link(remote, "commit", target.oid))
                for remote in snapshot.remotes
            ]
            self._choose_copy_link([(name, url) for name, url in links if url])
        elif operation in {"copy-branch-link", "edit-message", "delete-remote-branch"}:
            self.run_worker(
                self._context_read(operation, target, snapshot, repo, run),
                group="context-read",
                exit_on_error=False,
            )
        elif operation == "create-pr":
            self.set_busy(True, "Reading pull request branches…")
            self.run_worker(
                self._prepare_pull_request(target, snapshot, repo),
                group="context-read",
                exit_on_error=False,
            )
        elif operation in {"pull", "push"}:
            if operation == "pull":
                # A context menu is explicitly the conservative, ff-only path;
                # it never borrows the saved split-button default or its target.
                if self.repo is repo and self.snapshot_data is snapshot:
                    self.run_pull(
                        Action(
                            "pull",
                            pull_mode="ff-only",
                            expected_branch=snapshot.branch,
                            expected_head=snapshot.head,
                            expected_ref=target.revision,
                            expected_oid=target.oid,
                        )
                    )
                return
            if operation == "push" and not snapshot.upstream and len(snapshot.remotes) > 1:
                self.push_screen(
                    ActionPicker(
                        f"PUBLISH {snapshot.branch} TO",
                        [(r.name, f"{r.name} · {r.push_url or r.url}") for r in snapshot.remotes],
                    ),
                    lambda remote: run(Action("push", remote=remote)) if remote else None,
                )
            else:
                run(Action(operation))
        elif operation == "checkout":
            if target.ref:
                run(Action("checkout", target=target.revision, remote=target.ref.remote))
            else:
                refs = [
                    r
                    for r in snapshot.refs
                    if r.oid == target.oid and r.kind in {"local", "remote"}
                ]
                choices = [(r.full_name, f"Check out {r.name}") for r in refs]
                choices.append(("detached", f"Detached HEAD at {target.label}"))

                def checkout(choice: str | None) -> None:
                    if choice == "detached":
                        run(Action("checkout", target=target.oid))
                    elif ref := next((r for r in refs if r.full_name == choice), None):
                        run(
                            Action("checkout", target=ref.full_name, remote=ref.remote),
                            guard=ContextTarget(ref.oid, ref),
                        )

                self.push_screen(ActionPicker("CHECK OUT", choices), checkout)
        elif operation in {"create-branch", "rename-branch", "tag"}:
            self.push_screen(
                PromptDialog(operation.replace("-", " ").title() + f" · {target.label}", "Name"),
                lambda name: named(operation, name),
            )
        elif operation == "reset":
            modes = [
                ("reset-soft", "Soft — keep index and working files"),
                ("reset-mixed", "Mixed — keep working files, reset index"),
                ("reset-hard", "Hard — discard changes; may overwrite untracked files"),
            ]
            self.push_screen(
                ActionPicker(f"RESET {snapshot.branch} TO {target.label}", modes),
                lambda mode: run(Action(mode, target=target.revision)) if mode else None,
            )
        elif operation == "create-worktree":

            def destination(name: str = "") -> None:
                self.push_screen(
                    PromptDialog(f"Worktree at {target.label} · destination directory", "Path"),
                    lambda path: (
                        run(
                            Action(
                                "create-worktree", target=target.revision, name=name, paths=(path,)
                            )
                        )
                        if path
                        else None
                    ),
                )

            def worktree_mode(mode: str | None) -> None:
                if mode == "detached":
                    destination()
                elif mode == "new-branch":
                    self.push_screen(
                        PromptDialog("New worktree branch", "Branch name"),
                        lambda name: destination(name) if name else None,
                    )

            self.push_screen(
                ActionPicker(
                    "CREATE WORKTREE",
                    [
                        ("new-branch", "On a new branch…"),
                        ("detached", "With detached HEAD…"),
                    ],
                ),
                worktree_mode,
            )
        elif operation == "set-upstream" and target.ref:
            if target.ref.kind == "remote":
                run(
                    Action(
                        "set-upstream",
                        target=f"refs/heads/{snapshot.branch}",
                        name=target.revision,
                        remote=target.ref.remote,
                    )
                )
            else:
                remotes = [r for r in snapshot.refs if r.kind == "remote" and r.remote]

                def upstream(choice: str | None) -> None:
                    if ref := next((r for r in remotes if r.full_name == choice), None):
                        run(
                            Action(
                                "set-upstream",
                                target=target.revision,
                                name=ref.full_name,
                                remote=ref.remote,
                            )
                        )

                self.push_screen(
                    ActionPicker(
                        f"UPSTREAM FOR {target.label}", [(r.full_name, r.name) for r in remotes]
                    ),
                    upstream,
                )
        elif operation == "apply-patch":
            self.push_screen(
                PromptDialog(
                    "Apply patch to current working directory (index unchanged)", "Patch file path"
                ),
                lambda path: run(Action("apply-patch", paths=(path,))) if path else None,
            )
        else:
            run(Action(operation, target=target.revision))

    async def _prepare_pull_request(
        self, target: ContextTarget, snapshot: Snapshot, repo: Repository
    ) -> None:
        guard = Action(
            "create-pr",
            target=target.revision,
            expected_branch=snapshot.branch,
            expected_head=snapshot.head,
            expected_ref=target.revision,
            expected_oid=target.oid,
        )
        try:
            async with self._serial:
                plans = await asyncio.to_thread(repo.pull_request_plans, guard)
        except Exception as error:
            self.show_error(str(error))
            return
        finally:
            self.set_busy(False)
        if self.repo is not repo or len(self.screen_stack) > 1:
            return

        def compose(plan: PullRequestPlan) -> None:
            if self.repo is not repo or self.busy or len(self.screen_stack) > 1:
                return
            try:
                draft_key, draft = self._pull_request_draft(plan)
            except PersistenceError as error:
                self.show_error(str(error))
                return
            workspace = self._workspace_key

            async def create(title: str, description: str) -> str:
                if self._settings["offline"]:
                    raise PullRequestError(
                        "Offline mode: disable it before publishing. Your local draft is retained."
                    )
                if self.repo is not repo or self.busy:
                    raise PullRequestError("Repository changed. Close and choose the action again.")
                # The modal owns its disabled controls while in flight. The shared
                # flag/lock also exclude refresh and any other repository operation.
                self.busy = True
                try:
                    async with self._serial:
                        url = await asyncio.to_thread(
                            repo.create_pull_request, plan, title, description
                        )
                    try:
                        self._save_pull_request_draft(workspace, draft_key, "", "")
                    except PersistenceError as error:
                        # Creation has already happened: a cleanup error must not
                        # present a failed creation or invite duplicate publication.
                        self._draft_error(
                            "PR created, but its saved draft was not cleared: " + str(error)
                        )
                    return url
                finally:
                    self.busy = False

            def closed(url: str | None) -> None:
                if url:
                    # A newly created request should appear without another click.
                    self._next_remote_refresh = 0.0
                    if self._selected_kind == "pull-request":
                        self._clear_reference_selection()
                    self.query_one(ReferenceSidebar).update_pull_requests(None)
                self.update_controls()
                self.status(f"Pull request created: {url}" if url else "Pull request cancelled")
                if url:
                    self.poll_repository()

            self.push_screen(
                PullRequestDialog(
                    plan,
                    create,
                    draft=draft,
                    session_only=self._state_excluded,
                    save_draft=lambda title, description: self._save_pull_request_draft(
                        workspace, draft_key, title, description
                    ),
                ),
                closed,
            )

        if len(plans) == 1:
            compose(plans[0])
        elif plans:
            choices = {str(index): plan for index, plan in enumerate(plans)}
            self.push_screen(
                ActionPicker(
                    "CREATE PULL REQUEST IN",
                    [
                        (key, f"{plan.remote} · {plan.repository.web_url}")
                        for key, plan in choices.items()
                    ],
                ),
                lambda key: compose(choices[key]) if key in choices else None,
            )

    async def _context_read(
        self,
        operation: str,
        target: ContextTarget,
        snapshot: Snapshot,
        repo: Repository,
        run: Callable[[Action], None],
    ) -> None:
        """Read prompt/link data without blocking the event loop or drifting targets."""
        self.set_busy(True, "Reading selected context…")
        try:
            async with self._serial:
                if operation == "edit-message":
                    value = (await asyncio.to_thread(repo.detail, target.oid)).message
                else:
                    remote_name, source = await asyncio.to_thread(
                        repo.branch_remote_target, target.revision
                    )
                    remote = next((r for r in snapshot.remotes if r.name == remote_name), None)
                    value = (
                        remote_link(remote, "branch", source.removeprefix("refs/heads/"))
                        if remote
                        else ""
                    )
            if self.repo is not repo or len(self.screen_stack) > 1:
                return
            if operation == "edit-message":
                self.push_screen(
                    MessageDialog(
                        "Edit latest commit message (staged files are not included)", value
                    ),
                    lambda message: (
                        run(
                            Action(
                                "edit-message",
                                target=target.revision,
                                message=message,
                            )
                        )
                        if message
                        else None
                    ),
                )
            elif operation == "delete-remote-branch":
                # Resolve the actual source, not a possibly different tracking
                # alias, before asking the user to choose a destructive target.
                # Local deletion remains separate; the two are not atomic.
                destination = (remote.push_url or remote.url) if remote else remote_name
                self.push_screen(
                    ActionPicker(
                        "DELETE BRANCH ON REMOTE",
                        [
                            (
                                "delete",
                                f"Delete {source.removeprefix('refs/heads/')} on {remote_name}"
                                f" · {destination} (cannot undo here)",
                            ),
                        ],
                    ),
                    lambda choice: (
                        run(
                            Action(
                                "delete-remote-branch",
                                target=target.revision,
                                name=source,
                                remote=remote_name,
                                expected_push_url=destination,
                            )
                        )
                        if choice == "delete"
                        else None
                    ),
                )
            elif value:
                self._copy_context_value(value)
            else:
                self.notify("No supported browser link for this remote.", severity="warning")
        except Exception as error:
            self.show_error(str(error))
        finally:
            self.set_busy(False)

    def _copy_context_value(self, value: str) -> None:
        self.run_worker(self._copy_value(value), exit_on_error=False)

    async def _copy_value(self, value: str) -> None:
        result = await copy_text(self, value)
        self.status(result.value)

    def _choose_copy_link(self, links: list[tuple[str, str]]) -> None:
        if len(links) == 1:
            self._copy_context_value(links[0][1])
        elif links:
            by_name = dict(links)
            self.push_screen(
                ActionPicker(
                    "COPY LINK FROM REMOTE", [(name, f"{name} · {url}") for name, url in links]
                ),
                lambda name: self._copy_context_value(by_name[name]) if name in by_name else None,
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
        self.select_row("graph-table", event.key, explicit=True)
        self.status("Inspecting commit · use Checkout in Actions or the context menu to switch")

    def repaint_history(self) -> None:
        # App queries search the active screen. A modal can open between a
        # resize and this deferred callback; leave its focus/contents alone.
        # The next workspace refresh rebuilds columns at the new dimensions.
        if not self.snapshot_data or len(self.screen_stack) != 1:
            return
        self._rebuilding = True
        table = self.query_one("#graph-table", DataTable)
        previous_scroll = table.scroll_offset
        plain = self._settings["plain_glyphs"]
        cache_key = (self.snapshot_data.history, self.graph_style, plain, self.theme)
        if cache_key != self._graph_cache_key:
            self._graph_rows = render_history(
                self.snapshot_data.history,
                self.graph_style,
                plain_glyphs=plain,
                palette=self._palette,
            )
            self._graph_cache_key = cache_key
        self.query_one("#history-heading", Static).update(Text("History"))
        dirty = len(self.snapshot_data.files)
        wip_height = 2 if self.graph_style == "smooth" else 1
        geometry = (("wip", wip_height),) if dirty else ()
        geometry += tuple(
            (row.commit.oid if row.commit else f"connector-{index}", row.height)
            for index, row in enumerate(self._graph_rows)
        )
        preserve_scroll = bool(geometry) and geometry == self._graph_geometry
        self._graph_geometry = geometry
        self._configure_history_columns()
        self._commits = {}
        columns = [str(column.key.value) for column in table.ordered_columns]
        if dirty:
            cells = {
                "graph": Text(
                    (" *" if self.graph_style == "smooth" else "*")
                    if plain
                    else (" ◉" if self.graph_style == "smooth" else "◉"),
                    style=self._palette.teal,
                ),
                "refs": Text("Uncommitted", style=self._palette.muted),
                "subject": Text(
                    f"Working directory · {dirty} changed file{'s' if dirty != 1 else ''}",
                    style=f"bold {self._palette.teal}",
                    no_wrap=True,
                    overflow="ellipsis",
                ),
            }
            table.add_row(*(cells.get(key, "") for key in columns), height=wip_height, key="wip")
        for index, row in enumerate(self._graph_rows):
            if not row.commit:
                table.add_row(row.graph, *([""] * (len(columns) - 1)), key=f"connector-{index}")
                continue
            commit = row.commit
            self._commits[commit.oid] = commit
            badges = self._ref_badges.get(commit.oid)
            refs = badges.text if badges else Text()
            message = Text(
                commit.subject, style=self._palette.text, no_wrap=True, overflow="ellipsis"
            )
            cells = {
                "graph": row.graph,
                "subject": message,
                "refs": refs,
                "author": Text(
                    commit.author, style=self._palette.muted, no_wrap=True, overflow="ellipsis"
                ),
                "date": Text(
                    format_commit_date(commit.date),
                    style=Style(
                        color=self._palette.muted,
                        meta={"commit_date": format_commit_date(commit.date, full=True)},
                    ),
                    no_wrap=True,
                ),
                "sha": Text(commit.short_id, style=self._palette.muted),
            }
            table.add_row(*(cells[key] for key in columns), height=row.height, key=commit.oid)
        # A clean checkout has no synthetic node. Prefer its actual HEAD when
        # the old selection disappears; HEAD may be outside the loaded limit.
        key = next(
            (
                candidate
                for candidate in (self._history_key, "wip", self.snapshot_data.head, *self._commits)
                if candidate in table.rows
            ),
            "",
        )
        if key:
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
        table.display = bool(files)
        self.query_one("#wip-empty").display = not bool(files)
        self._file_rows = {}
        for staged in (False, True):
            for number, file in enumerate(files):
                if (staged and not file.staged) or (not staged and not file.unstaged):
                    continue
                key = f"{'s' if staged else 'u'}-{number}"
                self._file_rows[key] = (file, staged)
                state = "CONFLICT" if file.conflicted else ("STAGED" if staged else "UNSTAGED")
                color = (
                    self._palette.warning
                    if file.conflicted
                    else (self._palette.teal if staged else self._palette.muted)
                )
                table.add_row(Text(state, style=color), Text(file.path), key=key)
                if (
                    self._selected_file
                    and file.path == self._selected_file[0].path
                    and staged == self._selected_file[1]
                ):
                    table.move_cursor(row=table.get_row_index(key), animate=False)
        if not files:
            self._selected_file = None
            if self._active_diff and self._active_diff[2] == "":
                self.back_to_graph()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = event.data_table
        if self._rebuilding or not table.row_count:
            return
        # Rebuild highlights may be delivered after _finish_rebuild. Ignore
        # intermediate cursors and the graph selection we already reconciled;
        # actual arrow navigation still inspects the newly highlighted commit.
        if table.ordered_rows[table.cursor_row].key != event.row_key:
            return
        key = str(event.row_key.value)
        if table.id == "graph-table" and key == self._history_key:
            return
        self.select_row(table.id, key)

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
                changed_history = self._history_key != key
                if key in self.query_one("#graph-table", DataTable).rows:
                    self._history_key = key
                self._detail_token += 1
                self._selected_commit = None
                if explicit or changed_history:
                    self.back_to_graph()
                    self.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
                if explicit:
                    self.action_show_files()
            elif key in self._commits:
                self._history_key = key
                self.inspect_commit(self._commits[key])
                if explicit:
                    self._task_view = "primary"
                    self.query_one("#inspector-panel").remove_class("collapsed")
                    self.query_one("#inspector-splitter").remove_class("collapsed")
                    self._display_primary_panel("inspector")
                    if self.screen_stack[0].has_class("pane-constrained"):
                        self.call_after_refresh(self.query_one(CommitFileTree).focus)
        elif table_id == "wip-table" and key in self._file_rows:
            self._selected_file = self._file_rows[key]
            self.update_controls()
            if explicit:
                self.inspect_worktree_file(*self._selected_file)

    def on_commit_file_tree_file_selected(self, event: CommitFileTree.FileSelected) -> None:
        event.stop()
        if self._selected_commit and event.identity == self._selected_commit.oid:
            self.inspect_commit_file(event.file)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.tabbed_content.id != "inspector-tabs":
            return
        self.back_to_graph()
        if (
            event.tabbed_content.active == "details-tab"
            and self._selected_commit is None
            and self._history_key in self._commits
        ):
            # Returning from the clean Working directory keeps the real graph
            # selection available for inspection; no synthetic WIP row is needed.
            self.inspect_commit(self._commits[self._history_key], activate=False)

    def inspect_commit(self, commit: Commit, *, activate: bool = True) -> None:
        changed_commit = self._selected_commit is None or self._selected_commit.oid != commit.oid
        if changed_commit and (activate or (self._active_diff and self._active_diff[2])):
            self.back_to_graph()
        self._selected_commit = commit
        self._detail_token += 1
        if changed_commit:
            self._diff_token += 1
        token, generation, repo = self._detail_token, self._generation, self.repo
        # A menu can become the active screen while Git is reading. Inspector
        # widgets always belong to the mounted workspace, not that modal.
        workspace = self.screen_stack[0]
        tabs = workspace.query_one("#inspector-tabs", TabbedContent)
        if activate and tabs.active != "details-tab":
            tabs.active = "details-tab"
        workspace.query_one("#commit-detail", Static).update(
            Text(f"{commit.subject}\nLoading commit…")
        )
        if changed_commit:
            workspace.query_one(CommitFileTree).clear_files()

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
                text = Text(no_wrap=True, overflow="ellipsis")
                text.append(commit.subject + "\n\n", style=f"bold {TEXT}")
                text.append(f"{commit.author} · {commit.short_id}\n{local_date}", style=MUTED)
                metadata = Text(
                    f"{commit.subject}\n{commit.author} <{commit.email}>\n{local_date}\n{commit.oid}"
                )
                badges = self._ref_badges.get(commit.oid)
                if badges:
                    metadata.append("\n")
                    metadata.append(badges.description)
                header = workspace.query_one("#commit-detail", Static)
                header.update(theme_text(text, self._palette))
                header.tooltip = metadata
                tree = workspace.query_one(CommitFileTree)
                tree.set_files(detail.files, identity=commit.oid)
                # Loading detail must never turn an automatic tree highlight
                # into a diff. Refresh an already explicit view of this file.
                if self._active_diff and self._active_diff[2] == commit.oid:
                    active, _staged, _oid = self._active_diff
                    current = next(
                        (item for item in detail.files if item.path == active.path), None
                    )
                    if current:
                        self._active_diff = (current, False, commit.oid)
                        self.load_diff(current, oid=commit.oid, preserve_scroll=True)
                    else:
                        self.back_to_graph()
            except Exception as error:
                if token == self._detail_token and generation == self._generation:
                    self.show_error(str(error))

        self.run_worker(load(), group="detail", exit_on_error=False)

    def inspect_commit_file(self, file: FileChange) -> None:
        if self._selected_commit:
            self.open_diff(file, oid=self._selected_commit.oid)

    def inspect_worktree_file(self, file: FileChange, staged: bool) -> None:
        self._selected_file = (file, staged)
        self.update_controls()
        self.open_diff(file, staged=staged)

    def open_diff(self, file: FileChange, *, staged: bool = False, oid: str = "") -> None:
        """Temporarily use the center pane for an explicitly requested diff."""
        self._active_diff = (file, staged, oid)
        self._patch_document = None
        self._patch_stale = False
        self._diff_context = 3
        self._display_primary_panel("primary")
        self.query_one("#graph-workspace").display = False
        self.query_one("#diff-workspace").display = True
        source = f"COMMIT {oid[:8]}" if oid else ("INDEX" if staged else "WORKTREE")
        self.query_one("#center-diff-title", Static).update(Text(f"{source}  ·  {file.path}"))
        self.load_diff(file, staged=staged, oid=oid)

    def back_to_graph(self) -> None:
        """Dismiss the transient diff and invalidate any late diff worker."""
        if not self._active_diff:
            return
        self._active_diff = None
        self._patch_document = None
        self._patch_stale = False
        self._diff_token += 1
        workspace = self.screen_stack[0]
        workspace.query_one("#diff-workspace").display = False
        workspace.query_one("#graph-workspace").display = True

    def _apply_diff_presentation(self) -> None:
        workspace = self.screen_stack[0]
        split = workspace.query_one(SplitDiff)
        use_split = self._settings["diff_layout"] == "split" and not self._prefer_unified_diff
        split.display = use_split
        workspace.query_one(PrecisionDiff).display = (
            not use_split and self._patch_document is not None
        )
        workspace.query_one("#center-diff-scroll").display = (
            not use_split and self._patch_document is None
        )
        if use_split:
            split.set_patch(
                self._display_patch,
                plain_glyphs=self._settings["plain_glyphs"],
                palette=self._palette,
                stageable=self._patch_document is not None and self._patch_document.selectable,
            )
            split.set_stale(self._patch_stale)

    def on_unified_requested(self, event: UnifiedRequested) -> None:
        event.stop()
        if self._active_diff and event.patch == self._display_patch:
            self._prefer_unified_diff = True
            self._apply_diff_presentation()
            if self._patch_document is not None:
                self.query_one("#precision-lines").focus()

    def load_diff(
        self,
        file: FileChange,
        staged: bool = False,
        oid: str = "",
        *,
        preserve_scroll: bool = False,
    ) -> None:
        scope = "commit" if oid else "worktree"
        self._diff_tokens[scope] += 1
        token, epoch = self._diff_tokens[scope], self._diff_token
        generation, repo = self._generation, self.repo
        workspace = self.screen_stack[0]
        target = workspace.query_one("#center-diff", Static)
        precision = workspace.query_one(PrecisionDiff)
        scroll = workspace.query_one("#center-diff-scroll")
        context = self._diff_context
        if not preserve_scroll:
            self._prefer_unified_diff = False
            self._display_patch = ""
            workspace.query_one(SplitDiff).display = False
            precision.display = False
            scroll.display = True
            target.update("Loading diff…")

        async def load() -> None:
            document = None
            failure = ""
            patch = ""
            try:
                async with self._serial:
                    if (
                        token != self._diff_tokens[scope]
                        or epoch != self._diff_token
                        or generation != self._generation
                        or repo is not self.repo
                    ):
                        return
                    if oid:
                        patch = await asyncio.to_thread(repo.diff, file.path, oid=oid)
                    else:
                        document = await asyncio.to_thread(
                            PatchService(repo).open, file.path, staged=staged, context=context
                        )
            except Exception as error:
                failure = str(error)
            if (
                token != self._diff_tokens[scope]
                or epoch != self._diff_token
                or generation != self._generation
                or self._active_diff != (file, staged, oid)
            ):
                return
            if not oid and preserve_scroll and self._patch_document is not None:
                # An editor or external Git must not replace the user's review,
                # selection, cursor, or scroll position underneath them.
                if failure or document != self._patch_document:
                    self._patch_stale = True
                    precision.set_stale()
                    workspace.query_one(SplitDiff).set_stale()
                return
            if document is not None:
                self._patch_document = document
                self._patch_stale = False
                precision.display = True
                scroll.display = False
                precision.set_document(document, context=context)
                self._display_patch = (
                    "\n".join(line.text for line in document.lines)
                    or document.reason
                    or "No textual changes."
                )
                self._apply_diff_presentation()
                # Retain a literal source summary for copy/inspection consumers;
                # selection always uses the original document, never this text.
                target.update(
                    patch_text(
                        "\n".join(line.text for line in document.lines), palette=self._palette
                    )
                )
            else:
                precision.display = False
                scroll.display = True
                target.update(
                    Text(failure) if failure else patch_text(patch, palette=self._palette)
                )
                if not failure:
                    self._display_patch = patch
                    self._apply_diff_presentation()
                if not preserve_scroll:
                    scroll.scroll_home(animate=False)

        self.run_worker(load(), group="diff", exit_on_error=False)

    def on_precision_diff_apply_selection(self, event: PrecisionDiff.ApplySelection) -> None:
        event.stop()
        if self.query_one(SplitDiff).display:
            self.status("Split inspection is read-only; use Unified for exact staging.")
            return
        if self.busy:
            return
        if event.document is not self._patch_document or self._patch_stale:
            self.query_one(PrecisionDiff).set_stale()
            self.status("Patch changed. Refresh and select again before applying.")
            return
        self.run_mutation(
            Action("unstage" if event.document.staged else "stage", paths=(event.document.path,)),
            patch=event.document,
            selection=(event.start, event.end),
        )

    def on_precision_diff_refresh_requested(self, event: PrecisionDiff.RefreshRequested) -> None:
        event.stop()
        if self._active_diff and not self.busy:
            file, staged, oid = self._active_diff
            self._diff_context = min(10000, max(0, event.context))
            self.load_diff(file, staged=staged, oid=oid)

    def on_precision_diff_back_requested(self, event: PrecisionDiff.BackRequested) -> None:
        event.stop()
        self.back_to_graph()
        self.query_one("#graph-table").focus()

    def update_search(self, *, jump: bool = True) -> None:
        query, page = self._search_query, self._search_page
        scope_label = (
            f"Focused: {self._history_scope.label}"
            if self._history_scope and self._history_scope.kind == "focused"
            else "All refs + HEAD"
        )
        count = len(self._commits)
        self.query_one("#history-count", Static).update(
            Text(f"{count} commit{'s' if count != 1 else ''}")
        )
        caption = scope_label
        if query is not None:
            progress = page.examined_total if page else 0
            state = (
                "complete"
                if page and page.complete
                else "searching"
                if self._search_running
                else "incomplete"
            )
            caption += (
                f" · {len(self._search_matches)} batch matches · {progress} examined · {state}"
            )
            if self._search_note:
                caption += f" · {self._search_note}"
        if not self._commits:
            caption += " · No commits yet. Stage files to begin."
        selection = self._sidebar_selection
        if selection and selection.oid and selection.oid not in self._commits and query is None:
            caption += f" · Tip not in this window: {selection.label} — browse or Focus"
        caption += f" · {'Topology' if self.history_order == 'topology' else 'Author time'}"
        if self._history_page and self._history_page.omitted_before:
            caption += f" · {self._history_page.omitted_before} earlier rows omitted"
        if not self._scope_state.current:
            caption += " · CHANGED — refresh explicitly"
        self.query_one("#history-caption", Static).update(Text(caption))
        self.query_one("#history-caption", Static).tooltip = Text(caption)
        self.query_one("#history-caption", Static).styles.height = (
            2 if query or not self._scope_state.current else 1
        )
        self.query_one("#search-next", Button).disabled = not self._search_matches
        self.query_one("#search-controls").display = query is not None
        self.query_one("#search-more", Button).disabled = (
            self._search_running
            or query is None
            or bool(page and page.complete)
            or not self._scope_state.current
        )
        self.query_one("#search-stop", Button).disabled = not self._search_running
        self.query_one("#search-widen").display = bool(
            self._history_scope and self._history_scope.kind == "focused"
        )

    def _stop_search(self, *, clear_results: bool = False) -> None:
        self._search_token += 1
        self._search_running = False
        self._search_note = ""
        if clear_results:
            self._search_page = None
            self._search_matches = []

    def action_stop_search(self) -> None:
        self._stop_search()
        self._search_note = "stopped between batches"
        self.update_search(jump=False)

    def action_search_criteria(self) -> None:
        def chosen(query: SearchQuery | None) -> None:
            if query is not None:
                self._start_scoped_search(query)
                self.query_one("#graph-filter", Input).value = query.text

        self.push_screen(SearchCriteriaDialog(self._search_query), chosen)

    def action_search_more(self) -> None:
        if self._search_query and not self._search_running:
            cursor = self._search_page.next_cursor if self._search_page else None
            if self._search_page is None or not self._search_page.complete:
                self._start_scoped_search(self._search_query, cursor=cursor, force=True)

    def action_widen_search(self) -> None:
        if self._search_query:
            self._focus_history((), resume_query=self._search_query)

    def _start_scoped_search(
        self,
        query: SearchQuery,
        *,
        cursor: str | None = None,
        force: bool = False,
    ) -> None:
        if not (
            query.text or query.path is not None or query.authored_from or query.authored_through
        ):
            self._stop_search(clear_results=True)
            self._search_query = None
            self.update_search(jump=False)
            return
        if (
            not force
            and query == self._search_query
            and (self._search_running or self._search_page)
        ):
            return
        service, scope, order = self._investigation, self._history_scope, self.history_order
        if service is None or scope is None:
            return
        self._stop_search(clear_results=cursor is None)
        self._search_query = query
        if not self._scope_state.current:
            self._search_note = "scope changed — refresh explicitly"
            self.update_search(jump=False)
            return
        self._search_running = True
        token = self._search_token
        self.update_search(jump=False)

        async def search() -> None:
            next_cursor = cursor
            try:
                await asyncio.sleep(0.2)
                while token == self._search_token and scope is self._history_scope:
                    page = await asyncio.to_thread(
                        service.search, scope, query, order, next_cursor, 300
                    )
                    if token != self._search_token or scope is not self._history_scope:
                        return
                    if not self._scope_state.current or not page.state.current:
                        if not page.state.current:
                            self._scope_state = page.state
                        self._search_note = "scope changed — stopped"
                        return
                    self._search_page = page
                    self._search_matches = [hit.commit.oid for hit in page.hits]
                    if len(self.screen_stack) == 1:
                        self.update_search(jump=False)
                    # Keep only this bounded batch. Empty batches continue automatically;
                    # nonempty batches expose an explicit Search more control.
                    if page.hits or page.complete:
                        return
                    next_cursor = page.next_cursor
                    await asyncio.sleep(0)
            except Exception as error:
                if token == self._search_token:
                    self._search_note = f"incomplete: {error}"
            finally:
                if token == self._search_token:
                    self._search_running = False
                    if len(self.screen_stack) == 1:
                        self.update_search(jump=False)
                        self._update_history_controls()

        self.run_worker(search(), group="investigation-search", exit_on_error=False)

    def _history_context(self) -> _HistoryContext | None:
        if not self._history_scope or not self._history_page or not self._scope_snapshot:
            return None
        return _HistoryContext(
            self._history_scope,
            self._history_page,
            self._scope_snapshot,
            self._scope_state,
            self.history_order,
            self._focus_roots,
            tuple(self._history_trail),
            self._history_position,
            self._history_key,
            self.query_one("#graph-filter", Input).value,
            self._search_query,
            self._search_page,
            self.query_one("#graph-table", DataTable).scroll_offset,
        )

    def action_focus_history(self) -> None:
        selection = self._sidebar_selection
        if selection and selection.oid:
            root = selection.target if selection.target.startswith("refs/") else selection.oid
            self._focus_history((root,), expected=selection.oid)
        elif self._selected_commit:
            self._focus_history((self._selected_commit.oid,))
        else:
            self.status("Select a ref or commit, then choose Focus")

    def _focus_history(
        self,
        roots: tuple[str, ...],
        *,
        expected: str = "",
        resume_query: SearchQuery | None = None,
    ) -> None:
        service, previous = self._investigation, self._history_context()
        if service is None or previous is None or self.busy or self._history_loading:
            return
        self._history_token += 1
        token = self._history_token
        self._history_loading = True
        self._update_history_controls()

        async def focus() -> None:
            try:
                scope = await asyncio.to_thread(service.capture, roots)
                if expected and scope.root_oids != (expected,):
                    raise GitError("Selected ref changed; refresh/reselect before focusing it.")
                page = await asyncio.to_thread(service.history, scope, self.history_order)
                if (
                    token != self._history_token
                    or service is not self._investigation
                    or len(self.screen_stack) != 1
                ):
                    return
                snapshot = self.snapshot_data
                if snapshot is None:
                    return
                if not page.state.current:
                    raise GitError("Scope changed while focusing; refresh/reselect explicitly.")
                if self._focus_return is None:
                    self._focus_return = previous
                self._stop_search(clear_results=True)
                self._search_query = None
                self._history_scope, self._history_page = scope, page
                self._scope_state, self._focus_roots = page.state, roots
                self._history_trail = [None]
                self._history_position = 0
                self._scope_snapshot = self._captured_badges_snapshot(snapshot, scope)
                self.query_one("#graph-filter", Input).value = (
                    resume_query.text if resume_query else ""
                )
                await self.apply_snapshot(self._with_history(snapshot))
                if resume_query is not None:
                    self._start_scoped_search(resume_query, force=True)
                self.status(f"Scope: {scope.label} · Exit focus restores the previous view")
            except Exception as error:
                if token == self._history_token:
                    self.show_error(str(error))
            finally:
                if token == self._history_token:
                    self._history_loading = False
                    self._update_history_controls()

        self.run_worker(focus(), group="investigation", exit_on_error=False)

    def action_exit_focus(self) -> None:
        context, service = self._focus_return, self._investigation
        if context is None or service is None or self.busy or self._history_loading:
            return
        self._history_token += 1
        self._stop_search(clear_results=True)
        self._search_query, self._search_page = context.search_query, context.search_page
        self._search_matches = (
            [hit.commit.oid for hit in context.search_page.hits] if context.search_page else []
        )
        self._history_scope, self._history_page = context.scope, context.page
        self._scope_snapshot, self.history_order = context.snapshot, context.order
        self._scope_state = context.state
        self._focus_roots = context.roots
        self._history_trail, self._history_position = list(context.cursors), context.position
        self._history_key = context.selection
        self._focus_return = None
        self._restoring_view = True
        self.query_one("#graph-filter", Input).value = context.search

        async def restore() -> None:
            await self._poll_history_state()
            snapshot = self.snapshot_data
            if (
                snapshot is not None
                and self._history_scope is context.scope
                and len(self.screen_stack) == 1
            ):
                await self.apply_snapshot(self._with_history(snapshot))
                table = self.query_one("#graph-table", DataTable)
                self.call_after_refresh(
                    table.scroll_to, context.scroll.x, context.scroll.y, animate=False
                )
                self.status("Returned to the captured history window")
            self._restoring_view = False

        self.run_worker(restore(), group="investigation", exit_on_error=False)

    def _update_history_controls(self) -> None:
        workspace = self.screen_stack[0]
        blocked = self._history_loading or not self._scope_state.current
        workspace.query_one("#graph-order", Button).label = (
            "Topology" if self.history_order == "topology" else "Author time"
        )
        workspace.query_one("#graph-order", Button).disabled = blocked
        workspace.query_one("#focus-history", Button).disabled = self._history_loading
        workspace.query_one("#exit-focus").display = self._focus_return is not None
        workspace.query_one("#history-newer", Button).disabled = (
            blocked or self._history_position == 0
        )
        older = workspace.query_one("#load-more", Button)
        older.display = True
        older.disabled = blocked or self._history_page is None or not self._history_page.has_more

    def action_history_older(self) -> None:
        if self._history_page and self._history_page.next_cursor:
            self._request_history_page(self._history_page.next_cursor, self._history_position + 1)

    def action_history_newer(self) -> None:
        if self._history_position:
            position = self._history_position - 1
            self._request_history_page(self._history_trail[position], position)

    def action_toggle_history_order(self) -> None:
        order: HistoryOrder = "author-time" if self.history_order == "topology" else "topology"
        self._request_history_page(None, 0, order=order)

    def _request_history_page(
        self,
        cursor: str | None,
        position: int,
        *,
        order: HistoryOrder | None = None,
        selected_oid: str = "",
        persist_order: bool = True,
    ) -> None:
        service, scope = self._investigation, self._history_scope
        if (
            service is None
            or scope is None
            or self.busy
            or self._history_loading
            or not self._scope_state.current
            or len(self.screen_stack) != 1
        ):
            return
        requested_order = order or self.history_order
        self._history_token += 1
        token = self._history_token
        self._history_loading = True
        self._update_history_controls()
        self.status("Reading bounded history page…")

        async def load_page() -> None:
            try:
                page = await asyncio.to_thread(service.history, scope, requested_order, cursor)
                if (
                    token != self._history_token
                    or scope is not self._history_scope
                    or len(self.screen_stack) != 1
                ):
                    return
                if not self._scope_state.current or not page.state.current:
                    if not page.state.current:
                        self._scope_state = page.state
                    self.status("History changed — refresh explicitly before loading more")
                    return
                self._history_page = page
                if order is not None:
                    self._history_trail = [None]
                    self.history_order = order
                    self._settings["graph_order"] = (
                        "chronological" if order == "author-time" else "topo"
                    )
                    self._stop_search(clear_results=True)
                    if persist_order:
                        self._save_view_preference("graph_order", self._settings["graph_order"])
                elif selected_oid:
                    self._history_trail = self._history_trail[:position] + [cursor]
                elif position >= len(self._history_trail):
                    self._history_trail.append(cursor)
                self._history_position = position
                if selected_oid:
                    self._history_key = selected_oid
                if self.snapshot_data:
                    await self.apply_snapshot(self._with_history(self.snapshot_data))
                if order is not None and self._search_query:
                    self._start_scoped_search(self._search_query, force=True)
                self.status(
                    f"Showing {len(page.commits)} commits · {page.omitted_before} earlier rows omitted"
                )
            except Exception as error:
                if token == self._history_token:
                    self.show_error(str(error))
            finally:
                if token == self._history_token:
                    self._history_loading = False
                    self._update_history_controls()
                    if len(self.screen_stack) == 1:
                        self.update_search(jump=False)

        self.run_worker(load_page(), group="investigation", exit_on_error=False)

    def jump_to(self, key: str) -> None:
        table = self.query_one("#graph-table", DataTable)
        if key in table.rows:
            table.move_cursor(row=table.get_row_index(key), animate=False)
            self.select_row("graph-table", key, explicit=True)
        elif key == "wip":
            # The sidebar/current-worktree action still opens staging on a clean
            # checkout, without inventing a row or moving the graph cursor.
            self.select_row("graph-table", key, explicit=True)

    def next_match(self) -> None:
        if self._search_matches and self._search_page:
            index = (
                self._search_matches.index(self._history_key) + 1
                if self._history_key in self._search_matches
                else 0
            ) % len(self._search_matches)
            hit = self._search_page.hits[index]
            if hit.commit.oid in self._commits:
                self.jump_to(hit.commit.oid)
            else:
                self._request_history_page(
                    hit.history_cursor, self._history_position + 1, selected_oid=hit.commit.oid
                )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "graph-filter" and self.snapshot_data and not self._restoring_view:
            query = replace(self._search_query or SearchQuery(), text=event.value)
            self._start_scoped_search(query)

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
        elif ident == "search-criteria":
            self.action_search_criteria()
        elif ident == "search-more":
            self.action_search_more()
        elif ident == "search-stop":
            self.action_stop_search()
        elif ident == "search-widen":
            self.action_widen_search()
        elif ident == "graph-style":
            self.action_toggle_graph_style()
        elif ident == "graph-order":
            self.action_toggle_history_order()
        elif ident == "history-newer":
            self.action_history_newer()
        elif ident == "focus-history":
            self.action_focus_history()
        elif ident == "exit-focus":
            self.action_exit_focus()
        elif ident == "actions":
            self.action_command_picker()
        elif ident == "activity":
            self.action_activity()
        elif ident == "show-files":
            self.action_show_files()
        elif ident == "show-composer":
            self.action_show_composer()
        elif ident == "show-refs":
            self.action_show_refs()
        elif ident == "show-primary":
            self.back_to_graph()
            self.action_show_primary()
        elif ident == "back-to-graph":
            self.back_to_graph()
            self.query_one("#graph-table").focus()
        elif ident == "refresh":
            self.action_refresh()
        elif self.busy:
            return
        elif ident == "load-more":
            self.action_history_older()
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
        elif ident == "pull-menu":
            self.open_pull_menu(event.button)
        elif ident == "fetch":
            self.run_saved_pull_fetch()
        elif ident in {"pull", "push"}:
            self.network_action(ident)
        elif ident in {"stage", "unstage", "discard", "stage-all", "unstage-all"}:
            self.wip_action(ident)
        elif ident == "workspace-settings":
            self.action_workspace_settings()
        elif ident == "commit":
            self.commit_changes()
        elif ident in {"continue", "abort"} and self._managed_pull:
            self.run_pull(Action(ident))

    def action_command_picker(self) -> None:
        if self.busy or len(self.screen_stack) > 1:
            return
        actions = [
            ("open", "Open another repository"),
            ("refresh", "Refresh local repository"),
            ("create-branch", "Create and check out a branch"),
        ]
        snapshot = self.snapshot_data
        if snapshot and self.repo:
            actions.append(("refresh-prs", "Refresh PRs"))
            if snapshot.head and not snapshot.operation and not snapshot.conflicts:
                actions.append(("create-worktree", "Create worktree at HEAD…"))
            selection = self._sidebar_selection
            if (
                selection
                and selection.kind == "linked-worktree"
                and any(
                    str(worktree.path) == selection.target
                    and not worktree.bare
                    and not worktree.prunable
                    for worktree in snapshot.worktrees
                )
            ):
                actions.append(("open-worktree", "Open selected worktree"))
        if self._history_scope:
            actions.extend(
                [
                    ("focus-history", "Focus selected ref / commit (explicit scope)"),
                    ("graph-order", "Switch Topology / Author-time order"),
                    ("history-older", "Load older bounded history page"),
                    ("history-newer", "Return to newer history page"),
                    ("compare-commits", "Compare selected commit → captured endpoint"),
                    ("file-history", "File history at selected captured commit"),
                    ("blame", "Blame file at selected captured commit"),
                ]
            )
            if self._focus_return:
                actions.append(("exit-focus", "Exit focus / restore previous history context"))
        if self._sidebar_selection and self._sidebar_selection.oid:
            actions.extend(
                [
                    ("locate-ref", f"Locate {self._sidebar_selection.label} in unified graph"),
                    ("review-branch", "Review changes introduced by selected captured ref"),
                ]
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
                ("pull", pull_mode_label(self.pull_fetch_mode())),
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
                ("workspace-settings", "Workspace preferences, saved state and diagnostics [F4]"),
                ("diagnostics", "Preview / export local diagnostics (no upload)"),
                ("help", "Keyboard shortcuts and help"),
            ]
        )
        self.push_screen(ActionPicker("REPOSITORY ACTIONS", actions), self.handle_picked_action)

    def handle_picked_action(self, operation: str | None) -> None:
        if not operation:
            return
        if operation == "workspace-settings":
            self.action_workspace_settings()
        elif operation == "diagnostics":
            self.action_diagnostics()
        elif operation == "open":
            self.action_open_repository()
        elif operation == "refresh":
            self.action_refresh()
        elif operation in {"create-worktree", "open-worktree", "refresh-prs"}:
            if self.repo is None:
                return
            sidebar = self.query_one(ReferenceSidebar)
            if operation == "create-worktree":
                sidebar.post_message(ReferenceSidebar.WorktreeCreateRequested(self.repo.root))
            elif operation == "refresh-prs":
                sidebar.post_message(ReferenceSidebar.PullRequestsRequested(self.repo.root))
            elif self._sidebar_selection:
                sidebar.post_message(
                    ReferenceSidebar.WorktreeOpenRequested(self._sidebar_selection, self.repo.root)
                )
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
        elif operation == "graph-order":
            self.action_toggle_history_order()
        elif operation == "history-older":
            self.action_history_older()
        elif operation == "history-newer":
            self.action_history_newer()
        elif operation == "focus-history":
            self.action_focus_history()
        elif operation == "exit-focus":
            self.action_exit_focus()
        elif operation in {"compare-commits", "review-branch"}:
            self.action_compare_history(branch_review=operation == "review-branch")
        elif operation in {"file-history", "blame"}:
            self.action_file_investigation("history" if operation == "file-history" else "blame")
        elif operation == "pull":
            self.run_saved_pull_fetch()
        elif operation in {"continue", "abort"} and self._managed_pull:
            self.run_pull(Action(operation))
        elif operation in {"fetch", "push"}:
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

    def action_compare_history(self, *, branch_review: bool = False) -> None:
        service, scope = self._investigation, self._history_scope
        if service is None or scope is None or self.busy:
            return
        if branch_review:
            selected = self._sidebar_selection
            right = next(
                (
                    ref.oid
                    for ref in scope.captured_refs
                    if selected and ref.full_name == selected.target
                ),
                "",
            )
            if not selected or right != selected.oid or not scope.head_oid:
                self.status("Select a ref from this captured scope, or refresh explicitly first")
                return
            self.push_screen(
                ComparisonDialog(service, scope, scope.head_oid, right, "branch-review")
            )
            return
        left = self._selected_commit.oid if self._selected_commit else scope.head_oid
        if not left:
            self.status("Select a commit before comparing")
            return
        choices = {
            commit.oid: f"{commit.short_id} · {commit.subject}" for commit in self._commits.values()
        }
        for ref in scope.captured_refs:
            choices.setdefault(ref.oid, f"{ref.oid[:8]} · {ref.full_name}")

        def compare(right: str | None) -> None:
            if right in choices and service is self._investigation and scope is self._history_scope:
                self.push_screen(ComparisonDialog(service, scope, left, right, "endpoints"))

        self.push_screen(
            ActionPicker(f"COMPARE {left[:12]} → RIGHT ENDPOINT (captured)", list(choices.items())),
            compare,
        )

    def action_file_investigation(self, mode: str = "history") -> None:
        service, scope = self._investigation, self._history_scope
        if service is None or scope is None or self.busy:
            return
        revision = self._selected_commit.oid if self._selected_commit else scope.head_oid
        path = ""
        if self._active_diff:
            file, _, oid = self._active_diff
            revision = oid or scope.head_oid
            path = file.path
        if not revision:
            self.status("File investigation requires a captured commit")
            return

        def inspect(selected_path: str | None) -> None:
            if selected_path and service is self._investigation and scope is self._history_scope:
                self.push_screen(
                    FileInvestigationDialog(service, scope, revision, selected_path, mode)
                )

        if path:
            inspect(path)
        else:
            self.push_screen(
                PromptDialog(
                    f"{mode.title()} at {revision[:12]}", "Literal repository-relative file path"
                ),
                inspect,
            )

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

    def pull_fetch_mode(self) -> str:
        """Return a defensive default while a user settings file is being repaired."""
        mode = self._settings.get("pull_fetch_default", "pull-ff")
        return (
            mode if mode in {"fetch-all", "pull-ff", "pull-ff-only", "pull-rebase"} else "pull-ff"
        )

    def open_pull_menu(self, button: Button) -> None:
        if self.busy or len(self.screen_stack) > 1:
            return

        def selected(mode: str | None) -> None:
            # This is intentionally only a preference write: no fetch, plan, or
            # integration is scheduled by choosing from the menu.
            if mode is not None:
                self._save_view_preference("pull_fetch_default", mode)
                self._update_pull_button()
                self.status(f"Default pull/fetch action: {pull_mode_label(mode)}")

        self.push_screen(
            PullModeMenu(
                self.pull_fetch_mode(),
                screen_x=button.region.x,
                screen_y=button.region.y + button.region.height,
                plain=self._settings["plain_glyphs"],
            ),
            selected,
        )

    def _update_pull_button(self) -> None:
        if not self.is_mounted:
            return
        button = self.screen_stack[0].query_one("#fetch", Button)
        mode = self.pull_fetch_mode()
        button.label = "Fetch" if mode == "fetch-all" else "Pull"
        button.tooltip = pull_mode_label(mode) + ". Use the adjacent menu to change it."
        button.refresh(layout=True)

    def run_saved_pull_fetch(self) -> None:
        mode = self.pull_fetch_mode()
        if mode == "fetch-all":
            self.network_action("fetch")
            return
        self.run_pull(Action("pull", pull_mode=mode.removeprefix("pull-")))

    def run_pull(self, action: Action) -> None:
        """Fetch and freeze an exact guarded plan while the review owns the UI."""
        if self.busy or not self.repo or len(self.screen_stack) > 1:
            return
        if action.operation == "pull" and self._settings["offline"]:
            self.status(
                "Offline mode: network actions are unavailable until re-enabled in settings."
            )
            return
        self._mutation_epoch += 1
        self.set_busy(
            True,
            "Fetching and reviewing the configured upstream…"
            if action.operation == "pull"
            else f"Reviewing pull {action.operation}…",
        )
        self.run_worker(
            self.prepare_pull(action, self.repo), group="repository", exit_on_error=False
        )

    async def prepare_pull(self, action: Action, repo: Repository) -> None:
        coordinator = self._operations
        try:
            if coordinator is None or coordinator.repository is not repo:
                raise GitError("No current operation coordinator; reopen the repository.")
            plan: PullPlan = await asyncio.to_thread(coordinator.prepare_pull, action)
            if self.repo is not repo:
                await asyncio.to_thread(coordinator.cancel_pull, plan)
                self.set_busy(False)
                return
            if not plan.available:
                await asyncio.to_thread(coordinator.cancel_pull, plan)
                self.show_error(plan.reason or "This pull is unavailable.")
                self.set_busy(False)
                await self.load_snapshot(quiet=True)
                return
            if not plan.requires_confirmation:
                await self.complete_pull(plan, True, repo)
                return
            self.push_screen(
                PullReviewDialog(plan), lambda confirm: self.finish_pull(plan, confirm, repo)
            )
        except Exception as error:
            self.show_error(str(error))
            self.set_busy(False)
            # Fetch may have safely moved tracking refs before a later review
            # failure, so always redraw the current caches.
            await self.load_snapshot(quiet=True)

    def finish_pull(self, plan: PullPlan, confirm: bool, repo: Repository) -> None:
        if self.repo is not repo:
            self.set_busy(False)
            return
        self.run_worker(
            self.complete_pull(plan, confirm, repo), group="repository", exit_on_error=False
        )

    async def complete_pull(self, plan: PullPlan, confirm: bool, repo: Repository) -> None:
        coordinator = self._operations
        try:
            if coordinator is None or coordinator.repository is not repo:
                raise GitError("No current operation coordinator; reopen the repository.")
            if not confirm:
                await asyncio.to_thread(coordinator.cancel_pull, plan)
                self.status("Pull review cancelled; fetched tracking refs were retained.")
                return
            # Integration can run hooks or request native input. Never let a
            # Textual repaint compete with that terminal transaction.
            if not self.is_headless:
                failure: BaseException | None = None
                with self.suspend():
                    try:
                        result = coordinator.submit_pull(
                            plan, confirmed=plan.requires_confirmation and confirm
                        )
                    except BaseException as error:
                        failure = error
                if failure is not None:
                    raise failure
            else:
                result = await asyncio.to_thread(
                    coordinator.submit_pull, plan, confirmed=plan.requires_confirmation and confirm
                )
            self._last_error = ""
            self.status(result)
            self.notify(escape(result))
        except Exception as error:
            self.show_error(str(error))
        finally:
            self._mutation_epoch += 1
            self.set_busy(False)
            await self.load_snapshot(quiet=True)

    def network_action(self, operation: str, *, pull_mode: str = "") -> None:
        if self._settings["offline"]:
            self.status(
                "Offline mode: network actions are unavailable until re-enabled in settings."
            )
            return
        if not self.snapshot_data or not self.snapshot_data.remotes:
            self.notify(
                "No remote configured. Add one with git remote add, then refresh.",
                severity="warning",
            )
            return

        def run(remote: str | None = "") -> None:
            if remote is not None:
                values = {"remote": remote}
                if pull_mode:
                    values["pull_mode"] = pull_mode
                self.run_mutation(Action(operation, **values))

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
        message = self.query_one("#commit-message", TextArea).text
        if not message.strip():
            self.notify("Write a commit message first.", severity="warning")
            return
        if message == self._commit_template_seed:
            self.notify("Edit the commit template before committing.", severity="warning")
            return
        self.run_mutation(Action("commit", message=message))

    def run_mutation(
        self,
        action: Action,
        *,
        patch: PatchDocument | None = None,
        selection: tuple[int, int] | None = None,
    ) -> None:
        if self.busy or not self.repo or len(self.screen_stack) > 1:
            return
        self._mutation_epoch += 1
        self._pending_operation = (action.operation, datetime.now(UTC))
        self._detail_token += 1
        self._diff_token += 1
        self.set_busy(True, f"Running {action.operation}…")
        self.run_worker(
            self.mutate(action, patch=patch, selection=selection),
            group="repository",
            exit_on_error=False,
        )

    @staticmethod
    def _execute_reviewed(
        coordinator: OperationCoordinator,
        action: Action,
        *,
        patch: PatchDocument | None = None,
        selection: tuple[int, int] | None = None,
    ) -> str:
        plan = coordinator.plan(action, patch=patch, selection=selection)
        return coordinator.submit(plan)

    async def mutate(
        self,
        action: Action,
        *,
        patch: PatchDocument | None = None,
        selection: tuple[int, int] | None = None,
    ) -> None:
        try:
            coordinator = self._operations
            if coordinator is None or coordinator.repository is not self.repo:
                raise GitError("No current operation coordinator; reopen the repository.")
            if action.operation == "commit" and not self.is_headless:
                # A deliberate native-terminal handoff: no UI reader or repaint
                # may compete with hooks/signing. Run synchronously while suspended
                # so cancelling a UI waiter cannot resume over a live mutation.
                failure: BaseException | None = None
                with self.suspend():
                    try:
                        print(
                            "Rhizome: committing staged changes; hooks/signing may request input."
                        )
                        result = self._execute_reviewed(coordinator, action)
                    except BaseException as error:
                        # Textual 1.x resumes only on normal context exit. Restore
                        # the terminal/UI first, then report the native failure.
                        failure = error
                if failure is not None:
                    raise failure
            else:
                result = await asyncio.to_thread(
                    self._execute_reviewed, coordinator, action, patch=patch, selection=selection
                )
            # Successful app actions may deliberately show their resulting patch.
            # Failed/uncertain actions retain the old review and mark it stale.
            self._patch_document = None
            self._patch_stale = False
            self._last_error = ""
            self.status(result or f"{action.operation.title()} complete")
            # Textual toasts require markup strings, not Rich Text renderables.
            self.notify(escape(result or "Done"))
            if action.operation == "commit":
                editor = self.query_one("#commit-message", TextArea)
                if editor.text == action.message:
                    if self._save_commit_draft(""):
                        self._clear_visible_drafts()
                        editor.read_only = True
                        self._restoring_draft = True
                        try:
                            await self._seed_commit_template(coordinator.repository, editor)
                        finally:
                            self._restoring_draft = False
                            editor.read_only = False
                            self._update_composer_guidance()
                    else:
                        self.status(
                            "Commit succeeded; saved draft cleanup failed. Text retained; do not resubmit it."
                        )
                else:
                    self.status("Commit succeeded; newer composer text was retained.")
            self._record_diagnostic(
                "operation.finished", {"operation": action.operation, "status": "succeeded"}
            )
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
            self._record_diagnostic(
                "operation.finished",
                {
                    "operation": action.operation,
                    "status": "failed",
                    "error_type": type(error).__name__,
                },
            )
            self.show_error(str(error))
        finally:
            # Completion invalidates reads even when the resulting snapshot equals
            # the prior UI (or a failed hook changed content but not status flags).
            self._mutation_epoch += 1
            self._pending_operation = None
            await self.load_snapshot(quiet=True)

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = busy
        if message:
            self.status(message)
        self.update_controls()

    def update_controls(self) -> None:
        if not self.is_mounted:
            return
        workspace = self.screen_stack[0]
        for button in workspace.query(Button):
            if button.id and button.id.startswith("precision-"):
                continue  # The precision view owns selection/stale applicability.
            button.disabled = self.busy and button.id not in {
                "help",
                "search-next",
                "graph-style",
                "show-files",
                "show-composer",
                "show-refs",
                "show-primary",
                "activity",
            }
        workspace.query_one(PrecisionDiff).disabled = self.busy
        if self.snapshot_data:
            files = self.snapshot_data.files
            staged = sum(file.staged for file in files)
            workspace.query_one("#staged-summary", Static).update(
                f"{staged} staged file(s) · only index content will be committed"
            )
            selected = self._selected_file
            workspace.query_one("#stage", Button).disabled = (
                self.busy or not selected or selected[1]
            )
            workspace.query_one("#unstage", Button).disabled = (
                self.busy or not selected or not selected[1]
            )
            workspace.query_one("#discard", Button).disabled = (
                self.busy
                or not selected
                or selected[1]
                or selected[0].index == "?"
                or selected[0].conflicted
            )
            workspace.query_one("#stage-all", Button).disabled = self.busy or not any(
                f.unstaged for f in files
            )
            workspace.query_one("#unstage-all", Button).disabled = self.busy or not any(
                f.staged for f in files
            )
            workspace.query_one("#commit", Button).disabled = (
                self.busy
                or not staged
                or bool(self.snapshot_data.conflicts)
                or bool(self.snapshot_data.operation)
            )
            workspace.query_one("#continue", Button).disabled = (
                self.busy or self._managed_pull is None or bool(self.snapshot_data.conflicts)
            )
            workspace.query_one("#abort", Button).disabled = self.busy or self._managed_pull is None
            workspace.query_one("#fetch", Button).disabled = (
                self.busy or not self.snapshot_data.remotes or self._settings["offline"]
            )
            workspace.query_one("#pull-menu", Button).disabled = self.busy
            for ident in ("pull", "push"):
                workspace.query_one(f"#{ident}", Button).disabled = (
                    self.busy or not self.snapshot_data.remotes
                )
            workspace.query_one("#pop", Button).disabled = (
                self.busy or not self.snapshot_data.stashes
            )
        workspace.query_one("#search-next", Button).disabled = not self._search_matches
        workspace.query_one(ReferenceSidebar).set_busy(self.busy)

    def status(self, message: str) -> None:
        self._status_message = message.splitlines()[0] if message else "Ready"
        self._paint_status()

    def _set_refresh_error(self, message: str) -> None:
        message = message.splitlines()[0] if message else ""
        if message != self._refresh_error:
            self._refresh_error = message
            self._paint_status()

    def _paint_status(self) -> None:
        suffix = f" · Background refresh: {self._refresh_error}" if self._refresh_error else ""
        if self._settings_error:
            suffix += " · Settings unavailable: " + self._settings_error
        self.screen_stack[0].query_one("#status-line", Static).update(
            Text(self._status_message + suffix, no_wrap=True, overflow="ellipsis")
        )

    def show_error(self, message: str) -> None:
        if " is unavailable " in message:
            message += (
                " Inspect git status and use Git/editor explicitly for this workflow, then refresh."
                " External actions have no Rhizome checkpoint."
            )
        self.status("Git: " + message)
        if message != self._last_error:
            self.notify(escape(message), severity="error", timeout=12)
        self._last_error = message

    def action_open_repository(self) -> None:
        if not self.busy and len(self.screen_stack) == 1:
            self.push_screen(
                PromptDialog("OPEN LOCAL REPOSITORY", "/path/to/repository"), self.open_repository
            )

    def open_repository(
        self,
        path: str | None,
        *,
        worktree: bool = False,
        discard_unsaved: bool = False,
    ) -> None:
        if not path or self.busy or len(self.screen_stack) > 1:
            return
        if not discard_unsaved and self._has_unsaved_draft():
            self.push_screen(
                ActionPicker(
                    "Draft is not saved. Leave this workspace anyway?",
                    [("stay", "Stay with this draft"), ("leave", "Leave without saving")],
                ),
                lambda choice: (
                    self.open_repository(path, worktree=worktree, discard_unsaved=True)
                    if choice == "leave"
                    else None
                ),
            )
            return
        self._save_navigation_context()
        previous_repo = self.repo
        destination = Path(path).expanduser()
        self.set_busy(True, "Opening repository…")

        async def open_path() -> None:
            try:
                async with self._serial:
                    if worktree:
                        # A visible entry may have been removed externally since
                        # the last snapshot. Never discover its parent instead.
                        entries = (
                            await asyncio.to_thread(previous_repo.worktrees)
                            if previous_repo is not None
                            else ()
                        )
                        if not any(
                            item.path == destination and not item.bare and not item.prunable
                            for item in entries
                        ):
                            raise GitError(
                                "Worktree is no longer available. Refresh and try again."
                            )
                    repo = await asyncio.to_thread(Repository, destination)
                    if worktree and repo.root != destination.resolve():
                        raise GitError("Worktree moved or was removed. Refresh and try again.")
                    snapshot = await asyncio.to_thread(repo.snapshot, include_history=False)
                self.repo, self.path = repo, repo.root
                self._clear_reference_selection()
                self.back_to_graph()
                self._selected_commit = self._selected_file = None
                self._history_key = "wip"
                self.snapshot_data = None
                self._graph_geometry = ()
                self._investigation = InvestigationService(repo)
                self._history_scope = self._history_page = self._scope_snapshot = None
                self._focus_roots = ()
                self._focus_return = None
                self._search_query = None
                await self._restore_workspace_state(repo)
                self.history_order = (
                    "author-time"
                    if self._settings["graph_order"] == "chronological"
                    else "topology"
                )
                await self._capture_history(snapshot)
                self.query_one("#graph-filter", Input).value = ""
                await self.apply_snapshot(self._with_history(snapshot))
                self.status(f"Opened {repo.root}")
            except Exception as error:
                self.show_error(str(error))
            finally:
                self.set_busy(False)
                self._start_remote_refresh()

        self.run_worker(open_path(), group="repository", exit_on_error=False)

    def action_refresh(self) -> None:
        if len(self.screen_stack) == 1:
            self.request_refresh()

    def action_new_branch(self) -> None:
        if not self.busy and len(self.screen_stack) == 1:
            self.handle_picked_action("create-branch")

    def action_focus_filter(self) -> None:
        self.back_to_graph()
        self.query_one("#graph-filter", Input).focus()

    def action_clear_filter(self) -> None:
        if len(self.screen_stack) > 1:
            return
        workspace = self.screen_stack[0]
        if workspace.has_class("pane-constrained") and (
            workspace.has_class("responsive-inspector") or workspace.has_class("responsive-refs")
        ):
            self.action_show_primary()
            return
        if self._active_diff:
            self.back_to_graph()
            self.query_one("#graph-table").focus()
            return
        self._stop_search(clear_results=True)
        self._search_query = None
        self.query_one("#graph-filter", Input).value = ""
        self._clear_reference_selection()
        self.update_search(jump=False)
        self.query_one("#graph-table").focus()

    def _display_primary_panel(self, panel: str) -> None:
        workspace = self.screen_stack[0]
        for task, ident in (
            ("primary", "show-primary"),
            ("files", "show-files"),
            ("composer", "show-composer"),
            ("refs", "show-refs"),
        ):
            workspace.query_one(f"#{ident}", Button).set_class(
                self._task_view == task, "nav-active"
            )
        workspace.set_class(85 <= self.size.width < 120 and panel != "refs", "refs-auto-hidden")
        if workspace.has_class("pane-constrained"):
            workspace.remove_class(
                "responsive-inspector", "responsive-refs", "responsive-history-only"
            )
            workspace.add_class(
                {
                    "inspector": "responsive-inspector",
                    "refs": "responsive-refs",
                    "primary": "responsive-history-only",
                }[panel]
            )

    def action_show_files(self) -> None:
        if len(self.screen_stack) > 1:
            return
        workspace = self.screen_stack[0]
        self._task_view = "files"
        workspace.remove_class("task-composer")
        workspace.add_class("task-files")
        self.query_one("#inspector-panel").remove_class("collapsed")
        self.query_one("#inspector-splitter").remove_class("collapsed")
        self.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
        self._display_primary_panel("inspector")
        self.call_after_refresh(self.query_one("#wip-table").focus)

    def action_show_composer(self) -> None:
        if len(self.screen_stack) > 1:
            return
        workspace = self.screen_stack[0]
        self._task_view = "composer"
        workspace.remove_class("task-files")
        workspace.add_class("task-composer")
        self.query_one("#inspector-panel").remove_class("collapsed")
        self.query_one("#inspector-splitter").remove_class("collapsed")
        self.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
        self._display_primary_panel("inspector")
        self.call_after_refresh(self.query_one("#commit-message").focus)

    def action_show_refs(self) -> None:
        if len(self.screen_stack) > 1:
            return
        self._task_view = "refs"
        self.query_one("#refs-panel").remove_class("collapsed")
        self.query_one("#refs-splitter").remove_class("collapsed")
        self._display_primary_panel("refs")
        self.call_after_refresh(self.query_one("#ref-filter").focus)

    def action_show_primary(self) -> None:
        self._task_view = "primary"
        self._display_primary_panel("primary")
        if self._active_diff and self._patch_document:
            self.call_after_refresh(self.query_one(PrecisionDiff).focus_lines)
        elif self._active_diff:
            self.call_after_refresh(self.query_one("#center-diff-scroll").focus)
        else:
            self.call_after_refresh(self.query_one("#graph-table").focus)

    def action_toggle_refs(self) -> None:
        workspace = self.screen_stack[0]
        if 85 <= self.size.width < 120:
            if workspace.has_class("refs-auto-hidden"):
                self.action_show_refs()
            else:
                self.action_show_primary()
            return
        if workspace.has_class("pane-constrained"):
            if workspace.has_class("responsive-refs"):
                self.action_show_primary()
            else:
                self.action_show_refs()
            return
        refs = self.query_one("#refs-panel")
        splitter = self.query_one("#refs-splitter")
        refs.toggle_class("collapsed")
        splitter.toggle_class("collapsed")
        self.call_after_refresh(self._fit_panes)
        self.call_after_refresh(self.repaint_history)

    def action_toggle_inspector(self) -> None:
        workspace = self.screen_stack[0]
        if workspace.has_class("pane-constrained"):
            if workspace.has_class("responsive-inspector"):
                self.action_show_primary()
            elif self.query_one("#inspector-tabs", TabbedContent).active == "wip-tab":
                self.action_show_files()
            else:
                self.query_one("#inspector-panel").remove_class("collapsed")
                self._display_primary_panel("inspector")
                self.call_after_refresh(self.query_one(CommitFileTree).focus)
            return
        inspector = self.query_one("#inspector-panel")
        splitter = self.query_one("#inspector-splitter")
        inspector.toggle_class("collapsed")
        splitter.toggle_class("collapsed")
        self.call_after_refresh(self._fit_panes)
        self.call_after_refresh(self.repaint_history)

    def action_toggle_graph_style(self) -> None:
        if len(self.screen_stack) > 1:
            return
        self.graph_style = "compact" if self.graph_style == "smooth" else "smooth"
        shortcut = self._shortcut("toggle_graph_style")
        self.query_one("#graph-style", Button).label = Text(
            f"{self.graph_style.title()} [{shortcut}]"
        )
        self.repaint_history()
        self._save_view_preference("graph_style", self.graph_style)
        self.status(f"{self.graph_style.title()} graph · {shortcut} to switch")

    def action_activity(self) -> None:
        if len(self.screen_stack) > 1:
            return
        coordinator = self._operations

        def activity() -> tuple[OperationActivity, ...]:
            records = coordinator.activity() if coordinator else ()
            if coordinator is self._operations and self._pull_attention:
                records += (
                    OperationActivity(
                        id="workspace-pull-recovery",
                        operation="pull recovery",
                        state="blocked",
                        message=(
                            "Retained pull ownership discovered in this workspace, not a new Git operation.\n\n"
                            + self._pull_attention
                        ),
                        started=self._pull_attention_started or datetime.now(UTC),
                        elapsed=0.0,
                    ),
                )
            pending = self._pending_operation
            if pending and coordinator is self._operations:
                operation, started = pending
                if not any(
                    record.operation == operation and record.started >= started
                    for record in records
                ):
                    records += (
                        OperationActivity(
                            id="workspace-pending",
                            operation=operation,
                            state="queued",
                            message="Waiting for safe planning or another repository operation; not yet submitted.",
                            started=started,
                            elapsed=(datetime.now(UTC) - started).total_seconds(),
                        ),
                    )
            return records

        def context() -> tuple[str, ...]:
            return (
                "Local polling: " + ("reading" if self._refreshing else "idle"),
                "Hosted metadata: "
                + ("reading" if self._remote_reading is self.repo and self.repo else "idle"),
                "Network: offline"
                if self._settings["offline"]
                else (
                    f"Automatic refresh: {self.REMOTE_REFRESH_INTERVAL}s while active"
                    if self._settings["remote_refresh_enabled"]
                    else "Automatic remote refresh: paused"
                ),
            )

        self.push_screen(ActivityDialog(activity, context))

    def _has_unsaved_draft(self) -> bool:
        return bool(
            not self._state_excluded
            and self._state_error
            and self.screen_stack[0].query_one("#commit-message", TextArea).text
            != self._saved_draft
        )

    def action_quit(self) -> None:
        if self._has_unsaved_draft():
            self.push_screen(
                ActionPicker(
                    "Draft is not saved. Quit anyway?",
                    [("stay", "Stay with this draft"), ("leave", "Quit without saving")],
                ),
                lambda choice: self.exit() if choice == "leave" else None,
            )
            return
        self._save_navigation_context()
        self.exit()

    def action_help(self) -> None:
        if len(self.screen_stack) == 1:
            self.push_screen(HelpDialog(self._settings["keymap"]))

    def action_next_row(self) -> None:
        if isinstance(self.focused, (DataTable, CommitFileTree)):
            self.focused.action_cursor_down()

    def action_previous_row(self) -> None:
        if isinstance(self.focused, (DataTable, CommitFileTree)):
            self.focused.action_cursor_up()
