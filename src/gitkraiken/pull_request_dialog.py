"""A small PR composer with an explicit, persistent created-link result."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Link, Static, TextArea

from .clipboard import CopyResult, copy_text
from .pull_requests import PullRequestPlan


class _PullRequestLink(Link):
    # Textual 1.x Link's Enter binding names a nonexistent "select" action.
    BINDINGS = [("enter", "open_link", "Open link")]


class PullRequestDialog(ModalScreen[str | None]):
    """Keep drafts on failure; never dismiss or resubmit an in-flight request."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+enter", "submit", "Create PR")]

    DEFAULT_CSS = """
    PullRequestDialog > #pr-dialog {
        width: 76;
        max-width: 100%;
        height: 32;
        max-height: 95%;
        padding: 1 2;
        border: round #3d7c82;
        background: #142632;
    }
    PullRequestDialog #pr-content { height: 1fr; min-height: 1; }
    PullRequestDialog.created > #pr-dialog { height: 18; }
    PullRequestDialog #pr-form, PullRequestDialog #pr-result { height: auto; }
    PullRequestDialog .pr-field-label { margin-top: 1; color: #83aaa9; }
    PullRequestDialog #pr-title { margin-bottom: 0; }
    PullRequestDialog #pr-description {
        height: 8;
        border: round #294f59;
        background: #0d1821;
    }
    PullRequestDialog #pr-feedback { height: auto; margin-top: 1; color: #ee939b; }
    PullRequestDialog #pr-feedback.copied { color: #62d6c5; }
    PullRequestDialog #pr-url { width: 1fr; height: auto; margin-top: 1; }
    PullRequestDialog #pr-result { display: none; }
    PullRequestDialog.created #pr-form, PullRequestDialog.created .dialog-detail { display: none; }
    PullRequestDialog.created #pr-result { display: block; }
    PullRequestDialog .dialog-buttons { height: 3; margin-top: 1; align: right middle; }
    PullRequestDialog .dialog-buttons Button { margin-left: 1; min-width: 10; }
    """

    def __init__(
        self,
        plan: PullRequestPlan,
        create: Callable[[str, str], Awaitable[str]],
    ) -> None:
        super().__init__()
        self.plan = plan
        self._create = create
        self._submitting = False
        self._copying = False
        self.created_url = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="pr-dialog"):
            yield Label("CREATE PULL REQUEST", id="pr-heading", classes="dialog-title")
            with VerticalScroll(id="pr-content"):
                yield Static(
                    Text(
                        f"{self.plan.source_branch} → {self.plan.target_branch}\n"
                        f"{self.plan.repository.provider} · {self.plan.remote}\n"
                        f"{self.plan.repository.web_url}"
                    ),
                    classes="dialog-detail",
                )
                yield Static("", id="pr-feedback")
                with Vertical(id="pr-form"):
                    yield Label("Title (required)", classes="pr-field-label")
                    yield Input(placeholder="Summarize this change", id="pr-title")
                    yield Label("Description (optional · Markdown)", classes="pr-field-label")
                    yield TextArea(id="pr-description")
                    yield Static(
                        "Uses pushed branches; does not push or merge.\n"
                        "Add reviewers, work items and tags in the web UI afterward.",
                        classes="dialog-detail",
                    )
                with Vertical(id="pr-result"):
                    yield Static("Your pull request is ready. Open or copy the link below.")
                    yield _PullRequestLink("", url="", id="pr-url")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="pr-cancel")
                yield Button("Copy URL", id="pr-copy")
                yield Button("Done", id="pr-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#pr-copy", Button).display = False
        self.query_one("#pr-title", Input).focus()

    def action_cancel(self) -> None:
        if not self._submitting:
            self.dismiss(self.created_url or None)

    def action_submit(self) -> None:
        if self._submitting or self.created_url:
            return
        title = self.query_one("#pr-title", Input).value.strip()
        if not title:
            self.query_one("#pr-feedback", Static).update("Enter a title to create the PR.")
            self.query_one("#pr-title", Input).focus()
            return
        description = self.query_one("#pr-description", TextArea).text
        # Set this synchronously: rapid clicks / Ctrl+Enter cannot enqueue duplicates.
        self._submitting = True
        self.query_one("#pr-feedback", Static).update("Creating pull request…")
        for selector in ("#pr-title", "#pr-description", "#pr-submit", "#pr-cancel"):
            self.query_one(selector).disabled = True
        self.run_worker(self._submit(title, description), exit_on_error=False)

    async def _submit(self, title: str, description: str) -> None:
        try:
            self.created_url = await self._create(title, description)
        except Exception as error:
            # Literal text, including repository names and provider errors.
            feedback = self.query_one("#pr-feedback", Static)
            feedback.update(Text(str(error)))
            feedback.scroll_visible(animate=False)
        finally:
            self._submitting = False
            for selector in ("#pr-title", "#pr-description", "#pr-submit", "#pr-cancel"):
                self.query_one(selector).disabled = False
        if self.created_url:
            self.add_class("created")
            self.query_one("#pr-heading", Label).update("PULL REQUEST CREATED")
            link = self.query_one("#pr-url", Link)
            link.text = self.created_url
            link.url = self.created_url
            link.scroll_visible(animate=False)
            self.query_one("#pr-feedback", Static).update("")
            self.query_one("#pr-submit", Button).display = False
            self.query_one("#pr-copy", Button).display = True
            self.query_one("#pr-cancel", Button).label = "Close"
            self.query_one("#pr-copy", Button).focus()

    async def _copy_url(self) -> None:
        button = self.query_one("#pr-copy", Button)
        feedback = self.query_one("#pr-feedback", Static)
        try:
            result = await copy_text(self.app, self.created_url)
            feedback.update(result.value)
            feedback.set_class(result is CopyResult.SYSTEM, "copied")
        finally:
            self._copying = False
            button.disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "pr-submit":
            self.action_submit()
        elif event.button.id == "pr-copy" and self.created_url and not self._copying:
            self._copying = True
            event.button.disabled = True
            feedback = self.query_one("#pr-feedback", Static)
            feedback.remove_class("copied")
            feedback.update("Copying URL…")
            self.run_worker(self._copy_url(), exit_on_error=False)
        elif event.button.id == "pr-cancel":
            self.action_cancel()
