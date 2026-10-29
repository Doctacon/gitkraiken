# PrecisionDiff integration contract

## Component

`src/rhizome/precision_diff.py` provides `PrecisionDiff`, a self-contained Textual diff panel. Call `set_document(document, context=3)` with the exact `PatchDocument` returned by `PatchService.open`; the widget retains that object and only renders its `PatchLine`s. It never reconstructs patch text, calls Git, persists state, or mutates a document.

The parent should handle:

- `PrecisionDiff.ApplySelection(document, start, end)` by applying that same document with the inclusive canonical displayed IDs. `s` emits it only for an unstaged document; `u` only for a staged one.
- `PrecisionDiff.RefreshRequested(context)` by reopening the current patch with that context, then calling `set_document` with the result.
- `PrecisionDiff.BackRequested` by returning to the parent workspace.

Call `set_stale()` when the parent detects a changed patch. Stale, unsupported, empty, and unmarked views state why apply is unavailable and never emit `ApplySelection`. `focus_lines()` returns focus to the table. A fresh document focuses its first add/delete line, so `v`, `down`, `s` selects the adjacent delete/add replacement pair.

## Interaction surface

- `v` anchors/clears a contiguous range; arrows move its endpoint. Click selects; Shift-click extends from the prior cursor.
- `s`/`u`, explicit Stage/Unstage buttons, and hunk select/navigation (`h`, `[`/`]`) cover keyboard and visible controls.
- Literal search moves through source `PatchLine.text` without filtering or changing canonical IDs. `w` only renders visible space/tab markers.
- Changed token spans receive word emphasis; selection/captured/stale status is textual as well as colored.
- The component has compact, local `DEFAULT_CSS`, including explicit one-row, borderless button styling and explicit dark DataTable cursor text. At 80 columns, all precision controls remain reachable without horizontal overflow.

## Evidence and limits

Synthetic-only headless captures are in `precision-widget/evidence/precision-170x48.svg` and `precision-80x24.svg` (with rendered PNG inspection copies). They show the marked replacement range at 170×48 and a stale disabled state at 80×24. Capture command:

```sh
uv run python .ledger/202610280853-world-class-rhizome-execution/precision-widget/capture.py
```

The harness is deliberately duck-typed, has no Git identity/bytes, and cannot establish real application or Git behavior. Ruff and `py_compile` passed for the production component. The root-owned application integration test was not run or claimed passing; root must wire the messages to `PatchService` and the workspace.
