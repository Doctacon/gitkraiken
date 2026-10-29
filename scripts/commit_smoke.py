"""Real 80×24 Textual/Pilot + tmux commit handoff, using only disposable state.

Run: uv run --locked python scripts/commit_smoke.py
Requires the optional tmux smoke-test tool, not a Rhizome runtime dependency.
Never kills a live mutation: an unfinished fixture is retained for inspection.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

RUNNER = r'''
import asyncio
import json
import sys
import traceback
from pathlib import Path
from textual.widgets import DataTable, Static, TextArea
from rhizome.app import RhizomeApp

base = Path(sys.argv[1])
message = "  Reviewed summary  \n\n  Exact body text  \n\n"

async def main():
    app = RhizomeApp(base / "repo")
    async with app.run_test(headless=False, size=(80, 24)) as pilot:
        assert not app.is_headless
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.click("#show-files")
        app.query_one("#wip-table", DataTable).focus()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("v", "down", "s")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.click("#show-composer")
        editor = app.query_one("#commit-message", TextArea)
        editor.load_text(message)
        await pilot.pause()
        await pilot.click("#commit")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert editor.text == message
        status = app.query_one("#status-line", Static).renderable.plain
        assert "Git commit failed" in status, status
        assert (base / "refused").is_file()
        # A real resumed UI must still accept input and paint a modal after error.
        await pilot.press("ctrl+l")
        await pilot.pause()
        await pilot.click("#activity-close")
        await pilot.pause()
        (base / "reject").unlink()
        await pilot.click("#commit")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert editor.text == ""
        await pilot.press("ctrl+l")
        await pilot.pause()
        await pilot.click("#activity-close")
        await pilot.pause()
    return {"ok": True, "message": message, "headless": False}

try:
    result = asyncio.run(main())
except BaseException:
    result = {"ok": False, "error": traceback.format_exc()}
(base / "result.json").write_text(json.dumps(result))
'''


def main() -> None:
    if not shutil.which("tmux"):
        raise SystemExit("tmux is required for this optional real-terminal smoke")
    base = Path(tempfile.mkdtemp(prefix="rhizome-commit-smoke-")).resolve()
    server = f"rhizome-commit-{uuid.uuid4().hex[:12]}"
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    for name in ("TMUX", "TMUX_PANE"):
        env.pop(name, None)
    for name, directory in (
        ("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
        ("RHIZOME_STATE_HOME", "state"),
    ):
        (base / directory).mkdir()
        env[name] = str(base / directory)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_ALLOW_PROTOCOL="file", TERM="xterm-256color")
    repo = base / "repo"
    repo.mkdir()

    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(repo), *args], env=env)

    def tmux(*args: str) -> str:
        return subprocess.check_output(["tmux", "-L", server, *args], env=env, text=True).strip()

    pane = ""
    completed = False
    succeeded = False
    try:
        git("init", "-q", "-b", "main")
        git("config", "user.name", "Commit Smoke")
        git("config", "user.email", "commit-smoke@example.invalid")
        git("config", "commit.gpgsign", "false")
        git("config", "commit.cleanup", "verbatim")
        path = repo / "example.txt"
        path.write_text("a\nb\nc\nd\n")
        git("add", path.name)
        git("commit", "-qm", "base")
        before = git("rev-parse", "HEAD").strip()
        path.write_text("a\nB\nc\nD\n")
        (base / "reject").touch()
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\nset -eu\n"
            f"base={shlex.quote(str(base))}\n"
            "printf '%s\\n' \"$$\" >> \"$base/attempts\"\n"
            "printf 'APP_TTY_APPROVAL\\n' >&2\n"
            "IFS= read -r answer < /dev/tty\n"
            "printf '%s\\n' \"$answer\" >> \"$base/answers\"\n"
            "test \"$answer\" = approve\n"
            "if test -e \"$base/reject\"; then\n"
            "  : > \"$base/refused\"\n"
            "  printf 'Fixture hook refused commit\\n' >&2\n  exit 1\nfi\n"
        )
        hook.chmod(0o700)
        runner = base / "runner.py"
        runner.write_text(RUNNER)
        pane = tmux(
            "-f", "/dev/null", "new-session", "-d", "-P", "-F", "#{pane_id}",
            "-s", "commit", "-x", "80", "-y", "24", "/bin/sh",
        )
        tmux("set-option", "-t", "commit", "remain-on-exit", "on")
        tmux("send-keys", "-t", pane, "-l", shlex.join(["exec", sys.executable, str(runner), str(base)]))
        tmux("send-keys", "-t", pane, "Enter")
        answered = 0
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            attempts = (base / "attempts").read_text().splitlines() if (base / "attempts").exists() else []
            if len(attempts) > answered and "APP_TTY_APPROVAL" in tmux("capture-pane", "-p", "-t", pane):
                tmux("send-keys", "-t", pane, "-l", "approve")
                tmux("send-keys", "-t", pane, "Enter")
                answered += 1
            if tmux("display-message", "-p", "-t", pane, "#{pane_dead}") == "1":
                completed = True
                break
            time.sleep(0.05)
        if not completed:
            raise AssertionError(f"Fixture still active; retained tmux server {server} and {base}")
        result = json.loads((base / "result.json").read_text())
        assert result["ok"], result.get("error")
        assert answered == 2
        assert (base / "answers").read_text().splitlines() == ["approve", "approve"]
        assert git("rev-parse", "HEAD^").strip() == before
        assert git("show", "HEAD:example.txt") == b"a\nB\nc\nd\n"
        assert git("show", ":example.txt") == b"a\nB\nc\nd\n"
        assert path.read_text() == "a\nB\nc\nD\n"
        raw = git("cat-file", "commit", "HEAD").split(b"\n\n", 1)[1]
        assert raw == result["message"].encode()
        assert not (repo / ".git" / "index.lock").exists()
        assert not list((repo / ".git").glob("rhizome-commit-*"))
        print("PASS: real 80x24 UI review → partial stage → interactive refusal/draft → commit")
        print("PASS: UI resumes after both outcomes; exact message and unstaged edit preserved")
        succeeded = True
    finally:
        # Only tear down a pane whose application has already exited. Never use
        # kill-server/temporary-directory cleanup to manufacture quiescence.
        if completed:
            tmux("kill-server")
        if succeeded:
            shutil.rmtree(base)
        else:
            print(f"Retained fixture for inspection: {base}; tmux -L {server}", file=sys.stderr)


if __name__ == "__main__":
    main()
