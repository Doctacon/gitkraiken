# V03 precision-workflow throwaway prototype

A standalone Textual sketch of the proposed inspect → contiguous range → explicit stage/unstage → compose flow. It has **no repository access, persistence, patch application, or production wiring**. All files and changes are synthetic/in-memory.

## Run

From the repository root:

```sh
uv run --locked python .ledger/202610280853-world-class-rhizome-execution/prototype-precision/v03_precision_prototype.py --variant A
```

Replace `A` with `B` or `C`. The bottom switcher is always visible; use its buttons or `Ctrl+Shift+Left` / `Ctrl+Shift+Right`. That shortcut is intentionally ignored while a text field has focus.

## Prototype controls

- Click a diff row, press `v` to set/clear a contiguous range anchor, then use **Stage range** / **Unstage** (or `s` / `u`) outside text entry.
- **Simulate change** creates a stable, visible stale-selection refusal state.
- At 80 columns, **Files** and **Commit** summon the one primary panel; **Back** returns to the diff. This preserves the 80×24 task-first model rather than compressing three panes into unreadable columns.
- The simulated commit button reports a failed hook and leaves the draft visible. Checkout and risky actions are deliberately gated.

## Variants

- **A — Familiar three-pane:** Files / unified diff / composer. Recommended for adoption.
- **B — Task stack:** file queue, diff, and composer are vertically sequenced; clearest task order but sacrifices simultaneous composition context.
- **C — Focus rail:** compact file rail and expanded composer; strongest composing posture but has the least generous diff reading width.

See `verdict.md` and `evidence.md` for the recommendation and bounded evidence.
