#!/usr/bin/env python3
"""Throwaway V03 terminal interaction prototype. No repository access or persistence."""
from __future__ import annotations

import argparse
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, Static, TextArea

FILES = [("M", "src/rhizome/app.py", "3 unstaged · 1 staged"), ("M", "src/rhizome/app.tcss", "2 unstaged"), ("A", "docs/precision-notes.md", "1 unstaged")]
LINES = [
    "@@ -48,8 +48,10 @@ class RhizomeApp(App):",
    "  def refresh_workspace(self) -> None:",
    "-     self._status_message = 'Ready'",
    "+     # Keep this review stable while Git changes underneath it.",
    "+     self._status_message = 'Review ready'",
    "      self.refresh(recompose=False)",
    " ",
    "@@ -81,5 +83,8 @@ class RhizomeApp(App):",
    "+     if selection.is_stale:",
    "+         return Result.refused('Changed on disk')",
    "      return Result.ok()",
]

class V03Prototype(App[None]):
    TITLE = "Rhizome — V03 interaction prototype"
    CSS = """
    Screen { background: #f7fbfc; color: #14232c; }
    #header { height: 1; padding: 0 1; background: #e4f0f1; color: #10242b; text-style: bold; }
    #context { height: 1; padding: 0 1; background: #edf4f5; color: #36505b; }
    #notice { height: 1; padding: 0 1; background: #fff3df; color: #6b4300; }
    #body { height: 1fr; padding: 0 1; }
    .panel { border: round #a8c1c7; background: #ffffff; padding: 0 1; }
    .heading { height: 1; color: #006d77; text-style: bold; }
    #files { width: 27; min-width: 22; }
    #diff { width: 1fr; }
    #commit { width: 34; min-width: 28; }
    #commit-message { height: 5; border: round #8eabb2; background: #ffffff; color: #14232c; }
    #line-table { height: 1fr; border: none; background: #ffffff; }
    #line-table > .datatable--header { display: none; }
    #line-table > .datatable--cursor { background: #d8eeee; color: #14232c; }
    .file { height: 2; min-width: 0; padding: 0 1; background: #ffffff; color: #18323a; }
    .file:hover, .file:focus { background: #d8eeee; }
    .action { height: 1; min-width: 0; margin-right: 1; padding: 0 1; background: #d8eeee; color: #18323a; }
    .action:hover, .action:focus { background: #b9dfe1; color: #10242b; }
    .primary { background: #007c78; color: white; text-style: bold; }
    #selection { height: 2; color: #36505b; }
    #activity { height: 1; color: #48616c; }
    #back { height: 1; margin-bottom: 1; }
    #variant-switcher { height: 3; padding: 0 1; background: #e4f0f1; border-top: tall #b7cdd1; }
    #variant-title { height: 1; color: #36505b; }
    .variant { height: 1; min-width: 0; margin-right: 1; padding: 0 1; background: #dbecee; color: #18323a; }
    .variant.-active { background: #007c78; color: white; text-style: bold; }
    .muted { color: #48616c; }
    .narrow #body { padding: 0; }
    .narrow #files, .narrow #commit { display: none; }
    .narrow #diff { width: 1fr; }
    .narrow #diff-actions { height: 2; }
    .narrow #variant-switcher { height: 3; }
    .stack #body { layout: vertical; }
    .stack #files { width: 1fr; height: 5; }
    .stack #diff { width: 1fr; height: 1fr; }
    .stack #commit { width: 1fr; height: 7; }
    .stack #commit-message { height: 2; }
    .focus #body { layout: horizontal; }
    .focus #files { width: 20; }
    .focus #diff { width: 1fr; }
    .focus #commit { width: 42; }
    .focus #commit-message { height: 7; }
    /* At 80×24 one task owns the canvas; Files and Commit are explicit summons. */
    .narrow #files, .narrow #commit { display: none; }
    .narrow.files-open #files, .narrow.commit-open #commit { display: block; width: 1fr; }
    .narrow.files-open #diff, .narrow.files-open #commit,
    .narrow.commit-open #diff, .narrow.commit-open #files { display: none; }
    .narrow #mark { display: none; }
    """
    BINDINGS = [
        Binding("ctrl+shift+right", "next_variant", "Next prototype", priority=True),
        Binding("ctrl+shift+left", "previous_variant", "Previous prototype", priority=True),
        Binding("v", "mark_range", "Mark range", show=False),
        Binding("s", "stage", "Stage selected", show=False),
        Binding("u", "unstage", "Unstage selected", show=False),
        Binding("b,escape", "back", "Back", show=False),
        Binding("tab", "focus_next", "Next control", show=False),
    ]

    def __init__(self, variant: str = "A") -> None:
        super().__init__()
        self.variant = variant.upper()
        self.anchor: int | None = None
        self.selected = 3
        self.staged = False
        self.stale = False
        self.active_panel = "Diff"

    def compose(self) -> ComposeResult:
        yield Static("RHIZOME  /  synthetic precision review", id="header")
        yield Static("main  ·  3 files changed  ·  src/rhizome/app.py  ·  Worktree review", id="context")
        yield Static("Stable review — captured worktree state 7f3a. Simulated only; no Git operation will run.", id="notice")
        with Horizontal(id="body"):
            with Vertical(id="files", classes="panel"):
                yield Static("CHANGED FILES", classes="heading")
                for number, (state, path, detail) in enumerate(FILES):
                    yield Button(f"{state}  {path}\n   {detail}", classes="file", id=f"file-{number}")
                yield Static("Panel: Files  ·  click to inspect", classes="muted")
            with Vertical(id="diff", classes="panel"):
                yield Static("UNIFIED DIFF  /  src/rhizome/app.py", classes="heading")
                yield Static(id="selection")
                yield DataTable(id="line-table", cursor_type="row", zebra_stripes=False)
                with Horizontal(id="diff-actions"):
                    yield Button("Stage range [s]", id="stage", classes="action primary")
                    yield Button("Unstage [u]", id="unstage", classes="action")
                    yield Button("Mark start/end [v]", id="mark", classes="action")
                    yield Button("Simulate change", id="change", classes="action")
                    yield Button("Files", id="panel-files", classes="action")
                    yield Button("Commit", id="panel-commit", classes="action")
                yield Static(id="activity")
            with Vertical(id="commit", classes="panel"):
                yield Static("COMMIT", classes="heading")
                yield Static("Staged: 1 file · 3 lines" if self.staged else "Staged: 1 file · no selected lines", id="staged-summary")
                yield Label("Message (draft stays visible in this simulation)")
                yield TextArea("Keep review context stable", id="commit-message")
                with Horizontal():
                    yield Button("Commit (simulated)", id="commit-button", classes="action primary")
                    yield Button("Back [b]", id="back", classes="action")
                yield Static("Checkout is deliberately unavailable here.\nRisky actions: gated pending recovery proof.", classes="muted")
        with Vertical(id="variant-switcher"):
            yield Static("PROTOTYPE VARIANT — structural alternatives; Ctrl+Shift+←/→ cycles outside text entry", id="variant-title")
            with Horizontal():
                for name, label in [("A", "A · Familiar three-pane"), ("B", "B · Task stack"), ("C", "C · Focus rail")]:
                    yield Button(label, id=f"variant-{name}", classes="variant" + (" -active" if name == self.variant else ""))

    def on_mount(self) -> None:
        table = self.query_one("#line-table", DataTable)
        table.add_column("diff")
        for line in LINES:
            table.add_row(line)
        table.cursor_coordinate = (self.selected, 0)
        self._apply_layout()
        self._refresh_state()

    def on_resize(self) -> None:
        self._apply_layout()

    def _apply_layout(self) -> None:
        self.remove_class("stack", "focus", "narrow", "files-open", "commit-open")
        layout_class = {"A": None, "B": "stack", "C": "focus"}[self.variant]
        if layout_class:
            self.add_class(layout_class)
        if self.size.width <= 90:
            self.add_class("narrow")

    def _typing(self) -> bool:
        return isinstance(self.focused, (TextArea, Input))

    def _refresh_state(self) -> None:
        lo = min(self.anchor, self.selected) if self.anchor is not None else self.selected
        hi = max(self.anchor, self.selected) if self.anchor is not None else self.selected
        extent = f"Selection: lines {lo + 1}–{hi + 1}" if self.anchor is not None else f"Cursor: line {self.selected + 1} · press v to set range start"
        self.query_one("#selection", Static).update(extent + ("  ·  STALE — reselect before staging" if self.stale else "  ·  captured state"))
        self.query_one("#notice", Static).update("Changed on disk — selection is stale. Reselect before Stage." if self.stale else "Stable review — captured worktree state 7f3a. Simulated only; no Git operation will run.")
        self.query_one("#activity", Static).update("Activity: simulated stage complete; worktree selection remains visible." if self.staged else "Activity: idle · explicit Stage/Unstage only")
        self.query_one("#staged-summary", Static).update("Staged: 1 file · selected lines 4–5" if self.staged else "Staged: 1 file · no selected lines")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected = event.cursor_row
        self._refresh_state()

    def action_mark_range(self) -> None:
        if self._typing(): return
        self.anchor = self.selected if self.anchor is None else None
        self._refresh_state()

    def action_stage(self) -> None:
        if self._typing(): return
        if self.stale:
            self.notify("Refused: simulated source changed. Reselect a current range.", severity="warning")
        elif self.anchor is None:
            self.notify("Mark a contiguous range first (v), then Stage.", severity="warning")
        else:
            self.staged = True; self._refresh_state(); self.notify("Simulated: selected contiguous lines staged.")

    def action_unstage(self) -> None:
        if self._typing(): return
        self.staged = False; self._refresh_state(); self.notify("Simulated: selected lines unstaged; unrelated content retained.")

    def action_back(self) -> None:
        if self._typing(): return
        self.active_panel = "Diff"
        self.remove_class("files-open", "commit-open")
        self.query_one("#notice", Static).update("Back → Diff. File context and draft remain visible; no selection changed.")

    def action_next_variant(self) -> None:
        if not self._typing(): self._set_variant(("A", "B", "C")[("A", "B", "C").index(self.variant + "") + 1 if self.variant != "C" else 0])
    def action_previous_variant(self) -> None:
        if not self._typing(): self._set_variant(("A", "B", "C")[("A", "B", "C").index(self.variant) - 1])
    def _set_variant(self, variant: str) -> None:
        self.variant = variant; self._apply_layout()
        for name in "ABC": self.query_one(f"#variant-{name}").set_class(name == variant, "-active")
        self.notify(f"Variant {variant}: {dict(A='familiar three-pane', B='task stack', C='focus rail')[variant]}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ident = event.button.id or ""
        if ident.startswith("variant-"): self._set_variant(ident[-1])
        elif ident == "stage": self.action_stage()
        elif ident == "unstage": self.action_unstage()
        elif ident == "mark": self.action_mark_range()
        elif ident == "change": self.stale = True; self._refresh_state()
        elif ident == "panel-files":
            self.active_panel = "Files"; self.remove_class("commit-open"); self.add_class("files-open")
        elif ident == "panel-commit":
            self.active_panel = "Commit"; self.remove_class("files-open"); self.add_class("commit-open")
        elif ident == "back": self.action_back()
        elif ident == "commit-button": self.query_one("#notice", Static).update("Commit failed (simulated hook). Draft remains present; no commit was created.")
        elif ident.startswith("file-"): self.query_one("#notice", Static).update("File selected for inspection. Review context remains stable; no checkout occurs.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the throwaway V03 Textual interaction prototype.")
    parser.add_argument("--variant", choices="ABC", default="A", type=str.upper)
    V03Prototype(parser.parse_args().variant).run()
