#!/usr/bin/env python3
"""Headless interaction recording for prototype evidence, not a product test."""
import asyncio
import json
from pathlib import Path
from v03_precision_prototype import V03Prototype

async def journey(size: tuple[int, int]) -> dict[str, object]:
    app = V03Prototype("A")
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        # Keyboard: mark → stage → unstage. The work is in-memory only.
        await pilot.press("v", "s", "u")
        await pilot.pause()
        keyboard = {"staged_after_unstage": app.staged, "anchor_set": app.anchor is not None}
        # Mouse equivalents: named controls remain visible/clickable.
        await pilot.click("#mark")
        await pilot.click("#stage")
        await pilot.click("#change")
        await pilot.click("#stage")
        await pilot.click("#back")
        await pilot.click("#variant-B")
        await pilot.pause()
        mouse = {"variant": app.variant, "stale": app.stale, "staged": app.staged}
        # Variant key is ignored while composing, then works after focus changes.
        if size[0] <= 90:
            await pilot.click("#panel-commit")
        await pilot.click("#commit-message")
        await pilot.press("ctrl+shift+right")
        during_text = app.variant
        app.query_one("#line-table").focus()
        await pilot.press("ctrl+shift+right")
        await pilot.pause()
        return {"size": f"{size[0]}x{size[1]}", "keyboard": keyboard, "mouse": mouse,
                "variant_during_text_entry": during_text, "variant_after_text_entry": app.variant,
                "draft": app.query_one("#commit-message").text}

async def main() -> None:
    results = [await journey((80, 24)), await journey((170, 48))]
    out = Path(__file__).parent / "evidence" / "interaction-record.json"
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    asyncio.run(main())
