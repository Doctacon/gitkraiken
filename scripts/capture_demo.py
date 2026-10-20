"""Render a real demo repository headlessly for visual regression inspection.

uv run python scripts/capture_demo.py /tmp/workspace.svg --width 170 --height 48
rsvg-convert -o /tmp/workspace.png /tmp/workspace.svg
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from gitkraiken.app import GitKraikenApp
from gitkraiken.demo import create_demo


async def capture(output: Path, width: int, height: int, wip: bool, compact: bool = False) -> None:
    with tempfile.TemporaryDirectory(prefix="gitkraiken-preview-") as temp:
        app = GitKraikenApp(create_demo(Path(temp)))
        app.title = "GitKraiken · DEMO"
        async with app.run_test(size=(width, height)) as pilot:
            await pilot.pause(1)
            await app.workers.wait_for_complete()
            if compact:
                app.action_toggle_graph_style()
                await pilot.pause()
            if not wip:
                app.jump_to(app.snapshot_data.head)
                await pilot.pause(0.5)
                await app.workers.wait_for_complete()
            await pilot.pause()
            output.parent.mkdir(parents=True, exist_ok=True)
            app.save_screenshot(str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=170)
    parser.add_argument("--height", type=int, default=48)
    parser.add_argument("--wip", action="store_true")
    parser.add_argument("--compact", action="store_true", help="Capture the original compact graph")
    args = parser.parse_args()
    asyncio.run(capture(args.output, args.width, args.height, args.wip, args.compact))
