"""Native local integration process boundaries for guarded pull coordination."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from rhizome.git import GitOutcomeUncertain, Repository


def commit_file(git_run, root: Path, path: str, text: str, message: str) -> str:
    (root / path).write_text(text)
    git_run(root, "add", "--", path)
    git_run(root, "commit", "-m", message)
    return git_run(root, "rev-parse", "HEAD").stdout.strip()


def test_guarded_pull_fetch_disables_submodule_recursion_only_when_requested(git_repo, monkeypatch):
    repository = Repository(git_repo)
    scope = SimpleNamespace(
        remote="origin",
        no_tags=False,
        refspecs=(SimpleNamespace(raw="+refs/heads/*:refs/remotes/origin/*"),),
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(repository, "_validate_fetch_scope", lambda remote: (scope,))

    def record(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(["git", *command], 0, b"", b"")

    monkeypatch.setattr(repository, "_git", record)

    repository._execute_fetch("origin")
    repository._execute_fetch("origin", disable_submodule_recursion=True)

    ordinary, guarded = commands
    assert "submodule.recurse=false" not in ordinary
    assert "--no-recurse-submodules" not in ordinary
    assert guarded[:6] == [
        "-c",
        "fetch.pruneTags=false",
        "-c",
        "remote.origin.pruneTags=false",
        "-c",
        "submodule.recurse=false",
    ]
    assert guarded[6:10] == [
        "fetch",
        "--no-force",
        "--no-prune-tags",
        "--no-recurse-submodules",
    ]
    assert guarded[10:] == [
        "--refmap=",
        "--",
        "origin",
        "+refs/heads/*:refs/remotes/origin/*",
    ]


def test_native_integration_returns_success_and_nonzero_conflict(git_repo, git_run):
    base = commit_file(git_run, git_repo, "conflict.txt", "base\n", "base")
    git_run(git_repo, "switch", "-c", "topic")
    topic = commit_file(git_run, git_repo, "conflict.txt", "topic\n", "topic")
    git_run(git_repo, "switch", "main")

    fast_forward = Repository(git_repo)._run_integration(
        ["merge", "--ff-only", topic], operation="fast-forward pull"
    )
    assert fast_forward.returncode == 0
    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == topic

    git_run(git_repo, "reset", "--hard", base)
    commit_file(git_run, git_repo, "conflict.txt", "main\n", "main")
    conflicted = Repository(git_repo)._run_integration(
        ["merge", "--no-edit", topic], operation="merge pull"
    )
    assert conflicted.returncode != 0
    assert (git_repo / ".git" / "MERGE_HEAD").is_file()
    assert b"CONFLICT" in conflicted.stdout + conflicted.stderr


def test_native_integration_runs_hooks_in_isolated_repository_and_records_spawn_first(
    git_repo, git_run, monkeypatch, tmp_path
):
    base = commit_file(git_run, git_repo, "base.txt", "base\n", "base")
    git_run(git_repo, "switch", "-c", "topic")
    topic = commit_file(git_run, git_repo, "topic.txt", "topic\n", "topic")
    git_run(git_repo, "switch", "main")
    commit_file(git_run, git_repo, "main.txt", "main\n", "main")

    spawn_record = tmp_path / "spawn-record"
    hook_record = tmp_path / "hook-record"
    hostile = tmp_path / "hostile.git"
    monkeypatch.setenv("GIT_DIR", str(hostile))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "hostile-worktree"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "hostile-index"))
    hook = git_repo / ".git" / "hooks" / "pre-merge-commit"
    hook.write_text(
        "#!/bin/sh\nset -eu\n"
        f"test -s {shlex.quote(str(spawn_record))}\n"
        f"printf 'cwd=%s\\n' \"$PWD\" > {shlex.quote(str(hook_record))}\n"
        f'test "${{GIT_DIR-}}" != {shlex.quote(str(hostile))}\n'
        f'test "${{GIT_WORK_TREE-}}" != {shlex.quote(str(tmp_path / "hostile-worktree"))}\n'
        f'test "${{GIT_INDEX_FILE-}}" != {shlex.quote(str(tmp_path / "hostile-index"))}\n'
        f"printf 'hook-ran=yes\\n' >> {shlex.quote(str(hook_record))}\n"
    )
    hook.chmod(0o700)

    def record_spawn(pid: int) -> None:
        assert os.getpgid(pid) == pid
        descriptor = os.open(spawn_record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(f"pid={pid}\npgid={pid}\n")
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(tmp_path, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    result = Repository(git_repo)._run_integration(
        ["merge", "--no-ff", "--no-edit", topic],
        operation="merge pull",
        on_spawn=record_spawn,
    )

    assert result.returncode == 0
    assert "pid=" in spawn_record.read_text()
    assert hook_record.read_text() == f"cwd={git_repo}\nhook-ran=yes\n"
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name)
    assert len(git_run(git_repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()) == 3
    assert base != topic


def test_native_integration_bounds_regular_spool_diagnostics(git_repo, tmp_path):
    script = tmp_path / "large-diagnostics"
    script.write_text(
        "#!/bin/sh\n"
        "dd if=/dev/zero bs=1048576 count=2 2>/dev/null | tr '\\000' o\n"
        "dd if=/dev/zero bs=1048576 count=2 2>/dev/null | tr '\\000' e >&2\n"
        "exit 7\n"
    )
    script.chmod(0o700)

    result = Repository(git_repo)._run_integration(
        ["-c", f"alias.rhizome=!{shlex.quote(str(script))}", "rhizome"],
        operation="diagnostic fixture",
    )

    assert result.returncode == 7
    assert len(result.stdout) < 66_000
    assert len(result.stderr) < 66_000
    assert b"Git output truncated by Rhizome" in result.stdout
    assert b"Git error output truncated by Rhizome" in result.stderr


def test_native_integration_spawn_and_callback_exceptions_are_uncertain_without_kill(
    git_repo, monkeypatch, tmp_path
):
    repo = Repository(git_repo)

    def fail_spawn(*_args, **_kwargs):
        raise OSError("injected spawn boundary failure")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", fail_spawn)
        with pytest.raises(GitOutcomeUncertain, match="unobserved.*spawn boundary failure"):
            repo._run_integration(["status", "--short"], operation="spawn fixture")

    release = tmp_path / "release-callback-child"
    entered = tmp_path / "callback-child-entered"
    script = tmp_path / "wait-for-release"
    script.write_text(
        "#!/bin/sh\nset -eu\n"
        f": > {shlex.quote(str(entered))}\n"
        f"while test ! -e {shlex.quote(str(release))}; do sleep 0.02; done\n"
    )
    script.chmod(0o700)
    real_popen = subprocess.Popen
    spawned: list[tuple[subprocess.Popen[bytes], object]] = []

    def observe(command, *args, **kwargs):
        process = real_popen(command, *args, **kwargs)
        spawned.append((process, process.wait))
        return process

    def fail_callback(_pid: int) -> None:
        deadline = time.monotonic() + 5
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert entered.exists()
        raise OSError("injected durable callback failure")

    monkeypatch.setattr(subprocess, "Popen", observe)
    try:
        with pytest.raises(GitOutcomeUncertain, match="durable callback failure"):
            repo._run_integration(
                ["-c", f"alias.rhizome=!{shlex.quote(str(script))}", "rhizome"],
                operation="callback fixture",
                on_spawn=fail_callback,
            )
        assert len(spawned) == 1
        assert spawned[0][0].poll() is None, "callback failure must not kill or wait for Git"
    finally:
        release.touch()
        for _process, wait in spawned:
            assert wait(timeout=10) == 0


def test_native_integration_signal_terminated_git_is_uncertain(git_repo):
    spawned: list[int] = []

    def terminate(pid: int) -> None:
        spawned.append(pid)
        os.kill(pid, signal.SIGTERM)

    with pytest.raises(
        GitOutcomeUncertain,
        match=r"terminated by signal 15; process/group \d+ .*must be inspected before retrying",
    ):
        Repository(git_repo)._run_integration(
            ["status", "--short"],
            operation="signal fixture",
            on_spawn=terminate,
        )

    assert len(spawned) == 1


def test_native_integration_reports_surviving_descendant_without_killing_it(git_repo, tmp_path):
    child_record = tmp_path / "descendant-pid"
    script = tmp_path / "leave-descendant"
    script.write_text(
        f"#!/bin/sh\nsleep 30 &\nprintf '%s\\n' $! > {shlex.quote(str(child_record))}\nexit 0\n"
    )
    script.chmod(0o700)
    pgids: list[int] = []

    try:
        with pytest.raises(GitOutcomeUncertain, match="process group is still present"):
            Repository(git_repo)._run_integration(
                ["-c", f"alias.rhizome=!{shlex.quote(str(script))}", "rhizome"],
                operation="descendant fixture",
                on_spawn=pgids.append,
            )
        descendant = int(child_record.read_text())
        os.kill(descendant, 0)
        assert os.getpgid(descendant) == pgids[0]
    finally:
        if pgids:
            try:
                os.killpg(pgids[0], signal.SIGTERM)
            except ProcessLookupError:
                pass
