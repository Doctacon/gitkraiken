"""Explicit, keyboard-friendly dialogs. Repository strings are always literal text."""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .settings import COMMANDS


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
        border: round $primary-muted;
        background: $surface;
    }
    MessageDialog #message-value {
        height: 10;
        max-height: 14;
        border: round $primary-muted;
        background: $surface;
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
            yield TextArea(
                self.value,
                id="message-value",
                theme="monokai" if self.app.current_theme.dark else "github_light",
            )
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
        border: round $primary-muted;
        background: $surface;
        color: $text;
        scrollbar-size: 1 1;
    }
    BranchContextMenu > #branch-context-menu:focus {
        border: round $primary;
    }
    BranchContextMenu > #branch-context-menu > .option-list--option-highlighted {
        background: $primary-muted;
        color: $text;
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

    def __init__(self, keymap: dict[str, str] | None = None) -> None:
        super().__init__()
        self._keymap = keymap or {}

    def compose(self) -> ComposeResult:
        commands = "\n".join(
            f"{self._keymap.get(command.id, command.keys):<20} {command.description}"
            for command in COMMANDS
        )
        with Vertical(id="action-picker"):
            yield Label("YOUR REPOSITORY, AT YOUR FINGERTIPS", classes="dialog-title")
            with VerticalScroll():
                yield Static(
                    Text(
                        "CURRENT WORKSPACE COMMANDS (editors retain their own keys)\n"
                        + commands
                        + "\n\n"
                        "DAILY FLOW: inspect a changed file, choose exact lines or a hunk, stage, then write a commit.\n"
                        "This is optional guidance—your existing Git message style still works.\n\n"
                        "Arrow keys navigate; Enter / click inspects a commit or file.\n"
                        "Enter in search / Next jumps to the next captured-scope match.\n"
                        "Esc returns to the graph, then clears search / selection.\n\n"
                        "Files opens working changes; activate a file to review.\n"
                        "In a precision diff: v anchors a range; arrows extend it;\n"
                        "s stages, u unstages. Mouse/Shift-click and hunk controls work too.\n"
                        "Search, context and whitespace are reading aids, not patch input.\n"
                        "External edits mark the captured review stale: refresh/reselect.\n"
                        "Commit opens the durable composer for staged changes.\n"
                        "On repository open, saved drafts take priority over Git templates.\n"
                        "Edit a loaded template before committing; summary/body style is optional.\n"
                        "Git owns editor-mode cleanup, encoding and message hooks.\n"
                        "Commit temporarily hands the terminal to native hooks/signing.\n"
                        "The workspace resumes afterward; failures retain your draft.\n"
                        "WIP appears in the graph only when you have file changes.\n"
                        "Click a file or press Enter to show its diff in the center.\n"
                        "Back to graph / Esc returns without losing graph position.\n"
                        "A file changed in both index and worktree appears twice,\n"
                        "so you can inspect both versions separately.\n"
                        "Changed files fold with their caret or the Space key.\n"
                        "Drag pane dividers to resize the sidebar or inspector.\n"
                        "Drag a column header's right-edge │ to resize it.\n"
                        "Sizes are remembered for this running session.\n"
                        "Small panes show one primary task: Files, Commit, Refs or Back.\n"
                        "Workspace settings apply locally or to all workspaces. Global offline\n"
                        "is the master stop for network work; a workspace cannot bypass it.\n"
                        "Excluding a workspace keeps drafts, view and preferences session-only.\n"
                        "Reopen restores saved draft and view context when it is still valid.\n\n"
                        "Smooth graph: rounded bends and spaced, colored lanes.\n"
                        "Broken horizontal lines pass behind vertical lanes;\n"
                        "crossings are not joins. Dotted tails mean more history.\n"
                        "Compact graph: Git's original denser lane layout.\n\n"
                        "The graph stays unified across branches and checkouts.\n"
                        "Single-click a branch: select it without moving the graph.\n"
                        "Double-click a branch still only selects it.\n"
                        "Double-click a graph row inspects it, never checks it out.\n"
                        "Use an explicit Checkout action to change the checkout.\n"
                        "Unproven destructive actions are unavailable, not bypassed.\n"
                        "Right-click a branch or commit for its available actions.\n"
                        "Branch menus include checkout, reset, worktree and copy choices.\n"
                        "Sidebar sections: Repository, Local, Remotes, Worktrees,\n"
                        "PRs, Tags, Stashes. Each expands/collapses independently.\n"
                        "Worktrees: double-click or use Open selected worktree in Actions.\n"
                        "Actions: Create worktree at HEAD leaves this checkout intact.\n"
                        "PRs load automatically; Actions → Refresh PRs updates them sooner.\n"
                        "Select a PR for details and browser/copy link actions.\n"
                        "Existing PRs stay visible during background refreshes.\n"
                        "On a feature branch, right-click its target to start a PR.\n"
                        "PR composer: title + description; Done returns a web link.\n"
                        "Push first; PRs require authenticated gh or Azure CLI.\n"
                        "Discard, resets and message rewriting remain gated for safety.\n"
                        "A visible legacy choice is not permission to bypass that gate.\n"
                        "History dates use your computer's local timezone.\n"
                        "Older / Newer replace bounded history windows, without a total ceiling.\n"
                        "Topology is the default; author-time order is separate from graph style.\n"
                        "Ref selection never filters history: Focus is explicit and reversible.\n"
                        "Search examines the captured scope, not only rendered rows.\n"
                        "Changed refs are reported; Refresh explicitly captures new roots.\n"
                        "Split diffs are read-only; Unified enables supported exact staging.\n"
                        "Carets / Space fold sections, remotes and namespaces.\n"
                        "Sidebar search reveals matches without changing history;\n"
                        "clear it to restore folds. Escape clears that search first.\n"
                        "⑂ local/HEAD · ▤ linked worktree · ↗ remote tracking · ◇ tag\n"
                        "Markers share a commit when branch, worktree and remote tips agree.\n"
                        "Remote markers show last-fetched state, not live servers.\n"
                        "Fetch validates cache scope; unsafe ref/pruning settings are refused.\n"
                        "A new branch adds a pointer, not a fictional fork.\n\n"
                        "Integration, deletion, stash mutations, Pull and Continue/Abort\n"
                        "remain unavailable until their guarded recovery flows are proven.\n"
                        "Use external Git/editor deliberately, then refresh. External\n"
                        "actions are not covered by Rhizome recovery. Push is never forced.\n\n"
                        "Credentials: existing SSH agent or Git credential helper.\n"
                        "Local polling: 2s. Automatic remote refresh defaults to 60s,\n"
                        "pauses while inactive, and respects offline settings.\n"
                        "Slow automatic network work does not block local inspection.\n"
                        "tmux hotkey: prefix + g toggles in this pane. Your original\n"
                        "shell/editor keeps running; Ctrl+Q quits and restores it.\n"
                        "Explicit --popup: prefix + d or prefix + g detaches.\n"
                        "The hotkey requires the generated tmux binding."
                    ),
                    id="help-text",
                )
            yield Button("Close", id="cancel")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)
