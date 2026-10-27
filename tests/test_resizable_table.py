from rich.text import Text
from textual.app import App, ComposeResult
from ui_helpers import drag, header_grip, post_click

from rhizome.resizable_table import ResizableDataTable


class TableApp(App):
    CSS = "ResizableDataTable { width: 50; height: 12; border: solid blue; }"

    def __init__(self):
        super().__init__()
        self.selected = []

    def compose(self) -> ComposeResult:
        yield ResizableDataTable(id="table", cursor_type="row")

    def on_mount(self):
        table = self.query_one(ResizableDataTable)
        table.add_column("Name", width=15, key="name")
        table.add_column("Other", width=40, key="other")
        table.add_row(Text("A Unicode name: 日本語"), "one", key="a")
        table.add_row("Another", "two", key="b")

    def on_data_table_row_selected(self, event):
        self.selected.append(event.row_key.value)


async def test_header_drag_updates_width_and_scroll_geometry_without_selecting():
    app = TableApp()
    async with app.run_test(size=(90, 30)) as pilot:
        table = app.query_one(ResizableDataTable)
        old_width = table.virtual_size.width
        point = header_grip(table, "name")
        style = app.screen.get_style_at(table.region.x + point[0], table.region.y + point[1])
        assert style.meta["resize_column"] == "name"
        await drag(pilot, table, point, (9, 2))  # Release over a data row, not the header.
        assert table.columns["name"].width == 24
        assert table.column_widths == {"name": 24}
        assert table.virtual_size.width == old_width + 9
        assert table.cursor_row == 0
        assert not app.selected
        assert app.mouse_captured is None
        # The next ordinary click is not swallowed.
        await pilot.click(table, offset=(3, 3))
        assert app.selected == ["b"]


async def test_scrolled_header_drag_and_minimum():
    app = TableApp()
    async with app.run_test(size=(90, 30)) as pilot:
        table = app.query_one(ResizableDataTable)
        await pilot.pause()
        table.scroll_to(x=table.max_scroll_x, animate=False)
        await pilot.pause()
        previous_scroll = table.scroll_x
        await drag(pilot, table, header_grip(table, "other"), (6, 0))
        assert table.columns["other"].width == 46
        assert table.scroll_x == previous_scroll
        table.scroll_home(animate=False)
        await pilot.pause()
        table.column_minimums["name"] = 10
        await drag(pilot, table, header_grip(table, "name"), (-30, 0))
        assert table.columns["name"].width == 10
        assert app.mouse_captured is None


async def test_widths_survive_clear_and_column_rebuild_and_right_click_does_not_resize():
    app = TableApp()
    async with app.run_test(size=(90, 30)) as pilot:
        table = app.query_one(ResizableDataTable)
        await post_click(pilot, table, header_grip(table, "name"), button=3)
        assert not table.column_widths
        assert app.mouse_captured is None
        await drag(pilot, table, header_grip(table, "name"), (5, 0))
        table.clear(columns=True)
        table.add_column("Name", width=15, key="name")
        table.add_row("Still here", key="a")
        await pilot.pause()
        assert table.columns["name"].width == 20
        assert table.get_cell("a", "name") == "Still here"


async def test_drag_can_convert_auto_width_columns_to_fixed_widths():
    app = TableApp()
    async with app.run_test(size=(90, 30)) as pilot:
        table = app.query_one(ResizableDataTable)
        table.clear(columns=True)
        table.add_column("File", key="file")
        table.add_row("a-long-file-name.py", key="file-row")
        await pilot.pause()
        original = table.columns["file"].content_width
        await drag(pilot, table, header_grip(table, "file"), (5, 0))
        assert not table.columns["file"].auto_width
        assert table.columns["file"].width == original + 5
        table.clear()
        table.add_row("even-longer-file-name-for-new-status.py", key="file-row")
        await pilot.pause()
        assert table.columns["file"].width == original + 5
