#!/usr/bin/env python3
"""Capture deterministic SVG snapshots of the throwaway V03 Textual prototype."""
import asyncio
from pathlib import Path
from v03_precision_prototype import V03Prototype

OUT = Path(__file__).parent / "evidence"

async def capture(variant: str, size: tuple[int, int]) -> None:
    app = V03Prototype(variant)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        name = f"variant-{variant.lower()}-{size[0]}x{size[1]}.svg"
        (OUT / name).write_text(app.export_screenshot(), encoding="utf-8")

async def main() -> None:
    OUT.mkdir(exist_ok=True)
    for variant in "ABC":
        for size in ((80, 24), (170, 48)):
            await capture(variant, size)

if __name__ == "__main__":
    asyncio.run(main())
