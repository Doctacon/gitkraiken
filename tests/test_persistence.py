import json
import stat

import pytest

from rhizome.persistence import (
    DEFAULT_SETTINGS,
    StateCorruptionError,
    StateSecurityError,
    StateValidationError,
    WorkspaceStateStore,
)
from rhizome.settings import COMMANDS, CURATED_SETTINGS, validate_keymap, validate_preferences


@pytest.mark.parametrize("setting", ["theme", "graph_style", "graph_order"])
def test_non_string_settings_choices_are_rejected_without_overwrite(tmp_path, setting):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("global", {"theme": "dark"})
    before = store.settings_path.read_bytes()

    with pytest.raises(StateValidationError):
        store.save("global", {setting: []})

    assert store.settings_path.read_bytes() == before


def test_workspace_state_reopens_from_an_explicit_private_root(tmp_path):
    root = tmp_path / "state"
    first = WorkspaceStateStore(root)
    payload = {
        "commit_draft": {"summary": "Keep this draft", "body": "Details"},
        "layout": {"active_pane": "diff"},
    }

    first.save("worktree-a", payload)

    reopened = WorkspaceStateStore(root)
    assert reopened.load("worktree-a") == payload
    assert reopened.root == root
    assert reopened.settings_path == root / "settings.json"
    assert json.loads((root / "workspaces" / "worktree-a.json").read_text()) == {
        "schema_version": 1,
        "data": payload,
    }


def test_defaults_and_environment_override_are_independent_of_home(tmp_path, monkeypatch):
    state_home = tmp_path / "explicit-state"
    monkeypatch.setenv("RHIZOME_STATE_HOME", str(state_home))
    monkeypatch.setenv("HOME", str(tmp_path / "not-used"))

    store = WorkspaceStateStore()

    assert store.root == state_home
    assert store.load("global") == DEFAULT_SETTINGS
    assert not store.settings_path.exists()


