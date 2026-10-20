"""Explicit, keyboard-friendly dialogs. Repository strings are always literal text."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option


class ActionPicker(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, actions: list[tuple[str, str]]) -> None:
        super().__init__()
        self.heading, self.actions = title, actions

    def compose(self) -> ComposeResult:
        with Vertical(id="action-picker"):
            yield Label(Text(self.heading), classes="dialog-title")
            yield OptionList(
                *(Option(Text(label), id=value) for value, label in self.actions),
                id="action-options",
            )
            yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)


class PromptDialog(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.heading, self.placeholder, self.value = title, placeholder, value

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-dialog"):
            yield Label(Text(self.heading), classes="dialog-title")
            yield Input(value=self.value, placeholder=self.placeholder, id="prompt-value")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Continue", id="confirm", variant="primary")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        value = self.query_one(Input).value.strip()
        self.dismiss((value or None) if event.button.id == "confirm" else None)


class ConfirmDialog(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, detail: str) -> None:
        super().__init__()
        self.heading, self.detail = title, detail

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(Text(self.heading), classes="dialog-title")
            yield Label(Text(self.detail), classes="dialog-detail")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Continue", id="confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cancel").focus()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm")


class HelpDialog(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close"), ("question_mark", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="action-picker"):
            yield Label("YOUR REPOSITORY, AT YOUR FINGERTIPS", classes="dialog-title")
            with VerticalScroll():
                yield Static(
                    Text(
                        "↑ ↓ / j k     Navigate history and file lists\n"
                        "Enter / click Inspect commit/file; select branch context\n"
                        "/ or f        Search message, author, SHA, or ref\n"
                        "Enter in search / Next  Jump to next match\n"
                        "g             Switch Smooth / Compact graph\n"
                        "Esc           Clear focused search / reference selection\n"
                        "Ctrl+P / x    Actions for the selected item\n"
                        "Ctrl+O        Open another local repository\n"
                        "Ctrl+R / r    Refresh local state\n"
                        "Ctrl+B        Create a branch\n"
                        "Ctrl+Left     Toggle branch sidebar\n"
                        "Ctrl+Right    Toggle inspector\n"
                        "Ctrl+Q        Quit\n\n"
                        "Select Working directory to stage files and commit.\n"
                        "A file changed in both index and worktree appears twice,\n"
                        "so you can inspect both versions separately.\n\n"
                        "Smooth graph: rounded bends and spaced, colored lanes.\n"
                        "Broken horizontal lines pass behind vertical lanes;\n"
                        "crossings are not joins. Dotted tails mean more history.\n"
                        "Compact graph: Git's original denser lane layout.\n\n"
                        "The graph stays unified across branches and checkouts.\n"
                        "Single-click a branch: select it without moving the graph.\n"
                        "Double-click its label: request confirmed checkout.\n"
                        "Double-click a graph row: check out a branch at its tip\n"
                        "(choose if several), or confirm detached HEAD otherwise.\n"
                        "History dates use the author's recorded timezone;\n"
                        "hover a date for seconds and the timezone offset.\n"
                        "Actions → Locate moves to its tip; Load more reveals\n"
                        "unloaded tips explicitly (up to 5,000 commits).\n"
                        "Carets / Space fold sections, remotes and namespaces.\n"
                        "Sidebar search reveals matches without changing history;\n"
                        "clear it to restore folds. Escape clears that search first.\n"
                        "💻 local/HEAD · 🐙 GitHub · ☁ Azure · 🦊 GitLab · ↗ other\n"
                        "Local and remote markers share a commit when tips agree.\n"
                        "Remote markers show last-fetched state, not live servers.\n"
                        "A new branch adds a pointer, not a fictional fork.\n\n"
                        "Use Actions to merge, rebase, rename, or delete a branch.\n"
                        "Pull is fast-forward only. Push is never forced.\n"
                        "Resolve conflicts in your editor, stage, then Continue.\n\n"
                        "Credentials: existing SSH agent or Git credential helper.\n"
                        "No automatic network traffic. Local refresh every 5s.\n"
                        "tmux popup: prefix + d detaches; prefix + g also works\n"
                        "when you have installed the generated tmux binding."
                    )
                )
            yield Button("Close", id="cancel")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)
