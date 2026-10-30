"""The split-button preference is validated and stored without executing Git."""

from dataclasses import asdict

import pytest

from rhizome.models import Action
from rhizome.persistence import StateValidationError, WorkspaceStateStore
from rhizome.settings import CURATED_SETTINGS, validate_preferences


def test_default_pull_preference_matches_the_fast_forward_if_possible_choice(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    assert store.load("global")["pull_fetch_default"] == "pull-ff"
    assert "pull_fetch_default" in CURATED_SETTINGS
    assert not store.settings_path.exists()


@pytest.mark.parametrize("mode", ["fetch-all", "pull-ff", "pull-ff-only", "pull-rebase"])
def test_each_pull_preference_survives_reopen(tmp_path, mode):
    root = tmp_path / "state"
    store = WorkspaceStateStore(root)
    assert validate_preferences({"pull_fetch_default": mode}) == {"pull_fetch_default": mode}
    store.save("global", {"theme": "dark", "pull_fetch_default": mode})
    reopened = WorkspaceStateStore(root).load("global")
    assert reopened["pull_fetch_default"] == mode
    assert reopened["theme"] == "dark"


@pytest.mark.parametrize("value", ["pull", "merge", "rebase", "", None, [], {}, 0, True])
def test_invalid_pull_preference_cannot_overwrite_settings(tmp_path, value):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("global", {"pull_fetch_default": "fetch-all"})
    before = store.settings_path.read_bytes()
    with pytest.raises(StateValidationError):
        store.save("global", {"pull_fetch_default": value})
    assert store.settings_path.read_bytes() == before


def test_existing_settings_file_gains_default_without_overwriting_other_preferences(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.save("global", {"theme": "dark", "offline": True})
    settings = WorkspaceStateStore(tmp_path / "state").load("global")
    assert settings["pull_fetch_default"] == "pull-ff"
    assert settings["offline"] is True
    assert settings["theme"] == "dark"


def test_action_identity_includes_explicit_pull_policy():
    ff = Action("pull", pull_mode="ff")
    ff_only = Action("pull", pull_mode="ff-only")
    assert ff != ff_only
    assert asdict(ff)["pull_mode"] == "ff"
    assert Action("pull").pull_mode == ""
