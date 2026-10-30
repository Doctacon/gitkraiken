"""Presentation for the saved pull/fetch split control and pull review."""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from .pull import PullPlan
from .settings import PULL_FETCH_OPTIONS

PULL_FETCH_MODES = tuple((value, label) for label, value in PULL_FETCH_OPTIONS)


def pull_mode_label(mode: str) -> str:
    return dict(PULL_FETCH_MODES).get(mode, "Pull (fast-forward if possible)")


class _PullOptions(OptionList):
    async def _on_click(self, event: events.Click) -> None:
        if event.button != 1:
            event.prevent_default()
            event.stop()
            return
        await super()._on_click(event)


class PullModeMenu(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Close")]
    DEFAULT_CSS = """
    PullModeMenu { align: left top; overflow: hidden; background: transparent; }
    PullModeMenu > #pull-mode-menu { position: absolute; width: 38; max-width: 100%; height: auto; max-height: 100%; padding: 1; border: round $primary-muted; background: $panel; color: $text; }
    PullModeMenu #pull-mode-intro { height: auto; margin: 0 1 1 1; color: $text-muted; }
    PullModeMenu #pull-mode-options { height: auto; max-height: 9; border: none; background: $panel; }
    PullModeMenu #pull-mode-options > .option-list--option-highlighted { background: $primary-muted; color: $text; text-style: none; }
    """

    def __init__(self, mode: str, *, screen_x: int, screen_y: int, plain: bool = False) -> None:
        super().__init__()
        self.mode = mode if mode in dict(PULL_FETCH_MODES) else "pull-ff"
        self.screen_x, self.screen_y, self.plain = screen_x, screen_y, plain
        self._index = {mode_id: index for index, (mode_id, _) in enumerate(PULL_FETCH_MODES)}

    def compose(self) -> ComposeResult:
        checked, unchecked = ("(*)", "( )") if self.plain else ("◉", "○")
        with Vertical(id="pull-mode-menu"):
            yield Static("Default action for the main button", id="pull-mode-intro")
            yield _PullOptions(
                *(
                    Option(Text(f"{checked if mode == self.mode else unchecked}  {label}"), id=mode)
                    for mode, label in PULL_FETCH_MODES
                ),
                id="pull-mode-options",
            )

    def on_mount(self) -> None:
        options = self.query_one("#pull-mode-options", OptionList)
        options.highlighted = self._index[self.mode]
        options.focus()
        self._place_menu()

    def _place_menu(self) -> None:
        menu = self.query_one("#pull-mode-menu")
        width = min(42 if self.plain else 40, max(self.size.width, 1))
        # virtual_size excludes the frame's padding/border; treating it as
        # the outer height clips the last choices even though their own
        # allocated regions are large enough.
        height = min(len(PULL_FETCH_MODES) + 6, max(self.size.height, 1))
        menu.styles.width, menu.styles.height = width, height
        menu.styles.offset = (
            min(max(self.screen_x, 0), max(self.size.width - width, 0)),
            min(max(self.screen_y, 0), max(self.size.height - height, 0)),
        )

    def on_resize(self, _event: events.Resize) -> None:
        self._place_menu()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(str(event.option.id))

    def on_click(self, event: events.Click) -> None:
        if not self.query_one("#pull-mode-menu").region.contains(event.screen_x, event.screen_y):
            event.stop()
            self.dismiss(None)


class PullReviewDialog(ModalScreen[bool]):
    """A literal, scrollable rendering of one frozen coordinator plan."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    PullReviewDialog { align: center middle; }
    PullReviewDialog > #pull-review { width: 68; max-width: 94%; height: 20; max-height: 94%; padding: 1 2; border: round $primary; background: $panel; }
    PullReviewDialog #pull-review-title { height: 1; text-style: bold; color: $primary; }
    PullReviewDialog #pull-review-detail { height: 1fr; margin: 1 0; scrollbar-size: 1 1; }
    PullReviewDialog #pull-review-detail Static { height: auto; }
    PullReviewDialog #pull-review-actions { height: 3; align: right middle; }
    PullReviewDialog Button { margin-left: 1; }
    """

    def __init__(self, plan: PullPlan) -> None:
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        plan = self.plan
        kind = plan.kind.replace("-", " ").title()
        detail = (
            f"Branch: {plan.branch}\nUpstream: {plan.upstream}\n"
            f"Original HEAD: {plan.original_head}\nExact target: {plan.target_oid}\n"
            f"Incoming: {plan.incoming}  Outgoing: {plan.outgoing}\n\n"
            + "\n".join(plan.consequences or (plan.reason or "No integration is available.",))
        )
        button_label = "Abort operation" if plan.kind == "abort" else "Integrate"
        with Vertical(id="pull-review"):
            yield Static(Text(f"REVIEW PULL · {kind}", no_wrap=True), id="pull-review-title")
            with VerticalScroll(id="pull-review-detail"):
                yield Static(Text(detail))
            with Horizontal(id="pull-review-actions"):
                yield Button("Cancel", id="pull-review-cancel")
                yield Button(button_label, id="pull-review-confirm", variant="primary")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "pull-review-confirm")
