"""A stalled external transport must not block local repository inspection."""

import json
import os
import shlex
import subprocess
import threading

from test_app import settle
from textual import events
from textual.widgets import DataTable, Static, Tree

import rhizome.app as app_module
from rhizome.app import RhizomeApp
from rhizome.models import Action
from rhizome.persistence import WorkspaceStateStore
from rhizome.sidebar import ReferenceSidebar


async def test_slow_explicit_hosted_read_keeps_local_inspection_available(
    git_repo, git_run, tmp_path, monkeypatch
):
    WorkspaceStateStore().save("global", {"remote_refresh_enabled": False})
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed locally\n")
    git_run(git_repo, "remote", "add", "origin", "https://github.com/org/repo")
    started, release = tmp_path / "started", tmp_path / "release"
    binary_dir = tmp_path / "provider"
    binary_dir.mkdir()
    gh = binary_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        ': > "$RHIZOME_TEST_STARTED"\n'
        'while [ ! -f "$RHIZOME_TEST_RELEASE" ]; do sleep 0.02; done\n'
        "printf '[]'\n"
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_dir}:{os.environ['PATH']}")
    run = subprocess.run

    def stub_provider(command, *args, **kwargs):
        if command[0] == "gh":
            # Keep the suite's ban on real gh/az commands; explicitly run only
            # this disposable shell stub through that external process boundary.
            command = ["/bin/sh", str(gh), *command[1:]]
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", stub_provider)
    monkeypatch.setenv("RHIZOME_TEST_STARTED", str(started))
    monkeypatch.setenv("RHIZOME_TEST_RELEASE", str(release))
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        try:
            await settle(app, pilot)
            await pilot.click("#sidebar-prs-load")
            for _ in range(200):
                await pilot.pause(0.01)
                if started.exists():
                    break
            assert started.exists()
            await pilot.click("#show-files")
            app.query_one("#wip-table", DataTable).focus()
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.02)
                if app.query_one("#precision-diff").display:
                    break
            assert app.query_one("#precision-diff").display
            assert not app.busy
            assert not release.exists()
        finally:
            release.touch()
            await app.workers.wait_for_complete()


async def test_pr_results_are_bound_to_the_captured_remote_even_after_an_aba_change(
    git_repo, git_run, monkeypatch
):
    WorkspaceStateStore().save("global", {"remote_refresh_enabled": False})
    original = "https://github.com/org/original"
    transient = "https://github.com/org/transient"
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    git_run(git_repo, "remote", "add", "origin", original)
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        run = subprocess.run
        changed = False
        api_calls = []

        def transport(command, *args, **kwargs):
            nonlocal changed
            if "get-url" in command and not changed:
                changed = True
                run(
                    ["git", "-C", str(git_repo), "remote", "set-url", "origin", transient],
                    check=True,
                )
            if command[0] == "gh":
                api_calls.append(command)
                run(
                    ["git", "-C", str(git_repo), "remote", "set-url", "origin", original],
                    check=True,
                )
                payload = [
                    {
                        "number": 7,
                        "title": "From the transient repository",
                        "head": {"ref": "feature"},
                        "base": {"ref": "main"},
                        "user": {"login": "reviewer"},
                        "draft": False,
                    }
                ]
                return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b"")
            return run(command, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", transport)
        await pilot.click("#sidebar-prs-load")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert changed and len(api_calls) == 1
        assert any("org/transient/pulls" in argument for argument in api_calls[0])
        assert git_run(git_repo, "remote", "get-url", "origin").stdout.strip() == original
        nodes = [app.query_one(ReferenceSidebar).query_one(Tree).root]
        labels = []
        while nodes:
            node = nodes.pop()
            labels.append(str(node.label))
            nodes.extend(node.children)
        assert not any("From the transient repository" in label for label in labels)
        assert "changed" in app.query_one("#status-line", Static).renderable.plain.lower()


async def test_hidden_workspace_pauses_network_until_terminal_focus(
    git_repo, git_run, bare_remote, tmp_path, monkeypatch
):
    clock = [100.0]
    monkeypatch.setattr(app_module, "monotonic", lambda: clock[0])
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "-u", "origin", "main")
    original = git_run(git_repo, "rev-parse", "HEAD").stdout
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        peer = tmp_path / "peer"
        git_run(tmp_path, "clone", str(bare_remote), str(peer))
        git_run(peer, "commit", "--allow-empty", "-m", "remote change")
        git_run(peer, "push")
        changed = git_run(peer, "rev-parse", "HEAD").stdout
        app.post_message(events.AppBlur())
        await pilot.pause()
        clock[0] += 61
        app.poll_repository()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert git_run(git_repo, "rev-parse", "refs/remotes/origin/main").stdout == original
        app.post_message(events.AppFocus())
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert git_run(git_repo, "rev-parse", "refs/remotes/origin/main").stdout == changed
        assert git_run(git_repo, "rev-parse", "HEAD").stdout == original


