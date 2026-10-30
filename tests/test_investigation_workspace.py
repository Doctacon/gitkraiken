import subprocess

from test_app import settle
from textual.widgets import Button, DataTable, Static

from rhizome.app import RhizomeApp


def _fast_history(repo, count):
    stream = []
    previous = 0
    for index in range(count):
        mark = index + 1
        subject = f"fast commit {index}"
        stream.extend(
            (
                "commit refs/heads/main\n",
                f"mark :{mark}\n",
                f"committer Test User <test@example.invalid> {946684800 + index} +0000\n",
                f"data {len(subject)}\n{subject}\n",
            )
        )
        if previous:
            stream.append(f"from :{previous}\n")
        stream.append("\n")
        previous = mark
    stream.append("done\n")
    subprocess.run(
        ["git", "fast-import", "--quiet"],
        cwd=repo,
        check=True,
        text=True,
        input="".join(stream),
        capture_output=True,
    )


def _rows(app):
    return [
        row.key.value
        for row in app.query_one("#graph-table", DataTable).ordered_rows
        if not str(row.key.value).startswith("connector-") and row.key.value != "wip"
    ]


async def test_workspace_defaults_to_topology_independent_of_graph_density(
    git_repo, git_run, monkeypatch
):
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2030-01-01T00:00:00Z")
    git_run(git_repo, "commit", "--allow-empty", "-m", "newer dated parent")
    parent = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2020-01-01T00:00:00Z")
    git_run(git_repo, "commit", "--allow-empty", "-m", "older dated child")
    child = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        assert _rows(app) == [child, parent]
        assert await pilot.click("#graph-style")
        await settle(app, pilot)
        assert _rows(app) == [child, parent]
        assert await pilot.click("#graph-order")
        await settle(app, pilot)
        assert _rows(app) == [parent, child]
        assert await pilot.click("#graph-style")
        await settle(app, pilot)
        assert _rows(app) == [parent, child]


async def test_workspace_replaces_bounded_older_page_and_restores_newer(git_repo, git_run):
    _fast_history(git_repo, 601)
    oracle = git_run(git_repo, "rev-list", "--topo-order", "HEAD").stdout.splitlines()
    app = RhizomeApp(git_repo)

    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert _rows(app) == oracle[:300]
        assert len(_rows(app)) == 300
        table = app.query_one("#graph-table", DataTable)
        assert "fast commit 600" in str(table.get_cell(oracle[0], "subject"))

        assert await pilot.click("#load-more")
        await settle(app, pilot)
        assert _rows(app) == oracle[300:600]
        assert len(_rows(app)) == 300
        assert "fast commit 300" in str(table.get_cell(oracle[300], "subject"))
        assert "300 commits" in str(app.query_one("#history-count", Static).renderable)

        assert await pilot.click("#history-newer")
        await settle(app, pilot)
        assert _rows(app) == oracle[:300]


async def test_workspace_polling_marks_changed_without_retargeting_until_refresh(git_repo, git_run):
    _fast_history(git_repo, 301)
    app = RhizomeApp(git_repo)

    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        captured_rows = _rows(app)
        assert len(captured_rows) == 300
        git_run(git_repo, "commit", "--allow-empty", "-m", "external advance")
        advanced = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()

        caption = ""
        for _ in range(30):
            await pilot.pause(0.1)
            caption = str(app.query_one("#history-caption", Static).renderable)
            if "CHANGED" in caption.upper():
                break
        assert "CHANGED" in caption.upper()
        assert _rows(app) == captured_rows
        assert advanced not in _rows(app)
        assert app.query_one("#load-more", Button).disabled

        await pilot.press("ctrl+r")
        await settle(app, pilot)
        assert _rows(app)[0] == advanced
        assert "CHANGED" not in str(app.query_one("#history-caption", Static).renderable).upper()
