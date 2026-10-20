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

from gitkraiken.demo import create_demo
from gitkraiken.tmux import launch


def main() -> None:
    if not shutil.which("tmux"):
        raise SystemExit("tmux is required for this optional smoke test")
    server = f"gitkraiken-test-{uuid.uuid4().hex[:12]}"

    def tmux(*args: str) -> str:
        return subprocess.check_output(["tmux", "-L", server, *args], text=True).strip()

    old_tmux = os.environ.get("TMUX")
    try:
        with tempfile.TemporaryDirectory(prefix="gitkraiken-smoke-") as temp:
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
                if "GITKRAIKEN" in screen and "feature/workspace" in screen:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError(f"UI did not start in tmux:\n{screen}")
            assert "COMMIT GRAPH" in screen
            print("PASS: real tmux window starts, renders repository, and reuses its window")
    finally:
        subprocess.run(["tmux", "-L", server, "kill-server"], capture_output=True, check=False)
        if old_tmux is None:
            os.environ.pop("TMUX", None)
        else:
            os.environ["TMUX"] = old_tmux


if __name__ == "__main__":
    main()
