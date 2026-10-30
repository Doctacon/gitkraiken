from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from rhizome.git import GitError, GitOutcomeUncertain, Repository
from rhizome.models import Action
from rhizome.operations import OperationCoordinator
from rhizome.recovery import RecoveryJournal


def _commit(git_run, root: Path, name: str, content: str, message: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git_run(root, "add", "--", name)
    git_run(root, "commit", "-m", message)
    return git_run(root, "rev-parse", "HEAD").stdout.strip()


def _repositories(tmp_path: Path, git_run) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git_run(remote, "init", "--bare", "-b", "main")

    writer = tmp_path / "writer"
    writer.mkdir()
    git_run(writer, "init", "-b", "main")
    _commit(git_run, writer, "base.txt", "base\n", "base")
    git_run(writer, "remote", "add", "origin", str(remote))
    git_run(writer, "push", "-u", "origin", "main")

    local = tmp_path / "local"
    git_run(tmp_path, "clone", str(remote), str(local))
    git_run(local, "config", "user.name", "Test User")
    git_run(local, "config", "user.email", "test@example.invalid")
    git_run(local, "config", "commit.gpgsign", "false")
    return remote, writer, local


def _advance(git_run, writer: Path, name: str) -> str:
    oid = _commit(git_run, writer, name, f"{name}\n", f"add {name}")
    git_run(writer, "push", "origin", "main")
    return oid


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _install_lingering_post_merge_hook(
    local: Path, *, entered: Path, child_pid: Path, release: Path, cleaned: Path
) -> None:
    hook = local / ".git" / "hooks" / "post-merge"
    hook.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "(\n"
        f"  : > {shlex.quote(str(entered))}\n"
        "  count=0\n"
        f"  while test ! -e {shlex.quote(str(release))} && test $count -lt 750; do\n"
        "    sleep 0.02\n"
        "    count=$((count + 1))\n"
        "  done\n"
        f"  : > {shlex.quote(str(cleaned))}\n"
        ") </dev/null >/dev/null 2>&1 &\n"
        f"printf '%s\\n' \"$!\" > {shlex.quote(str(child_pid))}\n"
    )
    hook.chmod(0o700)


def test_fast_forward_hook_descendant_leaves_durable_common_repo_blocker(
    tmp_path: Path, git_run
) -> None:
    _remote, writer, local = _repositories(tmp_path, git_run)
    linked = tmp_path / "linked"
    git_run(local, "worktree", "add", "-b", "linked", str(linked), "HEAD")
    first_target = _advance(git_run, writer, "first.txt")

    entered = tmp_path / "hook-child-entered"
    child_pid = tmp_path / "hook-child-pid"
    release = tmp_path / "release-hook-child"
    cleaned = tmp_path / "hook-child-cleaned"
    _install_lingering_post_merge_hook(
        local, entered=entered, child_pid=child_pid, release=release, cleaned=cleaned
    )

    journal = RecoveryJournal(tmp_path / "pull-journal")
    coordinator = OperationCoordinator(Repository(local), pull_journal=journal)
    plan = coordinator.prepare_pull(Action("pull", pull_mode="ff-only"))

    try:
        with pytest.raises(GitOutcomeUncertain, match="process group is still present"):
            coordinator.submit_pull(plan)

        assert _wait_until(lambda: entered.exists() and child_pid.exists())
        descendant = int(child_pid.read_text().strip())
        os.kill(descendant, 0)
        assert git_run(local, "rev-parse", "HEAD").stdout.strip() == first_target
        assert coordinator.activity()[0].state == "uncertain"

        second_target = _advance(git_run, writer, "second.txt")
        fetched_before = git_run(local, "rev-parse", "refs/remotes/origin/main").stdout.strip()
        assert fetched_before == first_target != second_target

        restarted = OperationCoordinator(Repository(local), pull_journal=None)
        with pytest.raises(GitError, match="durable uncertain pull writer lease"):
            restarted.prepare_pull(Action("pull", pull_mode="ff-only"))
        assert (
            git_run(local, "rev-parse", "refs/remotes/origin/main").stdout.strip() == fetched_before
        )
        assert restarted.activity()[0].state == "failed"

        blocked = local / "blocked.txt"
        blocked.write_text("must remain unstaged\n")
        stage = restarted.plan(Action("stage", paths=("blocked.txt",)))
        with pytest.raises(GitError, match="durable uncertain pull writer lease"):
            restarted.submit(stage)
        assert git_run(local, "status", "--porcelain=v1", "--", "blocked.txt").stdout == (
            "?? blocked.txt\n"
        )
        assert restarted.activity()[0].state == "failed"

        linked_coordinator = OperationCoordinator(Repository(linked), pull_journal=None)
        with pytest.raises(GitError, match="durable uncertain pull writer lease"):
            linked_coordinator.prepare_pull(Action("pull", pull_mode="ff-only"))

        linked_file = linked / "linked.txt"
        linked_file.write_text("must also remain unstaged\n")
        linked_stage = linked_coordinator.plan(Action("stage", paths=("linked.txt",)))
        with pytest.raises(GitError, match="durable uncertain pull writer lease"):
            linked_coordinator.submit(linked_stage)
        assert git_run(linked, "status", "--porcelain=v1", "--", "linked.txt").stdout == (
            "?? linked.txt\n"
        )
    finally:
        release.touch()
        assert _wait_until(cleaned.exists), "post-merge fixture child did not clean up"


