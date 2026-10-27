"""Check actual light surfaces as well as Rich colors that bypass Textual CSS."""

from rich.color import Color
from rich.console import Console
from textual.widgets import DataTable, Input, OptionList, TextArea, Tree

from rhizome.app import RhizomeApp, patch_text
from rhizome.dialogs import ActionPicker, BranchContextMenu, MessageDialog, PromptDialog
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
        assert app.theme == "textual-light"
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
