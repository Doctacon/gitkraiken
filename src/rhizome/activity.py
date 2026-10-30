"""Read-only, bounded activity inspection for coordinator work."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static
from textual.widgets.option_list import Option

from .operations import OperationActivity


class ActivityDialog(ModalScreen[None]):
    """Inspect Git mutations separately from background repository reads."""

    BINDINGS = [("escape", "close", "Close")]
    _MAX_RECORDS = 20

    DEFAULT_CSS = """
    ActivityDialog {
        align: center middle;
        background: transparent;
    }
    ActivityDialog > #activity-dialog {
        width: 76;
        max-width: 100%;
        height: 22;
        max-height: 100%;
        padding: 0 2;
        border: round $primary-muted;
        background: $surface;
        color: $text;
    }
    ActivityDialog .dialog-title { height: 1; margin-bottom: 1; }
    ActivityDialog .activity-label {
        height: 1;
        color: $foreground-muted;
        text-style: bold underline;
    }
    ActivityDialog #activity-context {
        height: 3;
        min-height: 2;
        margin-bottom: 1;
        padding: 0 1;
        border-left: tall $primary-muted;
        color: $foreground-muted;
    }
    ActivityDialog #activity-list {
        height: 1fr;
        min-height: 3;
        margin-bottom: 1;
        border: round $primary-muted;
        background: $surface;
        color: $text;
    }
    ActivityDialog #activity-list:focus { border: round $primary; }
    ActivityDialog #activity-list > .option-list--option-highlighted {
        background: $primary-muted;
        color: $text;
        text-style: bold underline;
    }
    ActivityDialog #activity-detail {
        height: 3;
        min-height: 3;
        padding: 0 1;
        border-left: tall $primary-muted;
        color: $foreground-muted;
    }
    ActivityDialog #activity-notice {
        height: 2;
        margin-top: 0;
        color: $warning;
    }
    ActivityDialog .dialog-buttons { height: 3; align: right middle; }
    ActivityDialog .dialog-buttons Button { min-width: 10; margin-left: 1; }
    """

    def __init__(
        self,
        activity: Callable[[], tuple[OperationActivity, ...]],
        context: Callable[[], tuple[str, ...]],
    ) -> None:
        super().__init__()
        self._activity = activity
        self._read_context = context
        self._records: tuple[OperationActivity, ...] = ()

    def compose(self) -> ComposeResult:
        with Vertical(id="activity-dialog"):
            yield Label("ACTIVITY — LOCAL WORK", classes="dialog-title")
            yield Label("BACKGROUND READS", classes="activity-label")
            with VerticalScroll(id="activity-context"):
                yield Static(id="activity-context-text")
            yield Label("GIT MUTATIONS", classes="activity-label")
            yield OptionList(id="activity-list")
            with VerticalScroll(id="activity-detail"):
                yield Static("No Git mutations have been recorded.", id="activity-detail-text")
            yield Static(
                "Read activity is separate from Git mutations. Closing never cancels a running mutation.",
                id="activity-notice",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="activity-close", variant="primary")

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(1, self._refresh)
        self.query_one("#activity-list", OptionList).focus()

    def _refresh(self) -> None:
        """Refresh snapshots without moving an operator's current inspection."""
        options = self.query_one("#activity-list", OptionList)
        selected_id = self._selected_id(options)
        self._records = tuple(
            sorted(self._activity(), key=lambda record: record.started, reverse=True)[
                : self._MAX_RECORDS
            ]
        )
        self._update_context()
        options.clear_options()
        options.add_options(
            Option(self._record_label(record), id=record.id) for record in self._records
        )
        if not self._records:
            self.query_one("#activity-detail-text", Static).update(
                "No Git mutations have been recorded. Background reads above do not mutate Git."
            )
            return
        selected_index = next(
            (index for index, record in enumerate(self._records) if record.id == selected_id), 0
        )
        options.highlighted = selected_index
        self._show_record(self._records[selected_index])

    def _selected_id(self, options: OptionList) -> str | None:
        if options.highlighted is None:
            return None
        option = options.get_option_at_index(options.highlighted)
        return str(option.id) if option.id is not None else None

    def _update_context(self) -> None:
        context = self._read_context()
        text = "\n".join(context) if context else "No background read is currently reported."
        self.query_one("#activity-context-text", Static).update(Text(text))

    def _record_label(self, record: OperationActivity) -> Text:
        return Text(f"{record.state.upper():9} {record.operation}  ·  {self._elapsed(record)}")

    def _elapsed(self, record: OperationActivity) -> str:
        elapsed = record.elapsed
        if record.state in {"queued", "running"}:
            elapsed = max(elapsed, (datetime.now(UTC) - record.started).total_seconds())
        seconds = max(0, int(elapsed))
        hours, seconds = divmod(seconds, 3_600)
        minutes, seconds = divmod(seconds, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _show_record(self, record: OperationActivity) -> None:
        detail = Text()
        detail.append(f"{record.state.upper()} · {record.operation}\n")
        detail.append(f"Elapsed: {self._elapsed(record)}\n")
        if record.checkpoint_id:
            detail.append(f"Recovery checkpoint: {record.checkpoint_id}\n")
        detail.append("\n")
        # OperationCoordinator has already redacted and bounded this result. Text
        # renders it literally rather than interpreting Git output as markup.
        detail.append(record.message or "No result detail was recorded.")
        self.query_one("#activity-detail-text", Static).update(detail)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        record_id = str(event.option.id)
        record = next((item for item in self._records if item.id == record_id), None)
        if record is not None:
            self._show_record(record)

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "activity-close":
            self.action_close()
