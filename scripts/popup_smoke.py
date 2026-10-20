"""Exercise the actual popup CLI through an attached PTY on a custom tmux socket.

Run with: uv run python scripts/popup_smoke.py [--hotkey]
--hotkey sources the generated config and presses prefix+g to open/close/reopen.
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
from pathlib import Path

from gitkraiken.demo import create_demo
from gitkraiken.tmux import workspace_id


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
            root = create_demo(Path(temp) / "repo's parent")
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
            for run in range(2):
                if use_hotkey:
                    os.write(master, b"\x02g")
                else:
                    command = shlex.join([sys.executable, "-m", "gitkraiken", "--popup", str(root)])
                    command += f"; printf 'POPUP_{run}_EXIT:%s\\n' \"$?\""
                    tmux("send-keys", "-t", "host", "-l", command)
                    tmux("send-keys", "-t", "host", "Enter")
                for _ in range(80):
                    clients = tmux(
                        "list-clients", "-F", "#{client_name}\t#{session_name}"
                    ).splitlines()
                    inner = next(
                        (line.split("\t")[0] for line in clients if line.endswith("\t" + target)),
                        None,
                    )
                    if inner:
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError(
                        "Popup failed:\n"
                        + tmux("capture-pane", "-p", "-t", "host")
                        + "\n"
                        + repr(output[-1500:])
                    )
                for _ in range(40):
                    if "GITKRAIKEN" in tmux("capture-pane", "-p", "-t", target):
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("The popup did not render the repository")
                if use_hotkey:
                    os.write(master, b"\x02g")
                else:
                    tmux("detach-client", "-t", inner)
                for _ in range(30):
                    if use_hotkey:
                        detached = (
                            target not in tmux("list-clients", "-F", "#{session_name}").splitlines()
                        )
                    else:
                        detached = f"POPUP_{run}_EXIT:0" in tmux("capture-pane", "-p", "-t", "host")
                    if detached:
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("The popup did not detach cleanly")
                assert len(tmux("list-sessions", "-F", "#{session_name}").splitlines()) == 2
            print(
                f"PASS: {'prefix+g' if use_hotkey else 'CLI'} popup renders, detaches and reuses its session on a custom socket ({shell})"
            )
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
    parser.add_argument("--hotkey", action="store_true", help="test the generated prefix+g binding")
    main(use_hotkey=parser.parse_args().hotkey)
