import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import OptionList, Static
from ui_helpers import post_click

from gitkraiken.dialogs import BranchContextMenu


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
