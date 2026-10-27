from __future__ import annotations

from textual.widgets import DataTable

from rhizome.app import RhizomeApp


async def settle(app, pilot) -> None:
    for _ in range(100):
        await pilot.pause(0.02)
        if not app.busy:
            await app.workers.wait_for_complete()
            await pilot.pause()
            if not app.busy and not app.workers:
                return
    raise AssertionError("UI did not become idle")


def assert_workspace_fits(app) -> None:
    workspace = app.query_one("#workspace")
    history = app.query_one("#history-panel")
    assert history.region.width >= 34
    assert workspace.region.x <= history.region.x
    assert history.region.right <= workspace.region.right
    assert app.query_one("#graph-table", DataTable).row_count == len(app._graph_rows) + bool(
        app.snapshot_data.files
    )
    inspector = app.query_one("#inspector-panel")
    if inspector.display:
        assert inspector.region.right <= workspace.region.right
    for button in app.query("#toolbar Button"):
        assert button.region.right <= workspace.region.right


async def test_workspace_scales_through_tmux_pane_sizes(git_repo):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(140, 32)) as pilot:
        await settle(app, pilot)
        for height in (24, 32):
            for width in (60, 80, 100, 140):
                await pilot.resize_terminal(width, height)
                await settle(app, pilot)
                assert_workspace_fits(app)
                inspector = app.query_one("#inspector-panel")
                assert inspector.display is (width >= 85)

        # Ctrl+Arrow shortcuts still expose both side panels in the constrained slot.
        await pilot.resize_terminal(60, 24)
        await settle(app, pilot)
        app.action_toggle_inspector()
        await pilot.pause()
        assert app.query_one("#inspector-panel").display
        assert not app.query_one("#refs-panel").display
        assert_workspace_fits(app)
        app.action_toggle_refs()
        await pilot.pause()
        assert app.query_one("#refs-panel").display
        assert not app.query_one("#inspector-panel").display
        assert_workspace_fits(app)


async def test_resizing_preserves_explicit_side_panel_preferences(git_repo):
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(140, 32)) as pilot:
        await settle(app, pilot)
        app.action_toggle_refs()
        app.action_toggle_inspector()
        await pilot.pause()
        assert not app.query_one("#refs-panel").display
        assert not app.query_one("#inspector-panel").display

        await pilot.resize_terminal(60, 24)
        await settle(app, pilot)
        assert_workspace_fits(app)
        await pilot.resize_terminal(140, 32)
        await settle(app, pilot)
        assert not app.query_one("#refs-panel").display
        assert not app.query_one("#inspector-panel").display

        # An automatically hidden inspector returns on a large pane when the
        # user had not explicitly collapsed it.
        app.action_toggle_refs()
        app.action_toggle_inspector()
        await pilot.resize_terminal(80, 32)
        await settle(app, pilot)
        assert not app.query_one("#inspector-panel").display
        await pilot.resize_terminal(140, 32)
        await settle(app, pilot)
        assert app.query_one("#refs-panel").display
        assert app.query_one("#inspector-panel").display
        assert_workspace_fits(app)
