"""Explicit, keyboard-friendly dialogs. Repository strings are always literal text."""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static, TextArea
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


class MessageDialog(ModalScreen[str | None]):
    """Edit a commit message without treating its contents as markup."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+enter", "save", "Save")]

    DEFAULT_CSS = """
    MessageDialog > #message-dialog {
        width: 62;
        max-width: 100%;
        height: auto;
        max-height: 24;
        padding: 1 2;
        border: round #4f8790;
        background: #ffffff;
    }
    MessageDialog #message-value {
        height: 10;
        max-height: 14;
        border: round #a8c1c7;
        background: #f5fafa;
    }
    MessageDialog .dialog-buttons { height: 3; margin-top: 1; align: right middle; }
    MessageDialog .dialog-buttons Button { margin-left: 1; }
    """

    def __init__(self, title: str, value: str = "") -> None:
        super().__init__()
        self.heading, self.value = title, value

    def compose(self) -> ComposeResult:
        with Vertical(id="message-dialog"):
            yield Label(Text(self.heading), classes="dialog-title")
            yield TextArea(self.value, id="message-value", theme="github_light")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="confirm", variant="primary")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        value = self.query_one(TextArea).text.strip()
        self.dismiss(value or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "confirm":
            self.action_save()
        else:
            self.dismiss(None)


class _BranchContextOptions(OptionList):
    async def _on_click(self, event: events.Click) -> None:
        # Textual 1.x OptionList otherwise activates on any mouse button.
        if event.button != 1:
            event.prevent_default()
            event.stop()
            return
        await super()._on_click(event)


class BranchContextMenu(ModalScreen[str | None]):
    """A reusable, literal-text dropdown positioned at the invoking click."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    BranchContextMenu {
        align: left top;
        overflow: hidden;
        background: transparent;
    }
    BranchContextMenu > #branch-context-menu {
        position: absolute;
        width: 1;
        height: 3;
        max-width: 100%;
        padding: 0 1;
        border: round #4f8790;
        background: #ffffff;
        color: #14232c;
        scrollbar-size: 1 1;
    }
    BranchContextMenu > #branch-context-menu:focus {
        border: round #006d77;
    }
    BranchContextMenu > #branch-context-menu > .option-list--option-highlighted {
        background: #d8eeee;
        color: #14232c;
        text-style: none;
    }
    """

    def __init__(
        self,
        current_branch: str,
        target: str,
        *,
        screen_x: int,
        screen_y: int,
        actions: list[tuple[str, str] | None] | None = None,
    ) -> None:
        super().__init__()
        self.current_branch = current_branch
        self.target = target
        self.screen_x = screen_x
        self.screen_y = screen_y
        self.actions = (
            [("rebase", f"Rebase {current_branch} onto {target}")] if actions is None else actions
        )
        # Keep labels as Rich Text, rather than markup-bearing strings.
        self._labels: dict[str, Text] = {}
        self._option_index_by_id: dict[str, int] = {}
        for index, action in enumerate(self.actions):
            if action is not None:
                action_id, label = action
                self._labels[action_id] = Text(label)
                self._option_index_by_id[action_id] = index

    def compose(self) -> ComposeResult:
        content = []
        for action in self.actions:
            if action is None:
                # Textual 1.x's Separator shifts its render-cache indexes from
                # its selectable option indexes. A disabled, cropped rule is a
                # nonselectable visual separator without that mismatch.
                content.append(
                    Option(
                        Text("─" * 76, style="dim", no_wrap=True, overflow="crop"), disabled=True
                    )
                )
            else:
                action_id, _label = action
                content.append(Option(self._labels[action_id], id=action_id))
        yield _BranchContextOptions(*content, id="branch-context-menu")

    def on_mount(self) -> None:
        menu = self.query_one("#branch-context-menu", OptionList)
        if self._option_index_by_id:
            menu.highlighted = next(iter(self._option_index_by_id.values()))
        menu.focus()
        self._set_highlight_tooltip()
        self._place_menu()

    def _set_highlight_tooltip(self) -> None:
        menu = self.query_one("#branch-context-menu", OptionList)
        if menu.highlighted is not None:
            option = menu.get_option_at_index(menu.highlighted)
            if option.id is not None:
                menu.tooltip = self._labels[str(option.id)].copy()

    def _place_menu(self) -> None:
        menu = self.query_one("#branch-context-menu", OptionList)
        screen_width, screen_height = self.size
        longest = max((label.cell_len for label in self._labels.values()), default=1)
        width = min(max(longest + 4, 8), 76, max(screen_width, 1))
        content_width = max(width - 4, 1)  # Border and horizontal padding take two each.
        item_height = sum(
            len(label.wrap(self.app.console, content_width, overflow="fold"))
            for label in self._labels.values()
        )
        separator_height = sum(action is None for action in self.actions)
        height = min(max(item_height + separator_height + 2, 3), max(screen_height, 1))
        menu.styles.width = width
        menu.styles.height = height
        menu.styles.offset = (
            min(max(self.screen_x, 0), max(screen_width - width, 0)),
            min(max(self.screen_y, 0), max(screen_height - height, 0)),
        )

    def on_resize(self, _event: events.Resize) -> None:
        self._place_menu()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self._set_highlight_tooltip()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(str(event.option.id))

    def on_click(self, event: events.Click) -> None:
        menu = self.query_one("#branch-context-menu", OptionList)
        if not menu.region.contains(event.screen_x, event.screen_y):
            event.stop()
            self.dismiss(None)


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
                        "Esc           Back to graph; otherwise clear search / selection\n"
                        "Ctrl+P / x    Actions for the selected item\n"
                        "Ctrl+O        Open another local repository\n"
                        "Ctrl+R / r    Refresh local state\n"
                        "Ctrl+B        Create a branch\n"
                        "Ctrl+Left     Toggle branch sidebar\n"
                        "Ctrl+Right    Toggle inspector\n"
                        "Ctrl+Q        Quit\n\n"
                        "Select Working directory to stage files and commit.\n"
                        "WIP appears in the graph only when you have file changes.\n"
                        "Click a file or press Enter to show its diff in the center.\n"
                        "Back to graph / Esc returns without losing graph position.\n"
                        "A file changed in both index and worktree appears twice,\n"
                        "so you can inspect both versions separately.\n"
                        "Changed files fold with their caret or the Space key.\n"
                        "Drag pane dividers to resize the sidebar or inspector.\n"
                        "Drag a column header's right-edge │ to resize it.\n"
                        "Sizes are remembered for this running session.\n"
                        "Small panes show one side panel at a time; use Ctrl+Left/Right.\n\n"
                        "Smooth graph: rounded bends and spaced, colored lanes.\n"
                        "Broken horizontal lines pass behind vertical lanes;\n"
                        "crossings are not joins. Dotted tails mean more history.\n"
                        "Compact graph: Git's original denser lane layout.\n\n"
                        "The graph stays unified across branches and checkouts.\n"
                        "Single-click a branch: select it without moving the graph.\n"
                        "Double-click its label: check it out immediately.\n"
                        "Double-click a graph row: check out a branch at its tip\n"
                        "(choose if several), or check out detached HEAD.\n"
                        "Action and double-click checkouts take effect immediately.\n"
                        "Git actions do not show a confirmation dialog.\n"
                        "Right-click a branch or commit for its available actions.\n"
                        "Branch menus include checkout, reset, worktree and copy choices.\n"
                        "Sidebar sections: Repository, Local, Remotes, Worktrees,\n"
                        "PRs, Tags, Stashes. Each expands/collapses independently.\n"
                        "Worktrees: double-click or select + Open to switch workspace.\n"
                        "New creates a worktree at HEAD without switching this one.\n"
                        "PRs: Load PRs / Refresh PRs explicitly reads open requests.\n"
                        "Select a PR for details and browser/copy link actions.\n"
                        "Expanding sections and local refresh never load PRs automatically.\n"
                        "On a feature branch, right-click its target to start a PR.\n"
                        "PR composer: title + description; Done returns a web link.\n"
                        "Push first; PRs require authenticated gh or Azure CLI.\n"
                        "Reset opens a Soft, Mixed, or Hard mode picker; Hard discards work.\n"
                        "Only the latest commit message can be edited.\n"
                        "History dates use your computer's local timezone.\n"
                        "Actions → Locate moves to its tip; Load more reveals\n"
                        "unloaded tips explicitly (up to 5,000 commits).\n"
                        "Carets / Space fold sections, remotes and namespaces.\n"
                        "Sidebar search reveals matches without changing history;\n"
                        "clear it to restore folds. Escape clears that search first.\n"
                        "💻 local/HEAD · 🌳 worktree · 🐙 GitHub · ☁ Azure · 🦊 GitLab · ↗ other\n"
                        "Markers share a commit when branch, worktree and remote tips agree.\n"
                        "Remote markers show last-fetched state, not live servers.\n"
                        "A new branch adds a pointer, not a fictional fork.\n\n"
                        "Use Actions to merge, rebase, rename, or delete a branch.\n"
                        "Pull is fast-forward only. Push is never forced.\n"
                        "Resolve conflicts in your editor, stage, then Continue.\n\n"
                        "Credentials: existing SSH agent or Git credential helper.\n"
                        "No automatic fetch or network traffic. Local refresh every 5s.\n"
                        "tmux hotkey: prefix + g toggles in this pane. Your original\n"
                        "shell/editor keeps running; Ctrl+Q quits and restores it.\n"
                        "Explicit --popup: prefix + d or prefix + g detaches.\n"
                        "The hotkey requires the generated tmux binding."
                    )
                )
            yield Button("Close", id="cancel")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)
