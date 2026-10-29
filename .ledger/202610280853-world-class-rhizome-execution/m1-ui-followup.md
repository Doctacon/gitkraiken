Implemented owned M1 presentation work.

Highlights:
- Workspace controls now expose `reset-global`, grouped responsively; all seven actions visible at 80×24 action view.
- Global offline explanation/locking, prominent session-only exclusion, diagnostic labels/export error disable.
- Added `PrecisionDiff.set_plain_glyphs()` preserving document, range, cursor, and scroll.
- Updated Help core-controls/restored-context copy.
- Expanded capture script and public RhizomeApp/Pilot coverage.

Validation passed:
- `uv run ruff check ...`
- `uv run pytest -q tests/test_m1_visuals.py tests/test_precision_workspace.py tests/test_plain_workspace.py`
  - `10 passed`
- `uv run python scripts/capture_m1.py`
- Inspected generated PNGs, including:
  - `docs/assets/m1-precision-{wide,80x24,plain-80x24}.{svg,png}`
  - `docs/assets/m1-composer-{wide,80x24}.{svg,png}`
  - `docs/assets/m1-workspace-controls-{wide,80x24}.{svg,png}`
  - `docs/assets/m1-workspace-controls-actions-{wide,80x24}.{svg,png}`

Gap: root-owned `app.py::_apply_core_settings()` still needs to call `workspace.query_one(PrecisionDiff).set_plain_glyphs(plain)`. I did not edit `app.py`; until that is wired, the public settings route cannot prove a live precision-diff glyph toggle.