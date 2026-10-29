"""Real index effects from the keyboard-first precision workspace."""

from textual.widgets import DataTable, Static, TabbedContent, TextArea

from rhizome.app import RhizomeApp


async def test_keyboard_range_stages_only_selected_replacement(git_repo, git_run):
    path = git_repo / "example.txt"
    path.write_text("a\nb\nc\nd\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("a\nB\nc\nD\n")

    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
        table = app.query_one("#wip-table", DataTable)
        table.focus()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        # Opening a patch focuses its first changed line. Mark its start and
        # extend through the matching addition before explicitly staging.
        await pilot.press("v", "down", "s")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert git_run(git_repo, "show", ":example.txt").stdout == "a\nB\nc\nd\n"
        assert path.read_text() == "a\nB\nc\nD\n"


async def test_external_edit_preserves_review_and_refuses_stale_range(git_repo, git_run):
    path = git_repo / "example.txt"
    path.write_text("a\nb\nc\nd\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("a\nB\nc\nD\n")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
        app.query_one("#wip-table", DataTable).focus()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("v", "down")
        lines = app.query_one("#precision-lines", DataTable)
        reviewed = [str(lines.get_row_at(row)[0]) for row in range(lines.row_count)]
        cursor, scroll = lines.cursor_coordinate, lines.scroll_offset
        path.write_text("a\nUNREVIEWED\nc\nD\n")
        await pilot.press("ctrl+r")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert [str(lines.get_row_at(row)[0]) for row in range(lines.row_count)] == reviewed
        assert lines.cursor_coordinate == cursor and lines.scroll_offset == scroll
        assert "STALE" in app.query_one("#precision-status", Static).renderable
        await pilot.press("s")
        await app.workers.wait_for_complete()
        assert git_run(git_repo, "show", ":example.txt").stdout == "a\nb\nc\nd\n"
        assert path.read_text() == "a\nUNREVIEWED\nc\nD\n"

        await pilot.click("#precision-refresh")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert any("UNREVIEWED" in str(lines.get_row_at(row)[0]) for row in range(lines.row_count))
        assert "STALE" not in app.query_one("#precision-status", Static).renderable


async def test_narrow_mouse_and_keyboard_inspect_stage_commit_flow(git_repo, git_run):
    path = git_repo / "example.txt"
    path.write_text("a\nb\nc\nd\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("a\nB\nc\nD\n")
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.click("#show-files")
        app.query_one("#wip-table", DataTable).focus()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("v", "down", "s")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.click("#show-composer")
        await pilot.click("#commit-message")
        await pilot.press("F", "i", "x", "space", "s", "t", "a", "g", "e", "d")
        assert app.query_one("#commit-message", TextArea).text == "Fix staged"
        await pilot.click("#commit")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert git_run(git_repo, "log", "-1", "--format=%s").stdout.strip() == "Fix staged"
        assert git_run(git_repo, "show", "HEAD:example.txt").stdout == "a\nB\nc\nd\n"
        assert path.read_text() == "a\nB\nc\nD\n"
        assert app.query_one("#commit-message", TextArea).text == ""


async def test_mouse_hunk_selection_preserves_other_hunks(git_repo, git_run):
    path = git_repo / "example.txt"
    original = "".join(f"line {number}\n" for number in range(30))
    path.write_text(original)
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    edited = original.replace("line 2\n", "FIRST CHANGE\n").replace("line 27\n", "SECOND CHANGE\n")
    path.write_text(edited)
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.query_one("#inspector-tabs", TabbedContent).active = "wip-tab"
        app.query_one("#wip-table", DataTable).focus()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.click("#precision-hunk")
        await pilot.click("#precision-stage")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert git_run(git_repo, "show", ":example.txt").stdout == original.replace("line 2\n", "FIRST CHANGE\n")
        assert path.read_text() == edited
