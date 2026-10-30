"""Private, durable persistence for Rhizome workspace state.

The store deliberately handles JSON documents only.  It does not interpret saved
workspace operations and therefore cannot replay a mutation when state is restored.
"""

from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import re
import secrets
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rhizome.settings import validate_preferences

SCHEMA_VERSION = 1
_INSTALL_OWNER_MARKER = b"rhizome-install-root-v1"

DEFAULT_SETTINGS: dict[str, Any] = {
    "remote_refresh_enabled": True,
    "remote_refresh_interval": 60,
    "offline": False,
    "theme": "auto",
    "graph_style": "smooth",
    "graph_order": "topo",
    "diff_layout": "unified",
    "pull_fetch_default": "pull-ff",
    "plain_glyphs": False,
    "diagnostics_enabled": True,
    "excluded_workspaces": [],
    "keymap": {},
    "protected_branches": [],
}

_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class PersistenceError(Exception):
    """Base class for safe persistence failures."""


class StateValidationError(PersistenceError, ValueError):
    """The caller or an editable settings file supplied invalid state."""


class StateCorruptionError(PersistenceError):
    """A persisted document is damaged or uses an unsupported schema."""


class StateSecurityError(PersistenceError):
    """A store path is unsafe to access."""


class StateIOError(PersistenceError, OSError):
    """A filesystem operation failed without exposing its path or contents."""


