"""Synchronous, subprocess-backed Git repository engine.

This is the only module in Rhizome which invokes Git.  Arguments are always
passed as an argv sequence; callers cannot supply command-line fragments.
"""

from __future__ import annotations

import heapq
import os
import re
import shlex
import stat
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from .models import (
    Action,
    Commit,
    CommitDetail,
    FileChange,
    HistoryLine,
    Ref,
    Remote,
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
_URL_RE = re.compile(r"(?P<url>[a-z][a-z0-9+.-]*://[^\s'\"<>]+)", re.IGNORECASE)
_SCP_URL_RE = re.compile(r"(?P<url>(?<![\w./-])[^@\s/:]+@[^@\s/:]+:[^\s'\"<>]+)", re.IGNORECASE)


class GitError(RuntimeError):
    """A safe, human-readable Git failure."""

    def __init__(self, message: str) -> None:
        super().__init__(_redact(message))


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
        suffix = ""
        while value and value[-1] in ".,;)]}":
            suffix = value[-1] + suffix
            value = value[:-1]
        return _redact_url(value) + suffix

    return _SCP_URL_RE.sub(replace, _URL_RE.sub(replace, text))


def _decode(value: bytes) -> str:
    return value.decode("utf-8", "surrogateescape")


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
    timeout: float,
    ok_codes: tuple[int, ...],
    operation: str,
) -> None:
    if result.returncode in ok_codes:
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
    timeout: float = _LOCAL_TIMEOUT,
    ok_codes: tuple[int, ...] = (0,),
    operation: str = "command",
    input_bytes: bytes | None = None,
    configured_ssh_command: str = "",
) -> subprocess.CompletedProcess[bytes]:
    try:
        run_input: dict[str, object]
        if input_bytes is None:
            run_input = {"stdin": subprocess.DEVNULL}
        else:
            run_input = {"input": input_bytes}
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=_git_environment(configured_ssh_command, network=timeout == _NETWORK_TIMEOUT),
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
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
            )
            try:
                returncode = process.wait(timeout=timeout)
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
        timeout: float = _LOCAL_TIMEOUT,
        ok_codes: tuple[int, ...] = (0,),
        operation: str = "command",
        input_bytes: bytes | None = None,
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
        )

    def _git_diff(
        self,
        args: list[str],
        *,
        literal_paths: bool = False,
        ok_codes: tuple[int, ...] = (0,),
        operation: str = "read diff",
    ) -> subprocess.CompletedProcess[bytes]:
        argv = ["git"]
        if literal_paths:
            argv.append("--literal-pathspecs")
        argv.extend(args)
        return _run_spooled_process(argv, cwd=self.root, ok_codes=ok_codes, operation=operation)

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
            ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=no"],
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
        value = Path(_decode(result.stdout).rstrip("\n"))
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
                date=date,
                subject=subject,
                refs=tuple(dict.fromkeys(names_by_oid.get(oid, ()))),
            )

        missing = [oid for oid in selected_oids if oid not in commits_by_oid]
        if missing:
            raise GitError("Git returned incomplete history metadata.")
        return tuple(
            HistoryLine(graph="", commit=commits_by_oid[oid]) for oid in selected_oids
        ), has_more

    def snapshot(self, limit: int = 300, ref: str = "") -> Snapshot:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_HISTORY:
            raise GitError(f"History limit must be between 1 and {_MAX_HISTORY}.")
        head = self._head()
        branch = self._branch()
        refs = self._refs()
        upstream = self._upstream(branch)
        ahead, behind = self._ahead_behind(head, upstream)
        history, has_more = self._history(limit, ref, refs, head)
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
            date=date,
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
            if not action.message.strip():
                raise GitError("A commit message is required.")
            staged = self._git(
                ["diff", "--cached", "--quiet", "--exit-code", "--no-ext-diff", "--no-textconv"],
                ok_codes=(0, 1),
                operation="check staged changes",
            )
            if staged.returncode == 0:
                raise GitError("There are no staged changes to commit.")
            result = self._git(["commit", "-m", action.message], operation="commit")
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
            remote = self._remote(action.remote) if action.remote else ""
            command = ["fetch", "--", remote] if remote else ["fetch", "--all"]
            result = self._git(command, timeout=_NETWORK_TIMEOUT, operation="fetch")
        elif operation == "pull":
            command = ["pull", "--ff-only"]
            if action.remote:
                remote = self._remote(action.remote)
                command.append(remote)
                if action.target:
                    command.append(self._validate_branch(action.target, "Remote branch name"))
            elif action.target:
                raise GitError("A pull target requires an explicit remote.")
            result = self._git(command, timeout=_NETWORK_TIMEOUT, operation="pull")
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