def test_platform_default_respects_xdg_and_macos_application_support(tmp_path, monkeypatch):
    monkeypatch.delenv("RHIZOME_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    xdg_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_home))
    monkeypatch.setattr("rhizome.persistence.sys.platform", "darwin")
    assert WorkspaceStateStore().root == xdg_home / "rhizome"

    monkeypatch.delenv("XDG_STATE_HOME")
    assert (
        WorkspaceStateStore().root
        == tmp_path / "home" / "Library" / "Application Support" / "Rhizome"
    )


def test_global_settings_are_human_editable_and_validated(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("global", {"theme": "dark", "remote_refresh_interval": 120})

    assert store.settings_path.name == "settings.json"
    assert store.load("global") == {
        **DEFAULT_SETTINGS,
        "theme": "dark",
        "remote_refresh_interval": 120,
    }

    document = json.loads(store.settings_path.read_text())
    document["data"]["theme"] = "high-contrast"
    store.settings_path.write_text(json.dumps(document), encoding="utf-8")
    assert store.load("global")["theme"] == "high-contrast"

    with pytest.raises(StateValidationError, match="valid JSON state"):
        store.save("global", {"theme": "neon"})


def test_workspace_keys_and_state_are_isolated_and_clear_is_scoped(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("worktree-a", {"commit_draft": "A"})
    store.save("worktree-b", {"pull_request_draft": {"title": "B"}})

    store.clear("worktree-a")

    assert store.load("worktree-a") == {}
    assert store.load("worktree-b") == {"pull_request_draft": {"title": "B"}}
    store.clear("worktree-a")  # Missing state is an idempotent no-op.

    for unsafe_key in ("../escape", "nested/key", ".", "Global", ""):
        with pytest.raises(StateValidationError, match="workspace key"):
            store.load(unsafe_key)


def test_save_rejects_non_json_data_without_disturbing_existing_state(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("worktree-a", {"commit_draft": "preserved"})

    cyclic: dict = {}
    cyclic["cycle"] = cyclic
    invalid_payloads = [
        {"value": object()},
        {"value": float("nan")},
        {1: "JSON object keys must be strings"},
        cyclic,
    ]
    for payload in invalid_payloads:
        with pytest.raises(StateValidationError, match="valid JSON state"):
            store.save("worktree-a", payload)
        assert store.load("worktree-a") == {"commit_draft": "preserved"}

    with pytest.raises(StateValidationError, match="dictionary"):
        store.save("worktree-a", ["not", "a", "dict"])  # type: ignore[arg-type]


def test_atomic_replace_failure_preserves_previous_document(tmp_path, monkeypatch):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("worktree-a", {"commit_draft": "old"})
    target = store.root / "workspaces" / "worktree-a.json"
    previous_bytes = target.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("rhizome.persistence.os.replace", fail_replace)
    with pytest.raises(OSError, match="save workspace state safely"):
        store.save("worktree-a", {"commit_draft": "new"})

    assert target.read_bytes() == previous_bytes
    assert not list(target.parent.glob(".worktree-a.json.*.tmp"))


def test_corruption_is_reported_safely_and_cannot_be_silently_overwritten(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("worktree-a", {"commit_draft": "valuable"})
    target = store.root / "workspaces" / "worktree-a.json"
    target.write_text('{"schema_version": 1, "data": ', encoding="utf-8")

    with pytest.raises(StateCorruptionError) as load_error:
        store.load("worktree-a")
    assert str(target) not in str(load_error.value)
    assert "valuable" not in str(load_error.value)

    corrupt_bytes = target.read_bytes()
    with pytest.raises(StateCorruptionError):
        store.save("worktree-a", {"commit_draft": "replacement"})
    assert target.read_bytes() == corrupt_bytes

    store.clear("worktree-a")
    store.save("worktree-a", {"commit_draft": "replacement"})
    assert store.load("worktree-a") == {"commit_draft": "replacement"}


def test_store_uses_private_permissions_for_directories_and_files(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("worktree-a", {"layout": {"pane": "graph"}})
    store.save("global", {"offline": True})

    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / "workspaces").stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / "workspaces" / "worktree-a.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(store.settings_path.stat().st_mode) == 0o600


def test_state_store_refuses_managed_install_subtrees_before_creation(tmp_path):
    install_root = tmp_path / "managed-install"
    (install_root / "store").mkdir(parents=True)
    (install_root / ".rhizome-install-root").write_text("rhizome-install-root-v1\n")
    state_root = install_root / "store" / "private-state"

    with pytest.raises(StateSecurityError, match="managed Rhizome installation"):
        WorkspaceStateStore(state_root)

    assert not state_root.exists()


def test_existing_store_refuses_writes_after_ancestor_becomes_managed(tmp_path):
    install_root = tmp_path / "later-managed"
    state_root = install_root / "state"
    store = WorkspaceStateStore(state_root)
    store.save("worktree-a", {"commit_draft": "preserved"})
    target = state_root / "workspaces" / "worktree-a.json"
    before = target.read_bytes()
    (install_root / ".rhizome-install-root").write_text("rhizome-install-root-v1\n")

    with pytest.raises(StateSecurityError, match="managed Rhizome installation"):
        store.save("worktree-a", {"commit_draft": "replacement"})
    with pytest.raises(StateSecurityError, match="managed Rhizome installation"):
        store.clear("worktree-a")

    assert target.read_bytes() == before


def test_state_store_refuses_symlink_alias_into_managed_install(tmp_path):
    install_root = tmp_path / "managed-install"
    payload = install_root / "store" / ("a" * 40)
    payload.mkdir(parents=True)
    (install_root / ".rhizome-install-root").write_text("rhizome-install-root-v1\n")
    alias = tmp_path / "payload-alias"
    alias.symlink_to(payload, target_is_directory=True)
    state_root = alias / "private-state"

    with pytest.raises(StateSecurityError, match="managed Rhizome installation"):
        WorkspaceStateStore(state_root)

    assert not (payload / "private-state").exists()


def test_symlinked_store_paths_are_rejected_without_touching_the_target(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("do not touch", encoding="utf-8")
    root = tmp_path / "state"
    store = WorkspaceStateStore(root)
    workspaces = root / "workspaces"
    workspaces.mkdir(mode=0o700)
    (workspaces / "worktree-a.json").symlink_to(outside)

    with pytest.raises(StateSecurityError, match="symbolic link"):
        store.load("worktree-a")
    with pytest.raises(StateSecurityError, match="symbolic link"):
        store.save("worktree-a", {"commit_draft": "new"})
    with pytest.raises(StateSecurityError, match="symbolic link"):
        store.clear("worktree-a")
    assert outside.read_text(encoding="utf-8") == "do not touch"


def test_command_catalog_preserves_existing_bindings_and_adds_safe_m2_defaults():
    assert len({command.id for command in COMMANDS}) == len(COMMANDS)
    existing = [
        ("quit", "ctrl+q", "quit", False, True),
        ("command_picker", "ctrl+p", "command_picker", True, True),
        ("open_repository", "ctrl+o", "open_repository", True, True),
        ("refresh", "ctrl+r", "refresh", True, True),
        ("activity", "ctrl+l", "activity", True, True),
        ("new_branch", "ctrl+b", "new_branch", True, True),
        ("focus_filter", "f,slash", "focus_filter", False, True),
        ("refresh_shortcut", "r", "refresh", False, True),
        ("command_picker_shortcut", "x", "command_picker", False, True),
        ("toggle_graph_style", "g", "toggle_graph_style", False, True),
        ("help", "question_mark", "help", False, True),
        ("next_row", "j", "next_row", False, False),
        ("previous_row", "k", "previous_row", False, False),
        ("toggle_refs", "ctrl+left", "toggle_refs", False, False),
        ("toggle_inspector", "ctrl+right", "toggle_inspector", False, False),
        ("show_files", "f2", "show_files", False, False),
        ("show_composer", "f3", "show_composer", False, False),
        ("workspace_settings", "f4", "workspace_settings", False, False),
        ("clear_filter", "escape", "clear_filter", False, False),
    ]
    assert [
        (command.id, command.keys, command.action, command.priority, command.show)
        for command in COMMANDS[: len(existing)]
    ] == existing
    defaults = {command.id: command.keys for command in COMMANDS[len(existing) :]}
    assert defaults == {
        "focus_history": "f5",
        "exit_focus": "shift+f5",
        "toggle_history_order": "f6",
        "compare_history": "f7",
        "file_history": "f8",
        "blame": "f9",
        "history_newer": "ctrl+pageup",
        "history_older": "ctrl+pagedown",
        "search_criteria": "shift+f",
        "stop_search": "f10",
        "widen_search": "ctrl+f5",
    }
    assert "protected_branches" not in CURATED_SETTINGS


def test_keymap_validation_accepts_catalog_ids_and_normalizes_textual_keys():
    assert validate_keymap({}) == {}
    assert validate_keymap(
        {
            "focus_history": "CTRL+F11",
            "toggle_refs": "ctrl+left",
            "clear_filter": "escape",
        }
    ) == {
        "focus_history": "ctrl+f11",
        "toggle_refs": "ctrl+left",
        "clear_filter": "escape",
    }


@pytest.mark.parametrize(
    "mapping",
    [
        {"unknown": "f11"},
        {"focus_history": "f6"},
        {"focus_history": "/"},
        {"focus_history": "not_a_textual_key"},
        {"focus_history": "action_focus_history()"},
        {"focus_history": "escape"},
        {"focus_history": "alt+escape"},
        {"focus_history": "enter"},
        {"focus_history": "ctrl+enter"},
        {"focus_history": "shift+tab"},
        {"focus_history": "left"},
        {"focus_history": "ctrl+c"},
        {"command_picker": "a"},
        {"command_picker": "shift+a"},
    ],
)
def test_keymap_validation_rejects_unknown_conflicting_or_unsafe_bindings(mapping):
    with pytest.raises(ValueError):
        validate_keymap(mapping)


def test_preferences_validate_curated_choices_and_compatibility_fields():
    for theme in ("auto", "light", "dark", "high-contrast"):
        assert validate_preferences({"theme": theme}) == {"theme": theme}
    assert (
        validate_preferences(
            {
                "diff_layout": "split",
                "graph_style": "compact",
                "graph_order": "chronological",
                "excluded_workspaces": ["a" * 64],
                "protected_branches": ["main"],
            }
        )["diff_layout"]
        == "split"
    )
    for invalid in (
        {"theme": "neon"},
        {"diff_layout": "sideways"},
        {"remote_refresh_interval": True},
        {"new_policy": []},
    ):
        with pytest.raises(ValueError):
            validate_preferences(invalid)


def test_global_store_keeps_priority_commands_off_printable_alias_keys(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")

    for command_id, key in (("command_picker", "x"), ("refresh", "r")):
        with pytest.raises(StateValidationError):
            store.save("global", {"keymap": {command_id: key}})
    store.save(
        "global",
        {"keymap": {"command_picker_shortcut": "shift+x", "refresh_shortcut": "shift+r"}},
    )
    assert store.load("global")["keymap"] == {
        "command_picker_shortcut": "shift+x",
        "refresh_shortcut": "shift+r",
    }
    with pytest.raises(StateValidationError):
        store.save("global", {"keymap": {"clear_filter": "f11"}})


def test_global_store_validates_catalog_keymap_and_diff_layout_defaults(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")

    assert store.load("global")["diff_layout"] == "unified"
    store.save("global", {"keymap": {"focus_history": "CTRL+F11"}})
    assert store.load("global")["keymap"] == {"focus_history": "ctrl+f11"}
    before = store.settings_path.read_bytes()
    with pytest.raises(StateValidationError):
        store.save("global", {"keymap": {"custom": "keep"}})
    assert store.settings_path.read_bytes() == before


def test_clear_global_restores_defaults_and_keeps_workspace_documents(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("global", {"theme": "dark"})
    store.save("worktree-a", {"preferences": {"graph_style": "compact"}})

    store.clear("global")

    assert store.load("global") == DEFAULT_SETTINGS
    assert store.load("worktree-a") == {"preferences": {"graph_style": "compact"}}
    assert not store.settings_path.exists()
