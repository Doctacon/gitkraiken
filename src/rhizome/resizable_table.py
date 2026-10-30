"""DataTable header grips, with widths remembered by stable column key.

Textual 1.0 has no column-width setter. Keep the small amount of cache and
scroll-geometry invalidation it requires here rather than in the workspace.
"""

from __future__ import annotations

from typing import ClassVar

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widgets import DataTable
from textual.widgets._data_table import ColumnKey


class ResizableDataTable(DataTable):
    """Drag the right edge of a header to resize, without selecting a row."""

    COMPONENT_CLASSES: ClassVar[set[str]] = DataTable.COMPONENT_CLASSES | {
        "resizable-table--grip",
        "resizable-table--grip-active",
    }

    DEFAULT_CSS = """
    ResizableDataTable > .resizable-table--grip { color: $text-muted; }
    ResizableDataTable > .resizable-table--grip-active { color: $primary; }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.column_widths: dict[str, int] = {}
        self.column_minimums: dict[str, int] = {}
        self._resize_key: str | None = None
        self._resize_origin = 0
        self._resize_width = 0
        self._suppress_resize_click = False

    def preferred_column_width(self, key: str, default: int) -> int:
        return max(self.column_minimums.get(key, 4), self.column_widths.get(key, default))

    def add_column(self, label, *, width=None, key=None, default=None) -> ColumnKey:
        if key is not None and (width is not None or key in self.column_widths):
            width = self.preferred_column_width(key, width or 4)
        return super().add_column(label, width=width, key=key, default=default)

    def set_column_width(self, key: str, width: int) -> None:
        """Apply a user width without rebuilding rows, selection, or scroll position."""
        width = max(self.column_minimums.get(key, 4), width)
        self.column_widths[key] = width
        column = self.columns[key]
        if not column.auto_width and column.width == width:
            return
        column.auto_width = False
        column.width = width
        self._invalidate_column_layout()

    def _invalidate_column_layout(self) -> None:
        self._clear_caches()
        self._update_count += 1
        self._require_update_dimensions = True
        self.check_idle()
        self.refresh(layout=True)

    def _render_cell(self, row_index, column_index, base_style, width, cursor=False, hover=False):
        lines = super()._render_cell(row_index, column_index, base_style, width, cursor, hover)
        if row_index != -1 or column_index < 0 or width < 1:
            return lines
        key = str(self.ordered_columns[column_index].key.value)
        component = (
            "resizable-table--grip-active" if key == self._resize_key else "resizable-table--grip"
        )
        grip = Segment(
            "│",
            self.get_component_rich_style(component, partial=True)
            + Style(meta={"row": -1, "column": column_index, "resize_column": key})
            + base_style.background_style,
        )
        # Replace only the last padding cell; leave DataTable's cached segments
        # untouched. Segment metadata makes hit-testing scroll- and Unicode-safe.
        return [[*Strip(line).crop(0, width - 1), grip] for line in lines]

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        event.prevent_default()
        self._suppress_resize_click = False
        key = event.style.meta.get("resize_column")
        if event.button == 1 and key in self.columns:
            event.stop()
            column = self.columns[key]
            self._resize_key = key
            self._resize_origin = event.screen_x
            self._resize_width = column.get_render_width(self) - 2 * self.cell_padding
            self._suppress_resize_click = True
            self.capture_mouse()
            self._invalidate_column_layout()
        else:
            await super()._on_mouse_down(event)

    def _on_mouse_move(self, event: events.MouseMove) -> None:
        event.prevent_default()
        if self._resize_key is not None:
            event.stop()
            self._drag_column(event.screen_x)
        else:
            super()._on_mouse_move(event)

    def _drag_column(self, screen_x: int) -> None:
        if self._resize_key in self.columns:
            self.set_column_width(
                self._resize_key, self._resize_width + screen_x - self._resize_origin
            )

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        event.prevent_default()
        if self._resize_key is not None:
            event.stop()
            if event.button == 1:
                self._drag_column(event.screen_x)
            self._end_column_resize()
        else:
            await super()._on_mouse_up(event)

    def _end_column_resize(self) -> None:
        self._resize_key = None
        if self.app.mouse_captured is self:
            self.release_mouse()
        self._invalidate_column_layout()

    def on_mouse_release(self, _event: events.MouseRelease) -> None:
        if self._resize_key is not None:
            self._end_column_resize()

    def on_hide(self) -> None:
        if self._resize_key is not None:
            self._end_column_resize()

    def on_unmount(self) -> None:
        if self.app.mouse_captured is self:
            self.release_mouse()

    def consume_resize_click(self, event: events.Click) -> bool:
        """Also called by subclasses which implement their own click semantics."""
        if not self._suppress_resize_click:
            return False
        self._suppress_resize_click = False
        event.prevent_default()
        event.stop()
        return True

    async def _on_click(self, event: events.Click) -> None:
        event.prevent_default()
        if not self.consume_resize_click(event):
            await super()._on_click(event)
