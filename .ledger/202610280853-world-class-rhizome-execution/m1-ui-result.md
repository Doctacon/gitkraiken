Implemented owned M1 UI slice.

Changed:
- `app.tcss`: stronger focus/selection affordances; composer guidance and workspace settings styling.
- `precision_diff.py`: explicit `SELECTION:` status and non-color range marking.
- `activity.py`: clearer local-work hierarchy and focused-row treatment.
- `dialogs.py`: F4 and optional daily-flow help.
- Added `workspace_controls.py`: frozen request seam plus controls/diagnostics dialogs.
- Added `capture_m1.py` and `test_m1_visuals.py`.

Validation passed:
- `uv run ruff check` on Python additions/changes.
- `uv run pytest -q tests/test_m1_visuals.py -k precision` — 1 passed.
- `uv run pytest -q tests/test_precision_workspace.py tests/test_responsive_workspace.py tests/test_theme.py` — 8 passed.
- Mounted `WorkspaceControlsDialog` at 80×24 successfully.

Pending root integration:
- `app.py` must wire F4 / `#workspace-settings`; the new F4 test is intentionally not runnable yet.
- No screenshots generated: `scripts/capture_m1.py` uses the real F4 route and awaits that wiring.
- No commits made.