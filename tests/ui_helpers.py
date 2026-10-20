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


async def click(pilot, widget, offset, *, times=1):
    assert await pilot.click(widget, offset=offset)
    for chain in range(2, times + 1):
        await post_click(pilot, widget, offset, chain=chain)
