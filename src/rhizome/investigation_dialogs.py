"""Transient, captured-scope views for Git investigation results."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from .investigation import InvestigationService
from .models import (
    BlamePage,
    ComparisonIntent,
    ComparisonResult,
    FileHistoryPage,
    InvestigationScope,
    SearchQuery,
)


class ComparisonDialog(ModalScreen[None]):
    """Read a comparison without retargeting its captured revisions or scope.

    This screen deliberately takes OIDs rather than ref names: switching modes
    repeats the query with precisely the constructor endpoints, never with a
    currently moving branch tip.
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("b", "branch_review", "Branch review"),
        ("e", "endpoints", "Endpoints"),
    ]

    DEFAULT_CSS = """
    ComparisonDialog {
        align: center middle;
        background: transparent;
    }
    ComparisonDialog > #comparison-dialog {
        width: 104;
        max-width: 100%;
        height: 1fr;
        max-height: 95%;
        padding: 1 2;
        border: round $primary-muted;
        background: $surface;
        color: $text;
    }
    ComparisonDialog #comparison-scope {
        height: auto;
        color: $text-muted;
    }
    ComparisonDialog #comparison-request,
    ComparisonDialog #comparison-actual {
        height: auto;
        margin-top: 1;
        color: $text;
    }
    ComparisonDialog #comparison-modes {
        height: 3;
        margin-top: 1;
    }
    ComparisonDialog #comparison-modes Button {
        min-width: 17;
        margin-right: 1;
        background: $primary-muted;
        color: $text;
    }
    ComparisonDialog #comparison-modes Button.-active {
        background: $primary;
        color: $surface;
        text-style: bold;
    }
    ComparisonDialog #comparison-feedback {
        height: auto;
        margin-top: 1;
        color: $error;
    }
    ComparisonDialog #comparison-feedback.-loading { color: $text-muted; }
    ComparisonDialog #comparison-content {
        height: 1fr;
        min-height: 3;
        margin-top: 1;
        padding: 0 1;
        border: tall $primary-muted;
        background: $surface;
    }
    ComparisonDialog #comparison-files,
    ComparisonDialog #comparison-stat,
    ComparisonDialog #comparison-patch-label,
    ComparisonDialog #comparison-patch {
        height: auto;
    }
    ComparisonDialog #comparison-stat { margin-top: 1; color: $text-muted; }
    ComparisonDialog #comparison-patch-label { margin-top: 1; text-style: bold; }
    ComparisonDialog #comparison-patch { color: $text; }
    ComparisonDialog #comparison-buttons {
        height: 3;
        margin-top: 1;
        align: right middle;
    }
    ComparisonDialog #comparison-buttons Button { min-width: 10; }
    """

    def __init__(
        self,
        service: InvestigationService,
        scope: InvestigationScope,
        left_oid: str,
        right_oid: str,
        intent: ComparisonIntent = "endpoints",
    ) -> None:
        super().__init__()
        if intent not in ("branch-review", "endpoints"):
            raise ValueError("Comparison intent must be 'branch-review' or 'endpoints'.")
        self._service = service
        self._scope = scope
        # These are intentionally immutable captured inputs, not ref names to resolve later.
        self._left_oid = left_oid
        self._right_oid = right_oid
        self._intent: ComparisonIntent = intent
        self._result: ComparisonResult | None = None
        self._request_token = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="comparison-dialog"):
            yield Label("COMPARE REVISIONS", classes="dialog-title")
            yield Static(Text(f"Scope: {self._scope.label}"), id="comparison-scope")
            yield Static(self._requested_text(), id="comparison-request")
            with Horizontal(id="comparison-modes"):
                yield Button("Branch review", id="comparison-branch")
                yield Button("Endpoints", id="comparison-endpoints")
            yield Static("", id="comparison-feedback")
            with VerticalScroll(id="comparison-content"):
                yield Static("Actual comparison will appear here.", id="comparison-actual")
                yield Static("", id="comparison-files")
                yield Static("", id="comparison-stat")
                yield Static("", id="comparison-patch-label")
                yield Static("", id="comparison-patch")
            with Horizontal(id="comparison-buttons"):
                yield Button("Close", id="comparison-close", variant="primary")

    def on_mount(self) -> None:
        self._sync_mode_controls()
        self.query_one("#comparison-close", Button).focus()
        self._start_comparison()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_branch_review(self) -> None:
        self._set_intent("branch-review")

    def action_endpoints(self) -> None:
        self._set_intent("endpoints")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "comparison-close":
            self.action_close()
        elif event.button.id == "comparison-branch":
            self.action_branch_review()
        elif event.button.id == "comparison-endpoints":
            self.action_endpoints()

    def _set_intent(self, intent: ComparisonIntent) -> None:
        if intent == self._intent:
            return
        self._intent = intent
        self._sync_mode_controls()
        self._start_comparison()

    def _sync_mode_controls(self) -> None:
        for intent, selector in (
            ("branch-review", "#comparison-branch"),
            ("endpoints", "#comparison-endpoints"),
        ):
            button = self.query_one(selector, Button)
            active = intent == self._intent
            button.disabled = active
            button.set_class(active, "-active")

    def _start_comparison(self) -> None:
        self._request_token += 1
        token = self._request_token
        feedback = self.query_one("#comparison-feedback", Static)
        feedback.set_class(True, "-loading")
        feedback.update("Reading captured revisions…")
        self.run_worker(self._compare(token, self._intent), exit_on_error=False)

    async def _compare(self, token: int, intent: ComparisonIntent) -> None:
        try:
            result = await asyncio.to_thread(
                self._service.compare,
                self._scope,
                self._left_oid,
                self._right_oid,
                intent,
            )
        except Exception as error:
            if token != self._request_token or not self.is_mounted:
                return
            feedback = self.query_one("#comparison-feedback", Static)
            feedback.remove_class("-loading")
            suggestion = (
                " Use Endpoints for the exact left → right difference."
                if intent == "branch-review"
                else " The captured endpoints were not substituted."
            )
            feedback.update(Text(f"Comparison refused: {error}.{suggestion}"))
            return
        if token != self._request_token or not self.is_mounted:
            return
        self._result = result
        feedback = self.query_one("#comparison-feedback", Static)
        feedback.remove_class("-loading")
        feedback.update(
            "Captured scope changed; this result remains the captured comparison."
            if not result.state.current
            else ""
        )
        self._render_result(result)

    def _requested_text(self) -> Text:
        return Text(
            "Requested endpoints (fixed for this view)\n"
            f"Left:  {self._left_oid}\n"
            f"Right: {self._right_oid}"
        )

    def _render_result(self, result: ComparisonResult) -> None:
        merge_base = result.merge_base_oid or "not used for endpoint comparison"
        self.query_one("#comparison-actual", Static).update(
            Text(
                f"Actual {self._intent} comparison\n"
                f"Merge base: {merge_base}\n"
                f"Diff: {result.diff_from_oid} → {result.diff_to_oid}"
            )
        )
        files = "\n".join(self._file_line(file) for file in result.files)
        self.query_one("#comparison-files", Static).update(
            Text(f"Files ({len(result.files)})\n{files or 'No changed files'}")
        )
        self.query_one("#comparison-stat", Static).update(
            Text(result.stat or "No file statistics.")
        )
        patch_label = "PATCH — truncated by the display limit" if result.truncated else "PATCH"
        self.query_one("#comparison-patch-label", Static).update(patch_label)
        self.query_one("#comparison-patch", Static).update(
            Text(result.patch or "No textual patch.")
        )

    @staticmethod
    def _file_line(file: object) -> str:
        # FileChange is deliberately rendered as literal data, including unusual paths.
        status = f"{getattr(file, 'index', ' ')}{getattr(file, 'worktree', ' ')}".strip() or "M"
        path = str(getattr(file, "path", ""))
        original = str(getattr(file, "original_path", ""))
        return f"{status:2} {original + ' → ' if original else ''}{path}"


