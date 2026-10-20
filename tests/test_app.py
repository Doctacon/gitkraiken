from __future__ import annotations

import subprocess

import pytest
from textual.widgets import DataTable, Input, Static, TabbedContent, TextArea

from gitkraiken.app import GitKraikenApp
from gitkraiken.demo import create_demo
from gitkraiken.dialogs import ConfirmDialog


@pytest.fixture
def demo_repo(tmp_path):
    return create_demo(tmp_path / "demo")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


async def settle(app, pilot):
    for _ in range(100):
        await pilot.pause(0.05)
        if not app.busy:
            await app.workers.wait_for_complete()
            await pilot.pause()
            return
    raise AssertionError("UI did not become idle")


async def test_real_repository_mount_and_commit_diff(demo_repo):
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        assert app.snapshot_data is not None
        assert app.snapshot_data.branch == "feature/workspace"
        assert len(app._commits) >= 12
        table = app.query_one("#graph-table", DataTable)
        assert table.row_count > len(app._commits)  # WIP + native merge connectors
        oid = next(iter(app._commits))
        app.jump_to(oid)
        await settle(app, pilot)
        assert app.query_one("#inspector-tabs", TabbedContent).active == "details-tab"
        assert app._commit_files
        assert "diff --git" in str(app.query_one("#commit-diff", Static).render())


async def test_stage_commit_and_refresh(demo_repo):
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(170, 50)) as pilot:
        await settle(app, pilot)
        app.jump_to("wip")
        await pilot.click("#stage-all")
        await settle(app, pilot)
        assert all(f.staged for f in app.snapshot_data.files)
        message = app.query_one("#commit-message", TextArea)
        message.load_text("Commit from the terminal UI")
        await pilot.click("#commit")
        await settle(app, pilot)
        assert git(demo_repo, "log", "-1", "--format=%s") == "Commit from the terminal UI"
        assert not app.snapshot_data.files
        assert message.text == ""


async def test_search_preserves_topology_and_branch_filter_can_be_cleared(demo_repo):
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        count = table.row_count
        app.query_one("#graph-filter", Input).value = "credential"
        await settle(app, pilot)
        assert app._search_matches
        assert table.row_count == count
        ident = next(
            key for key, (target, _) in app._ref_targets.items() if target == "refs/heads/main"
        )
        await pilot.click(f"#{ident}")
        await settle(app, pilot)
        assert app.ref_filter == "refs/heads/main"
        assert git(demo_repo, "branch", "--show-current") == "feature/workspace"
        await pilot.press("escape")
        await settle(app, pilot)
        assert not app.ref_filter
        assert table.row_count == count


async def test_destructive_action_cancel_is_noop(demo_repo):
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        file = next(f for f in app.snapshot_data.files if f.path == "README.md")
        original = (demo_repo / file.path).read_text()
        app.inspect_worktree_file(file, False)
        app.wip_action("discard")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("escape")
        await settle(app, pilot)
        assert (demo_repo / file.path).read_text() == original


async def test_mixed_index_worktree_file_has_two_diff_choices(demo_repo):
    file = demo_repo / "src/workspace.py"
    file.write_text(file.read_text() + "UNSTAGED_ONLY = True\n")
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(app, pilot)
        rows = [
            (f, staged) for f, staged in app._file_rows.values() if f.path == "src/workspace.py"
        ]
        assert len(rows) == 2
        app.inspect_worktree_file(*next(row for row in rows if row[1]))
        await settle(app, pilot)
        assert "UNSTAGED_ONLY" not in str(app.query_one("#diff-view", Static).render())
        app.inspect_worktree_file(*next(row for row in rows if not row[1]))
        await settle(app, pilot)
        assert "UNSTAGED_ONLY" in str(app.query_one("#diff-view", Static).render())


async def test_empty_repository_and_narrow_screen(tmp_path):
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    app = GitKraikenApp(tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(app, pilot)
        assert app.snapshot_data is not None
        assert not app._commits
        assert app.query_one("#graph-table", DataTable).row_count == 1
        app.action_toggle_refs()
        app.action_toggle_inspector()
        await pilot.pause()
        app.action_help()
        await pilot.pause()
        await pilot.press("escape")
        assert len(app.screen_stack) == 1


async def test_mutations_are_not_cancelled_by_refresh(demo_repo, monkeypatch):
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(app, pilot)
        from gitkraiken.models import Action

        real_execute = app.repo.execute
        import time

        def delayed(action):
            time.sleep(0.1)
            return real_execute(action)

        monkeypatch.setattr(app.repo, "execute", delayed)
        app.run_mutation(Action("stage", paths=("README.md",)))
        app.action_refresh()
        app.run_mutation(Action("unstage"))  # ignored while busy, never overlaps
        await settle(app, pilot)
        assert "README.md" in git(demo_repo, "diff", "--cached", "--name-only")


async def test_open_repository_replaces_state_without_touching_original(demo_repo, tmp_path):
    second = tmp_path / "second"
    subprocess.run(["git", "init", "-b", "main", str(second)], check=True, capture_output=True)
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        app.open_repository(str(second))
        await settle(app, pilot)
        assert app.repo.root == second
        assert app.snapshot_data.root == second
        assert not app.snapshot_data.files
        assert git(demo_repo, "branch", "--show-current") == "feature/workspace"
        app.open_repository(str(tmp_path / "missing"))
        await settle(app, pilot)
        assert app.repo.root == second
        assert "does not exist" in app._last_error


async def test_global_shortcuts_cannot_replace_repository_under_confirmation(demo_repo, tmp_path):
    second = tmp_path / "second"
    subprocess.run(["git", "init", "-b", "main", str(second)], check=True, capture_output=True)
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        file = next(f for f in app.snapshot_data.files if f.path == "README.md")
        app.inspect_worktree_file(file, False)
        app.wip_action("discard")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("ctrl+o", "ctrl+b", "ctrl+r")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert isinstance(app.screen, ConfirmDialog)
        app.open_repository(str(second))
        assert app.repo.root == demo_repo
        await pilot.press("escape")
        assert "Getting started" in (demo_repo / "README.md").read_text()


async def test_obsolete_selection_reads_are_skipped(demo_repo, monkeypatch):
    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        calls = []
        real_detail = app.repo.detail

        def detail(oid):
            calls.append(oid)
            return real_detail(oid)

        monkeypatch.setattr(app.repo, "detail", detail)
        async with app._serial:
            for commit in app._commits.values():
                app.inspect_commit(commit)
        await settle(app, pilot)
        assert len(calls) <= 2
        assert calls[-1] == app._selected_commit.oid


@pytest.mark.parametrize("width", [120, 170])
async def test_ref_labels_fit_and_graph_starts_near_top(demo_repo, width):
    from textual.widgets import Button

    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(width, 45)) as pilot:
        await settle(app, pilot)
        app.fit_ref_labels()
        await pilot.pause()
        for button in app.query(".ref-button").results(Button):
            assert button.label.cell_len < button.content_size.width
        assert app.query_one("#graph-table").region.y < 12
        assert app.query_one("#graph-table").region.height > 20
        assert any("…" in str(b.label) for b in app.query(".ref-button").results(Button))


async def test_branch_creation_confirmation_is_wired(demo_repo):
    from gitkraiken.dialogs import PromptDialog

    app = GitKraikenApp(demo_repo)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(app, pilot)
        await pilot.click("#branch")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        app.screen.query_one(Input).value = "feature/from-ui"
        await pilot.click("#confirm")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.click("#confirm")
        await settle(app, pilot)
        assert app.snapshot_data.branch == "feature/from-ui"
