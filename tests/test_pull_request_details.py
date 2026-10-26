import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Link, Static

from gitkraiken.clipboard import CopyResult
from gitkraiken.pull_request_dialog import PullRequestDetailsDialog
from gitkraiken.pull_requests import PullRequest


class DetailsApp(App):
    def compose(self) -> ComposeResult:
        yield Static("Workspace")


def request():
    return PullRequest(
        42,
        "Fix [bold]literal[/bold] sidebar",
        "https://github.com/org/repo/pull/42",
        "feature/[literal]",
        "main",
        "author[literal]",
        True,
    )


@pytest.mark.parametrize("size", [(100, 40), (60, 20), (40, 15)])
async def test_details_are_literal_and_browser_copy_actions_are_explicit(size, monkeypatch):
    app = DetailsApp()
    opened = []
    monkeypatch.setattr(app, "open_url", opened.append)
    async with app.run_test(size=size) as pilot:
        dialog = PullRequestDetailsDialog(request())
        app.push_screen(dialog)
        await pilot.pause()
        assert dialog.query_one("#pr-details-title", Static).renderable.plain == request().title
        assert "feature/[literal] → main" in dialog.query_one("#pr-details-meta").renderable.plain
        assert "author[literal]" in dialog.query_one("#pr-details-meta").renderable.plain
        assert not opened
        assert dialog.query_one("#pr-details-close").region.bottom <= size[1]
        assert dialog.query_one("#pr-details-close").region.right <= size[0]
        assert app.focused is dialog.query_one(Link)
        await pilot.press("enter")
        assert opened == [request().url]
        await pilot.click("#pr-details-copy")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.clipboard == request().url
        assert dialog.query_one("#pr-details-feedback").renderable == CopyResult.TERMINAL.value
        assert not dialog.query_one("#pr-details-feedback").has_class("copied")
        await pilot.press("escape")
        assert len(app.screen_stack) == 1


async def test_details_copy_blocks_duplicates_but_allows_close(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    copied = []

    async def copy(app, value):
        copied.append(value)
        started.set()
        await release.wait()
        return CopyResult.SYSTEM

    monkeypatch.setattr("gitkraiken.pull_request_dialog.copy_text", copy)
    app = DetailsApp()
    async with app.run_test(size=(100, 40)) as pilot:
        dialog = PullRequestDetailsDialog(request())
        app.push_screen(dialog)
        await pilot.pause()
        await pilot.click("#pr-details-copy")
        await asyncio.wait_for(started.wait(), timeout=1)
        button = dialog.query_one("#pr-details-copy", Button)
        assert button.disabled
        dialog.on_button_pressed(Button.Pressed(button))
        await pilot.press("escape")
        release.set()
        await app.workers.wait_for_complete()
        assert copied == [request().url]
        assert len(app.screen_stack) == 1
