import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

from rhizome.persistence import StateCorruptionError, StateValidationError, WorkspaceStateStore


def test_diagnostics_are_local_redacted_metadata_behind_the_store(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")

    store.record_diagnostic(
        "refresh.finished",
        {
            "component": "remote-refresh",
            "status": "failed",
            "duration_ms": 125,
            "offline": False,
            "path": "/Users/example/secret/repository",
            "url": "https://example.test/private/repository",
            "message": "A commit message that must not be diagnostic content",
            "patch": "- secret source\n+ more secret source",
            "credential": "token-value",
        },
    )

    preview = store.diagnostics_preview()
    assert len(preview) == 1
    assert preview[0]["event"] == "refresh.finished"
    assert preview[0]["metadata"] == {
        "component": "remote-refresh",
        "duration_ms": 125,
        "offline": False,
        "status": "failed",
    }
    assert preview[0]["redacted_fields"] == 5
    serialized = json.dumps(preview)
    for secret in ("/Users/example", "https://", "commit message", "secret source", "token-value"):
        assert secret not in serialized


def test_diagnostic_export_is_explicit_private_and_previewable(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.record_diagnostic("workspace.opened", {"component": "workspace", "count": 2})
    export_directory = tmp_path / "exports"
    export_directory.mkdir(mode=0o755)
    destination = export_directory / "diagnostics.json"

    exported = store.export_diagnostics(destination)

    assert exported == destination
    assert stat.S_IMODE(export_directory.stat().st_mode) == 0o755
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["policy"] == {
        "retention_days": 7,
        "maximum_bytes": 10 * 1024 * 1024,
        "telemetry": False,
        "source_content": False,
    }
    assert document["diagnostics"] == store.diagnostics_preview()


def test_diagnostics_prune_entries_older_than_seven_days(tmp_path, monkeypatch):
    store = WorkspaceStateStore(tmp_path / "state")
    now = datetime(2026, 10, 28, 12, tzinfo=UTC)
    monkeypatch.setattr("rhizome.diagnostics._utc_now", lambda: now - timedelta(days=8))
    store.record_diagnostic("old.event", {"status": "old"})

    monkeypatch.setattr("rhizome.diagnostics._utc_now", lambda: now)
    store.record_diagnostic("new.event", {"status": "current"})

    assert [item["event"] for item in store.diagnostics_preview()] == ["new.event"]


def test_preview_physically_prunes_expired_diagnostics(tmp_path, monkeypatch):
    store = WorkspaceStateStore(tmp_path / "state")
    now = datetime(2026, 10, 28, 12, tzinfo=UTC)
    monkeypatch.setattr("rhizome.diagnostics._utc_now", lambda: now - timedelta(days=8))
    store.record_diagnostic("old.event", {"status": "old"})
    log = store.root / "diagnostics" / "events.jsonl"
    assert log.stat().st_size > 0

    monkeypatch.setattr("rhizome.diagnostics._utc_now", lambda: now)
    assert store.diagnostics_preview() == []
    assert log.read_bytes() == b""


def test_diagnostics_enforce_the_byte_budget_by_retaining_newest_entries(tmp_path, monkeypatch):
    store = WorkspaceStateStore(tmp_path / "state")
    monkeypatch.setattr("rhizome.diagnostics.DIAGNOSTIC_MAX_BYTES", 600)

    for number in range(20):
        store.record_diagnostic(
            f"event.{number}",
            {"status": "x" * 80, "count": number},
        )

    preview = store.diagnostics_preview()
    assert 0 < len(preview) < 20
    assert preview[-1]["event"] == "event.19"
    assert (store.root / "diagnostics" / "events.jsonl").stat().st_size <= 600


def test_invalid_diagnostic_event_or_metadata_is_rejected(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")

    with pytest.raises(StateValidationError, match="diagnostic event"):
        store.record_diagnostic("contains / private/path", {})
    with pytest.raises(StateValidationError, match="metadata"):
        store.record_diagnostic("valid.event", ["not", "an", "object"])  # type: ignore[arg-type]


def test_corrupt_diagnostics_are_not_overwritten_or_exported(tmp_path):
    store = WorkspaceStateStore(tmp_path / "state")
    store.record_diagnostic("workspace.opened", {"status": "ok"})
    log = store.root / "diagnostics" / "events.jsonl"
    log.write_text("not-json\n", encoding="utf-8")
    corrupt = log.read_bytes()

    with pytest.raises(StateCorruptionError) as preview_error:
        store.diagnostics_preview()
    with pytest.raises(StateCorruptionError):
        store.record_diagnostic("workspace.closed", {"status": "ok"})
    with pytest.raises(StateCorruptionError):
        store.export_diagnostics(tmp_path / "diagnostics.json")

    assert str(log) not in str(preview_error.value)
    assert log.read_bytes() == corrupt
