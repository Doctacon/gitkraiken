"""Synchronous, subprocess-backed Git repository engine.

This is the only module in Rhizome which invokes Git.  Arguments are always
passed as an argv sequence; callers cannot supply command-line fragments.
"""

from __future__ import annotations

import codecs
import errno
import hashlib
import heapq
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import termios
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from .models import (
    Action,
    BlameLine,
    CapturedRef,
    Commit,
    CommitDetail,
    ComparisonIntent,
    ComparisonResult,
    FileChange,
    FileHistoryEntry,
    HistoryLine,
    InvestigationScope,
    Ref,
    Remote,
    ScopeState,
    SearchQuery,
    Snapshot,
    Stash,
    Worktree,
)
from .pull_requests import (
    HostedRepository,
    PullRequestError,
    PullRequestListing,
    PullRequestPlan,
    hosted_repository,
)
from .pull_requests import create_pull_request as _create_hosted_pull_request
from .pull_requests import list_open_pull_requests as _list_open_pull_requests

_NETWORK_TIMEOUT = 60.0
_LOCAL_TIMEOUT = 30.0
_MAX_DIFF_BYTES = 2 * 1024 * 1024
_MAX_ERROR_BYTES = 64 * 1024
_MAX_HISTORY = 5_000
_MAX_PATCH_BYTES = 16 * 1024 * 1024
_MAX_COMMIT_TEMPLATE_BYTES = 1024 * 1024
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_REPOSITORY_ENVIRONMENT = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_SUPER_PREFIX",
    "GIT_WORK_TREE",
}
# Start once per maximal scheme-shaped token, not once per character in a
# long diagnostic. Preserve leading digits/punctuation rather than overlooking
# a credential-bearing URL immediately after them.
_URL_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])(?P<leading>[0-9+.-]*)"
    r"(?P<url>[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+)"
)
_SCP_URL_RE = re.compile(r"(?<![^@\s/:])(?P<url>[^@\s/:]+@[^@\s/:]+:[^\s'\"<>]+)")


@dataclass(frozen=True)
class _FetchRefspec:
    raw: str
    source: str
    destination: str = ""


@dataclass(frozen=True)
class _FetchRemoteScope:
    remote: str
    refspecs: tuple[_FetchRefspec, ...]
    no_tags: bool = False


class GitError(RuntimeError):
    """A safe, human-readable Git failure."""

    def __init__(self, message: str) -> None:
        super().__init__(_redact(message))


class GitOutcomeUncertain(GitError):
    """A mutation may have completed; inspect current state before another attempt."""


@dataclass(frozen=True)
class _CommitEncoding:
    configured: str
    codec: codecs.CodecInfo
    label: str


def _redact_url(url: str) -> str:
    """Remove URL userinfo, query values, and fragments used for credentials."""
    scp = re.fullmatch(r"(?:(?P<user>[^@\s/:]+)@)?(?P<host>[^@\s/:]+):(?P<path>.+)", url)
    if scp and "://" not in url:
        path = re.split(r"[?#]", scp.group("path"), maxsplit=1)[0]
        # Keep the conventional, non-secret SSH account for a useful display;
        # arbitrary usernames may themselves be access tokens.
        user = "git@" if scp.group("user") == "git" else ""
        return f"{user}{scp.group('host')}:{path}"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<redacted-url>" if "://" in url else url
    if not parts.scheme or not parts.netloc:
        return url
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        return "<redacted-url>"
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _redact(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        # Keep common punctuation which the broad URL match may consume.
        value = match.group("url")
        trimmed = value.rstrip(".,;)]}")
        suffix = value[len(trimmed) :]
        return match.groupdict().get("leading", "") + _redact_url(trimmed) + suffix

    return _SCP_URL_RE.sub(replace, _URL_RE.sub(replace, text))


def _decode(value: bytes) -> str:
    return value.decode("utf-8", "surrogateescape")


def _commit_date(value: str) -> str:
    # Git's strict ISO output changed its spelling of UTC across supported
    # versions. Keep the public date stable without losing nonzero offsets.
    return value.removesuffix("+00:00") + "Z" if value.endswith("+00:00") else value


def _noninteractive_ssh_command(command: str) -> str:
    """Force BatchMode for a conventional GIT_SSH_COMMAND value.

    Git interprets this environment value through a shell. Rebuilding it from
    tokens deliberately drops shell operators and removes contradictory
    BatchMode options; custom wrappers are still subject to the network timeout.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    if not tokens:
        tokens = ["ssh"]
    cleaned = [tokens[0]]
    index = 1
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if lowered.startswith("-obatchmode="):
            index += 1
            continue
        if lowered == "-o" and index + 1 < len(tokens):
            if tokens[index + 1].lower().startswith("batchmode="):
                index += 2
                continue
        cleaned.append(token)
        index += 1
    cleaned.insert(1, "-oBatchMode=yes")
    return shlex.join(cleaned)


def _has_ssh_environment_override() -> bool:
    return bool(os.environ.get("GIT_SSH_COMMAND") or os.environ.get("GIT_SSH"))


def _git_environment(configured_ssh_command: str = "", *, network: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    # A caller's shell may itself be inside another repository. These variables
    # must never override the explicit -C/cwd selected by Repository.
    for name in _REPOSITORY_ENVIRONMENT:
        env.pop(name, None)
    env.pop("GIT_CONFIG_PARAMETERS", None)
    env.pop("GIT_CONFIG_COUNT", None)
    for name in tuple(env):
        if name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            env.pop(name, None)

    # OpenSSH still reads the user's normal config and uses their agent, but it
    # cannot open a password/passphrase prompt. Custom GIT_SSH_COMMAND wrappers
    # need to accept OpenSSH-style -o options; the process timeout is the final
    # guard for wrappers which ignore them.
    ssh_command = env.get("GIT_SSH_COMMAND")
    if ssh_command:
        env["GIT_SSH_COMMAND"] = _noninteractive_ssh_command(ssh_command)
    elif custom_ssh := env.get("GIT_SSH"):
        env["GIT_SSH_COMMAND"] = _noninteractive_ssh_command(shlex.quote(custom_ssh))
        env.pop("GIT_SSH", None)
    elif network:
        # Only network calls need an override. This preserves core.sshCommand
        # for normal Git behavior while adding the mandatory BatchMode option.
        env["GIT_SSH_COMMAND"] = _noninteractive_ssh_command(configured_ssh_command or "ssh")

    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_EDITOR": "true",
            "GIT_SEQUENCE_EDITOR": "true",
            # Environment variables override inherited/core askPass helpers.
            # /usr/bin/false is present on the supported Unix-like platforms
            # and fails without opening any terminal or GUI prompt.
            "GIT_ASKPASS": "/usr/bin/false",
            "SSH_ASKPASS": "/usr/bin/false",
            "SSH_ASKPASS_REQUIRE": "never",
            "LC_ALL": "C",
        }
    )
    return env


def _raise_process_error(
    result: subprocess.CompletedProcess[bytes],
    *,
    timeout: float | None,
    ok_codes: tuple[int, ...] | None,
    operation: str,
) -> None:
    if ok_codes is None or result.returncode in ok_codes:
        return
    raw_detail = result.stderr or result.stdout
    clipped = raw_detail[:_MAX_ERROR_BYTES]
    detail = _decode(clipped).strip()
    if len(raw_detail) > _MAX_ERROR_BYTES:
        detail += "\n[Git error output truncated by Rhizome]"
    if not detail:
        detail = f"Git exited with status {result.returncode}."
    if timeout == _NETWORK_TIMEOUT:
        detail += (
            "\nGit was run noninteractively. Configure a credential helper or SSH agent "
            "and try again."
        )
    raise GitError(f"Git {operation} failed: {detail}")


def _run_process(
    argv: list[str],
    *,
    cwd: Path | None,
    timeout: float | None = _LOCAL_TIMEOUT,
    ok_codes: tuple[int, ...] | None = (0,),
    operation: str = "command",
    input_bytes: bytes | None = None,
    configured_ssh_command: str = "",
    index_file: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        env = _git_environment(configured_ssh_command, network=timeout == _NETWORK_TIMEOUT)
        if index_file is not None:
            # A private per-call override after inherited repository selectors are
            # removed. Never change the process environment or accept arbitrary env.
            env["GIT_INDEX_FILE"] = os.fspath(index_file)
        run_input: dict[str, object]
        if input_bytes is None:
            run_input = {"stdin": subprocess.DEVNULL}
        else:
            run_input = {"input": input_bytes}
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
            **run_input,
        )
    except FileNotFoundError as error:
        raise GitError("Git is not installed or is not available on PATH.") from error
    except subprocess.TimeoutExpired as error:
        raise GitError(
            f"Git {operation} timed out. Check the network, credentials, and remote host."
        ) from error
    except OSError as error:
        raise GitError(f"Could not run Git {operation}: {error}") from error

    _raise_process_error(result, timeout=timeout, ok_codes=ok_codes, operation=operation)
    return result


def _global_ssh_command() -> str:
    """Read only system/global core.sshCommand for clone network policy."""
    if _has_ssh_environment_override():
        return ""
    configured = ""
    for scope in ("--system", "--global"):
        result = _run_process(
            ["git", "config", scope, "--includes", "--get", "core.sshCommand"],
            cwd=None,
            ok_codes=(0, 1),
            operation="read SSH command configuration",
        )
        if result.returncode == 0:
            configured = _decode(result.stdout).rstrip("\n")
    return configured


def _run_spooled_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float = _LOCAL_TIMEOUT,
    ok_codes: tuple[int, ...] = (0,),
    operation: str = "diff",
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run Git with stdout/stderr spooled to disk beyond small memory limits."""
    try:
        with (
            tempfile.SpooledTemporaryFile(max_size=_MAX_DIFF_BYTES) as stdout_file,
            tempfile.SpooledTemporaryFile(max_size=_MAX_ERROR_BYTES) as stderr_file,
        ):
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=_git_environment(),
                stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
            )
            try:
                process.communicate(input=input_bytes, timeout=timeout)
                returncode = process.returncode
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.wait()
                raise GitError(
                    f"Git {operation} timed out. Check the network, credentials, and remote host."
                ) from error
            stdout_file.seek(0)
            stderr_file.seek(0)
            # One extra byte records whether the user-facing diff needs a
            # truncation marker without reading the complete spool into RAM.
            stdout = stdout_file.read(_MAX_DIFF_BYTES + 1)
            stderr = stderr_file.read(_MAX_ERROR_BYTES + 1)
    except FileNotFoundError as error:
        raise GitError("Git is not installed or is not available on PATH.") from error
    except OSError as error:
        raise GitError(f"Could not run Git {operation}: {error}") from error

    result = subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    _raise_process_error(result, timeout=timeout, ok_codes=ok_codes, operation=operation)
    return result


def _safe_value(value: str, label: str, *, required: bool = True) -> str:
    if required and not value:
        raise GitError(f"{label} is required.")
    if _CONTROL_RE.search(value):
        raise GitError(f"{label} contains unsupported control characters.")
    if value.startswith("-"):
        raise GitError(f"{label} must not begin with '-'.")
    return value


def _friendly_output(result: subprocess.CompletedProcess[bytes], fallback: str) -> str:
    output = _decode(result.stdout).strip() or _decode(result.stderr).strip()
    return _redact(output) if output else fallback


_COMMIT_TERMINAL_LOCK = threading.Lock()
# A child-side handoff must happen before Git can run a terminal-reading hook.
# A fresh isolated interpreter avoids unsafe preexec_fn calls in the threaded UI.
# exec preserves the PID/process group that the index transaction supervises.
_COMMIT_TERMINAL_EXEC = """
import os, signal, sys
fd = int(sys.argv[1])
previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
try:
    os.tcsetpgrp(fd, os.getpgrp())
finally:
    signal.signal(signal.SIGTTOU, previous)
os.close(fd)
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
"""


@contextmanager
def _commit_terminal(argv: list[str]):
    """Lease the caller's foreground terminal, or keep headless Git headless.

    The UI must suspend its input/rendering around this operation. Serialize
    in-process terminal leases, never steal a terminal from a background caller,
    and restore ownership/modes even on a failed or uncertain native outcome.
    """
    with _COMMIT_TERMINAL_LOCK:
        try:
            terminal = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError as error:
            if error.errno not in (errno.ENXIO, errno.ENODEV, errno.ENOENT):
                raise GitError(f"Cannot open the commit terminal: {error}") from error
            yield argv, ()
            return
        try:
            foreground = os.tcgetpgrp(terminal)
            if foreground != os.getpgrp():
                raise GitError("Bring Rhizome to the foreground before committing.")
            attributes = termios.tcgetattr(terminal)
            try:
                yield (
                    [sys.executable, "-I", "-S", "-c", _COMMIT_TERMINAL_EXEC, str(terminal), *argv],
                    (terminal,),
                )
            finally:
                # tcsetpgrp/tcsetattr must work while Git still owns the foreground.
                # Blocking SIGTTOU is thread-local; don't change the UI's handlers.
                mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
                try:
                    os.tcsetpgrp(terminal, foreground)
                    termios.tcsetattr(terminal, termios.TCSANOW, attributes)
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        finally:
            os.close(terminal)


