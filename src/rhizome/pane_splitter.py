"""A small draggable divider for sibling panes."""

from __future__ import annotations

from typing import Literal

from textual import events
from textual.message import Message
from textual.widget import Widget


class PaneGroup(Widget):
    """Fit dividers after this container, rather than the terminal, is laid out."""

    DEFAULT_CSS = """
    PaneGroup { layout: vertical; width: 1fr; height: 1fr; }
    PaneGroup.horizontal { layout: horizontal; }
    """

    def on_resize(self, _event: events.Resize) -> None:
        self.call_after_refresh(self.fit_panes)

    def on_show(self) -> None:
        self.call_after_refresh(self.fit_panes)

    def fit_panes(self) -> None:
        for splitter in self.query_children(PaneSplitter):
            splitter.fit()


class PaneSplitter(Widget):
    """Resize one sibling pane while reserving room for another flexible sibling.

    ``target`` and ``flexible`` are selectors for immediate siblings of the splitter.
    Call :meth:`fit` after a parent layout change to restore a manually chosen size.
    """

    class Resized(Message):
        """A drag ended; expensive consumers can update once rather than per cell."""

        def __init__(self, splitter: PaneSplitter) -> None:
            self.splitter = splitter
            super().__init__()

    DEFAULT_CSS = """
    PaneSplitter {
        width: 1;
        height: 1fr;
        color: $foreground-muted;
        background: transparent;
    }

    PaneSplitter.-horizontal {
        width: 1fr;
        height: 1;
    }

    PaneSplitter:hover, PaneSplitter.-dragging {
        color: $primary;
        background: $primary-muted;
    }
    """

    def __init__(
        self,
        target: str,
        flexible: str,
        *,
        axis: Literal["width", "height"] = "width",
        grow_direction: int = 1,
        minimum: int,
        flexible_minimum: int,
        id: str,
    ) -> None:
        super().__init__(id=id)
        if grow_direction not in (-1, 1):
            raise ValueError("grow_direction must be -1 or 1")
        if minimum < 0 or flexible_minimum < 0:
            raise ValueError("minimum sizes must be non-negative")
        self.target = target
        self.flexible = flexible
        self.axis = axis
        self.grow_direction = grow_direction
        self.minimum = minimum
        self.flexible_minimum = flexible_minimum
        self.preferred_size: int | None = None
        self._drag_origin: int | None = None
        self._drag_size: int | None = None
        if axis == "height":
            self.add_class("-horizontal")

    def render(self) -> str:
        """Render a quiet, always-visible one-cell divider."""
        if self.axis == "width":
            return "\n".join("│" for _ in range(max(1, self.outer_size.height)))
        return "─" * max(1, self.outer_size.width)

    def _sibling(self, selector: str) -> Widget:
        if self.parent is None:
            raise RuntimeError("PaneSplitter must be mounted before it can resize panes")
        matches = list(self.parent.query_children(selector))
        if len(matches) != 1:
            raise RuntimeError(f"PaneSplitter selector {selector!r} must match one sibling")
        return matches[0]

    def _dimension(self, widget: Widget) -> int:
        """Return the CSS border-box dimension, not the content area."""
        return widget.outer_size.width if self.axis == "width" else widget.outer_size.height

    def _outer_dimension(self, widget: Widget) -> int:
        margin = widget.styles.margin
        if self.axis == "width":
            return widget.outer_size.width + margin.left + margin.right
        return widget.outer_size.height + margin.top + margin.bottom

    def _margin_dimension(self, widget: Widget) -> int:
        return self._outer_dimension(widget) - self._dimension(widget)

    def _limits(self) -> tuple[int, int]:
        """Return border-box limits using the parent's current laid-out geometry."""
        target = self._sibling(self.target)
        flexible = self._sibling(self.flexible)
        if self.parent is None:  # Kept for type checkers; _sibling already checks it.
            raise RuntimeError("PaneSplitter has no parent")
        parent_size = (
            self.parent.content_size.width
            if self.axis == "width"
            else self.parent.content_size.height
        )
        occupied = sum(
            self._outer_dimension(sibling)
            for sibling in self.parent.children
            if sibling.display and sibling not in (target, flexible)
        )
        maximum = (
            parent_size
            - occupied
            - self._margin_dimension(target)
            - self._margin_dimension(flexible)
            - self.flexible_minimum
        )
        return self.minimum, max(self.minimum, maximum)

    def _clamp(self, size: int) -> int:
        minimum, maximum = self._limits()
        return max(minimum, min(size, maximum))

    def _apply(self, size: int, *, save_preference: bool = False) -> None:
        target = self._sibling(self.target)
        if self.axis == "width":
            target.styles.width = size
        else:
            target.styles.height = size
        if save_preference:
            self.preferred_size = size

    def fit(self) -> None:
        """Reapply the manual size after a parent or terminal resize.

        Until a drag establishes ``preferred_size``, CSS remains entirely in charge.
        """
        if self.preferred_size is None or not self.display or not self.is_on_screen:
            return
        target = self._sibling(self.target)
        if not target.display:
            return
        self._apply(self._clamp(self.preferred_size))

    def on_mouse_down(self, event: events.MouseDown) -> None:
        event.stop()
        event.prevent_default()
        if event.button != 1:
            return
        self._drag_origin = event.screen_x if self.axis == "width" else event.screen_y
        self._drag_size = self._dimension(self._sibling(self.target))
        self.add_class("-dragging")
        self.capture_mouse()

    def _resize_to(self, screen_position: int) -> None:
        if self._drag_origin is None or self._drag_size is None:
            return
        size = self._drag_size + self.grow_direction * (screen_position - self._drag_origin)
        self._apply(self._clamp(size), save_preference=True)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag_origin is None or self._drag_size is None:
            return
        event.stop()
        event.prevent_default()
        position = event.screen_x if self.axis == "width" else event.screen_y
        self._resize_to(position)

    def _end_drag(self) -> None:
        was_dragging = self._drag_origin is not None
        self._drag_origin = None
        self._drag_size = None
        self.remove_class("-dragging")
        if was_dragging:
            self.post_message(self.Resized(self))

    def on_mouse_up(self, event: events.MouseUp) -> None:
        event.stop()
        event.prevent_default()
        if self._drag_origin is None:
            return
        if event.button == 1:
            position = event.screen_x if self.axis == "width" else event.screen_y
            self._resize_to(position)
        self._end_drag()
        self.release_mouse()

    def on_mouse_release(self, event: events.MouseRelease) -> None:
        """Clear drag state if capture is cancelled by another widget or unmounting."""
        self._end_drag()
        event.stop()

    def on_hide(self) -> None:
        if self.app.mouse_captured is self:
            self.release_mouse()
        self._end_drag()

    def on_unmount(self) -> None:
        self.on_hide()
