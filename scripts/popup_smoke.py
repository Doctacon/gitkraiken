"""Exercise the actual popup CLI through an attached PTY on a custom tmux socket.

Run with: uv run python scripts/popup_smoke.py [--hotkey]
--hotkey also verifies prefix+g can still detach an explicitly requested popup.
Pane-local hotkey launch/resize/toggle behavior is covered by pane_smoke.py.
Uses zsh when available to catch its special handling of unquoted '=name'.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import pty
import select
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from gitkraiken.tmux import workspace_id


def create_fixture(path: Path) -> tuple[Path, str]:
    """Create a clean, local-only repository with a branch suitable for checkout."""
    root = path / "repo"

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT
        ).strip()

    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    git("config", "user.name", "Popup Smoke")
    git("config", "user.email", "popup-smoke@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "core.hooksPath", "/dev/null")
    (root / "src").mkdir()
    (root / "src" / "base.txt").write_text("base\n")
    git("add", ".")
    git("commit", "-q", "-m", "Base")
    git("switch", "-q", "-c", "test")
    (root / "topic.txt").write_text("topic\n")
    git("add", ".")
    git("commit", "-q", "-m", "Popup checkout target")
    target = git("rev-parse", "HEAD")
    git("switch", "-q", "main")
    return root, target


def main(*, use_hotkey: bool = False) -> None:
    if not shutil.which("tmux"):
        raise SystemExit("tmux >= 3.2 is required")
    server = f"gitkraiken-popup-test-{uuid.uuid4().hex[:12]}"
    master, slave = pty.openpty()
    attached = None
    stopped = threading.Event()
    output = bytearray()

    def tmux(*args: str) -> str:
        return subprocess.check_output(["tmux", "-L", server, *args], text=True).strip()

    def capture(target: str) -> str:
        return tmux("capture-pane", "-p", "-t", target)

    def capture_history(target: str) -> str:
        return tmux("capture-pane", "-p", "-S", "-", "-t", target)

    def popup_client(target: str) -> tuple[str, int]:
        clients = tmux(
            "list-clients", "-F", "#{client_name}\t#{session_name}\t#{client_pid}"
        ).splitlines()
        match = next((line.split("\t") for line in clients if line.split("\t")[1] == target), None)
        return (match[0], int(match[2])) if match else ("", 0)

    def wait_for(check: Callable[[], bool], message: str, *, timeout: float = 8) -> None:
        deadline = time.monotonic() + timeout
        error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                if check():
                    return
            except (OSError, subprocess.CalledProcessError) as caught:
                error = caught
            time.sleep(0.1)
        raise AssertionError(message) from error

    def send_mouse(target: str, x: int, y: int, *, times: int = 1) -> None:
        # SGR mouse coordinates are one-based. Complete press/release pairs are
        # delivered directly to the real Textual process, not a test app wrapper.
        event = f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m"
        for _ in range(times):
            tmux("send-keys", "-t", target, "-l", event)

    def drain() -> None:
        # A real terminal continuously drains its PTY. Without this, an artificial
        # client can deadlock on a full output buffer before the popup renders.
        while not stopped.is_set():
            try:
                if select.select([master], [], [], 0.1)[0]:
                    output.extend(os.read(master, 65536))
                    del output[:-100_000]
            except (OSError, ValueError):
                return

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        with tempfile.TemporaryDirectory(prefix="gitkraiken-popup-") as temp:
            root, checkout_oid = create_fixture(Path(temp) / "repo's parent")

            def git(*args: str) -> str:
                return subprocess.check_output(
                    ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT
                ).strip()

            tmux(
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                "host",
                "-x",
                "160",
                "-y",
                "48",
                "-c",
                str(root / "src"),
                "/bin/sh",
            )
            shell = shutil.which("zsh") or "/bin/sh"
            tmux("set-option", "-g", "default-shell", shell)
            if use_hotkey:
                config_path = Path(temp) / "gitkraiken.conf"
                config_path.write_text(
                    subprocess.check_output(
                        [sys.executable, "-m", "gitkraiken", "--tmux-config"],
                        cwd=temp,
                        text=True,
                    )
                )
                tmux("source-file", str(config_path))
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 48, 160, 0, 0))
            env = os.environ.copy()
            env.pop("TMUX", None)
            env.pop("TMUX_PANE", None)
            env["TERM"] = "xterm-256color"
            attached = subprocess.Popen(
                ["tmux", "-L", server, "attach", "-t", "host"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                start_new_session=True,
            )
            time.sleep(0.3)
            target = "gitkraiken-" + workspace_id(root)
            app_pid: int | None = None
            popup_client_pids: list[int] = []
            for run in range(2):
                command = shlex.join([sys.executable, "-m", "gitkraiken", "--popup", str(root)])
                command += f"; printf 'POPUP_{run}_EXIT:%s\\n' \"$?\""
                tmux("send-keys", "-t", "host", "-l", command)
                tmux("send-keys", "-t", "host", "Enter")

                inner = ""
                inner_pid = 0

                def popup_attached() -> bool:
                    nonlocal inner, inner_pid
                    inner, inner_pid = popup_client(target)
                    return bool(inner)

                wait_for(popup_attached, "Popup did not attach")
                popup_client_pids.append(inner_pid)
                wait_for(
                    lambda: "Popup checkout target" in capture(target),
                    "Popup did not finish rendering the repository",
                )

                current_pid = int(tmux("display-message", "-p", "-t", target, "#{pane_pid}"))
                if app_pid is None:
                    app_pid = current_pid
                assert current_pid == app_pid, "Reopening replaced the persistent application PID"
                assert tmux("display-message", "-p", "-t", target, "#{pane_dead}") == "0"
                os.kill(app_pid, 0)
                assert attached.poll() is None, "The outer tmux client exited"

                def assert_alive(
                    context: str,
                    expected_pid: int = current_pid,
                    expected_client: str = inner,
                    expected_client_pid: int = inner_pid,
                ) -> None:
                    # Mutations refresh asynchronously; require the same process and
                    # client to remain healthy long enough for a post-checkout repaint.
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        assert attached.poll() is None, f"{context} exited the outer tmux client"
                        current_client = popup_client(target)
                        assert current_client[0], f"{context} closed or detached the popup client"
                        assert current_client == (expected_client, expected_client_pid), (
                            f"{context} replaced the popup client PID"
                        )
                        os.kill(expected_client_pid, 0)
                        assert (
                            int(tmux("display-message", "-p", "-t", target, "#{pane_pid}"))
                            == expected_pid
                        ), f"{context} replaced the application PID"
                        assert tmux("display-message", "-p", "-t", target, "#{pane_dead}") == "0", (
                            f"{context} exited the application process"
                        )
                        os.kill(expected_pid, 0)
                        time.sleep(0.1)

                def point_for(text: str, *, contains: str = "") -> tuple[int, int]:
                    lines = capture(target).splitlines()
                    row = next(
                        (
                            number
                            for number, line in enumerate(lines)
                            if text in line and (not contains or contains in line)
                        ),
                        None,
                    )
                    assert row is not None, f"Could not find {text!r} in the popup"
                    return lines[row].index(text) + 1, row + 1

                def wait_for_header(text: str, context: str) -> None:
                    def rendered() -> bool:
                        assert tmux("display-message", "-p", "-t", target, "#{pane_dead}") == "0", (
                            f"{context} exited the application process before repainting"
                        )
                        return text in capture(target)

                    wait_for(rendered, f"Displayed header did not update to {text}")

                def open_actions(label: str, message: str) -> None:
                    def opened() -> bool:
                        screen = capture(target)
                        if "REPOSITORY ACTIONS" in screen:
                            return label in screen
                        # Polling briefly makes the app busy, during which Ctrl+P
                        # is intentionally ignored. Retry until the menu opens.
                        tmux("send-keys", "-t", target, "C-p")
                        return False

                    wait_for(opened, message)

                if run == 0:
                    # Select the test tip with one real graph click, then independently
                    # drive Actions -> checkout selected commit (detached HEAD).
                    graph_x, graph_y = point_for("●", contains="Popup checkout target")
                    send_mouse(target, graph_x, graph_y)
                    open_actions(
                        "Check out selected commit",
                        "Ctrl+P did not open checkout actions for the selected commit",
                    )
                    tmux("send-keys", "-t", target, "Down", "Down", "Down", "Enter")
                    wait_for(
                        lambda: git("branch", "--show-current") == "",
                        "Selected-commit Actions checkout did not detach HEAD",
                    )
                    assert git("rev-parse", "HEAD") == checkout_oid
                    wait_for_header("detached HEAD", "Selected-commit Actions checkout")
                    assert_alive("Selected-commit Actions checkout")

                    # The same commit also owns branch test. A graph double-click
                    # must immediately switch back to that branch from detached HEAD.
                    graph_x, graph_y = point_for("●", contains="Popup checkout target")
                    send_mouse(target, graph_x, graph_y, times=2)
                    wait_for(
                        lambda: git("branch", "--show-current") == "test",
                        "Graph double-click did not immediately switch to branch test",
                    )
                    assert git("rev-parse", "HEAD") == checkout_oid
                    wait_for_header("⑂ test", "Graph double-click checkout")
                    assert_alive("Graph double-click checkout")
                else:
                    assert git("branch", "--show-current") == "test"
                    assert git("rev-parse", "HEAD") == checkout_oid
                    assert "⑂ test" in capture(target), (
                        "Reopened popup did not display checked-out branch"
                    )

                    # Also select main in the real sidebar and use its Actions
                    # checkout, proving that path survives in the reopened popup.
                    main_x, main_y = point_for("main", contains="├ main")
                    send_mouse(target, main_x + 2, main_y)
                    wait_for(
                        lambda: "Selected main" in capture(target),
                        "Sidebar click did not select branch main",
                    )
                    open_actions(
                        "Check out refs/heads/main",
                        "Ctrl+P did not open checkout actions for the selected sidebar branch",
                    )
                    tmux("send-keys", "-t", target, "Down", "Down", "Down", "Down", "Enter")
                    wait_for(
                        lambda: git("branch", "--show-current") == "main",
                        "Sidebar Actions checkout did not switch to branch main",
                    )
                    wait_for_header("⑂ main", "Sidebar branch Actions checkout")
                    assert_alive("Sidebar branch Actions checkout")

                if use_hotkey:
                    os.write(master, b"\x02g")
                else:
                    tmux("detach-client", "-t", inner)

                def popup_detached(run_number: int = run) -> bool:
                    return target not in tmux(
                        "list-clients", "-F", "#{session_name}"
                    ).splitlines() and f"POPUP_{run_number}_EXIT:0" in capture("host")

                wait_for(popup_detached, "Popup did not detach cleanly", timeout=3)
                assert len(tmux("list-sessions", "-F", "#{session_name}").splitlines()) == 2
                assert int(tmux("display-message", "-p", "-t", target, "#{pane_pid}")) == app_pid
                os.kill(app_pid, 0)
            assert len(set(popup_client_pids)) == 2, "Reopen unexpectedly reused a popup client PID"
            print(
                f"PASS: explicit popup ({'prefix+g detach' if use_hotkey else 'CLI'}) keeps outer client PID "
                f"{attached.pid} and app PID {app_pid} alive throughout; popup client PIDs "
                f"{popup_client_pids} each stay alive while attached and change on reopen; "
                "headers detached HEAD → test → main; detach/reopen works "
                f"on a custom socket ({shell})"
            )
    except Exception:
        diagnostics: list[str] = []
        for pane_target in (locals().get("target", ""), "host"):
            if pane_target:
                try:
                    diagnostics.append(f"--- {pane_target} ---\n{capture_history(pane_target)}")
                except (subprocess.CalledProcessError, OSError):
                    pass
        try:
            diagnostics.append(
                "--- clients ---\n"
                + tmux("list-clients", "-F", "#{client_name}\t#{session_name}\t#{client_pid}")
            )
            diagnostics.append(
                "--- panes ---\n"
                + tmux(
                    "list-panes",
                    "-a",
                    "-F",
                    "#{session_name}\t#{pane_pid}\tdead=#{pane_dead}\t#{pane_current_command}",
                )
            )
        except (subprocess.CalledProcessError, OSError):
            pass
        diagnostics.append("--- PTY tail ---\n" + bytes(output[-4000:]).decode(errors="replace"))
        print("\n".join(diagnostics), file=sys.stderr)
        raise
    finally:
        subprocess.run(["tmux", "-L", server, "kill-server"], capture_output=True, check=False)
        if attached is not None:
            try:
                attached.wait(timeout=3)
            except subprocess.TimeoutExpired:
                attached.kill()
                attached.wait()
        stopped.set()
        reader.join(timeout=2)
        os.close(master)
        os.close(slave)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hotkey", action="store_true", help="test prefix+g detaching an explicit popup"
    )
    main(use_hotkey=parser.parse_args().hotkey)
