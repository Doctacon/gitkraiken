"""Bounded, source-free local diagnostics used through ``WorkspaceStateStore``."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import secrets
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from rhizome.persistence import (
    SCHEMA_VERSION,
    StateCorruptionError,
    StateIOError,
    StateSecurityError,
    StateValidationError,
    _absolute,
    _reject_symlink_components,
)

DIAGNOSTIC_RETENTION_DAYS = 7
DIAGNOSTIC_MAX_BYTES = 10 * 1024 * 1024

_EVENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SAFE_METADATA_FIELDS = {
    "attempt",
    "background",
    "component",
    "count",
    "duration_ms",
    "elapsed_ms",
    "error_type",
    "exit_code",
    "git_version",
    "offline",
    "operation",
    "platform",
    "status",
    "version",
}
_SAFE_STRING_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}\Z")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime:
    if type(value) is not str:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed.astimezone(UTC)


def _safe_scalar(value: Any) -> bool:
    if type(value) in (bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is str:
        return bool(_SAFE_STRING_PATTERN.fullmatch(value))
    return False


def _sanitize_metadata(metadata: dict[str, Any]) -> tuple[dict[str, Any], int]:
    safe: dict[str, Any] = {}
    redacted = 0
    for key, value in metadata.items():
        if type(key) is str and key in _SAFE_METADATA_FIELDS and _safe_scalar(value):
            safe[key] = value
        else:
            redacted += 1
    return safe, redacted


def _line_bytes(entry: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            entry, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        + "\n"
    ).encode("utf-8")


class _DiagnosticJournal:
    """Private helper; callers use the store's diagnostic methods."""

    def __init__(self, root: Path, atomic_write: Callable[[Path, bytes], None]) -> None:
        self._path = root / "diagnostics" / "events.jsonl"
        self._atomic_write = atomic_write

    def record(self, event: str, metadata: dict[str, Any] | None) -> None:
        if type(event) is not str or not _EVENT_PATTERN.fullmatch(event):
            raise StateValidationError("A safe diagnostic event name is required.")
        if metadata is None:
            metadata = {}
        if type(metadata) is not dict:
            raise StateValidationError("Diagnostic metadata must be a JSON object.")
        safe, redacted = _sanitize_metadata(metadata)
        now = _utc_now()
        entries = self._retained(self._read(), now)
        entries.append(
            {
                "schema_version": SCHEMA_VERSION,
                "occurred_at": _format_time(now),
                "event": event,
                "metadata": safe,
                "redacted_fields": redacted,
            }
        )
        content = self._within_budget(entries)
        self._atomic_write(self._path, b"".join(_line_bytes(entry) for entry in content))

    def preview(self) -> list[dict[str, Any]]:
        entries = self._read()
        retained = self._within_budget(self._retained(entries, _utc_now()))
        if retained != entries:
            self._atomic_write(self._path, b"".join(_line_bytes(entry) for entry in retained))
        return copy.deepcopy(retained)

    def clear(self) -> None:
        # Explicit deletion also permits discarding a corrupt journal, but never
        # follows a link or removes anything other than this owned regular file.
        _reject_symlink_components(self._path)
        try:
            if not self._path.exists():
                return
            if not stat.S_ISREG(self._path.lstat().st_mode):
                raise StateSecurityError("Private diagnostics must be a regular file.")
            self._path.unlink()
            directory = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            raise StateIOError("Unable to clear local diagnostics safely.") from None

    @staticmethod
    def export_preview(records: list[dict[str, Any]]) -> str:
        """Render the complete export document, shared by preview and publication."""
        diagnostics = copy.deepcopy(records)
        try:
            if type(diagnostics) is not list:
                raise ValueError
            for entry in diagnostics:
                _DiagnosticJournal._validate_entry(entry)
            if sum(len(_line_bytes(entry)) for entry in diagnostics) > DIAGNOSTIC_MAX_BYTES:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise StateValidationError("The diagnostic preview is invalid or too large.") from None
        document = {
            "schema_version": SCHEMA_VERSION,
            "policy": {
                "retention_days": DIAGNOSTIC_RETENTION_DAYS,
                "maximum_bytes": DIAGNOSTIC_MAX_BYTES,
                "telemetry": False,
                "source_content": False,
            },
            "diagnostics": diagnostics,
        }
        return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def export(
        self,
        destination: Path,
        *,
        records: list[dict[str, Any]] | None = None,
    ) -> Path:
        content = self.export_preview(self.preview() if records is None else records).encode(
            "utf-8"
        )
        target = _absolute(destination)
        self._atomic_export(target, content)
        return target

    def _read(self) -> list[dict[str, Any]]:
        _reject_symlink_components(self._path)
        if not self._path.exists():
            return []
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            if not stat.S_ISREG(self._path.lstat().st_mode):
                raise StateSecurityError("Private diagnostics must be a regular file.")
            descriptor = os.open(self._path, flags)
            try:
                mode = os.fstat(descriptor).st_mode
                if not stat.S_ISREG(mode):
                    raise StateSecurityError("Private diagnostics must be a regular file.")
                if os.fstat(descriptor).st_size > DIAGNOSTIC_MAX_BYTES:
                    raise StateCorruptionError("Saved diagnostics exceed their private size limit.")
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    raw_lines = stream.readlines()
            finally:
                os.close(descriptor)
        except (StateCorruptionError, StateSecurityError):
            raise
        except OSError:
            raise StateIOError("Unable to read local diagnostics safely.") from None

        entries: list[dict[str, Any]] = []
        try:
            for raw in raw_lines:
                entry = json.loads(
                    raw.decode("utf-8"),
                    parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
                )
                self._validate_entry(entry)
                entries.append(entry)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, RecursionError):
            raise StateCorruptionError("Saved diagnostics are corrupt or unsupported.") from None
        return entries

    @staticmethod
    def _validate_entry(entry: Any) -> None:
        if (
            type(entry) is not dict
            or entry.keys()
            != {"schema_version", "occurred_at", "event", "metadata", "redacted_fields"}
            or type(entry["schema_version"]) is not int
            or entry["schema_version"] != SCHEMA_VERSION
            or type(entry["event"]) is not str
            or not _EVENT_PATTERN.fullmatch(entry["event"])
            or type(entry["metadata"]) is not dict
            or type(entry["redacted_fields"]) is not int
            or entry["redacted_fields"] < 0
        ):
            raise ValueError
        _parse_time(entry["occurred_at"])
        safe, redacted = _sanitize_metadata(entry["metadata"])
        if safe != entry["metadata"] or redacted:
            raise ValueError

    @staticmethod
    def _retained(entries: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
        cutoff = now - timedelta(days=DIAGNOSTIC_RETENTION_DAYS)
        return [entry for entry in entries if _parse_time(entry["occurred_at"]) >= cutoff]

    @staticmethod
    def _within_budget(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        size = 0
        for entry in reversed(entries):
            entry_size = len(_line_bytes(entry))
            if entry_size > DIAGNOSTIC_MAX_BYTES:
                continue
            if size + entry_size > DIAGNOSTIC_MAX_BYTES:
                break
            selected.append(entry)
            size += entry_size
        selected.reverse()
        return selected

    @staticmethod
    def _atomic_export(target: Path, content: bytes) -> None:
        _reject_symlink_components(target)
        parent_existed = target.parent.exists()
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = target.parent.lstat().st_mode
            if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                raise StateSecurityError("A safe diagnostic export directory is required.")
            if not parent_existed:
                target.parent.chmod(0o700)
        except StateSecurityError:
            raise
        except OSError:
            raise StateIOError("Unable to prepare a diagnostic export safely.") from None
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
            # Atomic no-clobber publication: a user-selected destination may
            # exist or appear after the preview. Never replace that content.
            os.link(temporary, target)
            temporary.unlink()
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            raise StateIOError("Unable to export local diagnostics safely.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
