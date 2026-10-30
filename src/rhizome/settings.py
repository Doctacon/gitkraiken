"""Curated preferences and remappable command metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from textual.keys import (
    KEY_NAME_REPLACEMENTS,
    KEY_TO_UNICODE_NAME,
    Keys,
    _character_to_key,
    key_to_character,
)


@dataclass(frozen=True)
class CommandSpec:
    id: str
    keys: str
    action: str
    description: str
    priority: bool = False
    show: bool = True


COMMANDS = (
    CommandSpec("quit", "ctrl+q", "quit", "Quit"),
    CommandSpec("command_picker", "ctrl+p", "command_picker", "Actions", priority=True),
    CommandSpec("open_repository", "ctrl+o", "open_repository", "Open", priority=True),
    CommandSpec("refresh", "ctrl+r", "refresh", "Refresh", priority=True),
    CommandSpec("activity", "ctrl+l", "activity", "Activity", priority=True),
    CommandSpec("new_branch", "ctrl+b", "new_branch", "Branch", priority=True),
    CommandSpec("focus_filter", "f,slash", "focus_filter", "Search"),
    CommandSpec("refresh_shortcut", "r", "refresh", "Refresh letter shortcut"),
    CommandSpec("command_picker_shortcut", "x", "command_picker", "Actions letter shortcut"),
    CommandSpec("toggle_graph_style", "g", "toggle_graph_style", "Graph style"),
    CommandSpec("help", "question_mark", "help", "Help"),
    CommandSpec("next_row", "j", "next_row", "Next", show=False),
    CommandSpec("previous_row", "k", "previous_row", "Previous", show=False),
    CommandSpec("toggle_refs", "ctrl+left", "toggle_refs", "Refs", show=False),
    CommandSpec("toggle_inspector", "ctrl+right", "toggle_inspector", "Inspector", show=False),
    CommandSpec("show_files", "f2", "show_files", "Files", show=False),
    CommandSpec("show_composer", "f3", "show_composer", "Commit", show=False),
    CommandSpec("workspace_settings", "f4", "workspace_settings", "Workspace", show=False),
    CommandSpec("clear_filter", "escape", "clear_filter", "Clear selection", show=False),
    CommandSpec("focus_history", "f5", "focus_history", "Focus history"),
    CommandSpec("exit_focus", "shift+f5", "exit_focus", "Exit focus"),
    CommandSpec("toggle_history_order", "f6", "toggle_history_order", "History order"),
    CommandSpec("compare_history", "f7", "compare_history", "Compare history"),
    CommandSpec("file_history", "f8", "file_investigation('history')", "File history"),
    CommandSpec("blame", "f9", "file_investigation('blame')", "Blame"),
    CommandSpec("history_newer", "ctrl+pageup", "history_newer", "Newer history"),
    CommandSpec("history_older", "ctrl+pagedown", "history_older", "Older history"),
    CommandSpec("search_criteria", "shift+f", "search_criteria", "Search criteria"),
    CommandSpec("stop_search", "f10", "stop_search", "Stop search"),
    CommandSpec("widen_search", "ctrl+f5", "widen_search", "Widen search"),
)

CURATED_SETTINGS = (
    "offline",
    "remote_refresh_interval",
    "remote_refresh_enabled",
    "plain_glyphs",
    "diagnostics_enabled",
    "theme",
    "graph_style",
    "graph_order",
    "keymap",
    "diff_layout",
    "pull_fetch_default",
)

PULL_FETCH_OPTIONS = (
    ("Fetch All", "fetch-all"),
    ("Pull (fast-forward if possible)", "pull-ff"),
    ("Pull (fast-forward only)", "pull-ff-only"),
    ("Pull (rebase)", "pull-rebase"),
)

_COMMAND_IDS = frozenset(command.id for command in COMMANDS)
_MODIFIERS = {"ctrl", "shift", "alt", "meta", "super"}
_NAMED_KEYS = (
    {key.value for key in Keys}
    | set(KEY_TO_UNICODE_NAME)
    | set(KEY_NAME_REPLACEMENTS)
    | set(KEY_NAME_REPLACEMENTS.values())
)
_ALIAS_KEYS = {
    "ctrl+i": "tab",
    "ctrl+m": "enter",
    "return": "enter",
    "ctrl+left_square_brace": "escape",
    "ctrl+space": "ctrl+at",
}
_RESERVED_KEYS = {
    "escape",
    "tab",
    "enter",
    "ctrl+a",
    "ctrl+c",
    "ctrl+v",
    "ctrl+x",
    "ctrl+y",
    "ctrl+z",
    "ctrl+s",
    "ctrl+w",
}
_RESERVED_BASES = {
    "escape",
    "tab",
    "enter",
    "left",
    "right",
    "up",
    "down",
    "home",
    "end",
    "pageup",
    "pagedown",
    "insert",
    "delete",
    "backspace",
    "space",
}


def _key_parts(keys: str) -> tuple[str, ...]:
    if not isinstance(keys, str) or not keys or keys != keys.strip():
        raise ValueError("A key binding must be a non-empty Textual key string.")
    normalized = []
    for chord in keys.split(","):
        chord = chord.strip().lower()
        if not chord:
            raise ValueError("A key binding contains an empty key.")
        parts = chord.split("+")
        base = parts[-1]
        modifiers = parts[:-1]
        if len(base) == 1 and not base.isalnum():
            base = _character_to_key(base)
        base = KEY_NAME_REPLACEMENTS.get(base, base)
        if (
            any(modifier not in _MODIFIERS for modifier in modifiers)
            or len(modifiers) != len(set(modifiers))
            or not base
        ):
            raise ValueError(f"Invalid Textual key binding: {chord}")
        if not (
            (len(base) == 1 and base.isprintable() and not base.isspace()) or base in _NAMED_KEYS
        ):
            raise ValueError(f"Unknown Textual key name: {base}")
        ordered = [
            modifier
            for modifier in ("ctrl", "shift", "alt", "meta", "super")
            if modifier in modifiers
        ]
        canonical = "+".join((*ordered, base))
        normalized.append(_ALIAS_KEYS.get(canonical, canonical))
    if len(normalized) != len(set(normalized)):
        raise ValueError("A command cannot bind the same effective key twice.")
    return tuple(normalized)


def _defaults_for(command_id: str) -> set[str]:
    return {
        key for command in COMMANDS if command.id == command_id for key in _key_parts(command.keys)
    }


def validate_keymap(mapping: Mapping[str, str]) -> dict[str, str]:
    """Validate catalog-ID overrides and return normalized, user-owned mappings."""
    if type(mapping) is not dict:
        raise ValueError("Keymap must be a dictionary of command IDs to key strings.")
    if any(
        type(command_id) is not str or type(keys) is not str for command_id, keys in mapping.items()
    ):
        raise ValueError("Keymap must contain string command IDs and key strings.")
    unknown = set(mapping) - _COMMAND_IDS
    if unknown:
        raise ValueError("Keymap contains an unknown command ID.")

    normalized = {command_id: ",".join(_key_parts(keys)) for command_id, keys in mapping.items()}
    priorities = {command.id for command in COMMANDS if command.priority}
    for command_id, keys in normalized.items():
        defaults = _defaults_for(command_id)
        parsed_keys = _key_parts(keys)
        if command_id == "clear_filter" and set(parsed_keys) != defaults:
            raise ValueError(
                "Clear selection must remain bound to Escape for safe Back navigation."
            )
        for key in parsed_keys:
            *modifiers, base = key.split("+")
            if command_id in priorities and set(modifiers) <= {"shift"} and key_to_character(base):
                raise ValueError("Priority commands cannot capture ordinary printable text.")
            if key in defaults:
                continue
            if (
                key in _RESERVED_KEYS
                or base in _RESERVED_BASES
                or (modifiers == ["ctrl"] and len(base) == 1 and base.isalpha())
            ):
                raise ValueError("Keymap cannot capture reserved editor or navigation keys.")

    effective: dict[str, str] = {}
    for command_id in _COMMAND_IDS:
        keys = (
            _key_parts(normalized[command_id])
            if command_id in normalized
            else _defaults_for(command_id)
        )
        for key in keys:
            owner = effective.get(key)
            if owner is not None and owner != command_id:
                raise ValueError("Keymap assigns the same effective key to multiple commands.")
            effective[key] = command_id
    return normalized


def validate_preferences(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Validate curated global preferences while retaining compatibility fields."""
    if type(mapping) is not dict:
        raise ValueError("Preferences must be a dictionary.")
    allowed = set(CURATED_SETTINGS) | {"excluded_workspaces", "protected_branches"}
    if set(mapping) - allowed:
        raise ValueError("Preferences contain an unknown setting.")
    result = dict(mapping)
    for name in ("offline", "remote_refresh_enabled", "plain_glyphs", "diagnostics_enabled"):
        if name in result and type(result[name]) is not bool:
            raise ValueError(f"{name} must be a boolean.")
    if "remote_refresh_interval" in result and (
        type(result["remote_refresh_interval"]) is not int or result["remote_refresh_interval"] < 1
    ):
        raise ValueError("remote_refresh_interval must be a positive integer.")
    choices = {
        "theme": {"auto", "light", "dark", "high-contrast"},
        "graph_style": {"smooth", "compact"},
        "graph_order": {"topo", "chronological"},
        "diff_layout": {"unified", "split"},
        "pull_fetch_default": {value for _label, value in PULL_FETCH_OPTIONS},
    }
    for name, values in choices.items():
        if name in result and (type(result[name]) is not str or result[name] not in values):
            raise ValueError(f"Invalid {name} preference.")
    if "keymap" in result:
        result["keymap"] = validate_keymap(result["keymap"])
    if "excluded_workspaces" in result:
        excluded = result["excluded_workspaces"]
        if type(excluded) is not list or any(
            type(key) is not str
            or len(key) != 64
            or any(char not in "0123456789abcdef" for char in key)
            for key in excluded
        ):
            raise ValueError("Excluded workspaces must be opaque workspace identities.")
    if "protected_branches" in result:
        branches = result["protected_branches"]
        if type(branches) is not list or any(type(branch) is not str for branch in branches):
            raise ValueError("Protected branches must be strings.")
    return result
