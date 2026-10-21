"""Real mouse-event delivery around Textual 1.0's Pilot multi-click bug."""

from textual import events
from textual.geometry import Offset


async def post_click(pilot, widget, offset, *, chain=1, button=1):
    # Pilot.click(times=2) leaks the first Click's `chain` argument into the
    # second MouseDown, whose constructor rejects it in Textual 1.0. Use the
    # same screen routing, with fresh arguments for each event instead.
    x, y = widget.region.x + offset[0], widget.region.y + offset[1]
    pilot.app.mouse_position = Offset(x, y)
    for event_type in (events.MouseDown, events.MouseUp, events.Click):
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
            **({"chain": chain} if event_type is events.Click else {}),
        )
        pilot.app.screen._forward_event(event)
        await pilot.pause()


def header_grip(table, key):
    """A column's last padding cell, relative to the bordered, scrolled table."""
    index = table.get_column_index(key)
    width = sum(column.get_render_width(table) for column in table.ordered_columns[: index + 1])
    return (table.gutter.left + width - int(table.scroll_x) - 1, table.gutter.top)


async def drag(pilot, widget, start, delta):
    """Drag through screen routing, including the trailing click some terminals send."""
    x, y = widget.region.x + start[0], widget.region.y + start[1]
    end_x, end_y = x + delta[0], y + delta[1]
    for event_type, px, py in (
        (events.MouseDown, x, y),
        (events.MouseMove, end_x, end_y),
        (events.MouseUp, end_x, end_y),
        (events.Click, end_x, end_y),
    ):
        pilot.app.mouse_position = Offset(px, py)
        pilot.app.screen._forward_event(
            event_type(
                widget,
                x=px,
                y=py,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()


async def click(pilot, widget, offset, *, times=1):
    assert await pilot.click(widget, offset=offset)
    for chain in range(2, times + 1):
        await post_click(pilot, widget, offset, chain=chain)
