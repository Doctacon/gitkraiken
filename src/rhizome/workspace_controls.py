"""Small, local-only workspace controls and diagnostics previews.

The application owns persistence, confirmation, and all I/O.  These screens only
collect an explicit request or display a supplied, already-safe preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch


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
)


class WorkspaceControlsDialog(ModalScreen[WorkspaceControlRequest | None]):
    """A deliberately small set of safe, visible workspace preferences."""

    BINDINGS = [("escape", "close", "Close")]

    DEFAULT_CSS = """
    WorkspaceControlsDialog { align: center middle; background: #36505b 28%; }
    WorkspaceControlsDialog > #workspace-controls-dialog {
        width: 70; max-width: 100%; height: 24; max-height: 100%;
        padding: 1 2; border: round #4f8790; background: #ffffff; color: #14232c;
    }
    WorkspaceControlsDialog .dialog-title { height: 1; margin-bottom: 0; }
    WorkspaceControlsDialog #controls-intro, WorkspaceControlsDialog #controls-offline-policy {
        height: auto; margin: 1 0; color: #36505b;
    }
    WorkspaceControlsDialog #controls-error { display: none; color: #8d2635; text-style: bold; }
    WorkspaceControlsDialog #controls-error.-visible { display: block; height: auto; margin: 1 0; }
    WorkspaceControlsDialog #controls-offline-policy { border-left: tall #4f8790; padding-left: 1; }
    WorkspaceControlsDialog #controls-body { height: 1fr; min-height: 4; }
    /* Inputs use a tall border. Three rows prevent it being cut off at either edge. */
    WorkspaceControlsDialog .setting-row { height: 3; min-height: 3; padding: 0 1; }
    WorkspaceControlsDialog .setting-label { width: 1fr; content-align: left middle; }
    WorkspaceControlsDialog Select { width: 16; min-width: 16; }
    WorkspaceControlsDialog Input { width: 10; min-width: 10; }
    WorkspaceControlsDialog Switch { width: 9; min-width: 9; }
    WorkspaceControlsDialog .exclusion-status {
        height: auto; margin: 1 0; padding: 0 1; border-left: tall #c68a35;
        background: #fff3df; color: #6b4300; text-style: bold;
    }
    /* These are deliberate two-column groups, not a toolbar: all labels fit
       inside the 70-column dialog and remain tabbable in its scroll region. */
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
    ) -> None:
        super().__init__()
        defaults = {
            "offline": False,
            "remote_refresh_enabled": True,
            "remote_refresh_interval": 60,
            "plain_glyphs": False,
            "diagnostics_enabled": False,
        }
        self._global = {name: global_settings.get(name, defaults[name]) for name in _FIELDS}
        self._overrides = {name: overrides[name] for name in _FIELDS if name in overrides}
        self._excluded = excluded
        self._state_error = state_error

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
            yield Label("WORKSPACE CONTROLS", classes="dialog-title")
            yield Static(
                "Choose local refresh and display preferences. Apply changes to this workspace or your defaults.",
                id="controls-intro",
            )
            yield Static(
                self._state_error,
                id="controls-error",
                classes="-visible" if self._state_error else "",
            )
            yield Static("", id="controls-offline-policy")
            with VerticalScroll(id="controls-body"):
                with Horizontal(classes="setting-row"):
                    yield Label("Apply settings to", classes="setting-label")
                    yield Select(
                        [("This workspace", "workspace"), ("All workspaces", "global")],
                        value="workspace",
                        allow_blank=False,
                        id="controls-scope",
                    )
                yield Label("Effective settings", classes="dialog-title")
                yield self._toggle("Work offline", "offline", bool(values["offline"]))
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
                    "Use plain text glyphs", "plain-glyphs", bool(values["plain_glyphs"])
                )
                yield self._toggle(
                    "Keep local diagnostics",
                    "diagnostics-enabled",
                    bool(values["diagnostics_enabled"]),
                )
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
                    "Only an opaque exclusion marker is saved; Git content never changes.",
                    classes="dialog-detail",
                )
                with Vertical(classes="control-actions"):
                    with Horizontal(classes="control-action-row"):
                        yield Button("Reset workspace overrides", id="reset-overrides")
                        yield Button("Clear saved drafts", id="clear-drafts")
                    with Horizontal(classes="control-action-row"):
                        yield Button("Reset saved view", id="reset-view")
                        yield Button("Forget this workspace", id="forget-workspace")
                    with Horizontal(classes="control-action-row"):
                        yield Button(
                            "Include workspace" if self._excluded else "Exclude workspace",
                            id="toggle-exclusion",
                        )
                        yield Button("Local diagnostics", id="diagnostics")
                    with Horizontal(classes="control-action-row control-action-single"):
                        yield Button("Reset global preferences", id="reset-global")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="controls-close")
                yield Button("Apply settings", id="controls-apply", variant="primary")

    def _toggle(self, label: str, control_id: str, value: bool) -> Horizontal:
        return Horizontal(
            Label(label, classes="setting-label"),
            Switch(value=value, id=control_id),
            classes="setting-row",
        )

    def on_mount(self) -> None:
        self._update_offline_policy("workspace")
        self.query_one("#controls-scope", Select).focus()

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
            policy.update("Global offline is a master stop. A workspace setting cannot bypass it.")

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
        }

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
    DiagnosticsDialog { align: center middle; background: #36505b 28%; }
    DiagnosticsDialog > #diagnostics-dialog {
        width: 76; max-width: 100%; height: 22; max-height: 100%;
        padding: 1 2; border: round #4f8790; background: #ffffff; color: #14232c;
    }
    DiagnosticsDialog #diagnostics-summary { height: auto; margin-bottom: 1; color: #36505b; }
    DiagnosticsDialog #diagnostics-policy { height: auto; margin-bottom: 1; color: #36505b; }
    DiagnosticsDialog #diagnostics-error { height: auto; color: #8d2635; text-style: bold; }
    DiagnosticsDialog #diagnostics-preview { height: 1fr; min-height: 5; padding: 0 1; border: round #a8c1c7; background: #f5fafa; color: #14232c; }
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
