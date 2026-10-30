from __future__ import annotations

import dataclasses
import json
import os
import stat
from pathlib import Path

import pytest

from rhizome import recovery
from rhizome.git import Repository
from rhizome.recovery import RecoveryError, RecoveryJournal


def _commit_file(git_repo: Path, git_run, path: str, content: bytes, message: str) -> str:
    target = git_repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    git_run(git_repo, "add", "--", path)
    git_run(git_repo, "commit", "-m", message)
    return git_run(git_repo, "rev-parse", "HEAD").stdout.strip()


def _retained_file_bytes(path: Path) -> int:
    return sum(
        item.lstat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink()
    )


def test_prepare_and_recover_aside_preserve_distinct_index_and_worktree_preimages(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    _commit_file(git_repo, git_run, "script.sh", b"base\n", "base")
    script = git_repo / "script.sh"
    script.write_bytes(b"staged version\n")
    script.chmod(0o755)
    git_run(git_repo, "add", "--", "script.sh")
    script.write_bytes(b"unstaged version\n")
    (git_repo / "untracked.txt").write_text("private untracked\n")

    journal = RecoveryJournal(tmp_path / "private")
    checkpoint = journal.prepare(Repository(git_repo), operation="reset-hard", paths=("script.sh",))

    assert checkpoint.active is True
    assert checkpoint.repository == str(git_repo)
    assert checkpoint.size_bytes == _retained_file_bytes(
        tmp_path / "private" / "checkpoints" / checkpoint.id
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        checkpoint.active = False  # type: ignore[misc]

    script.write_bytes(b"newer work must remain\n")
    result = journal.recover_aside(Repository(git_repo), checkpoint.id)

    assert result.branch == ""
    assert result.directory is not None
    assert (result.directory / "index" / "script.sh").read_bytes() == b"staged version\n"
    assert (result.directory / "worktree" / "script.sh").read_bytes() == b"unstaged version\n"
    assert stat.S_IMODE((result.directory / "index" / "script.sh").stat().st_mode) == 0o700
    assert script.read_bytes() == b"newer work must remain\n"
    assert (git_repo / "untracked.txt").read_text() == "private untracked\n"
    assert not any(path.name == "untracked.txt" for path in result.directory.rglob("*"))
    assert "Current checkout was not changed" in result.summary


def test_index_preimage_uses_raw_blob_oid_despite_replace_ref(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    _commit_file(git_repo, git_run, "tracked.txt", b"base\n", "base")
    tracked = git_repo / "tracked.txt"
    tracked.write_bytes(b"true staged preimage\n")
    git_run(git_repo, "add", "tracked.txt")
    index_oid = git_run(git_repo, "ls-files", "-s", "tracked.txt").stdout.split()[1]
    replacement_source = git_repo / "replacement-source"
    replacement_source.write_bytes(b"replacement content must not be captured\n")
    replacement_oid = git_run(git_repo, "hash-object", "-w", "replacement-source").stdout.strip()
    replacement_source.unlink()
    git_run(git_repo, "replace", index_oid, replacement_oid)

    journal = RecoveryJournal(tmp_path / "private")
    checkpoint = journal.prepare(Repository(git_repo), operation="protect", paths=("tracked.txt",))
    recovered = journal.recover_aside(Repository(git_repo), checkpoint.id)

    assert recovered.directory is not None
    assert (recovered.directory / "index" / "tracked.txt").read_bytes() == (
        b"true staged preimage\n"
    )


def test_history_bundle_survives_source_ref_deletion_and_recovers_new_branch(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    main_oid = _commit_file(git_repo, git_run, "story.txt", b"main\n", "main")
    git_run(git_repo, "checkout", "-b", "ephemeral")
    lost_oid = _commit_file(git_repo, git_run, "story.txt", b"lost history\n", "lost")

    journal = RecoveryJournal(tmp_path / "private")
    checkpoint = journal.prepare(Repository(git_repo), operation="rewrite", history=True)

    git_run(git_repo, "checkout", "main")
    (git_repo / "newer.txt").write_text("newer untracked\n")
    git_run(git_repo, "branch", "-D", "ephemeral")
    git_run(git_repo, "reflog", "expire", "--expire=now", "--all")
    git_run(git_repo, "gc", "--prune=now")
    assert git_run(git_repo, "cat-file", "-e", lost_oid, check=False).returncode != 0

    before_head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    result = journal.recover_aside(Repository(git_repo), id=checkpoint.id)

    assert before_head == main_oid
    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == main_oid
    assert git_run(git_repo, "rev-parse", result.branch).stdout.strip() == lost_oid
    assert git_run(git_repo, "show", f"{result.branch}:story.txt").stdout == "lost history\n"
    assert (git_repo / "story.txt").read_bytes() == b"main\n"
    assert (git_repo / "newer.txt").read_text() == "newer untracked\n"
    assert result.directory is None


def test_absent_preimages_and_unsafe_paths_fail_closed(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    _commit_file(git_repo, git_run, "folder/tracked.txt", b"tracked\n", "tracked")
    git_run(git_repo, "rm", "--", "folder/tracked.txt")
    journal = RecoveryJournal(tmp_path / "private")

    deleted = journal.prepare(
        Repository(git_repo), operation="delete", paths=("folder/tracked.txt",)
    )
    recovered = journal.recover_aside(Repository(git_repo), deleted.id)
    assert recovered.directory is not None
    assert not (recovered.directory / "index" / "folder" / "tracked.txt").exists()
    assert not (recovered.directory / "worktree" / "folder" / "tracked.txt").exists()
    manifest = json.loads((recovered.directory / "preimages.json").read_bytes())
    assert manifest["paths"] == [
        {
            "path": "folder/tracked.txt",
            "index": {"present": False},
            "worktree": {"present": False},
        }
    ]

    with pytest.raises(RecoveryError, match="canonical repository-relative"):
        journal.prepare(Repository(git_repo), operation="unsafe", paths=("../outside",))

    git_run(git_repo, "reset", "--hard", "HEAD")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tracked.txt").write_text("must not be copied\n")
    os.rename(git_repo / "folder", git_repo / "real-folder")
    (git_repo / "folder").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RecoveryError, match="symlink ancestor"):
        journal.prepare(Repository(git_repo), operation="unsafe", paths=("folder/tracked.txt",))
    assert len(journal.list()) == 1


def test_renamed_path_captures_both_absent_old_and_present_new_identities(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    _commit_file(git_repo, git_run, "old.txt", b"committed\n", "base")
    git_run(git_repo, "mv", "old.txt", "new.txt")
    (git_repo / "new.txt").write_bytes(b"unstaged after rename\n")

    journal = RecoveryJournal(tmp_path / "private")
    checkpoint = journal.prepare(
        Repository(git_repo), operation="rewrite", paths=("old.txt", "new.txt")
    )
    result = journal.recover_aside(Repository(git_repo), checkpoint.id)

    assert result.directory is not None
    assert not (result.directory / "index" / "old.txt").exists()
    assert not (result.directory / "worktree" / "old.txt").exists()
    assert (result.directory / "index" / "new.txt").read_bytes() == b"committed\n"
    assert (result.directory / "worktree" / "new.txt").read_bytes() == b"unstaged after rename\n"


def test_budget_pins_unknown_outcomes_and_expiry_are_fail_closed(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "private"
    journal = RecoveryJournal(root)
    first = journal.prepare(Repository(git_repo), operation="protected")
    journal.complete(first.id)
    journal.pin(first.id)
    pinned = journal.list()[0]

    limited = RecoveryJournal(root, max_bytes=pinned.size_bytes + 64, max_age_days=1)
    with pytest.raises(RecoveryError, match="active or pinned"):
        limited.prepare(Repository(git_repo), operation="protected")
    assert [item.id for item in limited.list()] == [first.id]

    limited.pin(first.id, False)
    replacement = limited.prepare(Repository(git_repo), operation="protected")
    assert [item.id for item in limited.list()] == [replacement.id]

    limited.complete(replacement.id, outcome="uncertain")
    assert limited.list()[0].active is True
    limited.complete(replacement.id, outcome="failed")
    completed = limited.list()[0]
    assert completed.active is False

    future = (
        int(completed.created_at.timestamp() * 1_000_000_000) + 2 * 24 * 60 * 60 * 1_000_000_000
    )
    monkeypatch.setattr(recovery.time, "time_ns", lambda: future)
    assert limited.list() == ()


def test_corrupt_content_is_never_recovered_or_silently_evicted(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    _commit_file(git_repo, git_run, "tracked.txt", b"original\n", "tracked")
    journal = RecoveryJournal(tmp_path / "private")
    checkpoint = journal.prepare(Repository(git_repo), operation="protect", paths=("tracked.txt",))
    payload = (
        tmp_path / "private" / "checkpoints" / checkpoint.id / "files" / "worktree" / "tracked.txt"
    )
    payload.write_bytes(b"tampered\n")

    with pytest.raises(RecoveryError, match="corrupt"):
        journal.list()
    with pytest.raises(RecoveryError, match="corrupt"):
        journal.recover_aside(Repository(git_repo), checkpoint.id)
    with pytest.raises(RecoveryError, match="corrupt"):
        journal.prepare(Repository(git_repo), operation="another")
    assert payload.read_bytes() == b"tampered\n"


def test_atomic_publication_failure_leaves_no_checkpoint_or_repository_mutation(
    git_repo: Path, git_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit_file(git_repo, git_run, "tracked.txt", b"original\n", "tracked")
    (git_repo / "tracked.txt").write_bytes(b"dirty\n")
    before_status = git_run(git_repo, "status", "--porcelain=v1").stdout
    journal = RecoveryJournal(tmp_path / "private")

    def refuse_replace(source, destination):
        raise PermissionError("injected publication failure")

    monkeypatch.setattr(recovery.os, "replace", refuse_replace)
    with pytest.raises(RecoveryError, match="required recovery checkpoint"):
        journal.prepare(Repository(git_repo), operation="protect", paths=("tracked.txt",))

    assert list((tmp_path / "private" / "checkpoints").iterdir()) == []
    assert git_run(git_repo, "status", "--porcelain=v1").stdout == before_status
    assert (git_repo / "tracked.txt").read_bytes() == b"dirty\n"


def test_history_capture_refuses_before_bundling_when_preflight_cannot_fit(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    _commit_file(git_repo, git_run, "large.txt", b"x" * 4096, "content")
    journal = RecoveryJournal(tmp_path / "private", max_bytes=65_536)

    with pytest.raises(RecoveryError, match="before bundling"):
        journal.prepare(Repository(git_repo), operation="rewrite", history=True)
    assert journal.list() == ()


def test_pull_session_metadata_is_private_single_use_and_active_when_uncertain(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    head = _commit_file(git_repo, git_run, "tracked.txt", b"base\n", "base")
    root = tmp_path / "private"
    journal = RecoveryJournal(root)
    checkpoint = journal.prepare(Repository(git_repo), operation="pull-merge", history=True)

    session = journal.start_pull_session(
        checkpoint.id,
        repository=str(git_repo),
        kind="merge",
        branch="main",
        original_head=head,
        target_oid=head,
        outgoing=1,
        lease_token="a" * 64,
        other_refs="b" * 64,
    )
    assert session.phase == "prepared"
    with pytest.raises(RecoveryError, match="single-use"):
        journal.start_pull_session(
            checkpoint.id,
            repository=str(git_repo),
            kind="merge",
            branch="main",
            original_head=head,
            target_oid=head,
            outgoing=1,
            lease_token="a" * 64,
            other_refs="b" * 64,
        )

    restarted = RecoveryJournal(root)
    assert restarted.pull_sessions(str(git_repo)) == (session,)
    metadata = root / "checkpoints" / checkpoint.id / "checkpoint.json"
    assert stat.S_IMODE(metadata.stat().st_mode) == 0o600

    running = restarted.update_pull_session(checkpoint.id, phase="running", pid=4321)
    assert running.pid == 4321
    uncertain = restarted.update_pull_session(checkpoint.id, phase="uncertain")
    restarted.complete(checkpoint.id, outcome="uncertain")
    assert uncertain.phase == "uncertain"
    assert (uncertain.pid, uncertain.pgid) == (4321, 4321)
    retained = restarted.list(str(git_repo))[0]
    assert retained.active is True
    assert restarted.pull_sessions(str(git_repo))[0].phase == "uncertain"


def test_restart_lists_without_replaying_and_explicit_purge_removes_content(
    git_repo: Path, git_run, tmp_path: Path
) -> None:
    _commit_file(git_repo, git_run, "tracked.txt", b"base\n", "base")
    (git_repo / "tracked.txt").write_bytes(b"dirty\n")
    root = tmp_path / "private"
    checkpoint = RecoveryJournal(root).prepare(
        Repository(git_repo), operation="protected", paths=("tracked.txt",)
    )
    before = git_run(git_repo, "status", "--porcelain=v1").stdout

    restarted = RecoveryJournal(root)
    assert restarted.list(str(git_repo)) == (checkpoint,)
    assert restarted.list(str(tmp_path / "other")) == ()
    assert git_run(git_repo, "status", "--porcelain=v1").stdout == before
    assert (git_repo / "tracked.txt").read_bytes() == b"dirty\n"

    restarted.pin(id=checkpoint.id)
    restarted.purge(id=checkpoint.id)
    assert restarted.list() == ()
