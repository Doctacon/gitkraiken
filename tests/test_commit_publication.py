"""Public Repository.execute commit-publication fault proofs."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from rhizome.git import GitOutcomeUncertain, Repository
from rhizome.models import Action


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def _index_entries(repository: Path, index: Path) -> bytes:
    return subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=repository,
        env={**os.environ, "GIT_INDEX_FILE": os.fspath(index)},
        check=True,
        capture_output=True,
    ).stdout


@pytest.mark.parametrize(
    "fault",
    ("candidate-fsync", "candidate-move", "index-replace", "directory-fsync"),
)
def test_publication_fault_retains_created_commit_and_inspectable_storage(
    git_repo, git_run, monkeypatch, fault
):
    source = git_repo / "example.txt"
    source.write_text("base\n")
    git_run(git_repo, "add", source.name)
    git_run(git_repo, "commit", "-m", "base")
    before_head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    source.write_text("reviewed\n")
    git_run(git_repo, "add", source.name)
    real_index = git_repo / ".git" / "index"
    before_index = real_index.read_bytes()
    before_identity = _identity(real_index)
    before_entries = _index_entries(git_repo, real_index)
    candidate_path = git_repo.parent / "native-candidate-path"
    hook = git_repo / ".git" / "hooks" / "post-commit"
    hook.write_text(
        "#!/bin/sh\nset -e\n"
        f"printf '%s' \"$GIT_INDEX_FILE\" > {shlex.quote(str(candidate_path))}\n"
        "printf 'late hook content\\n' > hook-only.txt\ngit add hook-only.txt\n"
    )
    hook.chmod(0o700)
    fsync, replace = os.fsync, os.replace
    observation = {}

    def fail_flush(descriptor):
        if fault == "directory-fsync" and _identity(real_index) == observation.get("identity"):
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == _identity(real_index.parent):
                raise OSError(f"Injected {fault} failure")
        if candidate_path.exists():
            candidate = Path(candidate_path.read_text())
            info = os.fstat(descriptor)
            if candidate.exists() and (info.st_dev, info.st_ino) == _identity(candidate):
                observation.update(
                    candidate=candidate,
                    identity=_identity(candidate),
                    data=candidate.read_bytes(),
                    entries=_index_entries(git_repo, candidate),
                )
                if fault == "candidate-fsync":
                    raise OSError(f"Injected {fault} failure")
        return fsync(descriptor)

    def fail_move(source, destination, *args, **kwargs):
        if fault == "candidate-move" and source == observation.get("candidate"):
            raise OSError(f"Injected {fault} failure")
        if fault == "index-replace" and source == "index.lock" and destination == "index":
            assert kwargs.get("dst_dir_fd") is not None
            info = os.fstat(kwargs["dst_dir_fd"])
            assert (info.st_dev, info.st_ino) == _identity(real_index.parent)
            raise OSError(f"Injected {fault} failure")
        return replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", fail_flush)
    monkeypatch.setattr(os, "replace", fail_move)
    with pytest.raises(GitOutcomeUncertain, match=f"Injected {fault} failure") as failed:
        Repository(git_repo).execute(Action("commit", message="Retain after publication failure"))

    created = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert created != before_head
    assert git_run(git_repo, "rev-parse", "HEAD^").stdout.strip() == before_head
    assert git_run(git_repo, "show", "HEAD:example.txt").stdout == "reviewed\n"
    assert git_run(git_repo, "ls-tree", "--name-only", "HEAD").stdout == "example.txt\n"
    if fault == "directory-fsync":
        assert not real_index.with_name("index.lock").exists()
        assert _identity(real_index) != before_identity
        assert git_run(git_repo, "diff", "--cached", "--name-only").stdout == "hook-only.txt\n"
    else:
        assert _identity(real_index) == before_identity
        assert real_index.read_bytes() == before_index
        assert _index_entries(git_repo, real_index) == before_entries
        assert real_index.with_name("index.lock").is_file()
    candidate = observation["candidate"]
    if fault in ("index-replace", "directory-fsync"):
        assert not candidate.exists()
        candidate = real_index if fault == "directory-fsync" else real_index.with_name("index.lock")
    assert _identity(candidate) == observation["identity"]
    assert candidate.read_bytes() == observation["data"]
    assert _index_entries(git_repo, candidate) == observation["entries"]
    assert b"hook-only.txt\0" in observation["entries"]
    assert (git_repo / "hook-only.txt").read_text() == "late hook content\n"
    context = observation["candidate"].with_name("context.txt").read_text()
    assert f"native-created-oid: {created}" in context
    assert "phase: validating-index-publication" in context
    if fault != "candidate-fsync":
        assert "phase: candidate-move-pending" in context
    if fault in ("index-replace", "directory-fsync"):
        assert "phase: real-index-replace-pending" in context
    if fault == "directory-fsync":
        assert "phase: index-replaced-durability-pending" in context
    assert "phase: index-published" not in context
    assert created in str(failed.value)
    assert "before retrying" in str(failed.value)


@pytest.mark.parametrize("replacement", ("regular", "fifo"))
def test_replaced_recovery_context_is_neither_written_nor_opened_blockingly(
    git_repo, git_run, monkeypatch, replacement
):
    source = git_repo / "example.txt"
    source.write_text("base\n")
    git_run(git_repo, "add", source.name)
    git_run(git_repo, "commit", "-m", "base")
    before_head = git_run(git_repo, "rev-parse", "HEAD").stdout
    source.write_text("reviewed\n")
    git_run(git_repo, "add", source.name)
    index = git_repo / ".git" / "index"
    before_index = index.read_bytes()
    aside = git_repo.parent / "owned-context-aside"
    observation = {}
    blocking_attempts = []
    popen, open_file = subprocess.Popen, os.open

    def replace_context(command, *args, **kwargs):
        process = popen(command, *args, **kwargs)
        if "commit" in command and kwargs.get("env", {}).get("GIT_INDEX_FILE"):
            native_wait = process.wait

            def substitute(*wait_args, **wait_kwargs):
                code = native_wait(*wait_args, **wait_kwargs)
                context = Path(kwargs["env"]["GIT_INDEX_FILE"]).with_name("context.txt")
                observation.update(context=context, original=context.read_bytes(), code=code)
                os.replace(context, aside)
                if replacement == "regular":
                    context.write_bytes(b"foreign recovery context\n")
                else:
                    os.mkfifo(context, 0o600)
                observation["identity"] = _identity(context)
                return code

            process.wait = substitute
        return process

    def prevent_fixture_deadlock(path, flags, *args, **kwargs):
        if (
            replacement == "fifo"
            and path == observation.get("context")
            and flags & os.O_ACCMODE != os.O_RDWR
            and not flags & os.O_NONBLOCK
        ):
            blocking_attempts.append(path)
            raise BlockingIOError("A blocking FIFO open would hang without a reader")
        return open_file(path, flags, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", replace_context)
    monkeypatch.setattr(os, "open", prevent_fixture_deadlock)
    with pytest.raises(GitOutcomeUncertain):
        Repository(git_repo).execute(Action("commit", message="Do not write unknown context"))

    assert observation["code"] == 0
    assert not blocking_attempts
    context = observation["context"]
    assert _identity(context) == observation["identity"]
    if replacement == "regular":
        assert context.read_bytes() == b"foreign recovery context\n"
    assert aside.read_bytes() == observation["original"]
    assert git_run(git_repo, "rev-parse", "HEAD").stdout != before_head
    assert index.read_bytes() == before_index
    assert index.with_name("index.lock").is_file()
    assert context.with_name("index").is_file()


def test_candidate_directory_substitution_after_native_git_is_not_published(
    git_repo, git_run, monkeypatch
):
    source = git_repo / "example.txt"
    source.write_text("base\n")
    git_run(git_repo, "add", source.name)
    git_run(git_repo, "commit", "-m", "base")
    before_head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    source.write_text("reviewed\n")
    git_run(git_repo, "add", source.name)

    real_index = git_repo / ".git" / "index"
    real_index_before = real_index.read_bytes()
    real_index_identity = _identity(real_index)
    real_entries_before = _index_entries(git_repo, real_index)

    # Build a complete, valid, and observably distinct Git index using native Git.
    foreign_source = git_repo.parent / "foreign-candidate-source"
    foreign_source.mkdir()
    foreign_index = foreign_source / "index"
    foreign_context = foreign_source / "context.txt"
    foreign_only = git_repo / "foreign-only.txt"
    foreign_only.write_text("foreign candidate content\n")
    foreign_env = {**os.environ, "GIT_INDEX_FILE": os.fspath(foreign_index)}
    subprocess.run(
        ["git", "read-tree", "HEAD"],
        cwd=git_repo,
        env=foreign_env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "--", foreign_only.name],
        cwd=git_repo,
        env=foreign_env,
        check=True,
        capture_output=True,
    )
    foreign_context.write_bytes(b"foreign context must remain byte-identical\n")
    foreign_context.chmod(0o600)
    foreign_directory_identity = _identity(foreign_source)
    foreign_index_identity = _identity(foreign_index)
    foreign_index_before = foreign_index.read_bytes()
    foreign_context_before = foreign_context.read_bytes()
    foreign_entries_before = _index_entries(git_repo, foreign_index)
    assert foreign_entries_before != real_entries_before
    assert b"foreign-only.txt\0" in foreign_entries_before

    owned_aside = git_repo.parent / "owned-transaction-aside"
    popen = subprocess.Popen
    observation: dict[str, object] = {}

    def substitute_after_native_wait(command, *args, **kwargs):
        process = popen(command, *args, **kwargs)
        if isinstance(command, list) and "commit" in command:
            native_wait = process.wait

            def wait_then_substitute(*wait_args, **wait_kwargs):
                returncode = native_wait(*wait_args, **wait_kwargs)
                candidate = Path(kwargs["env"]["GIT_INDEX_FILE"])
                original_directory = candidate.parent
                lock = real_index.with_name("index.lock")
                observation.update(
                    native_returncode=returncode,
                    original_directory=original_directory,
                    original_directory_identity=_identity(original_directory),
                    original_index_identity=_identity(candidate),
                    original_index_bytes=candidate.read_bytes(),
                    original_entries=_index_entries(git_repo, candidate),
                    original_context_bytes=(original_directory / "context.txt").read_bytes(),
                    lock_identity=_identity(lock),
                )
                os.replace(original_directory, owned_aside)
                os.replace(foreign_source, original_directory)
                return returncode

            process.wait = wait_then_substitute
        return process

    monkeypatch.setattr(subprocess, "Popen", substitute_after_native_wait)

    with pytest.raises(GitOutcomeUncertain):
        Repository(git_repo).execute(Action("commit", message="Commit reviewed content"))

    assert observation["native_returncode"] == 0
    transaction_path = observation["original_directory"]
    assert isinstance(transaction_path, Path)
    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() != before_head
    assert git_run(git_repo, "show", "HEAD:example.txt").stdout == "reviewed\n"

    # The replacement directory is foreign. Rhizome must neither append recovery
    # observations to it nor move, publish, or clean up its candidate index.
    assert _identity(transaction_path) == foreign_directory_identity
    assert (transaction_path / "context.txt").read_bytes() == foreign_context_before
    assert _identity(transaction_path / "index") == foreign_index_identity
    assert (transaction_path / "index").read_bytes() == foreign_index_before
    assert _index_entries(git_repo, transaction_path / "index") == foreign_entries_before

    # The test-moved owned directory and candidate remain available for inspection.
    assert _identity(owned_aside) == observation["original_directory_identity"]
    assert _identity(owned_aside / "index") == observation["original_index_identity"]
    assert (owned_aside / "index").read_bytes() == observation["original_index_bytes"]
    assert _index_entries(git_repo, owned_aside / "index") == observation["original_entries"]
    assert (owned_aside / "context.txt").read_bytes() == observation["original_context_bytes"]

    # Native Git committed through its alternate index, but publication must fail
    # closed: the byte-identical real index and Rhizome-owned lock stay in place.
    assert _identity(real_index) == real_index_identity
    assert real_index.read_bytes() == real_index_before
    assert _index_entries(git_repo, real_index) == real_entries_before
    lock = real_index.with_name("index.lock")
    assert _identity(lock) == observation["lock_identity"]
