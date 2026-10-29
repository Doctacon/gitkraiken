"""Capture M1 UI evidence from a disposable, entirely local Git repository.

    uv run python scripts/capture_m1.py

Writes SVGs beneath ``docs/assets`` (and PNGs when ``rsvg-convert`` is already
available). No user home, credentials, clipboard, or network configuration is used.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from textual.containers import VerticalScroll
from textual.widgets import Button, DataTable

from rhizome.app import RhizomeApp


@contextmanager
def isolated_environment(root: Path):
    """Keep Git and Rhizome state entirely below a temporary directory."""
    # macOS exposes /var as a symlink to /private/var. Persistence correctly
    # rejects symlinked state roots, so hand it the physical temporary path.
    root = root.resolve()
    names = (
        "HOME",
        "XDG_CONFIG_HOME",
        "RHIZOME_STATE_HOME",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_ALLOW_PROTOCOL",
        "GIT_TERMINAL_PROMPT",
    )
    previous = {name: os.environ.get(name) for name in names}
    home, config, state = root / "home", root / "config", root / "state"
    for directory in (home, config, state):
        directory.mkdir()
    os.environ.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(config),
            "RHIZOME_STATE_HOME": str(state),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=M1 Capture",
            "-c",
            "user.email=m1@example.invalid",
            *args,
        ],
        check=True,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )


def make_repository(root: Path) -> Path:
    repo = root / "repository"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "notes.txt").write_text("one\ntwo\nthree\nfour\n")
    git(repo, "add", "notes.txt")
    git(repo, "commit", "-m", "Start local capture fixture")
    (repo / "notes.txt").write_text("one\nTWO — revised\nthree\nFOUR — revised\n")
    return repo


async def idle(app: RhizomeApp, pilot) -> None:
    for _ in range(100):
        await pilot.pause(0.03)
        await app.workers.wait_for_complete()
        if not app.busy and not app.workers:
            return
    raise RuntimeError("Rhizome did not become idle for capture")


def save_png(svg: Path) -> None:
    converter = shutil.which("rsvg-convert")
    if converter:
        subprocess.run([converter, "-o", str(svg.with_suffix(".png")), str(svg)], check=True)


async def capture(output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="rhizome-m1-capture-") as temporary:
        temp = Path(temporary)
        with isolated_environment(temp):
            app = RhizomeApp(make_repository(temp))
            async with app.run_test(size=(170, 48)) as pilot:
                await idle(app, pilot)
                wide = output / "m1-workspace-wide.svg"
                app.save_screenshot(str(wide))
                save_png(wide)

                await pilot.click("#show-files")
                app.query_one("#wip-table", DataTable).focus()
                await pilot.press("enter")
                await idle(app, pilot)
                precision_wide = output / "m1-precision-wide.svg"
                app.save_screenshot(str(precision_wide))
                save_png(precision_wide)

                await pilot.click("#show-composer")
                composer_wide = output / "m1-composer-wide.svg"
                app.save_screenshot(str(composer_wide))
                save_png(composer_wide)

                # This is the public M1 navigation route, rather than a private
                # mount, so the capture also catches a broken F4 affordance.
                await pilot.press("f4")
                await pilot.pause()
                controls_wide = output / "m1-workspace-controls-wide.svg"
                app.save_screenshot(str(controls_wide))
                save_png(controls_wide)
                app.query_one("#controls-body", VerticalScroll).scroll_end(animate=False)
                await pilot.pause()
                controls_actions_wide = output / "m1-workspace-controls-actions-wide.svg"
                app.save_screenshot(str(controls_actions_wide))
                save_png(controls_actions_wide)
                await pilot.press("escape")

                await pilot.resize_terminal(80, 24)
                await pilot.click("#show-files")
                app.query_one("#wip-table", DataTable).focus()
                await pilot.press("enter")
                await idle(app, pilot)
                precision = output / "m1-precision-80x24.svg"
                app.save_screenshot(str(precision))
                save_png(precision)

                await pilot.click("#show-composer")
                composer = output / "m1-composer-80x24.svg"
                app.save_screenshot(str(composer))
                save_png(composer)

                await pilot.press("f4")
                await pilot.pause()
                controls = output / "m1-workspace-controls-80x24.svg"
                app.save_screenshot(str(controls))
                save_png(controls)
                app.query_one("#controls-body", VerticalScroll).scroll_end(animate=False)
                await pilot.pause()
                controls_actions = output / "m1-workspace-controls-actions-80x24.svg"
                app.save_screenshot(str(controls_actions))
                save_png(controls_actions)

                # Apply via the public dialog, then return to the already
                # captured diff. This verifies display-only plain chrome.
                # The dialog was opened through F4. Focus below the fold keeps
                # the capture deterministic while exercising its scrollable,
                # keyboard-reachable controls at 80x24.
                app.query_one("#plain-glyphs").focus()
                await pilot.press("space")
                app.query_one("#controls-apply", Button).focus()
                await pilot.press("enter")
                await idle(app, pilot)
                await pilot.click("#show-files")
                app.query_one("#wip-table", DataTable).focus()
                await pilot.press("enter")
                await idle(app, pilot)
                await pilot.press("w")
                plain = output / "m1-precision-plain-80x24.svg"
                app.save_screenshot(str(plain))
                save_png(plain)


def main() -> None:
    output = Path("docs/assets")
    output.mkdir(parents=True, exist_ok=True)
    asyncio.run(capture(output))


if __name__ == "__main__":
    main()
