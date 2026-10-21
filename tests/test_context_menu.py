import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import OptionList, Static, TextArea
from ui_helpers import post_click

from gitkraiken.dialogs import BranchContextMenu, MessageDialog


class ContextMenuApp(App[None]):
    def compose(self) -> ComposeResult:
        yield Static("background", id="background")


@pytest.mark.asyncio
async def test_menu_is_positioned_at_click_and_chooses_literal_rebase_action() -> None:
    app = ContextMenuApp()
    results: list[str | None] = []
    async with app.run_test(size=(80, 20)) as pilot:
        menu = BranchContextMenu(
            "[bold]feature/current[/bold]",
            "test/[red]literal[/red]",
            screen_x=11,
            screen_y=7,
        )
        app.push_screen(menu, results.append)
        await pilot.pause()

        options = menu.query_one(OptionList)
        option = options.get_option_at_index(0)
        assert option.id == "rebase"
        assert isinstance(option.prompt, Text)
        assert option.prompt.plain == (
            "Rebase [bold]feature/current[/bold] onto test/[red]literal[/red]"
        )
        assert options.region.offset == (11, 7)

        await pilot.press("enter")
        await pilot.pause()
        assert results == ["rebase"]
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_menu_wraps_long_label_and_exposes_full_literal_target() -> None:
    app = ContextMenuApp()
    current = "current-with-a-long-name"
    target = "target/[bold]literal-and-long[/bold]"
    full_label = f"Rebase {current} onto {target}"
    async with app.run_test(size=(24, 12)) as pilot:
        menu = BranchContextMenu(current, target, screen_x=23, screen_y=11)
        app.push_screen(menu)
        await pilot.pause()

        options = menu.query_one(OptionList)
        region = options.region
        assert region.x >= 0
        assert region.y >= 0
        assert region.right <= menu.size.width
        assert region.bottom <= menu.size.height
        assert region.width == menu.size.width
        assert region.height > 3
        assert region.height == options.virtual_size.height + 2
        assert options.scrollable_content_region.height == options.virtual_size.height
        assert isinstance(options.tooltip, Text)
        assert options.tooltip.plain == full_label


@pytest.mark.asyncio
async def test_menu_clamps_wrapped_height_to_short_screen() -> None:
    app = ContextMenuApp()
    async with app.run_test(size=(20, 6)) as pilot:
        menu = BranchContextMenu(
            "current-with-a-very-long-name",
            "target-with-an-even-longer-name",
            screen_x=19,
            screen_y=5,
        )
        app.push_screen(menu)
        await pilot.pause()

        options = menu.query_one(OptionList)
        assert options.region == (0, 0, 20, 6)
        assert options.virtual_size.height > options.scrollable_content_region.height
        assert options.tooltip.plain.endswith("target-with-an-even-longer-name")


