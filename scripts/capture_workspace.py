"""Capture the current workspace in an isolated, local-only demo.

    uv run python scripts/capture_workspace.py /tmp/history.svg --theme light
    uv run python scripts/capture_workspace.py /tmp/review.svg --view diff --width 80 --height 24

A sibling PNG is also written when rsvg-convert is already installed. Unlike
historical milestone captures, this script follows the current public UI.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import tempfile
from pathlib import Path

from capture_m1 import idle, isolated_environment, save_png
from textual.widgets import DataTable, TextArea

from rhizome.app import RhizomeApp
from rhizome.demo import create_demo
from rhizome.persistence import WorkspaceStateStore
from rhizome.pull_controls import PullReviewDialog


def prepare_pull_review(repo: Path, temporary: Path) -> None:
    """Diverge only the disposable demo and its local bare origin."""

    def git(cwd: Path, *args: str) -> None:
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Demo",
                "-c",
                "user.email=demo@example.test",
                *args,
            ],
            cwd=cwd,
            capture_output=True,
            check=True,
        )

    git(repo, "add", "--all")
    git(repo, "commit", "-m", "Refine the local workspace")
    writer = temporary / "upstream-writer"
    git(
        temporary,
        "clone",
        "--branch",
        "feature/workspace",
        str(repo.parent / "origin.git"),
        str(writer),
    )
    (writer / "upstream-preview.txt").write_text("An independent upstream improvement.\n")
    git(writer, "add", "upstream-preview.txt")
    git(writer, "commit", "-m", "Refine upstream documentation")
    git(writer, "push", "origin", "HEAD:refs/heads/feature/workspace")


async def capture(output: Path, *, width: int, height: int, theme: str, view: str) -> None:
    with tempfile.TemporaryDirectory(prefix="rhizome-workspace-capture-") as temporary:
        root = Path(temporary)
        with isolated_environment(root):
            repo = create_demo(root / "demo")
            if view == "pull-review":
                prepare_pull_review(repo, root)
            WorkspaceStateStore().save("global", {"theme": theme, "remote_refresh_enabled": False})
            app = RhizomeApp(repo)
            async with app.run_test(size=(width, height)) as pilot:
                await idle(app, pilot)
                if view == "history":
                    app.jump_to(app.snapshot_data.head)
                    app.action_show_primary()
                elif view == "settings":
                    await pilot.press("f4")
                elif view == "pull-menu":
                    assert await pilot.click("#pull-menu")
                elif view == "pull-review":
                    assert await pilot.click("#fetch")
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    if not isinstance(app.screen, PullReviewDialog):
                        raise RuntimeError("Pull review did not open: " + app._last_error)
                elif view == "activity":
                    await pilot.press("f2")
                    await pilot.click("#stage-all")
                    await idle(app, pilot)
                    await pilot.click("#unstage-all")
                    await idle(app, pilot)
                    await pilot.press("ctrl+l")
                else:
                    await pilot.press("f2")
                    if view == "diff":
                        app.query_one("#wip-table", DataTable).focus()
                        await pilot.press("enter")
                    elif view == "composer":
                        await pilot.press("f3")
                        app.query_one("#commit-message", TextArea).load_text(
                            "Refine the workspace experience\n\n"
                            "Keep history readable and make the next action clear."
                        )
                if view != "pull-review":
                    await idle(app, pilot)
                output.parent.mkdir(parents=True, exist_ok=True)
                app.save_screenshot(str(output))
                save_png(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=170)
    parser.add_argument("--height", type=int, default=42)
    parser.add_argument("--theme", choices=("light", "dark", "high-contrast"), default="light")
    parser.add_argument(
        "--view",
        choices=(
            "history",
            "changes",
            "diff",
            "composer",
            "settings",
            "activity",
            "pull-menu",
            "pull-review",
        ),
        default="history",
    )
    args = parser.parse_args()
    asyncio.run(
        capture(args.output, width=args.width, height=args.height, theme=args.theme, view=args.view)
    )
