"""Small, local-only workspace controls and diagnostics previews.

The application owns persistence, confirmation, and all I/O.  These screens only
collect an explicit request or display a supplied, already-safe preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from .settings import COMMANDS, validate_keymap


@dataclass(frozen=True)
class WorkspaceControlRequest:
    action: str
    scope: str = "workspace"
    settings: dict = field(default_factory=dict)


_FIELDS = (
    "offline",
    "remote_refresh_enabled",
    "remote_refresh_interval",
    "plain_glyphs",
    "diagnostics_enabled",
    "theme",
    "graph_style",
    "graph_order",
    "diff_layout",
    "pull_fetch_default",
    "keymap",
)

_DEFAULTS = {
    "offline": False,
    "remote_refresh_enabled": True,
    "remote_refresh_interval": 60,
    "plain_glyphs": False,
    "diagnostics_enabled": False,
    "theme": "auto",
    "graph_style": "smooth",
    "graph_order": "topo",
    "diff_layout": "unified",
    "pull_fetch_default": "pull-ff",
    "keymap": {},
}


class KeymapDialog(ModalScreen[dict[str, str] | None]):
    """Edit a candidate keymap only; persistence remains the caller's job."""

    BINDINGS = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    KeymapDialog { align: center middle; background: $text-muted 28%; }
    KeymapDialog > #keymap-dialog { width: 70; max-width: 100%; height: 19; max-height: 100%; padding: 1 2; border: round $primary-muted; background: $surface; color: $text; }
    KeymapDialog #keymap-intro, KeymapDialog #keymap-error { height: auto; margin: 1 0; }
    KeymapDialog #keymap-intro { color: $text-muted; }
    KeymapDialog #keymap-error { display: none; color: $error; text-style: bold; }
    KeymapDialog #keymap-error.-visible { display: block; }
    KeymapDialog #keymap-body { height: 1fr; min-height: 6; }
    KeymapDialog .keymap-row { height: 3; min-height: 3; padding: 0 1; }
    KeymapDialog .keymap-label { width: 16; content-align: left middle; }
    KeymapDialog Select, KeymapDialog Input { width: 1fr; min-width: 16; }
    KeymapDialog .keymap-actions, KeymapDialog .dialog-buttons { height: 3; }
    KeymapDialog .keymap-actions Button { width: 1fr; min-width: 0; margin-right: 1; padding: 0 1; }
    KeymapDialog .dialog-buttons { align: right middle; }
    KeymapDialog .dialog-buttons Button { min-width: 10; margin-left: 1; }
    """

    def __init__(self, keymap: dict | None = None) -> None:
        super().__init__()
        self._keymap = dict(keymap or {})
        self._commands = tuple({command.id: command for command in COMMANDS}.values())

    def compose(self) -> ComposeResult:
        options = [(f"{item.description} ({item.id})", item.id) for item in self._commands]
        with Vertical(id="keymap-dialog"):
            yield Label("EDIT KEY BINDINGS", classes="dialog-title")
            yield Static(
                "Use Textual key names, for example ctrl+shift+p. Commas assign more than one key. "
                "Leave Keys blank to restore the displayed default.",
                id="keymap-intro",
            )
            yield Static("", id="keymap-error")
            with VerticalScroll(id="keymap-body"):
                with Horizontal(classes="keymap-row"):
                    yield Label("Command", classes="keymap-label")
                    yield Select(
                        options, value=self._commands[0].id, allow_blank=False, id="keymap-command"
                    )
                with Horizontal(classes="keymap-row"):
                    yield Label("Keys", classes="keymap-label")
                    yield Input(id="keymap-keys", placeholder="ctrl+shift+p")
                with Horizontal(classes="keymap-actions"):
                    yield Button("Assign", id="keymap-assign", variant="primary")
                    yield Button("Reset selected", id="keymap-reset-selected")
                    yield Button("Reset all", id="keymap-reset-all")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="keymap-close")
                yield Button("Apply", id="keymap-apply", variant="primary")

    def on_mount(self) -> None:
        self._show_selected()
        self.query_one("#keymap-command", Select).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def _selected(self) -> str:
        return str(self.query_one("#keymap-command", Select).value)

    def _show_selected(self) -> None:
        command_id = self._selected()
        command = next(command for command in self._commands if command.id == command_id)
        keys = self.query_one("#keymap-keys", Input)
        keys.value = self._keymap.get(command_id, "")
        keys.placeholder = f"Default: {command.keys}"

    def _error(self, message: str = "") -> None:
        error = self.query_one("#keymap-error", Static)
        error.update(message)
        error.set_class(bool(message), "-visible")

    def _assign(self) -> bool:
        command_id, keys = self._selected(), self.query_one("#keymap-keys", Input).value.strip()
        candidate = dict(self._keymap)
        if keys:
            candidate[command_id] = keys
        else:
            candidate.pop(command_id, None)
        try:
            self._keymap = validate_keymap(candidate)
        except ValueError as error:
            self._error(str(error))
            self.query_one("#keymap-keys", Input).focus()
            return False
        self._error()
        self._show_selected()
        return True

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "keymap-command":
            self._show_selected()
            self._error()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = str(event.button.id)
        if button_id == "keymap-close":
            self.action_close()
        elif button_id == "keymap-assign":
            self._assign()
        elif button_id == "keymap-reset-selected":
            self._keymap.pop(self._selected(), None)
            self._show_selected()
            self._error()
        elif button_id == "keymap-reset-all":
            self._keymap = {}
            self._show_selected()
            self._error()
        elif button_id == "keymap-apply" and self._assign():
            self.dismiss(dict(self._keymap))


class WorkspaceControlsDialog(ModalScreen[WorkspaceControlRequest | None]):
    """A deliberately small set of safe, visible workspace preferences."""

    BINDINGS = [("escape", "close", "Close")]

    DEFAULT_CSS = """
    WorkspaceControlsDialog { align: center middle; background: $text-muted 28%; }
    WorkspaceControlsDialog > #workspace-controls-dialog {
        width: 90%; min-width: 76; max-width: 100%;
        height: 90%; min-height: 22; max-height: 100%;
        padding: 1 2; border: round $primary-muted; background: $surface; color: $text;
    }
    WorkspaceControlsDialog.compact-controls > #workspace-controls-dialog { padding: 0 1; }
    WorkspaceControlsDialog .dialog-title { height: 1; margin-bottom: 0; }
    WorkspaceControlsDialog #controls-body { height: 1fr; min-height: 4; }
    WorkspaceControlsDialog #controls-intro,
    WorkspaceControlsDialog #settings-path { height: auto; margin: 1 0; color: $text-muted; }
    WorkspaceControlsDialog #controls-offline-policy { height: auto; margin: 0 1; color: $text-muted; }
    WorkspaceControlsDialog #settings-path, WorkspaceControlsDialog #controls-offline-policy {
        border-left: tall $primary-muted; padding-left: 1;
    }
    WorkspaceControlsDialog #controls-error { display: none; color: $error; text-style: bold; }
    WorkspaceControlsDialog #controls-error.-visible { display: block; height: auto; margin: 1 0; }
    WorkspaceControlsDialog .setting-row {
        height: 3; min-height: 3; padding: 0 1; align: left middle;
    }
    WorkspaceControlsDialog .setting-label { width: 1fr; height: 3; content-align: left middle; }
    WorkspaceControlsDialog Select { width: 30; min-width: 30; }
    WorkspaceControlsDialog #controls-body {
        scrollbar-size: 1 1;
        scrollbar-background: $surface; scrollbar-color: $foreground-muted 35%;
    }
    WorkspaceControlsDialog Input { width: 10; min-width: 10; }
    WorkspaceControlsDialog Switch { width: 9; min-width: 9; }
    WorkspaceControlsDialog .setting-row Button { height: 3; }
    WorkspaceControlsDialog #settings-columns { height: auto; margin-top: 1; }
    WorkspaceControlsDialog .settings-column { width: 1fr; height: auto; padding-right: 2; }
    WorkspaceControlsDialog #settings-columns.-narrow { layout: vertical; }
    WorkspaceControlsDialog #settings-columns.-narrow .settings-column { width: 100%; }
    WorkspaceControlsDialog .exclusion-status {
        height: auto; margin: 1 0; padding: 0 1; border-left: tall $warning;
        background: $warning-muted; color: $text-warning; text-style: bold;
    }
    WorkspaceControlsDialog .control-actions { height: auto; margin-top: 1; }
    WorkspaceControlsDialog .control-action-row { height: 1; margin-bottom: 1; }
    WorkspaceControlsDialog .control-action-row Button {
        width: 1fr; min-width: 0; margin-right: 1; padding: 0 1;
    }
    WorkspaceControlsDialog .control-action-row Button:last-of-type { margin-right: 0; }
    WorkspaceControlsDialog .control-action-single Button { width: 1fr; min-width: 0; padding: 0 1; }
    WorkspaceControlsDialog .dialog-buttons { height: 3; align: right middle; }
    WorkspaceControlsDialog .dialog-buttons Button { min-width: 10; margin-left: 1; }
    """

    def __init__(
        self,
        *,
        global_settings: dict,
        overrides: dict,
        excluded: bool,
        state_error: str = "",
        settings_path: str = "",
    ) -> None:
        super().__init__()
        self._global = {name: global_settings.get(name, _DEFAULTS[name]) for name in _FIELDS}
        self._overrides = {name: overrides[name] for name in _FIELDS if name in overrides}
        self._excluded = excluded
        self._state_error = state_error
        self._settings_path = settings_path

    def _values_for(self, scope: str) -> dict:
        values = self._global.copy()
        if scope == "workspace":
            values.update(self._overrides)
            # This is the effective value, not an editable workspace override.
            values["offline"] = bool(self._global["offline"] or values["offline"])
        return values

    def compose(self) -> ComposeResult:
        values = self._values_for("workspace")
        with Vertical(id="workspace-controls-dialog"):
            yield Label("SETTINGS", classes="dialog-title")
            with VerticalScroll(id="controls-body"):
                yield Static(
                    self._state_error,
                    id="controls-error",
                    classes="-visible" if self._state_error else "",
                )
                with Horizontal(classes="setting-row"):
                    yield Label("Apply settings to", classes="setting-label")
                    yield Select(
                        [("This workspace", "workspace"), ("All workspaces", "global")],
                        value="workspace",
                        allow_blank=False,
                        id="controls-scope",
                    )
                with Horizontal(id="settings-columns"):
                    with Vertical(classes="settings-column"):
                        yield Label("NETWORK & PRIVACY", classes="dialog-title")
                        yield self._toggle("Work offline", "offline", bool(values["offline"]))
                        yield Static("", id="controls-offline-policy")
                        yield self._toggle(
                            "Refresh remote state automatically",
                            "remote-refresh-enabled",
                            bool(values["remote_refresh_enabled"]),
                        )
                        with Horizontal(classes="setting-row"):
                            yield Label("Refresh interval (seconds)", classes="setting-label")
                            yield Input(
                                str(values["remote_refresh_interval"]), id="remote-refresh-interval"
                            )
                        yield self._toggle(
                            "Keep local diagnostics",
                            "diagnostics-enabled",
                            bool(values["diagnostics_enabled"]),
                        )
                    with Vertical(classes="settings-column"):
                        yield Label("APPEARANCE & GRAPH", classes="dialog-title")
                        yield self._toggle(
                            "Use plain text glyphs", "plain-glyphs", bool(values["plain_glyphs"])
                        )
                        yield self._select_row(
                            "Theme",
                            "theme",
                            [
                                ("Auto (light fallback)", "auto"),
                                ("Light", "light"),
                                ("Dark", "dark"),
                                ("High contrast", "high-contrast"),
                            ],
                            values["theme"],
                        )
                        yield self._select_row(
                            "Graph style",
                            "graph-style",
                            [("Smooth", "smooth"), ("Compact", "compact")],
                            values["graph_style"],
                        )
                        yield self._select_row(
                            "Graph order",
                            "graph-order",
                            [("Topology", "topo"), ("Chronological", "chronological")],
                            values["graph_order"],
                        )
                        yield self._select_row(
                            "Diff layout",
                            "diff-layout",
                            [("Unified", "unified"), ("Split (read-only)", "split")],
                            values["diff_layout"],
                        )
                        yield self._select_row(
                            "Default pull/fetch action",
                            "pull-fetch-default",
                            [
                                ("Fetch all", "fetch-all"),
                                ("Pull — ff if possible", "pull-ff"),
                                ("Pull — ff only", "pull-ff-only"),
                                ("Pull — rebase", "pull-rebase"),
                            ],
                            values["pull_fetch_default"],
                        )
                        with Horizontal(classes="setting-row"):
                            yield Label("Keyboard shortcuts", classes="setting-label")
                            yield Button("Edit key bindings", id="edit-keybindings")
                yield Static(
                    "SESSION-ONLY EXCLUSION: "
                    + (
                        "this workspace is not being saved; drafts, view and preferences end with this session."
                        if self._excluded
                        else "exclude this workspace to keep drafts, view and preferences for this session only."
                    ),
                    classes="exclusion-status dialog-detail",
                )
                yield Static(
                    "Only an exclusion marker is saved for preferences. Existing pull recovery is kept; excluded workspaces cannot start divergent pulls.",
                    classes="dialog-detail",
                )
                with Vertical(classes="control-actions"):
                    with Horizontal(classes="control-action-row"):
                        yield Button("Local diagnostics", id="diagnostics")
                        yield Button("Reset global preferences", id="reset-global")
                    with Horizontal(classes="control-action-row"):
                        yield Button("Reset workspace overrides", id="reset-overrides")
                        yield Button("Clear saved drafts", id="clear-drafts")
                    with Horizontal(classes="control-action-row"):
                        yield Button("Reset saved view", id="reset-view")
                        yield Button("Forget this workspace", id="forget-workspace")
                    with Horizontal(classes="control-action-row control-action-single"):
                        yield Button(
                            "Include workspace" if self._excluded else "Exclude workspace",
                            id="toggle-exclusion",
                        )
                    if self._settings_path:
                        with Horizontal(classes="control-action-row control-action-single"):
                            yield Button("Reload settings", id="reload-settings")
                yield Static(
                    "Choose local refresh and display preferences. Apply changes to this workspace or your defaults.",
                    id="controls-intro",
                )
                if self._settings_path:
                    yield Static(
                        f"Editable user settings file: {self._settings_path}", id="settings-path"
                    )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="controls-close")
                yield Button("Apply settings", id="controls-apply", variant="primary")

    def _toggle(self, label: str, control_id: str, value: bool) -> Horizontal:
        return Horizontal(
            Label(label, classes="setting-label"),
            Switch(value=value, id=control_id),
            classes="setting-row",
        )

    def _select_row(
        self, label: str, control_id: str, options: list[tuple[str, str]], value: str
    ) -> Horizontal:
        return Horizontal(
            Label(label, classes="setting-label"),
            Select(options, value=value, allow_blank=False, id=control_id),
            classes="setting-row",
        )

    def on_mount(self) -> None:
        self._update_settings_layout()
        self._update_offline_policy("workspace")
        self.query_one("#controls-scope", Select).focus()

    def on_resize(self, event: events.Resize) -> None:
        self._update_settings_layout()

    def _update_settings_layout(self) -> None:
        # Two columns make generous terminals easy to scan; a single column keeps
        # every label and select legible on an 80-column terminal.
        narrow = self.size.width < 110
        self.set_class(narrow or self.size.height < 32, "compact-controls")
        self.query_one("#settings-columns").set_class(narrow, "-narrow")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "controls-scope":
            return
        scope = str(event.value)
        self._set_values(self._values_for(scope))
        self._update_offline_policy(scope)

    def _update_offline_policy(self, scope: str) -> None:
        locked = scope == "workspace" and bool(self._global["offline"])
        offline = self.query_one("#offline", Switch)
        offline.disabled = locked
        policy = self.query_one("#controls-offline-policy", Static)
        if locked:
            policy.update(
                "GLOBAL OFFLINE IS ON: it stops all network work. This workspace cannot bypass it; "
                "choose All workspaces to turn it off."
            )
        elif scope == "global":
            policy.update(
                "Global offline is the master stop for network work. Turning it off can resume eligible workspaces."
            )
        else:
            policy.update("Global offline overrides workspace settings.")

    def _set_values(self, values: dict) -> None:
        self.query_one("#offline", Switch).value = bool(values["offline"])
        self.query_one("#remote-refresh-enabled", Switch).value = bool(
            values["remote_refresh_enabled"]
        )
        self.query_one("#remote-refresh-interval", Input).value = str(
            values["remote_refresh_interval"]
        )
        self.query_one("#plain-glyphs", Switch).value = bool(values["plain_glyphs"])
        self.query_one("#diagnostics-enabled", Switch).value = bool(values["diagnostics_enabled"])
        for control_id, name in (
            ("#theme", "theme"),
            ("#graph-style", "graph_style"),
            ("#graph-order", "graph_order"),
            ("#diff-layout", "diff_layout"),
            ("#pull-fetch-default", "pull_fetch_default"),
        ):
            self.query_one(control_id, Select).value = values[name]

    def _settings(self) -> dict | None:
        try:
            interval = int(self.query_one("#remote-refresh-interval", Input).value.strip())
            if interval < 1:
                raise ValueError
        except ValueError:
            error = self.query_one("#controls-error", Static)
            error.update("Refresh interval must be a whole number of at least 1.")
            error.add_class("-visible")
            self.query_one("#remote-refresh-interval", Input).focus()
            return None
        scope = str(self.query_one("#controls-scope", Select).value)
        # A locked effective value must not overwrite a dormant workspace
        # preference; the global master stop is applied by the app.
        offline = (
            bool(self._overrides.get("offline", False))
            if scope == "workspace" and self._global["offline"]
            else self.query_one("#offline", Switch).value
        )
        return {
            "offline": offline,
            "remote_refresh_enabled": self.query_one("#remote-refresh-enabled", Switch).value,
            "remote_refresh_interval": interval,
            "plain_glyphs": self.query_one("#plain-glyphs", Switch).value,
            "diagnostics_enabled": self.query_one("#diagnostics-enabled", Switch).value,
            "theme": str(self.query_one("#theme", Select).value),
            "graph_style": str(self.query_one("#graph-style", Select).value),
            "graph_order": str(self.query_one("#graph-order", Select).value),
            "diff_layout": str(self.query_one("#diff-layout", Select).value),
            "pull_fetch_default": str(self.query_one("#pull-fetch-default", Select).value),
            "keymap": dict(self._values_for(scope)["keymap"]),
        }

    def _edit_keybindings(self) -> None:
        scope = str(self.query_one("#controls-scope", Select).value)
        current = dict(self._values_for(scope)["keymap"])

        def update(keymap: dict[str, str] | None) -> None:
            if keymap is not None:
                target = self._global if scope == "global" else self._overrides
                target["keymap"] = keymap

        self.app.push_screen(KeymapDialog(current), update)

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = str(event.button.id)
        if button_id == "controls-close":
            self.action_close()
        elif button_id == "controls-apply":
            settings = self._settings()
            if settings is not None:
                scope = str(self.query_one("#controls-scope", Select).value)
                self.dismiss(WorkspaceControlRequest("settings", scope, settings))
        elif button_id == "edit-keybindings":
            self._edit_keybindings()
        elif button_id == "reload-settings":
            self.dismiss(WorkspaceControlRequest("reload-settings"))
        elif button_id in {
            "reset-overrides",
            "clear-drafts",
            "reset-view",
            "forget-workspace",
            "toggle-exclusion",
            "diagnostics",
            "reset-global",
        }:
            self.dismiss(WorkspaceControlRequest(button_id))


class DiagnosticsDialog(ModalScreen[str | None]):
    """Read-only local diagnostic preview; export and clearing are app actions."""

    BINDINGS = [("escape", "close", "Close")]

    DEFAULT_CSS = """
    DiagnosticsDialog { align: center middle; background: $text-muted 28%; }
    DiagnosticsDialog > #diagnostics-dialog {
        width: 76; max-width: 100%; height: 22; max-height: 100%;
        padding: 1 2; border: round $primary-muted; background: $surface; color: $text;
    }
    DiagnosticsDialog #diagnostics-summary { height: auto; margin-bottom: 1; color: $text-muted; }
    DiagnosticsDialog #diagnostics-policy { height: auto; margin-bottom: 1; color: $text-muted; }
    DiagnosticsDialog #diagnostics-error { height: auto; color: $error; text-style: bold; }
    DiagnosticsDialog #diagnostics-preview { height: 1fr; min-height: 5; padding: 0 1; border: round $primary-muted; background: $surface; color: $text; }
    DiagnosticsDialog .dialog-buttons { height: 3; align: right middle; }
    DiagnosticsDialog .dialog-buttons Button { min-width: 10; margin-left: 1; }
    """

    def __init__(self, *, preview: str, summary: str, error: str = "") -> None:
        super().__init__()
        self._preview, self._summary, self._error = preview, summary, error

    def compose(self) -> ComposeResult:
        with Vertical(id="diagnostics-dialog"):
            yield Label("LOCAL DIAGNOSTICS", classes="dialog-title")
            yield Static(Text(self._summary), id="diagnostics-summary")
            yield Static(
                "Retention: 7 days or 10 MiB. No source content, credentials, or telemetry. "
                "Nothing is uploaded automatically.",
                id="diagnostics-policy",
            )
            yield Static(Text(self._error), id="diagnostics-error")
            with VerticalScroll(id="diagnostics-preview"):
                yield Static(Text(self._preview or "No local diagnostic records."))
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="diagnostics-close")
                yield Button("Clear local diagnostics", id="diagnostics-clear")
                yield Button(
                    "Export preview",
                    id="diagnostics-export",
                    variant="primary",
                    disabled=bool(self._error),
                )

    def on_mount(self) -> None:
        self.query_one("#diagnostics-preview", VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "diagnostics-export":
            self.dismiss("export")
        elif event.button.id == "diagnostics-clear":
            self.dismiss("clear")
        else:
            self.action_close()
