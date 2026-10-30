import pytest
from test_app import settle
from test_m1_controls import control
from textual.widgets import Button, Select, Static, TextArea

from rhizome.app import RhizomeApp
from rhizome.persistence import WorkspaceStateStore


async def test_saved_command_keymap_replaces_binding_without_stealing_composer_text(
    git_repo, git_run
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    WorkspaceStateStore().save(
        "global", {"keymap": {"toggle_graph_style": "ctrl+alt+g", "command_picker": "ctrl+alt+p"}}
    )
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(140, 42)) as pilot:
        await settle(app, pilot)
        style = app.query_one("#graph-style", Button)
        assert "Smooth" in str(style.label)
        await pilot.press("ctrl+alt+g")
        await settle(app, pilot)
        assert "Compact" in str(style.label)
        await pilot.press("g")
        await settle(app, pilot)
        assert "Compact" in str(style.label)
        await pilot.press("f3")
        await pilot.click("#commit-message")
        await pilot.press(*"xg ordinary text")
        assert app.query_one("#commit-message", TextArea).text == "xg ordinary text"
        await pilot.press("ctrl+alt+p")
        await pilot.pause()
        assert app.screen.query_one("#action-options")
        await pilot.press("escape")
        assert await pilot.click("#help")
        await pilot.pause()
        help_text = str(app.screen.query_one("#help-text", Static).renderable)
        assert "ctrl+alt+g" in help_text and "5,000" not in help_text


async def test_workspace_preferences_override_reload_and_reset_without_changing_global_choices(
    git_repo, git_run
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "base")
    store = WorkspaceStateStore()
    store.save("global", {"theme": "dark"})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        assert app.theme == "rhizome-dark"
        await pilot.press("f4")
        await pilot.pause()
        app.screen.query_one("#theme", Select).value = "light"
        app.screen.query_one("#graph-order", Select).value = "chronological"
        assert await pilot.click("#controls-apply")
        await settle(app, pilot)
        assert app.theme == "rhizome-light"
        assert str(app.query_one("#graph-order", Button).label) == "Author time"
        assert store.load("global")["theme"] == "dark"
        assert store.load("global")["graph_order"] == "topo"
        await pilot.press("f6")
        await settle(app, pilot)
        await pilot.press("f6")
        await settle(app, pilot)
        assert str(app.query_one("#graph-order", Button).label) == "Author time"
        assert store.load("global")["graph_order"] == "topo"
        key = next((store.root / "workspaces").glob("*.json")).stem
        assert store.load(key)["preferences"]["graph_order"] == "chronological"
        current = store.load("global")
        store.save("global", {**current, "theme": "high-contrast"}, expected=current)
        await control(app, pilot, "reload-settings")
        await settle(app, pilot)
        assert app.theme == "rhizome-light"
        await control(app, pilot, "reset-overrides")
        await settle(app, pilot)
        assert app.theme == "rhizome-high-contrast"
        assert store.load(key)["preferences"] == {}
        valid = store.settings_path.read_bytes()
        store.settings_path.write_text("{ invalid JSON")
        await control(app, pilot, "reload-settings")
        await settle(app, pilot)
        assert app.theme == "rhizome-high-contrast"
        store.settings_path.write_bytes(valid)


@pytest.mark.parametrize(
    "preference,hint,expected",
    [
        ("auto", "15;0", "dark"),
        ("auto", "0;15", "light"),
        ("auto", "garbage;0", "light"),
        ("auto", "7;default", "light"),
        ("light", "15;0", "light"),
        ("dark", "", "dark"),
    ],
)
async def test_appearance_hints_fallback_and_explicit_override_in_real_app(
    git_repo, monkeypatch, preference, hint, expected
):
    monkeypatch.setenv("COLORFGBG", hint)
    WorkspaceStateStore().save("global", {"theme": preference})
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        assert app.theme == f"rhizome-{expected}"
