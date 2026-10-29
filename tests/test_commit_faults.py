"""Public Repository.execute real-Git commit fault proofs."""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from rhizome.git import GitError, GitOutcomeUncertain, Repository
from rhizome.models import Action


def _head_state(git_repo, git_run) -> tuple[str, str]:
    head = git_run(git_repo, "rev-parse", "--verify", "HEAD").stdout.strip()
    symbolic = git_run(
        git_repo, "symbolic-ref", "--quiet", "HEAD", check=False
    ).stdout.removesuffix("\n")
    return head, symbolic


def test_definite_commit_launch_failure_cleans_owned_transaction(
    git_repo, git_run, monkeypatch
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    git_run(git_repo, "add", path.name)

    index = git_repo / ".git" / "index"
    before_head = _head_state(git_repo, git_run)
    before_index = index.read_bytes()
    before_entries = git_run(git_repo, "ls-files", "--stage", "-z").stdout
    repo = Repository(git_repo)
    popen = subprocess.Popen
    which = shutil.which
    launch_observation: dict[str, object] = {}
    launches = []

    def no_commit_executable(command, *args, **kwargs):
        if command == "git":
            lock = index.with_name("index.lock")
            artifacts = list(index.parent.glob("rhizome-commit-*"))
            assert len(artifacts) == 1
            candidate = artifacts[0] / "index"
            launch_observation.update(
                lock_identity=(lock.stat().st_dev, lock.stat().st_ino),
                lock_bytes=lock.read_bytes(),
                candidate_identity=(candidate.stat().st_dev, candidate.stat().st_ino),
                candidate_bytes=candidate.read_bytes(),
                artifact=artifacts[0],
            )
            return None
        return which(command, *args, **kwargs)

    def observe_launch(command, *args, **kwargs):
        if isinstance(command, list) and "commit" in command:
            launches.append(command)
        return popen(command, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", no_commit_executable)
    monkeypatch.setattr(subprocess, "Popen", observe_launch)

    with pytest.raises(GitError, match="Could not start Git commit") as failed:
        repo.execute(Action("commit", message="Launch must fail cleanly"))

    assert not isinstance(failed.value, GitOutcomeUncertain)
    assert not launches
    assert launch_observation["candidate_bytes"] == before_index
    assert launch_observation["lock_bytes"]
    assert _head_state(git_repo, git_run) == before_head
    assert index.read_bytes() == before_index
    assert git_run(git_repo, "ls-files", "--stage", "-z").stdout == before_entries
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert path.read_text() == "reviewed\n"
    assert not index.with_name("index.lock").exists()
    assert not list(index.parent.glob("rhizome-commit-*"))
    assert not os.path.exists(launch_observation["artifact"])


def test_spawn_boundary_failure_with_a_live_child_retains_transaction(
    git_repo, git_run, monkeypatch
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    before_head = _head_state(git_repo, git_run)
    path.write_text("reviewed\n")
    git_run(git_repo, "add", path.name)
    index = git_repo / ".git" / "index"
    before_index = index.read_bytes()
    entered, release = git_repo.parent / "spawn-entered", git_repo.parent / "spawn-release"
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f": > {shlex.quote(str(entered))}\n"
        f"while test ! -e {shlex.quote(str(release))}; do sleep 0.02; done\n"
        "printf 'hook content\\n' > hook-only.txt\n"
        "git add hook-only.txt\n"
    )
    hook.chmod(0o700)
    popen = subprocess.Popen
    spawned = {}

    def interrupted_creation(command, *args, **kwargs):
        process = popen(command, *args, **kwargs)
        if isinstance(command, list) and "commit" in command:
            spawned["process"] = process
            spawned["candidate"] = Path(kwargs["env"]["GIT_INDEX_FILE"])
            deadline = time.monotonic() + 10
            while not entered.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert entered.exists(), "native Git never reached the fixture hook"
            raise OSError("Injected interruption after process creation")
        return process

    monkeypatch.setattr(subprocess, "Popen", interrupted_creation)
    try:
        with pytest.raises(GitOutcomeUncertain):
            Repository(git_repo).execute(Action("commit", message="Unknown creation outcome"))
        assert entered.exists()
        assert spawned["process"].poll() is None
        assert _head_state(git_repo, git_run) == before_head
        assert index.read_bytes() == before_index
        assert index.with_name("index.lock").is_file()
        assert spawned["candidate"].is_file()
    finally:
        release.touch()
        if "process" in spawned:
            spawned["process"].wait(timeout=20)

    assert spawned["process"].returncode == 0
    assert index.read_bytes() == before_index
    assert index.with_name("index.lock").is_file()
    assert spawned["candidate"].is_file()
    assert git_run(git_repo, "show", "HEAD:hook-only.txt").stdout == "hook content\n"


def test_unprovable_group_liveness_retains_the_unpublished_index(
    git_repo, git_run, monkeypatch
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    before_head = _head_state(git_repo, git_run)
    path.write_text("reviewed\n")
    git_run(git_repo, "add", path.name)
    index = git_repo / ".git" / "index"
    before_index = index.read_bytes()
    probes = []

    def denied_probe(pgid, signal_number):
        assert pgid > 0 and signal_number == 0
        probes.append(pgid)
        raise PermissionError("Injected unprovable group liveness")

    monkeypatch.setattr(os, "killpg", denied_probe)
    with pytest.raises(GitOutcomeUncertain, match="group quiescence"):
        Repository(git_repo).execute(Action("commit", message="Fail closed on an unknown group"))

    assert len(probes) == 1
    assert _head_state(git_repo, git_run)[0] != before_head[0]
    assert git_run(git_repo, "show", "HEAD:example.txt").stdout == "reviewed\n"
    assert index.read_bytes() == before_index
    assert index.with_name("index.lock").is_file()
    artifacts = list(index.parent.glob("rhizome-commit-*"))
    assert len(artifacts) == 1 and (artifacts[0] / "index").is_file()
    assert f"git-pgid: {probes[0]}" in (artifacts[0] / "context.txt").read_text()


def test_negative_direct_signal_retains_owned_lock_and_candidate(
    git_repo, git_run, monkeypatch
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    git_run(git_repo, "add", path.name)

    index = git_repo / ".git" / "index"
    before_head = _head_state(git_repo, git_run)
    before_index = index.read_bytes()
    before_entries = git_run(git_repo, "ls-files", "--stage", "-z").stdout
    hook_finished = git_repo.parent / "signal-hook-finished"
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"trap ': > {shlex.quote(str(hook_finished))}' EXIT\n"
        "kill -TERM \"$PPID\"\n"
    )
    hook.chmod(0o700)

    popen = subprocess.Popen
    spawned: dict[str, object] = {}

    def observe_native_commit(command, *args, **kwargs):
        process = popen(command, *args, **kwargs)
        if isinstance(command, list) and "commit" in command:
            artifacts = list(index.parent.glob("rhizome-commit-*"))
            assert len(artifacts) == 1
            candidate = artifacts[0] / "index"
            lock = index.with_name("index.lock")
            spawned.update(
                process=process,
                artifact=artifacts[0],
                artifact_identity=(artifacts[0].stat().st_dev, artifacts[0].stat().st_ino),
                candidate=candidate,
                candidate_bytes=candidate.read_bytes(),
                lock_identity=(lock.stat().st_dev, lock.stat().st_ino),
            )
        return process

    monkeypatch.setattr(subprocess, "Popen", observe_native_commit)

    with pytest.raises(GitOutcomeUncertain, match="interrupted by a signal"):
        Repository(git_repo).execute(Action("commit", message="Signal native Git"))

    process = spawned["process"]
    assert process.returncode is not None and process.returncode < 0
    assert hook_finished.is_file(), "the signal-injecting hook must finish, not be killed"
    assert _head_state(git_repo, git_run) == before_head
    assert index.read_bytes() == before_index
    assert git_run(git_repo, "ls-files", "--stage", "-z").stdout == before_entries
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert path.read_text() == "reviewed\n"

    artifact = spawned["artifact"]
    candidate = spawned["candidate"]
    lock = index.with_name("index.lock")
    assert artifact.is_dir()
    assert (artifact.stat().st_dev, artifact.stat().st_ino) == spawned["artifact_identity"]
    assert candidate.is_file()
    retained_candidate = candidate.read_bytes()
    assert spawned["candidate_bytes"] == before_index
    assert retained_candidate
    assert subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=git_repo,
        env={**os.environ, "GIT_INDEX_FILE": str(candidate)},
        check=True,
        capture_output=True,
    ).stdout == before_entries.encode()
    assert lock.is_file()
    assert (lock.stat().st_dev, lock.stat().st_ino) == spawned["lock_identity"]
    context = (artifact / "context.txt").read_text()
    assert f"git-pid: {process.pid}" in context
    assert f"native-returncode: {process.returncode}" in context
    assert "phase: native-returned" in context


@pytest.mark.parametrize(
    ("launcher", "redirect_output"),
    (("post-commit", False), ("post-commit", True), ("signer", True)),
)
def test_foreground_hook_child_blocks_publication_after_hook_shell_is_killed(
    git_repo, git_run, monkeypatch, launcher, redirect_output
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    git_run(git_repo, "add", path.name)

    index = git_repo / ".git" / "index"
    before_index = index.read_bytes()
    before_index_identity = (index.stat().st_dev, index.stat().st_ino)
    before_head = _head_state(git_repo, git_run)
    hook_pid_file = git_repo.parent / "hook-shell-pid"
    child_pid_file = git_repo.parent / "hook-child-pid"
    candidate_file = git_repo.parent / "hook-candidate-index"
    child_ready = git_repo.parent / "hook-child-ready"
    release_child = git_repo.parent / "release-hook-child"
    child_finished = git_repo.parent / "hook-child-finished"
    child_status = git_repo.parent / "hook-child-status"
    late_path = git_repo / "late-hook.txt"
    child = git_repo.parent / "foreground-hook-child"
    child.write_text(
        "#!/bin/sh\n"
        + ("exec >/dev/null 2>&1\n" if redirect_output else "")
        + f"printf '%s\\n' \"$$\" > {shlex.quote(str(child_pid_file))}\n"
        f"printf '%s\\n' \"$GIT_INDEX_FILE\" > {shlex.quote(str(candidate_file))}\n"
        f": > {shlex.quote(str(child_ready))}\n"
        f"while test ! -e {shlex.quote(str(release_child))}; do sleep 0.02; done\n"
        f"printf 'late hook content\\n' > {shlex.quote(str(late_path))}\n"
        f"git add -- {shlex.quote(late_path.name)}\n"
        "status=$?\n"
        f"printf '%s\\n' \"$status\" > {shlex.quote(str(child_status))}\n"
        f": > {shlex.quote(str(child_finished))}\n"
        "exit \"$status\"\n"
    )
    child.chmod(0o700)
    hook = (
        git_repo / ".git" / "hooks" / "post-commit"
        if launcher == "post-commit" else git_repo.parent / "fixture-signer"
    )
    hook.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$$\" > {shlex.quote(str(hook_pid_file))}\n"
        f"/bin/sh {shlex.quote(str(child))}\n"
    )
    hook.chmod(0o700)
    if launcher == "signer":
        git_run(git_repo, "config", "gpg.program", str(hook))
        git_run(git_repo, "config", "user.signingKey", "fixture-key")
        git_run(git_repo, "config", "commit.gpgsign", "true")

    repo = Repository(git_repo)
    popen = subprocess.Popen
    native_exited = threading.Event()
    execute_finished = threading.Event()
    spawned: dict[str, object] = {}
    outcome: list[str | BaseException] = []

    def observe_native_commit(command, *args, **kwargs):
        process = popen(command, *args, **kwargs)
        if isinstance(command, list) and "commit" in command:
            native_wait = process.wait

            def observe_wait(*wait_args, **wait_kwargs):
                returncode = native_wait(*wait_args, **wait_kwargs)
                spawned["returncode"] = returncode
                native_exited.set()
                return returncode

            process.wait = observe_wait
            spawned["process"] = process
        return process

    monkeypatch.setattr(subprocess, "Popen", observe_native_commit)

    def execute() -> None:
        try:
            outcome.append(repo.execute(Action("commit", message="Keep a live foreground child")))
        except BaseException as error:
            outcome.append(error)
        finally:
            execute_finished.set()

    worker = threading.Thread(target=execute, name="foreground-hook-commit")
    worker.start()
    child_was_ready = False
    native_exited_while_child_active = False
    native_was_alive = False
    candidate = None
    active_candidate_exists = False
    active_index_identity = None
    active_index_bytes = b""
    active_execute_finished = False
    try:
        deadline = time.monotonic() + 10
        while not child_ready.exists() and not execute_finished.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_was_ready = child_ready.exists()
        if child_was_ready:
            hook_pid = int(hook_pid_file.read_text())
            child_pid = int(child_pid_file.read_text())
            candidate = candidate_file.read_text().removesuffix("\n")
            assert candidate
            candidate = os.path.abspath(candidate)
            assert os.path.isfile(candidate)
            assert not os.path.exists(f"{candidate}.lock"), (
                "the foreground child must still be gated before taking its candidate lock"
            )
            assert not child_finished.exists()
            os.kill(child_pid, 0)
            os.kill(hook_pid, signal.SIGKILL)
            native_exited_while_child_active = native_exited.wait(10 if redirect_output else 1)
            # Git 2.36 itself waits for inherited hook-output pipe EOF. That is
            # not a defect: both paths must preserve the active candidate. The
            # redirected cases require direct Git exit and expose the missing
            # descendant barrier independently of native buffering behavior.
            execute_finished.wait(1)
            native_was_alive = spawned["process"].poll() is None
            active_candidate_exists = os.path.isfile(candidate)
            active_index_identity = (index.stat().st_dev, index.stat().st_ino)
            active_index_bytes = index.read_bytes()
            active_execute_finished = execute_finished.is_set()
            assert not child_finished.exists()
            os.kill(child_pid, 0)
    finally:
        release_child.touch()
        if child_was_ready:
            deadline = time.monotonic() + 15
            while not child_finished.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert child_finished.exists(), "foreground child did not complete after its gate was released"
        worker.join(timeout=15)
        assert not worker.is_alive(), "Repository.execute did not settle after the child completed"

    assert child_was_ready, "native Git never reached the foreground-child fixture gate"
    if redirect_output:
        assert native_exited_while_child_active, (
            "native Git did not directly exit while the redirected foreground child remained active"
        )
    elif not native_exited_while_child_active:
        assert native_was_alive and not active_execute_finished
    if launcher == "post-commit":
        assert spawned["returncode"] == 0, "post-commit failure is normalized to native success"
        assert _head_state(git_repo, git_run)[0] != before_head[0]
        assert git_run(git_repo, "rev-parse", "HEAD^").stdout.strip() == before_head[0]
    else:
        assert spawned["returncode"] > 0, "signer termination must reach native nonzero completion"
        assert _head_state(git_repo, git_run) == before_head
    assert active_candidate_exists, "Rhizome deleted or moved the candidate while its writer was active"
    assert active_index_identity == before_index_identity
    assert active_index_bytes == before_index
    if active_execute_finished:
        assert isinstance(outcome[0], GitOutcomeUncertain), (
            "Rhizome reported a settled result while the foreground child was active"
        )

    assert child_status.read_text() == "0\n", "the child's late candidate staging did not survive"
    assert len(outcome) == 1
    if isinstance(outcome[0], GitOutcomeUncertain):
        assert candidate is not None
        assert os.path.isfile(candidate)
        assert index.with_name("index.lock").is_file()
        assert index.read_bytes() == before_index
        context = Path(candidate).with_name("context.txt").read_text()
        assert f"git-pgid: {spawned['process'].pid}" in context
        assert "phase: native-group-still-present" in context
        assert subprocess.run(
            ["git", "show", ":late-hook.txt"],
            cwd=git_repo,
            env={**os.environ, "GIT_INDEX_FILE": candidate},
            check=True,
            capture_output=True,
            text=True,
        ).stdout == "late hook content\n"
    else:
        if launcher == "post-commit":
            assert isinstance(outcome[0], str), outcome[0]
        else:
            assert isinstance(outcome[0], GitError) and "sign" in str(outcome[0])
        assert git_run(git_repo, "show", ":late-hook.txt").stdout == "late hook content\n"
        assert not index.with_name("index.lock").exists()
        assert not list(index.parent.glob("rhizome-commit-*"))


def test_duplicate_native_header_beyond_retained_output_is_ambiguous(
    git_repo, git_run, monkeypatch
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    git_run(git_repo, "add", path.name)

    index = git_repo / ".git" / "index"
    before_head = _head_state(git_repo, git_run)
    before_index = index.read_bytes()
    before_entries = git_run(git_repo, "ls-files", "--stage", "-z").stdout
    real_git = shutil.which("git")
    assert real_git is not None
    wrapper_directory = git_repo.parent / "git-wrapper"
    wrapper_directory.mkdir()
    wrapper = wrapper_directory / "git"
    candidate_at_return = git_repo.parent / "candidate-at-native-return"
    wrapper.write_text(
        "#!/bin/sh\n"
        "is_commit=\n"
        "for argument do\n"
        "  test \"$argument\" = commit && is_commit=yes\n"
        "done\n"
        f"{shlex.quote(real_git)} \"$@\"\n"
        "status=$?\n"
        "if test \"$status\" -eq 0 && test \"$is_commit\" = yes; then\n"
        f"  ln \"$GIT_INDEX_FILE\" {shlex.quote(str(candidate_at_return))} || exit 97\n"
        "  dd if=/dev/zero bs=70000 count=1 2>/dev/null | tr '\\000' x\n"
        f"  printf '\\n[fixture {before_head[0]}] duplicate native-shaped header\\n'\n"
        "fi\n"
        "exit \"$status\"\n"
    )
    wrapper.chmod(0o700)
    repo = Repository(git_repo)
    monkeypatch.setenv("PATH", f"{wrapper_directory}{os.pathsep}{os.environ['PATH']}")

    popen = subprocess.Popen
    spawned: dict[str, object] = {}

    def observe_native_commit(command, *args, **kwargs):
        process = popen(command, *args, **kwargs)
        if isinstance(command, list) and "commit" in command:
            artifacts = list(index.parent.glob("rhizome-commit-*"))
            assert len(artifacts) == 1
            lock = index.with_name("index.lock")
            spawned.update(
                process=process,
                artifact=artifacts[0],
                artifact_identity=(artifacts[0].stat().st_dev, artifacts[0].stat().st_ino),
                lock_identity=(lock.stat().st_dev, lock.stat().st_ino),
            )
        return process

    monkeypatch.setattr(subprocess, "Popen", observe_native_commit)

    with pytest.raises(GitOutcomeUncertain, match="unambiguous created commit"):
        repo.execute(Action("commit", message="Reject hidden duplicate identity"))

    after_head = _head_state(git_repo, git_run)
    assert after_head[0] != before_head[0]
    assert after_head[1] == before_head[1]
    assert git_run(git_repo, "rev-parse", "HEAD^").stdout.strip() == before_head[0]
    assert git_run(git_repo, "show", "HEAD:example.txt").stdout == "reviewed\n"
    assert git_run(git_repo, "diff", "--cached", "--quiet", "--exit-code").returncode == 0
    assert path.read_text() == "reviewed\n"
    assert before_index
    assert before_entries

    process = spawned["process"]
    assert process.returncode == 0
    artifact = spawned["artifact"]
    assert artifact.is_dir()
    assert (artifact.stat().st_dev, artifact.stat().st_ino) == spawned["artifact_identity"]
    assert candidate_at_return.is_file()
    assert os.path.samefile(candidate_at_return, index)
    assert candidate_at_return.read_bytes() == index.read_bytes()
    assert not (artifact / "index").exists()
    assert not index.with_name("index.lock").exists()
    context = (artifact / "context.txt").read_text()
    assert f"git-pid: {process.pid}" in context
    assert "native-returncode: 0" in context
    assert "native-created-oid: (unverified)" in context


def test_native_nonzero_after_ref_move_records_observed_state_and_publication(
    git_repo, git_run
):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed unique tree content\n")
    git_run(git_repo, "add", path.name)

    index = git_repo / ".git" / "index"
    before_head = _head_state(git_repo, git_run)
    before_index = index.read_bytes()
    before_entries = git_run(git_repo, "ls-files", "--stage", "-z").stdout
    moved_tree = git_repo.parent / "temporarily-moved-tree-object"
    tree_oid_file = git_repo.parent / "moved-tree-oid"
    tree_source_file = git_repo.parent / "moved-tree-source"
    hook = git_repo / ".git" / "hooks" / "post-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "tree=$(git rev-parse 'HEAD^{tree}')\n"
        "objects=$(git rev-parse --git-path objects)\n"
        "prefix=$(printf '%s' \"$tree\" | cut -c1-2)\n"
        "suffix=$(printf '%s' \"$tree\" | cut -c3-)\n"
        "source=$objects/$prefix/$suffix\n"
        f"printf '%s' \"$tree\" > {shlex.quote(str(tree_oid_file))}\n"
        f"printf '%s' \"$source\" > {shlex.quote(str(tree_source_file))}\n"
        f"mv \"$source\" {shlex.quote(str(moved_tree))}\n"
    )
    hook.chmod(0o700)

    try:
        with pytest.raises(GitOutcomeUncertain) as failed:
            Repository(git_repo).execute(
                Action("commit", message="Fail native summary after ref movement")
            )
    finally:
        if moved_tree.exists():
            source = tree_source_file.read_text()
            source_path = git_repo / source if not os.path.isabs(source) else source
            os.replace(moved_tree, source_path)

    assert "reporting a commit failure" in str(failed.value).lower()
    assert tree_oid_file.read_text()
    assert tree_source_file.read_text()
    assert not moved_tree.exists()
    after_head = _head_state(git_repo, git_run)
    assert after_head[0] != before_head[0]
    assert after_head[1] == before_head[1]
    assert git_run(git_repo, "rev-parse", "HEAD^").stdout.strip() == before_head[0]
    assert git_run(git_repo, "show", "HEAD:example.txt").stdout == "reviewed unique tree content\n"
    assert git_run(git_repo, "diff", "--cached", "--quiet", "--exit-code").returncode == 0
    assert path.read_text() == "reviewed unique tree content\n"
    assert before_index
    assert before_entries

    assert not index.with_name("index.lock").exists()
    artifacts = list(index.parent.glob("rhizome-commit-*"))
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert not (artifact / "index").exists()
    context = (artifact / "context.txt").read_text()
    assert "native-returncode: " in context
    assert "native-returncode: 0" not in context
    assert "native-created-oid: (unverified)" in context
    assert f"native-created-oid: {after_head[0]}" not in context
    assert f"observed HEAD: {after_head[0]}" in context
    assert f"observed symbolic HEAD: {after_head[1]}" in context
    assert "phase: index-published" in context