@pytest.mark.asyncio
async def test_menu_mouse_click_chooses_rebase_action() -> None:
    app = ContextMenuApp()
    results: list[str | None] = []
    async with app.run_test(size=(60, 15)) as pilot:
        menu = BranchContextMenu("main", "test", screen_x=10, screen_y=5)
        app.push_screen(menu, results.append)
        await pilot.pause()

        options = menu.query_one(OptionList)
        await post_click(pilot, options, (2, 1), button=3)
        assert not results
        assert len(app.screen_stack) == 2
        assert await pilot.click(options, offset=(2, 1))
        await pilot.pause()
        assert results == ["rebase"]
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_menu_groups_actions_as_literal_text_and_keyboard_skips_separator() -> None:
    app = ContextMenuApp()
    results: list[str | None] = []
    actions = [
        None,
        ("checkout", "Checkout [bold]literal[/bold]"),
        None,
        ("copy", "Copy branch name"),
        ("delete", "Delete branch"),
    ]
    async with app.run_test(size=(50, 14)) as pilot:
        menu = BranchContextMenu("main", "topic", screen_x=2, screen_y=2, actions=actions)
        app.push_screen(menu, results.append)
        await pilot.pause()

        options = menu.query_one(OptionList)
        assert options.tooltip.plain == "Checkout [bold]literal[/bold]"
        # Check Textual's rendered rows, not just its option model. Separators
        # used to shift its row cache so labels and click metadata diverged.
        rows = [options.render_line(row).text.rstrip() for row in range(5)]
        assert rows == [
            "─" * options.virtual_size.width,
            "Checkout [bold]literal[/bold]",
            "─" * options.virtual_size.width,
            "Copy branch name",
            "Delete branch",
        ]
        copy_metadata = next(iter(options.render_line(3))).style.meta
        assert copy_metadata["option"] == options.get_option_index("copy")
        # The rounded OptionList border occupies the first screen row.
        await post_click(pilot, options, (2, 4))
        await pilot.pause()
        assert results == ["copy"]

        menu = BranchContextMenu("main", "topic", screen_x=2, screen_y=2, actions=actions)
        app.push_screen(menu, results.append)
        await pilot.pause()
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        assert results == ["copy", "delete"]


@pytest.mark.asyncio
async def test_menu_scrolls_to_last_action_and_repositions_after_resize() -> None:
    app = ContextMenuApp()
    actions = [(f"action-{number}", f"Action {number}") for number in range(12)]
    results: list[str | None] = []
    async with app.run_test(size=(22, 6)) as pilot:
        menu = BranchContextMenu("main", "topic", screen_x=21, screen_y=5, actions=actions)
        app.push_screen(menu, results.append)
        await pilot.pause()

        options = menu.query_one(OptionList)
        assert options.virtual_size.height > options.scrollable_content_region.height
        await pilot.press(*(["down"] * 11), "enter")
        await pilot.pause()
        assert results == ["action-11"]

        menu = BranchContextMenu("main", "topic", screen_x=21, screen_y=5, actions=actions)
        app.push_screen(menu)
        await pilot.pause()
        await pilot.resize_terminal(50, 16)
        await pilot.pause()
        options = menu.query_one(OptionList)
        assert options.region.right <= 50
        assert options.region.bottom <= 16


@pytest.mark.asyncio
async def test_message_dialog_preserves_multiline_text_and_can_cancel() -> None:
    app = ContextMenuApp()
    saved: list[str | None] = []
    original = "Subject\n\nBody [not markup]"
    async with app.run_test(size=(80, 25)) as pilot:
        dialog = MessageDialog("Edit latest commit", original)
        app.push_screen(dialog, saved.append)
        await pilot.pause()
        assert dialog.query_one(TextArea).text == original
        await pilot.press("ctrl+enter")
        await pilot.pause()
        assert saved == [original]

        dialog = MessageDialog("Edit latest commit", "new message")
        app.push_screen(dialog, saved.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert saved == [original, None]

        dialog = MessageDialog("Edit latest commit", " \n ")
        app.push_screen(dialog, saved.append)
        await pilot.pause()
        await pilot.press("ctrl+enter")
        await pilot.pause()
        assert saved == [original, None, None]


@pytest.mark.asyncio
async def test_menu_escape_and_outside_click_cancel() -> None:
    app = ContextMenuApp()
    results: list[str | None] = []
    async with app.run_test(size=(60, 15)) as pilot:
        first = BranchContextMenu("main", "test", screen_x=10, screen_y=5)
        app.push_screen(first, results.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert results == [None]

        second = BranchContextMenu("main", "test", screen_x=10, screen_y=5)
        app.push_screen(second, results.append)
        await pilot.pause()
        option_region = second.query_one(OptionList).region
        assert not option_region.contains(0, 0)
        assert await pilot.click(second, offset=(0, 0))
        await pilot.pause()
        assert results == [None, None]
        assert len(app.screen_stack) == 1
