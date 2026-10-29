"""Public native-commit process boundaries; no coordinator/UI bypass proof."""

from __future__ import annotations

import shlex
import subprocess
import time
import tracemalloc

import pytest

from rhizome.git import GitError, GitOutcomeUncertain, Repository
from rhizome.models import Action


@pytest.mark.parametrize("fill", ("x", "+"))
def test_large_native_hook_output_is_not_buffered_in_memory(git_repo, git_run, fill):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    before = git_run(git_repo, "rev-parse", "HEAD").stdout
    path.write_text("reviewed\n")
    git_run(git_repo, "add", path.name)
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        "printf 'url 0https://user:fixture-secret@example.invalid/path?token=fixture-secret\\n' >&2\n"
        "printf 'scp fixture-secret@example.invalid:repo\\n' >&2\n"
        "printf 'scp git@example.invalid:repo\\n' >&2\n"
        f"dd if=/dev/zero bs=1048576 count=16 2>/dev/null | tr '\\000' {shlex.quote(fill)} >&2\n"
        "exit 1\n"
    )
    hook.chmod(0o700)
    repo = Repository(git_repo)

    # A structural buffering regression, not the application's acceptance memory
    # budget: a 16-MiB child stream must not become a complete Python byte string.
    started = time.monotonic()
    tracemalloc.start()
    try:
        with pytest.raises(GitError) as failed:
            repo.execute(Action("commit", message="Bound native hook output"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    elapsed = time.monotonic() - started
    assert "truncated" in str(failed.value).lower()
    assert "0https://example.invalid/path" in str(failed.value)
    assert "fixture-secret" not in str(failed.value)
    assert "scp example.invalid:repo" in str(failed.value)
    assert "scp git@example.invalid:repo" in str(failed.value)
    assert git_run(git_repo, "rev-parse", "HEAD").stdout == before
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert path.read_text() == "reviewed\n"
    assert peak < 8 * 1024 * 1024, f"native process capture allocated {peak} bytes"
    # This loose guard catches catastrophic diagnostic scanning, not a p95 or
    # human/terminal responsiveness acceptance claim.
    assert elapsed < 10, f"bounded native diagnostic took {elapsed:.2f}s"


def test_interrupted_wait_retains_a_live_git_and_its_recovery_identity(
    git_repo, git_run, monkeypatch
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    before_head = git_run(git_repo, "rev-parse", "HEAD").stdout
    path.write_text("reviewed\n")
    git_run(git_repo, "add", path.name)
    index = git_repo / ".git" / "index"
    before_index = index.read_bytes()
    entered = git_repo.parent / "hook-entered"
    release = git_repo.parent / "release-hook"
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\nset -e\n"
        "printf 'hook content\\n' > hook-only.txt\ngit add hook-only.txt\n"
        f": > {shlex.quote(str(entered))}\n"
        f"while test ! -e {shlex.quote(str(release))}; do sleep 0.02; done\n"
    )
    hook.chmod(0o700)
    popen = subprocess.Popen
    children = []

    def interrupt_wait(command, *args, **kwargs):
        process = popen(command, *args, **kwargs)
        if "commit" in command:
            wait = process.wait
            children.append((process, wait))

            def interrupted(*_args, **_kwargs):
                deadline = time.monotonic() + 10
                while not entered.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert entered.exists(), "native Git never reached its fixture hook"
                raise OSError("Injected wait interruption")

            process.wait = interrupted
        return process

    monkeypatch.setattr(subprocess, "Popen", interrupt_wait)
    try:
        with pytest.raises(GitOutcomeUncertain, match="Injected wait interruption"):
            Repository(git_repo).execute(Action("commit", message="Unsettled native Git"))
        assert len(children) == 1
        process, _ = children[0]
        assert process.poll() is None, "Rhizome must not kill Git after an interrupted wait"
        assert index.read_bytes() == before_index
        assert (index.parent / "index.lock").is_file()
        artifacts = list(index.parent.glob("rhizome-commit-*"))
        assert len(artifacts) == 1
        assert (artifacts[0] / "index").is_file()
        assert f"git-pid: {process.pid}" in (artifacts[0] / "context.txt").read_text()
    finally:
        release.touch()
        for process, wait in children:
            process.wait = wait
            assert wait(timeout=20) == 0

    assert index.read_bytes() == before_index
    assert git_run(git_repo, "rev-parse", "HEAD").stdout != before_head
    assert git_run(git_repo, "show", "HEAD:hook-only.txt").stdout == "hook content\n"


def test_dangling_alternate_lock_prevents_index_publication(git_repo, git_run):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    before_head = git_run(git_repo, "rev-parse", "HEAD").stdout
    path.write_text("reviewed\n")
    git_run(git_repo, "add", path.name)
    index = git_repo / ".git" / "index"
    before_index = index.read_bytes()
    hook = git_repo / ".git" / "hooks" / "post-commit"
    hook.write_text('#!/bin/sh\nln -s never-present "$GIT_INDEX_FILE.lock"\n')
    hook.chmod(0o700)

    with pytest.raises(GitOutcomeUncertain, match="alternate index.*locked"):
        Repository(git_repo).execute(Action("commit", message="Do not publish an unsettled index"))

    assert git_run(git_repo, "rev-parse", "HEAD").stdout != before_head
    assert index.read_bytes() == before_index
    assert (index.parent / "index.lock").is_file()
    artifacts = list(index.parent.glob("rhizome-commit-*"))
    assert len(artifacts) == 1
    alternate_lock = artifacts[0] / "index.lock"
    assert alternate_lock.is_symlink() and not alternate_lock.exists()
    assert (artifacts[0] / "index").is_file()
