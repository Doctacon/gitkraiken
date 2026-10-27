"""Exercise prefix+g in a split tmux window on an isolated server and real PTY.

Run with: uv run python scripts/pane_smoke.py
Never touches the user's tmux server or configuration.
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from popup_smoke import create_fixture

from gitkraiken.tmux import hotkey


def main() -> None:
    if not shutil.which("tmux"):
        raise SystemExit("tmux >= 3.2 is required")
    server = f"gitkraiken-pane-test-{uuid.uuid4().hex[:12]}"
    master, slave = pty.openpty()
    attached = None
    stopped = threading.Event()
    output = bytearray()

    def tmux(*args: str) -> str:
        return subprocess.check_output(["tmux", "-L", server, *args], text=True).strip()

    def capture(target: str) -> str:
        return tmux("capture-pane", "-p", "-t", target)

    def value(target: str, field: str) -> str:
        return tmux("display-message", "-p", "-t", target, field)

    def host_panes() -> list[str]:
        return tmux("list-panes", "-t", "host", "-F", "#{pane_id}").splitlines()

    def wait_for(check: Callable[[], bool], message: str) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(0.1)
        raise AssertionError(message)

    def drain() -> None:
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
        with tempfile.TemporaryDirectory(prefix="gitkraiken-pane-") as temp:
            root, _ = create_fixture(Path(temp) / "repo's parent")

            def git(directory: Path, *args: str) -> str:
                return subprocess.check_output(
                    ["git", "-C", str(directory), *args], text=True, stderr=subprocess.STDOUT
                ).strip()

            # Only local remotes: test freshness without credentials or network.
            remote, peer = Path(temp) / "remote.git", Path(temp) / "peer"
            git(root, "clone", "--bare", str(root), str(remote))
            git(root, "remote", "add", "origin", str(remote))
            git(root, "push", "-u", "origin", "main")
            git(root, "clone", str(remote), str(peer))
            git(peer, "config", "user.name", "Pane Smoke")
            git(peer, "config", "user.email", "pane-smoke@example.invalid")
            git(peer, "config", "commit.gpgsign", "false")
            git(peer, "config", "core.hooksPath", "/dev/null")
            origin = tmux(
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                "host",
                "-x",
                "180",
                "-y",
                "52",
                "-c",
                str(root / "src"),
                "/bin/sh",
            )
            neighbor = tmux(
                "split-window",
                "-h",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                origin,
                "-c",
                str(root),
                "/bin/sh",
            )
            tmux("resize-pane", "-t", origin, "-x", "110")
            below = tmux(
                "split-window",
                "-v",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                origin,
                "-l",
                "16",
                "-c",
                str(root),
                "/bin/sh",
            )
            tmux("send-keys", "-t", origin, "-l", "GK_SMOKE_KEEP=preserved")
            tmux("send-keys", "-t", origin, "Enter")
            tmux("send-keys", "-t", neighbor, "-l", "printf 'NEIGHBOR_STAYS_VISIBLE\\n'")
            tmux("send-keys", "-t", neighbor, "Enter")
            config_path = Path(temp) / "gitkraiken.conf"
            config_path.write_text(
                subprocess.check_output(
                    [sys.executable, "-m", "gitkraiken", "--tmux-config"],
                    text=True,
                )
            )
            tmux("source-file", str(config_path))
            tmux("set-option", "-g", "default-shell", shutil.which("zsh") or "/bin/sh")
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 52, 180, 0, 0))
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
            geometry = "#{pane_left},#{pane_top},#{pane_width},#{pane_height}"
            origin_geometry = value(origin, geometry)
            origin_pid = value(origin, "#{pane_pid}")
            neighbor_geometry = {p: value(p, geometry) for p in (neighbor, below)}
            neighbor_pid = {p: value(p, "#{pane_pid}") for p in (neighbor, below)}
            neighbor_screen = {p: capture(p) for p in (neighbor, below)}

            def open_app(source: str) -> str:
                tmux("select-pane", "-t", source)
                os.write(master, b"\x02g")
                wait_for(
                    lambda: "GITKRAIKEN" in capture("host"),
                    "prefix+g did not open GitKraiken inside the originating pane",
                )
                target = value("host", "#{pane_id}")

                def painted() -> bool:
                    lines = capture(target).splitlines()
                    return (
                        "COMMIT GRAPH" in "\n".join(lines)
                        and len(lines) == int(value(target, "#{pane_height}"))
                        and "Ctrl+P" in lines[-1]
                    )

                wait_for(painted, "App did not repaint at the originating pane's size")
                return target

            def hide_app(target: str, source: str) -> None:
                tmux("select-pane", "-t", target)
                os.write(master, b"\x02g")
                wait_for(
                    lambda: source in host_panes(), "prefix+g did not restore the original pane"
                )
                assert target not in host_panes()
                assert value("host", "#{pane_id}") == source

            def app_gone(target: str, source: str) -> bool:
                panes = tmux("list-panes", "-a", "-F", "#{pane_id}").splitlines()
                return target not in panes and source in host_panes()

            app = open_app(origin)
            app_pid = int(value(app, "#{pane_pid}"))
            assert value(app, geometry) == origin_geometry
            for p in (neighbor, below):
                assert value(p, geometry) == neighbor_geometry[p]
                assert value(p, "#{pane_pid}") == neighbor_pid[p]
                assert capture(p) == neighbor_screen[p]
            assert tmux("list-clients", "-F", "#{session_name}").splitlines() == ["host"]
            assert attached.poll() is None

            # Native resize must resize the actual TUI, in both axes, without a
            # replacement process or overlay. The bottom hint proves a repaint
            # at the new height, not merely a changed tmux pane dimension.
            for width, height in ((80, 26), (60, 22), (110, 34)):
                tmux("resize-pane", "-t", app, "-x", str(width), "-y", str(height))

                def resized(expected_height: int = height) -> bool:
                    lines = capture(app).splitlines()
                    return len(lines) == expected_height and "Ctrl+P" in lines[-1]

                wait_for(resized, f"App did not repaint after resize to {width}x{height}")
                assert value(app, "#{pane_width},#{pane_height}") == f"{width},{height}"
                assert int(value(app, "#{pane_pid}")) == app_pid
                assert value(app, "#{pane_dead}") == "0"
                assert "GITKRAIKEN" in capture(app)
                assert len(host_panes()) == 3
            resized_geometry = value(app, geometry)

            # Other panes accept input while the app remains open. A second
            # origin in the same repository must get its own independent app.
            tmux("select-pane", "-t", neighbor)
            os.write(master, b"printf 'NEIGHBOR_STILL_INTERACTIVE\\n'\r")
            wait_for(
                lambda: "NEIGHBOR_STILL_INTERACTIVE" in capture(neighbor),
                "Neighbor did not accept input while GitKraiken was open",
            )
            # Concurrent handlers can arrive before a previous run-shell job
            # finishes. An even number must leave exactly one hidden/visible
            # pair, both for an existing app and for first-time initialization.
            socket = value("host", "#{socket_path}")
            client = tmux("list-clients", "-F", "#{client_name}")
            for target in (app, neighbor):
                with ThreadPoolExecutor(max_workers=4) as handlers:
                    pending = [
                        handlers.submit(hotkey, socket=socket, client=client, pane=target)
                        for _ in range(4)
                    ]
                    for handler in pending:
                        handler.result(timeout=15)
            assert app in host_panes() and neighbor in host_panes()
            assert len(tmux("list-sessions", "-F", "#{session_name}").splitlines()) == 3
            second = open_app(neighbor)
            assert second != app
            assert app in host_panes()
            assert int(value(app, "#{pane_pid}")) == app_pid
            tmux("send-keys", "-t", second, "C-q")
            wait_for(lambda: app_gone(second, neighbor), "Second origin was not restored on quit")
            assert app in host_panes()

            # Hide/show preserves app state and the original shell's process.
            tmux("send-keys", "-t", app, "g")  # Smooth -> Compact
            wait_for(lambda: "Compact" in capture(app), "Graph style did not change")
            hide_app(app, origin)
            assert value(origin, "#{pane_pid}") == origin_pid
            assert value(origin, geometry) == resized_geometry
            time.sleep(2.1)  # exceed duplicate focus-event debounce
            old_head = git(root, "rev-parse", "HEAD")
            git(peer, "commit", "--allow-empty", "-m", "Remote change while hidden")
            git(peer, "push", "origin", "main")
            assert open_app(origin) == app
            wait_for(lambda: "↓1" in capture(app), "Reopen did not refresh remote state")
            assert git(root, "rev-parse", "HEAD") == old_head  # fetch, never pull
            assert int(value(app, "#{pane_pid}")) == app_pid
            wait_for(lambda: "Compact" in capture(app), "Reopen lost the selected graph style")

            # Preserve zoom only when the user explicitly requested it. Both
            # toggles and exit restoration must retain that choice.
            tmux("resize-pane", "-Z", "-t", app)
            hide_app(app, origin)
            assert value(origin, "#{window_zoomed_flag}") == "1"
            assert open_app(origin) == app
            assert value(app, "#{window_zoomed_flag}") == "1"
            tmux("send-keys", "-t", app, "C-q")
            wait_for(lambda: app_gone(app, origin), "Ctrl+Q did not restore the original pane")
            assert value(origin, "#{pane_pid}") == origin_pid
            assert value(origin, "#{window_zoomed_flag}") == "1"
            tmux("resize-pane", "-Z", "-t", origin)
            assert value(origin, geometry) == resized_geometry
            tmux("send-keys", "-t", origin, "-l", "printf 'SHELL_STATE:%s\\n' \"$GK_SMOKE_KEEP\"")
            tmux("send-keys", "-t", origin, "Enter")
            wait_for(lambda: "SHELL_STATE:preserved" in capture(origin), "Shell state was lost")

            # Even SIGKILL must restore the source. Cleanup cannot steal focus
            # from another pane, and a hidden app dying must not swap anything.
            for hidden in (False, True):
                app = open_app(origin)
                app_pid = int(value(app, "#{pane_pid}"))
                if hidden:
                    hide_app(app, origin)
                tmux("select-pane", "-t", neighbor)
                os.kill(app_pid, signal.SIGKILL)
                wait_for(
                    lambda target=app: app_gone(target, origin),
                    "Killed app did not restore its origin",
                )
                assert value("host", "#{pane_id}") == neighbor
                assert value(origin, "#{pane_pid}") == origin_pid
                assert len(host_panes()) == 3
            assert tmux("list-sessions", "-F", "#{session_name}").splitlines() == ["host"]
            for p in (neighbor, below):
                assert value(p, "#{pane_pid}") == neighbor_pid[p]
            assert attached.poll() is None
            print(
                "PASS: prefix+g is pane-local; native resize 80x26 → 60x22 → 110x34; "
                "neighbors stay interactive; independent origins; concurrent toggles; state and zoom; "
                "remote state refreshed on resume without pulling; "
                "Ctrl+Q and visible/hidden crashes restore the original shell without focus theft"
            )
    except Exception:
        try:
            print("--- host ---\n" + capture("host"), file=sys.stderr)
            print(
                "--- panes ---\n"
                + tmux(
                    "list-panes",
                    "-a",
                    "-F",
                    "#{session_name} #{pane_id} #{pane_width}x#{pane_height} #{pane_current_command}",
                ),
                file=sys.stderr,
            )
        except subprocess.CalledProcessError:
            pass
        print(bytes(output[-2000:]).decode(errors="replace"), file=sys.stderr)
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
    main()
