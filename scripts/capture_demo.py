"""Render a real demo repository headlessly for visual regression inspection.

uv run python scripts/capture_demo.py /tmp/workspace.svg --width 170 --height 48
rsvg-convert -o /tmp/workspace.png /tmp/workspace.svg
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import tempfile
from pathlib import Path

from textual.widgets import Input, Tree

from gitkraiken.app import GitKraikenApp
from gitkraiken.demo import create_demo


def add_reference_fixture(root: Path) -> None:
    """Populate a disposable demo with local-only remotes and nested namespaces."""

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    url = git("remote", "get-url", "origin")
    names = ("feature/jlott/layout", "feature/jlott/search", "feature/connor/sync")
    for name in names:
        git("branch", name, "HEAD")
    for owner in ("origin", "upstream", "team/origin", "archive"):
        if owner != "origin":
            git("remote", "add", owner, url)
        for name in (*names, "main"):
            git("update-ref", f"refs/remotes/{owner}/{name}", "main" if name == "main" else "HEAD")


async def wait_for_idle(app, pilot) -> None:
    for _ in range(100):
        await pilot.pause(0.05)
        await app.workers.wait_for_complete()
        await pilot.pause()
        if not app.busy and not app.workers:
            return
    raise RuntimeError("Preview did not become idle")


async def capture(
    output: Path,
    width: int,
    height: int,
    wip: bool,
    compact: bool = False,
    *,
    many_refs: bool = False,
    ref_filter: str = "",
    collapse_remotes: bool = False,
) -> None:
    with tempfile.TemporaryDirectory(prefix="gitkraiken-preview-") as temp:
        root = create_demo(Path(temp))
        if many_refs:
            add_reference_fixture(root)
        app = GitKraikenApp(root)
        app.title = "GitKraiken · DEMO"
        async with app.run_test(size=(width, height)) as pilot:
            await wait_for_idle(app, pilot)
            if compact:
                app.action_toggle_graph_style()
                await pilot.pause()
            if not wip:
                app.jump_to(app.snapshot_data.head)
            if ref_filter:
                app.query_one("#ref-filter", Input).value = ref_filter
            if collapse_remotes:
                for node in app.query_one("#refs-tree", Tree).root.children:
                    if node.data.key == "group:remotes":
                        node.collapse()
            await wait_for_idle(app, pilot)
            output.parent.mkdir(parents=True, exist_ok=True)
            app.save_screenshot(str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=170)
    parser.add_argument("--height", type=int, default=48)
    parser.add_argument("--wip", action="store_true")
    parser.add_argument("--compact", action="store_true", help="Capture the original compact graph")
    parser.add_argument(
        "--many-refs", action="store_true", help="Add local-only remotes and nested refs"
    )
    parser.add_argument("--ref-filter", default="", help="Preview sidebar-only search")
    parser.add_argument(
        "--collapse-remotes", action="store_true", help="Preview a folded Remotes section"
    )
    args = parser.parse_args()
    asyncio.run(
        capture(
            args.output,
            args.width,
            args.height,
            args.wip,
            args.compact,
            many_refs=args.many_refs,
            ref_filter=args.ref_filter,
            collapse_remotes=args.collapse_remotes,
        )
    )
