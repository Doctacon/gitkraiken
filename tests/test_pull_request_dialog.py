import asyncio
import subprocess

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Link, Static, TextArea

from gitkraiken.clipboard import CopyResult
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
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.clipboard == url
        assert dialog.query_one("#pr-feedback").renderable == CopyResult.TERMINAL.value
        assert not dialog.query_one("#pr-feedback").has_class("copied")
        assert not dialog.query_one("#pr-copy").disabled
        await pilot.press("ctrl+enter")
        assert len(requests) == 1
        await pilot.click("#pr-cancel")
        await pilot.pause()
        assert closed == [url]


async def test_copy_created_url_uses_system_clipboard_without_osc52(plan, monkeypatch):
    """A terminal without OSC 52 must still copy through macOS's clipboard."""
    app = DialogApp()
    url = "https://github.com/org/repo/pull/42"
    copied = []
    monkeypatch.setattr("sys.platform", "darwin")
    for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda command: "/usr/bin/pbcopy" if command == "pbcopy" else None
    )

    def run(argv, **kwargs):
        copied.append((argv, kwargs["input"]))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", run)

    async def create(title, description):
        return url

    async with app.run_test(size=(100, 40)) as pilot:
        dialog = PullRequestDialog(plan, create)
        app.push_screen(dialog)
        await pilot.pause()
        dialog.query_one(Input).value = "Title"
        await pilot.click("#pr-submit")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.click("#pr-copy")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert copied == [(["/usr/bin/pbcopy"], url.encode("utf-8"))]
        assert "OSC 52" not in str(dialog.query_one("#pr-feedback").renderable)
        assert "copied" in str(dialog.query_one("#pr-feedback").renderable).lower()
        assert dialog.query_one("#pr-feedback").has_class("copied")
        assert not dialog.query_one("#pr-copy").disabled
        assert app.screen is dialog
        assert dialog.created_url == url


async def test_slow_clipboard_prevents_duplicate_copies_but_does_not_block_close(plan, monkeypatch):
    app = DialogApp()
    url = "https://github.com/org/repo/pull/42"
    started = asyncio.Event()
    release = asyncio.Event()
    copied = []
    closed = []

    async def copy(app, value):
        copied.append(value)
        started.set()
        await release.wait()
        return CopyResult.SYSTEM

    monkeypatch.setattr("gitkraiken.pull_request_dialog.copy_text", copy)

    async def create(title, description):
        return url

    async with app.run_test(size=(100, 40)) as pilot:
        dialog = PullRequestDialog(plan, create)
        app.push_screen(dialog, closed.append)
        await pilot.pause()
        dialog.query_one(Input).value = "Title"
        await pilot.click("#pr-submit")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.click("#pr-copy")
        await asyncio.wait_for(started.wait(), timeout=1)
        button = dialog.query_one("#pr-copy", Button)
        assert button.disabled
        dialog.on_button_pressed(Button.Pressed(button))
        assert copied == [url]
        await pilot.press("escape")
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert closed == [url]
        assert len(app.screen_stack) == 1


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
