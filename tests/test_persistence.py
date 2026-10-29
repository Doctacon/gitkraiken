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


def test_clear_global_restores_defaults_and_keeps_workspace_documents(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("global", {"theme": "dark"})
    store.save("worktree-a", {"preferences": {"graph_style": "compact"}})

    store.clear("global")

    assert store.load("global") == DEFAULT_SETTINGS
    assert store.load("worktree-a") == {"preferences": {"graph_style": "compact"}}
    assert not store.settings_path.exists()
