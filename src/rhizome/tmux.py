"""Opt-in tmux launch and a printable prefix+g binding; never edit user config."""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
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
    return "exec " + shlex.join([sys.executable, "-m", "rhizome", str(root.resolve())])


def _tmux_quote(command: str) -> str:
    return '"' + command.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def config() -> str:
    """Emit a tmux config line for THIS installation, with no PATH dependency.

    The pane path is intentionally not interpolated into shell source. The helper
    receives tmux identifiers and asks that server for the pane's directory.
    """
    if any(char in sys.executable for char in "\n\r\0"):
        raise TmuxError("The Python installation path contains unsupported control characters.")
    # One tmux format expansion happens when run-shell executes. Escape literal
    # hashes in an installation path, then quote for its shell and tmux's parser.
    command = shlex.join([sys.executable.replace("#", "##"), "-m", "rhizome.tmux"])
    command += " --socket #{q:socket_path} --client #{q:client_name} --pane #{q:pane_id}"
    quoted = _tmux_quote(command)
    return (
        "# Rhizome: prefix + g toggles this pane's repo in place; other panes stay visible.\n"
        "# Generated for this machine. Regenerate after moving/reinstalling the Python environment.\n"
        f"bind-key -T prefix g run-shell -b {quoted}\n"
    )


def hotkey(*, socket: str, client: str, pane: str) -> None:
    """Handle the binding on its originating server, pane and attached client."""
    from .git import Repository

    markers = _run(
        "display-message",
        "-p",
        "-t",
        pane,
        "#{@rhizome_popup}|#{@rhizome_origin}",
        socket=socket,
    ).stdout.removesuffix("\n")
    popup, _, origin = markers.partition("|")
    if origin:
        with _pane_lock(origin, socket=socket):
            _run("swap-pane", "-d", "-Z", "-s", origin, "-t", pane, socket=socket)
        return
    if popup == "1":
        _run("detach-client", "-t", client, socket=socket)
        return
    directory = _run(
        "display-message", "-p", "-t", pane, "#{pane_current_path}", socket=socket
    ).stdout.removesuffix("\n")
    if not directory:
        raise TmuxError("Could not determine the current pane's directory.")
    launch_pane(Repository(directory).root, pane=pane, socket=socket)


@contextmanager
def _pane_lock(origin: str, *, socket: str) -> Iterator[None]:
    """Serialize startup, toggles and exit restoration for one originating pane."""
    channel = f"rhizome-pane-{origin}"
    _run("wait-for", "-L", channel, socket=socket)
    try:
        yield
    finally:
        _run("wait-for", "-U", channel, socket=socket, check=False)


def _pane_value(pane: str, value: str, *, socket: str, check: bool = True) -> str:
    return _run(
        "display-message", "-p", "-t", pane, value, socket=socket, check=check
    ).stdout.strip()


def _notify_focus(target: str, *, socket: str) -> None:
    """Best-effort notification that a persistent app became visible again."""
    try:
        _run(
            "send-keys",
            "-t",
            target,
            "-H",
            "1b",
            "5b",
            "49",
            socket=socket,
            check=False,
        )
    except TmuxError:
        # A resume notification must never turn a successful swap/select into an
        # open failure or interfere with restoring the user's parked process.
        pass


def launch_pane(root: Path, *, pane: str, socket: str) -> None:
    """Swap a persistent app into exactly this pane's slot, parking its live process.

    Unlike a popup, a real pane receives native terminal resizes and does not
    obstruct other panes. Each origin/repository pair owns a detached parking
    session; neither the user's shell nor any neighboring pane is respawned.
    """
    root = root.resolve()
    session = f"rhizome-pane-{pane.removeprefix('%')}-{workspace_id(root)}"
    with _pane_lock(pane, socket=socket):
        app = _pane_value(f"={session}:", "#{@rhizome_app}", socket=socket, check=False)
        reused = bool(app)
        if not app:
            # tmux replaces control separators with '_' in a C locale. These
            # numeric/owned-ID fields use a printable separator instead.
            size = _pane_value(pane, "#{pane_width}|#{pane_height}", socket=socket)
            width, height = size.split("|")
            # Bootstrap with a harmless process. Install the death hook before
            # starting the app, so even an immediate startup failure is restored.
            created = _run(
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{session_id}|#{pane_id}",
                "-s",
                session,
                "-x",
                width,
                "-y",
                height,
                "-c",
                str(root),
                "sleep 86400",
                socket=socket,
            ).stdout.strip()
            parking, app = created.split("|")
            try:
                _run("set-option", "-t", parking, "destroy-unattached", "off", socket=socket)
                _run("set-option", "-t", parking, "status", "off", socket=socket)
                _run("set-option", "-t", parking, "@rhizome_app", app, socket=socket)
                _run("set-option", "-p", "-t", app, "@rhizome_origin", pane, socket=socket)
                _run("set-option", "-p", "-t", app, "@rhizome_parking", parking, socket=socket)
                _run("set-option", "-p", "-t", app, "remain-on-exit", "on", socket=socket)
                restore = shlex.join(
                    [
                        token.replace("#", "##")
                        for token in [
                            sys.executable,
                            "-m",
                            "rhizome.tmux",
                            "--socket",
                            socket,
                            "--pane",
                            app,
                            "--restore",
                        ]
                    ]
                )
                _run(
                    "set-hook",
                    "-p",
                    "-t",
                    app,
                    "pane-died",
                    "run-shell -b " + _tmux_quote(restore),
                    socket=socket,
                )
                _run(
                    "respawn-pane",
                    "-k",
                    "-t",
                    app,
                    "-c",
                    str(root),
                    app_command(root),
                    socket=socket,
                )
            except Exception:
                # Setup has not moved any user process yet.
                _run("kill-pane", "-t", app, socket=socket, check=False)
                raise
        # Do not kill the app on a swap error: a timed-out client may have already
        # completed the swap, leaving the user's process in the parking session.
        _run("swap-pane", "-d", "-Z", "-s", app, "-t", pane, socket=socket)
        if reused:
            _notify_focus(app, socket=socket)


