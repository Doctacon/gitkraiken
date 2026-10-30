from test_app import click_reference, settle
from textual.widgets import DataTable, Input, Static

from rhizome.app import RhizomeApp


def _rows(app):
    return [
        row.key.value
        for row in app.query_one("#graph-table", DataTable).ordered_rows
        if not str(row.key.value).startswith("connector-") and row.key.value != "wip"
    ]


async def test_explicit_ref_focus_is_labeled_and_returns_to_original_graph(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    base = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "switch", "-c", "topic")
    git_run(git_repo, "commit", "--allow-empty", "-m", "topic work")
    topic = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "switch", "main")
    git_run(git_repo, "commit", "--allow-empty", "-m", "main work")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(150, 42)) as pilot:
        await settle(app, pilot)
        original = _rows(app)
        await click_reference(app, pilot, "refs/heads/topic")
        await settle(app, pilot)
        assert _rows(app) == original
        assert await pilot.click("#focus-history")
        await settle(app, pilot)
        assert _rows(app) == [topic, base]
        assert "Focused" in str(app.query_one("#history-caption", Static).render())
        assert await pilot.click("#exit-focus")
        await settle(app, pilot)
        assert _rows(app) == original
        assert app.query_one("#graph-filter", Input).value == ""
