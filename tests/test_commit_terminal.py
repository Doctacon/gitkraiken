"""Real controlling-terminal coverage for the native Git write runners."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import select
import shlex
import shutil
import signal
import subprocess
import termios
import time
from pathlib import Path

import pytest

from rhizome.git import Repository
from rhizome.models import Action

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX job control")


def _process_group_states(process_group: int) -> list[str]:
    """Return ps state strings for a disposable fixture process group."""
    result = subprocess.run(
        ["ps", "-axo", "pgid=,stat="],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    states = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].isdigit() and int(fields[0]) == process_group:
            states.append(fields[1])
    return states


@pytest.mark.parametrize("prompt_stream", ("terminal", "stderr", "ssh-signing"))
@pytest.mark.parametrize("runner", ("reviewed-commit", "integration"))
def test_public_commit_hook_can_read_the_controlling_terminal(
    git_repo,
    git_run,
    tmp_path,
    monkeypatch,
    prompt_stream,
    runner,
):
    """A public commit should retain the foreground-terminal access native Git has."""
    # Fixed identities and dates make the native and Repository commits exact
    # commit-object twins while all Git configuration remains fixture-local.
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2025-01-02T03:04:05+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2025-01-02T03:04:05+00:00")
    native_repo = tmp_path / "native-twin"
    native_repo.mkdir()
    git_run(native_repo, "init", "-b", "main")
    for repository in (git_repo, native_repo):
        git_run(repository, "config", "user.name", "Test User")
        git_run(repository, "config", "user.email", "test@example.invalid")
        git_run(repository, "config", "commit.gpgsign", "false")
        path = repository / "example.txt"
        path.write_text("base\n")
        git_run(repository, "add", path.name)
        git_run(repository, "commit", "-m", "base")
        path.write_text("reviewed\n")
        git_run(repository, "add", path.name)

    native_entered = tmp_path / "native-hook-entered"
    native_answer = tmp_path / "native-hook-answer"
    product_entered = tmp_path / "product-hook-entered"
    product_answer = tmp_path / "product-hook-answer"

    def install_hook(repository: Path, entered: Path, answer: Path) -> None:
        hook = repository / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            f": > {shlex.quote(str(entered))}\n"
            "printf 'RHIZOME_TTY_PROMPT\\n' "
            + ("> /dev/tty\n" if prompt_stream == "terminal" else ">&2\n")
            + "IFS= read -r reply < /dev/tty\n"
            'test "$reply" = approve\n'
            f"printf '%s\\n' \"$reply\" > {shlex.quote(str(answer))}\n"
        )
        hook.chmod(0o700)

    approval = b"approve\n"
    prompt = b"RHIZOME_TTY_PROMPT"
    if prompt_stream == "ssh-signing":
        # A fresh disposable encrypted key, not a user key/agent/device. Both
        # native Git and Repository invoke the real installed SSH signer.
        signer = shutil.which("ssh-keygen")
        assert signer is not None, "the SSH-signing integration fixture requires ssh-keygen"
        monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
        monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "never")
        key = tmp_path / "fixture-signing-key"
        subprocess.run(
            [signer, "-q", "-t", "ed25519", "-N", "fixture-passphrase", "-f", str(key)],
            check=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
        allowed = tmp_path / "fixture-allowed-signers"
        allowed.write_text("test@example.invalid " + key.with_suffix(".pub").read_text())
        for repository in (native_repo, git_repo):
            git_run(repository, "config", "gpg.format", "ssh")
            git_run(repository, "config", "gpg.ssh.program", signer)
            git_run(repository, "config", "user.signingkey", str(key))
            git_run(repository, "config", "gpg.ssh.allowedSignersFile", str(allowed))
            git_run(repository, "config", "commit.gpgsign", "true")
        approval = b"fixture-passphrase\n"
        prompt = b"Enter passphrase"
    else:
        install_hook(native_repo, native_entered, native_answer)
        install_hook(git_repo, product_entered, product_answer)

    observation_path = tmp_path / "terminal-observation.json"
    product_pid_path = tmp_path / "product-git-pid"
    product_outcome_path = tmp_path / "product-outcome.json"
    abort_path = tmp_path / "abort-terminal-fixture"
    cleanup_input_path = tmp_path / "release-terminal-input"
    master_fd, slave_fd = pty.openpty()
    controller_pid = os.fork()
    if controller_pid == 0:
        # This session and its newly allocated PTY are entirely test-owned. No
        # operator terminal or enclosing tmux foreground group is ever changed.
        try:
            os.close(master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            for descriptor in (0, 1, 2):
                os.dup2(slave_fd, descriptor)
            if slave_fd > 2:
                os.close(slave_fd)
            controller_group = os.getpgrp()
            os.tcsetpgrp(0, controller_group)

            native = subprocess.Popen(
                ["git", "commit", "-m", "terminal hook"],
                cwd=native_repo,
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
            )
            native_abort_requested = False
            while native.poll() is None:
                native_abort_requested = native_abort_requested or abort_path.exists()
                time.sleep(0.01)
            observation: dict[str, object] = {"native_returncode": native.returncode}
            if native.returncode != 0 or native_abort_requested:
                observation["fixture_error"] = (
                    "parent timeout requested native-twin cleanup"
                    if native_abort_requested
                    else "native Git twin failed"
                )
                observation_path.write_text(json.dumps(observation))
                os._exit(2)

            worker_pid = os.fork()
            if worker_pid == 0:
                # Restore normal job-control dispositions before Repository
                # launches Git; only the controller ignores SIGTTOU while it
                # moves its private terminal's foreground group during cleanup.
                signal.signal(signal.SIGTTOU, signal.SIG_DFL)
                real_popen = subprocess.Popen
                spawned: list[subprocess.Popen[bytes]] = []

                def observe_commit(command, *args, **kwargs):
                    process = real_popen(command, *args, **kwargs)
                    if "commit" in command and kwargs.get("process_group") == 0:
                        spawned.append(process)
                        product_pid_path.write_text(f"{process.pid}\n")
                    return process

                subprocess.Popen = observe_commit
                try:
                    repository = Repository(git_repo)
                    if runner == "integration":
                        # Merge Continue invokes this same native commit/hook/
                        # signing path; observe the new integration runner's
                        # actual terminal lease, not only the shared helper.
                        completed = repository._run_integration(
                            ["commit", "-m", "terminal hook"], operation="integration terminal test"
                        )
                        if completed.returncode:
                            raise AssertionError(completed.stderr.decode("utf-8", "replace"))
                        result = completed.stdout.decode("utf-8", "replace")
                    else:
                        result = repository.execute(Action("commit", message="terminal hook"))
                    product_outcome_path.write_text(
                        json.dumps(
                            {
                                "kind": "success",
                                "result": result,
                                "native_returncode": spawned[0].returncode,
                            }
                        )
                    )
                    os._exit(0)
                except BaseException as error:
                    product_outcome_path.write_text(
                        json.dumps(
                            {
                                "kind": "error",
                                "type": type(error).__name__,
                                "message": str(error),
                                "native_returncode": (spawned[0].returncode if spawned else None),
                            }
                        )
                    )
                    os._exit(1)

            # SIGTTIN stops the grandchild Git process group rather than this
            # direct worker. Record explicit ps evidence under a bounded poll.
            signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            git_pid = None
            stopped_states: list[str] = []
            worker_status = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                waited, status = os.waitpid(worker_pid, os.WNOHANG)
                if waited == worker_pid:
                    worker_status = status
                    break
                if git_pid is None and product_pid_path.exists():
                    git_pid = int(product_pid_path.read_text())
                if git_pid is not None and (
                    product_entered.exists() or prompt_stream == "ssh-signing"
                ):
                    states = _process_group_states(git_pid)
                    if states and any("T" in state for state in states):
                        stopped_states = states
                        break
                # A parent-side timeout asks this session-owning controller to
                # perform the same foreground handoff cleanup. Wait until the
                # real Git PID is known so no worker or process group is orphaned.
                if abort_path.exists() and git_pid is not None:
                    break
                time.sleep(0.01)

            missing_pid_at_deadline = worker_status is None and git_pid is None
            while worker_status is None and git_pid is None:
                # A launch-boundary fixture failure must still reveal either a
                # completed worker or the child group the controller must release.
                waited, status = os.waitpid(worker_pid, os.WNOHANG)
                if waited == worker_pid:
                    worker_status = status
                    break
                if product_pid_path.exists():
                    git_pid = int(product_pid_path.read_text())
                    break
                time.sleep(0.01)

            handoff_performed = False
            completion_timed_out = False
            if worker_status is None and git_pid is not None:
                # A stopped normal hook is never killed or mistaken for
                # quiescence. Give its group the fixture PTY foreground, resume
                # it, and let the already queued answer release it naturally.
                os.tcsetpgrp(0, git_pid)
                os.killpg(git_pid, signal.SIGCONT)
                handoff_performed = True
                cleanup_input_path.touch()
                completion_deadline = time.monotonic() + 15
                while time.monotonic() < completion_deadline:
                    waited, status = os.waitpid(worker_pid, os.WNOHANG)
                    if waited == worker_pid:
                        worker_status = status
                        break
                    time.sleep(0.01)
                if worker_status is None:
                    completion_timed_out = True
                    # Foreground ownership and queued input have already released
                    # the only intentional gate. Observe the child rather than
                    # killing it or returning while it is still pending.
                    _, worker_status = os.waitpid(worker_pid, 0)

            restored_by_product = os.tcgetpgrp(0) == controller_group
            os.tcsetpgrp(0, controller_group)
            restored_foreground = os.tcgetpgrp(0) == controller_group
            remaining_states = _process_group_states(git_pid) if git_pid is not None else []
            observation.update(
                {
                    "git_pid": git_pid,
                    "hook_entered_before_handoff": product_entered.exists(),
                    "stopped_states": stopped_states,
                    "handoff_performed": handoff_performed,
                    "worker_status": worker_status,
                    "restored_by_product": restored_by_product,
                    "restored_foreground": restored_foreground,
                    "remaining_group_states": remaining_states,
                }
            )
            if product_outcome_path.exists():
                observation["product_outcome"] = json.loads(product_outcome_path.read_text())
            if missing_pid_at_deadline:
                observation["fixture_error"] = "product launch exceeded bounded observation"
            elif completion_timed_out:
                observation["fixture_error"] = "product completion exceeded bounded observation"
            observation_path.write_text(json.dumps(observation))
            os._exit(0)
        except BaseException as error:
            observation_path.write_text(
                json.dumps({"fixture_error": f"{type(error).__name__}: {error}"})
            )
            os._exit(4)

    os.close(slave_fd)
    transcript = bytearray()
    approvals_sent = 0
    cleanup_input_sent = False
    controller_status = None
    controller_timed_out = False

    def service_terminal() -> None:
        nonlocal approvals_sent, cleanup_input_sent
        if cleanup_input_path.exists() and not cleanup_input_sent and approvals_sent < 2:
            os.write(master_fd, approval)
            cleanup_input_sent = True
        readable, _, _ = select.select([master_fd], [], [], 0.05)
        if not readable:
            return
        try:
            chunk = os.read(master_fd, 4096)
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            chunk = b""
        transcript.extend(chunk)
        prompts = transcript.count(prompt)
        while approvals_sent < prompts:
            os.write(master_fd, approval)
            approvals_sent += 1

    deadline = time.monotonic() + 35
    try:
        while time.monotonic() < deadline:
            service_terminal()
            waited, status = os.waitpid(controller_pid, os.WNOHANG)
            if waited == controller_pid:
                controller_status = status
                break
        if controller_status is None:
            controller_timed_out = True
            # Keep the private PTY alive, queue release input, and ask the
            # session-owning controller to foreground/SIGCONT a pending product
            # group. Do not report the timeout until that controller and every
            # child it supervises have completed and been observed.
            abort_path.touch()
            os.write(master_fd, approval * 2)
            while controller_status is None:
                service_terminal()
                waited, status = os.waitpid(controller_pid, os.WNOHANG)
                if waited == controller_pid:
                    controller_status = status
    finally:
        os.close(master_fd)

    assert not controller_timed_out, "PTY fixture controller exceeded its bounded observation"
    assert controller_status is not None
    assert os.waitstatus_to_exitcode(controller_status) == 0
    observation = json.loads(observation_path.read_text())
    assert "fixture_error" not in observation, observation
    assert approvals_sent == 2, transcript.decode("utf-8", "replace")
    assert observation["native_returncode"] == 0
    if prompt_stream == "ssh-signing":
        for repository in (native_repo, git_repo):
            assert (
                "gpgsig -----BEGIN SSH SIGNATURE-----"
                in git_run(repository, "cat-file", "commit", "HEAD").stdout
            )
            git_run(repository, "verify-commit", "HEAD")
    else:
        assert native_entered.is_file() and native_answer.read_text() == "approve\n"
        assert observation["hook_entered_before_handoff"] is True
        assert product_answer.read_text() == "approve\n"
    assert observation["product_outcome"]["kind"] == "success", observation
    assert observation["product_outcome"]["native_returncode"] == 0
    assert observation["restored_by_product"] is True
    assert observation["restored_foreground"] is True
    assert observation["remaining_group_states"] == []

    # Both paths accepted the same hook input and must leave the same commit,
    # logical index, staged content, and worktree content.
    assert (
        git_run(git_repo, "rev-parse", "HEAD").stdout
        == git_run(native_repo, "rev-parse", "HEAD").stdout
    )
    assert (
        git_run(git_repo, "ls-files", "--stage", "-z").stdout
        == git_run(native_repo, "ls-files", "--stage", "-z").stdout
    )
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert git_run(native_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert (git_repo / "example.txt").read_text() == "reviewed\n"
    assert (native_repo / "example.txt").read_text() == "reviewed\n"
    assert git_run(git_repo, "status", "--porcelain").stdout == ""
    assert git_run(native_repo, "status", "--porcelain").stdout == ""

    assert observation["stopped_states"] == [], (
        "Repository's Git process group lost foreground access to its controlling PTY "
        f"and was stopped while the native Git twin was not: {observation}"
    )
