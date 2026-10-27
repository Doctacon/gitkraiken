"""Background inspector reads must target the workspace, not the active modal."""

import threading

import pytest
from test_app import settle
from textual.widgets import Static

from rhizome.app import RhizomeApp
from rhizome.commit_files import CommitFileTree
from rhizome.dialogs import ActionPicker


@pytest.mark.parametrize("read_kind", ["detail", "diff"])
async def test_inspection_finishes_without_disturbing_open_actions(
    git_repo, git_run, monkeypatch, read_kind
):
    (git_repo / "note.txt").write_text("committed content\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "Base file")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        workspace = app.screen_stack[0]
        real_read = getattr(app.repo, read_kind)
        started, release = threading.Event(), threading.Event()

        def delayed(*args, **kwargs):
            started.set()
            assert release.wait(10)
            return real_read(*args, **kwargs)

        monkeypatch.setattr(app.repo, read_kind, delayed)
        try:
            if read_kind == "detail":
                app.inspect_commit(app._selected_commit)
            else:
                tree = app.query_one(CommitFileTree)
                app.inspect_commit_file(next(iter(tree._files.values())))
            worker = next(worker for worker in app.workers if worker.group == read_kind)
            for _ in range(100):
                if started.is_set():
                    break
                await pilot.pause(0.01)
            assert started.is_set()
            app.action_command_picker()
            await pilot.pause()
            menu = app.screen
            assert isinstance(menu, ActionPicker)
        finally:
            release.set()
        await worker.wait()
        await settle(app, pilot)
        assert app.screen is menu
        assert app._last_error == ""
        if read_kind == "detail":
            header = workspace.query_one("#commit-detail", Static).renderable.plain
            assert "Base file" in header
            assert "Loading" not in header
            assert len(workspace.query_one(CommitFileTree)._files) == 1
        else:
            assert (
                "+committed content" in workspace.query_one("#center-diff", Static).renderable.plain
            )
        await pilot.press("escape")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
