from __future__ import annotations

from test_app import settle
from textual.widgets import Button

from rhizome.app import RhizomeApp
from rhizome.persistence import WorkspaceStateStore


async def test_manual_fetch_updates_cache_without_checkout_or_index_changes(
    git_repo, bare_remote, tmp_path, git_run
):
    path = git_repo / "local.txt"
    path.write_text("local\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "local base")
    before_head = git_run(git_repo, "rev-parse", "HEAD").stdout
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "origin", "main")
    seed = tmp_path / "incoming-seed"
    git_run(tmp_path, "clone", str(bare_remote), str(seed))
    git_run(seed, "config", "user.name", "Test User")
    git_run(seed, "config", "user.email", "test@example.com")
    (seed / "incoming.txt").write_text("remote only\n")
    git_run(seed, "add", "incoming.txt")
    git_run(seed, "commit", "-m", "incoming")
    incoming = git_run(seed, "rev-parse", "HEAD").stdout
    git_run(seed, "push", "origin", "main")
    path.write_text("local unstaged edits\n")
    index = git_repo / ".git" / "index"
    before_index = index.read_bytes()
    WorkspaceStateStore().save("global", {"remote_refresh_enabled": False})
    app = RhizomeApp(git_repo)

    async with app.run_test(size=(170, 48)) as pilot:
        await settle(app, pilot)
        assert git_run(git_repo, "rev-parse", "refs/remotes/origin/main").stdout == before_head
        assert not app.query_one("#fetch", Button).disabled
        await pilot.click("#fetch")
        await settle(app, pilot)
        assert git_run(git_repo, "rev-parse", "refs/remotes/origin/main").stdout == incoming
        assert git_run(git_repo, "rev-parse", "HEAD").stdout == before_head
        assert index.read_bytes() == before_index
        assert path.read_text() == "local unstaged edits\n"
        assert not (git_repo / "incoming.txt").exists()
