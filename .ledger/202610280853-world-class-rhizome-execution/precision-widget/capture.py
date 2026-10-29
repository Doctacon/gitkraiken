#!/usr/bin/env python3
"""Headless visual capture for PrecisionDiff using synthetic, non-Git data only."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from textual.app import App, ComposeResult

from rhizome.patches import PatchLine
from rhizome.precision_diff import PrecisionDiff

OUT = Path(__file__).parent / "evidence"


class Harness(App[None]):
    CSS = "Screen { background: #f7fbfc; }"

    def compose(self) -> ComposeResult:
        yield PrecisionDiff(id="precision")

    def on_mount(self) -> None:
        # This duck-typed display fixture is intentionally ledger-only. It has no
        # repository identity or patch bytes and cannot be applied by a service.
        document = SimpleNamespace(
            path="src/rhizome/example.py",
            staged=False,
            selectable=True,
            reason="",
            lines=(
                PatchLine(1, "hunk", "@@ -8,7 +8,8 @@ def review():", hunk=1),
                PatchLine(2, "context", "     return ready", hunk=1, old_line=8, new_line=8),
                PatchLine(3, "delete", "-    return old_value", hunk=1, old_line=9),
                PatchLine(4, "add", "+    return reviewed_value", hunk=1, new_line=9),
                PatchLine(5, "context", "     save()", hunk=1, old_line=10, new_line=10),
                PatchLine(6, "hunk", "@@ -31,3 +32,4 @@ def save():", hunk=2),
                PatchLine(7, "add", "+    audit('reviewed')", hunk=2, new_line=32),
                PatchLine(8, "context", "     return None", hunk=2, old_line=33, new_line=33),
            ),
        )
        self.query_one(PrecisionDiff).set_document(document)  # type: ignore[arg-type]


async def capture(size: tuple[int, int]) -> None:
    app = Harness()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        if size[0] > 100:
            await pilot.press("v", "down")
        else:
            app.query_one(PrecisionDiff).set_stale()
        await pilot.pause()
        (OUT / f"precision-{size[0]}x{size[1]}.svg").write_text(
            app.export_screenshot(), encoding="utf-8"
        )


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in ((170, 48), (80, 24)):
        await capture(size)


if __name__ == "__main__":
    asyncio.run(main())
