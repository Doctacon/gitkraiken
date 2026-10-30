import subprocess

from test_app import settle
from textual.widgets import DataTable, Input, Static

from rhizome.app import RhizomeApp


async def test_search_finds_unrendered_body_and_jumps_to_bounded_result(git_repo, git_run):
    stream = []
    for number in range(1, 302):
        message = f"commit {number}" + ("\n\nneedle outside rendered page" if number == 1 else "")
        stream.append(
            f"commit refs/heads/main\nmark :{number}\ncommitter Test <test@example.invalid> {1700000000 + number} +0000\ndata {len(message)}\n{message}\n"
        )
        if number > 1:
            stream.append(f"from :{number - 1}\n")
        stream.append("\n")
    subprocess.run(
        ["git", "fast-import", "--quiet"],
        cwd=git_repo,
        input="".join(stream),
        text=True,
        check=True,
        capture_output=True,
    )
    oldest = git_run(git_repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(150, 42)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        original = [row.key.value for row in table.ordered_rows]
        assert oldest not in original
        assert await pilot.click("#graph-filter")
        await pilot.press(*"needle")
        await settle(app, pilot)
        caption = str(app.query_one("#history-caption", Static).render())
        assert "301 examined" in caption
        assert "complete" in caption.lower()
        assert [row.key.value for row in table.ordered_rows] == original
        assert await pilot.click("#search-next")
        await settle(app, pilot)
        assert table.row_count <= 300
        assert table.ordered_rows[table.cursor_row].key.value == oldest
        assert app.query_one("#graph-filter", Input).value == "needle"


async def test_observed_scope_change_stays_stale_until_explicit_refresh(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "captured")
    captured = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        git_run(git_repo, "commit", "--allow-empty", "-m", "external")
        await pilot.pause(2.5)
        await settle(app, pilot)
        assert "CHANGED" in str(app.query_one("#history-caption", Static).render())
        git_run(git_repo, "reset", "--hard", captured)
        await pilot.pause(2.5)
        await settle(app, pilot)
        assert "CHANGED" in str(app.query_one("#history-caption", Static).render())
        await pilot.press("ctrl+r")
        await settle(app, pilot)
        assert "CHANGED" not in str(app.query_one("#history-caption", Static).render())