def test_cross_process_writer_guard_blocks_pull_fetch_during_checkout_hook(
    tmp_path: Path, git_run
) -> None:
    _remote, writer, local = _repositories(tmp_path, git_run)
    git_run(local, "branch", "other")
    git_run(local, "branch", "--set-upstream-to=origin/main", "other")
    before = git_run(local, "rev-parse", "refs/remotes/origin/main").stdout.strip()
    entered = tmp_path / "checkout-hook-entered"
    release = tmp_path / "release-checkout-hook"
    checkout_done = tmp_path / "checkout-done"
    fetch_done = tmp_path / "fetch-done"
    hook = local / ".git" / "hooks" / "post-checkout"
    hook.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f": > {shlex.quote(str(entered))}\n"
        "count=0\n"
        f"while test ! -e {shlex.quote(str(release))} && test $count -lt 750; do\n"
        "  sleep 0.02\n"
        "  count=$((count + 1))\n"
        "done\n"
    )
    hook.chmod(0o700)
    environment = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1] / "src")
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    checkout_script = (
        "from pathlib import Path\n"
        "from rhizome.git import Repository\n"
        "from rhizome.models import Action\n"
        "from rhizome.operations import OperationCoordinator\n"
        f"c=OperationCoordinator(Repository(Path({str(local)!r})))\n"
        "p=c.plan(Action('checkout', target='other'))\n"
        "c.submit(p)\n"
        f"Path({str(checkout_done)!r}).touch()\n"
    )
    fetch_script = (
        "from pathlib import Path\n"
        "from rhizome.git import Repository\n"
        "from rhizome.models import Action\n"
        "from rhizome.operations import OperationCoordinator\n"
        f"c=OperationCoordinator(Repository(Path({str(local)!r})))\n"
        "p=c.prepare_pull(Action('pull', pull_mode='ff-only'))\n"
        f"Path({str(fetch_done)!r}).write_text(p.target_oid)\n"
    )
    checkout_process = subprocess.Popen(
        [sys.executable, "-c", checkout_script],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    fetch_process: subprocess.Popen[str] | None = None
    try:
        assert _wait_until(entered.exists), "checkout hook did not block"
        target = _advance(git_run, writer, "cross-process.txt")
        fetch_process = subprocess.Popen(
            [sys.executable, "-c", fetch_script],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.5)
        assert not fetch_done.exists()
        assert git_run(local, "rev-parse", "refs/remotes/origin/main").stdout.strip() == before

        release.touch()
        checkout_stdout, checkout_stderr = checkout_process.communicate(timeout=15)
        assert checkout_process.returncode == 0, checkout_stdout + checkout_stderr
        fetch_stdout, fetch_stderr = fetch_process.communicate(timeout=15)
        assert fetch_process.returncode == 0, fetch_stdout + fetch_stderr
        assert checkout_done.exists()
        assert fetch_done.read_text() == target
        assert git_run(local, "rev-parse", "refs/remotes/origin/main").stdout.strip() == target
    finally:
        release.touch()
        for process in (checkout_process, fetch_process):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def test_successful_fast_forward_clears_lease_and_allows_next_pull(tmp_path: Path, git_run) -> None:
    _remote, writer, local = _repositories(tmp_path, git_run)
    first_target = _advance(git_run, writer, "first.txt")
    first = OperationCoordinator(
        Repository(local), pull_journal=RecoveryJournal(tmp_path / "pull-journal")
    )

    first_plan = first.prepare_pull(Action("pull", pull_mode="ff-only"))
    assert first.submit_pull(first_plan) == f"Fast-forwarded main to {first_target[:12]}."

    second_target = _advance(git_run, writer, "second.txt")
    restarted = OperationCoordinator(Repository(local), pull_journal=None)
    second_plan = restarted.prepare_pull(Action("pull", pull_mode="ff-only"))
    assert restarted.submit_pull(second_plan) == f"Fast-forwarded main to {second_target[:12]}."
    assert git_run(local, "rev-parse", "HEAD").stdout.strip() == second_target