class FileInvestigationDialog(ModalScreen[None]):
    """Page captured file history or blame without changing the root investigation."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("h", "history", "History"),
        ("b", "blame", "Blame"),
        ("left", "newer", "Newer"),
        ("right", "older", "Older"),
    ]

    DEFAULT_CSS = """
    FileInvestigationDialog {
        align: center middle;
        background: transparent;
    }
    FileInvestigationDialog > #file-investigation-dialog {
        width: 104;
        max-width: 100%;
        height: 1fr;
        max-height: 95%;
        padding: 1 2;
        border: round $primary-muted;
        background: $surface;
        color: $text;
    }
    FileInvestigationDialog #file-investigation-scope,
    FileInvestigationDialog #file-investigation-request,
    FileInvestigationDialog #file-investigation-meta,
    FileInvestigationDialog #file-investigation-limitations {
        height: auto;
    }
    FileInvestigationDialog #file-investigation-scope,
    FileInvestigationDialog #file-investigation-meta,
    FileInvestigationDialog #file-investigation-limitations { color: $text-muted; }
    FileInvestigationDialog #file-investigation-request { margin-top: 1; }
    FileInvestigationDialog #file-investigation-controls {
        height: 3;
        margin-top: 1;
        align: left middle;
    }
    FileInvestigationDialog #file-investigation-controls Button {
        min-width: 11;
        margin-right: 1;
        background: $primary-muted;
        color: $text;
    }
    FileInvestigationDialog #file-investigation-controls Button.-active {
        background: $primary;
        color: $surface;
        text-style: bold;
    }
    FileInvestigationDialog #file-investigation-feedback {
        height: auto;
        margin-top: 1;
        color: $error;
    }
    FileInvestigationDialog #file-investigation-feedback.-loading { color: $text-muted; }
    FileInvestigationDialog #file-investigation-content {
        height: 1fr;
        min-height: 3;
        margin-top: 1;
        padding: 0 1;
        border: tall $primary-muted;
        background: $surface;
    }
    FileInvestigationDialog #file-investigation-entries { height: auto; margin-top: 1; }
    FileInvestigationDialog #file-investigation-close { margin-left: 1; }
    """

    def __init__(
        self,
        service: InvestigationService,
        scope: InvestigationScope,
        start_oid: str,
        path: str,
        mode: str = "history",
    ) -> None:
        super().__init__()
        if mode not in ("history", "blame"):
            raise ValueError("File investigation mode must be 'history' or 'blame'.")
        self._service = service
        self._scope = scope
        self._start_oid = start_oid
        self._path = path
        self._mode = mode
        # A trail is only opaque service cursors. The screen retains one page, never a growing log.
        self._trails: dict[str, list[str | None]] = {"history": [None], "blame": [None]}
        self._trail_indexes = {"history": 0, "blame": 0}
        self._page: FileHistoryPage | BlamePage | None = None
        self._page_mode: str | None = None
        self._request_token = 0
        self._loading = False

    def compose(self) -> ComposeResult:
        with Vertical(id="file-investigation-dialog"):
            yield Label("FILE INVESTIGATION", classes="dialog-title")
            yield Static(Text(f"Scope: {self._scope.label}"), id="file-investigation-scope")
            yield Static(self._request_text(), id="file-investigation-request")
            yield Static("", id="file-investigation-feedback")
            with VerticalScroll(id="file-investigation-content"):
                yield Static("Waiting for captured file data.", id="file-investigation-meta")
                yield Static("", id="file-investigation-limitations")
                yield Static("", id="file-investigation-entries")
            with Horizontal(id="file-investigation-controls"):
                yield Button("History", id="file-investigation-history")
                yield Button("Blame", id="file-investigation-blame")
                yield Button("← Newer", id="file-investigation-newer")
                yield Button("Older →", id="file-investigation-older")
                yield Button("Close", id="file-investigation-close", variant="primary")

    def on_mount(self) -> None:
        self._sync_controls()
        self.query_one("#file-investigation-close", Button).focus()
        self._start_page()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_history(self) -> None:
        self._set_mode("history")

    def action_blame(self) -> None:
        self._set_mode("blame")

    def action_newer(self) -> None:
        index = self._trail_indexes[self._mode]
        if index:
            self._trail_indexes[self._mode] = index - 1
            self._start_page()

    def action_older(self) -> None:
        if (
            self._page is None
            or self._page_mode != self._mode
            or self._loading
            or not self._page.state.current
            or self._page.complete
        ):
            return
        trail = self._trails[self._mode]
        index = self._trail_indexes[self._mode]
        next_cursor = self._page.next_cursor
        if next_cursor is None:
            return
        if index + 1 == len(trail):
            trail.append(next_cursor)
        self._trail_indexes[self._mode] = index + 1
        self._start_page()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {
            "file-investigation-history": self.action_history,
            "file-investigation-blame": self.action_blame,
            "file-investigation-newer": self.action_newer,
            "file-investigation-older": self.action_older,
            "file-investigation-close": self.action_close,
        }
        action = actions.get(str(event.button.id))
        if action is not None:
            action()

    def _set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self._sync_controls()
        self._start_page()

    def _start_page(self) -> None:
        self._request_token += 1
        token = self._request_token
        cursor = self._trails[self._mode][self._trail_indexes[self._mode]]
        self._loading = True
        self._sync_controls()
        feedback = self.query_one("#file-investigation-feedback", Static)
        feedback.set_class(True, "-loading")
        feedback.update("Reading captured file data…")
        self.run_worker(self._read_page(token, self._mode, cursor), exit_on_error=False)

    async def _read_page(self, token: int, mode: str, cursor: str | None) -> None:
        try:
            if mode == "history":
                page = await asyncio.to_thread(
                    self._service.file_history,
                    self._scope,
                    self._start_oid,
                    self._path,
                    cursor,
                    300,
                )
            else:
                page = await asyncio.to_thread(
                    self._service.blame, self._scope, self._start_oid, self._path, cursor, 300
                )
        except Exception as error:
            if token != self._request_token or not self.is_mounted:
                return
            self._loading = False
            feedback = self.query_one("#file-investigation-feedback", Static)
            feedback.remove_class("-loading")
            feedback.update(
                Text(
                    f"File investigation failed: {error}. Captured revision and path were not changed."
                )
            )
            self._sync_controls()
            return
        if token != self._request_token or not self.is_mounted:
            return
        self._loading = False
        self._page = page
        self._page_mode = mode
        feedback = self.query_one("#file-investigation-feedback", Static)
        feedback.remove_class("-loading")
        feedback.update(
            "Captured scope changed; paging stopped. Refresh or reselect from the root view."
            if not page.state.current
            else ""
        )
        self._render_page(page)
        self._sync_controls()

    def _sync_controls(self) -> None:
        for mode, selector in (
            ("history", "#file-investigation-history"),
            ("blame", "#file-investigation-blame"),
        ):
            button = self.query_one(selector, Button)
            active = mode == self._mode
            button.disabled = active
            button.set_class(active, "-active")
        page = self._page
        index = self._trail_indexes[self._mode]
        can_page = (
            page is not None
            and self._page_mode == self._mode
            and page.state.current
            and not self._loading
        )
        self.query_one("#file-investigation-newer", Button).disabled = not can_page or index == 0
        self.query_one("#file-investigation-older", Button).disabled = (
            not can_page or page is None or page.complete or page.next_cursor is None
        )

    def _request_text(self) -> Text:
        return Text(
            "Captured revision and path (fixed for this view)\n"
            f"Revision: {self._start_oid}\n"
            f"Path: {self._path}"
        )

    def _render_page(self, page: FileHistoryPage | BlamePage) -> None:
        limitations = ", ".join(page.limitations) or "none reported"
        completeness = (
            "Locally complete for this captured scope."
            if page.complete
            else "More locally available data can be read with Older."
        )
        if isinstance(page, FileHistoryPage):
            meta = (
                f"History · {completeness}\n"
                f"Rename following: {'Git heuristic' if page.follow_renames else 'off'}"
            )
            entries = (
                "\n\n".join(
                    "\n".join(
                        part
                        for part in (
                            f"{entry.commit.oid}  {entry.commit.author}  {entry.commit.date}",
                            entry.commit.subject,
                            f"Path: {entry.path}",
                            f"Previous path: {entry.previous_path}" if entry.previous_path else "",
                            f"Change: {entry.status}",
                        )
                        if part
                    )
                    for entry in page.entries
                )
                or "No file-history entries in this local captured scope."
            )
        else:
            meta = f"Blame · {completeness}"
            entries = (
                "\n\n".join(
                    "\n".join(
                        (
                            f"Line {line.final_line}: {line.commit_oid}  {line.author} <{line.email}>  {line.date}",
                            f"Original: {line.original_path}:{line.original_line}"
                            + ("  boundary" if line.boundary else ""),
                            line.text,
                        )
                    )
                    for line in page.lines
                )
                or "No blame lines are locally available."
            )
        self.query_one("#file-investigation-meta", Static).update(Text(meta))
        self.query_one("#file-investigation-limitations", Static).update(
            Text(f"Limitations: {limitations}")
        )
        self.query_one("#file-investigation-entries", Static).update(Text(entries))


class SearchCriteriaDialog(ModalScreen[SearchQuery | None]):
    """Collect search criteria only; the root view owns captured-scope searching."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+enter", "apply", "Search"),
    ]

    DEFAULT_CSS = """
    SearchCriteriaDialog {
        align: center middle;
        background: transparent;
    }
    SearchCriteriaDialog > #search-criteria-dialog {
        width: 72;
        max-width: 100%;
        height: 1fr;
        max-height: 95%;
        padding: 1 2;
        border: round $primary-muted;
        background: $surface;
        color: $text;
    }
    SearchCriteriaDialog #search-criteria-content { height: 1fr; min-height: 3; }
    SearchCriteriaDialog .search-criteria-label { height: auto; margin-top: 1; color: $text-muted; }
    SearchCriteriaDialog .search-criteria-hint { height: auto; color: $text-muted; }
    SearchCriteriaDialog #search-criteria-error { height: auto; margin-top: 1; color: $error; }
    SearchCriteriaDialog #search-criteria-buttons {
        height: 3;
        margin-top: 1;
        align: right middle;
    }
    SearchCriteriaDialog #search-criteria-buttons Button { min-width: 10; margin-left: 1; }
    """

    def __init__(self, query: SearchQuery | None = None) -> None:
        super().__init__()
        if query is not None and not isinstance(query, SearchQuery):
            raise ValueError("Search criteria must be a SearchQuery or None.")
        self._query = query or SearchQuery()

    def compose(self) -> ComposeResult:
        query = self._query
        with Vertical(id="search-criteria-dialog"):
            yield Label("SEARCH CAPTURED HISTORY", classes="dialog-title")
            with VerticalScroll(id="search-criteria-content"):
                yield Static(
                    "Search only the active captured scope. This dialog does not search, refresh, or change that scope.",
                    classes="search-criteria-hint",
                )
                yield Label("Text substring", classes="search-criteria-label")
                yield Input(
                    query.text,
                    placeholder="Message, author, email, SHA, or captured ref",
                    id="search-text",
                )
                yield Static(
                    "Text fields match as a case-insensitive OR.", classes="search-criteria-hint"
                )
                yield Label("Literal file path (optional)", classes="search-criteria-label")
                yield Input(query.path or "", placeholder="src/example.py", id="search-path")
                yield Static(
                    "Literal path only; no glob or pattern matching. Path and dates narrow text matches (AND).",
                    classes="search-criteria-hint",
                )
                yield Label("Author from (optional)", classes="search-criteria-label")
                yield Input(
                    self._format_date(query.authored_from),
                    placeholder="2026-01-01T09:30:00+00:00",
                    id="search-from",
                )
                yield Label("Author through (optional)", classes="search-criteria-label")
                yield Input(
                    self._format_date(query.authored_through),
                    placeholder="2026-01-31T17:00:00+00:00",
                    id="search-through",
                )
                yield Static(
                    "Inclusive ISO 8601 timestamps with a timezone are required. These use author dates, not commit dates.",
                    classes="search-criteria-hint",
                )
                yield Static("", id="search-criteria-error")
            with Horizontal(id="search-criteria-buttons"):
                yield Button("Cancel", id="search-criteria-cancel")
                yield Button("Search", id="search-criteria-apply", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#search-text", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        text = self.query_one("#search-text", Input).value.strip()
        path_value = self.query_one("#search-path", Input).value
        path = path_value if path_value else None
        try:
            authored_from = self._parse_date(
                "Author from", self.query_one("#search-from", Input).value
            )
            authored_through = self._parse_date(
                "Author through", self.query_one("#search-through", Input).value
            )
            if not (
                text
                or path is not None
                or authored_from is not None
                or authored_through is not None
            ):
                raise ValueError("Enter text, a literal path, or an author date.")
            if (
                authored_from is not None
                and authored_through is not None
                and authored_from > authored_through
            ):
                raise ValueError("Author from must be before or equal to author through.")
        except ValueError as error:
            self.query_one("#search-criteria-error", Static).update(Text(str(error)))
            return
        self.dismiss(SearchQuery(text, path, authored_from, authored_through))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_apply()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "search-criteria-apply":
            self.action_apply()
        else:
            self.action_cancel()

    @staticmethod
    def _format_date(value: datetime | None) -> str:
        return value.isoformat() if value is not None else ""

    @staticmethod
    def _parse_date(label: str, value: str) -> datetime | None:
        source = value.strip()
        if not source:
            return None
        try:
            parsed = datetime.fromisoformat(
                source.replace("Z", "+00:00") if source.endswith("Z") else source
            )
        except ValueError as error:
            raise ValueError(f"{label} must be an ISO 8601 timestamp with a timezone.") from error
        if parsed.utcoffset() is None:
            raise ValueError(f"{label} must include a timezone, such as +00:00.")
        return parsed
