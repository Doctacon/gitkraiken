import pytest
from test_app import settle
from textual.widgets import Button, DataTable, Static

from rhizome.app import RhizomeApp
from rhizome.git import Repository
from rhizome.persistence import WorkspaceStateStore
from rhizome.precision_diff import PrecisionDiff


@pytest.mark.parametrize("width", [200, 80])
async def test_split_inspection_is_read_only_and_returns_to_canonical_unified(
    git_repo, git_run, width
):
    path = git_repo / "split.txt"
    path.write_text("context\nold line\nold second\ntail\n")
    git_run(git_repo, "add", "split.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("context\nnew line\nnew second\ntail\n")
    before_index = git_run(git_repo, "diff", "--cached").stdout
    before_file = path.read_bytes()
    WorkspaceStateStore().save("global", {"diff_layout": "split"})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(width, 50 if width == 200 else 24)) as pilot:
        await settle(app, pilot)
        file = next(
            file
            for file in Repository(git_repo).snapshot(include_history=False).files
            if file.path == "split.txt"
        )
        app.open_diff(file)
        await settle(app, pilot)
        assert app.query_one("#split-diff").display
        assert not app.query_one(PrecisionDiff).display
        table = app.query_one("#split-lines", DataTable)
        assert any(
            "old line" in str(table.get_row_at(row)[0])
            and "new line" in str(table.get_row_at(row)[1])
            for row in range(table.row_count)
        )
        if width == 80:
            assert app.query_one("#split-narrow-notice").display
            unified = app.query_one("#split-unified", DataTable)
            source = [str(unified.get_row_at(row)[0]) for row in range(unified.row_count)]
            assert source.index("-old second") < source.index("+new line")
            unified.focus()
        else:
            table.focus()
        await pilot.press("v", "s")
        await settle(app, pilot)
        assert git_run(git_repo, "diff", "--cached").stdout == before_index
        assert path.read_bytes() == before_file
        assert await pilot.click("#split-show-unified")
        await pilot.pause()
        assert app.query_one(PrecisionDiff).display
        assert not app.query_one("#split-diff").display
        lines = app.query_one("#precision-lines", DataTable)
        assert any("old line" in str(lines.get_row_at(row)[0]) for row in range(lines.row_count))
        assert any("new line" in str(lines.get_row_at(row)[0]) for row in range(lines.row_count))
        assert git_run(git_repo, "diff", "--cached").stdout == before_index


async def test_split_newline_side_wrapping_and_explicit_canonical_staging(git_repo, git_run):
    path = git_repo / "split.txt"
    path.write_text("before")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    replacement = "after\n" + "long content " * 30 + "\n"
    path.write_text(replacement)
    WorkspaceStateStore().save("global", {"diff_layout": "split"})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(200, 50)) as pilot:
        await settle(app, pilot)
        app.open_diff(Repository(git_repo).snapshot(include_history=False).files[0])
        await settle(app, pilot)
        table = app.query_one("#split-lines", DataTable)
        markers = [
            table.get_row_at(row)
            for row in range(table.row_count)
            if "No newline" in str(table.get_row_at(row))
        ]
        assert len(markers) == 1
        assert "No newline" in str(markers[0][0])
        assert "No newline" not in str(markers[0][1])
        assert any(row.height > 1 for row in table.rows.values())
        assert git_run(git_repo, "show", ":split.txt").stdout == "before"
        assert path.read_text() == replacement
        assert await pilot.click("#split-show-unified")
        await pilot.press("h", "s")
        await settle(app, pilot)
        assert git_run(git_repo, "show", ":split.txt").stdout == replacement
        assert path.read_text() == replacement


async def test_split_committed_source_offers_honest_static_unified_view(git_repo, git_run):
    path = git_repo / "committed.txt"
    path.write_text("committed content\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    oid = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    WorkspaceStateStore().save("global", {"diff_layout": "split"})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(200, 50)) as pilot:
        await settle(app, pilot)
        app.open_diff(Repository(git_repo).detail(oid).files[0], oid=oid)
        await settle(app, pilot)
        assert str(app.query_one("#split-show-unified", Button).label) == "Unified view"
        assert "exact staging" not in str(app.query_one(".split-caption", Static).renderable)
        assert await pilot.click("#split-show-unified")
        await pilot.pause()
        assert app.query_one("#center-diff-scroll").display
        assert not app.query_one(PrecisionDiff).display
        assert "committed content" in str(app.query_one("#center-diff", Static).renderable)
        assert not git_run(git_repo, "status", "--porcelain").stdout


async def test_stale_split_returns_without_bypassing_canonical_review_guard(git_repo, git_run):
    path = git_repo / "stale.txt"
    path.write_text("before\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    WorkspaceStateStore().save("global", {"diff_layout": "split"})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(200, 50)) as pilot:
        await settle(app, pilot)
        app.open_diff(Repository(git_repo).snapshot(include_history=False).files[0])
        await settle(app, pilot)
        path.write_text("external edit\n")
        await pilot.press("ctrl+r")
        await settle(app, pilot)
        assert "STALE" in str(app.query_one(".split-caption", Static).renderable)
        assert str(app.query_one("#split-show-unified", Button).label) == "Unified view"
        assert await pilot.click("#split-show-unified")
        await pilot.press("h", "s")
        await settle(app, pilot)
        assert (
            "changed on disk" in str(app.query_one("#precision-status", Static).renderable).lower()
        )
        assert git_run(git_repo, "show", ":stale.txt").stdout == "before\n"
        assert path.read_text() == "external edit\n"
