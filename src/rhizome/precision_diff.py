"""A presentation-only, keyboard-first view of one canonical patch document."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import events, on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, DataTable, Input, Static

from .patches import PatchDocument, PatchLine
from .theme import LIGHT, Palette, theme_text


class _PatchTable(DataTable[str]):
    """DataTable which retains Shift state that DataTable's selection omits."""

    class LineClicked(Message):
        def __init__(self, row: int, previous_row: int, *, extend: bool) -> None:
            self.row = row
            self.previous_row = previous_row
            self.extend = extend
            super().__init__()

    async def _on_click(self, event: events.Click) -> None:
        row = event.style.meta.get("row", -1)
        previous_row = self.cursor_row
        extend = event.shift
        await super()._on_click(event)
        if isinstance(row, int) and row >= 0:
            self.post_message(self.LineClicked(row, previous_row, extend=extend))


@dataclass(frozen=True)
class _Match:
    start: int
    end: int


class PrecisionDiff(Vertical):
    """Inspect and select an immutable :class:`PatchDocument` without applying it.

    The parent owns every Git call. This widget only emits the same document object
    it was given plus inclusive displayed-line IDs, so presentation never becomes a
    source of patch data.
    """

    DEFAULT_CSS = """
    PrecisionDiff {
        height: 1fr;
        min-width: 0;
        background: $surface;
        color: $text;
        border: round $primary-muted;
    }
    PrecisionDiff .precision-heading {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $primary;
        text-style: bold;
    }
    PrecisionDiff #precision-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $panel;
    }
    PrecisionDiff.-stale #precision-status {
        background: $warning-muted;
        color: $text-warning;
        text-style: bold;
    }
    PrecisionDiff #precision-search-row, PrecisionDiff .precision-actions {
        height: 1;
        padding: 0 1;
        background: $panel;
    }
    PrecisionDiff #precision-search {
        width: 1fr;
        height: 1;
        min-width: 12;
        padding: 0 1;
        border: none;
        background: $surface;
        color: $text;
    }
    PrecisionDiff #precision-search:focus { background: $surface; }
    PrecisionDiff .precision-search-button,
    PrecisionDiff .precision-action {
        height: 1;
        min-width: 0;
        margin-left: 1;
        padding: 0 1;
        border: none;
        background: $primary-muted;
        color: $text;
    }
    PrecisionDiff .precision-action:hover, PrecisionDiff .precision-action:focus,
    PrecisionDiff .precision-search-button:hover, PrecisionDiff .precision-search-button:focus {
        background: $primary-muted;
        color: $text;
    }
    PrecisionDiff .precision-primary {
        background: $primary;
        color: $surface;
        text-style: bold;
    }
    PrecisionDiff .precision-primary:hover, PrecisionDiff .precision-primary:focus {
        background: $primary;
        color: $surface;
    }
    PrecisionDiff .precision-action:disabled {
        background: $panel;
        color: $text-muted;
        text-style: none;
    }
    PrecisionDiff #precision-lines {
        height: 1fr;
        min-height: 4;
        border: none;
        background: $surface;
        color: $text;
    }
    PrecisionDiff #precision-lines > .datatable--header { display: none; }
    PrecisionDiff #precision-lines > .datatable--cursor {
        background: $primary-muted;
        color: $text;
        text-style: none;
    }
    PrecisionDiff #precision-lines > .datatable--hover { background: $panel; }
    PrecisionDiff #precision-note {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $background;
    }
    PrecisionDiff.-narrow { border-left: none; border-right: none; }
    PrecisionDiff.-narrow #precision-search-row { padding: 0; }
    PrecisionDiff.-narrow #precision-search { min-width: 1; }
    PrecisionDiff.-narrow .precision-actions { padding: 0; }
    PrecisionDiff.-narrow .precision-action { margin-left: 0; }
    """

    BINDINGS = [
        Binding("v", "mark_range", "Mark", show=False),
        Binding("s", "stage", "Stage", show=False),
        Binding("u", "unstage", "Unstage", show=False),
        Binding("h", "select_hunk", "Select hunk", show=False),
        Binding("[", "previous_hunk", "Previous hunk", show=False),
        Binding("]", "next_hunk", "Next hunk", show=False),
        Binding("w", "toggle_whitespace", "Whitespace", show=False),
        Binding("ctrl+f,slash", "focus_search", "Find in diff", show=False),
        Binding("escape,b", "back", "Back", show=False),
    ]

    class ApplySelection(Message):
        """Request an inclusive, canonical displayed-ID selection be applied."""

        def __init__(self, document: PatchDocument, start: int, end: int) -> None:
            self.document = document
            self.start = start
            self.end = end
            super().__init__()

    class RefreshRequested(Message):
        def __init__(self, context: int) -> None:
            self.context = context
            super().__init__()

    class BackRequested(Message):
        pass

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._document: PatchDocument | None = None
        self._diff_context = 3
        self._stale = False
        self._anchor: int | None = None
        self._cursor_id: int | None = None
        self._show_whitespace = False
        self._plain_glyphs = False
        self._palette = LIGHT
        self._search_matches: list[_Match] = []
        self._search_index = -1
        self._base_lines: dict[int, Text] = {}
        self._painted_selection: set[int] = set()

    def compose(self):
        yield Static("PRECISION DIFF", classes="precision-heading")
        yield Static("Open a changed file to select exact lines.", id="precision-status")
        with Horizontal(id="precision-search-row"):
            yield Input(placeholder="Find literal text", id="precision-search")
            yield Button("Find", id="precision-find", classes="precision-search-button")
        yield _PatchTable(
            id="precision-lines",
            cursor_type="row",
            cursor_foreground_priority="renderable",
            zebra_stripes=False,
            show_header=False,
        )
        with Horizontal(classes="precision-actions"):
            yield Button(
                "Stage [s]", id="precision-stage", classes="precision-action precision-primary"
            )
            yield Button("Unstage [u]", id="precision-unstage", classes="precision-action")
            yield Button("Mark [v]", id="precision-mark", classes="precision-action")
            yield Button("Hunk [h]", id="precision-hunk", classes="precision-action")
            yield Button("Spaces [w]", id="precision-whitespace", classes="precision-action")
        with Horizontal(classes="precision-actions"):
            yield Button("Prev hunk", id="precision-previous-hunk", classes="precision-action")
            yield Button("Next hunk", id="precision-next-hunk", classes="precision-action")
            yield Button("More context", id="precision-context", classes="precision-action")
            yield Button("Refresh", id="precision-refresh", classes="precision-action")
            yield Button("Back", id="precision-hunk-back", classes="precision-action")
        yield Static(
            "↑↓ move  ·  v anchors a contiguous range  ·  w shows whitespace", id="precision-note"
        )

    def set_document(self, document: PatchDocument, *, context: int = 3) -> None:
        """Render *document* while retaining that exact immutable object for apply."""
        self._document = document
        self._diff_context = context
        self._stale = False
        self._anchor = None
        self._search_matches = []
        self._search_index = -1
        changed = next((line for line in document.lines if line.kind in {"add", "delete"}), None)
        self._cursor_id = (
            changed.id if changed else (document.lines[0].id if document.lines else None)
        )
        self._render_lines()
        self._refresh_chrome()
        self.call_after_refresh(self.focus_lines)

    def set_stale(self, stale: bool = True) -> None:
        self._stale = stale
        self.set_class(stale, "-stale")
        self._refresh_chrome()

    def set_palette(self, palette: Palette) -> None:
        """Rerender cosmetic colors without changing the captured diff state."""
        if palette == self._palette:
            return
        self._palette = palette
        if not self.is_mounted or self._document is None:
            return
        table = self.query_one("#precision-lines", _PatchTable)
        scroll = table.scroll_offset
        self._render_lines()
        self._refresh_chrome()
        self.call_after_refresh(table.scroll_to, scroll.x, scroll.y, animate=False)

    def set_plain_glyphs(self, enabled: bool) -> None:
        """Use ASCII only for generated diff and whitespace chrome.

        The captured document, canonical source text, selection, cursor, and
        scroll location are deliberately retained while its presentation changes.
        """
        enabled = bool(enabled)
        if enabled == self._plain_glyphs:
            return
        self._plain_glyphs = enabled
        if not self.is_mounted or self._document is None:
            return
        table = self.query_one("#precision-lines", _PatchTable)
        scroll = table.scroll_offset
        self._render_lines()
        self._refresh_chrome()
        self.call_after_refresh(table.scroll_to, scroll.x, scroll.y, animate=False)

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width <= 90, "-narrow")

    def focus_lines(self) -> None:
        """Put keyboard navigation back on the diff after an external panel action."""
        self.query_one("#precision-lines", _PatchTable).focus()

    def _typing(self) -> bool:
        return isinstance(self.app.focused, Input)

    def _render_lines(self) -> None:
        table = self.query_one("#precision-lines", _PatchTable)
        table.clear(columns=True)
        table.add_column("diff", key="line")
        self._base_lines = {}
        self._painted_selection = set()
        if not self._document:
            return
        cursor_row = 0
        for row, line in enumerate(self._document.lines):
            rendered = self._line_text(line)
            self._base_lines[line.id] = rendered
            table.add_row(rendered, key=str(line.id))
            if line.id == self._cursor_id:
                cursor_row = row
        if self._document.lines:
            table.cursor_coordinate = (cursor_row, 0)

    def _line_text(self, line: PatchLine) -> Text:
        marker = {
            "add": "+",
            "delete": "-" if self._plain_glyphs else "−",
            "context": " ",
            "hunk": "@",
            "meta": "." if self._plain_glyphs else "·",
        }[line.kind]
        number = line.new_line if line.kind != "delete" else line.old_line
        prefix = f"{number or '':>4} {marker} "
        display = line.text[1:] if line.kind in {"add", "delete", "context"} else line.text
        if self._show_whitespace:
            # This is a display-only transformation of generated whitespace
            # indicators, never a modification of captured patch source.
            tab, space = (">", ".") if self._plain_glyphs else ("⇥", "·")
            display = display.replace("\t", tab).replace(" ", space)
        color = {
            "add": LIGHT.addition,
            "delete": LIGHT.deletion,
            "hunk": LIGHT.graph[1],
            "meta": LIGHT.muted,
            "context": LIGHT.text,
        }[line.kind]
        text = Text(prefix + display, style=color, no_wrap=True, overflow="ellipsis")
        return theme_text(text, self._palette)

    @on(DataTable.RowSelected)
    def _line_selected(self, event: DataTable.RowSelected) -> None:
        self._set_cursor_row(event.cursor_row, extend=False)

    @on(DataTable.RowHighlighted)
    def _line_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._set_cursor_row(event.cursor_row, extend=False)

    @on(_PatchTable.LineClicked)
    def _line_clicked(self, event: _PatchTable.LineClicked) -> None:
        if (
            event.extend
            and self._anchor is None
            and self._document
            and 0 <= event.previous_row < len(self._document.lines)
        ):
            self._anchor = self._document.lines[event.previous_row].id
        self._set_cursor_row(event.row, extend=False)

    def _set_cursor_row(self, row: int, *, extend: bool) -> None:
        if not self._document or not 0 <= row < len(self._document.lines):
            return
        self._cursor_id = self._document.lines[row].id
        if extend and self._anchor is None:
            self._anchor = self._cursor_id
        self._refresh_chrome()

    def _range(self) -> tuple[int, int] | None:
        if self._anchor is None or self._cursor_id is None:
            return None
        return min(self._anchor, self._cursor_id), max(self._anchor, self._cursor_id)

    def _can_apply(self) -> tuple[bool, str]:
        if not self._document:
            return False, "Open a changed file first."
        if self._stale:
            return False, "Changed on disk — refresh before applying a selection."
        if not self._document.selectable:
            return False, self._document.reason or "This patch does not support line selection."
        selected = self._range()
        if selected is None:
            return False, "Mark a contiguous range with v, then apply it."
        start, end = selected
        lines = [line for line in self._document.lines if start <= line.id <= end]
        if not any(line.kind in {"add", "delete"} for line in lines):
            return False, "The selected range must include an added or removed line."
        return True, ""

    def _refresh_chrome(self) -> None:
        self.set_class(self._stale, "-stale")
        selected = self._range()
        marked = (
            {line_id for line_id in self._base_lines if selected[0] <= line_id <= selected[1]}
            if selected
            else set()
        )
        table = self.query_one("#precision-lines", _PatchTable)
        for line_id in marked ^ self._painted_selection:
            rendered = self._base_lines[line_id].copy()
            if line_id in marked:
                # Keep source text quiet and its add/remove colors intact.
                # The fill and explicit SELECTION status identify the marked range.
                rendered.stylize(f"on {self._palette.selection}")
            table.update_cell(str(line_id), "line", rendered)
        self._painted_selection = marked
        status = self.query_one("#precision-status", Static)
        stage = self.query_one("#precision-stage", Button)
        unstage = self.query_one("#precision-unstage", Button)
        if not self._document:
            status.update("Open a changed file to select exact lines.")
            stage.disabled = unstage.disabled = True
            return
        if self._stale:
            status.update("STALE — changed on disk. Refresh the patch; apply is disabled.")
        elif not self._document.selectable:
            status.update(self._document.reason or "This patch does not support line selection.")
        else:
            selected = self._range()
            if selected:
                changed = sum(
                    line.kind in {"add", "delete"} and line.id in marked
                    for line in self._document.lines
                )
                status.update(
                    f"SELECTION: {len(marked)} diff rows | {changed} changed lines | captured patch"
                    if self._plain_glyphs
                    else f"SELECTION: {len(marked)} diff rows · {changed} changed lines · captured patch"
                )
            else:
                status.update(
                    f"Cursor: displayed line {self._cursor_id} · press v, then move to extend"
                )
        allowed, _ = self._can_apply()
        stage.disabled = not (allowed and not self._document.staged)
        unstage.disabled = not (allowed and self._document.staged)

    def action_mark_range(self) -> None:
        if self._typing() or self._cursor_id is None:
            return
        self._anchor = None if self._anchor is not None else self._cursor_id
        self._refresh_chrome()

    def action_stage(self) -> None:
        if self._typing() or not self._document or self._document.staged:
            return
        self._emit_apply()

    def action_unstage(self) -> None:
        if self._typing() or not self._document or not self._document.staged:
            return
        self._emit_apply()

    def _emit_apply(self) -> None:
        allowed, reason = self._can_apply()
        if not allowed:
            self.query_one("#precision-status", Static).update(reason)
            return
        selection = self._range()
        if selection is not None and self._document is not None:
            self.post_message(self.ApplySelection(self._document, *selection))

    def action_select_hunk(self) -> None:
        if self._typing() or not self._document or self._cursor_id is None:
            return
        current = next((line for line in self._document.lines if line.id == self._cursor_id), None)
        if current is None or current.hunk is None:
            return
        lines = [line for line in self._document.lines if line.hunk == current.hunk]
        if lines:
            self._anchor, self._cursor_id = lines[0].id, lines[-1].id
            self._move_to_id(self._cursor_id)
            self._refresh_chrome()

    def action_previous_hunk(self) -> None:
        self._move_hunk(-1)

    def action_next_hunk(self) -> None:
        self._move_hunk(1)

    def _move_hunk(self, direction: int) -> None:
        if self._typing() or not self._document or self._cursor_id is None:
            return
        hunks = sorted({line.hunk for line in self._document.lines if line.hunk is not None})
        current = next(
            (line.hunk for line in self._document.lines if line.id == self._cursor_id), None
        )
        if current not in hunks:
            return
        target_index = max(0, min(len(hunks) - 1, hunks.index(current) + direction))
        target = hunks[target_index]
        first = next(line for line in self._document.lines if line.hunk == target)
        self._cursor_id, self._anchor = first.id, None
        self._move_to_id(first.id)
        self._refresh_chrome()

    def _move_to_id(self, line_id: int) -> None:
        if not self._document:
            return
        row = next((i for i, line in enumerate(self._document.lines) if line.id == line_id), None)
        if row is not None:
            self.query_one("#precision-lines", _PatchTable).cursor_coordinate = (row, 0)

    def action_toggle_whitespace(self) -> None:
        if self._typing():
            return
        self._show_whitespace = not self._show_whitespace
        self._render_lines()
        self._refresh_chrome()

    def action_back(self) -> None:
        if not self._typing():
            self.post_message(self.BackRequested())

    @on(Input.Submitted, "#precision-search")
    def _search_submitted(self) -> None:
        self._find_next()

    @on(Button.Pressed)
    def _button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "precision-stage": self.action_stage,
            "precision-unstage": self.action_unstage,
            "precision-mark": self.action_mark_range,
            "precision-hunk": self.action_select_hunk,
            "precision-previous-hunk": self.action_previous_hunk,
            "precision-next-hunk": self.action_next_hunk,
            "precision-context": self._request_more_context,
            "precision-refresh": self._request_refresh,
            "precision-whitespace": self.action_toggle_whitespace,
            "precision-hunk-back": self.action_back,
            "precision-find": self._find_next,
        }
        action = actions.get(event.button.id or "")
        if action:
            event.stop()
            action()

    def action_focus_search(self) -> None:
        if not self._typing():
            self.query_one("#precision-search", Input).focus()

    def _request_refresh(self) -> None:
        if self._document:
            self.post_message(self.RefreshRequested(self._diff_context))

    def _request_more_context(self) -> None:
        if self._document:
            self.post_message(self.RefreshRequested(self._diff_context + 3))

    def _find_next(self) -> None:
        query = self.query_one("#precision-search", Input).value
        if not query or not self._document:
            return
        self._search_matches = [
            _Match(line.id, line.id) for line in self._document.lines if query in line.text
        ]
        if not self._search_matches:
            self.query_one("#precision-status", Static).update(f"No literal match for {query!r}.")
            return
        self._search_index = (self._search_index + 1) % len(self._search_matches)
        line_id = self._search_matches[self._search_index].start
        self._cursor_id = line_id
        self._move_to_id(line_id)
        self.query_one("#precision-status", Static).update(
            f"Match {self._search_index + 1}/{len(self._search_matches)} for {query!r}."
        )