class _CommitIndexTransaction:
    """Own a real index lock while native Git and hooks use a private candidate.

    A commit can advance refs before the index is published. Unsettled transactions
    therefore retain their artifacts and owned lock, never silently roll back refs
    or discard a candidate which a surviving interrupted hook could still use.
    """

    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        index = repository._git_path("index")
        self.directory = index.parent.resolve()
        self.index = self.directory / index.name
        self.lock_name = index.name + ".lock"
        self.directory_fd: int | None = None
        self.lock_fd: int | None = None
        self.lock_owner: tuple[int, int] | None = None
        self.temporary: Path | None = None
        self.temporary_fd: int | None = None
        self.temporary_owner: tuple[int, int] | None = None
        self.context_fd: int | None = None
        self.context_owner: tuple[int, int] | None = None
        self.started = False
        self.git_pid: int | None = None
        self.git_pgid: int | None = None
        self.settled = False
        self.published = False
        self.created_oid = ""

    @staticmethod
    def _identity(info: os.stat_result) -> tuple[int, int]:
        return info.st_dev, info.st_ino

    def _verify_temporary(self) -> None:
        if self.temporary is None:
            raise GitError("The commit index transaction is not open.")
        info = self.temporary.lstat()
        if (
            self.temporary_fd is None
            or not stat.S_ISDIR(info.st_mode)
            or self._identity(info) != self.temporary_owner
            or self._identity(os.fstat(self.temporary_fd)) != self.temporary_owner
        ):
            raise GitError("Commit temporary directory ownership changed.")

    @property
    def candidate(self) -> Path:
        self._verify_temporary()
        assert self.temporary is not None
        return self.temporary / "index"

    def _owns_lock(self) -> bool:
        if self.lock_owner is None or self.directory_fd is None:
            return False
        try:
            return (
                self._identity(
                    os.stat(
                        self.lock_name,
                        dir_fd=self.directory_fd,
                        follow_symlinks=False,
                    )
                )
                == self.lock_owner
            )
        except FileNotFoundError:
            return False

    def verify(self) -> None:
        self._verify_temporary()
        current = self.repository._git_path("index")
        if (
            self.directory_fd is None
            or current.name != self.index.name
            or current.parent.resolve() != self.directory
            or self._identity(self.directory.stat()) != self._identity(os.fstat(self.directory_fd))
            or not self._owns_lock()
        ):
            raise GitError("Repository or index lock ownership changed during commit.")

    def __enter__(self) -> _CommitIndexTransaction:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            self.directory_fd = os.open(
                self.directory,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            self.lock_fd = os.open(
                self.lock_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
                dir_fd=self.directory_fd,
            )
            self.lock_owner = self._identity(os.fstat(self.lock_fd))
            self.temporary = Path(tempfile.mkdtemp(prefix="rhizome-commit-", dir=self.directory))
            self.temporary_fd = os.open(
                self.temporary,
                os.O_RDONLY | nofollow | getattr(os, "O_DIRECTORY", 0),
            )
            self.temporary_owner = self._identity(os.fstat(self.temporary_fd))
            source_fd = os.open(
                self.index.name,
                os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0),
                dir_fd=self.directory_fd,
            )
            with os.fdopen(source_fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise GitError("The Git index is not a regular file; no commit attempted.")
                with self.candidate.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                self.candidate.chmod(stat.S_IMODE(info.st_mode))
            self.verify()
            return self
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise

    def record(self, head: str, symbolic: str) -> None:
        """Leave bounded, source/message-free context for interrupted transactions."""
        context = (
            "Rhizome ordinary commit index transaction\n"
            f"pid: {os.getpid()}\noriginal HEAD: {head or '(unborn)'}\n"
            f"symbolic HEAD: {symbolic or '(detached)'}\n"
            f"real index: {os.fspath(self.index)!r}\n"
            f"candidate: {os.fspath(self.candidate)!r}\n"
            "Do not remove a lock or retry until Git/hooks have stopped and HEAD,\n"
            "the real index, and this candidate have been inspected. No automatic replay.\n"
        ).encode()
        self.context_fd = os.open(
            self.candidate.parent / "context.txt",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        self.context_owner = self._identity(os.fstat(self.context_fd))
        with os.fdopen(os.dup(self.context_fd), "wb") as stream:
            stream.write(context)
            stream.flush()
            os.fsync(stream.fileno())
        with os.fdopen(os.dup(self.lock_fd), "wb") as stream:
            stream.write(context)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_fd = os.open(self.candidate.parent, os.O_RDONLY)
        try:
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)
        os.fsync(self.directory_fd)
        self.verify()

    def run(
        self,
        args: list[str],
        *,
        input_bytes: bytes,
    ) -> tuple[subprocess.CompletedProcess[bytes], str]:
        """Wait for native Git without a deadline or an exceptional-path kill.

        Regular temporary files avoid pipe deadlocks and unbounded Python
        buffering. A dedicated inherited process group detects foreground
        writers surviving their launcher. Deliberate group/session escape is
        outside this boundary; no group is signalled to terminate or cancel it.
        """
        argv = ["git", *args]
        env = _git_environment()
        env["GIT_INDEX_FILE"] = os.fspath(self.candidate)
        search_path = os.pathsep.join(
            os.fspath(self.repository.root / entry) if not os.path.isabs(entry) else entry
            for entry in env.get("PATH", os.defpath).split(os.pathsep)
        )
        if shutil.which("git", path=search_path) is None:
            raise GitError("Could not start Git commit: Git executable is unavailable.")
        with (
            _commit_terminal(argv) as (command, terminal_fds),
            tempfile.TemporaryFile() as stdin,
            tempfile.TemporaryFile() as stdout,
            tempfile.TemporaryFile() as stderr,
        ):
            if not stat.S_ISREG(os.fstat(stdin.fileno()).st_mode):
                raise GitError("Could not prepare regular commit-message input.")
            stdin.write(input_bytes)
            stdin.flush()
            stdin.seek(0)
            self._append_context("phase: native-spawn-pending\n")
            # Once construction is attempted, even OSError may follow a live
            # child. Only preparation failures before this point are settled.
            self.started = True
            process = subprocess.Popen(
                command,
                cwd=self.repository.root,
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                process_group=0,
                pass_fds=terminal_fds,
            )
            self.git_pid = self.git_pgid = process.pid
            self._append_context(
                f"git-pid: {process.pid}\ngit-pgid: {process.pid}\nphase: native-spawned\n"
            )
            # No Popen context manager: an exceptional exit must not implicitly
            # wait for unfinished Git. __exit__ retains the transaction instead.
            if terminal_fds:
                offset = 0

                def show_diagnostics() -> None:
                    nonlocal offset
                    # Tail the regular spool, not a pipe whose closure could
                    # interrupt a surviving hook. Prompts on stderr stay visible.
                    end = os.fstat(stderr.fileno()).st_size
                    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
                    try:
                        while offset < end:
                            chunk = os.pread(
                                stderr.fileno(), min(_MAX_ERROR_BYTES, end - offset), offset
                            )
                            if not chunk:
                                break
                            offset += len(chunk)
                            remaining = memoryview(chunk)
                            while remaining:
                                remaining = remaining[os.write(terminal_fds[0], remaining) :]
                    finally:
                        signal.pthread_sigmask(signal.SIG_SETMASK, mask)

                while True:
                    try:
                        returncode = process.wait(timeout=0.05)
                    except subprocess.TimeoutExpired:
                        # Poll only to display output: no overall deadline,
                        # cancellation, termination, or automatic retry.
                        show_diagnostics()
                    else:
                        show_diagnostics()
                        break
            else:
                returncode = process.wait()
            self._append_context(f"native-returncode: {returncode}\nphase: native-returned\n")
            if returncode >= 0:
                self._verify_group_quiescence()
            stdout.seek(0)
            created_oid = self._created_from_stream(stdout) if returncode == 0 else ""
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read(_MAX_ERROR_BYTES + 1)
            diagnostic = stderr.read(_MAX_ERROR_BYTES + 1)
            if len(output) > _MAX_ERROR_BYTES:
                output = output[:_MAX_ERROR_BYTES] + b"\n[Git output truncated by Rhizome]\n"
            return subprocess.CompletedProcess(argv, returncode, output, diagnostic), created_oid

    def _verify_group_quiescence(self) -> None:
        assert self.git_pgid is not None
        while True:
            try:
                # Signal zero is only an existence probe, never termination.
                os.killpg(self.git_pgid, 0)
            except InterruptedError:
                continue
            except ProcessLookupError:
                self._append_context("phase: native-group-absent\n")
                return
            except OSError as error:
                raise GitOutcomeUncertain(
                    f"Could not establish native process-group quiescence: {error}"
                ) from error
            self._append_context("phase: native-group-still-present\n")
            # Git has returned but its inherited writers may not have. Retain
            # immediately rather than hanging on a possible orphaned zombie or
            # pretending the remaining group has completed. No child is killed.
            raise GitOutcomeUncertain(
                "Native Git returned but its process group is still present; "
                "hook or signer writers may remain active."
            )

    @staticmethod
    def _created_from_stream(stream) -> str:
        # Count all native-shaped headers, even beyond retained display output.
        # Bound a line's prefix so an enormous subject cannot allocate a whole
        # line. A header beyond this prefix bound is unverified, never guessed.
        header = re.compile(rb"^\[[^\[\r\n]*? ([0-9a-f]{40}|[0-9a-f]{64})\] ")
        prefix = b""
        created: list[bytes] = []
        while chunk := stream.read(_MAX_ERROR_BYTES):
            parts = chunk.split(b"\n")
            for position, part in enumerate(parts):
                prefix += part[: max(0, _MAX_ERROR_BYTES - len(prefix))]
                if position < len(parts) - 1:
                    if match := header.match(prefix):
                        created.append(match.group(1))
                    if len(created) > 1:
                        return ""
                    prefix = b""
        if match := header.match(prefix):
            created.append(match.group(1))
        return created[0].decode("ascii") if len(created) == 1 else ""

    def _append_context(self, observation: str) -> None:
        descriptor = os.open(
            self.candidate.parent / "context.txt",
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(descriptor, "wb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or self._identity(info) != self.context_owner:
                raise GitError("Commit recovery context ownership changed.")
            stream.write(observation.encode("utf-8", "surrogateescape"))
            stream.flush()
            os.fsync(stream.fileno())

    def record_created(self, oid: str) -> None:
        self.created_oid = oid
        self._append_context(f"native-created-oid: {oid or '(unverified)'}\n")

    def record_observed(self, oid: str, symbolic: str, *, when: str) -> None:
        self._append_context(
            f"observation: {when}\n"
            f"observed HEAD: {oid or '(unborn)'}\n"
            f"observed symbolic HEAD: {symbolic or '(detached)'}\n"
        )

    def publish(self) -> None:
        self.verify()
        self._append_context("phase: validating-index-publication\n")
        try:
            self.candidate.with_name("index.lock").lstat()
        except FileNotFoundError:
            pass
        else:
            raise GitError("The alternate index is still locked; its state is not stable.")
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        candidate_fd = os.open(
            self.candidate, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            candidate_info = os.fstat(candidate_fd)
            if not stat.S_ISREG(candidate_info.st_mode):
                raise GitError("Git did not leave a regular commit index.")
            # Validate the binary index, including extensions/shared storage.
            self.repository._git(
                ["ls-files", "--stage", "-z"],
                index_file=self.candidate,
                operation="validate commit index",
            )
            os.fsync(candidate_fd)
        finally:
            os.close(candidate_fd)
        shared = self.repository._git(
            ["rev-parse", "--shared-index-path"],
            index_file=self.candidate,
            operation="inspect commit split index",
        ).stdout
        if shared := _decode(shared).removesuffix("\n"):
            shared_path = Path(shared)
            if not shared_path.is_absolute():
                shared_path = self.repository.root / shared_path
            if (
                not re.fullmatch(r"sharedindex\.[0-9a-f]+", shared_path.name)
                or shared_path.resolve() != self.repository._git_path(shared_path.name).resolve()
                or shared_path.resolve().is_relative_to(self.candidate.parent.resolve())
            ):
                raise GitError("Commit split-index storage is not durable.")
            shared_fd = os.open(shared_path, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0))
            try:
                if not stat.S_ISREG(os.fstat(shared_fd).st_mode):
                    raise GitError("Commit split-index storage is not regular.")
                os.fsync(shared_fd)
            finally:
                os.close(shared_fd)
            parent_fd = os.open(shared_path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        self.verify()
        self._append_context("phase: candidate-move-pending\n")
        os.replace(self.candidate, self.lock_name, dst_dir_fd=self.directory_fd)
        self.lock_owner = self._identity(candidate_info)
        self.verify()
        self._append_context("phase: real-index-replace-pending\n")
        os.replace(
            self.lock_name,
            self.index.name,
            src_dir_fd=self.directory_fd,
            dst_dir_fd=self.directory_fd,
        )
        self.published = True
        self._append_context("phase: index-replaced-durability-pending\n")
        os.fsync(self.directory_fd)
        self._append_context("phase: index-published\n")

    def __exit__(self, _type: object, error: BaseException | None, _traceback: object) -> bool:
        preserve = self.started and not self.settled
        cleanup_error: BaseException | None = None
        try:
            if not preserve:
                if self._owns_lock():
                    os.unlink(self.lock_name, dir_fd=self.directory_fd)
                if self.temporary is not None:
                    self._verify_temporary()
                    shutil.rmtree(self.temporary)
        except (OSError, GitError) as failure:
            cleanup_error = failure
        finally:
            for descriptor in (
                self.context_fd,
                self.temporary_fd,
                self.lock_fd,
                self.directory_fd,
            ):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError as failure:
                        cleanup_error = cleanup_error or failure
            self.context_fd = self.temporary_fd = self.lock_fd = self.directory_fd = None
        if preserve or (cleanup_error is not None and self.started):
            detail = error or cleanup_error or "Commit did not reach a verified outcome."
            raise GitOutcomeUncertain(
                f"Commit outcome or index publication is uncertain: {detail}\n"
                f"Git process: {self.git_pid or '(unobserved)'}. "
                f"Git process group: {self.git_pgid or '(unobserved)'}. "
                f"Native Git's reported commit: {self.created_oid or '(unverified)'}. "
                f"Transaction artifacts: {self.temporary}. "
                "Any owned index lock is deliberately retained until inspection. "
                "Inspect HEAD, staged content, and any retained index.lock before retrying. "
                "Do not remove transaction files while Git or hooks may still be running."
            ) from (error or cleanup_error)
        if cleanup_error is not None:
            raise GitError(f"Commit transaction cleanup failed: {cleanup_error}") from cleanup_error
        return False


class Repository:
    """A discovered non-bare Git worktree with synchronous operations."""

    def __init__(self, path: str | Path = ".") -> None:
        candidate = Path(path).expanduser()
        if not candidate.exists():
            raise GitError(f"Repository path does not exist: {candidate}")
        if candidate.is_file():
            candidate = candidate.parent
        try:
            candidate = candidate.resolve()
        except OSError as error:
            raise GitError(f"Could not resolve repository path: {error}") from error

        bare = _run_process(
            ["git", "-C", os.fspath(candidate), "rev-parse", "--is-bare-repository"],
            cwd=None,
            operation="repository discovery",
        )
        if _decode(bare.stdout).strip() == "true":
            raise GitError("Bare repositories are not supported; open a Git worktree instead.")
        top = _run_process(
            [
                "git",
                "-C",
                os.fspath(candidate),
                "rev-parse",
                "--path-format=absolute",
                "--show-toplevel",
            ],
            cwd=None,
            operation="repository discovery",
        )
        # Remove Git's record terminator, not newlines belonging to the path.
        root_text = _decode(top.stdout).removesuffix("\n")
        if not root_text:
            raise GitError("Git did not report a worktree root.")
        self.root = Path(root_text).resolve()

    @classmethod
    def clone(cls, url: str, destination: str) -> Path:
        """Clone *url* without prompting and return the resulting worktree root."""
        _safe_value(url, "Clone URL")
        if not destination or "\x00" in destination:
            raise GitError("Clone destination is required and must not contain NUL bytes.")
        result = _run_process(
            ["git", "clone", "--", url, destination],
            cwd=None,
            timeout=_NETWORK_TIMEOUT,
            operation="clone",
            configured_ssh_command=_global_ssh_command(),
        )
        # Ensure a successful but unusual clone still produced a usable worktree.
        repo = cls(Path(destination))
        if result.returncode == 0:
            return repo.root
        raise GitError("Git clone did not create a worktree.")  # pragma: no cover

    def _configured_ssh_command(self) -> str:
        if _has_ssh_environment_override():
            return ""
        result = _run_process(
            ["git", "config", "--includes", "--get", "core.sshCommand"],
            cwd=self.root,
            ok_codes=(0, 1),
            operation="read SSH command configuration",
        )
        return _decode(result.stdout).rstrip("\n") if result.returncode == 0 else ""

    def _git(
        self,
        args: list[str],
        *,
        literal_paths: bool = False,
        timeout: float | None = _LOCAL_TIMEOUT,
        ok_codes: tuple[int, ...] | None = (0,),
        operation: str = "command",
        input_bytes: bytes | None = None,
        index_file: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        argv = ["git"]
        if literal_paths:
            argv.append("--literal-pathspecs")
        argv.extend(args)
        configured_ssh_command = (
            self._configured_ssh_command() if timeout == _NETWORK_TIMEOUT else ""
        )
        return _run_process(
            argv,
            cwd=self.root,
            timeout=timeout,
            ok_codes=ok_codes,
            operation=operation,
            input_bytes=input_bytes,
            configured_ssh_command=configured_ssh_command,
            index_file=index_file,
        )

    def _run_integration(
        self,
        args: list[str],
        *,
        operation: str,
        on_spawn: Callable[[int], None] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run one reviewed local integration command under native Git.

        The coordinator owns argv construction and outcome policy. This seam only
        provides native terminal/hook behavior and returns ordinary Git failures
        unchanged so the coordinator can distinguish a conflict from other exits.
        Once process construction is attempted, exceptions are uncertain and the
        process group is never signalled, cancelled, or automatically retried.
        """
        argv = ["git", *args]
        env = _git_environment()
        search_path = os.pathsep.join(
            os.fspath(self.root / entry) if not os.path.isabs(entry) else entry
            for entry in env.get("PATH", os.defpath).split(os.pathsep)
        )
        if shutil.which("git", path=search_path) is None:
            raise GitError(f"Could not start Git {operation}: Git executable is unavailable.")

        started = False
        process: subprocess.Popen[bytes] | None = None
        try:
            with (
                _commit_terminal(argv) as (command, terminal_fds),
                tempfile.TemporaryFile() as stdin,
                tempfile.TemporaryFile() as stdout,
                tempfile.TemporaryFile() as stderr,
            ):
                if not stat.S_ISREG(os.fstat(stdin.fileno()).st_mode):
                    raise GitError(f"Could not prepare regular Git {operation} input.")
                # No Popen context manager: exceptional exits must neither wait for
                # nor implicitly kill a Git process or one of its hooks/signers.
                started = True
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    env=env,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                    process_group=0,
                    pass_fds=terminal_fds,
                )
                if on_spawn is not None:
                    on_spawn(process.pid)

                if terminal_fds:
                    offset = 0

                    def show_diagnostics() -> None:
                        nonlocal offset
                        end = os.fstat(stderr.fileno()).st_size
                        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
                        try:
                            while offset < end:
                                chunk = os.pread(
                                    stderr.fileno(), min(_MAX_ERROR_BYTES, end - offset), offset
                                )
                                if not chunk:
                                    break
                                offset += len(chunk)
                                remaining = memoryview(chunk)
                                while remaining:
                                    remaining = remaining[os.write(terminal_fds[0], remaining) :]
                        finally:
                            signal.pthread_sigmask(signal.SIG_SETMASK, mask)

                    while True:
                        try:
                            returncode = process.wait(timeout=0.05)
                        except subprocess.TimeoutExpired:
                            # Poll only to display diagnostics; there is no
                            # mutation deadline, cancellation, or retry here.
                            show_diagnostics()
                        else:
                            show_diagnostics()
                            break
                else:
                    returncode = process.wait()

                if returncode < 0:
                    while True:
                        try:
                            # A signal-terminated leader is always uncertain. Probe
                            # only to preserve whether inherited writers remain.
                            os.killpg(process.pid, 0)
                        except InterruptedError:
                            continue
                        except ProcessLookupError:
                            group_state = "is absent"
                            break
                        except OSError as error:
                            raise GitOutcomeUncertain(
                                f"Native Git {operation} was terminated by signal {-returncode}; "
                                f"could not inspect process/group {process.pid}: {error}"
                            ) from error
                        group_state = "is still present"
                        break
                    raise GitOutcomeUncertain(
                        f"Native Git {operation} was terminated by signal {-returncode}; "
                        f"process/group {process.pid} {group_state}. Repository state and "
                        "child-process quiescence must be inspected before retrying."
                    )

                while True:
                    try:
                        # Signal zero only probes for inherited writers.
                        os.killpg(process.pid, 0)
                    except InterruptedError:
                        continue
                    except ProcessLookupError:
                        break
                    except OSError as error:
                        raise GitOutcomeUncertain(
                            "Could not establish native process-group quiescence "
                            f"after Git {operation}: {error}"
                        ) from error
                    raise GitOutcomeUncertain(
                        f"Native Git {operation} returned but its process group is still "
                        "present; hook or signer writers may remain active."
                    )

                stdout.seek(0)
                stderr.seek(0)
                output = stdout.read(_MAX_ERROR_BYTES + 1)
                diagnostic = stderr.read(_MAX_ERROR_BYTES + 1)
                if len(output) > _MAX_ERROR_BYTES:
                    output = output[:_MAX_ERROR_BYTES] + b"\n[Git output truncated by Rhizome]\n"
                if len(diagnostic) > _MAX_ERROR_BYTES:
                    diagnostic = diagnostic[:_MAX_ERROR_BYTES] + (
                        b"\n[Git error output truncated by Rhizome]\n"
                    )
                return subprocess.CompletedProcess(argv, returncode, output, diagnostic)
        except GitOutcomeUncertain:
            raise
        except BaseException as error:
            if started:
                pid = process.pid if process is not None else "(unobserved)"
                raise GitOutcomeUncertain(
                    f"Git {operation} outcome is uncertain after native spawn; "
                    f"process/group: {pid}. Inspect the repository before retrying: {error}"
                ) from error
            if isinstance(error, GitError):
                raise
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise GitError(f"Could not run Git {operation}: {error}") from error

    def _attribute_snapshot(self, paths: tuple[str, ...], *, source: str = "") -> bytes:
        """Read effective attributes at a captured tree without changing the real index.

        Git 2.36 has --cached but not check-attr --source. A private temporary
        index supplies that tree using the supported interface; split/sparse
        storage is disabled for this disposable index only, never in user config.
        """
        data = b"".join(os.fsencode(path) + b"\x00" for path in paths)
        command = ["check-attr", "-z", "--all", "--stdin"]
        if not source:
            return self._git(
                command,
                literal_paths=True,
                input_bytes=data,
                operation="review effective path attributes",
            ).stdout
        with tempfile.TemporaryDirectory(prefix="rhizome-attributes-") as directory:
            index = Path(directory) / "index"
            self._git(
                [
                    "-c",
                    "core.splitIndex=false",
                    "-c",
                    "core.sparseCheckout=false",
                    "-c",
                    "index.sparse=false",
                    "read-tree",
                    source,
                ],
                index_file=index,
                operation="read captured attribute tree",
            )
            return self._git(
                [*command, "--cached"],
                literal_paths=True,
                input_bytes=data,
                index_file=index,
                operation="review captured path attributes",
            ).stdout

    def _replace_index_entries(self, records: bytes, validate: Callable[[], None]) -> None:
        """Publish selected entries while continuously owning Git's real index lock.

        Git builds a candidate from the locked current index. Validation reads the
        unchanged real index under that same lock; the final rename is the only
        visibility point. HEAD and arbitrary editor writes have no joint Git lock,
        so those are rechecked immediately before publication, not claimed atomic.
        """
        index = self._git_path("index")
        directory = index.parent.resolve()
        index = directory / index.name
        lock_name = index.name + ".lock"
        directory_fd = lock_fd = None
        lock_owner = None
        published = False
        nofollow = getattr(os, "O_NOFOLLOW", 0)

        def identity(info: os.stat_result) -> tuple[int, int]:
            return info.st_dev, info.st_ino

        def owns_lock() -> bool:
            try:
                return (
                    identity(os.stat(lock_name, dir_fd=directory_fd, follow_symlinks=False))
                    == lock_owner
                )
            except FileNotFoundError:
                return False

        try:
            directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            lock_fd = os.open(
                lock_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
                dir_fd=directory_fd,
            )
            lock_owner = identity(os.fstat(lock_fd))
            with tempfile.TemporaryDirectory(prefix="rhizome-index-", dir=directory) as temporary:
                candidate = Path(temporary) / "index"
                try:
                    source_fd = os.open(index.name, os.O_RDONLY | nofollow, dir_fd=directory_fd)
                except FileNotFoundError:
                    source_fd = None  # Git must create a valid index, not read a zero-byte file.
                if source_fd is not None:
                    with os.fdopen(source_fd, "rb") as source:
                        info = os.fstat(source.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            raise GitError(
                                "The Git index is not a regular file; no changes applied."
                            )
                        with candidate.open("xb") as destination:
                            shutil.copyfileobj(source, destination)
                        candidate.chmod(stat.S_IMODE(info.st_mode))
                self._git(
                    # The real index still references its old shared base until
                    # publication. Native candidate maintenance must not expire it.
                    [
                        "-c",
                        "splitIndex.sharedIndexExpire=never",
                        "update-index",
                        "-z",
                        "--index-info",
                    ],
                    input_bytes=records,
                    index_file=candidate,
                    operation="apply selected patch to index",
                )
                candidate_fd = os.open(candidate, os.O_RDONLY | nofollow)
                try:
                    candidate_info = os.fstat(candidate_fd)
                    if not stat.S_ISREG(candidate_info.st_mode):
                        raise GitError("Git did not create a regular candidate index.")
                    os.fsync(candidate_fd)
                finally:
                    os.close(candidate_fd)
                shared = self._git(
                    ["rev-parse", "--shared-index-path"],
                    index_file=candidate,
                    operation="inspect candidate split index",
                ).stdout
                if shared := _decode(shared).removesuffix("\n"):
                    shared_path = Path(shared)
                    if not shared_path.is_absolute():
                        shared_path = self.root / shared_path
                    # A split index encodes a shared-file hash, not an arbitrary
                    # path. Its sidecar must live in Git's durable administrative
                    # directory, never in our soon-to-be-removed candidate folder.
                    if (
                        not re.fullmatch(r"sharedindex\.[0-9a-f]+", shared_path.name)
                        or shared_path.resolve() != self._git_path(shared_path.name).resolve()
                        or shared_path.resolve().is_relative_to(Path(temporary).resolve())
                    ):
                        raise GitError(
                            "Split-index sidecar storage is not durable; no index changes applied."
                        )
                    shared_fd = os.open(shared_path, os.O_RDONLY | nofollow)
                    try:
                        if not stat.S_ISREG(os.fstat(shared_fd).st_mode):
                            raise GitError("Split-index storage is not a regular file.")
                        os.fsync(shared_fd)
                    finally:
                        os.close(shared_fd)
                    parent_fd = os.open(shared_path.parent, os.O_RDONLY)
                    try:
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                if not owns_lock():
                    raise GitError("Git index lock ownership changed; no changes applied.")
                os.replace(candidate, lock_name, dst_dir_fd=directory_fd)
                lock_owner = identity(candidate_info)
                validate()
                current = self._git_path("index")
                if (
                    current.name != index.name
                    or current.parent.resolve() != directory
                    or identity(directory.stat()) != identity(os.fstat(directory_fd))
                    or not owns_lock()
                ):
                    raise GitError("Repository identity changed; no index changes applied.")
                os.replace(lock_name, index.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                published = True
                os.fsync(directory_fd)
        except OSError as error:
            if published:
                raise GitOutcomeUncertain(
                    "Index replaced, but durability or cleanup is uncertain. "
                    "Refresh and inspect before retrying."
                ) from error
            raise GitError(f"Git apply selected patch to index failed: {error}") from error
        finally:
            if directory_fd is not None and lock_owner is not None and not published:
                if owns_lock():
                    os.unlink(lock_name, dir_fd=directory_fd)
            if lock_fd is not None:
                os.close(lock_fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def _commit_encoding(self) -> _CommitEncoding:
        """Resolve and validate the encoding Git will record for a commit."""
        try:
            result = self._git(
                ["config", "--includes", "--null", "--get", "i18n.commitEncoding"],
                ok_codes=(0, 1),
                operation="read commit encoding configuration",
            )
        except GitError as error:
            raise GitError("Could not read the commit encoding configuration.") from error
        raw = b"UTF-8\0" if result.returncode == 1 else result.stdout
        if len(raw) > 129 or not raw.endswith(b"\0") or b"\0" in raw[:-1] or len(raw) == 1:
            raise GitError("The commit encoding configuration is malformed.")
        try:
            configured = raw[:-1].decode("ascii", "strict")
            # Python normalizes punctuation/whitespace in codec aliases; Git
            # writes this exact value into a commit header. Accept a name, not
            # an alias containing controls or header-delimiting whitespace.
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", configured):
                raise GitError("The commit encoding configuration is malformed.")
            codec = codecs.lookup(configured)
        except (LookupError, UnicodeDecodeError) as error:
            raise GitError("The configured commit encoding is unsupported by Rhizome.") from error

        # Git commit objects and their headers require an ASCII-compatible text
        # encoding. This also rejects Python's binary and text-transform codecs.
        probe_bytes = bytes(range(128))
        probe_text = probe_bytes.decode("ascii")
        try:
            if (
                probe_text.encode(codec.name, "strict") != probe_bytes
                or probe_bytes.decode(codec.name, "strict") != probe_text
            ):
                raise ValueError
        except (LookupError, UnicodeError, TypeError, ValueError) as error:
            raise GitError(
                "The configured commit encoding is not an ASCII-compatible text encoding."
            ) from error
        label = codec.name.upper().replace("_", "-")
        return _CommitEncoding(configured, codec, label)

    def _read_commit_template(self) -> str | None:
        """Read the effective configured commit template without blocking on special files."""
        try:
            result = self._git(
                ["config", "--includes", "--null", "--path", "--get", "commit.template"],
                ok_codes=(0, 1),
                operation="read commit template configuration",
            )
        except GitError as error:
            raise GitError("Could not read the commit template configuration.") from error
        if result.returncode == 1:
            return None
        raw_path = result.stdout
        if (
            len(raw_path) > 16 * 1024
            or not raw_path.endswith(b"\0")
            or b"\0" in raw_path[:-1]
            or len(raw_path) == 1
        ):
            raise GitError("The commit template configuration is malformed.")
        path = Path(os.fsdecode(raw_path[:-1]))
        if not path.is_absolute():
            path = self.root / path

        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError as error:
            raise GitError("The commit template file is missing.") from error
        except PermissionError as error:
            raise GitError("The commit template file is unreadable.") from error
        except OSError as error:
            raise GitError("The commit template file could not be opened.") from error
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise GitError("The commit template must be a regular file.")
            if before.st_size > _MAX_COMMIT_TEMPLATE_BYTES:
                raise GitError("The commit template exceeds Rhizome's 1 MiB limit.")
            chunks: list[bytes] = []
            remaining = _MAX_COMMIT_TEMPLATE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
        except GitError:
            raise
        except OSError as error:
            raise GitError("The commit template file could not be read.") from error
        finally:
            os.close(descriptor)

        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise GitError("The commit template changed while it was being read.")
        content = b"".join(chunks)
        if len(content) > _MAX_COMMIT_TEMPLATE_BYTES:
            raise GitError("The commit template exceeds Rhizome's 1 MiB limit.")
        if b"\0" in content:
            raise GitError("The commit template contains unsupported NUL bytes.")

        encoding = self._commit_encoding()
        try:
            decoded = content.decode(encoding.codec.name, "strict")
        except (LookupError, UnicodeError, TypeError) as error:
            raise GitError(f"The commit template is not valid {encoding.label} text.") from error
        if "\0" in decoded:
            raise GitError("The commit template contains unsupported NUL characters.")
        return decoded

    def _encode_commit_message(
        self,
        message: str,
        encoding: _CommitEncoding | None = None,
    ) -> bytes:
        """Strictly encode an accepted message without altering its text."""
        encoding = encoding or self._commit_encoding()
        if "\0" in message:
            raise GitError("The commit message contains unsupported NUL characters.")
        try:
            return message.encode(encoding.codec.name, "strict")
        except (LookupError, UnicodeError, TypeError) as error:
            raise GitError(f"The commit message cannot be encoded as {encoding.label}.") from error

    def _commit_index_fingerprint(self) -> bytes:
        """Bind flags/extensions as well as entries, without normalizing the index."""
        try:
            descriptor = os.open(
                self._git_path("index"),
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise GitError("The Git index is not a regular file.")
                return hashlib.file_digest(stream, "sha256").digest()
        except OSError as error:
            raise GitError(f"Could not capture the commit index: {error}") from error

    def _commit_reviewed(self, action: Action, validate: Callable[[], None]) -> str:
        """Commit a locked reviewed index using native hooks/signing/ref updates."""
        if not action.message.strip():
            raise GitError("A commit message is required.")
        self._ensure_no_operation_or_conflicts("commit")
        staged = self._git(
            ["diff", "--cached", "--quiet", "--exit-code", "--no-ext-diff", "--no-textconv"],
            ok_codes=(0, 1),
            operation="check staged changes",
        )
        if staged.returncode == 0:
            raise GitError("There are no staged changes to commit.")
        commit_encoding = self._commit_encoding()
        message_bytes = self._encode_commit_message(action.message, commit_encoding)

        def head_state() -> tuple[str, str]:
            symbolic = self._git(
                ["symbolic-ref", "--quiet", "HEAD"],
                ok_codes=(0, 1),
                operation="inspect commit HEAD identity",
            )
            return self._head(), _decode(symbolic.stdout).removesuffix("\n")

        before = head_state()
        index_before = self._commit_index_fingerprint()
        transaction = _CommitIndexTransaction(self)
        try:
            with transaction:
                validate()
                self._preflight_context(action)
                self._ensure_no_operation_or_conflicts("commit")
                if before != head_state() or index_before != self._commit_index_fingerprint():
                    raise GitError(
                        "Repository changed before the commit index was locked. Review again."
                    )
                staged = self._git(
                    [
                        "diff",
                        "--cached",
                        "--quiet",
                        "--exit-code",
                        "--no-ext-diff",
                        "--no-textconv",
                    ],
                    index_file=transaction.candidate,
                    ok_codes=(0, 1),
                    operation="check locked staged changes",
                )
                if staged.returncode == 0:
                    raise GitError("There are no staged changes to commit.")
                transaction.record(*before)
                result, created_oid = transaction.run(
                    [
                        "-c",
                        "core.abbrev=no",
                        "-c",
                        "color.ui=false",
                        "-c",
                        "color.diff=false",
                        "-c",
                        "splitIndex.sharedIndexExpire=never",
                        "-c",
                        f"i18n.commitEncoding={commit_encoding.configured}",
                        "commit",
                        "--edit",
                        "--no-status",
                        "--no-verbose",
                        "-F",
                        "-",
                    ],
                    input_bytes=message_bytes,
                )
                if result.returncode < 0:
                    raise GitOutcomeUncertain(
                        "Git was interrupted by a signal; hook quiescence is unknown."
                    )
                # Native Git prints its created OID after post-commit, not the
                # potentially substituted HEAD. This is a bounded compatibility
                # seam, not porcelain: missing/ambiguous headers fail closed.
                # Hook stdout is stderr; subjects/indented path summaries are data
                # (Git 2.36 hook.c/sequencer.c), not competing result identities.
                transaction.record_created(created_oid)
                # Synchronous hook index edits survive success and rejection.
                # Identity ambiguity/ref movement cannot veto index publication.
                transaction.record_observed(*head_state(), when="before-index-publication")
                transaction.publish()
                after = head_state()
                transaction.record_observed(*after, when="after-index-publication")
                if result.returncode == 0:
                    if not created_oid:
                        raise GitOutcomeUncertain(
                            "Git did not report one unambiguous created commit."
                        )
                    kind = self._git(
                        ["--no-replace-objects", "cat-file", "-t", created_oid],
                        operation="verify created commit type",
                    ).stdout
                    raw = self._git(
                        ["--no-replace-objects", "cat-file", "commit", created_oid],
                        operation="verify created commit parents",
                    ).stdout
                    parents = [
                        line.removeprefix(b"parent ")
                        for line in raw.split(b"\n\n", 1)[0].split(b"\n")
                        if line.startswith(b"parent ")
                    ]
                    expected = [before[0].encode("ascii")] if before[0] else []
                    if (
                        kind != b"commit\n"
                        or created_oid != after[0]
                        or after != head_state()
                        or after[0] == before[0]
                        or after[1] != before[1]
                        or parents != expected
                    ):
                        raise GitOutcomeUncertain(
                            "HEAD/ref postconditions do not match the created commit."
                        )
                elif after != before:
                    raise GitOutcomeUncertain(
                        "HEAD/ref changed despite Git reporting a commit failure."
                    )
                transaction.settled = True
                _raise_process_error(result, timeout=None, ok_codes=(0,), operation="commit")
                return _friendly_output(result, "Git commit completed.")
        except OSError as error:
            raise GitError(f"Could not prepare the commit index transaction: {error}") from error

    def _git_diff(
        self,
        args: list[str],
        *,
        literal_paths: bool = False,
        ok_codes: tuple[int, ...] = (0,),
        operation: str = "read diff",
        input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        # Git 2.36 diff's stat-cache refresh does not honor optional-locks
        # alone. Reading a diff must not take or rewrite the real index.
        argv = ["git", "--no-optional-locks", "-c", "diff.autoRefreshIndex=false"]
        if literal_paths:
            argv.append("--literal-pathspecs")
        argv.extend(args)
        return _run_spooled_process(
            argv, cwd=self.root, ok_codes=ok_codes, operation=operation, input_bytes=input_bytes
        )

    def _head(self) -> str:
        result = self._git(
            ["rev-parse", "--verify", "--quiet", "HEAD"],
            ok_codes=(0, 1),
            operation="read HEAD",
        )
        return _decode(result.stdout).strip() if result.returncode == 0 else ""

    def _branch(self) -> str:
        result = self._git(
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            ok_codes=(0, 1),
            operation="read current branch",
        )
        return _decode(result.stdout).strip() if result.returncode == 0 else ""

    def _resolve_commit(self, revision: str, *, label: str = "Revision") -> str:
        _safe_value(revision, label)
        result = self._git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"],
            ok_codes=(0, 1),
            operation="validate revision",
        )
        oid = _decode(result.stdout).strip()
        if result.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40,64}", oid):
            raise GitError(f"{label} does not name a commit: {_redact(revision)}")
        return oid.lower()

    def _validate_branch(self, name: str, label: str = "Branch name") -> str:
        _safe_value(name, label)
        result = self._git(
            ["check-ref-format", "--branch", name],
            ok_codes=(0, 1),
            operation="validate branch name",
        )
        if result.returncode != 0:
            raise GitError(f"Invalid {label.lower()}: {name}")
        return name

    def _local_branch_name(self, value: str, label: str = "Branch name") -> str:
        if value.startswith("refs/") and not value.startswith("refs/heads/"):
            raise GitError(f"{label} is not a local branch: {value}")
        return self._validate_branch(value.removeprefix("refs/heads/"), label)

    def _validate_tag(self, name: str) -> str:
        _safe_value(name, "Tag name")
        result = self._git(
            ["check-ref-format", f"refs/tags/{name}"],
            ok_codes=(0, 1),
            operation="validate tag name",
        )
        if result.returncode != 0:
            raise GitError(f"Invalid tag name: {name}")
        return name

    def _tag_name(self, value: str) -> str:
        if value.startswith("refs/") and not value.startswith("refs/tags/"):
            raise GitError(f"Tag target is not a tag: {value}")
        return self._validate_tag(value.removeprefix("refs/tags/"))

    def _path(self, path: str) -> str:
        if not path:
            raise GitError("A repository-relative path is required.")
        if "\x00" in path:
            raise GitError("Paths must not contain NUL bytes.")
        pure = PurePosixPath(path)
        if pure.is_absolute() or any(part == ".." for part in pure.parts):
            raise GitError("Paths must stay within the repository worktree.")
        if path == ".":
            raise GitError("An explicit file path is required.")
        return path

    def _paths(self, paths: tuple[str, ...]) -> list[str]:
        return [self._path(path) for path in paths]

    def _related_status_paths(self, paths: list[str]) -> list[str]:
        """Include both sides when a selected status entry is a rename/copy."""
        expanded = list(paths)
        selected = set(paths)
        for change in self._status():
            if not change.original_path:
                continue
            if change.path in selected:
                expanded.append(change.original_path)
            elif change.original_path in selected:
                expanded.append(change.path)
        return list(dict.fromkeys(expanded))

    def _refs(self) -> tuple[Ref, ...]:
        fmt = "%(refname)%09%(objectname)%09%(*objectname)%09%(upstream:short)%09%(symref)"
        result = self._git(
            ["for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes", "refs/tags"],
            operation="list refs",
        )
        current = self._branch()
        # Remote names may contain slashes. Match the most specific configured
        # name rather than assuming the first path component is the owner.
        remote_names = sorted(self._remote_names(), key=len, reverse=True)
        refs: list[Ref] = []
        for line in _decode(result.stdout).splitlines():
            fields = line.split("\t")
            if len(fields) != 5:
                continue
            full_name, object_oid, peeled_oid, upstream, symref = fields
            if symref:  # e.g. refs/remotes/origin/HEAD, not a checkout target.
                continue
            if full_name.startswith("refs/heads/"):
                kind = "local"
                name = full_name.removeprefix("refs/heads/")
            elif full_name.startswith("refs/remotes/"):
                kind = "remote"
                name = full_name.removeprefix("refs/remotes/")
            elif full_name.startswith("refs/tags/"):
                kind = "tag"
                name = full_name.removeprefix("refs/tags/")
            else:  # pragma: no cover - constrained by for-each-ref prefixes
                continue
            owner = ""
            if kind == "remote":
                owner = next(
                    (
                        remote_name
                        for remote_name in remote_names
                        if name.startswith(f"{remote_name}/")
                    ),
                    "",
                )
            refs.append(
                Ref(
                    name=name,
                    full_name=full_name,
                    oid=peeled_oid or object_oid,
                    kind=kind,
                    current=kind == "local" and name == current,
                    upstream=upstream if kind == "local" else "",
                    remote=owner,
                )
            )
        return tuple(refs)

    def _remote_names(self) -> tuple[str, ...]:
        result = self._git(["remote"], operation="list remotes")
        return tuple(line for line in _decode(result.stdout).splitlines() if line)

    def _remote(self, requested: str, *, sole_when_empty: bool = False) -> str:
        names = self._remote_names()
        if requested:
            _safe_value(requested, "Remote")
            if requested not in names:
                raise GitError(f"Unknown remote: {requested}")
            return requested
        if sole_when_empty:
            if len(names) == 1:
                return names[0]
            if not names:
                raise GitError("This repository has no remotes.")
            raise GitError("More than one remote exists; select one before pushing.")
        return ""

    def _remote_push_urls(self, name: str) -> tuple[str, ...]:
        result = self._git(
            ["remote", "get-url", "--push", "--all", "--", name],
            operation="read remote push URLs",
        )
        return tuple(url for url in _decode(result.stdout).splitlines() if url)

    def _remotes(self) -> tuple[Remote, ...]:
        remotes: list[Remote] = []
        for name in self._remote_names():
            result = self._git(["remote", "get-url", "--", name], operation="read remote URL")
            raw_url = _decode(result.stdout).rstrip("\n")
            url = _redact_url(raw_url)
            lower = raw_url.lower()
            provider = "Git"
            if "github.com" in lower:
                provider = "GitHub"
            elif "dev.azure.com" in lower or "visualstudio.com" in lower:
                provider = "Azure DevOps"
            elif "gitlab.com" in lower:
                provider = "GitLab"
            push_urls = self._remote_push_urls(name)
            push_url = _redact_url(push_urls[0]) if len(push_urls) == 1 else ""
            remotes.append(
                Remote(
                    name=name,
                    url=url,
                    provider=provider,
                    web_url=self._web_url(url),
                    push_url=push_url,
                )
            )
        return tuple(remotes)

    @staticmethod
    def _web_url(url: str) -> str:
        value = url.removesuffix(".git")
        if re.match(r"^[^/@\s]+@[^/:\s]+:.+$", value):
            host_path = value.split("@", 1)[1]
            host, path = host_path.split(":", 1)
            return f"https://{host}/{path}"
        if value.startswith(("http://", "https://")):
            return value
        return ""

    def worktrees(self) -> tuple[Worktree, ...]:
        """Return Git's registered worktrees without touching their contents."""
        result = self._git(
            ["worktree", "list", "--porcelain", "-z"],
            ok_codes=(0, 129),
            operation="list worktrees",
        )
        if result.returncode == 129:
            raise GitError("Listing worktrees requires Git 2.36 or newer.")
        worktrees: list[Worktree] = []
        fields: list[str] = []

        def append_record(record: list[str]) -> None:
            values: dict[str, str] = {}
            markers: set[str] = set()
            for field in record:
                key, separator, value = field.partition(" ")
                if separator:
                    values[key] = value
                else:
                    markers.add(key)
            raw_path = values.get("worktree", "")
            if not raw_path:
                return
            path = Path(raw_path)
            try:
                current = path.resolve() == self.root
            except OSError:
                current = False
            head = values.get("HEAD", "")
            if head and not head.strip("0"):
                head = ""
            detached = "detached" in markers
            branch_ref = values.get("branch", "")
            branch = ""
            if not detached and branch_ref.startswith("refs/heads/"):
                branch = branch_ref.removeprefix("refs/heads/")
            worktrees.append(
                Worktree(
                    path=path,
                    head=head,
                    branch=branch,
                    current=current,
                    bare="bare" in markers,
                    detached=detached,
                    locked="locked" in markers or "locked" in values,
                    lock_reason=values.get("locked", ""),
                    prunable="prunable" in markers or "prunable" in values,
                    prune_reason=values.get("prunable", ""),
                    # Git always lists the main worktree first, even from a linked checkout.
                    primary=not worktrees,
                )
            )

        for field in _decode(result.stdout).split("\x00"):
            if field:
                fields.append(field)
            elif fields:
                append_record(fields)
                fields = []
        if fields:
            append_record(fields)
        return tuple(worktrees)

    def _stashes(self) -> tuple[Stash, ...]:
        result = self._git(
            ["stash", "list", "--format=%gd%x00%H%x00%gs%x00"], operation="list stashes"
        )
        fields = _decode(result.stdout).split("\x00")
        stashes: list[Stash] = []
        for offset in range(0, len(fields) - 2, 3):
            ref = fields[offset].lstrip("\n")
            oid = fields[offset + 1]
            subject = fields[offset + 2]
            if ref and oid:
                stashes.append(Stash(ref=ref, oid=oid, subject=subject))
        return tuple(stashes)

    def _status(self) -> tuple[FileChange, ...]:
        result = self._git(
            [
                "--no-optional-locks",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignored=no",
            ],
            operation="read status",
        )
        records = _decode(result.stdout).split("\x00")
        changes: list[FileChange] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if len(record) < 4 or record[2] != " ":
                continue
            index_state, worktree_state = record[0], record[1]
            path = record[3:]
            original = ""
            if index_state in {"R", "C"} or worktree_state in {"R", "C"}:
                if index >= len(records):
                    break
                original = records[index]
                index += 1
            changes.append(
                FileChange(
                    path=path,
                    index=index_state,
                    worktree=worktree_state,
                    original_path=original,
                )
            )
        return tuple(changes)

    def _intent_to_add(self, path: str) -> bool:
        """Return whether *path* is an index intent-to-add placeholder.

        diff-files represents that special index entry with source mode 000000;
        unlike an ordinary tracked empty blob, it has no worktree restoration
        source and must not be passed to ``git restore --worktree``.
        """
        result = self._git(
            ["diff-files", "--raw", "-z", "--no-ext-diff", "--", path],
            literal_paths=True,
            operation="inspect discard path",
        )
        return any(record.startswith(b":000000 ") for record in result.stdout.split(b"\x00"))

    def _git_path(self, name: str) -> Path:
        result = self._git(["rev-parse", "--git-path", name], operation="inspect repository state")
        value = Path(_decode(result.stdout).removesuffix("\n"))
        return value if value.is_absolute() else self.root / value

    def _operation(self) -> str:
        rebase_apply = self._git_path("rebase-apply")
        if rebase_apply.is_dir() and (rebase_apply / "applying").exists():
            return "am"
        checks = (
            ("merge", ("MERGE_HEAD",)),
            ("rebase", ("rebase-merge", "rebase-apply")),
            ("cherry-pick", ("CHERRY_PICK_HEAD",)),
            ("revert", ("REVERT_HEAD",)),
        )
        for operation, paths in checks:
            if any(self._git_path(path).exists() for path in paths):
                return operation
        return ""

    def _ensure_no_operation_or_conflicts(self, operation: str) -> None:
        current = self._operation()
        if current:
            raise GitError(
                f"Cannot {operation} while a {current} operation is active; "
                "continue or abort it first."
            )
        if any(change.conflicted for change in self._status()):
            raise GitError(f"Cannot {operation} while the worktree has unresolved conflicts.")

    def _preflight_context(self, action: Action) -> None:
        """Validate captured menu context before an action can mutate the repository."""
        # expected_ref is also the signal that empty captured values are intentional:
        # an unborn expected_head and a detached expected_branch must still be checked.
        # A nonempty expected_head retains the same generic guard without expected_ref.
        context_guarded = bool(action.expected_ref or action.expected_head)
        if context_guarded and (
            self._head() != action.expected_head or self._branch() != action.expected_branch
        ):
            raise GitError("Branch changed. Refresh and choose the action again.")
        if action.expected_ref and action.expected_oid:
            try:
                actual = self._resolve_commit(action.expected_ref, label="Selected ref")
            except GitError as error:
                raise GitError(
                    "The selected ref changed. Refresh and choose the action again."
                ) from error
            if actual != action.expected_oid:
                raise GitError("The selected ref changed. Refresh and choose the action again.")
        if context_guarded and action.operation not in {
            "stage",
            "unstage",
            "continue",
            "abort",
        }:
            self._ensure_no_operation_or_conflicts(action.operation)

    def _upstream(self, branch: str) -> str:
        if not branch:
            return ""
        result = self._git(
            ["for-each-ref", "--format=%(upstream:short)", f"refs/heads/{branch}"],
            operation="read upstream",
        )
        return _decode(result.stdout).strip()

    def _upstream_push_target(self, branch: str) -> tuple[str, str]:
        """Return the configured upstream remote and its full branch ref."""
        fmt = "%(upstream:remotename)%00%(upstream:remoteref)"
        result = self._git(
            ["for-each-ref", f"--format={fmt}", f"refs/heads/{branch}"],
            operation="read push upstream",
        )
        fields = _decode(result.stdout).rstrip("\n").split("\x00", 1)
        if len(fields) != 2 or not all(fields):
            return "", ""
        remote, destination = fields
        if destination.startswith("refs/heads/"):
            self._validate_branch(destination.removeprefix("refs/heads/"), "Upstream branch name")
        else:
            raise GitError("The current branch upstream is not a remote branch.")
        return self._remote(remote), destination

    def _ahead_behind(self, head: str, upstream: str) -> tuple[int, int]:
        if not head or not upstream:
            return 0, 0
        result = self._git(
            ["rev-list", "--left-right", "--count", f"{head}...{upstream}"],
            ok_codes=(0, 128),
            operation="compare upstream",
        )
        if result.returncode != 0:
            return 0, 0
        fields = _decode(result.stdout).split()
        if len(fields) != 2:
            return 0, 0
        return int(fields[0]), int(fields[1])

    def _investigation_read(
        self, args: list[str], *, operation: str, input_bytes: bytes | None = None
    ) -> bytes:
        """Bound read output and never let live replacement refs rewrite exact OIDs."""
        result = self._git_diff(
            ["--no-replace-objects", *args], operation=operation, input_bytes=input_bytes
        )
        if len(result.stdout) > _MAX_DIFF_BYTES:
            raise GitError(f"Git {operation} exceeds the investigation output limit.")
        return result.stdout

    def _investigation_identity(self) -> str:
        paths = [self.root]
        for option in ("--absolute-git-dir", "--git-common-dir"):
            value = _decode(self._git(["rev-parse", option]).stdout).removesuffix("\n")
            path = Path(value)
            paths.append((path if path.is_absolute() else self.root / path).resolve())
        try:
            records = [(os.fspath(path), path.stat().st_dev, path.stat().st_ino) for path in paths]
        except OSError as error:
            raise GitError("The captured repository is no longer available.") from error
        return hashlib.sha256(repr(records).encode("utf-8", "surrogateescape")).hexdigest()

    def _investigation_shallow(self) -> str:
        path = self._git_path("shallow")
        try:
            with path.open("rb") as stream:
                data = stream.read(_MAX_DIFF_BYTES + 1)
        except FileNotFoundError:
            return ""
        except OSError as error:
            raise GitError("Cannot read the repository's shallow boundary.") from error
        if len(data) > _MAX_DIFF_BYTES:
            raise GitError("The shallow boundary exceeds the investigation output limit.")
        return hashlib.sha256(data).hexdigest() if data else ""

    def _investigation_refs(self) -> tuple[CapturedRef, ...]:
        fmt = "%(refname)%00%(objectname)%00%(objecttype)%00%(*objectname)%00%(*objecttype)"
        data = self._investigation_read(
            ["for-each-ref", "--sort=refname", f"--format={fmt}"], operation="capture refs"
        )
        refs = []
        for line in _decode(data).split("\n"):
            if not line:
                continue
            fields = line.split("\x00")
            if len(fields) != 5:
                raise GitError("Git returned malformed captured refs.")
            name, oid, kind, peeled, peeled_kind = fields
            if kind == "commit":
                refs.append(CapturedRef(name, oid))
            elif kind == "tag":
                if peeled_kind == "commit":
                    refs.append(CapturedRef(name, peeled))
                elif peeled_kind == "tag":
                    # Nested annotated tags are uncommon; resolve the captured object,
                    # not the moving name. Tags to blobs/trees are not history roots.
                    result = self._git(
                        [
                            "--no-replace-objects",
                            "rev-parse",
                            "--verify",
                            "--quiet",
                            f"{oid}^{{commit}}",
                        ],
                        ok_codes=(0, 1),
                        operation="peel captured tag",
                    )
                    if result.returncode == 0:
                        refs.append(CapturedRef(name, _decode(result.stdout).strip()))
        return tuple(refs)

    def _investigation_worktree_heads(self) -> tuple[CapturedRef, ...]:
        # Git's legacy --all includes detached HEADs in linked worktrees too.
        # Capture those exact objects rather than silently narrowing unified history.
        return tuple(
            sorted(
                (
                    CapturedRef(os.fspath(worktree.path), worktree.head)
                    for worktree in self.worktrees()
                    if not worktree.bare
                    and not worktree.prunable
                    and worktree.head
                    and set(worktree.head) != {"0"}
                ),
                key=lambda item: item.full_name,
            )
        )

    def capture_investigation(self, roots: tuple[str, ...] = ()) -> InvestigationScope:
        """Capture all commit-bearing refs, or explicit full refs/HEAD/full OIDs."""
        if not isinstance(roots, tuple) or any(not isinstance(root, str) for root in roots):
            raise ValueError("Investigation roots must be a tuple of exact revision strings.")
        repository_id = self._investigation_identity()
        refs = self._investigation_refs()
        by_name = {ref.full_name: ref.oid for ref in refs}
        head = self._head()
        branch_result = self._git(
            ["symbolic-ref", "--quiet", "HEAD"], ok_codes=(0, 1), operation="capture HEAD branch"
        )
        branch = _decode(branch_result.stdout).strip() if branch_result.returncode == 0 else ""
        bindings: list[CapturedRef] = []
        selected = []
        worktree_heads = self._investigation_worktree_heads() if not roots else ()
        if not roots:
            bindings.extend(refs)
            bindings.append(CapturedRef("HEAD", head))
            selected.extend(ref.oid for ref in refs)
            selected.extend(ref.oid for ref in worktree_heads)
            if head:
                selected.append(head)
        for root in roots:
            _safe_value(root, "Investigation root")
            if root == "HEAD":
                if not head:
                    raise GitError("HEAD does not name a commit yet.")
                oid = head
                bindings.append(CapturedRef(root, oid))
            elif root.startswith("refs/"):
                if root not in by_name:
                    raise GitError(f"Investigation root is not an exact commit-bearing ref: {root}")
                oid = by_name[root]
                bindings.append(CapturedRef(root, oid))
            elif re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", root):
                oid = self._resolve_commit(root, label="Investigation root")
            else:
                raise GitError(
                    "Use HEAD, a full refs/... name, or a full commit OID for investigation."
                )
            selected.append(oid)
        scope = InvestigationScope(
            repository_id=repository_id,
            scope_id=uuid.uuid4().hex,
            kind="focused" if roots else "unified",
            label=", ".join(roots) if roots else "All refs + HEAD",
            root_oids=tuple(sorted(set(selected))),
            captured_refs=refs,
            bindings=tuple(sorted(set(bindings), key=lambda ref: ref.full_name)),
            head_oid=head,
            branch=branch,
            shallow_fingerprint=self._investigation_shallow(),
            worktree_heads=worktree_heads,
        )
        state = self.investigation_status(scope)
        if not state.current:
            raise GitError(f"Repository changed during scope capture: {state.reason}")
        return scope

    def investigation_status(self, scope: InvestigationScope) -> ScopeState:
        """Observe freshness without ever retargeting a captured root."""
        try:
            if self._investigation_identity() != scope.repository_id:
                return ScopeState(False, False, "The captured repository was replaced.")
            if scope.root_oids:
                data = self._git(
                    [
                        "--no-replace-objects",
                        "cat-file",
                        "--batch-check=%(objectname) %(objecttype)",
                    ],
                    input_bytes=("\n".join(scope.root_oids) + "\n").encode("ascii"),
                    operation="check captured objects",
                ).stdout
                records = _decode(data).splitlines()
                if len(records) != len(scope.root_oids) or any(
                    record != f"{oid} commit"
                    for oid, record in zip(scope.root_oids, records, strict=True)
                ):
                    return ScopeState(
                        False, False, "Captured commit objects are no longer available."
                    )
            if self._investigation_shallow() != scope.shallow_fingerprint:
                return ScopeState(
                    False, True, "The shallow history boundary changed; refresh explicitly."
                )
            current_refs = self._investigation_refs()
            if (
                scope.kind == "unified"
                and self._investigation_worktree_heads() != scope.worktree_heads
            ):
                return ScopeState(False, True, "Worktree HEADs changed; refresh explicitly.")
            if scope.kind == "unified" and current_refs != scope.captured_refs:
                return ScopeState(False, True, "Repository refs changed; refresh explicitly.")
            current = {ref.full_name: ref.oid for ref in current_refs}
            if any(ref.full_name == "HEAD" for ref in scope.bindings):
                current["HEAD"] = self._head()
                result = self._git(["symbolic-ref", "--quiet", "HEAD"], ok_codes=(0, 1))
                branch = _decode(result.stdout).strip() if result.returncode == 0 else ""
                if branch != scope.branch:
                    return ScopeState(False, True, "HEAD's branch changed; refresh explicitly.")
            if any(current.get(ref.full_name) != ref.oid for ref in scope.bindings):
                return ScopeState(False, True, "A captured ref changed; refresh explicitly.")
        except (GitError, OSError, UnicodeError) as error:
            return ScopeState(False, False, f"Captured scope unavailable: {error}")
        return ScopeState(True, True)

    @contextmanager
    def _investigation_lines(
        self,
        args: list[str],
        roots: tuple[str, ...],
        *,
        paths: tuple[str, ...] = (),
        nul: bool = False,
    ):
        """Stream bounded index/path records with a wall-clock timeout."""
        argv = ["git", "--no-replace-objects", *args, "--stdin", "--", *paths]
        expired = threading.Event()
        with (
            tempfile.SpooledTemporaryFile(max_size=64 * 1024) as input_file,
            tempfile.SpooledTemporaryFile(max_size=_MAX_ERROR_BYTES) as errors,
        ):
            input_file.write(("\n".join(roots) + "\n").encode("ascii"))
            input_file.seek(0)
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=self.root,
                    env=_git_environment(),
                    stdin=input_file,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                )
            except OSError as error:
                raise GitError(f"Cannot read investigation history: {error}") from error

            def expire() -> None:
                if process.poll() is None:
                    expired.set()
                    process.kill()

            timer = threading.Timer(_LOCAL_TIMEOUT, expire)
            timer.daemon = True
            timer.start()
            try:
                stdout = process.stdout
                assert stdout is not None

                def records():
                    if not nul:
                        while line := stdout.readline(4097):
                            if len(line) > 4096:
                                raise GitError("Git returned an oversized history index record.")
                            yield line
                        return
                    pending = b""
                    while chunk := stdout.read(64 * 1024):
                        parts = (pending + chunk).split(b"\x00")
                        pending = parts.pop()
                        for part in parts:
                            if len(part) > _MAX_DIFF_BYTES:
                                raise GitError("Git returned an oversized file-history record.")
                            yield part
                        if len(pending) > _MAX_DIFF_BYTES:
                            raise GitError("Git returned an oversized file-history record.")
                    if pending:
                        raise GitError("Git returned incomplete file-history output.")

                yield records()
                returncode = process.wait()
                if expired.is_set():
                    raise GitError("Git investigation history timed out; the view is incomplete.")
                errors.seek(0)
                result = subprocess.CompletedProcess(
                    argv, returncode, b"", errors.read(_MAX_ERROR_BYTES + 1)
                )
                _raise_process_error(
                    result,
                    timeout=_LOCAL_TIMEOUT,
                    ok_codes=(0,),
                    operation="read investigation history",
                )
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                process.wait()
                if process.stdout is not None:
                    process.stdout.close()

    @staticmethod
    def _investigation_log_options() -> list[str]:
        return ["--no-show-signature", "--no-patch", "--no-decorate", "--no-notes", "--no-color"]

    def _investigation_commits(
        self, scope: InvestigationScope, oids: tuple[str, ...]
    ) -> tuple[Commit, ...]:
        if not oids:
            return ()
        data = self._investigation_read(
            [
                "log",
                "--no-walk=unsorted",
                *self._investigation_log_options(),
                "-z",
                "--format=%H%x00%P%x00%an%x00%ae%x00%aI%x00%s",
                "--stdin",
                "--",
            ],
            input_bytes=("\n".join(oids) + "\n").encode("ascii"),
            operation="read investigation metadata",
        )
        fields = _decode(data).split("\x00")
        if fields[-1] != "" or (len(fields) - 1) % 6:
            raise GitError("Git returned malformed investigation metadata.")
        names: dict[str, list[str]] = {}
        for ref in scope.captured_refs:
            short = re.sub(r"^refs/(?:heads|remotes|tags)/", "", ref.full_name)
            names.setdefault(ref.oid, []).append(short)
        if scope.head_oid:
            names.setdefault(scope.head_oid, []).append("HEAD")
        commits = {}
        for start in range(0, len(fields) - 1, 6):
            oid, parents, author, email, date, subject = fields[start : start + 6]
            commits[oid] = Commit(
                oid,
                tuple(parents.split()),
                author,
                email,
                _commit_date(date),
                subject,
                tuple(dict.fromkeys(names.get(oid, ()))),
            )
        if any(oid not in commits for oid in oids):
            raise GitError("Git returned incomplete investigation metadata.")
        return tuple(commits[oid] for oid in oids)

    def investigation_history(
        self,
        scope: InvestigationScope,
        *,
        order: str = "topology",
        offset: int = 0,
        after: tuple[int, int] | None = None,
        limit: int = 300,
    ) -> tuple[tuple[Commit, ...], tuple[tuple[int, int], ...], bool]:
        """Read one bounded window from exact roots; never retain a whole-scope index."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("History limit must be between 1 and 1000.")
        if order not in ("topology", "author-time"):
            raise ValueError("Unknown history order.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("History offset must be a non-negative integer.")
        state = self.investigation_status(scope)
        if not state.available:
            raise GitError(state.reason)
        if self._investigation_shallow() != scope.shallow_fingerprint:
            raise GitError("The shallow history boundary changed; refresh before loading more.")
        if not scope.root_oids:
            return (), (), False
        options = self._investigation_log_options()
        if order == "topology":
            data = self._investigation_read(
                [
                    "log",
                    "--topo-order",
                    *options,
                    f"--skip={offset}",
                    f"-n{limit + 1}",
                    "--format=%H",
                    "--stdin",
                    "--",
                ],
                input_bytes=("\n".join(scope.root_oids) + "\n").encode("ascii"),
                operation="read topology page",
            )
            oids = tuple(_decode(data).splitlines())
            has_more = len(oids) > limit
            oids = oids[:limit]
            keys = tuple((0, offset + position) for position in range(len(oids)))
        else:
            with self._investigation_lines(
                ["log", "--author-date-order", *options, "--format=%at%x00%H"], scope.root_oids
            ) as lines:

                def candidates():
                    for position, line in enumerate(lines):
                        try:
                            raw_epoch, raw_oid = line.rstrip(b"\n").split(b"\x00")
                            key = (-int(raw_epoch), position)
                            oid = raw_oid.decode("ascii")
                        except (ValueError, UnicodeError) as error:
                            raise GitError("Git returned a malformed history index.") from error
                        if after is None or key > after:
                            yield key, oid

                selected = heapq.nsmallest(limit + 1, candidates(), key=lambda item: item[0])
            has_more = len(selected) > limit
            oids = tuple(item[1] for item in selected[:limit])
            keys = tuple(item[0] for item in selected[:limit])
        commits = self._investigation_commits(scope, oids)
        if self._investigation_shallow() != scope.shallow_fingerprint:
            raise GitError(
                "The shallow history boundary changed while reading; refresh explicitly."
            )
        return commits, keys, has_more

    def _investigation_path_matches(self, oids: tuple[str, ...], path: str) -> set[str]:
        selected = PurePosixPath(self._path(path)).as_posix()
        data = self._investigation_read(
            [
                "diff-tree",
                "--stdin",
                "--root",
                "-m",
                "-r",
                "-z",
                "--name-status",
                "-M",
                "-C",
                "--no-ext-diff",
                "--no-textconv",
            ],
            input_bytes=("\n".join(oids) + "\n").encode("ascii"),
            operation="search changed paths",
        )
        tokens = _decode(data).split("\x00")
        index, oid = 0, ""
        allowed = set(oids)
        matched = set()
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if not token:
                continue
            if token in allowed:
                oid = token
                continue
            if not oid or not re.fullmatch(r"[ACDMRTUXB][0-9]*", token):
                raise GitError("Git returned malformed changed-path records.")
            count = 2 if token[0] in "RC" else 1
            paths = tokens[index : index + count]
            if len(paths) != count or not all(paths):
                raise GitError("Git returned incomplete changed-path records.")
            if selected in paths:
                matched.add(oid)
            index += count
        return matched

    def investigation_search(
        self,
        scope: InvestigationScope,
        query: SearchQuery,
        *,
        order: str = "topology",
        offset: int = 0,
        after: tuple[int, int] | None = None,
        scan_limit: int = 1000,
    ) -> tuple[
        tuple[Commit, ...],
        tuple[tuple[int, int], ...],
        tuple[tuple[int, tuple[str, ...], tuple[str, ...]], ...],
        bool,
    ]:
        """Search full messages and captured identity metadata in one traversal batch."""
        if query.path is not None:
            self._path(query.path)
        commits, keys, has_more = self.investigation_history(
            scope, order=order, offset=offset, after=after, limit=scan_limit
        )
        if not commits:
            return commits, keys, (), has_more
        data = self._investigation_read(
            [
                "log",
                "--no-walk=unsorted",
                *self._investigation_log_options(),
                "-z",
                "--format=%H%x00%B",
                "--stdin",
                "--",
            ],
            input_bytes=("\n".join(commit.oid for commit in commits) + "\n").encode("ascii"),
            operation="search full commit messages",
        )
        fields = _decode(data).split("\x00")
        if fields[-1] != "" or (len(fields) - 1) % 2:
            raise GitError("Git returned unsupported commit message data.")
        messages = dict(zip(fields[:-1:2], fields[1:-1:2], strict=True))
        needle = query.text.casefold()
        path_matches = (
            self._investigation_path_matches(tuple(commit.oid for commit in commits), query.path)
            if query.path is not None
            else None
        )
        matches = []
        for index, commit in enumerate(commits):
            if commit.oid not in messages:
                raise GitError("Git returned incomplete search metadata.")
            matched = []
            for name, value in (
                ("message", messages[commit.oid]),
                ("author", commit.author),
                ("email", commit.email),
                ("sha", commit.oid),
            ):
                if needle and needle in value.casefold():
                    matched.append(name)
            refs = tuple(
                ref.full_name
                for ref in scope.captured_refs
                if ref.oid == commit.oid and needle and needle in ref.full_name.casefold()
            )
            if commit.oid == scope.head_oid and needle and needle in "head":
                refs += ("HEAD",)
            if refs:
                matched.append("ref")
            if needle and not matched:
                continue
            if path_matches is not None:
                if commit.oid not in path_matches:
                    continue
                matched.append("path")
            if query.authored_from is not None or query.authored_through is not None:
                try:
                    authored = datetime.fromisoformat(commit.date)
                except ValueError as error:
                    raise GitError("Git returned an unsupported author date.") from error
                if query.authored_from is not None and authored < query.authored_from:
                    continue
                if query.authored_through is not None and authored > query.authored_through:
                    continue
                matched.append("author-date")
            matches.append((index, tuple(matched), refs))
        if self._investigation_shallow() != scope.shallow_fingerprint:
            raise GitError(
                "The shallow history boundary changed while searching; refresh explicitly."
            )
        return commits, keys, tuple(matches), has_more

    def _investigation_revision(self, scope: InvestigationScope, oid: str) -> str:
        if not isinstance(oid, str) or not re.fullmatch(
            r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", oid
        ):
            raise GitError("Investigation revisions must be full commit OIDs.")
        resolved = self._resolve_commit(oid, label="Investigation revision")
        if resolved != oid.lower():
            raise GitError("Investigation revision must name a commit object, not a tag object.")
        for root in scope.root_oids:
            if root == resolved:
                return resolved
            result = self._git(
                ["--no-replace-objects", "merge-base", "--is-ancestor", resolved, root],
                ok_codes=(0, 1),
                operation="check investigation revision scope",
            )
            if result.returncode == 0:
                return resolved
        raise GitError("The requested commit is outside the captured investigation scope.")

    def _investigation_ready(self, scope: InvestigationScope) -> ScopeState:
        state = self.investigation_status(scope)
        if not state.available:
            raise GitError(state.reason)
        if self._investigation_shallow() != scope.shallow_fingerprint:
            raise GitError("The shallow history boundary changed; refresh explicitly.")
        return state

    def investigation_compare(
        self,
        scope: InvestigationScope,
        left_oid: str,
        right_oid: str,
        intent: ComparisonIntent,
    ) -> ComparisonResult:
        """Compare captured trees with explicit semantics; never substitute a base."""
        if intent not in ("branch-review", "endpoints"):
            raise ValueError("Comparison intent must be 'branch-review' or 'endpoints'.")
        self._investigation_ready(scope)
        left = self._investigation_revision(scope, left_oid)
        right = self._investigation_revision(scope, right_oid)
        base = None
        if intent == "branch-review":
            if scope.shallow_fingerprint:
                raise GitError(
                    "Branch review is unavailable in shallow history; use explicit endpoint comparison."
                )
            result = self._git(
                ["--no-replace-objects", "merge-base", "--all", left, right],
                ok_codes=(0, 1),
                operation="find comparison merge bases",
            )
            bases = _decode(result.stdout).splitlines()
            if len(bases) != 1:
                raise GitError(
                    "Branch review requires one unique merge base; use explicit endpoint comparison."
                )
            base = bases[0]
        start = base or left
        options = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M"]
        names = self._investigation_read(
            [*options, "--name-status", "-z", start, right, "--"], operation="read comparison files"
        )
        statistics = self._investigation_read(
            [*options, "--stat", start, right, "--"], operation="read comparison statistics"
        )
        patch = self._git_diff(
            ["--no-replace-objects", *options, start, right, "--"],
            operation="read comparison patch",
        ).stdout
        state = self._investigation_ready(scope)
        return ComparisonResult(
            scope.scope_id,
            intent,
            left,
            right,
            base,
            start,
            right,
            self._parse_name_status(names),
            _decode(statistics).rstrip("\n"),
            self._bounded_diff(patch),
            len(patch) > _MAX_DIFF_BYTES,
            state,
        )

    def investigation_file_history(
        self,
        scope: InvestigationScope,
        start_oid: str,
        path: str,
        *,
        offset: int = 0,
        limit: int = 300,
        follow_renames: bool = True,
    ) -> tuple[tuple[FileHistoryEntry, ...], bool, tuple[str, ...], ScopeState]:
        """Read Git's single-file history, explicitly retaining rename limitations."""
        self._investigation_ready(scope)
        revision = self._investigation_revision(scope, start_oid)
        selected = self._path(path)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("File history limit must be between 1 and 1000.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("File history offset must be non-negative.")
        if not isinstance(follow_renames, bool):
            raise ValueError("Follow renames must be a boolean.")
        options = [option for option in self._investigation_log_options() if option != "--no-patch"]
        # --skip can bypass the diff generation that --follow needs to discover
        # earlier names. Stream and discard the prefix instead of losing renames
        # or retaining an offset-sized buffer.
        records: list[tuple[str, list[FileChange]]] = []
        position = -1
        with self._investigation_lines(
            [
                "--literal-pathspecs",
                "log",
                *options,
                "--topo-order",
                "-z",
                "--name-status",
                "-M",
                *(["--follow"] if follow_renames else []),
                f"-n{offset + limit + 1}",
                "--format=%x00%H",
            ],
            (revision,),
            paths=(selected,),
            nul=True,
        ) as raw_tokens:
            tokens = (_decode(token) for token in raw_tokens)
            for token in tokens:
                if not token:
                    continue
                if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", token):
                    position += 1
                    if position >= offset:
                        records.append((token, []))
                    continue
                status = token.removeprefix("\n")
                if position < 0 or not re.fullmatch(r"[ACDMRTUXB][0-9]*", status):
                    raise GitError("Git returned unsupported file-history records.")
                count = 2 if status[0] in "RC" else 1
                names = [next(tokens, "") for _ in range(count)]
                if not all(names):
                    raise GitError("Git returned incomplete file history.")
                if position >= offset:
                    records[-1][1].append(
                        FileChange(
                            names[-1], index=status[0], original_path=names[0] if count == 2 else ""
                        )
                    )
        if not records and offset == 0:
            raise GitError(
                "No file history is available for that literal path at the captured revision."
            )
        has_more = len(records) > limit
        records = records[:limit]
        commits = self._investigation_commits(scope, tuple(oid for oid, _ in records))
        entries = []
        for commit, (_, changes) in zip(commits, records, strict=True):
            if len(changes) != 1:
                raise GitError(
                    "File history needs one explicit file; this Git history representation is unsupported."
                )
            change = changes[0]
            entries.append(
                FileHistoryEntry(commit, change.path, change.original_path, change.index)
            )
        limitations = ("rename-heuristic",) if follow_renames else ()
        if scope.shallow_fingerprint:
            limitations += ("shallow-history",)
        return tuple(entries), has_more, limitations, self._investigation_ready(scope)

    def investigation_blame(
        self,
        scope: InvestigationScope,
        start_oid: str,
        path: str,
        *,
        offset: int = 0,
        limit: int = 300,
    ) -> tuple[tuple[BlameLine, ...], bool, tuple[str, ...], ScopeState]:
        """Read a bounded porcelain blame range, refusing binary/oversized blobs."""
        self._investigation_ready(scope)
        revision = self._investigation_revision(scope, start_oid)
        selected = PurePosixPath(self._path(path)).as_posix()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("Blame limit must be between 1 and 1000.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Blame offset must be non-negative.")
        tree = self._investigation_read(
            ["--literal-pathspecs", "ls-tree", "--full-tree", "-z", revision, "--", selected],
            operation="inspect blame file",
        ).split(b"\x00")
        if len(tree) != 2 or tree[-1] != b"":
            raise GitError("Blame requires an existing regular file at the captured revision.")
        try:
            metadata, actual_path = tree[0].split(b"\t", 1)
            mode, kind, raw_oid = metadata.split()
        except ValueError as error:
            raise GitError("Git returned unsupported blame file metadata.") from error
        if (
            kind != b"blob"
            or mode not in (b"100644", b"100755")
            or _decode(actual_path) != selected
        ):
            raise GitError("Blame is available only for one regular text file.")
        oid = raw_oid.decode("ascii")
        size = int(
            self._investigation_read(["cat-file", "-s", oid], operation="inspect blame size")
        )
        if size > _MAX_DIFF_BYTES:
            raise GitError(
                "Blame is unavailable for oversized files (limit: 2 MiB); use Git externally."
            )
        content = self._investigation_read(["cat-file", "blob", oid], operation="read blame file")
        if b"\x00" in content:
            raise GitError("Blame is unavailable for binary files.")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise GitError(
                "Blame requires UTF-8 text; this file's encoding is unsupported."
            ) from error
        total = content.count(b"\n") + bool(content and not content.endswith(b"\n"))
        through = min(offset + limit, total)
        limitations = ("rename-heuristic",) + (
            ("shallow-history",) if scope.shallow_fingerprint else ()
        )
        if offset >= total:
            return (), False, limitations, self._investigation_ready(scope)
        data = self._investigation_read(
            [
                "--literal-pathspecs",
                "blame",
                "--line-porcelain",
                "--no-textconv",
                "--encoding=UTF-8",
                f"-L{offset + 1},{through}",
                revision,
                "--",
                selected,
            ],
            operation="read blame lines",
        )
        lines: list[BlameLine] = []
        header: list[str] | None = None
        fields: dict[bytes, bytes] = {}
        for raw in data.split(b"\n"):
            if header is None:
                if not raw:
                    continue
                header = _decode(raw).split()
                if len(header) not in (3, 4) or not re.fullmatch(
                    r"(?:[0-9a-f]{40}|[0-9a-f]{64})", header[0]
                ):
                    raise GitError("Git returned unsupported blame records.")
                fields = {}
            elif raw.startswith(b"\t"):
                try:
                    filename = fields[b"filename"]
                    if filename.startswith(b'"'):
                        if not filename.endswith(b'"'):
                            raise ValueError("Malformed quoted filename")
                        decoded_filename = codecs.escape_decode(filename[1:-1])[0]
                        if not isinstance(decoded_filename, bytes):
                            raise ValueError("Malformed quoted filename")
                        filename = decoded_filename
                    zone = fields[b"author-tz"].decode("ascii")
                    if not re.fullmatch(r"[+-][0-9]{4}", zone):
                        raise ValueError("Malformed timezone")
                    minutes = (int(zone[1:3]) * 60 + int(zone[3:])) * (-1 if zone[0] == "-" else 1)
                    date = datetime.fromtimestamp(
                        int(fields[b"author-time"]), timezone(timedelta(minutes=minutes))
                    )
                    original_line, final_line = int(header[1]), int(header[2])
                    if original_line < 1 or final_line != offset + len(lines) + 1:
                        raise ValueError("Unexpected line numbers")
                    lines.append(
                        BlameLine(
                            header[0],
                            _decode(filename),
                            original_line,
                            final_line,
                            _decode(fields[b"author"]),
                            _decode(fields[b"author-mail"]).removeprefix("<").removesuffix(">"),
                            _commit_date(date.isoformat()),
                            _decode(raw[1:]),
                            b"boundary" in fields,
                        )
                    )
                except (KeyError, ValueError, OverflowError, UnicodeError) as error:
                    raise GitError(
                        "Git returned incomplete or unsupported blame metadata."
                    ) from error
                header = None
            else:
                name, _, value = raw.partition(b" ")
                fields[name] = value
        if header is not None or len(lines) != through - offset:
            raise GitError("Git returned incomplete blame output.")
        return tuple(lines), through < total, limitations, self._investigation_ready(scope)

    def _history(
        self, limit: int, ref: str, refs: tuple[Ref, ...], head: str
    ) -> tuple[tuple[HistoryLine, ...], bool]:
        if not head and not refs:
            return (), False
        revision_args: list[str]
        if ref:
            revision_args = [self._resolve_commit(ref, label="History ref")]
        else:
            revision_args = ["--all"]
            if head and not any(item.oid == head for item in refs):
                revision_args.append(head)

        # Scan only the cheap timestamp/OID index for every reachable commit.
        # Git's traversal position is the deterministic tie-breaker for commits
        # with the same author timestamp, independent of the checked-out ref.
        index_result = self._git(
            [
                "log",
                "--author-date-order",
                "--no-show-signature",
                "--no-patch",
                "--no-decorate",
                "--no-notes",
                "--no-color",
                "--format=%at%x00%H",
                *revision_args,
            ],
            operation="read history index",
        )

        def index_records():
            for position, raw_line in enumerate(BytesIO(index_result.stdout)):
                raw_line = raw_line.rstrip(b"\n")
                if not raw_line:
                    continue
                fields = raw_line.split(b"\x00", 1)
                if len(fields) != 2:
                    raise GitError("Git returned a malformed history index.")
                timestamp, raw_oid = fields
                try:
                    yield int(timestamp), position, raw_oid.decode("ascii")
                except (UnicodeDecodeError, ValueError) as error:
                    raise GitError("Git returned a malformed history index.") from error

        selected = heapq.nsmallest(
            limit + 1,
            index_records(),
            key=lambda item: (-item[0], item[1]),
        )
        has_more = len(selected) > limit
        selected_oids = [item[2] for item in selected[:limit]]
        if not selected_oids:
            return (), has_more

        # Fetch full metadata only for the bounded selection. Do not depend on
        # --no-walk preserving stdin order: map records back to the chosen OIDs.
        fmt = "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%s"
        metadata_result = self._git(
            [
                "log",
                "--no-walk=unsorted",
                "--no-show-signature",
                "--no-patch",
                "--no-decorate",
                "--no-notes",
                "--no-color",
                f"--format={fmt}",
                "--stdin",
            ],
            operation="read history metadata",
            input_bytes=("\n".join(selected_oids) + "\n").encode("ascii"),
        )

        names_by_oid: dict[str, list[str]] = {}
        for item in refs:
            names_by_oid.setdefault(item.oid, []).append(item.name)
        if head:
            names_by_oid.setdefault(head, []).append("HEAD")

        commits_by_oid: dict[str, Commit] = {}
        # str.splitlines() treats the record-separator marker (0x1e) as a
        # line boundary, so split only on Git's actual output newline.
        for raw_line in _decode(metadata_result.stdout).split("\n"):
            if not raw_line.startswith("\x1e"):
                continue
            fields = raw_line[1:].split("\x1f", 5)
            if len(fields) != 6:
                continue
            oid, parents, author, email, date, subject = fields
            commits_by_oid[oid] = Commit(
                oid=oid,
                parents=tuple(parents.split()) if parents else (),
                author=author,
                email=email,
                date=_commit_date(date),
                subject=subject,
                refs=tuple(dict.fromkeys(names_by_oid.get(oid, ()))),
            )

        missing = [oid for oid in selected_oids if oid not in commits_by_oid]
        if missing:
            raise GitError("Git returned incomplete history metadata.")
        return tuple(
            HistoryLine(graph="", commit=commits_by_oid[oid]) for oid in selected_oids
        ), has_more

    def snapshot(
        self, limit: int = 300, ref: str = "", *, include_history: bool = True
    ) -> Snapshot:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_HISTORY:
            raise GitError(f"History limit must be between 1 and {_MAX_HISTORY}.")
        head = self._head()
        branch = self._branch()
        refs = self._refs()
        upstream = self._upstream(branch)
        ahead, behind = self._ahead_behind(head, upstream)
        history, has_more = (
            self._history(limit, ref, refs, head) if include_history else ((), False)
        )
        return Snapshot(
            root=self.root,
            branch=branch,
            head=head,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
            refs=refs,
            remotes=self._remotes(),
            stashes=self._stashes(),
            files=self._status(),
            history=history,
            operation=self._operation(),
            has_more=has_more,
            worktrees=self.worktrees(),
        )

    def _commit(self, oid: str) -> Commit:
        result = self._git(
            [
                "show",
                "-s",
                "--no-show-signature",
                "--format=%H%x00%P%x00%an%x00%ae%x00%aI%x00%s",
                oid,
            ],
            operation="read commit",
        )
        fields = _decode(result.stdout).rstrip("\n").split("\x00", 5)
        if len(fields) != 6:
            raise GitError("Git returned malformed commit metadata.")
        commit_oid, parents, author, email, date, subject = fields
        refs = tuple(item.name for item in self._refs() if item.oid == commit_oid)
        return Commit(
            oid=commit_oid,
            parents=tuple(parents.split()) if parents else (),
            author=author,
            email=email,
            date=_commit_date(date),
            subject=subject,
            refs=refs,
        )

    def _empty_tree(self) -> str:
        result = self._git(
            ["hash-object", "-t", "tree", "--stdin"],
            operation="create empty tree identifier",
            input_bytes=b"",
        )
        return _decode(result.stdout).strip()

    def _commit_range(self, oid: str, parents: tuple[str, ...]) -> tuple[str, str]:
        return (parents[0] if parents else self._empty_tree(), oid)

    @staticmethod
    def _parse_name_status(data: bytes) -> tuple[FileChange, ...]:
        fields = _decode(data).split("\x00")
        changes: list[FileChange] = []
        index = 0
        while index < len(fields):
            status = fields[index]
            index += 1
            if not status:
                continue
            kind = status[0]
            if kind in {"R", "C"}:
                if index + 1 >= len(fields):
                    break
                original, path = fields[index], fields[index + 1]
                index += 2
                changes.append(FileChange(path=path, index=kind, original_path=original))
            else:
                if index >= len(fields):
                    break
                path = fields[index]
                index += 1
                changes.append(FileChange(path=path, index=kind))
        return tuple(changes)

    def detail(self, oid: str) -> CommitDetail:
        resolved = self._resolve_commit(oid, label="Commit")
        commit = self._commit(resolved)
        old, new = self._commit_range(resolved, commit.parents)
        names = self._git(
            ["diff", "--no-textconv", "--no-ext-diff", "--name-status", "-z", "-M", old, new],
            operation="read commit files",
        )
        stat = self._git(
            ["diff", "--no-textconv", "--no-ext-diff", "--stat", "--no-color", old, new],
            operation="read commit statistics",
        )
        message = self._git(
            ["show", "-s", "--no-show-signature", "--format=%B", resolved],
            operation="read commit message",
        )
        return CommitDetail(
            commit=commit,
            message=_decode(message.stdout).rstrip("\n"),
            files=self._parse_name_status(names.stdout),
            stat=_decode(stat.stdout).rstrip("\n"),
        )

    def _diff_paths(self, path: str, oid: str = "") -> list[str]:
        selected = self._path(path)
        related = [selected]
        changes: tuple[FileChange, ...]
        if oid:
            commit = self._commit(oid)
            old, new = self._commit_range(oid, commit.parents)
            result = self._git(
                ["diff", "--no-textconv", "--no-ext-diff", "--name-status", "-z", "-M", old, new]
            )
            changes = self._parse_name_status(result.stdout)
        else:
            changes = self._status()
        for change in changes:
            if change.path == selected and change.original_path:
                related.append(change.original_path)
            elif change.original_path == selected:
                related.append(change.path)
        return list(dict.fromkeys(related))

    @staticmethod
    def _bounded_diff(data: bytes) -> str:
        if len(data) <= _MAX_DIFF_BYTES:
            return _decode(data)
        clipped = data[:_MAX_DIFF_BYTES]
        return _decode(clipped) + "\n\n[diff truncated by Rhizome]\n"

    def diff(self, path: str = "", *, staged: bool = False, oid: str = "") -> str:
        resolved = self._resolve_commit(oid, label="Commit") if oid else ""
        paths = self._diff_paths(path, resolved) if path else []
        if resolved:
            commit = self._commit(resolved)
            old, new = self._commit_range(resolved, commit.parents)
            args = [
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                old,
                new,
            ]
            if paths:
                args.extend(["--", *paths])
            result = self._git_diff(args, literal_paths=bool(paths), operation="read commit diff")
            return self._bounded_diff(result.stdout)

        args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv"]
        if staged:
            args.append("--cached")
        if paths:
            args.extend(["--", *paths])
        result = self._git_diff(args, literal_paths=bool(paths), operation="read diff")
        output = bytearray(result.stdout)

        # Git intentionally excludes untracked files from normal diffs. Add them
        # as no-index patches while retaining safe, literal argv operands.
        if not staged:
            untracked = [
                change.path
                for change in self._status()
                if change.index == "?" and (not path or change.path == path)
            ]
            for untracked_path in untracked:
                patch = self._git_diff(
                    [
                        "diff",
                        "--no-index",
                        "--no-color",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--",
                        os.devnull,
                        untracked_path,
                    ],
                    ok_codes=(0, 1),
                    operation="read untracked file diff",
                )
                if output and patch.stdout and len(output) <= _MAX_DIFF_BYTES:
                    output.extend(b"\n")
                remaining = _MAX_DIFF_BYTES + 1 - len(output)
                if remaining > 0:
                    output.extend(patch.stdout[:remaining])
                if len(output) > _MAX_DIFF_BYTES:
                    break
        return self._bounded_diff(bytes(output))

    def _exact_stash(self, ref: str, expected_oid: str = "") -> str:
        _safe_value(ref, "Stash ref")
        stash = next((item for item in self._stashes() if item.ref == ref), None)
        if stash is None:
            raise GitError(f"Unknown stash ref: {ref}")
        if expected_oid and stash.oid != expected_oid:
            raise GitError("The selected stash changed. Refresh and select it again.")
        return ref

    def _local_branches(self) -> set[str]:
        return {ref.name for ref in self._refs() if ref.kind == "local"}

    def _canonical_upstream(self, branch: str) -> tuple[str, str, str]:
        """Return full tracking ref, owner and remote source without DWIM ambiguity."""
        full_name = f"refs/heads/{branch}"
        result = self._git(
            [
                "for-each-ref",
                "--format=%(refname)%00%(upstream)%00%(upstream:remotename)%00%(upstream:remoteref)",
                full_name,
            ],
            operation="read checkout upstream",
        )
        for line in _decode(result.stdout).splitlines():
            fields = line.split("\x00")
            if len(fields) == 4 and fields[0] == full_name:
                return fields[1], fields[2], fields[3]
        return "", "", ""

    @staticmethod
    def _refspec_match(pattern: str, ref: str) -> str | None:
        """Return a wildcard capture when *pattern* matches a full ref."""
        wildcard_count = pattern.count("*")
        if wildcard_count == 0:
            return "" if pattern == ref else None
        if wildcard_count != 1:
            return None
        prefix, suffix = pattern.split("*", 1)
        if not ref.startswith(prefix) or not ref.endswith(suffix):
            return None
        if len(ref) < len(prefix) + len(suffix):
            return None
        end = len(ref) - len(suffix) if suffix else len(ref)
        return ref[len(prefix) : end]

    def _valid_refspec_pattern(self, pattern: str) -> bool:
        result = self._git(
            ["check-ref-format", "--refspec-pattern", pattern],
            ok_codes=(0, 1),
            operation="validate remote fetch mapping",
        )
        return result.returncode == 0

    def _fetch_config_values(self, key: str, *, boolean: bool = False) -> tuple[str, ...]:
        command = ["config", "--includes", "--null"]
        if boolean:
            command.append("--type=bool")
        command.extend(["--get-all", key])
        result = self._git(
            command,
            ok_codes=(0, 1),
            operation="read effective fetch configuration",
        )
        if result.returncode == 1:
            return ()
        values = _decode(result.stdout).split("\x00")
        if values and values[-1] == "":
            values.pop()
        return tuple(values)

    def _fetch_boolean(self, key: str) -> bool | None:
        values = self._fetch_config_values(key, boolean=True)
        if not values:
            return None
        value = values[-1].casefold()
        if value not in {"true", "false"}:  # Defensive: --type=bool canonicalizes values.
            raise GitError(f"Fetch configuration {key} is not a valid boolean.")
        return value == "true"

    @staticmethod
    def _fetch_patterns_overlap(left: str, right: str) -> bool:
        if "*" not in left:
            return Repository._refspec_match(right, left) is not None
        if "*" not in right:
            return Repository._refspec_match(left, right) is not None
        left_prefix, left_suffix = left.split("*", 1)
        right_prefix, right_suffix = right.split("*", 1)
        return (left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)) and (
            left_suffix.endswith(right_suffix) or right_suffix.endswith(left_suffix)
        )

    def _valid_fetch_source(self, source: str, *, negative: bool = False) -> bool:
        if not source or source.count("*") > 1:
            return False
        if source == "HEAD":
            return not negative
        if not negative and "*" not in source and re.fullmatch(r"[0-9a-fA-F]{40,64}", source):
            return True
        return source.startswith("refs/") and self._valid_refspec_pattern(source)

    def _parse_fetch_refspecs(self, remote: str) -> tuple[_FetchRefspec, ...]:
        raw_refspecs = self._fetch_config_values(f"remote.{remote}.fetch")
        parsed: list[_FetchRefspec] = []
        destinations: list[tuple[str, str]] = []
        for raw in raw_refspecs:
            if not raw or _CONTROL_RE.search(raw):
                raise GitError(f"Remote {remote} has a malformed fetch refspec.")
            if raw.startswith("^"):
                source = raw[1:]
                if ":" in source or not self._valid_fetch_source(source, negative=True):
                    raise GitError(f"Remote {remote} has a malformed negative fetch refspec: {raw}")
                parsed.append(_FetchRefspec(raw=raw, source=source))
                continue

            value = raw[1:] if raw.startswith("+") else raw
            if not value or value.startswith("+") or value.count(":") > 1:
                raise GitError(f"Remote {remote} has a malformed fetch refspec: {raw}")
            source, separator, destination = value.partition(":")
            if not self._valid_fetch_source(source):
                raise GitError(f"Remote {remote} has a malformed fetch source: {raw}")
            if not separator:
                if "*" in source:
                    raise GitError(
                        f"Remote {remote} has an unsupported wildcard source-only fetch refspec: {raw}"
                    )
                parsed.append(_FetchRefspec(raw=raw, source=source))
                continue
            if (
                not destination
                or source.count("*") != destination.count("*")
                or destination.count("*") > 1
                or not self._valid_refspec_pattern(destination)
            ):
                raise GitError(f"Remote {remote} has a malformed fetch refspec: {raw}")
            if not destination.startswith("refs/remotes/"):
                raise GitError(
                    f"Remote {remote} fetch destination {destination} is outside the "
                    "remote-tracking cache namespace."
                )
            for prior_raw, prior_destination in destinations:
                if (
                    self._fetch_patterns_overlap(prior_destination, destination)
                    and prior_raw != raw
                ):
                    raise GitError(
                        f"Remote {remote} has ambiguous fetch destinations "
                        f"{prior_destination} and {destination}."
                    )
            destinations.append((raw, destination))
            parsed.append(_FetchRefspec(raw=raw, source=source, destination=destination))
        if parsed and all(raw.raw.startswith("^") for raw in parsed):
            raise GitError(
                f"Remote {remote} has negative fetch refspecs without a positive source."
            )
        return tuple(parsed)

    def _loose_fetch_symbolic_refs(self) -> dict[str, str]:
        storage = self._fetch_config_values("extensions.refStorage")
        if storage and storage[-1].casefold() != "files":
            raise GitError(
                "Fetch destination validation does not support this repository ref storage."
            )
        # An unreadable user-ref directory can hide an inbound cache alias.
        # Inspect all loose storage, while retaining immediate cache targets.
        root = self._git_path("refs")
        symbolic: dict[str, str] = {}
        if not root.exists():
            return symbolic
        if root.is_symlink() or not root.is_dir():
            raise GitError("Git ref storage is not a safe directory.")

        def unreadable(error: OSError) -> None:
            raise error

        try:
            for directory, names, files in os.walk(root, followlinks=False, onerror=unreadable):
                directory_path = Path(directory)
                for name in names:
                    if (directory_path / name).is_symlink():
                        raise GitError("Git ref storage contains an unsupported symlink.")
                for name in files:
                    path = directory_path / name
                    if path.is_symlink() or not path.is_file():
                        raise GitError("Git ref storage contains an unsupported ref file.")
                    with path.open("rb") as ref_file:
                        data = ref_file.read(4097)
                    relative = path.relative_to(root).as_posix()
                    refname = f"refs/{relative}"
                    if not data.startswith(b"ref: ") or not refname.startswith("refs/remotes/"):
                        continue
                    if len(data) > 4096:
                        raise GitError("Remote-tracking symbolic ref is malformed.")
                    target = _decode(data[5:]).removesuffix("\n")
                    if (
                        _CONTROL_RE.search(target)
                        or not self._valid_refspec_pattern(refname)
                        or not self._valid_refspec_pattern(target)
                    ):
                        raise GitError("Remote-tracking symbolic ref is malformed.")
                    symbolic[refname] = target
        except OSError as error:
            raise GitError(f"Could not inspect fetch symbolic refs: {error}") from error
        return symbolic

    def _fetch_symbolic_refs(self) -> tuple[dict[str, str], dict[str, str]]:
        symbolic = self._loose_fetch_symbolic_refs()
        aliases: dict[str, str] = {}
        result = self._git(
            ["for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)"],
            operation="inspect fetch symbolic refs",
        )
        for line in _decode(result.stdout).splitlines():
            fields = line.split("\x00")
            if len(fields) != 3:
                raise GitError("Git returned an incomplete symbolic-ref inventory.")
            refname, oid, target = fields
            if not target:
                continue
            if refname.startswith("refs/remotes/"):
                # Keep the immediate loose target, including dangling chains;
                # for-each-ref reports the terminal referent of resolving refs.
                symbolic.setdefault(refname, target)
            elif oid and oid.strip("0"):
                aliases[refname] = target
        return symbolic, aliases

    def _validate_fetch_symbolic_destinations(
        self, remote: str, refspecs: tuple[_FetchRefspec, ...]
    ) -> None:
        destinations = tuple(item.destination for item in refspecs if item.destination)
        if not destinations:
            return
        symbolic, aliases = self._fetch_symbolic_refs()
        terminal_destinations: set[str] = set()
        candidates = {
            refname
            for refname in symbolic
            if any(self._refspec_match(pattern, refname) is not None for pattern in destinations)
        }
        candidates.update(pattern for pattern in destinations if "*" not in pattern)
        for destination in candidates:
            current = destination
            seen: set[str] = set()
            while True:
                if current in seen:
                    raise GitError(
                        f"Remote {remote} fetch destination {destination} has a symbolic ref cycle."
                    )
                seen.add(current)
                target = symbolic.get(current)
                if target is None:
                    result = self._git(
                        ["symbolic-ref", "--quiet", current],
                        ok_codes=(0, 1),
                        operation="inspect fetch symbolic destination",
                    )
                    if result.returncode == 1:
                        terminal_destinations.add(current)
                        break
                    target = _decode(result.stdout).removesuffix("\n")
                    symbolic[current] = target
                if not target.startswith("refs/remotes/"):
                    raise GitError(
                        f"Remote {remote} fetch symbolic destination {destination} resolves "
                        f"outside the remote-tracking cache namespace to {target}."
                    )
                current = target
        for refname, target in aliases.items():
            if target in terminal_destinations or any(
                self._refspec_match(pattern, target) is not None for pattern in destinations
            ):
                raise GitError(
                    f"Remote {remote} fetch could change user-owned symbolic ref {refname} "
                    f"through its remote-tracking target {target}. Inspect it with external Git."
                )

    def _fetch_remote_scope(self, remote: str) -> _FetchRemoteScope:
        refspecs = self._parse_fetch_refspecs(remote)
        remote_prune = self._fetch_boolean(f"remote.{remote}.pruneTags")
        global_prune = self._fetch_boolean("fetch.pruneTags")
        effective_prune = remote_prune if remote_prune is not None else global_prune
        if effective_prune:
            raise GitError(
                f"Remote {remote} has effective tag pruning enabled; Rhizome fetch will not "
                "delete user-owned tags."
            )
        tag_options = self._fetch_config_values(f"remote.{remote}.tagOpt")
        if tag_options and (
            any(value not in {"--tags", "--no-tags"} for value in tag_options)
            or tag_options[-1] != "--no-tags"
        ):
            raise GitError(
                f"Remote {remote} has explicit tag fetch behavior; only native default "
                "non-force tag auto-follow or explicit --no-tags is supported."
            )
        self._validate_fetch_symbolic_destinations(remote, refspecs)
        return _FetchRemoteScope(remote=remote, refspecs=refspecs, no_tags=bool(tag_options))

    def _validate_fetch_scope(self, requested_remote: str = "") -> tuple[_FetchRemoteScope, ...]:
        """Return a pinned safe fetch scope for coordinator planning and execution.

        This is a fail-closed preflight, not an atomic lock against another process
        changing refs or configuration after validation.
        """
        if requested_remote:
            names = (self._remote(requested_remote),)
        else:
            selected: list[str] = []
            for name in self._remote_names():
                skip_default = self._fetch_boolean(f"remote.{name}.skipDefaultUpdate")
                skip_all = self._fetch_boolean(f"remote.{name}.skipFetchAll")
                if skip_default or skip_all:
                    continue
                selected.append(name)
            names = tuple(selected)
        scopes = tuple(self._fetch_remote_scope(name) for name in names)
        destinations: list[tuple[str, str]] = []
        for scope in scopes:
            for refspec in scope.refspecs:
                if not refspec.destination:
                    continue
                for prior_remote, prior_destination in destinations:
                    if prior_remote != scope.remote and self._fetch_patterns_overlap(
                        prior_destination, refspec.destination
                    ):
                        raise GitError(
                            f"Remotes {prior_remote} and {scope.remote} have ambiguous overlapping "
                            f"fetch destination {refspec.destination}."
                        )
                destinations.append((scope.remote, refspec.destination))
        return scopes

    def _execute_fetch(
        self,
        requested_remote: str = "",
        *,
        disable_submodule_recursion: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        scopes = self._validate_fetch_scope(requested_remote)
        result = subprocess.CompletedProcess(["git", "fetch"], 0, b"", b"")
        for scope in scopes:
            command = [
                "-c",
                "fetch.pruneTags=false",
                "-c",
                f"remote.{scope.remote}.pruneTags=false",
            ]
            if disable_submodule_recursion:
                command.extend(["-c", "submodule.recurse=false"])
            command.extend(["fetch", "--no-force", "--no-prune-tags"])
            if disable_submodule_recursion:
                command.append("--no-recurse-submodules")
            command.append("--refmap=")
            if scope.no_tags:
                command.append("--no-tags")
            pinned_refspecs = [item.raw for item in scope.refspecs] or ["HEAD"]
            command.extend(["--", scope.remote, *pinned_refspecs])
            result = self._git(command, timeout=_NETWORK_TIMEOUT, operation="fetch")
        return result

    def _remote_source_ref(self, remote: str, tracking_ref: str) -> str:
        """Reverse exactly one remote fetch refspec for *tracking_ref*."""
        result = self._git(
            ["config", "--null", "--get-all", f"remote.{remote}.fetch"],
            ok_codes=(0, 1),
            operation="read remote fetch mappings",
        )
        refspecs = [value for value in _decode(result.stdout).split("\x00") if value]
        positive: list[tuple[str, str]] = []
        negative: list[str] = []
        for refspec in refspecs:
            value = refspec.removeprefix("+")
            if value.startswith("^"):
                pattern = value[1:]
                if self._valid_refspec_pattern(pattern):
                    negative.append(pattern)
                continue
            source, separator, destination = value.partition(":")
            if (
                not separator
                or source.count("*") != destination.count("*")
                or not self._valid_refspec_pattern(source)
                or not self._valid_refspec_pattern(destination)
            ):
                continue
            positive.append((source, destination))

        sources: set[str] = set()
        for source, destination in positive:
            capture = self._refspec_match(destination, tracking_ref)
            if capture is None:
                continue
            mapped_source = source.replace("*", capture)
            if any(self._refspec_match(pattern, mapped_source) is not None for pattern in negative):
                continue
            if self._valid_refspec_pattern(mapped_source):
                sources.add(mapped_source)

        if not sources:
            raise GitError(
                f"No fetch refspec for remote {remote} maps to {tracking_ref}; "
                "checkout would not have a valid upstream."
            )
        if len(sources) != 1:
            raise GitError(
                f"Remote {remote} has ambiguous fetch refspecs for {tracking_ref}; "
                "checkout was not changed."
            )
        source = sources.pop()
        destinations = set()
        for source_pattern, destination in positive:
            capture = self._refspec_match(source_pattern, source)
            if capture is not None:
                destinations.add(destination.replace("*", capture))
        if destinations != {tracking_ref}:
            raise GitError(
                f"Remote {remote} has ambiguous fetch refspecs for source {source}; "
                f"it does not map uniquely back to {tracking_ref}. Checkout was not changed."
            )
        return source

    def _exact_remote_tracking_ref(self, full_ref: str, remote: str) -> tuple[Ref, str]:
        if not full_ref.startswith("refs/remotes/"):
            raise GitError(f"Remote branch must be a full refs/remotes ref: {full_ref}")
        configured_remote = self._remote(remote)
        selected = next(
            (ref for ref in self._refs() if ref.kind == "remote" and ref.full_name == full_ref),
            None,
        )
        if selected is None:
            raise GitError(f"Unknown remote branch: {full_ref}")
        if not selected.remote:
            raise GitError(f"Cannot determine the configured remote owner of {full_ref}.")
        if selected.remote != configured_remote:
            raise GitError(
                f"Remote branch {full_ref} is owned by {selected.remote}, "
                f"not remote {configured_remote}."
            )
        source = self._remote_source_ref(configured_remote, full_ref)
        return selected, source

    def branch_remote_target(self, full_ref: str) -> tuple[str, str]:
        """Return the exact remote and source branch represented by *full_ref*."""
        _safe_value(full_ref, "Branch ref")
        if full_ref.startswith("refs/heads/"):
            branch = self._local_branch_name(full_ref)
            if full_ref not in {ref.full_name for ref in self._refs() if ref.kind == "local"}:
                raise GitError(f"Unknown local branch: {full_ref}")
            tracking_ref, remote, source = self._canonical_upstream(branch)
            if not tracking_ref or not remote or not source:
                raise GitError(f"Local branch {full_ref} has no configured remote upstream.")
            configured_remote = self._remote(remote)
            mapped_source = self._remote_source_ref(configured_remote, tracking_ref)
            if source != mapped_source:
                raise GitError(f"Local branch {full_ref} has an inconsistent remote mapping.")
            if not source.startswith("refs/heads/"):
                raise GitError(f"Upstream for {full_ref} is not a remote branch.")
            return configured_remote, source
        if full_ref.startswith("refs/remotes/"):
            selected = next(
                (ref for ref in self._refs() if ref.kind == "remote" and ref.full_name == full_ref),
                None,
            )
            if selected is None:
                raise GitError(f"Unknown remote branch: {full_ref}")
            if not selected.remote:
                raise GitError(f"Cannot determine the configured remote owner of {full_ref}.")
            _, source = self._exact_remote_tracking_ref(full_ref, selected.remote)
            if not source.startswith("refs/heads/"):
                raise GitError(f"Remote ref {full_ref} does not map to a branch source.")
            return selected.remote, source
        raise GitError(f"Unsupported branch ref: {full_ref}")

    def _remote_fetch_url(self, name: str) -> str:
        configured = self._remote(name)
        result = self._git(
            ["remote", "get-url", "--", configured], operation="read pull request remote URL"
        )
        return _decode(result.stdout).rstrip("\n")

    def _hosted_remote(self, name: str) -> HostedRepository | None:
        return hosted_repository(self._remote_fetch_url(name))

    @staticmethod
    def _same_hosted_repository(left: HostedRepository, right: HostedRepository) -> bool:
        if left.provider != right.provider:
            return False
        if left.provider == "GitHub":
            return (left.owner.casefold(), left.repository.casefold()) == (
                right.owner.casefold(),
                right.repository.casefold(),
            )
        if left.provider == "Azure DevOps":
            return (
                left.organization.casefold(),
                left.project.casefold(),
                left.repository.casefold(),
            ) == (
                right.organization.casefold(),
                right.project.casefold(),
                right.repository.casefold(),
            )
        return False

    def open_pull_requests(self) -> tuple[PullRequestListing, ...]:
        """List requests for each distinct supported fetch repository."""
        repositories: list[tuple[str, HostedRepository]] = []
        for remote in sorted(self._remote_names()):
            repository = self._hosted_remote(remote)
            if repository is None:
                continue
            if any(
                self._same_hosted_repository(repository, existing) for _, existing in repositories
            ):
                continue
            repositories.append((remote, repository))

        listings: list[PullRequestListing] = []
        for remote, repository in repositories:
            try:
                requests = _list_open_pull_requests(self.root, repository)
            except PullRequestError as error:
                listings.append(
                    PullRequestListing(remote=remote, repository=repository, error=str(error))
                )
            else:
                listings.append(
                    PullRequestListing(
                        remote=remote,
                        repository=repository,
                        requests=requests,
                    )
                )
        return tuple(listings)

    def _safe_pull_request_remote(self, name: str) -> HostedRepository:
        repository = self._hosted_remote(name)
        if repository is None:
            raise GitError(
                f"Remote {name} is not a supported GitHub.com or Azure DevOps repository."
            )
        push_urls = self._remote_push_urls(name)
        if len(push_urls) != 1:
            if push_urls:
                raise GitError(
                    f"Remote {name} has multiple push destinations; configure exactly one "
                    "before creating a pull request."
                )
            raise GitError(f"Remote {name} has no push destination.")
        push_repository = hosted_repository(push_urls[0])
        if push_repository is None or not self._same_hosted_repository(push_repository, repository):
            raise GitError(
                f"Remote {name} pushes to a different or unsupported hosted repository; "
                "refusing to create a cross-repository pull request."
            )
        return repository

    def pull_request_plans(self, action: Action) -> tuple[PullRequestPlan, ...]:
        """Derive safe pull-request choices using local repository state only."""
        if action.operation != "create-pr":
            raise GitError("Pull request planning requires a create-pr action.")
        if (
            not action.expected_ref
            or action.expected_ref != action.target
            or not action.expected_oid
            or not action.expected_head
        ):
            raise GitError(
                "Pull request context is incomplete. Refresh and choose the action again."
            )
        self._preflight_context(action)

        branch = self._branch()
        head = self._head()
        if not branch or not head:
            raise GitError("Cannot create a pull request from a detached or unborn HEAD.")
        if action.expected_branch != branch or action.expected_head != head:
            raise GitError("Branch changed. Refresh and choose the action again.")
        if action.target == f"refs/heads/{branch}":
            raise GitError("The pull request target cannot be the current local branch.")

        source_ref = f"refs/heads/{branch}"
        source_upstream = self._canonical_upstream(branch)
        source_owner = ""
        if any(source_upstream) and source_upstream[1] != ".":
            source_owner, source_full = self.branch_remote_target(source_ref)
            source_branch = source_full.removeprefix("refs/heads/")
        else:
            # remote='.' means this branch tracks another local branch. It is a
            # history/integration hint, not evidence that the source is hosted
            # under the tracked branch's name.
            source_branch = self._validate_branch(branch, "Source branch name")

        target_owner = ""
        if action.target.startswith("refs/heads/"):
            target_branch = self._local_branch_name(action.target, "Target branch name")
            if action.target not in {ref.full_name for ref in self._refs() if ref.kind == "local"}:
                raise GitError(f"Unknown local branch: {action.target}")
            target_upstream = self._canonical_upstream(target_branch)
            if any(target_upstream) and target_upstream[1] != ".":
                target_owner, target_full = self.branch_remote_target(action.target)
                target_branch = target_full.removeprefix("refs/heads/")
        elif action.target.startswith("refs/remotes/"):
            target_owner, target_full = self.branch_remote_target(action.target)
            target_branch = target_full.removeprefix("refs/heads/")
        else:
            raise GitError("Pull request target must be a local or remote branch ref.")

        if source_branch == target_branch:
            raise GitError("Pull request source and target hosted branch names must differ.")

        owners = tuple(dict.fromkeys(owner for owner in (source_owner, target_owner) if owner))
        if len(owners) > 1:
            owner_repositories = [self._safe_pull_request_remote(owner) for owner in owners]
            if any(
                not self._same_hosted_repository(item, owner_repositories[0])
                for item in owner_repositories[1:]
            ):
                raise GitError(
                    "Source and target branches belong to different hosted repositories; "
                    "cross-repository and fork pull requests are not supported."
                )
            candidates = (target_owner or source_owner,)
        elif owners:
            candidates = owners
        else:
            candidates = self._remote_names()

        plans: list[PullRequestPlan] = []
        for remote in candidates:
            # With no configured branch owners, unsupported remotes are not PR
            # choices. Owned remotes are checked above and fail explicitly.
            if not owners and self._hosted_remote(remote) is None:
                continue
            repository = self._safe_pull_request_remote(remote)
            plans.append(
                PullRequestPlan(
                    remote=remote,
                    repository=repository,
                    source_branch=source_branch,
                    target_branch=target_branch,
                    guard=action,
                )
            )
        if not plans:
            raise GitError("No supported GitHub.com or Azure DevOps remote is configured.")
        return tuple(plans)

    def create_pull_request(self, plan: PullRequestPlan, title: str, description: str) -> str:
        """Verify live hosted branches and create exactly one pull request."""
        if not title.strip():
            raise PullRequestError("A pull request title is required.")

        current_plans = self.pull_request_plans(plan.guard)
        if plan not in current_plans:
            raise GitError(
                "Pull request repository or branch mapping changed. Refresh and choose it again."
            )

        source_ref = f"refs/heads/{plan.source_branch}"
        target_ref = f"refs/heads/{plan.target_branch}"
        result = self._git(
            ["ls-remote", "--heads", "--", plan.remote, source_ref, target_ref],
            timeout=_NETWORK_TIMEOUT,
            operation="verify pull request branches",
        )
        remote_refs: dict[str, str] = {}
        for line in _decode(result.stdout).splitlines():
            oid, separator, ref = line.partition("\t")
            if (
                separator
                and ref in {source_ref, target_ref}
                and re.fullmatch(r"[0-9a-fA-F]{40,64}", oid)
            ):
                remote_refs[ref] = oid.lower()

        if source_ref not in remote_refs:
            raise GitError(
                f"Source branch {plan.source_branch} is not published on remote {plan.remote}. "
                "Push it first, then try again."
            )
        if target_ref not in remote_refs:
            raise GitError(
                f"Target branch {plan.target_branch} is not published on remote {plan.remote}. "
                "Publish the target branch first, then try again."
            )
        if remote_refs[source_ref] != plan.guard.expected_head.lower():
            raise GitError(
                f"Remote source branch {plan.source_branch} is not at the current HEAD. "
                "Push your latest commits first, or fetch and reconcile if the remote has advanced."
            )

        # Network verification can take time. Recheck both the captured refs and
        # all local remote/mapping choices before handing control to a provider.
        refreshed_plans = self.pull_request_plans(plan.guard)
        if plan not in refreshed_plans:
            raise GitError(
                "Pull request repository or branch mapping changed. Refresh and choose it again."
            )
        return _create_hosted_pull_request(
            self.root,
            plan.repository,
            plan.source_branch,
            plan.target_branch,
            title,
            description,
        )

    def _checkout_remote_ref(
        self,
        selected: Ref,
        local: dict[str, Ref],
        requested_remote: str,
    ) -> subprocess.CompletedProcess[bytes]:
        if requested_remote:
            configured_remote = self._remote(requested_remote)
            if selected.remote != configured_remote:
                owner = selected.remote or "no configured remote"
                raise GitError(
                    f"Checkout target {selected.full_name} is owned by {owner}, "
                    f"not remote {configured_remote}. Refresh and select the branch again."
                )
        if not selected.remote:
            raise GitError(
                f"Cannot determine the configured remote owner of {selected.full_name}; "
                "refresh the repository or configure its remote before checkout."
            )

        owner_prefix = f"{selected.remote}/"
        if not selected.name.startswith(owner_prefix):  # Defensive: Ref ownership is derived here.
            raise GitError(f"Invalid remote branch ownership for {selected.full_name}.")
        local_name = selected.name.removeprefix(owner_prefix)
        self._validate_branch(local_name, "Tracking branch name")
        # Resolve the source before any switch can change HEAD. This also makes
        # reuse reject a tracking destination whose current mapping is no longer
        # unique.
        merge_ref = self._remote_source_ref(selected.remote, selected.full_name)
        existing = local.get(local_name)
        if existing is not None:
            upstream, upstream_remote, upstream_source = self._canonical_upstream(local_name)
            if (upstream, upstream_remote, upstream_source) != (
                selected.full_name,
                selected.remote,
                merge_ref,
            ):
                if upstream:
                    detail = (
                        f"tracks {upstream} via remote {upstream_remote or '(unknown)'} "
                        f"({upstream_source})"
                    )
                else:
                    detail = "has no upstream"
                raise GitError(
                    f"Local branch {existing.full_name} {detail} and cannot be reused for "
                    f"remote ref {selected.full_name} ({selected.remote}: {merge_ref}). "
                    "Choose or create a differently named "
                    "local branch."
                )
            return self._git(["switch", "--", local_name], operation="checkout")

        # Native --track can choose an ambiguous mapping. Record the validated
        # mapping first: a config-lock/write failure must not change HEAD or
        # create an untracked branch that strict reuse would refuse to retry.
        # A failed switch may leave retryable config for a nonexistent branch;
        # never roll back worktree/refs or rewrite the user's whole config.
        self._git(
            [
                "config",
                "--local",
                "--replace-all",
                f"branch.{local_name}.remote",
                selected.remote,
            ],
            operation="configure checkout remote",
        )
        self._git(
            [
                "config",
                "--local",
                "--replace-all",
                f"branch.{local_name}.merge",
                merge_ref,
            ],
            operation="configure checkout upstream",
        )
        return self._git(
            ["switch", "--no-track", "-c", local_name, selected.full_name],
            operation="checkout remote branch",
        )

    def _execute_checkout(
        self, target: str, requested_remote: str = ""
    ) -> subprocess.CompletedProcess[bytes]:
        _safe_value(target, "Checkout target")
        refs = self._refs()
        local = {ref.name: ref for ref in refs if ref.kind == "local"}
        remote = {ref.name: ref for ref in refs if ref.kind == "remote"}
        tags = {ref.name: ref for ref in refs if ref.kind == "tag"}
        local_full = {ref.full_name: ref for ref in local.values()}
        remote_full = {ref.full_name: ref for ref in remote.values()}
        tag_full = {ref.full_name: ref for ref in tags.values()}

        # An explicit owner makes the intended namespace unambiguous even when
        # a local branch has the same display name as the remote-tracking ref.
        if requested_remote:
            selected = (
                remote_full.get(target)
                if target.startswith("refs/remotes/")
                else remote.get(target)
            )
            if selected is None:
                raise GitError(f"Remote checkout target does not exist: {target}")
            return self._checkout_remote_ref(selected, local, requested_remote)

        # Fully qualified names are exact namespace requests. Do not feed a
        # missing one back through revision DWIM, where another namespace could
        # accidentally satisfy it.
        if target.startswith("refs/heads/"):
            selected = local_full.get(target)
            if selected is None:
                raise GitError(f"Unknown local branch: {target}")
            return self._git(["switch", "--", selected.name], operation="checkout")
        if target.startswith("refs/remotes/"):
            selected = remote_full.get(target)
            if selected is None:
                raise GitError(f"Unknown remote branch: {target}")
            return self._checkout_remote_ref(selected, local, "")
        if target.startswith("refs/tags/"):
            selected = tag_full.get(target)
            if selected is None:
                raise GitError(f"Unknown tag: {target}")
            oid = self._resolve_commit(selected.full_name, label="Tag")
            return self._git(["switch", "--detach", oid], operation="checkout tag")

        # Retain the established local/remote/tag preference for ordinary short
        # inputs. Callers can use a full ref (and owner for remotes) to disambiguate.
        if target in local:
            return self._git(["switch", "--", target], operation="checkout")
        if target in remote:
            return self._checkout_remote_ref(remote[target], local, "")
        if target in tags:
            oid = self._resolve_commit(tags[target].full_name, label="Tag")
            return self._git(["switch", "--detach", oid], operation="checkout tag")
        oid = self._resolve_commit(target, label="Checkout target")
        return self._git(["switch", "--detach", oid], operation="checkout commit")

    def _external_path(self, value: str, label: str) -> Path:
        if not value or "\x00" in value:
            raise GitError(f"{label} is required and must not contain NUL bytes.")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.root / path
        try:
            return path.resolve()
        except OSError as error:
            raise GitError(f"Could not resolve {label.lower()}: {error}") from error

    def _read_patch(self, value: str) -> bytes:
        path = self._external_path(value, "Patch filename")
        descriptor = -1
        try:
            # Reject obvious devices/FIFOs before opening. O_NONBLOCK plus a
            # second fd-level check also closes the replacement race without
            # ever waiting for a FIFO writer.
            if not stat.S_ISREG(path.stat().st_mode):
                raise GitError(f"Patch is not a regular file: {path}")
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(path, flags)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise GitError(f"Patch is not a regular file: {path}")
            with os.fdopen(descriptor, "rb") as patch_file:
                descriptor = -1  # fdopen owns and closes it from this point.
                data = patch_file.read(_MAX_PATCH_BYTES + 1)
        except GitError:
            raise
        except OSError as error:
            raise GitError(f"Could not read patch file: {error}") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(data) > _MAX_PATCH_BYTES:
            raise GitError(f"Patch file exceeds the {_MAX_PATCH_BYTES // (1024 * 1024)} MiB limit.")
        return data

    def execute(self, action: Action) -> str:
        """Execute one allowlisted mutation and return a short success message."""
        allowed = {
            "checkout",
            "create-branch",
            "create-worktree",
            "rename-branch",
            "delete-branch",
            "merge",
            "rebase",
            "cherry-pick",
            "revert",
            "reset-soft",
            "reset-mixed",
            "reset-hard",
            "edit-message",
            "tag",
            "delete-tag",
            "stash",
            "stash-apply",
            "stash-pop",
            "stash-drop",
            "stage",
            "unstage",
            "discard",
            "commit",
            "apply-patch",
            "fetch",
            "pull",
            "push",
            "set-upstream",
            "delete-remote-branch",
            "continue",
            "abort",
        }
        operation = action.operation
        if operation not in allowed:
            raise GitError(f"Unsupported Git action: {operation or '(empty)'}")
        if operation == "pull":
            raise GitError("Pull requires the guarded coordinator review path.")

        self._preflight_context(action)
        fallback = f"Git {operation} completed."
        result: subprocess.CompletedProcess[bytes]
        if operation == "checkout":
            result = self._execute_checkout(action.target, action.remote)
        elif operation == "create-branch":
            name = self._validate_branch(action.name)
            target = self._resolve_commit(action.target or "HEAD", label="Branch target")
            result = self._git(["switch", "-c", name, target], operation="create branch")
        elif operation == "create-worktree":
            if len(action.paths) != 1:
                raise GitError("Create worktree requires exactly one destination path.")
            target = self._resolve_commit(action.target, label="Worktree target")
            destination = self._external_path(action.paths[0], "Worktree destination")
            if destination.exists():
                if not destination.is_dir():
                    raise GitError(f"Worktree destination is not a directory: {destination}")
                try:
                    nonempty = next(destination.iterdir(), None) is not None
                except OSError as error:
                    raise GitError(f"Could not inspect worktree destination: {error}") from error
                if nonempty:
                    raise GitError(f"Worktree destination is not empty: {destination}")
            if action.name:
                name = self._validate_branch(action.name, "New worktree branch name")
                command = ["worktree", "add", "-b", name, "--", os.fspath(destination), target]
            else:
                command = ["worktree", "add", "--detach", "--", os.fspath(destination), target]
            result = self._git(command, operation="create worktree")
        elif operation == "rename-branch":
            old = self._local_branch_name(action.target, "Existing branch name")
            new = self._local_branch_name(action.name, "New branch name")
            if old not in self._local_branches():
                raise GitError(f"Unknown local branch: {old}")
            result = self._git(["branch", "-m", old, new], operation="rename branch")
        elif operation == "delete-branch":
            name = self._local_branch_name(action.target)
            if name not in self._local_branches():
                raise GitError(f"Unknown local branch: {name}")
            result = self._git(["branch", "-d", "--", name], operation="delete branch")
        elif operation in {
            "merge",
            "rebase",
            "cherry-pick",
            "revert",
            "reset-soft",
            "reset-mixed",
            "reset-hard",
        }:
            target = self._resolve_commit(action.target, label=f"{operation} target")
            if operation == "rebase" and (
                (action.expected_branch and self._branch() != action.expected_branch)
                or (action.expected_head and self._head() != action.expected_head)
                or (action.expected_oid and target != action.expected_oid)
            ):
                raise GitError("Branch changed. Refresh and choose the rebase again.")
            if operation.startswith("reset-"):
                self._ensure_no_operation_or_conflicts(operation)
            command = {
                "merge": ["merge", "--no-edit", target],
                # Rebase only this branch and never implicitly stash work, even
                # when the user's Git defaults enable those broader behaviors.
                "rebase": [
                    "-c",
                    "rebase.autoStash=false",
                    "-c",
                    "rebase.updateRefs=false",
                    "rebase",
                    target,
                ],
                "cherry-pick": ["cherry-pick", target],
                "revert": ["revert", "--no-edit", target],
                "reset-soft": ["reset", "--soft", target],
                "reset-mixed": ["reset", "--mixed", target],
                "reset-hard": ["reset", "--hard", target],
            }[operation]
            result = self._git(command, operation=operation)
        elif operation == "edit-message":
            if not action.message.strip():
                raise GitError("A commit message is required.")
            target = self._resolve_commit(action.target, label="Commit target")
            head = self._head()
            if (
                not head
                or target != head
                or (action.expected_oid and target != action.expected_oid)
            ):
                raise GitError("The selected commit is no longer the current HEAD.")
            if not self._branch():
                raise GitError("Cannot edit a commit message while HEAD is detached.")
            self._ensure_no_operation_or_conflicts(operation)
            result = self._git(
                ["commit", "--amend", "--only", "-m", action.message],
                operation="edit commit message",
            )
        elif operation == "tag":
            name = self._tag_name(action.name)
            target = self._resolve_commit(action.target or "HEAD", label="Tag target")
            result = self._git(["tag", "--", name, target], operation="create tag")
        elif operation == "delete-tag":
            name = self._tag_name(action.target)
            if name not in {ref.name for ref in self._refs() if ref.kind == "tag"}:
                raise GitError(f"Unknown tag: {name}")
            result = self._git(["tag", "-d", "--", name], operation="delete tag")
        elif operation == "stash":
            command = ["stash", "push", "--include-untracked"]
            if action.message:
                command.extend(["-m", action.message])
            result = self._git(command, operation="stash")
        elif operation in {"stash-apply", "stash-pop", "stash-drop"}:
            ref = self._exact_stash(action.target, action.expected_oid)
            subcommand = operation.removeprefix("stash-")
            result = self._git(["stash", subcommand, "--", ref], operation=operation)
        elif operation == "stage":
            if action.paths:
                paths = self._related_status_paths(self._paths(action.paths))
                result = self._git(
                    ["add", "--", *paths], literal_paths=True, operation="stage files"
                )
            else:
                result = self._git(["add", "--all"], operation="stage all files")
        elif operation == "unstage":
            paths = self._related_status_paths(self._paths(action.paths)) if action.paths else ["."]
            if self._head():
                result = self._git(
                    ["restore", "--staged", "--", *paths],
                    literal_paths=True,
                    operation="unstage files",
                )
            else:
                result = self._git(
                    ["rm", "--cached", "-r", "--ignore-unmatch", "--", *paths],
                    literal_paths=True,
                    operation="unstage files",
                )
        elif operation == "discard":
            if not action.paths:
                raise GitError("Discard requires one or more explicit tracked file paths.")
            paths = self._paths(action.paths)
            for path in paths:
                tracked = self._git(
                    ["ls-files", "-z", "--", path],
                    literal_paths=True,
                    operation="validate discard path",
                )
                tracked_paths = _decode(tracked.stdout).split("\x00")
                if path not in tracked_paths:
                    raise GitError(f"Cannot discard untracked path: {path}")
                if self._intent_to_add(path):
                    raise GitError(f"Cannot discard intent-to-add path safely: {path}")
            result = self._git(
                ["restore", "--worktree", "--", *paths],
                literal_paths=True,
                operation="discard worktree changes",
            )
        elif operation == "commit":
            return self._commit_reviewed(action, lambda: self._preflight_context(action))
        elif operation == "apply-patch":
            if len(action.paths) != 1:
                raise GitError("Apply patch requires exactly one patch filename.")
            self._ensure_no_operation_or_conflicts(operation)
            patch = self._read_patch(action.paths[0])
            self._git(
                ["apply", "--check"],
                operation="check patch",
                input_bytes=patch,
            )
            result = self._git(["apply"], operation="apply patch", input_bytes=patch)
        elif operation == "fetch":
            result = self._execute_fetch(action.remote)
        elif operation == "set-upstream":
            if not action.target.startswith("refs/heads/"):
                raise GitError("Set upstream target must be a full refs/heads ref.")
            branch = self._local_branch_name(action.target)
            if action.target not in {ref.full_name for ref in self._refs() if ref.kind == "local"}:
                raise GitError(f"Unknown local branch: {action.target}")
            selected, source = self._exact_remote_tracking_ref(action.name, action.remote)
            if not source.startswith("refs/heads/"):
                raise GitError(
                    f"Remote tracking ref {selected.full_name} does not map to a branch source."
                )
            remote = selected.remote
            self._git(
                ["config", "--local", "--replace-all", f"branch.{branch}.remote", remote],
                operation="configure branch remote",
            )
            result = self._git(
                ["config", "--local", "--replace-all", f"branch.{branch}.merge", source],
                operation="configure branch upstream",
            )
        elif operation == "delete-remote-branch":
            if not action.expected_oid:
                raise GitError("Deleting a remote branch requires its captured object ID.")
            selected, source = self._exact_remote_tracking_ref(action.target, action.remote)
            if action.name and source != action.name:
                raise GitError("Remote branch mapping changed. Choose the deletion again.")
            if not source.startswith("refs/heads/"):
                raise GitError(
                    f"Remote tracking ref {selected.full_name} does not map to a branch source."
                )
            if selected.oid != action.expected_oid:
                raise GitError("The selected remote branch changed. Refresh and select it again.")
            push_urls = self._remote_push_urls(selected.remote)
            if len(push_urls) != 1:
                if push_urls:
                    raise GitError(
                        f"Remote {selected.remote} has multiple push destinations; configure "
                        "exactly one before deleting its branch."
                    )
                raise GitError(f"Remote {selected.remote} has no push destination.")
            if action.expected_push_url and _redact_url(push_urls[0]) != action.expected_push_url:
                raise GitError("Remote push destination changed. Choose the deletion again.")
            command = [
                "-c",
                f"remote.{selected.remote}.mirror=false",
                "-c",
                "push.followTags=false",
                "push",
                "--no-mirror",
                "--no-follow-tags",
                f"--force-with-lease={source}:{action.expected_oid}",
                selected.remote,
                f":{source}",
            ]
            result = self._git(
                command,
                timeout=_NETWORK_TIMEOUT,
                operation="delete remote branch",
            )
        elif operation == "push":
            branch = self._branch()
            if not branch or not self._head():
                raise GitError("Cannot push a detached or unborn HEAD; check out a branch first.")
            upstream = self._upstream(branch)
            set_upstream = not upstream
            if action.remote:
                remote = self._remote(action.remote)
                destination = f"refs/heads/{branch}"
            elif upstream:
                remote, destination = self._upstream_push_target(branch)
                if not remote:
                    raise GitError("The current branch has an invalid upstream configuration.")
            else:
                remote = self._remote("", sole_when_empty=True)
                destination = f"refs/heads/{branch}"
            push_urls = self._remote_push_urls(remote)
            if len(push_urls) != 1:
                if push_urls:
                    raise GitError(
                        f"Remote {remote} has multiple push destinations; configure exactly one "
                        "before pushing."
                    )
                raise GitError(f"Remote {remote} has no push destination.")
            # Explicit source and destination refs prevent push.default,
            # remote.*.push, mirror, followTags, or a leading '+' refspec from
            # widening this action beyond the current branch.
            command = [
                "-c",
                f"remote.{remote}.mirror=false",
                "-c",
                "push.followTags=false",
                "push",
                "--no-force",
                "--no-mirror",
                "--no-follow-tags",
            ]
            if set_upstream:
                command.append("--set-upstream")
            command.extend([remote, f"refs/heads/{branch}:{destination}"])
            result = self._git(command, timeout=_NETWORK_TIMEOUT, operation="push")
        else:  # continue / abort
            current = self._operation()
            if not current:
                raise GitError(f"There is no Git operation to {operation}.")
            command = {
                ("merge", "continue"): ["merge", "--continue"],
                ("merge", "abort"): ["merge", "--abort"],
                ("rebase", "continue"): ["rebase", "--continue"],
                ("rebase", "abort"): ["rebase", "--abort"],
                ("cherry-pick", "continue"): ["cherry-pick", "--continue"],
                ("cherry-pick", "abort"): ["cherry-pick", "--abort"],
                ("revert", "continue"): ["revert", "--continue"],
                ("revert", "abort"): ["revert", "--abort"],
                ("am", "continue"): ["am", "--continue"],
                ("am", "abort"): ["am", "--abort"],
            }[(current, operation)]
            result = self._git(command, operation=f"{current} {operation}")
        return _friendly_output(result, fallback)