def _default_root() -> Path:
    override = os.environ.get("RHIZOME_STATE_HOME")
    if override:
        return Path(override).expanduser()
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home).expanduser() / "rhizome"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Rhizome"
    return Path.home() / ".local" / "state" / "rhizome"


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _reject_symlink_components(path: Path) -> None:
    """Reject every existing symlink in a path before app-owned access."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            raise StateIOError("Unable to inspect private state storage safely.") from None
        if stat.S_ISLNK(mode):
            raise StateSecurityError("Private state storage cannot use a symbolic link.")


def _reject_managed_install_ancestor(path: Path) -> None:
    """Refuse state below an installer-owned root before making any filesystem change."""
    try:
        physical = path.resolve(strict=False)
    except (OSError, RuntimeError):
        raise StateIOError("Unable to inspect private state storage safely.") from None

    try:
        if physical.exists() and not physical.is_dir():
            physical = physical.parent
    except OSError:
        raise StateIOError("Unable to inspect private state storage safely.") from None

    for ancestor in (physical, *physical.parents):
        marker = ancestor / ".rhizome-install-root"
        try:
            mode = marker.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            raise StateIOError("Unable to inspect private state storage safely.") from None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise StateSecurityError(
                "Private state storage cannot be located inside a managed Rhizome installation."
            )
        try:
            with marker.open("rb") as stream:
                value = stream.read(len(_INSTALL_OWNER_MARKER) + 2)
        except OSError:
            raise StateIOError("Unable to inspect private state storage safely.") from None
        if value not in {_INSTALL_OWNER_MARKER, _INSTALL_OWNER_MARKER + b"\n"}:
            raise StateSecurityError(
                "Private state storage is below an unverifiable installation marker."
            )
        raise StateSecurityError(
            "Private state storage cannot be located inside a managed Rhizome installation."
        )


def _ensure_private_directory(path: Path) -> None:
    _reject_managed_install_ancestor(path)
    _reject_symlink_components(path)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        mode = path.lstat().st_mode
        if not stat.S_ISDIR(mode):
            raise StateSecurityError("Private state storage must be a directory.")
        if stat.S_ISLNK(mode):
            raise StateSecurityError("Private state storage cannot use a symbolic link.")
        path.chmod(0o700)
    except (StateSecurityError, StateIOError):
        raise
    except OSError:
        raise StateIOError("Unable to prepare private state storage safely.") from None


def _validate_json_value(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise StateValidationError("State must contain valid JSON state values.")
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise StateValidationError("State must contain valid JSON state values.")
            _validate_json_value(item)
        return
    raise StateValidationError("State must contain valid JSON state values.")


def _validate_settings(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_preferences(data)
    except ValueError:
        raise StateValidationError("Settings must contain valid JSON state values.") from None


def _json_bytes(document: dict[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                document,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise StateValidationError("State must contain valid JSON state values.") from None


def _parse_json(raw: bytes) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError

    return json.loads(raw.decode("utf-8"), parse_constant=reject_constant)


class WorkspaceStateStore:
    """Persist isolated JSON state documents in a private user-owned directory."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = _absolute(Path(root) if root is not None else _default_root())
        _reject_managed_install_ancestor(self._root)
        _ensure_private_directory(self._root)

    @property
    def root(self) -> Path:
        """The private application state directory."""
        return self._root

    @property
    def settings_path(self) -> Path:
        """The editable global settings document."""
        return self._root / "settings.json"

    def load(self, workspace_key: str) -> dict[str, Any]:
        """Load one document, returning defaults for state not yet saved."""
        target = self._target_for(workspace_key)
        if not target.exists():
            return copy.deepcopy(DEFAULT_SETTINGS) if workspace_key == "global" else {}
        data = self._read_document(target, settings=workspace_key == "global")
        if workspace_key == "global":
            return {**copy.deepcopy(DEFAULT_SETTINGS), **data}
        return data

    @contextmanager
    def _locked(self, workspace_key: str):
        self._target_for(workspace_key)  # Validate before deriving the lock name.
        directory = self._root / "locks"
        _ensure_private_directory(directory)
        path = directory / f"{workspace_key}.lock"
        _reject_symlink_components(path)
        descriptor = None
        try:
            descriptor = os.open(
                path,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                0o600,
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise StateSecurityError("Private state lock must be a regular file.")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if descriptor is not None:
                os.close(descriptor)
            raise StateIOError("Private state is busy or unavailable.") from None
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise
        try:
            yield
        finally:
            os.close(descriptor)

    def save(
        self,
        workspace_key: str,
        data: dict[str, Any],
        *,
        expected: dict[str, Any] | None = None,
    ) -> None:
        """Atomically save; optional expected state refuses another instance's edits."""
        with self._locked(workspace_key):
            if expected is not None and self.load(workspace_key) != expected:
                raise StateValidationError(
                    "Saved state changed in another instance. Keep your text and reopen before saving."
                )
            self._save_document(workspace_key, data)

    def _save_document(self, workspace_key: str, data: dict[str, Any]) -> None:
        if type(data) is not dict:
            raise StateValidationError("Workspace state must be a dictionary of JSON values.")
        try:
            _validate_json_value(data)
        except RecursionError:
            raise StateValidationError("State must contain valid JSON state values.") from None
        if workspace_key == "global":
            data = _validate_settings(data)
        target = self._target_for(workspace_key)
        parent = target.parent
        _ensure_private_directory(parent)

        # Refuse to hide damage.  Clearing is the explicit discard operation.
        if target.exists() or target.is_symlink():
            self._read_document(target, settings=workspace_key == "global")

        document = {"schema_version": SCHEMA_VERSION, "data": copy.deepcopy(data)}
        self._atomic_write(target, _json_bytes(document))

    def clear(self, workspace_key: str) -> None:
        """Explicitly discard one state document without affecting other keys."""
        with self._locked(workspace_key):
            self._clear_document(workspace_key)

    def _clear_document(self, workspace_key: str) -> None:
        target = self._target_for(workspace_key)
        _reject_managed_install_ancestor(target)
        _reject_symlink_components(target)
        try:
            if not target.exists():
                return
            mode = target.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise StateSecurityError("Private state storage cannot use a symbolic link.")
            if not stat.S_ISREG(mode):
                raise StateSecurityError("Private state documents must be regular files.")
            target.unlink()
            self._sync_directory(target.parent)
        except (StateSecurityError, StateIOError):
            raise
        except OSError:
            raise StateIOError("Unable to clear workspace state safely.") from None

    def record_diagnostic(self, event: str, metadata: dict[str, Any] | None = None) -> None:
        """Record bounded, redacted operational metadata locally."""
        with self._locked("diagnostics"):
            self._diagnostic_journal().record(event, metadata)

    def diagnostics_preview(self) -> list[dict[str, Any]]:
        """Return the exact redacted records an explicit export would contain."""
        with self._locked("diagnostics"):
            return self._diagnostic_journal().preview()

    def diagnostics_export_preview(self, records: list[dict[str, Any]]) -> str:
        """Render every byte an export of these captured records will contain."""
        return self._diagnostic_journal().export_preview(records)

    def export_diagnostics(
        self,
        destination: Path,
        *,
        records: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Export the captured preview when supplied; never overwrite an existing file."""
        if not isinstance(destination, Path):
            raise StateValidationError("A diagnostic export path is required.")
        with self._locked("diagnostics"):
            return self._diagnostic_journal().export(destination, records=records)

    def clear_diagnostics(self) -> None:
        """Explicitly purge local diagnostics, independently of drafts and preferences."""
        with self._locked("diagnostics"):
            self._diagnostic_journal().clear()

    def _diagnostic_journal(self):
        from rhizome.diagnostics import _DiagnosticJournal

        return _DiagnosticJournal(self._root, self._atomic_write)

    def _target_for(self, workspace_key: str) -> Path:
        if type(workspace_key) is not str or (
            workspace_key != "global"
            and (not _KEY_PATTERN.fullmatch(workspace_key) or workspace_key.casefold() == "global")
        ):
            raise StateValidationError("A safe workspace key is required.")
        if workspace_key == "global":
            target = self.settings_path
        else:
            target = self._root / "workspaces" / f"{workspace_key}.json"
        _reject_symlink_components(target)
        return target

    def _read_document(self, target: Path, *, settings: bool) -> dict[str, Any]:
        _reject_managed_install_ancestor(target)
        _reject_symlink_components(target)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            if not stat.S_ISREG(target.lstat().st_mode):
                raise StateSecurityError("Private state documents must be regular files.")
            descriptor = os.open(target, flags)
            try:
                mode = os.fstat(descriptor).st_mode
                if not stat.S_ISREG(mode):
                    raise StateSecurityError("Private state documents must be regular files.")
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    raw = stream.read()
            finally:
                os.close(descriptor)
        except StateSecurityError:
            raise
        except OSError:
            raise StateIOError("Unable to read workspace state safely.") from None

        try:
            document = _parse_json(raw)
            if (
                type(document) is not dict
                or document.keys() != {"schema_version", "data"}
                or document["schema_version"] != SCHEMA_VERSION
                or type(document["schema_version"]) is not int
                or type(document["data"]) is not dict
            ):
                raise ValueError
            data = document["data"]
            _validate_json_value(data)
        except StateValidationError:
            raise StateCorruptionError("Saved workspace state is corrupt or unsupported.") from None
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise StateCorruptionError("Saved workspace state is corrupt or unsupported.") from None
        if settings:
            data = _validate_settings(data)
        return copy.deepcopy(data)

    def _atomic_write(self, target: Path, content: bytes) -> None:
        _ensure_private_directory(target.parent)
        temporary = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
        descriptor: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, target)
            self._sync_directory(target.parent)
        except OSError:
            raise StateIOError("Unable to save workspace state safely.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        try:
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            raise StateIOError("Unable to make workspace state durable safely.") from None