async def test_delayed_local_status_cannot_overwrite_a_completed_mutations_state(
    git_repo, git_run, monkeypatch
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    git_run(git_repo, "add", "example.txt")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        # The UI starts at the eventual result. Thus a snapshot-only generation
        # need not change when this app stages the same bytes again.
        git_run(git_repo, "restore", "--staged", "example.txt")
        started, release = threading.Event(), threading.Event()
        guard = threading.Lock()
        run = subprocess.run

        def delayed_status(command, *args, **kwargs):
            result = run(command, *args, **kwargs)
            should_delay = False
            if "status" in command and "--porcelain=v1" in command:
                with guard:
                    if not started.is_set():
                        started.set()
                        should_delay = True
            if should_delay:
                assert release.wait(10)
            return result

        monkeypatch.setattr(subprocess, "run", delayed_status)
        try:
            app.poll_repository()
            for _ in range(200):
                await pilot.pause(0.01)
                if started.is_set():
                    break
            assert started.is_set()
            app.run_mutation(Action("stage", paths=("example.txt",)))
            for _ in range(200):
                await pilot.pause(0.01)
                if git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n":
                    break
            assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
        finally:
            release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#wip-table", DataTable)
        assert table.row_count == 1
        assert str(table.get_row_at(0)[0]) == "STAGED"
        assert not app.busy


async def test_slow_automatic_fetch_does_not_block_opening_local_diff(
    git_repo, git_run, tmp_path, monkeypatch
):
    path = git_repo / "example.txt"
    path.write_text("committed\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed locally\n")
    git_run(git_repo, "remote", "add", "origin", "ssh://git@example.invalid/project.git")
    started, release = tmp_path / "started", tmp_path / "release"
    ssh = tmp_path / "fake-ssh"
    ssh.write_text(
        "#!/bin/sh\n"
        ': > "$RHIZOME_TEST_STARTED"\n'
        'while [ ! -f "$RHIZOME_TEST_RELEASE" ]; do sleep 0.02; done\n'
        "exit 1\n"
    )
    ssh.chmod(0o755)
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "ssh")
    monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")
    monkeypatch.setenv("GIT_SSH_COMMAND", shlex.quote(str(ssh)))
    monkeypatch.setenv("RHIZOME_TEST_STARTED", str(started))
    monkeypatch.setenv("RHIZOME_TEST_RELEASE", str(release))
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        try:
            for _ in range(200):
                await pilot.pause(0.01)
                if started.exists():
                    break
            assert started.exists(), "the real Git fetch never reached the fake transport"
            await pilot.click("#show-files")
            app.query_one("#wip-table", DataTable).focus()
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.02)
                if app.query_one("#precision-diff").display:
                    break
            assert app.query_one("#precision-diff").display
            lines = app.query_one("#precision-lines", DataTable)
            assert any(
                "reviewed locally" in str(lines.get_row_at(row)[0])
                for row in range(lines.row_count)
            )
            assert not release.exists()
        finally:
            release.touch()
            await app.workers.wait_for_complete()
