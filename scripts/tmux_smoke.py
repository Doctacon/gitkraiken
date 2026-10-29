"""Exercise a real isolated tmux server without touching the user's sessions.

Run with: uv run python scripts/tmux_smoke.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from rhizome.demo import create_demo
from rhizome.tmux import launch


def main() -> None:
    if not shutil.which("tmux"):
        raise SystemExit("tmux is required for this optional smoke test")
    server = f"rhizome-test-{uuid.uuid4().hex[:12]}"

    def tmux(*args: str) -> str:
        return subprocess.check_output(["tmux", "-L", server, *args], text=True).strip()

    old_tmux = os.environ.get("TMUX")
    with tempfile.TemporaryDirectory(prefix="rhizome-smoke-") as temp:
        try:
            root = create_demo(Path(temp) / "a repo's directory")
            tmux(
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                "test",
                "-x",
                "170",
                "-y",
                "50",
                "sleep 60",
            )
            socket = tmux("display-message", "-p", "#{socket_path}")
            os.environ["TMUX"] = f"{socket},0,0"
            launch(root)
            before = tmux("list-windows", "-F", "#{window_id}")
            launch(root)
            assert before == tmux("list-windows", "-F", "#{window_id}"), "window was duplicated"
            for _ in range(40):
                screen = tmux("capture-pane", "-p")
                if "RHIZOME" in screen and "feature/workspace" in screen:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError(f"UI did not start in tmux:\n{screen}")
            assert "COMMIT GRAPH" in screen
            # Let the app and its Git workers finish before deleting their repository.
            pane = tmux("display-message", "-p", "#{pane_id}")
            tmux("send-keys", "-t", pane, "C-q")
            for _ in range(100):
                if pane not in tmux("list-panes", "-a", "-F", "#{pane_id}").splitlines():
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("Rhizome did not exit before fixture cleanup")
            print("PASS: real tmux window starts, renders repository, reuses its window, and quits")
        finally:
            # This must run inside TemporaryDirectory, before its cleanup starts.
            subprocess.run(["tmux", "-L", server, "kill-server"], capture_output=True, check=False)
            if old_tmux is None:
                os.environ.pop("TMUX", None)
            else:
                os.environ["TMUX"] = old_tmux


if __name__ == "__main__":
    main()
