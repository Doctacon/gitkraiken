import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Link, Static, TextArea

from gitkraiken.models import Action
from gitkraiken.pull_request_dialog import PullRequestDialog
from gitkraiken.pull_requests import PullRequestError, PullRequestPlan, hosted_repository


class DialogApp(App):
    def compose(self) -> ComposeResult:
        yield Static("Workspace")


@pytest.fixture
def plan():
    return PullRequestPlan(
        remote="origin",
        repository=hosted_repository("https://github.com/org/repo"),
        source_branch="feature/[literal]",
        target_branch="test",
        guard=Action("create-pr"),
    )


@pytest.mark.parametrize("size", [(100, 40), (60, 20)])
async def test_composer_validates_title_retains_description_and_shows_copyable_url(
    plan, size, monkeypatch
):
    app = DialogApp()
    requests = []
    closed = []
    url = "https://github.com/org/repo/pull/42"
    opened = []
    monkeypatch.setattr(app, "open_url", opened.append)

    async def create(title, description):
        requests.append((title, description))
        return url

    async with app.run_test(size=size) as pilot:
        dialog = PullRequestDialog(plan, create)
        app.push_screen(dialog, closed.append)
        await pilot.pause()
        assert app.focused is dialog.query_one("#pr-title")
        assert dialog.query_one("#pr-submit").region.bottom <= size[1]
        assert dialog.query_one("#pr-submit").region.right <= size[0]
        assert "feature/[literal] → test" in dialog.query_one(".dialog-detail").renderable.plain
        dialog.query_one(Input).value = "   "
        await pilot.click("#pr-submit")
        assert not requests
        assert "Enter a title" in str(dialog.query_one("#pr-feedback").renderable)
        dialog.query_one(Input).value = "  Title [bold]literal[/bold]  "
        description = "A summary\n\n- Unicode: café\n- [link](https://example.com)\n"
        dialog.query_one(TextArea).load_text(description)
        # Keyboard submission is explicit; Enter in the description remains a newline.
        dialog.query_one(TextArea).focus()
        await pilot.press("ctrl+enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert requests == [("Title [bold]literal[/bold]", description)]
        assert dialog.created_url == url
        assert dialog.query_one(Link).url == url
        assert dialog.query_one(Link).text == url
        assert dialog.query_one(Link).region.bottom <= dialog.query_one("#pr-content").region.bottom
        assert not dialog.query_one("#pr-submit", Button).display
        dialog.query_one(Link).focus()
        await pilot.press("enter")
        assert opened == [url]
        assert not closed
        await pilot.click("#pr-copy")
        assert app.clipboard == url
        await pilot.press("ctrl+enter")
        assert len(requests) == 1
        await pilot.click("#pr-cancel")
        await pilot.pause()
        assert closed == [url]


async def test_cancel_never_calls_provider_and_empty_description_is_valid(plan):
    app = DialogApp()
    requests = []

    async def create(title, description):
        requests.append((title, description))
        return "https://github.com/org/repo/pull/1"

    async with app.run_test(size=(100, 40)) as pilot:
        dialog = PullRequestDialog(plan, create)
        app.push_screen(dialog)
        await pilot.pause()
        dialog.query_one(Input).value = "Abandoned draft"
        await pilot.press("escape")
        assert not requests
        dialog = PullRequestDialog(plan, create)
        app.push_screen(dialog)
        await pilot.pause()
        dialog.query_one(Input).value = "Title only"
        await pilot.click("#pr-submit")
        await app.workers.wait_for_complete()
        assert requests == [("Title only", "")]


async def test_inflight_cannot_duplicate_or_cancel_and_error_keeps_draft(plan):
    app = DialogApp()
    started = asyncio.Event()
    release = asyncio.Event()
    requests = []

    async def create(title, description):
        requests.append((title, description))
        started.set()
        await release.wait()
        raise PullRequestError("Cannot create [literal] PR. Run gh auth login in a terminal.")

    async with app.run_test(size=(100, 40)) as pilot:
        dialog = PullRequestDialog(plan, create)
        app.push_screen(dialog)
        await pilot.pause()
        dialog.query_one(Input).value = "Draft title"
        dialog.query_one(TextArea).load_text("Draft\n\nbody")
        await pilot.click("#pr-submit")
        await started.wait()
        await pilot.press("ctrl+enter", "escape")
        dialog.action_submit()
        assert app.screen is dialog
        assert len(requests) == 1
        assert dialog.query_one("#pr-cancel").disabled
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert dialog.query_one(Input).value == "Draft title"
        assert dialog.query_one(TextArea).text == "Draft\n\nbody"
        assert "[literal]" in dialog.query_one("#pr-feedback").renderable.plain
        assert "gh auth login" in dialog.query_one("#pr-feedback").renderable.plain
        assert not dialog.created_url
        assert not dialog.query_one("#pr-submit").disabled
        await pilot.press("escape")
        assert len(app.screen_stack) == 1
