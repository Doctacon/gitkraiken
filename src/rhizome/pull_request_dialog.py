"""A small PR composer with an explicit, persistent created-link result."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Link, Static, TextArea

from .clipboard import CopyResult, copy_text
from .pull_requests import PullRequest, PullRequestPlan


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
        border: round #4f8790;
        background: #ffffff;
    }
    PullRequestDialog #pr-content { height: 1fr; min-height: 1; }
    PullRequestDialog.created > #pr-dialog { height: 18; }
    PullRequestDialog #pr-form, PullRequestDialog #pr-result { height: auto; }
    PullRequestDialog .pr-field-label { margin-top: 1; color: #36505b; }
    PullRequestDialog #pr-title { margin-bottom: 0; }
    PullRequestDialog #pr-description {
        height: 8;
        border: round #a8c1c7;
        background: #f5fafa;
    }
    PullRequestDialog #pr-feedback { height: auto; margin-top: 1; color: #b13a48; }
    PullRequestDialog #pr-feedback.copied { color: #007c78; }
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
        *,
        draft: tuple[str, str] = ("", ""),
        save_draft: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__()
        self.plan = plan
        self._create = create
        self._draft = draft
        self._save_draft = save_draft
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
                    yield Input(self._draft[0], placeholder="Summarize this change", id="pr-title")
                    yield Label("Description (optional · Markdown)", classes="pr-field-label")
                    yield TextArea(self._draft[1], id="pr-description", theme="github_light")
                    yield Static("", id="pr-draft-status", classes="dialog-detail")
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
        self._persist_draft()

    def _persist_draft(self) -> None:
        if not self.is_mounted or self.created_url:
            return
        status = self.query_one("#pr-draft-status", Static)
        if self._save_draft is None:
            status.update("Draft is kept only in this dialog")
            return
        title = self.query_one("#pr-title", Input).value
        description = self.query_one("#pr-description", TextArea).text
        try:
            self._save_draft(title, description)
        except Exception as error:
            status.update(Text("Draft not saved: " + str(error)))
        else:
            status.update("Draft saved locally" if title or description else "Draft is empty")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "pr-title":
            event.stop()
            self._persist_draft()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "pr-description":
            event.stop()
            self._persist_draft()

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


class PullRequestDetailsDialog(ModalScreen[None]):
    """Read-only PR summary; opening the provider and copying are explicit actions."""

    BINDINGS = [("escape", "close", "Close")]

    DEFAULT_CSS = """
    PullRequestDetailsDialog > #pr-details {
        width: 76;
        max-width: 100%;
        height: 22;
        max-height: 95%;
        padding: 1 2;
        border: round #4f8790;
        background: #ffffff;
    }
    PullRequestDetailsDialog #pr-details-content { height: 1fr; min-height: 1; }
    PullRequestDetailsDialog #pr-details-title { height: auto; text-style: bold; margin-bottom: 1; }
    PullRequestDetailsDialog #pr-details-meta { height: auto; color: #36505b; }
    PullRequestDetailsDialog #pr-details-url { height: auto; margin-top: 1; color: #48616c; }
    PullRequestDetailsDialog #pr-details-link { height: auto; margin-top: 1; color: #006d77; }
    PullRequestDetailsDialog #pr-details-feedback { height: auto; margin-top: 1; }
    PullRequestDetailsDialog #pr-details-feedback.copied { color: #007c78; }
    PullRequestDetailsDialog .dialog-buttons { height: 3; margin-top: 1; align: right middle; }
    PullRequestDetailsDialog .dialog-buttons Button { margin-left: 1; min-width: 10; }
    """

    def __init__(self, request: PullRequest) -> None:
        super().__init__()
        self.request = request
        self._copying = False

    def compose(self) -> ComposeResult:
        request = self.request
        with Vertical(id="pr-details"):
            yield Label(
                f"{'DRAFT' if request.draft else 'OPEN'} PULL REQUEST  #{request.number}",
                classes="dialog-title",
            )
            with VerticalScroll(id="pr-details-content"):
                yield Static(Text(request.title), id="pr-details-title")
                yield Static(
                    Text(
                        f"{request.source_branch} → {request.target_branch}\n"
                        f"Author: {request.author or 'Unknown'}\n"
                        "Review, comment, or merge in your browser."
                    ),
                    id="pr-details-meta",
                )
                yield Static(Text(request.url), id="pr-details-url")
                yield _PullRequestLink("Open in browser ↗", url=request.url, id="pr-details-link")
                yield Static("", id="pr-details-feedback")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Copy URL", id="pr-details-copy")
                yield Button("Close", id="pr-details-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#pr-details-link").focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "pr-details-close":
            self.action_close()
        elif event.button.id == "pr-details-copy" and not self._copying:
            self._copying = True
            event.button.disabled = True
            feedback = self.query_one("#pr-details-feedback", Static)
            feedback.remove_class("copied")
            feedback.update("Copying URL…")
            self.run_worker(self._copy_url(), exit_on_error=False)

    async def _copy_url(self) -> None:
        button = self.query_one("#pr-details-copy", Button)
        feedback = self.query_one("#pr-details-feedback", Static)
        try:
            result = await copy_text(self.app, self.request.url)
            feedback.update(result.value)
            feedback.set_class(result is CopyResult.SYSTEM, "copied")
        finally:
            self._copying = False
            button.disabled = False
