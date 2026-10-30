from test_app import choose_option, click_reference, settle
from textual.widgets import Button, DataTable, Input, OptionList, Static

from rhizome.app import RhizomeApp
from rhizome.dialogs import ActionPicker, PromptDialog
from rhizome.investigation_dialogs import ComparisonDialog, FileInvestigationDialog


def _commit(repo, git_run, name):
    (repo / name).write_text(f"{name}\n")
    git_run(repo, "add", name)
    git_run(repo, "commit", "-m", name)
    return git_run(repo, "rev-parse", "HEAD").stdout.strip()


async def test_actions_compare_uses_full_oid_picker_and_fixed_endpoints(git_repo, git_run):
    base = _commit(git_repo, git_run, "base.txt")
    selected = _commit(git_repo, git_run, "selected.txt")
    app = RhizomeApp(git_repo)

    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("ctrl+p")
        assert isinstance(app.screen, ActionPicker)
        app.screen.query_one(OptionList).get_option("compare-commits")
        await choose_option(app, pilot, "compare-commits")

        assert isinstance(app.screen, ActionPicker)
        picker = app.screen.query_one(OptionList)
        picker.get_option(base)
        assert all(len(str(action_id)) in (40, 64) for action_id, _ in app.screen.actions)
        await choose_option(app, pilot, base)
        await settle(app, pilot)

        assert isinstance(app.screen, ComparisonDialog)
        requested = str(app.screen.query_one("#comparison-request", Static).renderable)
        assert f"Left:  {selected}" in requested
        assert f"Right: {base}" in requested
        await pilot.click("#comparison-branch")
        await settle(app, pilot)
        assert str(app.screen.query_one("#comparison-request", Static).renderable) == requested
        actual = str(app.screen.query_one("#comparison-actual", Static).renderable)
        assert "Actual branch-review comparison" in actual
        assert f"Merge base: {base}" in actual
        await pilot.click("#comparison-endpoints")
        await settle(app, pilot)
        assert str(app.screen.query_one("#comparison-request", Static).renderable) == requested
        actual = str(app.screen.query_one("#comparison-actual", Static).renderable)
        assert "Actual endpoints comparison" in actual
        assert f"Diff: {selected} → {base}" in actual
        assert await pilot.click("#comparison-close")
        assert len(app.screen_stack) == 1


async def test_selected_ref_review_opens_branch_mode_with_captured_endpoints(git_repo, git_run):
    base = _commit(git_repo, git_run, "base.txt")
    git_run(git_repo, "switch", "-c", "source")
    source = _commit(git_repo, git_run, "source.txt")
    git_run(git_repo, "switch", "main")
    main = _commit(git_repo, git_run, "main.txt")
    app = RhizomeApp(git_repo)

    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await click_reference(app, pilot, "refs/heads/source")
        await settle(app, pilot)
        await pilot.press("ctrl+p")
        assert isinstance(app.screen, ActionPicker)
        app.screen.query_one(OptionList).get_option("review-branch")
        await choose_option(app, pilot, "review-branch")
        await settle(app, pilot)

        assert isinstance(app.screen, ComparisonDialog)
        requested = str(app.screen.query_one("#comparison-request", Static).renderable)
        actual = str(app.screen.query_one("#comparison-actual", Static).renderable)
        assert f"Left:  {main}" in requested
        assert f"Right: {source}" in requested
        assert "Actual branch-review comparison" in actual
        assert f"Merge base: {base}" in actual


async def test_file_actions_switch_modes_and_close_without_changing_workspace_or_git(
    git_repo, git_run
):
    path = "literal [notes].txt"
    (git_repo / path).write_text("one\ntwo\n")
    git_run(git_repo, "add", path)
    git_run(git_repo, "commit", "-m", "initial notes")
    (git_repo / path).write_text("one\nchanged\n")
    git_run(git_repo, "add", path)
    git_run(git_repo, "commit", "-m", "selected notes")
    app = RhizomeApp(git_repo)
    before_refs = git_run(git_repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout
    before_status = git_run(git_repo, "status", "--porcelain=v1").stdout

    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        search = app.query_one("#graph-filter", Input)
        search.value = "selected notes"
        await settle(app, pilot)
        table = app.query_one("#graph-table", DataTable)
        before_rows = [row.key.value for row in table.ordered_rows]
        before_selection = table.ordered_rows[table.cursor_row].key.value

        await pilot.press("ctrl+p")
        assert isinstance(app.screen, ActionPicker)
        app.screen.query_one(OptionList).get_option("file-history")
        await choose_option(app, pilot, "file-history")
        assert isinstance(app.screen, PromptDialog)
        app.screen.query_one(Input).value = path
        await pilot.press("enter")
        await settle(app, pilot)

        assert isinstance(app.screen, FileInvestigationDialog)
        meta = str(app.screen.query_one("#file-investigation-meta", Static).renderable)
        entries = str(app.screen.query_one("#file-investigation-entries", Static).renderable)
        assert "History" in meta and "Locally complete" in meta
        assert path in entries and "selected notes" in entries
        assert app.screen.query_one("#file-investigation-older", Button).disabled

        assert await pilot.click("#file-investigation-blame")
        await settle(app, pilot)
        meta = str(app.screen.query_one("#file-investigation-meta", Static).renderable)
        entries = str(app.screen.query_one("#file-investigation-entries", Static).renderable)
        assert "Blame" in meta and "Locally complete" in meta
        assert "Line 1:" in entries and "Line 2:" in entries
        assert app.screen.query_one("#file-investigation-older", Button).disabled
        assert await pilot.click("#file-investigation-close")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert search.value == "selected notes"
        assert [row.key.value for row in table.ordered_rows] == before_rows
        assert table.ordered_rows[table.cursor_row].key.value == before_selection

    assert (
        git_run(git_repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout == before_refs
    )
    assert git_run(git_repo, "status", "--porcelain=v1").stdout == before_status
