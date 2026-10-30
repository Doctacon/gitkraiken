"""Check actual light surfaces as well as Rich colors that bypass Textual CSS."""

import pytest
from rich.color import Color
from rich.console import Console
from rich.text import Text
from textual.widgets import DataTable, Input, OptionList, TextArea, Tree

from rhizome.app import RhizomeApp, patch_text
from rhizome.dialogs import ActionPicker, BranchContextMenu, MessageDialog, PromptDialog
from rhizome.persistence import WorkspaceStateStore
from rhizome.theme import GRAPH_PALETTE, MUTED, PROVIDER_COLORS, SUBTLE_TEXT, TEAL, TEXT


def luminance(color: str) -> float:
    channels = (value / 255 for value in Color.parse(color).get_truecolor())
    linear = tuple(
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    )
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


async def test_workspace_defaults_to_light_with_readable_rich_colors(git_repo):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.theme == "rhizome-light"
        assert not app.current_theme.dark
        for selector in (
            "Screen",
            "#repo-header",
            "#refs-panel",
            "#history-panel",
            "#inspector-panel",
            "#commit-file-tree",
            "#center-diff-scroll",
            "#wip-table",
        ):
            assert luminance(app.query_one(selector).styles.background.hex) > 0.8, selector

        # Foreground Rich spans have priority over the table's selection color.
        table = app.query_one("#graph-table", DataTable)
        foregrounds = (TEXT, MUTED, SUBTLE_TEXT, *GRAPH_PALETTE, *PROVIDER_COLORS.values())
        for focused in (False, True):
            if focused:
                table.focus()
            else:
                app.query_one("#graph-filter", Input).focus()
            await pilot.pause()
            backgrounds = (
                table.styles.background.hex,
                table.get_component_styles("datatable--cursor").background.hex,
                table.get_component_styles("datatable--hover").background.hex,
            )
            for foreground in foregrounds:
                for background in backgrounds:
                    assert contrast(foreground, background) >= 4.5, (foreground, background)

        # Search fields stay compact; a generic Input border would hide this row.
        search = app.query_one("#ref-filter", Input)
        assert search.content_size.height == 1
        placeholder = search.get_component_styles("input--placeholder").color.hex
        assert contrast(placeholder, search.styles.background.hex) >= 4.5
        tree = app.query_one("#commit-file-tree", Tree)
        for focused in (False, True):
            if focused:
                app.query_one("#inspector-tabs").active = "details-tab"
                tree.focus()
            await pilot.pause()
            assert contrast(TEAL, tree.get_component_styles("tree--cursor").background.hex) >= 4.5

        editor = app.query_one("#commit-message", TextArea)
        assert editor.theme == "github_light"
        assert luminance(editor.styles.background.hex) > 0.8

        diff = patch_text("@@ -1 +1 @@\n-old\n+new\n")
        console = Console()
        background = app.query_one("#center-diff-scroll").styles.background.hex
        for span in diff.spans:
            color = diff.get_style_at_offset(console, span.start).color
            assert color is not None
            assert contrast(color.get_truecolor().hex, background) >= 4.5


@pytest.mark.parametrize("preference", ["light", "dark", "high-contrast"])
@pytest.mark.parametrize("graph_style", ["smooth", "compact"])
async def test_history_selection_highlights_without_underlining_rows(
    git_repo, git_run, preference, graph_style
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "Base")
    git_run(git_repo, "commit", "--allow-empty", "-m", "Selected commit")
    selected = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "commit", "--allow-empty", "-m", "Newest commit")
    WorkspaceStateStore().save("global", {"theme": preference, "graph_style": graph_style})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        app.jump_to(selected)
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#graph-table", DataTable)
        row_index = table.get_row_index(selected)
        row = table.ordered_rows[row_index]
        assert table.cursor_row == row_index
        assert row.height == (2 if graph_style == "smooth" else 1)

        for focused in (False, True):
            (table if focused else app.query_one("#graph-filter", Input)).focus()
            await pilot.pause()
            cursor = table.get_component_rich_style("datatable--cursor")
            assert cursor.bold
            assert cursor.bgcolor != table.rich_style.bgcolor
            start = (
                table.header_height
                + sum(row.height for row in table.ordered_rows[:row_index])
                - int(table.scroll_y)
            )
            # Smooth rows have a text line and a connector line. Neither should
            # have a full-width underline; ref-badge underlines are independent.
            for y in range(start, start + row.height):
                segments = [
                    segment
                    for segment in table.render_line(y)
                    if segment.text and segment.style and segment.style.meta.get("row") == row_index
                ]
                assert segments
                assert all(segment.style.bgcolor == cursor.bgcolor for segment in segments)
                assert all(not segment.style.underline for segment in segments)


@pytest.mark.parametrize("preference", ["dark", "high-contrast"])
async def test_saved_dark_palettes_reach_actual_history_spans_and_editor(
    git_repo, git_run, preference
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "readable history")
    WorkspaceStateStore().save("global", {"theme": preference})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(170, 48)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.theme == f"rhizome-{preference}"
        assert app.current_theme.dark
        table = app.query_one("#graph-table", DataTable)
        backgrounds = (
            table.styles.background.hex,
            table.get_component_styles("datatable--cursor").background.hex,
        )
        assert luminance(backgrounds[0]) < 0.1
        console = Console()
        for row in table.rows:
            for cell in table.get_row(row):
                if not isinstance(cell, Text) or not cell:
                    continue
                for offset in {0, *(span.start for span in cell.spans)}:
                    color = cell.get_style_at_offset(console, offset).color
                    if color:
                        for background in backgrounds:
                            assert contrast(color.get_truecolor().hex, background) >= 4.5
        assert app.query_one("#commit-message", TextArea).theme == "monokai"


async def test_dialogs_and_editors_remain_light(git_repo):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        dialogs = (
            (PromptDialog("Open repository"), "#prompt-dialog"),
            (MessageDialog("Commit message", "Readable text"), "#message-dialog"),
            (ActionPicker("Actions", [("refresh", "Refresh")]), "#action-picker"),
            (BranchContextMenu("main", "topic", screen_x=10, screen_y=10), "#branch-context-menu"),
        )
        for dialog, selector in dialogs:
            app.push_screen(dialog)
            await pilot.pause()
            surface = dialog.query_one(selector)
            assert luminance(surface.styles.background.hex) > 0.8
            for editor in dialog.query(TextArea):
                assert editor.theme == "github_light"
                assert luminance(editor.styles.background.hex) > 0.8
            for field in dialog.query(Input):
                assert contrast(field.styles.color.hex, field.styles.background.hex) >= 4.5
            for options in dialog.query(OptionList):
                highlight = options.get_component_rich_style("option-list--option-highlighted")
                foreground = highlight.color.get_truecolor().hex
                background = highlight.bgcolor.get_truecolor().hex
                assert contrast(foreground, background) >= 4.5
            await pilot.press("escape")