def restore_pane(*, pane: str, socket: str) -> None:
    """Handle Ctrl+Q, startup failure or a killed app without losing the origin."""
    origin = _pane_value(pane, "#{@rhizome_origin}", socket=socket, check=False)
    if not origin:
        return  # Another cleanup already removed the app pane.
    with _pane_lock(origin, socket=socket):
        parking = _pane_value(pane, "#{@rhizome_parking}", socket=socket, check=False)
        current = _pane_value(pane, "#{session_id}", socket=socket, check=False)
        if not current or not parking:
            return
        if _pane_value(pane, "#{pane_dead}", socket=socket, check=False) != "1":
            return  # A user may have respawned the pane before this hook ran.
        original_session = _pane_value(origin, "#{session_id}", socket=socket, check=False)
        if current != parking and original_session == parking:
            # -d preserves the active slot: no focus theft if the user moved to a
            # neighbor while the app was exiting. -Z preserves existing zoom.
            _run("swap-pane", "-d", "-Z", "-s", origin, "-t", pane, socket=socket)
        # Kill only our dead application, never the parking session (which may
        # still contain a user's process). Hidden app exits need no swap at all.
        _run("kill-pane", "-t", pane, socket=socket, check=False)


def hotkey_main() -> None:
    """Internal entrypoint for generated run-shell bindings, not an interactive app."""
    parser = argparse.ArgumentParser(description="Rhizome tmux hotkey handler")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--client", default="")
    parser.add_argument("--pane", required=True)
    parser.add_argument("--restore", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    from .git import GitError

    try:
        if args.restore:
            restore_pane(socket=args.socket, pane=args.pane)
        else:
            hotkey(socket=args.socket, client=args.client, pane=args.pane)
    except (GitError, TmuxError, OSError, ValueError) as error:
        # A run-shell job has no interactive terminal. Put errors on the invoking
        # client's status line instead of opening tmux's command-output pane.
        message = str(error).splitlines()[0]
        _run(
            "display-message",
            *(["-c", args.client] if args.client else ["-t", args.pane]),
            "-d",
            "5000",
            "Rhizome: " + message.replace("#", "##"),
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
    session = f"rhizome-{key}"
    command = app_command(root)
    inside = bool(os.environ.get("TMUX")) or bool(client and socket)

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _run(*args, check=check, socket=socket)

    if popup and not inside:
        raise TmuxError("--popup needs an existing tmux client. Use --tmux outside tmux.")
    if popup:
        reused = not run("has-session", "-t", f"={session}", check=False).returncode
        if not reused:
            run("new-session", "-d", "-s", session, "-c", str(root), command)
        # Only our dedicated popup sessions carry this marker. The hotkey can
        # safely detach their client instead of recursively opening another popup.
        # set-option does not accept has-session's '=name' target syntax.
        run("set-option", "-t", session, "@rhizome_popup", "1")
        if reused:
            _notify_focus(f"={session}:", socket=socket)
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
        windows = run("list-windows", "-F", "#{window_id}|#{@rhizome_id}").stdout
        for line in windows.splitlines():
            window, _, marker = line.partition("|")
            if marker == key:
                run("select-window", "-t", window)
                _notify_focus(window, socket=socket)
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
        run("set-option", "-w", "-t", window, "@rhizome_id", key)
    else:
        result = subprocess.run(
            [*_tmux_args(socket), "new-session", "-A", "-s", session, "-c", str(root), command],
            check=False,
        )
        if result.returncode:
            raise TmuxError("Could not attach tmux. Run this command from an interactive terminal.")


if __name__ == "__main__":
    hotkey_main()
