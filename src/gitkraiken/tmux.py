"""Opt-in tmux launch and a printable prefix+g binding; never edit user config."""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


class TmuxError(RuntimeError):
    pass


def _tmux_args(socket: str = "") -> list[str]:
    return ["tmux", "-S", socket] if socket else ["tmux"]


def _run(*args: str, check: bool = True, socket: str = "") -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [*_tmux_args(socket), *args], text=True, capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TmuxError(f"Could not communicate with tmux: {error}") from error
    if check and result.returncode:
        raise TmuxError(result.stderr.strip() or "tmux command failed")
    return result


def workspace_id(root: Path) -> str:
    return hashlib.sha256(os.fsencode(root.resolve())).hexdigest()[:16]


def app_command(root: Path) -> str:
    # tmux's command argument is shell source; quote every dynamic token.
    return "exec " + shlex.join([sys.executable, "-m", "gitkraiken", str(root.resolve())])


def config() -> str:
    """Emit a tmux config line for THIS installation, with no PATH dependency.

    The pane path is intentionally not interpolated into shell source. The helper
    receives tmux identifiers and asks that server for the pane's directory.
    """
    if any(char in sys.executable for char in "\n\r\0"):
        raise TmuxError("The Python installation path contains unsupported control characters.")
    # One tmux format expansion happens when run-shell executes. Escape literal
    # hashes in an installation path, then quote for its shell and tmux's parser.
    command = shlex.join([sys.executable.replace("#", "##"), "-m", "gitkraiken.tmux"])
    command += " --socket #{q:socket_path} --client #{q:client_name} --pane #{q:pane_id}"
    quoted = '"' + command.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'
    return (
        "# GitKraiken: prefix + g opens this pane's repo; repeat inside the popup to detach.\n"
        "# Generated for this machine. Regenerate after moving/reinstalling the Python environment.\n"
        f"bind-key -T prefix g run-shell -b {quoted}\n"
    )


def hotkey(*, socket: str, client: str, pane: str) -> None:
    """Handle the binding on its originating server, pane and attached client."""
    from .git import Repository

    marker = _run(
        "display-message", "-p", "-t", pane, "#{@gitkraiken_popup}", socket=socket
    ).stdout.strip()
    if marker == "1":
        _run("detach-client", "-t", client, socket=socket)
        return
    directory = _run(
        "display-message", "-p", "-t", pane, "#{pane_current_path}", socket=socket
    ).stdout.removesuffix("\n")
    if not directory:
        raise TmuxError("Could not determine the current pane's directory.")
    launch(Repository(directory).root, popup=True, client=client, socket=socket)


def hotkey_main() -> None:
    """Internal entrypoint for generated run-shell bindings, not an interactive app."""
    parser = argparse.ArgumentParser(description="GitKraiken tmux hotkey handler")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--client", required=True)
    parser.add_argument("--pane", required=True)
    args = parser.parse_args()
    from .git import GitError

    try:
        hotkey(socket=args.socket, client=args.client, pane=args.pane)
    except (GitError, TmuxError, OSError, ValueError) as error:
        # A run-shell job has no interactive terminal. Put errors on the invoking
        # client's status line instead of opening tmux's command-output pane.
        message = str(error).splitlines()[0]
        _run(
            "display-message",
            "-c",
            args.client,
            "-d",
            "5000",
            "GitKraiken: " + message.replace("#", "##"),
            socket=args.socket,
            check=False,
        )


def launch(root: Path, *, popup: bool = False, client: str = "", socket: str = "") -> None:
    """Reuse a repo window in tmux, or attach a dedicated session outside it.

    Popup sessions are persistent until the user quits the application. Detach with
    the normal tmux detach binding to close the popup without losing its state.
    """
    if shutil.which("tmux") is None:
        raise TmuxError("tmux is not installed. Install tmux >= 3.2 or omit --tmux/--popup.")
    root = root.resolve()
    key = workspace_id(root)
    session = f"gitkraiken-{key}"
    command = app_command(root)
    inside = bool(os.environ.get("TMUX")) or bool(client and socket)

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _run(*args, check=check, socket=socket)

    if popup and not inside:
        raise TmuxError("--popup needs an existing tmux client. Use --tmux outside tmux.")
    if popup:
        if run("has-session", "-t", f"={session}", check=False).returncode:
            run("new-session", "-d", "-s", session, "-c", str(root), command)
        # Only our dedicated popup sessions carry this marker. The hotkey can
        # safely detach their client instead of recursively opening another popup.
        # set-option does not accept has-session's '=name' target syntax.
        run("set-option", "-t", session, "@gitkraiken_popup", "1")
        popup_socket = socket or run("display-message", "-p", "#{socket_path}").stdout.strip()
        if not popup_socket:
            raise TmuxError("Could not determine the current tmux server socket.")
        attach = shlex.join(
            # A leading '=' is exact-match syntax to tmux, but zsh expands an
            # unquoted '=name' as a command path. Our generated session name is
            # already unambiguous; use it without '=' in this shell command.
            ["env", "-u", "TMUX", "tmux", "-S", popup_socket, "attach-session", "-t", session]
        )
        # Display-popup blocks until detached; inherit the controlling terminal.
        result = subprocess.run(
            [
                *_tmux_args(popup_socket),
                "display-popup",
                *(["-c", client] if client else []),
                "-d",
                str(root),
                "-w",
                "96%",
                "-h",
                "94%",
                "-E",
                attach,
            ],
            check=False,
        )
        if result.returncode:
            raise TmuxError(
                "Could not open tmux popup. tmux >= 3.2 and an attached client are required."
            )
    elif inside:
        windows = run("list-windows", "-F", "#{window_id}\t#{@gitkraiken_id}").stdout
        for line in windows.splitlines():
            window, _, marker = line.partition("\t")
            if marker == key:
                run("select-window", "-t", window)
                return
        window = run(
            "new-window",
            "-P",
            "-F",
            "#{window_id}",
            "-n",
            f"git · {root.name}",
            "-c",
            str(root),
            command,
        ).stdout.strip()
        run("set-option", "-w", "-t", window, "@gitkraiken_id", key)
    else:
        result = subprocess.run(
            [*_tmux_args(socket), "new-session", "-A", "-s", session, "-c", str(root), command],
            check=False,
        )
        if result.returncode:
            raise TmuxError("Could not attach tmux. Run this command from an interactive terminal.")


if __name__ == "__main__":
    hotkey_main()
