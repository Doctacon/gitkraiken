from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.geometry import Offset
from textual.widgets import Static

from rhizome.pane_splitter import PaneSplitter


class SplitterApp(App[None]):
    CSS = """
    #row { height: 12; }
    #target { width: 20; }
    #flex { width: 1fr; }
    #other { width: 10; }
    #column { width: 30; height: 12; }
    #top { height: 4; }
    #bottom { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="row"):
            yield Static(id="target")
            yield PaneSplitter("#target", "#flex", minimum=8, flexible_minimum=15, id="split")
            yield Static(id="flex")
            yield Static(id="other")
        with Vertical(id="column"):
            yield Static(id="top")
            yield PaneSplitter(
                "#top", "#bottom", axis="height", minimum=2, flexible_minimum=3, id="vertical"
            )
            yield Static(id="bottom")


async def send(pilot, widget, event_type, x, y, *, button=1):
    pilot.app.mouse_position = Offset(x, y)
    event = event_type(
        widget,
        x=x,
        y=y,
        delta_x=0,
        delta_y=0,
        button=button,
        shift=False,
        meta=False,
        ctrl=False,
    )
    pilot.app.screen._forward_event(event)
    await pilot.pause()


async def test_horizontal_drag_clamps_captures_and_fits_after_resize():
    app = SplitterApp()
    async with app.run_test(size=(80, 30)) as pilot:
        splitter = app.query_one("#split", PaneSplitter)
        target = app.query_one("#target")
        assert splitter.preferred_size is None
        assert target._inline_styles.width is None

        x, y = splitter.region.x, splitter.region.y
        await send(pilot, splitter, events.MouseDown, x, y, button=3)
        assert app.mouse_captured is None
        assert splitter.preferred_size is None

        x, y = splitter.region.x, splitter.region.y
        await send(pilot, splitter, events.MouseDown, x, y)
        assert app.mouse_captured is splitter
        await send(pilot, splitter, events.MouseMove, x + 9, y)
        assert target.size.width == 29
        assert splitter.preferred_size == 29

        # Delivery remains with the splitter beyond its one-cell handle.
        await send(pilot, splitter, events.MouseMove, x - 99, y)
        assert target.size.width == 8
        await send(pilot, splitter, events.MouseUp, x - 99, y)
        assert app.mouse_captured is None

        x, y = splitter.region.x, splitter.region.y
        await send(pilot, splitter, events.MouseDown, x, y)
        await send(pilot, splitter, events.MouseUp, x + 42, y)
        assert splitter.preferred_size == 50

        await pilot.resize_terminal(45, 30)
        await pilot.pause()
        splitter.fit()
        await pilot.pause()
        assert target.outer_size.width == 19  # 45 - splitter - other - flexible minimum
        assert splitter.preferred_size == 50

        await pilot.resize_terminal(80, 30)
        await pilot.pause()
        splitter.fit()
        await pilot.pause()
        assert target.outer_size.width == 50
        assert splitter.preferred_size == 50

        inline_width = target._inline_styles.width
        splitter.preferred_size = 30
        target.display = False
        splitter.fit()
        assert target._inline_styles.width == inline_width
        target.display = True
        splitter.display = False
        splitter.fit()
        assert target._inline_styles.width == inline_width


async def test_negative_and_vertical_growth_use_screen_coordinates():
    app = SplitterApp()
    async with app.run_test(size=(80, 30)) as pilot:
        splitter = app.query_one("#split", PaneSplitter)
        splitter.grow_direction = -1
        target = app.query_one("#target")
        x, y = splitter.region.x, splitter.region.y
        await send(pilot, splitter, events.MouseDown, x, y)
        await send(pilot, splitter, events.MouseMove, x - 6, y)
        assert target.size.width == 26
        await send(pilot, splitter, events.MouseUp, x - 6, y)

        vertical = app.query_one("#vertical", PaneSplitter)
        top = app.query_one("#top")
        x, y = vertical.region.x, vertical.region.y
        await send(pilot, vertical, events.MouseDown, x, y)
        await send(pilot, vertical, events.MouseMove, x, y + 3)
        assert top.outer_size.height == 7
        await send(pilot, vertical, events.MouseUp, x, y + 3)


class BorderedSplitterApp(App[None]):
    CSS = """
    #bordered-row { height: 12; }
    #bordered-target { width: 20; padding: 1 2; border: solid $primary; }
    #bordered-flex { width: 1fr; }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="bordered-row"):
            yield Static(id="bordered-target")
            yield PaneSplitter(
                "#bordered-target", "#bordered-flex", minimum=12, flexible_minimum=15, id="bordered"
            )
            yield Static(id="bordered-flex")


async def test_border_box_sizes_and_final_mouse_up_position():
    app = BorderedSplitterApp()
    async with app.run_test(size=(60, 20)) as pilot:
        splitter = app.query_one("#bordered", PaneSplitter)
        target = app.query_one("#bordered-target")
        assert target.outer_size.width == 20
        assert target.size.width == 14  # Padding and border are outside content, inside CSS width.
        assert splitter.render() == "\n".join("│" for _ in range(splitter.outer_size.height))

        x, y = splitter.region.x, splitter.region.y
        await send(pilot, splitter, events.MouseDown, x, y)
        # MouseUp may be the only position update received by the handle.
        await send(pilot, splitter, events.MouseUp, x + 5, y)
        assert target.outer_size.width == 25
        assert splitter.preferred_size == 25

        x, y = splitter.region.x, splitter.region.y
        await send(pilot, splitter, events.MouseDown, x, y)
        await send(pilot, splitter, events.MouseUp, x - 99, y)
        assert target.outer_size.width == 12
        assert splitter.preferred_size == 12
