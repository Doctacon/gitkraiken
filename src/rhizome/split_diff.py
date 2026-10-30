"""Read-only, cosmetic side-by-side rendering of unified diff text.

This deliberately does not interpret a patch as a staging document.  It only
keeps the supplied source text and gives callers a way to request that exact
text be shown by the precision/unified staging view.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import zip_longest

from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, DataTable, Static

from .theme import LIGHT, Palette, theme_text

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class UnifiedRequested(Message):
    """Request the original, unmodified patch for exact unified staging."""

    def __init__(self, patch: str) -> None:
        self.patch = patch
        super().__init__()


@dataclass(frozen=True)
class _Line:
    number: int | None
    marker: str
    text: str
    kind: str


@dataclass(frozen=True)
class _Row:
    old: _Line | None
    new: _Line | None
    unified: str | None = None
    kind: str = "context"


class SplitDiff(Vertical):
    """A deliberately approximate split inspection view of cosmetic diff text."""

    DEFAULT_CSS = """
    SplitDiff {
        height: 1fr;
        min-width: 0;
        background: $surface;
        color: $text;
        border: round $primary-muted;
    }
    SplitDiff .split-caption {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $primary;
        text-style: bold;
    }
    SplitDiff .split-headings {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    SplitDiff .split-headings > Static { width: 1fr; }
    SplitDiff #split-lines, SplitDiff #split-unified {
        height: 1fr;
        min-height: 3;
        border: none;
        background: $surface;
        color: $text;
    }
    SplitDiff #split-lines > .datatable--header,
    SplitDiff #split-unified > .datatable--header { display: none; }
    SplitDiff #split-lines > .datatable--cursor,
    SplitDiff #split-unified > .datatable--cursor { background: $primary-muted; color: $text; }
    SplitDiff #split-unified { display: none; }
    SplitDiff #split-narrow-notice { display: none; }
    SplitDiff .split-footer {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    SplitDiff .split-footer > Static { width: 1fr; min-width: 0; }
    SplitDiff .split-unified-button {
        height: 1;
        min-width: 0;
        margin-left: 1;
        padding: 0 1;
        border: none;
        background: $primary-muted;
        color: $text;
    }
    SplitDiff .split-unified-button:hover, SplitDiff .split-unified-button:focus {
        background: $primary;
        color: $surface;
    }
    SplitDiff.-narrow #split-lines, SplitDiff.-narrow .split-headings { display: none; }
    SplitDiff.-narrow #split-unified, SplitDiff.-narrow #split-narrow-notice { display: block; }
    """

    def __init__(self, *, id: str | None = "split-diff") -> None:
        super().__init__(id=id)
        self._patch = ""
        self._plain_glyphs = False
        self._palette = LIGHT
        self._rows: list[_Row] = []
        self._render_width = -1
        self._stageable = False

    def compose(self):
        yield Static(
            "Split inspection · read-only · Unified for exact staging", classes="split-caption"
        )
        with Horizontal(classes="split-headings"):
            yield Static("OLD")
            yield Static("NEW")
        yield DataTable(id="split-lines", cursor_type="row", show_header=False, zebra_stripes=False)
        yield Static(
            "Narrow view: unified diff shown; widen to 100 columns for split inspection.",
            id="split-narrow-notice",
        )
        yield DataTable(
            id="split-unified", cursor_type="row", show_header=False, zebra_stripes=False
        )
        with Horizontal(classes="split-footer"):
            yield Static(
                "Visual alignment only; long lines wrap when supported or are visibly clipped."
            )
            yield Button(
                "Unified for exact staging", id="split-show-unified", classes="split-unified-button"
            )

    def on_mount(self) -> None:
        self._render_tables()
        self._update_layout()

    def on_resize(self) -> None:
        self._update_layout()

    def set_patch(
        self,
        patch: str,
        *,
        plain_glyphs: bool = False,
        palette: Palette = LIGHT,
        stageable: bool = False,
    ) -> None:
        """Render *patch* cosmetically, retaining it verbatim for the request event."""
        self._patch = patch
        self._plain_glyphs = bool(plain_glyphs)
        self._palette = palette
        self._stageable = stageable
        self._rows = _split_rows(patch)
        if self.is_mounted:
            self._render_tables()
            self.set_stale(False)

    def set_stale(self, stale: bool = True) -> None:
        if self.is_mounted:
            eligible = self._stageable and not stale
            separator = " | " if self._plain_glyphs else " · "
            self.query_one(".split-caption", Static).update(
                ("STALE" + separator + "changed on disk" + separator if stale else "")
                + separator.join(
                    (
                        "Split inspection",
                        "read-only",
                        "Unified for exact staging" if eligible else "Unified source view",
                    )
                )
            )
            self.query_one("#split-show-unified", Button).label = (
                "Unified for exact staging" if eligible else "Unified view"
            )

    def clear(self) -> None:
        """Remove the current visual inspection and its retained source text."""
        self._patch = ""
        self._rows = []
        if self.is_mounted:
            self._render_tables()

    @on(Button.Pressed, "#split-show-unified")
    def _request_unified(self) -> None:
        self.post_message(UnifiedRequested(self._patch))

    def _update_layout(self) -> None:
        if not self.is_mounted:
            return
        self.set_class(self.size.width < 100, "-narrow")
        if self.size.width != self._render_width:
            self._render_tables()

    def _render_tables(self) -> None:
        split = self.query_one("#split-lines", DataTable)
        unified = self.query_one("#split-unified", DataTable)
        split.clear(columns=True)
        unified.clear(columns=True)
        self._render_width = self.size.width
        half = max(1, (self.size.width - 6) // 2)
        split.add_column("Old", width=half)
        split.add_column("New", width=half)
        unified.add_column("Unified", width=max(1, self.size.width - 4))
        if not self._rows:
            split.add_row(self._text("No patch loaded.", "meta"), self._text("", "meta"))
            unified.add_row(self._text("No patch loaded.", "meta"))
            return
        for row in self._rows:
            split.add_row(self._display(row.old), self._display(row.new), height=None)
        # A narrow fallback is the literal source order, not paired/interleaved changes.
        for raw in self._patch.splitlines():
            kind = (
                "add"
                if raw.startswith("+")
                else "delete"
                if raw.startswith("-")
                else "hunk"
                if raw.startswith("@@")
                else "context"
            )
            unified.add_row(self._text(raw, kind), height=None)

    def _display(self, line: _Line | None) -> Text:
        if line is None:
            return self._text("", "context")
        number = "     " if line.number is None else f"{line.number:>5}"
        marker = "." if self._plain_glyphs and line.marker == "·" else line.marker
        return self._text(f"{number} {marker} {line.text}", line.kind)

    def _text(self, value: str, kind: str) -> Text:
        color = {
            "add": LIGHT.addition,
            "delete": LIGHT.deletion,
            "hunk": LIGHT.warning,
            "meta": LIGHT.teal,
            "marker": LIGHT.muted,
        }.get(kind, LIGHT.text)
        text = Text(value, style=color, no_wrap=False, overflow="fold")
        return theme_text(text, self._palette)


def _split_rows(patch: str) -> list[_Row]:
    """Make a best-effort visual alignment without claiming patch semantics."""
    rows: list[_Row] = []
    old_number: int | None = None
    new_number: int | None = None
    previous_prefix = ""
    deleted: list[_Line] = []
    added: list[_Line] = []

    def flush_changes() -> None:
        nonlocal deleted, added
        for old, new in zip_longest(deleted, added):
            rows.append(_Row(old, new, kind="change"))
        deleted = []
        added = []

    for raw in patch.splitlines():
        hunk = _HUNK.match(raw)
        if hunk:
            flush_changes()
            old_number, new_number = int(hunk.group(1)), int(hunk.group(3))
            line = _Line(None, "@", raw, "hunk")
            rows.append(_Row(line, line, raw, "hunk"))
        elif raw.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
            flush_changes()
            old_number = new_number = None
            line = _Line(None, ".", raw, "meta")
            rows.append(_Row(line, line, raw, "meta"))
        elif raw.startswith("-") and old_number is not None:
            deleted.append(_Line(old_number, "-", raw[1:], "delete"))
            old_number += 1
        elif raw.startswith("+") and new_number is not None:
            added.append(_Line(new_number, "+", raw[1:], "add"))
            new_number += 1
        elif raw.startswith(" ") and old_number is not None and new_number is not None:
            flush_changes()
            old = _Line(old_number, " ", raw[1:], "context")
            new = _Line(new_number, " ", raw[1:], "context")
            rows.append(_Row(old, new, raw, "context"))
            old_number += 1
            new_number += 1
        elif raw.startswith("\\ No newline"):
            flush_changes()
            marker = _Line(None, "!", raw, "marker")
            # Git's marker describes the immediately preceding source line,
            # not necessarily both sides of the comparison.
            rows.append(
                _Row(
                    marker if previous_prefix != "+" else None,
                    marker if previous_prefix != "-" else None,
                    raw,
                    "marker",
                )
            )
        else:
            flush_changes()
            line = _Line(None, "·", raw, "meta")
            rows.append(_Row(line, line, raw, "meta"))
        previous_prefix = raw[:1]
    flush_changes()
    return rows


def _unified_row(row: _Row) -> str:
    if row.unified is not None:
        return row.unified
    if row.old is not None and row.new is not None:
        return f"{row.old.marker}{row.old.text}\n{row.new.marker}{row.new.text}"
    line = row.old or row.new
    return "" if line is None else f"{line.marker}{line.text}"
